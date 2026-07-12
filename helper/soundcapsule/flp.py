"""Lossless FLP/FST/FSC event parser and narrowly-scoped merger.

FL Studio files use a fixed 22-byte header followed by a typed event stream.
Every event retains its original encoded bytes. Mutating code only re-encodes
the exact events it changes, so unknown future events survive round trips.

The merger intentionally handles generator channel states and pattern notes
only. Mixer, automation, playlist, layer, and routing graph changes are out of
scope for version one.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import struct
from typing import Iterator, Sequence


HEADER = struct.Struct("<4sIhHH4sI")
MAGIC_HEADER = b"FLhd"
MAGIC_DATA = b"FLdt"

FORMAT_PROJECT = 0x00
FORMAT_SCORE = 0x10
FORMAT_CHANNEL_STATE = 0x20

EVENT_CHANNEL_NEW = 64
EVENT_CHANNEL_ENABLED = 0
EVENT_PROJECT_LOOP_MODE = 9
EVENT_PATTERN_NEW = 65
EVENT_CURRENT_PATTERN = 67
EVENT_CHANNEL_TYPE = 21
EVENT_CHANNEL_ROUTED_TO = 22
EVENT_CHANNEL_NAME_LEGACY = 192
EVENT_PATTERN_NAME = 193
EVENT_FL_VERSION = 199
EVENT_PROJECT_DATA_PATH = 202
EVENT_PLUGIN_INTERNAL_NAME = 201
EVENT_PLUGIN_NAME = 203
EVENT_PATTERN_NOTES = 224
EVENT_CHANNEL_SAMPLE_PATH = 196

# Event ownership began with the model independently established by PyFLP and
# is extended here for FL Studio 25.2.  Image-Line changed several IDs used at
# the end of the Channel Rack region; the three boundary IDs below were
# validated across every FLP bundled with build 25.2.5.5055.
POST_CHANNEL_BOUNDARY_IDS = frozenset({99, 233, 238})
CHANNEL_EVENT_IDS = frozenset(
    {
        0, 2, 3, 15, 20, 21, 22, 32,
        64, 69, 70, 71, 72, 73, 74, 75, 76, 83, 85, 86, 89, 94, 97,
        131, 132, 135, 138, 139, 140, 142, 143, 144, 145, 153,
        192, 196,
        225, 231, 234, 235, 237, 244, 245, 250,
    }
)
PLUGIN_EVENT_IDS = frozenset({128, 155, 201, 203, 228, 229})
FL25_CHANNEL_EVENT_IDS = frozenset(
    {41, 48, 50, 51, 104, 170, 209, 212, 213, 215, 218, 219, 221}
)
CHANNEL_OWNED_EVENT_IDS = CHANNEL_EVENT_IDS | PLUGIN_EVENT_IDS | FL25_CHANNEL_EVENT_IDS
RACK_GLOBAL_EVENT_IDS = frozenset({11, 13, 133})

NOTE_STRUCT = struct.Struct("<IHHIHHBBBBBBBB")
NOTE_SIZE = NOTE_STRUCT.size

SUPPORTED_PROJECT_MAJOR = 25


class FLPFormatError(ValueError):
    """Raised for corrupt or structurally invalid FLP data."""


class FLPUnsupportedError(RuntimeError):
    """Raised when a requested safe mutation is not supported."""


def decode_varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while True:
        if offset >= len(data):
            raise FLPFormatError("truncated event length")
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return value, offset
        shift += 7
        if shift > 35:
            raise FLPFormatError("event length varint is too large")


def encode_varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("varint cannot encode a negative value")
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


@dataclass(frozen=True, slots=True)
class Event:
    id: int
    payload: bytes
    raw: bytes
    source_offset: int = -1

    @property
    def scalar(self) -> int:
        if self.id < 64:
            return self.payload[0]
        if self.id < 128:
            return int.from_bytes(self.payload, "little")
        if self.id < 192:
            return int.from_bytes(self.payload, "little")
        raise TypeError("data/text event has no scalar value")

    def with_payload(self, payload: bytes) -> "Event":
        return Event(self.id, payload, encode_event(self.id, payload), self.source_offset)

    def with_scalar(self, value: int) -> "Event":
        size = 1 if self.id < 64 else 2 if self.id < 128 else 4
        return self.with_payload(value.to_bytes(size, "little", signed=False))


def encode_event(event_id: int, payload: bytes) -> bytes:
    if not 0 <= event_id <= 255:
        raise ValueError("event id outside byte range")
    expected = 1 if event_id < 64 else 2 if event_id < 128 else 4 if event_id < 192 else None
    if expected is not None and len(payload) != expected:
        raise ValueError(f"event {event_id} requires {expected} payload bytes")
    if expected is None:
        return bytes((event_id,)) + encode_varint(len(payload)) + payload
    return bytes((event_id,)) + payload


def scalar_event(event_id: int, value: int) -> Event:
    size = 1 if event_id < 64 else 2 if event_id < 128 else 4
    payload = value.to_bytes(size, "little", signed=False)
    return Event(event_id, payload, encode_event(event_id, payload))


def data_event(event_id: int, payload: bytes) -> Event:
    return Event(event_id, payload, encode_event(event_id, payload))


def text_event(event_id: int, text: str, *, unicode_text: bool = True) -> Event:
    payload = text.encode("utf-16le") + b"\0\0" if unicode_text else text.encode("utf-8") + b"\0"
    return data_event(event_id, payload)


def parse_text(payload: bytes) -> str:
    # Modern FL projects terminate UTF-16LE strings with two zero bytes.  The
    # previous "second byte is zero" heuristic failed for non-Latin text.
    if len(payload) >= 2 and len(payload) % 2 == 0 and payload.endswith(b"\0\0"):
        return payload.decode("utf-16le", errors="replace").rstrip("\0")
    return payload.decode("utf-8", errors="replace").rstrip("\0")


def iter_events(data: bytes) -> Iterator[Event]:
    offset = 0
    while offset < len(data):
        start = offset
        event_id = data[offset]
        offset += 1
        if event_id < 64:
            size = 1
        elif event_id < 128:
            size = 2
        elif event_id < 192:
            size = 4
        else:
            size, offset = decode_varint(data, offset)
        end = offset + size
        if end > len(data):
            raise FLPFormatError(f"truncated payload for event {event_id} at {start}")
        payload = data[offset:end]
        yield Event(event_id, payload, data[start:end], start)
        offset = end


@dataclass(slots=True)
class NoteRecord:
    raw: bytes

    @classmethod
    def parse_many(cls, payload: bytes) -> list["NoteRecord"]:
        if len(payload) % NOTE_SIZE:
            raise FLPFormatError(f"note payload length {len(payload)} is not divisible by {NOTE_SIZE}")
        return [cls(payload[i : i + NOTE_SIZE]) for i in range(0, len(payload), NOTE_SIZE)]

    @property
    def values(self) -> tuple[int, ...]:
        return NOTE_STRUCT.unpack(self.raw)

    @property
    def position(self) -> int:
        return self.values[0]

    @property
    def rack_channel(self) -> int:
        return self.values[2]

    @property
    def length(self) -> int:
        return self.values[3]

    @property
    def key(self) -> int:
        return self.values[4]

    def remap(self, *, channel: int | None = None, ppq_from: int | None = None, ppq_to: int | None = None) -> "NoteRecord":
        values = list(self.values)
        if channel is not None:
            values[2] = channel
        if ppq_from and ppq_to and ppq_from != ppq_to:
            values[0] = round(values[0] * ppq_to / ppq_from)
            values[3] = max(1, round(values[3] * ppq_to / ppq_from)) if values[3] else 0
        return NoteRecord(NOTE_STRUCT.pack(*values))

    def to_dict(self) -> dict[str, int | bool]:
        v = self.values
        return {
            "position": v[0], "flags": v[1], "rack_channel": v[2], "length": v[3],
            "key": v[4], "group": v[5], "fine_pitch": v[6], "unknown": v[7],
            "release": v[8], "midi_channel": v[9], "pan": v[10], "velocity": v[11],
            "mod_x": v[12], "mod_y": v[13], "slide": bool(v[1] & 8),
        }


@dataclass(frozen=True, slots=True)
class ChannelSection:
    iid: int
    events: tuple[Event, ...]

    @property
    def name(self) -> str:
        for event_id in (EVENT_PLUGIN_NAME, EVENT_CHANNEL_NAME_LEGACY, EVENT_PLUGIN_INTERNAL_NAME):
            for event in self.events:
                if event.id == event_id:
                    value = parse_text(event.payload)
                    if value:
                        return value
        return f"Channel {self.iid}"

    @property
    def plugin_name(self) -> str:
        internal = next(
            (parse_text(event.payload) for event in self.events
             if event.id == EVENT_PLUGIN_INTERNAL_NAME and parse_text(event.payload)),
            "",
        )
        display = next(
            (parse_text(event.payload) for event in self.events
             if event.id == EVENT_PLUGIN_NAME and parse_text(event.payload)),
            "",
        )
        # Native generators identify themselves in event 201; event 203 is the
        # user-editable Channel Rack name. Generic third-party wrappers are the
        # exception, so retain the display-name fallback for those channels.
        if internal and internal.casefold() not in {
            "fruity wrapper", "wrapper", "vst wrapper", "vst3 wrapper", "clap wrapper"
        }:
            return internal
        if internal and display:
            return display
        if self.channel_type == 0:
            return "Sampler"
        if self.channel_type == 4:
            return "Audio Clip"
        if self.channel_type == 5:
            return "Automation Clip"
        return internal or display or "Generator"

    @property
    def channel_type(self) -> int | None:
        for event in self.events:
            if event.id == EVENT_CHANNEL_TYPE:
                return event.scalar
        return None

    @property
    def sample_path(self) -> str | None:
        for event in self.events:
            if event.id == EVENT_CHANNEL_SAMPLE_PATH:
                return parse_text(event.payload)
        return None

    def remap(self, iid: int, *, route_to_master: bool = False) -> "ChannelSection":
        remapped: list[Event] = []
        for event in self.events:
            if event.id == EVENT_CHANNEL_NEW:
                remapped.append(event.with_scalar(iid))
            elif route_to_master and event.id == EVENT_CHANNEL_ROUTED_TO:
                remapped.append(event.with_scalar(0))
            else:
                remapped.append(event)
        return ChannelSection(iid, tuple(remapped))

    def with_sample_path(self, path: str, *, unicode_text: bool = True) -> "ChannelSection":
        replacement = text_event(EVENT_CHANNEL_SAMPLE_PATH, path, unicode_text=unicode_text)
        events = list(self.events)
        for index, event in enumerate(events):
            if event.id == EVENT_CHANNEL_SAMPLE_PATH:
                events[index] = replacement
                return ChannelSection(self.iid, tuple(events))
        events.append(replacement)
        return ChannelSection(self.iid, tuple(events))

    def with_enabled(self, enabled: bool) -> "ChannelSection":
        events = list(self.events)
        for index, event in enumerate(events):
            if event.id == EVENT_CHANNEL_ENABLED:
                events[index] = event.with_scalar(int(enabled))
                return ChannelSection(self.iid, tuple(events))
        events.insert(1, scalar_event(EVENT_CHANNEL_ENABLED, int(enabled)))
        return ChannelSection(self.iid, tuple(events))


@dataclass(slots=True)
class FLPFile:
    format: int
    channel_count: int
    ppq: int
    events: list[Event]

    @classmethod
    def from_bytes(cls, raw: bytes) -> "FLPFile":
        if len(raw) < HEADER.size:
            raise FLPFormatError("file shorter than FLP header")
        magic, header_size, fmt, channel_count, ppq, data_magic, data_size = HEADER.unpack_from(raw)
        if magic != MAGIC_HEADER or data_magic != MAGIC_DATA or header_size != 6:
            raise FLPFormatError("invalid FLP chunk header")
        body = raw[HEADER.size:]
        if len(body) != data_size:
            raise FLPFormatError(f"declared event data is {data_size} bytes; got {len(body)}")
        events = list(iter_events(body))
        instance = cls(fmt, channel_count, ppq, events)
        instance.validate()
        return instance

    @classmethod
    def read(cls, path: Path | str) -> "FLPFile":
        return cls.from_bytes(Path(path).read_bytes())

    def to_bytes(self) -> bytes:
        body = b"".join(event.raw for event in self.events)
        return HEADER.pack(MAGIC_HEADER, 6, self.format, self.channel_count, self.ppq, MAGIC_DATA, len(body)) + body

    def write(self, path: Path | str) -> None:
        Path(path).write_bytes(self.to_bytes())

    def clone(self) -> "FLPFile":
        return FLPFile(self.format, self.channel_count, self.ppq, list(self.events))

    def validate(self) -> None:
        if self.ppq <= 0:
            raise FLPFormatError("PPQ must be positive")
        new_channels = sum(1 for event in self.events if event.id == EVENT_CHANNEL_NEW)
        if self.format == FORMAT_PROJECT and new_channels != self.channel_count:
            raise FLPFormatError(
                f"header declares {self.channel_count} channels but event stream contains {new_channels}"
            )
        for _, event in self._pattern_note_events():
            NoteRecord.parse_many(event.payload)

    @property
    def fl_version(self) -> str:
        for event in self.events:
            if event.id == EVENT_FL_VERSION:
                return parse_text(event.payload)
        return ""

    @property
    def current_pattern(self) -> int:
        for event in self.events:
            if event.id == EVENT_CURRENT_PATTERN:
                return event.scalar
        return 1

    @property
    def data_path(self) -> str | None:
        for event in self.events:
            if event.id == EVENT_PROJECT_DATA_PATH:
                value = parse_text(event.payload).strip()
                return value or None
        return None

    def channel_sections(self) -> list[ChannelSection]:
        starts = [index for index, event in enumerate(self.events) if event.id == EVENT_CHANNEL_NEW]
        starts = starts[: self.channel_count]
        sections: list[ChannelSection] = []
        for position, start in enumerate(starts):
            natural_end = starts[position + 1] if position + 1 < len(starts) else len(self.events)
            end = next(
                (
                    i for i in range(start + 1, natural_end)
                    if self.events[i].id in POST_CHANNEL_BOUNDARY_IDS or self.events[i].id == EVENT_PATTERN_NEW
                ),
                natural_end,
            )
            owned = tuple(event for event in self.events[start:end] if event.id not in RACK_GLOBAL_EVENT_IDS)
            # Unknown events inside the span are intentionally retained.  This
            # preserves newer wrapper/plugin state before an ID has a name.
            sections.append(ChannelSection(owned[0].scalar, owned))
        return sections

    def pattern_notes(self) -> dict[int, list[NoteRecord]]:
        result: dict[int, list[NoteRecord]] = {}
        for pattern, event in self._pattern_note_events():
            result.setdefault(pattern, []).extend(NoteRecord.parse_many(event.payload))
        return result

    def _pattern_note_events(self) -> Iterator[tuple[int, Event]]:
        # ID 224 is overloaded by FL: it is also used for opaque channel state.
        # Treat it as notes only after a PatternNew event and when the exact
        # event object is not owned by a Channel Rack section.
        channel_owned = {
            id(event) for section in self.channel_sections() for event in section.events
        }
        current = 0
        for event in self.events:
            if event.id == EVENT_PATTERN_NEW:
                current = event.scalar
            elif (
                event.id == EVENT_PATTERN_NOTES
                and current > 0
                and id(event) not in channel_owned
            ):
                yield current, event

    def max_pattern_id(self) -> int:
        return max((event.scalar for event in self.events if event.id == EVENT_PATTERN_NEW), default=0)

    def extract_channels(self, channel_ids: Sequence[int]) -> list[ChannelSection]:
        lookup = {section.iid: section for section in self.channel_sections()}
        missing = [iid for iid in channel_ids if iid not in lookup]
        if missing:
            raise FLPUnsupportedError(f"channel ids not found: {missing}")
        sections = [lookup[iid] for iid in channel_ids]
        ambiguous = sorted(
            {
                event.id
                for section in sections
                for event in section.events
                if event.id not in CHANNEL_OWNED_EVENT_IDS
            }
        )
        if ambiguous:
            raise FLPUnsupportedError(
                "selected channels contain unprofiled FLP events: " + ", ".join(map(str, ambiguous))
            )
        unsupported = [section.name for section in sections if section.channel_type in (3, 5)]
        if unsupported:
            raise FLPUnsupportedError("version one supports generator and sampler channels only: " + ", ".join(unsupported))
        return sections

    def channel_state(self, section: ChannelSection) -> "FLPFile":
        normalized = section.remap(0)
        return FLPFile(FORMAT_CHANNEL_STATE, 1, self.ppq, list(normalized.events))

    def isolated_preview_project(self, channel_ids: Sequence[int], pattern_id: int) -> "FLPFile":
        selected = set(channel_ids)
        if not selected:
            raise FLPUnsupportedError("select at least one Channel Rack channel")
        # Validates channel types and event ownership before any mutation.
        self.extract_channels(channel_ids)
        target = self.clone()
        sections = target.channel_sections()
        known = {section.iid for section in sections}
        missing = selected - known
        if missing:
            raise FLPUnsupportedError(f"channel ids not found: {sorted(missing)}")

        for section in sections:
            if section.iid in selected:
                replacement = section.with_enabled(True).remap(section.iid, route_to_master=True)
            else:
                replacement = section.with_enabled(False)
            target._replace_channel_events(section, replacement.events)

        target._filter_pattern_notes(pattern_id, selected)
        target._set_current_pattern(pattern_id)
        # FL stores transport loop mode as 0 = Pattern, 1 = Song.
        target._set_scalar_event(EVENT_PROJECT_LOOP_MODE, 0)
        target.validate()
        return target

    def _channel_insert_index(self) -> int:
        sections = self.channel_sections()
        if not sections:
            return 0
        last = sections[-1]
        last_raw = last.events[-1]
        for index, event in enumerate(self.events):
            if event is last_raw:
                return index + 1
        raise FLPFormatError("could not locate last channel boundary")

    def append_capsule(
        self,
        sections: Sequence[ChannelSection],
        notes_by_source_channel: dict[int, Sequence[NoteRecord]],
        *,
        source_ppq: int,
        pattern_name: str,
        target_pattern_id: int | None = None,
    ) -> tuple["FLPFile", dict[int, int], int]:
        target = self.clone()
        existing_ids = [section.iid for section in target.channel_sections()]
        next_channel = max(existing_ids, default=-1) + 1
        mapping = {section.iid: next_channel + offset for offset, section in enumerate(sections)}
        new_events: list[Event] = []
        for section in sections:
            new_events.extend(section.remap(mapping[section.iid], route_to_master=True).events)
        insert_at = target._channel_insert_index()
        target.events[insert_at:insert_at] = new_events
        target.channel_count += len(sections)

        pattern_id = target_pattern_id if target_pattern_id is not None else target.max_pattern_id() + 1
        imported_notes: list[NoteRecord] = []
        for section in sections:
            for note in notes_by_source_channel.get(section.iid, ()):
                imported_notes.append(
                    note.remap(
                        channel=mapping[section.iid],
                        ppq_from=source_ppq,
                        ppq_to=target.ppq,
                    )
                )

        if target_pattern_id is None:
            unicode_text = _uses_unicode_text(target)
            pattern_events = [
                scalar_event(EVENT_PATTERN_NEW, pattern_id),
                data_event(EVENT_PATTERN_NOTES, b"".join(note.raw for note in imported_notes)),
                scalar_event(EVENT_PATTERN_NEW, pattern_id),
                text_event(EVENT_PATTERN_NAME, pattern_name, unicode_text=unicode_text),
            ]
            pattern_insert = next(
                (index for index, event in enumerate(target.events) if event.id == EVENT_CHANNEL_NEW),
                len(target.events),
            )
            target.events[pattern_insert:pattern_insert] = pattern_events
        else:
            target._append_pattern_notes(pattern_id, imported_notes)
        target._set_current_pattern(pattern_id)
        target.validate()
        return target, mapping, pattern_id

    def override_capsule(
        self,
        sections: Sequence[ChannelSection],
        notes_by_source_channel: dict[int, Sequence[NoteRecord]],
        target_channel_ids: Sequence[int],
        *,
        source_ppq: int,
        pattern_id: int,
    ) -> "FLPFile":
        if len(sections) != len(target_channel_ids):
            raise FLPUnsupportedError("override requires an equal number of capsule and selected destination channels")
        target = self.clone()
        existing = {section.iid: section for section in target.channel_sections()}
        for iid in target_channel_ids:
            if iid not in existing:
                raise FLPUnsupportedError(f"destination channel {iid} does not exist")

        pairs = list(zip(sections, target_channel_ids, strict=True))
        for source, destination in pairs:
            old = existing[destination]
            replacement = source.remap(destination)
            replacement_events = _preserve_destination_route(replacement.events, old.events)
            target._replace_channel_events(old, replacement_events)

        replacement_notes: list[NoteRecord] = []
        for source, destination in pairs:
            replacement_notes.extend(
                note.remap(channel=destination, ppq_from=source_ppq, ppq_to=target.ppq)
                for note in notes_by_source_channel.get(source.iid, ())
            )
        target._replace_pattern_channel_notes(pattern_id, set(target_channel_ids), replacement_notes)
        target.validate()
        return target

    def _set_current_pattern(self, pattern_id: int) -> None:
        for index, event in enumerate(self.events):
            if event.id == EVENT_CURRENT_PATTERN:
                self.events[index] = event.with_scalar(pattern_id)
                return
        self.events.append(scalar_event(EVENT_CURRENT_PATTERN, pattern_id))

    def _set_scalar_event(self, event_id: int, value: int) -> None:
        for index, event in enumerate(self.events):
            if event.id == event_id:
                self.events[index] = event.with_scalar(value)
                return
        self.events.insert(0, scalar_event(event_id, value))

    def _replace_channel_events(self, old: ChannelSection, replacement_events: Sequence[Event]) -> None:
        old_ids = {id(event) for event in old.events}
        start = next(index for index, event in enumerate(self.events) if id(event) in old_ids)
        rebuilt = self.events[:start] + list(replacement_events)
        rebuilt.extend(event for event in self.events[start:] if id(event) not in old_ids)
        self.events = rebuilt

    def _filter_pattern_notes(self, pattern_id: int, channels: set[int]) -> None:
        found = False
        note_events = {id(event) for pattern, event in self._pattern_note_events() if pattern == pattern_id}
        for index, event in enumerate(self.events):
            if id(event) in note_events:
                kept = [note for note in NoteRecord.parse_many(event.payload) if note.rack_channel in channels]
                self.events[index] = event.with_payload(b"".join(note.raw for note in kept))
                found = True
        if not found:
            raise FLPUnsupportedError(f"pattern {pattern_id} has no note event to render")

    def _replace_pattern_channel_notes(self, pattern_id: int, channels: set[int], replacement_notes: Sequence[NoteRecord]) -> None:
        note_events = {id(event) for pattern, event in self._pattern_note_events() if pattern == pattern_id}
        for index, event in enumerate(self.events):
            if id(event) in note_events:
                kept = [note for note in NoteRecord.parse_many(event.payload) if note.rack_channel not in channels]
                payload = b"".join(note.raw for note in (*kept, *replacement_notes))
                self.events[index] = event.with_payload(payload)
                return
        self.events.extend([scalar_event(EVENT_PATTERN_NEW, pattern_id), data_event(EVENT_PATTERN_NOTES, b"".join(note.raw for note in replacement_notes))])

    def _append_pattern_notes(
        self, pattern_id: int, imported_notes: Sequence[NoteRecord]
    ) -> None:
        note_events = {
            id(event)
            for pattern, event in self._pattern_note_events()
            if pattern == pattern_id
        }
        for index, event in enumerate(self.events):
            if id(event) in note_events:
                self.events[index] = event.with_payload(
                    event.payload + b"".join(note.raw for note in imported_notes)
                )
                return
        self.events.extend(
            [
                scalar_event(EVENT_PATTERN_NEW, pattern_id),
                data_event(
                    EVENT_PATTERN_NOTES,
                    b"".join(note.raw for note in imported_notes),
                ),
            ]
        )


def _uses_unicode_text(project: FLPFile) -> bool:
    version = project.fl_version
    try:
        major, minor, *_ = (int(part) for part in version.split("."))
        return (major, minor) >= (11, 5)
    except (ValueError, TypeError):
        return True


def _preserve_destination_route(replacement_events: Sequence[Event], destination_events: Sequence[Event]) -> list[Event]:
    destination_route = next((event for event in destination_events if event.id == EVENT_CHANNEL_ROUTED_TO), None)
    result = list(replacement_events)
    if destination_route is None:
        result = [event for event in result if event.id != EVENT_CHANNEL_ROUTED_TO]
        return result
    for index, event in enumerate(result):
        if event.id == EVENT_CHANNEL_ROUTED_TO:
            result[index] = destination_route
            return result
    result.insert(1, destination_route)
    return result
