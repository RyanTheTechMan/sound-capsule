from __future__ import annotations

import tempfile
import unittest
import wave
import hashlib
import json
import zipfile
from pathlib import Path

from soundcapsule.capsule import Capsule
from soundcapsule.flp import (
    EVENT_CHANNEL_NEW,
    EVENT_CHANNEL_ENABLED,
    EVENT_CHANNEL_ROUTED_TO,
    EVENT_CHANNEL_SAMPLE_PATH,
    EVENT_CHANNEL_TYPE,
    EVENT_CURRENT_PATTERN,
    EVENT_FL_VERSION,
    EVENT_PATTERN_NAME,
    EVENT_PATTERN_NEW,
    EVENT_PATTERN_NOTES,
    EVENT_PLUGIN_INTERNAL_NAME,
    EVENT_PLUGIN_NAME,
    FLPFile,
    FORMAT_PROJECT,
    NOTE_STRUCT,
    NoteRecord,
    data_event,
    scalar_event,
    text_event,
)


def note(channel: int, *, position: int, length: int, key: int, flags: int = 0) -> NoteRecord:
    return NoteRecord(
        NOTE_STRUCT.pack(
            position,
            flags,
            channel,
            length,
            key,
            7,  # group
            121,  # fine pitch
            0,
            64,  # release
            3,  # MIDI channel
            72,  # pan
            101,  # velocity
            44,  # Mod X
            55,  # Mod Y
        )
    )


def fixture_project(*, ppq: int = 96) -> FLPFile:
    note_a = note(2, position=24, length=96, key=60, flags=8)
    note_b = note(5, position=192, length=48, key=67)
    events = [
        text_event(EVENT_FL_VERSION, "25.2.5.5055", unicode_text=False),
        scalar_event(EVENT_CURRENT_PATTERN, 3),
        scalar_event(EVENT_CHANNEL_NEW, 2),
        scalar_event(EVENT_CHANNEL_TYPE, 2),
        scalar_event(EVENT_CHANNEL_ROUTED_TO, 7),
        text_event(EVENT_PLUGIN_INTERNAL_NAME, "Fruity Wrapper"),
        text_event(EVENT_PLUGIN_NAME, "Serum Lead"),
        data_event(
            251,
            bytes.fromhex(
                "02000000ffffffffffffffffffffffffffffff7f000000000000f0bf"
                "ffffffff00ffffff7fffffff7f0000"
            ),
        ),
        data_event(213, b"opaque-plugin-state\x00\xff\x10"),
        scalar_event(EVENT_CHANNEL_NEW, 5),
        scalar_event(EVENT_CHANNEL_TYPE, 0),
        scalar_event(EVENT_CHANNEL_ROUTED_TO, 12),
        text_event(EVENT_PLUGIN_INTERNAL_NAME, "Sampler"),
        text_event(EVENT_PLUGIN_NAME, "Kick"),
        data_event(213, b"opaque-sampler-state"),
        scalar_event(EVENT_PATTERN_NEW, 3),
        data_event(EVENT_PATTERN_NOTES, note_a.raw + note_b.raw),
        scalar_event(EVENT_PATTERN_NEW, 3),
        text_event(EVENT_PATTERN_NAME, "Verse"),
    ]
    return FLPFile(FORMAT_PROJECT, 2, ppq, events)


def write_silence(path: Path) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(44_100)
        output.writeframes(b"\0\0\0\0" * 256)


