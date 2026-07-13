from __future__ import annotations

import tempfile
import unittest
import wave
import hashlib
import json
import struct
import zipfile
from pathlib import Path

from soundcapsule.capsule import Capsule, _open_capsule_archive
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
    EVENT_PADDING,
    EVENT_PLUGIN_INTERNAL_NAME,
    EVENT_PLUGIN_NAME,
    EVENT_TEMPO,
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
        scalar_event(EVENT_TEMPO, 130_000),
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


def write_silence(path: Path, duration_seconds: float | None = None) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(44_100)
        frames = 256 if duration_seconds is None else round(44_100 * duration_seconds)
        output.writeframes(b"\0\0\0\0" * frames)


def write_float_silence(path: Path, duration_seconds: float = 1.0) -> None:
    sample_rate = 48_000
    channels = 2
    block_align = channels * 4
    data = b"\0" * (round(sample_rate * duration_seconds) * block_align)
    path.write_bytes(
        b"RIFF"
        + struct.pack("<I", 36 + len(data))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 3, channels, sample_rate,
                      sample_rate * block_align, block_align, 32)
        + b"data"
        + struct.pack("<I", len(data))
        + data
    )


def write_rf64_silence(path: Path) -> None:
    channels = 2
    sample_rate = 44_100
    block_align = channels * 2
    frames = 256
    data = b"\0" * (frames * block_align)
    body = (
        b"WAVEds64"
        + struct.pack("<IQQQI", 28, 0, len(data), frames, 0)
        + b"fmt "
        + struct.pack(
            "<IHHIIHH", 16, 1, channels, sample_rate,
            sample_rate * block_align, block_align, 16,
        )
        + b"data"
        + struct.pack("<I", 0xFFFFFFFF)
        + data
    )
    contents = bytearray(b"RF64\xff\xff\xff\xff" + body)
    struct.pack_into("<Q", contents, 20, len(contents) - 8)
    path.write_bytes(contents)


def make_legacy_capsule(capsule: Capsule) -> Capsule:
    """Rewrite a playable test capsule as the legacy outer-ZIP format."""
    preview = capsule.path.with_name(f".{capsule.path.name}.preview.wav")
    try:
        capsule.export_preview(preview)
        manifest = capsule.manifest
        with _open_capsule_archive(capsule.path) as source:
            members = {name: source.read(name) for name in source.namelist()}
        members[manifest.preview_path] = preview.read_bytes()
        with zipfile.ZipFile(capsule.path, "w") as target:
            for name, data in members.items():
                target.writestr(
                    name,
                    data,
                    compress_type=(
                        zipfile.ZIP_STORED
                        if name == manifest.preview_path
                        else zipfile.ZIP_DEFLATED
                    ),
                )
    finally:
        preview.unlink(missing_ok=True)
    capsule.verify()
    return capsule


