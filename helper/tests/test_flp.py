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
    AUTOMATION_BINDING_STRUCT,
    REMOTE_CONTROLLER_STRUCT,
    AutomationBinding,
    AutomationConnection,
    EVENT_ARRANGEMENT_NEW,
    EVENT_AUTOMATION_BINDINGS,
    EVENT_AUTOMATION_POINTS,
    EVENT_CHANNEL_NEW,
    EVENT_CHANNEL_ENABLED,
    EVENT_CHANNEL_ROUTED_TO,
    EVENT_CHANNEL_SAMPLE_PATH,
    EVENT_CHANNEL_TYPE,
    EVENT_CURRENT_PATTERN,
    EVENT_CURRENT_ARRANGEMENT,
    EVENT_FL_VERSION,
    EVENT_EFFECT_SLOT_INDEX,
    EVENT_INSERT_ACTIVE,
    EVENT_INSERT_COLOR,
    EVENT_INSERT_FLAGS,
    EVENT_INSERT_ICON,
    EVENT_INSERT_INPUT,
    EVENT_INSERT_NAME,
    EVENT_INSERT_OUTPUT,
    EVENT_INSERT_ROUTING,
    EVENT_MIXER_PARAMS,
    EVENT_PATTERN_NAME,
    EVENT_PATTERN_NEW,
    EVENT_PATTERN_NOTES,
    EVENT_PLAYLIST,
    EVENT_PLAYLIST_SELECTION,
    EVENT_PROJECT_LOOP_MODE,
    EVENT_REMOTE_CONTROLLER,
    EVENT_PADDING,
    EVENT_PLUGIN_INTERNAL_NAME,
    EVENT_PLUGIN_LOCATION,
    EVENT_PLUGIN_NAME,
    EVENT_TEMPO,
    FLPFile,
    FORMAT_PROJECT,
    MIXER_PARAM_STRUCT,
    MIXER_PARAM_SLOT_ENABLED,
    MIXER_PARAM_SLOT_MIX,
    MixerParamRecord,
    NOTE_STRUCT,
    PlaylistItem,
    RemoteControllerLink,
    NoteRecord,
    PORTABLE_MIXER_PARAM_IDS,
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
    project = FLPFile(FORMAT_PROJECT, 2, ppq, events)
    _add_fixture_mixer(project)
    return project


def _add_fixture_mixer(project: FLPFile) -> None:
    def flags_payload(flags: int = 12) -> bytes:
        return struct.pack("<III", 0, flags, 0)

    def insert_events(index: int, *, current: bool = False) -> list:
        events = []
        if index > 0 or current:
            events.extend(
                [
                    scalar_event(EVENT_INSERT_INPUT, 0xFFFFFFFF),
                    scalar_event(EVENT_INSERT_OUTPUT, 0xFFFFFFFF),
                ]
            )
        active = index in {7, 12}
        if active:
            events.extend(
                [
                    scalar_event(EVENT_INSERT_COLOR, 0x006C665E + index),
                    scalar_event(EVENT_INSERT_ACTIVE, 1),
                    *(
                        [scalar_event(EVENT_INSERT_ICON, 42)]
                        if index == 7
                        else []
                    ),
                    text_event(
                        EVENT_INSERT_NAME,
                        "Lead Insert" if index == 7 else "Kick Insert",
                    ),
                ]
            )
        else:
            events.append(scalar_event(EVENT_INSERT_ACTIVE, 0))
        events.append(
            data_event(
                EVENT_INSERT_FLAGS,
                flags_payload(13 if index == 7 else 12),
            )
        )
        if active:
            events.extend(
                [
                    text_event(
                        EVENT_PLUGIN_INTERNAL_NAME,
                        "Fruity Parametric EQ 2" if index == 7 else "Fruity Reeverb 2",
                    ),
                    data_event(
                        EVENT_PLUGIN_LOCATION,
                        struct.pack("<13I", index, 0, 2, *([0] * 10)),
                    ),
                    data_event(213, b"fixture-effect-state-" + bytes((index,))),
                ]
            )
        events.append(scalar_event(EVENT_EFFECT_SLOT_INDEX, 0))
        events.extend(
            scalar_event(EVENT_EFFECT_SLOT_INDEX, slot) for slot in range(1, 10)
        )
        events.append(data_event(EVENT_INSERT_ROUTING, b"\x00" if index == 0 else b"\x01"))
        return events

    mixer_events = []
    for index in range(17):
        mixer_events.extend(insert_events(index))
    mixer_events.extend(insert_events(17, current=True))

    params = []
    defaults = {
        192: 12_800,
        193: 0,
        194: 0,
        208: 0,
        209: 0,
        210: 0,
        216: 5_777,
        217: 33_145,
        218: 55_825,
        224: 17_500,
        225: 17_500,
        226: 17_500,
    }
    for index in range(17):
        key = 64 + index
        for slot in range(10):
            channel_data = (key << 6) | slot
            params.append(MIXER_PARAM_STRUCT.pack(b"\0" * 4, 0, 31, channel_data, 1))
            mix = 9_600 if index == 7 and slot == 0 else 12_800
            params.append(MIXER_PARAM_STRUCT.pack(b"\0" * 4, 1, 31, channel_data, mix))
        for parameter_id, value in defaults.items():
            if index == 7 and parameter_id == 192:
                value = 11_200
            if index == 7 and parameter_id == 193:
                value = -640
            if index == 7 and parameter_id == 194:
                value = -3_200
            if index == 7 and parameter_id == 208:
                value = 1_500
            if index == 7 and parameter_id == 216:
                value = 6_000
            params.append(
                MIXER_PARAM_STRUCT.pack(
                    b"\0" * 4, parameter_id, 31, key << 6, value
                )
            )
    project.events.extend([*mixer_events, data_event(EVENT_MIXER_PARAMS, b"".join(params))])


def playlist_item(
    channel_iid: int, *, position: int, length: int, item_size: int = 60,
    track: int = 1,
) -> bytes:
    raw = bytearray(item_size)
    struct.pack_into(
        "<IHHIHH", raw, 0, position, 20_480, channel_iid, length,
        500 - track, 0,
    )
    raw[16:20] = bytes((120, 0, 64, 0))
    raw[20:24] = bytes((64, 100, 128, 128))
    struct.pack_into("<ff", raw, 24, 0.0, 0.0)
    return bytes(raw)


def pattern_playlist_item(
    pattern_id: int, *, position: int, length: int, item_size: int = 60,
    track: int = 1,
) -> bytes:
    return PlaylistItem.synthetic_pattern(
        pattern_id,
        position=position,
        length=length,
        item_size=item_size,
    ).with_playlist_track(track).raw


def automation_points(*points: tuple[float, float, float]) -> bytes:
    payload = bytearray(21)
    payload[:4] = b"\x01\x00\x00\x00"
    struct.pack_into("<I", payload, 17, len(points))
    previous = 0.0
    for position, value, tension in points:
        payload.extend(
            struct.pack("<ddf4s", position - previous, value, tension, b"\0" * 4)
        )
        previous = position
    payload.extend(b"\0" * 112)
    return bytes(payload)


def remote_controller_link(
    source_iid: int, target_event_id: int, *, marker: int = 1
) -> bytes:
    return REMOTE_CONTROLLER_STRUCT.pack(
        b"\x00\x00",
        source_iid,
        bytes((marker, 0, 0, 0)),
        target_event_id,
        struct.pack("<II", 8, 469),
    )


def add_automation_target_binding(
    project: FLPFile, target_event_id: int, *, initial_value: int = 0
) -> None:
    event = next(
        item for item in project.events if item.id == EVENT_AUTOMATION_BINDINGS
    )
    records = AutomationBinding.parse_many(event.payload)
    if any(record.target_event_id == target_event_id for record in records):
        return
    project.events[project.events.index(event)] = event.with_payload(
        event.payload
        + AUTOMATION_BINDING_STRUCT.pack(0, target_event_id, initial_value)
    )