class FLPRoundTripTests(unittest.TestCase):
    def test_plugin_name_uses_internal_generator_not_renamed_channel(self) -> None:
        project = fixture_project()
        events = list(project.events)
        internal_index = next(
            index for index, event in enumerate(events)
            if event.id == EVENT_PLUGIN_INTERNAL_NAME
        )
        events[internal_index] = text_event(EVENT_PLUGIN_INTERNAL_NAME, "FLEX")
        section = FLPFile(FORMAT_PROJECT, project.channel_count, project.ppq, events).channel_sections()[0]
        self.assertEqual(section.name, "Serum Lead")
        self.assertEqual(section.plugin_name, "FLEX")

    def test_byte_exact_round_trip_preserves_unknown_events(self) -> None:
        source = fixture_project()
        encoded = source.to_bytes()
        parsed = FLPFile.from_bytes(encoded)
        self.assertEqual(encoded, parsed.to_bytes())
        self.assertEqual(parsed.fl_version, "25.2.5.5055")
        self.assertEqual([section.iid for section in parsed.channel_sections()], [2, 5])
        self.assertEqual(
            next(event.payload for event in parsed.channel_sections()[0].events if event.id == 251),
            bytes.fromhex(
                "02000000ffffffffffffffffffffffffffffff7f000000000000f0bf"
                "ffffffff00ffffff7fffffff7f0000"
            ),
        )
        self.assertEqual(parsed.channel_sections()[0].events[-1].payload, b"opaque-plugin-state\x00\xff\x10")

    def test_fl26_global_event_64_is_not_counted_as_a_channel(self) -> None:
        source = fixture_project()
        source.events[1:1] = [
            scalar_event(EVENT_CHANNEL_NEW, 2),
            scalar_event(48, 0),
            scalar_event(0, 0),
        ]

        parsed = FLPFile.from_bytes(source.to_bytes())

        self.assertEqual(parsed.channel_count, 2)
        self.assertEqual([section.iid for section in parsed.channel_sections()], [2, 5])
        self.assertEqual(parsed.to_bytes(), source.to_bytes())

    def test_channel_ownership_stops_before_mixer_and_ignores_rack_events(self) -> None:
        project = fixture_project()
        pattern_at = next(i for i, event in enumerate(project.events) if event.id == EVENT_PATTERN_NEW)
        project.events[pattern_at:pattern_at] = [
            scalar_event(133, 480),  # Channel Rack window height, not channel state.
            scalar_event(99, 1),  # FL 25 post-channel boundary.
            data_event(213, b"mixer-slot-plugin-state"),
        ]

        sections = project.channel_sections()

        self.assertEqual([section.iid for section in sections], [2, 5])
        self.assertNotIn(133, {event.id for section in sections for event in section.events})
        self.assertNotIn(b"mixer-slot-plugin-state", {event.payload for section in sections for event in section.events})

    def test_channel_ownership_excludes_interleaved_pattern_metadata(self) -> None:
        project = fixture_project()
        second_start = project.events.index(project.channel_sections()[1].events[0])
        project.events[second_start:second_start] = [
            scalar_event(EVENT_PATTERN_NEW, 3),
            text_event(EVENT_PATTERN_NAME, "Interleaved name"),
            scalar_event(150, 123),
        ]
        first = project.channel_sections()[0]
        self.assertNotIn(EVENT_PATTERN_NAME, {event.id for event in first.events})
        self.assertEqual(first.events[-1].payload, b"opaque-plugin-state\x00\xff\x10")

    def test_event_224_before_pattern_context_is_not_parsed_as_notes(self) -> None:
        project = fixture_project()
        opaque_224 = data_event(EVENT_PATTERN_NOTES, b"opaque-project-state-not-notes-123")
        project.events.insert(0, opaque_224)

        reparsed = FLPFile.from_bytes(project.to_bytes())

        self.assertIn(opaque_224.payload, {event.payload for event in reparsed.events})
        self.assertEqual([note.rack_channel for note in reparsed.pattern_notes()[3]], [2, 5])

    def test_override_preserves_unowned_interleaved_events(self) -> None:
        source = fixture_project()
        destination = fixture_project()
        second_start = destination.events.index(destination.channel_sections()[1].events[0])
        global_event = scalar_event(133, 777)
        destination.events.insert(second_start + 2, global_event)

        merged = destination.override_capsule(
            source.extract_channels([2]),
            {2: [source.pattern_notes()[3][0]]},
            [5],
            source_ppq=source.ppq,
            pattern_id=3,
        )

        self.assertIn(global_event, merged.events)
        self.assertEqual(sum(event is global_event for event in merged.events), 1)

    def test_append_remaps_channels_rescales_notes_and_routes_master(self) -> None:
        source = fixture_project(ppq=96)
        destination = fixture_project(ppq=192)
        sections = source.extract_channels([2, 5])
        notes = source.pattern_notes()[3]
        notes_by_channel = {iid: [item for item in notes if item.rack_channel == iid] for iid in (2, 5)}

        merged, mapping, pattern_id = destination.append_capsule(
            sections,
            notes_by_channel,
            source_ppq=source.ppq,
            pattern_name="Imported Sound",
        )

        self.assertEqual(mapping, {2: 6, 5: 7})
        self.assertEqual(pattern_id, 4)
        self.assertEqual(merged.channel_count, 4)
        appended = merged.channel_sections()[-2:]
        self.assertEqual([section.iid for section in appended], [6, 7])
        self.assertEqual(
            [next(event.scalar for event in section.events if event.id == EVENT_CHANNEL_ROUTED_TO) for section in appended],
            [0, 0],
        )
        imported_notes = merged.pattern_notes()[4]
        self.assertEqual([(item.rack_channel, item.position, item.length) for item in imported_notes], [(6, 48, 192), (7, 384, 96)])
        self.assertEqual(imported_notes[0].to_dict()["slide"], True)
        first_channel_index = next(i for i, event in enumerate(merged.events) if event.id == EVENT_CHANNEL_NEW)
        imported_pattern_index = next(
            i for i, event in enumerate(merged.events)
            if event.id == EVENT_PATTERN_NEW and event.scalar == pattern_id
        )
        self.assertLess(imported_pattern_index, first_channel_index)

    def test_append_to_current_pattern_preserves_existing_notes(self) -> None:
        source = fixture_project(ppq=96)
        destination = fixture_project(ppq=192)
        sections = source.extract_channels([2])
        notes = {2: [source.pattern_notes()[3][0]]}

        merged, mapping, pattern_id = destination.append_capsule(
            sections,
            notes,
            source_ppq=source.ppq,
            pattern_name="Ignored for current pattern",
            target_pattern_id=3,
        )

        self.assertEqual(mapping, {2: 6})
        self.assertEqual(pattern_id, 3)
        self.assertEqual(merged.max_pattern_id(), 3)
        merged_notes = merged.pattern_notes()[3]
        self.assertEqual([item.rack_channel for item in merged_notes], [2, 6, 5])
        imported = merged_notes[1]
        self.assertEqual((imported.position, imported.length), (48, 192))
        self.assertEqual(
            [item.position for item in merged_notes],
            sorted(item.position for item in merged_notes),
        )

    def test_wrapped_instrument_channel_type_is_supported(self) -> None:
        project = fixture_project()
        channel_type = next(
            event for event in project.channel_sections()[0].events if event.id == EVENT_CHANNEL_TYPE
        )
        project.events[project.events.index(channel_type)] = channel_type.with_scalar(4)
        self.assertEqual(project.extract_channels([2])[0].channel_type, 4)

    def test_isolated_preview_keeps_only_selected_pattern_channel(self) -> None:
        source = fixture_project()
        preview = source.isolated_preview_project([2], 3)

        selected, muted = preview.channel_sections()
        self.assertEqual(
            next(event.scalar for event in selected.events if event.id == EVENT_CHANNEL_ENABLED), 1
        )
        self.assertEqual(
            next(event.scalar for event in selected.events if event.id == EVENT_CHANNEL_ROUTED_TO), 0
        )
        self.assertEqual(
            next(event.scalar for event in muted.events if event.id == EVENT_CHANNEL_ENABLED), 0
        )
        self.assertEqual([note.rack_channel for note in preview.pattern_notes()[3]], [2])
        self.assertEqual([note.rack_channel for note in source.pattern_notes()[3]], [2, 5])

    def test_override_preserves_routes_and_unrelated_notes(self) -> None:
        capsule_source = fixture_project(ppq=96)
        destination = fixture_project(ppq=192)
        source_section = capsule_source.extract_channels([2])
        source_notes = {2: [capsule_source.pattern_notes()[3][0]]}

        merged = destination.override_capsule(
            source_section,
            source_notes,
            [5],
            source_ppq=96,
            pattern_id=3,
        )

        target = next(section for section in merged.channel_sections() if section.iid == 5)
        self.assertEqual(next(event.scalar for event in target.events if event.id == EVENT_CHANNEL_ROUTED_TO), 12)
        self.assertEqual(target.name, "Serum Lead")
        notes = merged.pattern_notes()[3]
        self.assertEqual(sum(item.rack_channel == 2 for item in notes), 1)
        replacement = next(item for item in notes if item.rack_channel == 5)
        self.assertEqual((replacement.position, replacement.length, replacement.key), (48, 192, 60))