class FLPRoundTripTests(unittest.TestCase):
    def test_tempo_and_user_channel_rename_preserve_plugin_identity(self) -> None:
        project = fixture_project()
        original = project.channel_sections()[0]
        renamed = original.with_name("Saved Capsule Title")

        self.assertEqual(project.tempo_bpm, 130.0)
        self.assertEqual(renamed.name, "Saved Capsule Title")
        self.assertEqual(
            next(
                event.payload for event in renamed.events
                if event.id == EVENT_PLUGIN_INTERNAL_NAME
            ),
            next(
                event.payload for event in original.events
                if event.id == EVENT_PLUGIN_INTERNAL_NAME
            ),
        )
        self.assertEqual(
            next(event.payload for event in renamed.events if event.id == 213),
            next(event.payload for event in original.events if event.id == 213),
        )

    def test_fl26_three_byte_event_172_does_not_hide_pattern_notes(self) -> None:
        expected_note = note(4, position=48, length=48, key=65, flags=0x4000)
        source = FLPFile(
            FORMAT_PROJECT,
            0,
            96,
            [
                text_event(EVENT_FL_VERSION, "26.1.0.5530", unicode_text=False),
                scalar_event(172, 0x010101),
                text_event(192, "FL Studio 26.1.0.5530.5530"),
                scalar_event(EVENT_PATTERN_NEW, 1),
                data_event(EVENT_PATTERN_NOTES, expected_note.raw),
            ],
        )

        encoded = source.to_bytes()
        parsed = FLPFile.from_bytes(encoded)

        self.assertEqual(parsed.to_bytes(), encoded)
        self.assertEqual(parsed.pattern_notes()[1][0].to_dict(), expected_note.to_dict())

    def test_fl26_windows_zero_padding_round_trip_is_byte_exact(self) -> None:
        source = FLPFile(
            FORMAT_PROJECT,
            0,
            96,
            [
                text_event(EVENT_FL_VERSION, "26.1.0.5530", unicode_text=False),
                data_event(237, bytes(range(16))),
                text_event(231, "Unsorted"),
                scalar_event(146, 0xFFFFFFFF),
            ],
        )
        encoded = source.to_bytes()
        insert_at = 22 + len(source.events[0].raw)
        padded = bytearray(encoded[:insert_at] + b"\0" + encoded[insert_at:])
        padded[18:22] = (len(padded) - 22).to_bytes(4, "little")

        parsed = FLPFile.from_bytes(bytes(padded))

        self.assertEqual(bytes(padded), parsed.to_bytes())
        self.assertEqual(sum(event.id == EVENT_PADDING for event in parsed.events), 1)
        self.assertEqual(parsed.fl_version, "26.1.0.5530")

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
    def test_float_preview_duration_matches_fl_studio_mac_render(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = root / "preview.wav"
            write_float_silence(preview, duration_seconds=1.25)
            capsule = Capsule.build(
                root / "Float.flcapsule", name="Float", project=fixture_project(),
                channel_ids=[2], pattern_id=3, preview_wav=preview,
            )

            self.assertAlmostEqual(capsule.preview_duration_seconds(), 1.25)

    def test_rf64_preview_remains_playable_and_extracts_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = root / "preview.wav"
            write_rf64_silence(preview)
            capsule = Capsule.build(
                root / "RF64.flcapsule.wav", name="RF64", project=fixture_project(),
                channel_ids=[2], pattern_id=3, preview_wav=preview,
            )

            capsule.verify()
            self.assertEqual(capsule.path.read_bytes()[:4], b"RF64")
            self.assertEqual(capsule.export_preview(root / "out.wav").read_bytes(), preview.read_bytes())

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
            self.assertEqual(manifest.schema_version, 2)
            self.assertEqual(manifest.source_tempo_bpm, 130.0)
            self.assertEqual(manifest.tags, ["dark", "lead"])
            self.assertEqual([channel.source_iid for channel in manifest.channels], [2, 5])
            self.assertEqual(
                [channel.name for channel in manifest.channels], ["Serum Lead", "Kick"]
            )
            self.assertEqual(capsule.read_channel_state(manifest.channels[0]).format, 0x20)
            self.assertEqual(capsule.read_notes(manifest.channels[0])[0].to_dict()["mod_y"], 55)
            extracted_preview = capsule.extract_preview(root / "cache")
            self.assertEqual(extracted_preview.read_bytes(), preview.read_bytes())
            self.assertEqual(capsule.path.read_bytes()[:4], b"RIFF")
            with wave.open(str(capsule.path), "rb") as reader:
                self.assertEqual((reader.getnchannels(), reader.getframerate()), (2, 44_100))
            with self.assertRaises(zipfile.BadZipFile):
                zipfile.ZipFile(capsule.path)

    def test_single_channel_title_is_stored_as_the_channel_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = root / "preview.wav"
            write_silence(preview)

            capsule = Capsule.build(
                root / "Custom.flcapsule", name="Custom title",
                project=fixture_project(), channel_ids=[2], pattern_id=3,
                preview_wav=preview,
            )

            self.assertEqual(capsule.manifest.name, "Custom title")
            self.assertEqual(capsule.manifest.channels[0].name, "Custom title")

    def test_playable_capsule_rejects_plain_wav_and_corrupt_payload_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = root / "preview.wav"
            write_silence(preview)
            with self.assertRaisesRegex(ValueError, "does not contain Sound Capsule data"):
                Capsule(preview).verify()

            capsule = Capsule.build(
                root / "Lead.flcapsule.wav", name="Lead", project=fixture_project(),
                channel_ids=[2], pattern_id=3, preview_wav=preview,
            )
            contents = bytearray(capsule.path.read_bytes())
            header = contents.index(b"FLCAPS01")
            contents[header + 16] ^= 0x01
            capsule.path.write_bytes(contents)
            with self.assertRaisesRegex(ValueError, "payload checksum mismatch"):
                capsule.verify()

    def test_playable_capsule_rejects_duplicate_and_nonfinal_scap_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = root / "preview.wav"
            write_silence(preview)
            capsule = Capsule.build(
                root / "Lead.flcapsule.wav", name="Lead", project=fixture_project(),
                channel_ids=[2], pattern_id=3, preview_wav=preview,
            )
            original = capsule.path.read_bytes()
            scap_offset = original.index(b"FLCAPS01") - 8
            duplicated = bytearray(original + original[scap_offset:])
            struct.pack_into("<I", duplicated, 4, len(duplicated) - 8)
            capsule.path.write_bytes(duplicated)
            with self.assertRaisesRegex(ValueError, "duplicate SCAP"):
                capsule.verify()

            nonfinal = bytearray(original + b"JUNK\0\0\0\0")
            struct.pack_into("<I", nonfinal, 4, len(nonfinal) - 8)
            capsule.path.write_bytes(nonfinal)
            with self.assertRaisesRegex(ValueError, "SCAP must be the final"):
                capsule.verify()

    def test_metadata_rewrite_preserves_the_exact_audio_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = root / "preview.wav"
            write_silence(preview)
            capsule = Capsule.build(
                root / "Lead.flcapsule.wav", name="Lead", project=fixture_project(),
                channel_ids=[2], pattern_id=3, preview_wav=preview,
            )
            manifest = capsule.manifest
            manifest.tags = ["rewritten"]
            capsule.replace_manifest(manifest)

            extracted = capsule.export_preview(root / "after.wav")
            self.assertEqual(extracted.read_bytes(), preview.read_bytes())
            self.assertEqual(capsule.manifest.tags, ["rewritten"])
            capsule.verify()

    def test_legacy_capsule_converts_to_playable_without_changing_contents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = root / "preview.wav"
            write_silence(preview)
            legacy = Capsule.build(
                root / "Lead.flcapsule", name="Lead", project=fixture_project(),
                channel_ids=[2], pattern_id=3, preview_wav=preview,
            )
            make_legacy_capsule(legacy)
            original_manifest = legacy.manifest.to_dict()

            converted = legacy.convert_to_playable(root / "Lead.flcapsule.wav")

            self.assertEqual(converted.container_format, "playable")
            self.assertEqual(converted.manifest.to_dict(), original_manifest)
            self.assertEqual(converted.export_preview(root / "converted.wav").read_bytes(), preview.read_bytes())
            self.assertTrue(legacy.path.exists())

    def test_schema_one_capsule_remains_readable_without_tempo(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = root / "preview.wav"
            write_silence(preview)
            capsule = Capsule.build(
                root / "Legacy.flcapsule", name="Legacy", project=fixture_project(),
                channel_ids=[2], pattern_id=3, preview_wav=preview,
            )
            make_legacy_capsule(capsule)
            with zipfile.ZipFile(capsule.path) as source:
                members = {
                    name: source.read(name)
                    for name in source.namelist() if name != "checksums.json"
                }
            manifest = json.loads(members["manifest.json"])
            manifest["schema_version"] = 1
            manifest.pop("source_tempo_bpm", None)
            members["manifest.json"] = json.dumps(manifest).encode()
            checksums = {
                name: hashlib.sha256(data).hexdigest() for name, data in members.items()
            }
            with zipfile.ZipFile(capsule.path, "w") as target:
                for name, data in members.items():
                    target.writestr(name, data)
                target.writestr("checksums.json", json.dumps(checksums))

            capsule.verify()
            self.assertEqual(capsule.manifest.schema_version, 1)
            self.assertIsNone(capsule.manifest.source_tempo_bpm)

    def test_capsule_rejects_newer_schema_even_with_valid_checksums(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = root / "preview.wav"
            write_silence(preview)
            capsule = Capsule.build(
                root / "Lead.flcapsule", name="Lead", project=fixture_project(),
                channel_ids=[2], pattern_id=3, preview_wav=preview,
            )
            make_legacy_capsule(capsule)
            with zipfile.ZipFile(capsule.path) as source:
                members = {name: source.read(name) for name in source.namelist() if name != "checksums.json"}
            manifest = json.loads(members["manifest.json"])
            manifest["schema_version"] = 3
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
            make_legacy_capsule(capsule)
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