def fixture_project_with_automation(
    *, ppq: int = 96, playlist_item_size: int = 60
) -> FLPFile:
    project = fixture_project(ppq=ppq)
    pattern_at = next(
        index for index, event in enumerate(project.events)
        if event.id == EVENT_PATTERN_NEW
    )
    project.events[pattern_at:pattern_at] = [
        scalar_event(EVENT_CHANNEL_NEW, 9),
        scalar_event(EVENT_CHANNEL_TYPE, 5),
        text_event(EVENT_PLUGIN_INTERNAL_NAME, "Automation Clip"),
        text_event(EVENT_PLUGIN_NAME, "Serum macro sweep"),
        data_event(
            EVENT_AUTOMATION_POINTS,
            automation_points((0.0, 0.25, 0.0), (4.0, 0.75, 0.5)),
        ),
        data_event(218, b"opaque-automation-state"),
    ]
    first_channel = next(
        index for index, event in enumerate(project.events)
        if event.id == EVENT_CHANNEL_NEW
    )
    project.events.insert(
        first_channel,
        data_event(
            EVENT_AUTOMATION_BINDINGS,
            AUTOMATION_BINDING_STRUCT.pack(0, (2 << 16) | 0x80D5, 0),
        ),
    )
    project.events.extend(
        [
            scalar_event(EVENT_ARRANGEMENT_NEW, 0),
            data_event(
                EVENT_PLAYLIST,
                pattern_playlist_item(
                    3, position=960, length=384, item_size=playlist_item_size
                )
                + playlist_item(
                    9, position=960, length=384, item_size=playlist_item_size
                ),
            ),
            scalar_event(EVENT_CURRENT_ARRANGEMENT, 0),
        ]
    )
    project.channel_count = 3
    return project


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