class CapsuleTests(unittest.TestCase):
    def test_capsule_is_portable_and_checksum_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = root / "preview.wav"
            write_silence(preview)
            capsule = Capsule.build(
                root / "Lead.flcapsule",
                name="Lead",
                project=fixture_project(),
                channel_ids=[2, 5],
                pattern_id=3,
                preview_wav=preview,
                tags=["lead", "dark", "lead"],
            )

            capsule.verify()
            manifest = capsule.manifest
            self.assertEqual(manifest.name, "Lead")
            self.assertEqual(manifest.tags, ["dark", "lead"])
            self.assertEqual([channel.source_iid for channel in manifest.channels], [2, 5])
            self.assertEqual(capsule.read_channel_state(manifest.channels[0]).format, 0x20)
            self.assertEqual(capsule.read_notes(manifest.channels[0])[0].to_dict()["mod_y"], 55)
            self.assertTrue(capsule.extract_preview(root / "cache").is_file())
            with zipfile.ZipFile(capsule.path) as archive:
                self.assertEqual(
                    archive.getinfo(manifest.preview_path).compress_type,
                    zipfile.ZIP_STORED,
                )

    def test_capsule_rejects_newer_schema_even_with_valid_checksums(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = root / "preview.wav"
            write_silence(preview)
            capsule = Capsule.build(
                root / "Lead.flcapsule", name="Lead", project=fixture_project(),
                channel_ids=[2], pattern_id=3, preview_wav=preview,
            )
            with zipfile.ZipFile(capsule.path) as source:
                members = {name: source.read(name) for name in source.namelist() if name != "checksums.json"}
            manifest = json.loads(members["manifest.json"])
            manifest["schema_version"] = 2
            members["manifest.json"] = json.dumps(manifest).encode()
            checksums = {name: hashlib.sha256(data).hexdigest() for name, data in members.items()}
            with zipfile.ZipFile(capsule.path, "w") as target:
                for name, data in members.items():
                    target.writestr(name, data)
                target.writestr("checksums.json", json.dumps(checksums))
            with self.assertRaisesRegex(ValueError, "newer capsule schema"):
                capsule.verify()

    def test_capsule_requires_checksum_for_every_member(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = root / "preview.wav"
            write_silence(preview)
            capsule = Capsule.build(
                root / "Lead.flcapsule", name="Lead", project=fixture_project(),
                channel_ids=[2], pattern_id=3, preview_wav=preview,
            )
            with zipfile.ZipFile(capsule.path) as source:
                members = {name: source.read(name) for name in source.namelist()}
            checksums = json.loads(members["checksums.json"])
            del checksums["preview.wav"]
            members["checksums.json"] = json.dumps(checksums).encode()
            with zipfile.ZipFile(capsule.path, "w") as target:
                for name, data in members.items():
                    target.writestr(name, data)
            with self.assertRaisesRegex(ValueError, "invalid checksum coverage"):
                capsule.verify()

    def test_sampler_asset_is_embedded_and_extractable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_audio = root / "snare.wav"
            write_silence(source_audio)
            preview = root / "preview.wav"
            write_silence(preview)
            project = fixture_project()
            second = project.channel_sections()[1]
            insert_at = project.events.index(second.events[-1])
            project.events.insert(insert_at, text_event(EVENT_CHANNEL_SAMPLE_PATH, str(source_audio)))
            capsule = Capsule.build(
                root / "Sampler.flcapsule", name="Sampler", project=project,
                channel_ids=[5], pattern_id=3, preview_wav=preview,
            )
            channel = capsule.manifest.channels[0]
            source_audio.unlink()
            extracted = capsule.extract_sample_asset(channel, root / "restored")
            self.assertIsNotNone(extracted)
            self.assertTrue(extracted.is_file())


if __name__ == "__main__":
    unittest.main()