class MixerInsertFLPTests(unittest.TestCase):
    def test_fl25_and_fl26_use_the_supported_insert_parameter_mapping(self) -> None:
        for version in ("25.2.5.5055", "26.1.0.5294"):
            project = fixture_project()
            version_event = next(
                event for event in project.events if event.id == EVENT_FL_VERSION
            )
            project.events[project.events.index(version_event)] = text_event(
                EVENT_FL_VERSION, version, unicode_text=False
            )

            parsed = FLPFile.from_bytes(project.to_bytes())
            state = parsed.mixer_insert_state(7)

            self.assertEqual(parsed._mixer_param_key(0), 64)
            self.assertEqual(parsed._mixer_param_key(16), 80)
            state.validate_mixer_insert_state()

    def test_gapped_slots_bypass_mix_and_parameter_remapping_are_lossless(self) -> None:
        source = fixture_project()
        source_insert = {
            section.index: section for section in source.mixer_insert_sections()
        }[7]
        slot_two = next(
            event
            for event in source_insert.events
            if event.id == EVENT_EFFECT_SLOT_INDEX and event.scalar == 2
        )
        expanded_events = []
        for event in source_insert.events:
            if event is slot_two:
                expanded_events.extend(
                    [
                        text_event(EVENT_PLUGIN_INTERNAL_NAME, "Fruity Delay 3"),
                        data_event(
                            EVENT_PLUGIN_LOCATION,
                            struct.pack("<13I", 7, 2, 2, *([0] * 10)),
                        ),
                        data_event(213, b"gapped-slot-two-state"),
                    ]
                )
            expanded_events.append(event)
        source._replace_mixer_insert_events(source_insert, expanded_events)
        params = source._mixer_params_event()
        rebuilt_params = []
        for record in MixerParamRecord.parse_many(params.payload):
            prefix, parameter_id, marker, channel_data, value = record.values
            if record.insert_key == 71 and record.slot_index == 2:
                if parameter_id == 0:
                    value = 0
                elif parameter_id == 1:
                    value = 5_000
            rebuilt_params.append(
                MIXER_PARAM_STRUCT.pack(
                    prefix, parameter_id, marker, channel_data, value
                )
            )
        params_at = source.events.index(params)
        source.events[params_at] = params.with_payload(b"".join(rebuilt_params))

        state = source.mixer_insert_state(7)
        destination = fixture_project()
        restored, allocated = destination.restore_mixer_insert_states([(state, [2])])

        self.assertEqual(allocated, [1])
        insert = {
            section.index: section for section in restored.mixer_insert_sections()
        }[1]
        self.assertEqual(
            [slot.index for slot in insert.effect_slots], list(range(10))
        )
        self.assertEqual(
            [slot.index for slot in insert.effect_slots if slot.occupied], [0, 2]
        )
        raw_events = b"".join(event.raw for event in insert.events)
        self.assertLess(
            raw_events.index(b"fixture-effect-state-\x07"),
            raw_events.index(b"gapped-slot-two-state"),
        )
        values = {
            (record.parameter_id, record.slot_index): record.value
            for record in insert.params
        }
        self.assertEqual(values[(0, 2)], 0)
        self.assertEqual(values[(1, 2)], 5_000)
        self.assertTrue(all(record.insert_key == 65 for record in insert.params))
        locations = [
            (slot.index, struct.unpack_from("<III", event.payload))
            for slot in insert.effect_slots
            for event in slot.events
            if event.id == EVENT_PLUGIN_LOCATION
        ]
        self.assertEqual(locations, [(0, (1, 0, 2)), (2, (1, 2, 2))])

    def test_extracts_portable_insert_state_with_effects_and_parameters(self) -> None:
        state = fixture_project().mixer_insert_state(7)

        state.validate_mixer_insert_state()
        self.assertEqual(
            [slot.plugin_name for slot in state.mixer_effect_slots() if slot.occupied],
            ["Fruity Parametric EQ 2"],
        )
        self.assertFalse(
            any(
                event.id in {
                    EVENT_INSERT_INPUT,
                    EVENT_INSERT_OUTPUT,
                    EVENT_INSERT_ROUTING,
                }
                for event in state.events
            )
        )
        self.assertIn(b"fixture-effect-state-\x07", state.to_bytes())
        state_ids = [event.id for event in state.events]
        self.assertIn(EVENT_INSERT_NAME, state_ids)
        self.assertIn(EVENT_INSERT_COLOR, state_ids)
        self.assertIn(EVENT_INSERT_ICON, state_ids)
        records = MixerParamRecord.parse_many(state.events[-1].payload)
        values = {(record.parameter_id, record.slot_index): record.value for record in records}
        self.assertEqual(values[(192, 0)], 11_200)
        self.assertEqual(values[(193, 0)], -640)
        self.assertEqual(values[(194, 0)], -3_200)
        self.assertEqual(values[(208, 0)], 1_500)
        self.assertEqual(values[(216, 0)], 6_000)
        self.assertEqual(values[(1, 0)], 9_600)
        flags = next(event for event in state.events if event.id == EVENT_INSERT_FLAGS)
        self.assertEqual(int.from_bytes(flags.payload[4:8], "little"), 13)

    def test_preview_keeps_selected_insert_processing_and_removes_sends(self) -> None:
        project = fixture_project()

        wet = project.isolated_preview_project(
            [2], 3, preserve_mixer_inserts=True
        )
        dry = project.isolated_preview_project(
            [2], 3, preserve_mixer_inserts=False
        )

        self.assertEqual(wet.channel_sections()[0].mixer_insert, 7)
        self.assertEqual(dry.channel_sections()[0].mixer_insert, 0)
        restored = {section.index: section for section in wet.mixer_insert_sections()}[7]
        self.assertEqual(restored.routes_to(), {0})
        self.assertTrue(
            all(
                event.scalar == 0xFFFFFFFF
                for event in restored.events
                if event.id in {EVENT_INSERT_INPUT, EVENT_INSERT_OUTPUT}
            )
        )

    def test_restore_allocates_one_fresh_insert_and_preserves_sharing(self) -> None:
        source = fixture_project()
        destination = fixture_project()
        state = source.mixer_insert_state(7)
        original_insert_seven = {
            section.index: section for section in destination.mixer_insert_sections()
        }[7]

        merged, allocated = destination.restore_mixer_insert_states(
            [(state, [2, 5])]
        )

        self.assertEqual(allocated, [1])
        channels = {section.iid: section for section in merged.channel_sections()}
        self.assertEqual(channels[2].mixer_insert, 1)
        self.assertEqual(channels[5].mixer_insert, 1)
        sections = {section.index: section for section in merged.mixer_insert_sections()}
        self.assertIn(b"fixture-effect-state-\x07", b"".join(e.raw for e in sections[1].events))
        location = next(
            event for event in sections[1].effect_slots[0].events
            if event.id == EVENT_PLUGIN_LOCATION
        )
        self.assertEqual(struct.unpack_from("<III", location.payload), (1, 0, 2))
        self.assertEqual(sections[7].events, original_insert_seven.events)
        values = {
            (record.parameter_id, record.slot_index): record.value
            for record in sections[1].params
        }
        self.assertEqual(values[(192, 0)], 11_200)
        self.assertEqual(values[(193, 0)], -640)
        self.assertEqual(values[(1, 0)], 9_600)

    def test_restore_accepts_windows_generated_default_insert_name(self) -> None:
        source = fixture_project()
        destination = fixture_project()
        target = {
            section.index: section for section in destination.mixer_insert_sections()
        }[1]
        replacement = []
        for event in target.events:
            if event is target.flags_event:
                replacement.append(text_event(EVENT_INSERT_NAME, "Insert 11"))
            replacement.append(event)
        destination._replace_mixer_insert_events(
            target,
            replacement,
        )

        restored, allocated = destination.restore_mixer_insert_states(
            [(source.mixer_insert_state(7), [2])]
        )

        self.assertEqual(allocated, [1])
        self.assertEqual(
            {
                section.iid: section for section in restored.channel_sections()
            }[2].mixer_insert,
            1,
        )

    def test_restore_never_overwrites_effect_under_default_insert_name(self) -> None:
        source = fixture_project()
        destination = fixture_project()
        target = {
            section.index: section for section in destination.mixer_insert_sections()
        }[1]
        slot_zero = next(
            event
            for event in target.events
            if event.id == EVENT_EFFECT_SLOT_INDEX and event.scalar == 0
        )
        replacement = []
        for event in target.events:
            if event is target.flags_event:
                replacement.append(text_event(EVENT_INSERT_NAME, "Insert 11"))
            if event is slot_zero:
                replacement.extend(
                    [
                        text_event(EVENT_PLUGIN_INTERNAL_NAME, "Fruity Reeverb 2"),
                        data_event(
                            EVENT_PLUGIN_LOCATION,
                            struct.pack("<13I", 1, 0, 2, *([0] * 10)),
                        ),
                        data_event(213, b"windows-effect-state"),
                    ]
                )
            replacement.append(event)
        destination._replace_mixer_insert_events(target, replacement)
        protected = {
            section.index: section for section in destination.mixer_insert_sections()
        }[1]
        self.assertFalse(protected.is_pristine())
        protected_events = tuple(event.raw for event in protected.events)
        protected_params = tuple(record.raw for record in protected.params)

        restored, allocated = destination.restore_mixer_insert_states(
            [(source.mixer_insert_state(7), [2])]
        )

        self.assertEqual(allocated, [2])
        after = {
            section.index: section for section in restored.mixer_insert_sections()
        }[1]
        self.assertEqual(tuple(event.raw for event in after.events), protected_events)
        self.assertEqual(tuple(record.raw for record in after.params), protected_params)
        self.assertIn(b"windows-effect-state", b"".join(event.raw for event in after.events))

    def test_restore_preserves_unrelated_raw_events(self) -> None:
        destination = fixture_project()
        opaque = data_event(252, b"unrelated-future-project-state\x00\xff")
        destination.events.insert(0, opaque)

        restored, _ = destination.restore_mixer_insert_states(
            [(destination.mixer_insert_state(7), [2])]
        )

        self.assertIn(opaque.raw, [event.raw for event in restored.events])

    def test_restore_preserves_destination_layout_flags(self) -> None:
        source = fixture_project()
        source_section = {
            section.index: section for section in source.mixer_insert_sections()
        }[7]
        source_flags = source_section.flags_event
        source_payload = bytearray(source_flags.payload)
        source_payload[4:8] = (0x1003).to_bytes(4, "little")
        source._replace_mixer_insert_events(
            source_section,
            [
                source_flags.with_payload(bytes(source_payload))
                if event is source_flags
                else event
                for event in source_section.events
            ],
        )
        state = source.mixer_insert_state(7)

        destination = fixture_project()
        target_section = {
            section.index: section for section in destination.mixer_insert_sections()
        }[1]
        target_flags = target_section.flags_event
        target_payload = bytearray(target_flags.payload)
        target_payload[4:8] = (0x400C).to_bytes(4, "little")
        destination._replace_mixer_insert_events(
            target_section,
            [
                target_flags.with_payload(bytes(target_payload))
                if event is target_flags
                else event
                for event in target_section.events
            ],
        )

        merged, _ = destination.restore_mixer_insert_states([(state, [2])])
        restored_flags = {
            section.index: section for section in merged.mixer_insert_sections()
        }[1].flags_event
        self.assertEqual(int.from_bytes(restored_flags.payload[4:8], "little"), 0x4003)

    def test_restore_fails_without_mutating_when_pristine_inserts_are_insufficient(self) -> None:
        destination = fixture_project()
        before = destination.to_bytes()
        state = destination.mixer_insert_state(7)

        with self.assertRaisesRegex(RuntimeError, "15 pristine mixer inserts"):
            destination.restore_mixer_insert_states([(state, [2])] * 15)

        self.assertEqual(destination.to_bytes(), before)

    def test_master_insert_is_never_exported(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Master mixer state"):
            fixture_project().mixer_insert_state(0)


class FLPRoundTripTests(unittest.TestCase):
    def test_channel_mixer_route_uses_fl_event_104_without_an_index_offset(self) -> None:
        project = fixture_project()
        routes = [
            next(
                event for event in section.events
                if event.id == EVENT_CHANNEL_ROUTED_TO
            )
            for section in project.channel_sections()
        ]

        self.assertEqual(EVENT_CHANNEL_ROUTED_TO, 104)
        self.assertEqual([event.id for event in routes], [104, 104])
        self.assertEqual([event.scalar for event in routes], [7, 12])
        self.assertEqual(
            [section.mixer_insert for section in FLPFile.from_bytes(project.to_bytes()).channel_sections()],
            [7, 12],
        )

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


class PlaylistPhraseFLPTests(unittest.TestCase):
    @staticmethod
    def project_with_playlist(*items: bytes) -> FLPFile:
        project = fixture_project()
        project.events.extend(
            [
                scalar_event(EVENT_ARRANGEMENT_NEW, 0),
                data_event(EVENT_PLAYLIST, b"".join(items)),
                scalar_event(EVENT_CURRENT_ARRANGEMENT, 0),
            ]
        )
        return project

    def test_fl25_and_fl26_clip_offsets_parse_losslessly(self) -> None:
        for item_size in (60, 88):
            channel_raw = bytearray(
                playlist_item(9, position=96, length=384, item_size=item_size)
            )
            struct.pack_into("<ff", channel_raw, 24, 1.25, 5.25)
            pattern_raw = bytearray(
                pattern_playlist_item(
                    3, position=192, length=384, item_size=item_size
                )
            )
            struct.pack_into("<II", pattern_raw, 24, 48, 432)

            items = PlaylistItem.parse_many(bytes(channel_raw) + bytes(pattern_raw))

            self.assertEqual(items[0].raw, bytes(channel_raw))
            self.assertEqual(struct.unpack_from("<ff", items[0].raw, 24), (1.25, 5.25))
            self.assertEqual(items[1].raw, bytes(pattern_raw))
            self.assertEqual(struct.unpack_from("<II", items[1].raw, 24), (48, 432))

    def test_fl26_synthetic_and_adapted_placements_use_audible_tail_defaults(self) -> None:
        synthetic = PlaylistItem.synthetic_pattern(
            3, position=0, length=384, item_size=88, runtime_id=4
        )
        adapted = PlaylistItem(
            pattern_playlist_item(3, position=0, length=384, item_size=60)
        ).adapt_size(88)

        for item in (synthetic, adapted):
            self.assertEqual(struct.unpack_from("<d", item.raw, 64)[0], 1.0)
            self.assertEqual(struct.unpack_from("<I", item.raw, 76)[0], 0xFFFFFFFF)

    def test_playlist_selection_takes_priority_and_crops_repetitions(self) -> None:
        project = self.project_with_playlist(
            pattern_playlist_item(3, position=100, length=100),
            pattern_playlist_item(3, position=260, length=100),
            pattern_playlist_item(3, position=500, length=100),
        )
        project.events.append(
            data_event(EVENT_PLAYLIST_SELECTION, struct.pack("<II", 120, 330))
        )

        window = project.resolve_playlist_capture_window(3, playhead=550)

        self.assertEqual((window.start, window.end, window.source), (120, 330, "selection"))
        self.assertEqual(
            [(item.position, item.length) for item in window.pattern_items],
            [(0, 80), (140, 70)],
        )
        self.assertEqual(
            [struct.unpack_from("<II", item.raw, 24) for item in window.pattern_items],
            [(20, 100), (0, 70)],
        )

    def test_playhead_uses_containing_then_nearest_earlier_on_ties(self) -> None:
        project = self.project_with_playlist(
            pattern_playlist_item(3, position=100, length=100),
            pattern_playlist_item(3, position=300, length=100),
        )

        containing = project.resolve_playlist_capture_window(3, playhead=350)
        tied = project.resolve_playlist_capture_window(3, playhead=250)

        self.assertEqual((containing.start, containing.end), (300, 400))
        self.assertEqual((tied.start, tied.end), (100, 200))

    def test_standalone_pattern_window_is_synthesized_at_playhead(self) -> None:
        project = fixture_project()

        window = project.resolve_playlist_capture_window(
            3, playhead=720, pattern_length_steps=16
        )

        self.assertEqual((window.start, window.end, window.source), (720, 1104, "standalone"))
        self.assertEqual(
            [(item.pattern_id, item.position, item.length) for item in window.pattern_items],
            [(3, 0, 384)],
        )

    def test_selection_without_current_pattern_fails_clearly(self) -> None:
        project = self.project_with_playlist(
            pattern_playlist_item(3, position=100, length=100)
        )

        with self.assertRaisesRegex(RuntimeError, "contains no Pattern 3 clips"):
            project.resolve_playlist_capture_window(
                3, playhead=0, selection_start=500, selection_end=600
            )

    def test_automation_crop_preserves_curve_offsets_and_opaque_bytes(self) -> None:
        source = PlaylistItem(
            playlist_item(9, position=80, length=300, item_size=88)
        )

        cropped = source.crop_to_window(120, 300, ppq=96)

        self.assertIsNotNone(cropped)
        assert cropped is not None
        self.assertEqual((cropped.position, cropped.length), (0, 180))
        start, end = struct.unpack_from("<ff", cropped.raw, 24)
        self.assertAlmostEqual(start, 40 / 96)
        self.assertAlmostEqual(end, 220 / 96, places=6)
        self.assertEqual(cropped.raw[12:24], source.raw[12:24])
        self.assertEqual(cropped.raw[32:], source.raw[32:])

    def test_automation_carry_seed_samples_the_placed_endpoint_for_fl25_and_26(
        self,
    ) -> None:
        for item_size in (60, 88):
            raw = bytearray(
                playlist_item(
                    9,
                    position=384,
                    length=192,
                    item_size=item_size,
                )
            )
            struct.pack_into("<ff", raw, 24, 1.5, 3.25)
            struct.pack_into("<H", raw, 18, struct.unpack_from("<H", raw, 18)[0] | 0x2000)
            source = PlaylistItem(bytes(raw))

            seed = source.as_automation_carry_seed(12, ppq=96)

            self.assertEqual((seed.position, seed.item_index, seed.length), (0, 12, 1))
            self.assertFalse(seed.muted)
            start, end = struct.unpack_from("<ff", seed.raw, 24)
            self.assertEqual(end, 3.25)
            self.assertLess(start, end)
            self.assertEqual(
                struct.unpack("<I", struct.pack("<f", start))[0] + 1,
                struct.unpack("<I", struct.pack("<f", end))[0],
            )
            self.assertEqual(seed.raw[32:], source.raw[32:])

    def test_one_tick_automation_seed_survives_lower_destination_ppq(self) -> None:
        source = PlaylistItem(
            playlist_item(9, position=0, length=1, item_size=60)
        )

        remapped = source.remap_channel(
            11,
            source_anchor=0,
            destination_anchor=480,
            source_ppq=960,
            destination_ppq=96,
        )

        self.assertEqual((remapped.position, remapped.length), (480, 1))

    def test_phrase_preview_omits_distant_automation_and_marks_range_end(self) -> None:
        project = fixture_project_with_automation()
        playlist_index = project._current_playlist_event_index()
        assert playlist_index is not None
        playlist = project.events[playlist_index]
        project.events[playlist_index] = playlist.with_payload(
            playlist.payload + playlist_item(9, position=10_000, length=384)
        )
        window = project.resolve_playlist_capture_window(
            3, playhead=960, selection_start=960, selection_end=2_000
        )

        preview = project.isolated_preview_project(
            [2, 9], 3, playlist_window=window
        )

        preview_index = preview._current_playlist_event_index()
        assert preview_index is not None
        items = PlaylistItem.parse_many(preview.events[preview_index].payload)
        automation = [item for item in items if not item.is_pattern]
        patterns = [item for item in items if item.is_pattern]
        self.assertEqual([(item.position, item.length) for item in automation], [(0, 384)])
        self.assertEqual(len(patterns), 2)
        self.assertTrue(patterns[-1].muted)
        self.assertEqual(patterns[-1].end_position, window.duration)

    def test_phrase_preview_rebases_saved_playlist_selection_for_rendering(self) -> None:
        project = self.project_with_playlist(
            pattern_playlist_item(3, position=8_256, length=768),
            pattern_playlist_item(3, position=9_024, length=384),
        )
        project.events.append(
            data_event(EVENT_PLAYLIST_SELECTION, struct.pack("<II", 8_064, 9_792))
        )
        window = project.resolve_playlist_capture_window(3, playhead=0)

        preview = project.isolated_preview_project(
            [2], 3, playlist_window=window
        )

        self.assertEqual((window.start, window.end), (8_064, 9_792))
        self.assertEqual(preview.playlist_selection(), (0, 1_728))

    def test_playhead_phrase_preview_clears_stale_playlist_selection(self) -> None:
        project = fixture_project_with_automation()
        project.events.append(
            data_event(EVENT_PLAYLIST_SELECTION, struct.pack("<II", 8_064, 9_792))
        )
        window = project.resolve_playlist_capture_window(
            3,
            playhead=960,
            selection_start=-1,
            selection_end=-1,
        )

        preview = project.isolated_preview_project(
            [2, 9], 3, playlist_window=window
        )

        self.assertEqual(window.source, "playhead")
        self.assertIsNone(preview.playlist_selection())

    def test_append_phrase_scales_ppq_and_keeps_gaps(self) -> None:
        destination = fixture_project(ppq=192)
        items = [
            PlaylistItem(pattern_playlist_item(3, position=0, length=96)),
            PlaylistItem(pattern_playlist_item(3, position=192, length=48)),
        ]

        merged = destination.append_playlist_phrase(
            items,
            source_pattern_id=3,
            destination_pattern_id=3,
            source_ppq=96,
            playlist_anchor=480,
        )

        self.assertEqual(
            [(item.position, item.length) for item in merged.playlist_items_for_pattern(3)],
            [(480, 192), (864, 96)],
        )
        self.assertEqual(
            [item.runtime_id for item in merged.playlist_items_for_pattern(3)],
            [0, 1],
        )

    def test_append_phrase_uses_the_first_empty_playlist_track(self) -> None:
        destination = fixture_project()
        destination.events.extend(
            [
                scalar_event(EVENT_ARRANGEMENT_NEW, 0),
                data_event(
                    EVENT_PLAYLIST,
                    pattern_playlist_item(
                        2, position=0, length=384, track=1
                    )
                    + playlist_item(
                        5, position=0, length=384, track=3
                    ),
                ),
                scalar_event(EVENT_CURRENT_ARRANGEMENT, 0),
            ]
        )

        merged = destination.append_playlist_phrase(
            [PlaylistItem(pattern_playlist_item(3, position=0, length=192))],
            source_pattern_id=3,
            destination_pattern_id=4,
            source_ppq=96,
            playlist_anchor=480,
        )

        imported = merged.playlist_items_for_pattern(4)
        self.assertEqual([item.playlist_track for item in imported], [2])


class AutomationClipFLPTests(unittest.TestCase):
    def test_parses_fl25_fl26_remote_controller_binary_layout_losslessly(self) -> None:
        raw = bytes.fromhex(
            "0000220000000000011fc87108000000d5010000"
        )

        link = RemoteControllerLink.parse(raw)

        self.assertEqual(link.source_automation_iid, 34)
        self.assertEqual(link.target_event_id, 0x71C81F01)
        self.assertEqual(link.raw, raw)

    def test_reads_automation_points_and_accumulates_delta_positions(self) -> None:
        section = next(
            section
            for section in fixture_project_with_automation().channel_sections()
            if section.iid == 9
        )

        points = section.automation_points()

        self.assertEqual([point.position for point in points], [0.0, 4.0])
        self.assertEqual([point.value for point in points], [0.25, 0.75])
        self.assertEqual([point.tension for point in points], [0.0, 0.5])

    def test_reads_channel_target_and_current_playlist_instances(self) -> None:
        project = fixture_project_with_automation()

        binding = project.automation_bindings()[9]
        playlist = project.playlist_items_for_channels([9])[9]

        self.assertEqual(binding.target_channel_iid({2, 5, 9}), 2)
        self.assertEqual(binding.target_event_id, (2 << 16) | 0x80D5)
        self.assertEqual(len(playlist), 1)
        self.assertEqual((playlist[0].position, playlist[0].length), (960, 384))

    def test_decodes_lossless_primary_and_linked_controller_connections(self) -> None:
        project = fixture_project_with_automation()
        primary = (2 << 16) | 0x80D5
        effect = 0x71C0802A
        add_automation_target_binding(project, effect, initial_value=42)
        add_automation_target_binding(project, 0x70001FC0, initial_value=64)
        project.events.extend(
            [
                data_event(
                    EVENT_REMOTE_CONTROLLER,
                    remote_controller_link(9, primary, marker=1),
                ),
                data_event(
                    EVENT_REMOTE_CONTROLLER,
                    remote_controller_link(9, effect, marker=2),
                ),
                data_event(
                    EVENT_REMOTE_CONTROLLER,
                    remote_controller_link(9, 0x70001FC0, marker=3),
                ),
            ]
        )

        connections = project.automation_connections(9)

        self.assertEqual([item.role for item in connections], ["primary", "linked"])
        self.assertEqual(
            [item.target.kind for item in connections],
            ["generator_parameter", "effect_parameter"],
        )
        self.assertEqual(connections[0].remote_link.raw[4:8], b"\x01\x00\x00\x00")
        remapped = connections[1].remote_link.remap(
            source_automation_iid=17, target_event_id=0x7040802A
        )
        self.assertEqual(remapped.source_automation_iid, 17)
        self.assertEqual(remapped.target_event_id, 0x7040802A)
        self.assertEqual(remapped.raw[:2] + remapped.raw[4:8] + remapped.raw[12:],
                         connections[1].remote_link.raw[:2]
                         + connections[1].remote_link.raw[4:8]
                         + connections[1].remote_link.raw[12:])

    def test_missing_secondary_binding_is_still_classified_and_filtered(self) -> None:
        project = fixture_project_with_automation()
        primary = (2 << 16) | 0x80D5
        master = 0x70001FC0
        project.events.extend(
            [
                data_event(
                    EVENT_REMOTE_CONTROLLER,
                    remote_controller_link(9, primary, marker=1),
                ),
                # FL does not always duplicate secondary Master/global links
                # in event 216. Event 227 still identifies them completely.
                data_event(
                    EVENT_REMOTE_CONTROLLER,
                    remote_controller_link(9, master, marker=2),
                ),
            ]
        )

        records = project.automation_connection_records(9)
        connections = project.automation_connections(9)

        self.assertEqual([item[0] for item in records], ["primary", "linked"])
        self.assertIsNone(records[1][1])
        self.assertEqual(records[1][2].target_event_id, master)
        self.assertEqual(len(connections), 1)
        self.assertEqual(connections[0].target.kind, "generator_parameter")

    def test_binding_resolution_skips_unbound_secondary_link_before_primary(self) -> None:
        project = fixture_project_with_automation()
        primary = (2 << 16) | 0x80D5
        master = 0x70001FC0
        project.events.extend(
            [
                data_event(
                    EVENT_REMOTE_CONTROLLER,
                    remote_controller_link(9, master, marker=1),
                ),
                data_event(
                    EVENT_REMOTE_CONTROLLER,
                    remote_controller_link(9, primary, marker=2),
                ),
            ]
        )

        self.assertEqual(
            project.automation_bindings()[9].target_event_id,
            primary,
        )
        records = project.automation_connection_records(9)
        self.assertEqual([item[0] for item in records], ["primary", "linked"])
        self.assertIsNone(records[1][1])

    def test_arbitrarily_many_links_are_filtered_independently(self) -> None:
        project = fixture_project_with_automation()
        primary = (2 << 16) | 0x80D5
        effect = 0x71C0802A
        add_automation_target_binding(project, effect, initial_value=42)
        unsupported = [
            0x70001FC0 + index for index in range(2, 22)
        ]
        targets = [primary, *unsupported[:10], effect, *unsupported[10:]]
        project.events.extend(
            data_event(
                EVENT_REMOTE_CONTROLLER,
                remote_controller_link(9, target, marker=index + 1),
            )
            for index, target in enumerate(targets)
        )

        records = project.automation_connection_records(9)
        connections = project.automation_connections(9)

        self.assertEqual(len(records), len(targets))
        self.assertEqual(
            [connection.target.kind for connection in connections],
            ["generator_parameter", "effect_parameter"],
        )
        self.assertTrue(all(connection.binding is not None for connection in connections))
        self.assertEqual(
            [
                AutomationConnection.from_bytes(
                    connection.to_bytes(),
                    role=connection.role,
                    target=connection.target,
                ).target_event_id
                for connection in connections
            ],
            [primary, effect],
        )

    def test_event_216_targets_are_deduplicated_across_controller_links(self) -> None:
        project = fixture_project_with_automation()
        primary = (2 << 16) | 0x80D5
        original = next(
            section for section in project.channel_sections() if section.iid == 9
        )
        pattern_at = next(
            index
            for index, event in enumerate(project.events)
            if event.id == EVENT_PATTERN_NEW
        )
        project.events[pattern_at:pattern_at] = original.remap(10).events
        project.channel_count += 1
        project.events.extend(
            [
                data_event(
                    EVENT_REMOTE_CONTROLLER,
                    remote_controller_link(9, primary, marker=1),
                ),
                data_event(
                    EVENT_REMOTE_CONTROLLER,
                    remote_controller_link(10, primary, marker=2),
                ),
            ]
        )

        bindings = project.automation_bindings()

        self.assertEqual(set(bindings), {9, 10})
        self.assertEqual(
            {binding.target_event_id for binding in bindings.values()}, {primary}
        )
        self.assertEqual(
            project.automation_connection_records(10)[0][2].raw[4], 2
        )

    def test_preview_promotes_retained_link_when_primary_is_excluded(self) -> None:
        project = fixture_project_with_automation()
        primary = (2 << 16) | 0x80D5
        effect = 0x71C0802A
        add_automation_target_binding(project, effect, initial_value=42)
        project.events.extend(
            [
                data_event(
                    EVENT_REMOTE_CONTROLLER,
                    remote_controller_link(9, primary, marker=1),
                ),
                data_event(
                    EVENT_REMOTE_CONTROLLER,
                    remote_controller_link(9, effect, marker=2),
                ),
            ]
        )

        preview = project.isolated_preview_project(
            [2, 9],
            3,
            preserve_mixer_inserts=True,
            automation_target_event_ids={9: [effect]},
        )

        self.assertEqual(preview.automation_bindings()[9].target_event_id, effect)
        links = preview.remote_controller_links()[9]
        self.assertEqual([link.target_event_id for link in links], [effect])

    def test_preview_retargets_binding_when_only_retained_link_has_no_table_row(
        self,
    ) -> None:
        project = fixture_project_with_automation()
        master = 0x70001FC0
        effect = 0x71C0802A
        binding_event = next(
            event for event in project.events
            if event.id == EVENT_AUTOMATION_BINDINGS
        )
        project.events[project.events.index(binding_event)] = (
            binding_event.with_payload(
                AUTOMATION_BINDING_STRUCT.pack(0, master, 0)
            )
        )
        project.events.extend(
            [
                data_event(
                    EVENT_REMOTE_CONTROLLER,
                    remote_controller_link(9, master, marker=1),
                ),
                data_event(
                    EVENT_REMOTE_CONTROLLER,
                    remote_controller_link(9, effect, marker=2),
                ),
            ]
        )

        preview = project.isolated_preview_project(
            [2, 9],
            3,
            preserve_mixer_inserts=True,
            automation_target_event_ids={9: [effect]},
        )

        self.assertEqual(preview.automation_bindings()[9].target_event_id, effect)
        self.assertEqual(
            [
                link.target_event_id
                for link in preview.remote_controller_links()[9]
            ],
            [effect],
        )

    def test_classifies_and_remaps_portable_mixer_automation_targets(self) -> None:
        project = fixture_project()

        insert_volume = project.classify_automation_binding(
            AutomationBinding(AUTOMATION_BINDING_STRUCT.pack(0, 0x71C01FC0, 0))
        )
        effect_mix = project.classify_automation_binding(
            AutomationBinding(AUTOMATION_BINDING_STRUCT.pack(0, 0x71C01F01, 0))
        )
        effect_parameter = project.classify_automation_binding(
            AutomationBinding(AUTOMATION_BINDING_STRUCT.pack(0, 0x71C0802A, 0))
        )

        self.assertEqual(
            (insert_volume.kind, insert_volume.source_insert_index, insert_volume.control_id),
            ("insert_control", 7, 192),
        )
        self.assertEqual(
            (effect_mix.kind, effect_mix.slot_index, effect_mix.control_id),
            ("effect_slot_control", 0, 1),
        )
        self.assertEqual(
            (
                effect_parameter.kind,
                effect_parameter.source_insert_index,
                effect_parameter.slot_index,
                effect_parameter.parameter_index,
            ),
            ("effect_parameter", 7, 0, 42),
        )
        self.assertEqual(
            effect_parameter.target_event_id(
                channel_mapping={}, insert_mapping={7: 1}
            ),
            0x7040802A,
        )
        self.assertIsNone(
            project.classify_automation_binding(
                AutomationBinding(AUTOMATION_BINDING_STRUCT.pack(0, 0x70001FC0, 0))
            )
        )
        for control_id in PORTABLE_MIXER_PARAM_IDS - {
            MIXER_PARAM_SLOT_ENABLED,
            MIXER_PARAM_SLOT_MIX,
        }:
            target = project.classify_automation_event_id(
                0x71C00000 + 0x1F00 + control_id
            )
            self.assertIsNotNone(target, control_id)
            self.assertEqual(
                (target.kind, target.source_insert_index, target.control_id),
                ("insert_control", 7, control_id),
            )

    def test_classifies_and_remaps_generator_channel_controls(self) -> None:
        project = fixture_project()

        for control_id in (0, 1, 4):
            target = project.classify_automation_event_id((2 << 16) | control_id)
            self.assertEqual(
                (
                    target.kind,
                    target.source_channel_iid,
                    target.parameter_index,
                    target.control_id,
                ),
                ("generator_parameter", 2, None, control_id),
            )
            self.assertEqual(
                target.target_event_id(
                    channel_mapping={2: 11}, insert_mapping={}
                ),
                (11 << 16) | control_id,
            )
        self.assertIsNone(project.classify_automation_event_id((2 << 16) | 5))

    def test_reads_fl26_88_byte_playlist_items(self) -> None:
        project = fixture_project_with_automation(playlist_item_size=88)

        playlist = project.playlist_items_for_channels([9])[9]

        self.assertEqual(len(playlist), 1)
        self.assertEqual(playlist[0].record_size, 88)
        self.assertEqual((playlist[0].item_index, playlist[0].length), (9, 384))

    def test_automation_preview_uses_song_mode_and_normalizes_playlist(self) -> None:
        preview = fixture_project_with_automation().isolated_preview_project([2, 9], 3)

        loop_mode = next(
            event.scalar for event in preview.events
            if event.id == EVENT_PROJECT_LOOP_MODE
        )
        playlist_index = preview._current_playlist_event_index()
        self.assertIsNotNone(playlist_index)
        items = PlaylistItem.parse_many(preview.events[playlist_index].payload)

        self.assertEqual(loop_mode, 1)
        self.assertEqual(len(items), 2)
        pattern = next(item for item in items if item.item_index > item.pattern_base)
        automation = next(item for item in items if item.item_index <= item.pattern_base)
        self.assertEqual((pattern.position, pattern.item_index), (0, pattern.pattern_base + 3))
        self.assertEqual(pattern.raw[24:32], b"\xff" * 8)
        self.assertEqual(pattern.runtime_id, 0)
        self.assertEqual((automation.position, automation.item_index), (0, 9))
        self.assertEqual(
            {note.rack_channel for note in preview.pattern_notes()[3]},
            {2},
        )

    def test_fl26_automation_preview_marks_synthesized_item_as_pattern(self) -> None:
        preview = fixture_project_with_automation(
            playlist_item_size=88
        ).isolated_preview_project([2, 9], 3)

        playlist_index = preview._current_playlist_event_index()
        self.assertIsNotNone(playlist_index)
        pattern = next(
            item
            for item in PlaylistItem.parse_many(preview.events[playlist_index].payload)
            if item.item_index > item.pattern_base
        )

        self.assertEqual(pattern.record_size, 88)
        self.assertEqual(pattern.raw[24:32], b"\xff" * 8)
        self.assertEqual(pattern.runtime_id, 0)

    def test_append_remaps_target_and_places_automation_at_playhead(self) -> None:
        source = fixture_project_with_automation(ppq=96)
        destination = fixture_project(ppq=192)
        destination.events.extend(
            [
                scalar_event(EVENT_ARRANGEMENT_NEW, 0),
                data_event(EVENT_PLAYLIST, b""),
                scalar_event(EVENT_CURRENT_ARRANGEMENT, 0),
            ]
        )
        sections = [
            section for section in source.channel_sections()
            if section.iid in {2, 9}
        ]

        merged, mapping, _ = destination.append_capsule(
            sections,
            {2: source.pattern_notes()[3]},
            source_ppq=source.ppq,
            pattern_name="Imported",
            automation_bindings=source.automation_bindings(),
            automation_playlist_items=source.playlist_items_for_channels([9]),
            playlist_anchor=480,
        )

        self.assertEqual(mapping, {2: 6, 9: 7})
        binding = merged.automation_bindings()[7]
        self.assertEqual(binding.target_channel_iid({2, 5, 6, 7}), 6)
        item = merged.playlist_items_for_channels([7])[7][0]
        self.assertEqual((item.position, item.length), (480, 768))

    def test_append_adapts_automation_to_destination_playlist_layout(self) -> None:
        source = fixture_project_with_automation(playlist_item_size=60)
        destination = fixture_project()
        destination.events.extend(
            [
                scalar_event(EVENT_ARRANGEMENT_NEW, 0),
                data_event(
                    EVENT_PLAYLIST,
                    playlist_item(5, position=0, length=384, item_size=88),
                ),
                scalar_event(EVENT_CURRENT_ARRANGEMENT, 0),
            ]
        )
        sections = [
            section for section in source.channel_sections()
            if section.iid in {2, 9}
        ]

        merged, mapping, _ = destination.append_capsule(
            sections,
            {2: source.pattern_notes()[3]},
            source_ppq=source.ppq,
            pattern_name="Imported",
            automation_bindings=source.automation_bindings(),
            automation_playlist_items=source.playlist_items_for_channels([9]),
            playlist_anchor=480,
        )

        imported = merged.playlist_items_for_channels([mapping[9]])[mapping[9]][0]
        self.assertEqual(imported.record_size, 88)
        self.assertEqual((imported.position, imported.length), (480, 384))


class CapsuleTests(unittest.TestCase):
    def test_schema_four_embeds_each_distinct_selected_insert_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = root / "preview.wav"
            write_silence(preview)
            capsule = Capsule.build(
                root / "Mixed.flcapsule",
                name="Mixed",
                project=fixture_project(),
                channel_ids=[2, 5],
                pattern_id=3,
                preview_wav=preview,
                include_mixer_insert=True,
            )

            capsule.verify()
            inserts = capsule.manifest.mixer_inserts
            self.assertEqual([insert.source_index for insert in inserts], [7, 12])
            self.assertEqual(
                [insert.channel_source_iids for insert in inserts], [[2], [5]]
            )
            for insert in inserts:
                capsule.read_mixer_insert_state(insert).validate_mixer_insert_state()

    def test_schema_four_preserves_shared_insert_associations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = root / "preview.wav"
            write_silence(preview)
            project = fixture_project()
            channel = {section.iid: section for section in project.channel_sections()}[5]
            project._replace_channel_events(
                channel, channel.with_mixer_insert(7).events
            )

            capsule = Capsule.build(
                root / "Shared.flcapsule",
                name="Shared",
                project=project,
                channel_ids=[2, 5],
                pattern_id=3,
                preview_wav=preview,
                include_mixer_insert=True,
            )

            self.assertEqual(len(capsule.manifest.mixer_inserts), 1)
            insert = capsule.manifest.mixer_inserts[0]
            self.assertEqual(insert.source_index, 7)
            self.assertEqual(insert.channel_source_iids, [2, 5])

    def test_mixer_capture_can_be_disabled_and_never_embeds_master(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = root / "preview.wav"
            write_silence(preview)
            project = fixture_project()
            channel = {section.iid: section for section in project.channel_sections()}[2]
            project._replace_channel_events(
                channel, channel.with_mixer_insert(0).events
            )

            disabled = Capsule.build(
                root / "Disabled.flcapsule",
                name="Disabled",
                project=fixture_project(),
                channel_ids=[2],
                pattern_id=3,
                preview_wav=preview,
                include_mixer_insert=False,
            )
            master = Capsule.build(
                root / "Master.flcapsule",
                name="Master",
                project=project,
                channel_ids=[2],
                pattern_id=3,
                preview_wav=preview,
                include_mixer_insert=True,
            )

            self.assertFalse(disabled.manifest.mixer_inserts)
            self.assertFalse(master.manifest.mixer_inserts)

    def test_verification_rejects_missing_mixer_insert_member(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = root / "preview.wav"
            write_silence(preview)
            capsule = Capsule.build(
                root / "Broken.flcapsule",
                name="Broken",
                project=fixture_project(),
                channel_ids=[2],
                pattern_id=3,
                preview_wav=preview,
                include_mixer_insert=True,
            )
            make_legacy_capsule(capsule)
            missing = capsule.manifest.mixer_inserts[0].state_path
            with zipfile.ZipFile(capsule.path) as source:
                members = {
                    name: source.read(name)
                    for name in source.namelist()
                    if name != missing
                }
            with zipfile.ZipFile(capsule.path, "w") as target:
                for name, data in members.items():
                    target.writestr(name, data)

            with self.assertRaisesRegex(ValueError, "missing required members"):
                capsule.verify()

    def test_verification_rejects_structurally_corrupt_mixer_insert_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = root / "preview.wav"
            write_silence(preview)
            capsule = Capsule.build(
                root / "Corrupt.flcapsule",
                name="Corrupt",
                project=fixture_project(),
                channel_ids=[2],
                pattern_id=3,
                preview_wav=preview,
                include_mixer_insert=True,
            )
            make_legacy_capsule(capsule)
            state_path = capsule.manifest.mixer_inserts[0].state_path
            with zipfile.ZipFile(capsule.path) as source:
                members = {
                    name: source.read(name)
                    for name in source.namelist()
                    if name != "checksums.json"
                }
            state = FLPFile.from_bytes(members[state_path])
            flags = next(
                event for event in state.events if event.id == EVENT_INSERT_FLAGS
            )
            payload = bytearray(flags.payload)
            payload[4:8] = (
                int.from_bytes(payload[4:8], "little") | 0x40
            ).to_bytes(4, "little")
            state.events[state.events.index(flags)] = flags.with_payload(bytes(payload))
            members[state_path] = state.to_bytes()
            checksums = {
                name: hashlib.sha256(data).hexdigest()
                for name, data in members.items()
            }
            with zipfile.ZipFile(capsule.path, "w") as target:
                for name, data in members.items():
                    target.writestr(name, data)
                target.writestr("checksums.json", json.dumps(checksums))

            with self.assertRaisesRegex(ValueError, "non-portable flags"):
                capsule.verify()

    def test_capsule_preserves_selected_automation_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = root / "preview.wav"
            write_silence(preview)
            capsule = Capsule.build(
                root / "Automated.flcapsule",
                name="Automated",
                project=fixture_project_with_automation(),
                channel_ids=[2, 9],
                pattern_id=3,
                preview_wav=preview,
            )

            capsule.verify()
            self.assertEqual(capsule.manifest.schema_version, 6)
            self.assertEqual(len(capsule.manifest.automations), 1)
            automation = capsule.manifest.automations[0]
            self.assertEqual(automation.source_iid, 9)
            self.assertEqual(len(automation.targets), 1)
            target = automation.targets[0]
            self.assertEqual(
                (target.target_kind, target.source_channel_iid, target.parameter_index),
                ("generator_parameter", 2, 213),
            )
            self.assertEqual(capsule.read_automation_binding(automation).target_event_id, (2 << 16) | 0x80D5)
            item = capsule.read_automation_playlist(automation)[0]
            self.assertEqual((item.item_index, item.position, item.length), (9, 0, 384))

    def test_verification_rejects_missing_automation_connection_member(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = root / "preview.wav"
            write_silence(preview)
            capsule = Capsule.build(
                root / "Missing-Connection.flcapsule",
                name="Missing Connection",
                project=fixture_project_with_automation(),
                channel_ids=[2, 9],
                pattern_id=3,
                preview_wav=preview,
            )
            make_legacy_capsule(capsule)
            missing = capsule.manifest.automations[0].targets[0].state_path
            with zipfile.ZipFile(capsule.path) as source:
                members = {
                    name: source.read(name)
                    for name in source.namelist()
                    if name != missing
                }
            with zipfile.ZipFile(capsule.path, "w") as target:
                for name, data in members.items():
                    target.writestr(name, data)

            with self.assertRaisesRegex(ValueError, "missing required members"):
                capsule.verify()

    def test_verification_rejects_corrupt_automation_connection_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = root / "preview.wav"
            write_silence(preview)
            capsule = Capsule.build(
                root / "Corrupt-Connection.flcapsule",
                name="Corrupt Connection",
                project=fixture_project_with_automation(),
                channel_ids=[2, 9],
                pattern_id=3,
                preview_wav=preview,
            )
            make_legacy_capsule(capsule)
            state_path = capsule.manifest.automations[0].targets[0].state_path
            with zipfile.ZipFile(capsule.path) as source:
                members = {
                    name: source.read(name)
                    for name in source.namelist()
                    if name != "checksums.json"
                }
            members[state_path] = b"broken"
            checksums = {
                name: hashlib.sha256(data).hexdigest()
                for name, data in members.items()
            }
            with zipfile.ZipFile(capsule.path, "w") as target:
                for name, data in members.items():
                    target.writestr(name, data)
                target.writestr("checksums.json", json.dumps(checksums))

            with self.assertRaisesRegex(ValueError, "connection state is truncated"):
                capsule.verify()

    def test_schema_five_capsule_preserves_effect_parameter_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = root / "preview.wav"
            write_silence(preview)
            project = fixture_project_with_automation()
            binding_event = next(
                event for event in project.events
                if event.id == EVENT_AUTOMATION_BINDINGS
            )
            project.events[project.events.index(binding_event)] = binding_event.with_payload(
                AUTOMATION_BINDING_STRUCT.pack(0, 0x71C0802A, 0)
            )

            capsule = Capsule.build(
                root / "Effect-Automated.flcapsule",
                name="Effect Automated",
                project=project,
                channel_ids=[2, 9],
                pattern_id=3,
                preview_wav=preview,
            )

            capsule.verify()
            automation = capsule.manifest.automations[0]
            target = automation.targets[0]
            self.assertEqual(
                (
                    target.target_kind,
                    target.source_insert_index,
                    target.slot_index,
                    target.parameter_index,
                ),
                ("effect_parameter", 7, 0, 42),
            )
            self.assertEqual(
                capsule.read_automation_binding(automation).target_event_id,
                0x71C0802A,
            )

    def test_schema_five_preserves_multiple_connections_and_omits_master(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = root / "preview.wav"
            write_silence(preview)
            project = fixture_project_with_automation()
            primary = (2 << 16) | 0x80D5
            add_automation_target_binding(
                project, 0x71C0802A, initial_value=42
            )
            add_automation_target_binding(
                project, 0x70001FC0, initial_value=64
            )
            project.events.extend(
                [
                    data_event(
                        EVENT_REMOTE_CONTROLLER,
                        remote_controller_link(9, primary, marker=1),
                    ),
                    data_event(
                        EVENT_REMOTE_CONTROLLER,
                        remote_controller_link(9, 0x71C0802A, marker=2),
                    ),
                    data_event(
                        EVENT_REMOTE_CONTROLLER,
                        remote_controller_link(9, 0x70001FC0, marker=3),
                    ),
                ]
            )

            capsule = Capsule.build(
                root / "Multi-Target.flcapsule",
                name="Multi Target",
                project=project,
                channel_ids=[2, 9],
                pattern_id=3,
                preview_wav=preview,
            )

            capsule.verify()
            automation = capsule.manifest.automations[0]
            self.assertEqual(
                [(target.role, target.target_kind) for target in automation.targets],
                [
                    ("primary", "generator_parameter"),
                    ("linked", "effect_parameter"),
                ],
            )
            connections = capsule.read_automation_connections(automation)
            self.assertEqual(len(connections), 2)
            self.assertEqual(connections[1].remote_link.raw[4], 2)

    def test_capsule_rejects_mixer_or_global_automation_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = root / "preview.wav"
            write_silence(preview)
            project = fixture_project_with_automation()
            binding = next(
                event for event in project.events
                if event.id == EVENT_AUTOMATION_BINDINGS
            )
            project.events[project.events.index(binding)] = binding.with_payload(
                AUTOMATION_BINDING_STRUCT.pack(0, 0x70401FC0, 0)
            )

            with self.assertRaisesRegex(
                RuntimeError,
                "target is not portable with the selected generators",
            ):
                Capsule.build(
                    root / "Unsupported.flcapsule",
                    name="Unsupported",
                    project=project,
                    channel_ids=[2, 9],
                    pattern_id=3,
                    preview_wav=preview,
                )

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
            self.assertEqual(manifest.schema_version, 6)
            self.assertEqual(manifest.playlist_phrase.duration_ticks, 240)
            self.assertEqual(manifest.source_tempo_bpm, 130.0)
            self.assertEqual(manifest.tags, ["dark", "lead"])
            self.assertEqual([channel.source_iid for channel in manifest.channels], [2, 5])
            self.assertEqual(
                [channel.name for channel in manifest.channels], ["Serum Lead", "Kick"]
            )
            channel_state = capsule.read_channel_state(manifest.channels[0])
            self.assertEqual(channel_state.format, 0x20)
            self.assertEqual(channel_state.events[0].id, EVENT_FL_VERSION)
            self.assertEqual(channel_state.fl_version, "25.2.5.5055")
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

    def test_schemas_one_through_three_remain_readable_without_mixer_state(self) -> None:
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
            original_manifest = json.loads(members["manifest.json"])
            phrase_path = original_manifest["playlist_phrase"]["pattern_playlist_path"]
            members.pop(phrase_path)
            for schema_version in (1, 2, 3):
                manifest = dict(original_manifest)
                manifest["schema_version"] = schema_version
                manifest.pop("mixer_inserts", None)
                manifest.pop("playlist_phrase", None)
                if schema_version == 1:
                    manifest.pop("source_tempo_bpm", None)
                members["manifest.json"] = json.dumps(manifest).encode()
                checksums = {
                    name: hashlib.sha256(data).hexdigest()
                    for name, data in members.items()
                }
                with zipfile.ZipFile(capsule.path, "w") as target:
                    for name, data in members.items():
                        target.writestr(name, data)
                    target.writestr("checksums.json", json.dumps(checksums))

                capsule.verify()
                self.assertEqual(capsule.manifest.schema_version, schema_version)
                self.assertFalse(capsule.manifest.mixer_inserts)
                if schema_version == 1:
                    self.assertIsNone(capsule.manifest.source_tempo_bpm)

    def test_schema_four_automation_remains_readable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = root / "preview.wav"
            write_silence(preview)
            capsule = Capsule.build(
                root / "Legacy-Automation.flcapsule",
                name="Legacy Automation",
                project=fixture_project_with_automation(),
                channel_ids=[2, 9],
                pattern_id=3,
                preview_wav=preview,
            )
            make_legacy_capsule(capsule)
            with zipfile.ZipFile(capsule.path) as source:
                members = {
                    name: source.read(name)
                    for name in source.namelist() if name != "checksums.json"
                }
            manifest = json.loads(members["manifest.json"])
            manifest["schema_version"] = 4
            phrase_path = manifest.pop("playlist_phrase")["pattern_playlist_path"]
            members.pop(phrase_path)
            automation = manifest["automations"][0]
            target = automation.pop("targets")[0]
            automation["target_source_iid"] = target["source_channel_iid"]
            automation["binding_path"] = target["state_path"]
            members[target["state_path"]] = capsule.read_automation_binding(
                capsule.manifest.automations[0]
            ).raw
            members["manifest.json"] = json.dumps(manifest).encode()
            checksums = {
                name: hashlib.sha256(data).hexdigest()
                for name, data in members.items()
            }
            with zipfile.ZipFile(capsule.path, "w") as target_archive:
                for name, data in members.items():
                    target_archive.writestr(name, data)
                target_archive.writestr("checksums.json", json.dumps(checksums))

            capsule.verify()
            legacy = capsule.manifest.automations[0]
            self.assertEqual(capsule.manifest.schema_version, 4)
            self.assertEqual(legacy.target_source_iid, 2)
            self.assertFalse(legacy.targets)

    def test_schema_five_automation_remains_readable_without_phrase(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = root / "preview.wav"
            write_silence(preview)
            capsule = Capsule.build(
                root / "Schema-Five.flcapsule",
                name="Schema Five",
                project=fixture_project_with_automation(),
                channel_ids=[2, 9],
                pattern_id=3,
                preview_wav=preview,
            )
            make_legacy_capsule(capsule)
            with zipfile.ZipFile(capsule.path) as source:
                members = {
                    name: source.read(name)
                    for name in source.namelist() if name != "checksums.json"
                }
            manifest = json.loads(members["manifest.json"])
            manifest["schema_version"] = 5
            phrase_path = manifest.pop("playlist_phrase")["pattern_playlist_path"]
            members.pop(phrase_path)
            members["manifest.json"] = json.dumps(manifest).encode()
            checksums = {
                name: hashlib.sha256(data).hexdigest()
                for name, data in members.items()
            }
            with zipfile.ZipFile(capsule.path, "w") as target:
                for name, data in members.items():
                    target.writestr(name, data)
                target.writestr("checksums.json", json.dumps(checksums))

            capsule.verify()

            self.assertEqual(capsule.manifest.schema_version, 5)
            self.assertIsNone(capsule.manifest.playlist_phrase)
            self.assertEqual(len(capsule.manifest.automations), 1)

    def test_schema_six_requires_playlist_phrase_member(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = root / "preview.wav"
            write_silence(preview)
            capsule = Capsule.build(
                root / "Missing-Phrase.flcapsule",
                name="Missing Phrase",
                project=fixture_project(),
                channel_ids=[2],
                pattern_id=3,
                preview_wav=preview,
            )
            make_legacy_capsule(capsule)
            phrase_path = capsule.manifest.playlist_phrase.pattern_playlist_path
            with zipfile.ZipFile(capsule.path) as source:
                members = {
                    name: source.read(name)
                    for name in source.namelist() if name != phrase_path
                }
            with zipfile.ZipFile(capsule.path, "w") as target:
                for name, data in members.items():
                    target.writestr(name, data)

            with self.assertRaisesRegex(ValueError, "missing required members"):
                capsule.verify()

    def test_schema_six_rejects_invalid_pattern_placement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = root / "preview.wav"
            write_silence(preview)
            capsule = Capsule.build(
                root / "Invalid-Phrase.flcapsule",
                name="Invalid Phrase",
                project=fixture_project(),
                channel_ids=[2],
                pattern_id=3,
                preview_wav=preview,
            )
            make_legacy_capsule(capsule)
            phrase_path = capsule.manifest.playlist_phrase.pattern_playlist_path
            with zipfile.ZipFile(capsule.path) as source:
                members = {
                    name: source.read(name)
                    for name in source.namelist() if name != "checksums.json"
                }
            placement = bytearray(members[phrase_path])
            struct.pack_into("<H", placement, 6, 2)
            members[phrase_path] = bytes(placement)
            checksums = {
                name: hashlib.sha256(data).hexdigest()
                for name, data in members.items()
            }
            with zipfile.ZipFile(capsule.path, "w") as target:
                for name, data in members.items():
                    target.writestr(name, data)
                target.writestr("checksums.json", json.dumps(checksums))

            with self.assertRaisesRegex(ValueError, "phrase placement is invalid"):
                capsule.verify()

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
            manifest["schema_version"] = 7
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
