"""Lossless FLP/FST/FSC event parser and narrowly-scoped merger.

FL Studio files use a fixed 22-byte header followed by a typed event stream.
Every event retains its original encoded bytes. Mutating code only re-encodes
the exact events it changes, so unknown future events survive round trips.

The merger handles generator, Automation Clip, and portable mixer-insert states,
pattern notes, Channel Rack automation targets, and the selected automation
instances in the current Playlist arrangement. Mixer/global automation, layers,
and non-portable mixer routing graphs remain intentionally out of scope.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import re
import struct
from typing import Iterator, Sequence


HEADER = struct.Struct("<4sIhHH4sI")
MAGIC_HEADER = b"FLhd"
MAGIC_DATA = b"FLdt"

FORMAT_PROJECT = 0x00
FORMAT_SCORE = 0x10
FORMAT_CHANNEL_STATE = 0x20
FORMAT_INSERT_STATE = 0x40

EVENT_CHANNEL_NEW = 64
EVENT_CHANNEL_ENABLED = 0
EVENT_PROJECT_LOOP_MODE = 9
EVENT_PATTERN_NEW = 65
EVENT_CURRENT_PATTERN = 67
EVENT_TEMPO = 156
EVENT_CHANNEL_TYPE = 21
# Channel Rack target mixer insert. The stored value matches FL's visible
# insert number directly: 0 is Master, 1 is Insert 1, and so on.
EVENT_CHANNEL_ROUTED_TO = 104
EVENT_CHANNEL_NAME_LEGACY = 192
EVENT_PATTERN_NAME = 193
EVENT_FL_VERSION = 199
EVENT_PROJECT_DATA_PATH = 202
EVENT_PLUGIN_INTERNAL_NAME = 201
EVENT_PLUGIN_NAME = 203
EVENT_PLUGIN_LOCATION = 212
EVENT_PATTERN_NOTES = 224
EVENT_CHANNEL_SAMPLE_PATH = 196
EVENT_AUTOMATION_BINDINGS = 216
EVENT_PLAYLIST_SELECTION = 217
EVENT_REMOTE_CONTROLLER = 227
EVENT_AUTOMATION_POINTS = 234
EVENT_PLAYLIST = 233
EVENT_ARRANGEMENT_NEW = 99
EVENT_CURRENT_ARRANGEMENT = 100

# FL 24 through FL 26 mixer streams are a sequence of insert records followed
# by one fixed-width parameter table. Insert 0 is Master and the final record is
# FL's synthetic "Current" insert; neither is available for capsule imports.
EVENT_INSERT_ACTIVE = 42
EVENT_INSERT_ICON = 95
EVENT_EFFECT_SLOT_INDEX = 98
EVENT_INSERT_OUTPUT = 147
EVENT_INSERT_COLOR = 149
EVENT_INSERT_INPUT = 154
EVENT_INSERT_NAME = 204
EVENT_MIXER_PARAMS = 225
EVENT_INSERT_ROUTING = 235
EVENT_INSERT_FLAGS = 236

MIXER_PARAM_STRUCT = struct.Struct("<4sBBHi")
MIXER_PARAM_SLOT_ENABLED = 0
MIXER_PARAM_SLOT_MIX = 1
MIXER_PARAM_ROUTE_START = 64
MIXER_PARAM_ROUTE_END = 191
MIXER_PARAM_VOLUME = 192
MIXER_PARAM_PAN = 193
MIXER_PARAM_STEREO_SEPARATION = 194
MIXER_PARAM_LOW_GAIN = 208
MIXER_PARAM_MID_GAIN = 209
MIXER_PARAM_HIGH_GAIN = 210
MIXER_PARAM_LOW_FREQ = 216
MIXER_PARAM_MID_FREQ = 217
MIXER_PARAM_HIGH_FREQ = 218
MIXER_PARAM_LOW_Q = 224
MIXER_PARAM_MID_Q = 225
MIXER_PARAM_HIGH_Q = 226
MIXER_AUTOMATION_NAMESPACE = 0x70000000
MIXER_AUTOMATION_CONTROL_BASE = 0x1F00
MIXER_AUTOMATION_EFFECT_PARAMETER_BASE = 0x8000
MIXER_AUTOMATION_SLOT_STRIDE = 64
PORTABLE_GENERATOR_CONTROL_NAMES = {
    0: "channel volume",
    1: "channel panning",
    4: "channel pitch",
}
PORTABLE_GENERATOR_CONTROL_IDS = frozenset(PORTABLE_GENERATOR_CONTROL_NAMES)
PORTABLE_MIXER_PARAM_IDS = frozenset(
    {
        MIXER_PARAM_SLOT_ENABLED,
        MIXER_PARAM_SLOT_MIX,
        MIXER_PARAM_VOLUME,
        MIXER_PARAM_PAN,
        MIXER_PARAM_STEREO_SEPARATION,
        MIXER_PARAM_LOW_GAIN,
        MIXER_PARAM_MID_GAIN,
        MIXER_PARAM_HIGH_GAIN,
        MIXER_PARAM_LOW_FREQ,
        MIXER_PARAM_MID_FREQ,
        MIXER_PARAM_HIGH_FREQ,
        MIXER_PARAM_LOW_Q,
        MIXER_PARAM_MID_Q,
        MIXER_PARAM_HIGH_Q,
    }
)
REQUIRED_PORTABLE_MIXER_PARAM_KEYS = frozenset(
    {
        *((MIXER_PARAM_SLOT_ENABLED, slot) for slot in range(10)),
        *((MIXER_PARAM_SLOT_MIX, slot) for slot in range(10)),
        *((parameter_id, 0) for parameter_id in PORTABLE_MIXER_PARAM_IDS if parameter_id > 1),
    }
)
DEFAULT_MIXER_PARAMS = {
    MIXER_PARAM_VOLUME: 12_800,
    MIXER_PARAM_PAN: 0,
    MIXER_PARAM_STEREO_SEPARATION: 0,
    MIXER_PARAM_LOW_GAIN: 0,
    MIXER_PARAM_MID_GAIN: 0,
    MIXER_PARAM_HIGH_GAIN: 0,
    MIXER_PARAM_LOW_FREQ: 5_777,
    MIXER_PARAM_MID_FREQ: 33_145,
    MIXER_PARAM_HIGH_FREQ: 55_825,
    MIXER_PARAM_LOW_Q: 17_500,
    MIXER_PARAM_MID_Q: 17_500,
    MIXER_PARAM_HIGH_Q: 17_500,
}
MIXER_AUTOMATION_CONTROL_NAMES = {
    MIXER_PARAM_SLOT_ENABLED: "effect enabled",
    MIXER_PARAM_SLOT_MIX: "effect mix level",
    MIXER_PARAM_VOLUME: "insert volume",
    MIXER_PARAM_PAN: "insert pan",
    MIXER_PARAM_STEREO_SEPARATION: "insert stereo separation",
    MIXER_PARAM_LOW_GAIN: "insert low EQ gain",
    MIXER_PARAM_MID_GAIN: "insert mid EQ gain",
    MIXER_PARAM_HIGH_GAIN: "insert high EQ gain",
    MIXER_PARAM_LOW_FREQ: "insert low EQ frequency",
    MIXER_PARAM_MID_FREQ: "insert mid EQ frequency",
    MIXER_PARAM_HIGH_FREQ: "insert high EQ frequency",
    MIXER_PARAM_LOW_Q: "insert low EQ bandwidth",
    MIXER_PARAM_MID_Q: "insert mid EQ bandwidth",
    MIXER_PARAM_HIGH_Q: "insert high EQ bandwidth",
}
PORTABLE_INSERT_PREFIX_IDS = frozenset(
    {EVENT_INSERT_ACTIVE, EVENT_INSERT_ICON, EVENT_INSERT_COLOR, EVENT_INSERT_NAME}
)
# Audio-affecting flags: polarity, L/R swap, effect enable, insert enable, and
# threaded-processing disable. Docking, separators, locking, solo, and Audio
# Track links are deliberately stripped from portable insert states.
PORTABLE_INSERT_FLAGS_MASK = 0x001F
DEFAULT_PORTABLE_INSERT_FLAGS = 0x000C
DEFAULT_INSERT_NAME_PATTERN = re.compile(r"Insert [1-9][0-9]*")

# Although event IDs 128-191 normally carry four-byte scalar payloads, current
# FL 25/26 projects write event 172 with three bytes. Treating its following
# byte as payload swallows the next event ID and can hide the Pattern Notes
# block later in the stream.
FIXED_EVENT_SIZE_OVERRIDES = {172: 3}

# Some FL Studio 26 Windows projects contain a single zero padding byte between
# otherwise normally encoded events.  It is not an event and must remain byte
# exact when the project is written again.
EVENT_PADDING = -1

# Event ownership began with the model independently established by PyFLP and
# is extended here for FL Studio 25.2.  Image-Line changed several IDs used at
# the end of the Channel Rack region; the three boundary IDs below and opaque
# per-channel event 251 were validated with 25.2.5.5055 and 25.2.5.5319 projects
# loaded and rendered on macOS and Windows.
POST_CHANNEL_BOUNDARY_IDS = frozenset({99, 233, 238})
CHANNEL_EVENT_IDS = frozenset(
    {
        0, 2, 3, 15, 20, 21, 22, 32,
        64, 69, 70, 71, 72, 73, 74, 75, 76, 83, 85, 86, 89, 94, 97,
        EVENT_CHANNEL_ROUTED_TO,
        131, 132, 135, 138, 139, 140, 142, 143, 144, 145, 153,
        192, 196,
        225, 231, 234, 235, 237, 244, 245, 250,
    }
)
PLUGIN_EVENT_IDS = frozenset({128, 155, 201, 203, 228, 229})
FL25_CHANNEL_EVENT_IDS = frozenset(
    {41, 48, 50, 51, 170, 209, 212, 213, 215, 218, 219, 221, 251}
)
CHANNEL_OWNED_EVENT_IDS = CHANNEL_EVENT_IDS | PLUGIN_EVENT_IDS | FL25_CHANNEL_EVENT_IDS
RACK_GLOBAL_EVENT_IDS = frozenset({11, 13, 133})

NOTE_STRUCT = struct.Struct("<IHHIHHBBBBBBBB")
NOTE_SIZE = NOTE_STRUCT.size
AUTOMATION_BINDING_STRUCT = struct.Struct("<III")
REMOTE_CONTROLLER_STRUCT = struct.Struct("<2sH4sI8s")
AUTOMATION_CONNECTION_HEADER = struct.Struct("<4sB3x")
AUTOMATION_CONNECTION_MAGIC = b"SCA5"
AUTOMATION_POINT_STRUCT = struct.Struct("<ddf4s")
AUTOMATION_POINT_HEADER_SIZE = 21
PLAYLIST_ITEM_SIZES = (88, 60, 32)

SUPPORTED_PROJECT_MAJOR = 26


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
        size = FIXED_EVENT_SIZE_OVERRIDES.get(
            self.id, 1 if self.id < 64 else 2 if self.id < 128 else 4
        )
        return self.with_payload(value.to_bytes(size, "little", signed=False))


def encode_event(event_id: int, payload: bytes) -> bytes:
    if not 0 <= event_id <= 255:
        raise ValueError("event id outside byte range")
    expected = FIXED_EVENT_SIZE_OVERRIDES.get(
        event_id,
        1 if event_id < 64 else 2 if event_id < 128 else 4 if event_id < 192 else None,
    )
    if expected is not None and len(payload) != expected:
        raise ValueError(f"event {event_id} requires {expected} payload bytes")
    if expected is None:
        return bytes((event_id,)) + encode_varint(len(payload)) + payload
    return bytes((event_id,)) + payload


def scalar_event(event_id: int, value: int) -> Event:
    size = FIXED_EVENT_SIZE_OVERRIDES.get(
        event_id, 1 if event_id < 64 else 2 if event_id < 128 else 4
    )
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


def _parse_events_strict(data: bytes, offset: int = 0) -> tuple[list[Event], FLPFormatError | None]:
    events: list[Event] = []
    while offset < len(data):
        start = offset
        event_id = data[offset]
        offset += 1
        if event_id < 192:
            size = FIXED_EVENT_SIZE_OVERRIDES.get(
                event_id, 1 if event_id < 64 else 2 if event_id < 128 else 4
            )
        else:
            try:
                size, offset = decode_varint(data, offset)
            except FLPFormatError as error:
                return events, error
        end = offset + size
        if end > len(data):
            return events, FLPFormatError(
                f"truncated payload for event {event_id} at {start}"
            )
        payload = data[offset:end]
        events.append(Event(event_id, payload, data[start:end], start))
        offset = end
    return events, None


def iter_events(data: bytes) -> Iterator[Event]:
    events, error = _parse_events_strict(data)
    if error is None:
        yield from events
        return

    # FL Studio 26.1.0.5530 on Windows has been observed writing one zero
    # alignment byte before a normal variable-length event.  A legacy parser
    # reads the zero plus the following event ID as a byte event and eventually
    # loses framing.  The earliest recent boundary whose removal restores the
    # complete stream is the point where framing was first lost; later zeroes
    # can appear to work only because the already-misaligned prefix swallowed
    # their event IDs.  The bounded fallback keeps malformed files rejected.
    for index in range(max(0, len(events) - 64), len(events)):
        candidate = events[index]
        if candidate.id != EVENT_CHANNEL_ENABLED or candidate.raw[:1] != b"\0":
            continue
        suffix, suffix_error = _parse_events_strict(data, candidate.source_offset + 1)
        if suffix_error is None:
            yield from events[:index]
            yield Event(EVENT_PADDING, b"", b"\0", candidate.source_offset)
            yield from suffix
            return
    raise error


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
class AutomationBinding:
    raw: bytes

    @classmethod
    def parse_many(cls, payload: bytes) -> list["AutomationBinding"]:
        if len(payload) % AUTOMATION_BINDING_STRUCT.size:
            raise FLPFormatError("automation binding payload is not a sequence of 12-byte records")
        return [
            cls(payload[offset : offset + AUTOMATION_BINDING_STRUCT.size])
            for offset in range(0, len(payload), AUTOMATION_BINDING_STRUCT.size)
        ]

    @property
    def target_event_id(self) -> int:
        return AUTOMATION_BINDING_STRUCT.unpack(self.raw)[1]

    def target_channel_iid(self, known_channel_ids: set[int]) -> int | None:
        candidate = self.target_event_id >> 16
        return candidate if candidate in known_channel_ids else None

    def remap_target_channel(self, target_iid: int) -> "AutomationBinding":
        prefix, event_id, initial_value = AUTOMATION_BINDING_STRUCT.unpack(self.raw)
        remapped = ((target_iid & 0xFFFF) << 16) | (event_id & 0xFFFF)
        return AutomationBinding(
            AUTOMATION_BINDING_STRUCT.pack(prefix, remapped, initial_value)
        )

    def with_target_event_id(self, target_event_id: int) -> "AutomationBinding":
        if not 0 <= target_event_id <= 0xFFFFFFFF:
            raise ValueError("automation target event id is outside the supported range")
        prefix, _, initial_value = AUTOMATION_BINDING_STRUCT.unpack(self.raw)
        return AutomationBinding(
            AUTOMATION_BINDING_STRUCT.pack(prefix, target_event_id, initial_value)
        )


@dataclass(frozen=True, slots=True)
class RemoteControllerLink:
    """Lossless FL event-227 internal-controller connection.

    FL 25/26 store the Automation Clip instance ID at byte 2 and the
    destination event ID at byte 8. The remaining controller, formula, and
    mapping bytes are opaque and deliberately preserved.
    """

    raw: bytes

    @classmethod
    def parse(cls, payload: bytes) -> "RemoteControllerLink":
        if len(payload) != REMOTE_CONTROLLER_STRUCT.size:
            raise FLPFormatError(
                "remote-controller link is not a 20-byte FL 25/26 record"
            )
        return cls(payload)

    @property
    def source_automation_iid(self) -> int:
        return REMOTE_CONTROLLER_STRUCT.unpack(self.raw)[1]

    @property
    def target_event_id(self) -> int:
        return REMOTE_CONTROLLER_STRUCT.unpack(self.raw)[3]

    def remap(
        self, *, source_automation_iid: int, target_event_id: int
    ) -> "RemoteControllerLink":
        if not 0 <= source_automation_iid <= 0xFFFF:
            raise FLPUnsupportedError(
                "destination Automation Clip ID exceeds FL's controller-link range"
            )
        if not 0 <= target_event_id <= 0xFFFFFFFF:
            raise ValueError("automation target event id is outside the supported range")
        prefix, _, opaque, _, suffix = REMOTE_CONTROLLER_STRUCT.unpack(self.raw)
        return RemoteControllerLink(
            REMOTE_CONTROLLER_STRUCT.pack(
                prefix, source_automation_iid, opaque, target_event_id, suffix
            )
        )


@dataclass(frozen=True, slots=True)
class AutomationTarget:
    """Portable semantic identity for one Automation Clip destination."""

    kind: str
    source_channel_iid: int | None = None
    source_insert_index: int | None = None
    slot_index: int | None = None
    parameter_index: int | None = None
    control_id: int | None = None

    def target_event_id(
        self,
        *,
        channel_mapping: dict[int, int],
        insert_mapping: dict[int, int],
    ) -> int:
        if self.kind == "generator_parameter":
            if self.source_channel_iid is None:
                raise FLPUnsupportedError("generator automation target is incomplete")
            target_iid = channel_mapping.get(self.source_channel_iid)
            if target_iid is None:
                raise FLPUnsupportedError(
                    "generator automation target is not included in the import"
                )
            if self.parameter_index is not None and self.control_id is None:
                low_word = (
                    MIXER_AUTOMATION_EFFECT_PARAMETER_BASE + self.parameter_index
                )
            elif (
                self.parameter_index is None
                and self.control_id in PORTABLE_GENERATOR_CONTROL_IDS
            ):
                low_word = self.control_id
            else:
                raise FLPUnsupportedError(
                    "generator automation parameter identity is incomplete"
                )
            event_id = (target_iid << 16) + low_word
        elif self.kind in {
            "insert_control",
            "effect_parameter",
            "effect_slot_control",
        }:
            if self.source_insert_index is None:
                raise FLPUnsupportedError("mixer automation target is incomplete")
            destination_insert = insert_mapping.get(self.source_insert_index)
            if destination_insert is None:
                raise FLPUnsupportedError(
                    "mixer automation target has no restored destination insert"
                )
            slot_index = self.slot_index or 0
            event_word = (
                destination_insert * MIXER_AUTOMATION_SLOT_STRIDE + slot_index
            ) << 16
            if self.kind == "effect_parameter":
                if self.parameter_index is None:
                    raise FLPUnsupportedError("effect automation target is incomplete")
                event_id = (
                    MIXER_AUTOMATION_NAMESPACE
                    + event_word
                    + MIXER_AUTOMATION_EFFECT_PARAMETER_BASE
                    + self.parameter_index
                )
            else:
                if self.control_id is None:
                    raise FLPUnsupportedError("mixer control automation target is incomplete")
                event_id = (
                    MIXER_AUTOMATION_NAMESPACE
                    + event_word
                    + MIXER_AUTOMATION_CONTROL_BASE
                    + self.control_id
                )
        else:
            raise FLPUnsupportedError(
                f"unsupported automation target kind {self.kind!r}"
            )
        if not 0 <= event_id <= 0xFFFFFFFF:
            raise FLPUnsupportedError("remapped automation target is outside FL's event range")
        return event_id


@dataclass(frozen=True, slots=True)
class AutomationConnection:
    """One portable destination and its connection-specific FL state."""

    role: str
    target: AutomationTarget
    binding: AutomationBinding | None = None
    remote_link: RemoteControllerLink | None = None

    @property
    def target_event_id(self) -> int:
        if self.binding is not None:
            return self.binding.target_event_id
        if self.remote_link is not None:
            return self.remote_link.target_event_id
        raise FLPFormatError("automation connection has no raw target state")

    def to_bytes(self) -> bytes:
        flags = (1 if self.binding is not None else 0) | (
            2 if self.remote_link is not None else 0
        )
        payload = bytearray(
            AUTOMATION_CONNECTION_HEADER.pack(AUTOMATION_CONNECTION_MAGIC, flags)
        )
        if self.binding is not None:
            payload.extend(self.binding.raw)
        if self.remote_link is not None:
            payload.extend(self.remote_link.raw)
        return bytes(payload)

    @classmethod
    def from_bytes(
        cls,
        payload: bytes,
        *,
        role: str,
        target: AutomationTarget,
    ) -> "AutomationConnection":
        if len(payload) < AUTOMATION_CONNECTION_HEADER.size:
            raise FLPFormatError("automation connection state is truncated")
        magic, flags = AUTOMATION_CONNECTION_HEADER.unpack_from(payload)
        if magic != AUTOMATION_CONNECTION_MAGIC or flags & ~3:
            raise FLPFormatError("automation connection state has an invalid header")
        offset = AUTOMATION_CONNECTION_HEADER.size
        binding = None
        link = None
        if flags & 1:
            end = offset + AUTOMATION_BINDING_STRUCT.size
            records = AutomationBinding.parse_many(payload[offset:end])
            if len(records) != 1:
                raise FLPFormatError("automation connection binding is missing")
            binding = records[0]
            offset = end
        if flags & 2:
            end = offset + REMOTE_CONTROLLER_STRUCT.size
            link = RemoteControllerLink.parse(payload[offset:end])
            offset = end
        if offset != len(payload):
            raise FLPFormatError("automation connection state has trailing bytes")
        if role == "primary" and binding is None:
            raise FLPFormatError(
                "primary automation connection has no event-216 binding"
            )
        if (
            binding is not None
            and link is not None
            and binding.target_event_id != link.target_event_id
        ):
            raise FLPFormatError(
                "automation binding and controller link disagree"
            )
        if role == "linked" and (binding is None or link is None):
            raise FLPFormatError(
                "linked automation connection has invalid event-227 state"
            )
        return cls(
            role=role, target=target, binding=binding, remote_link=link
        )


@dataclass(frozen=True, slots=True)
class AutomationPoint:
    position: float
    value: float
    tension: float

    @classmethod
    def parse_many(cls, payload: bytes) -> list["AutomationPoint"]:
        if len(payload) < AUTOMATION_POINT_HEADER_SIZE:
            raise FLPFormatError("automation point payload is truncated")
        count = struct.unpack_from("<I", payload, 17)[0]
        required = AUTOMATION_POINT_HEADER_SIZE + count * AUTOMATION_POINT_STRUCT.size
        if required > len(payload):
            raise FLPFormatError("automation point payload is truncated")
        if count > 1_000_000:
            raise FLPFormatError("automation point payload is unreasonably large")
        position = 0.0
        result: list[AutomationPoint] = []
        for offset in range(
            AUTOMATION_POINT_HEADER_SIZE, required, AUTOMATION_POINT_STRUCT.size
        ):
            delta, value, tension, _ = AUTOMATION_POINT_STRUCT.unpack_from(payload, offset)
            if not all(math.isfinite(item) for item in (delta, value, tension)):
                raise FLPFormatError("automation point payload contains non-finite values")
            position += delta
            result.append(cls(position, value, tension))
        return result


@dataclass(frozen=True, slots=True)
class PlaylistItem:
    raw: bytes

    @classmethod
    def parse_many(cls, payload: bytes) -> list["PlaylistItem"]:
        if not payload:
            return []
        for item_size in PLAYLIST_ITEM_SIZES:
            if len(payload) % item_size:
                continue
            items = [
                cls(payload[offset : offset + item_size])
                for offset in range(0, len(payload), item_size)
            ]
            # These invariant fields distinguish Playlist items from other
            # variable-size event payloads and prevent a coincidental length
            # match from being accepted as a known layout.
            if all(
                item.pattern_base == 20_480 and item.raw[16:18] == b"\x78\x00"
                for item in items
            ):
                return items
        raise FLPUnsupportedError(
            "the current FL Studio Playlist item format is not supported"
        )

    @property
    def record_size(self) -> int:
        return len(self.raw)

    @property
    def position(self) -> int:
        return struct.unpack_from("<I", self.raw, 0)[0]

    @property
    def pattern_base(self) -> int:
        return struct.unpack_from("<H", self.raw, 4)[0]

    @property
    def item_index(self) -> int:
        return struct.unpack_from("<H", self.raw, 6)[0]

    @property
    def length(self) -> int:
        return struct.unpack_from("<I", self.raw, 8)[0]

    @property
    def end_position(self) -> int:
        return self.position + self.length

    @property
    def is_pattern(self) -> bool:
        return self.item_index > self.pattern_base

    @property
    def pattern_id(self) -> int | None:
        if not self.is_pattern:
            return None
        return self.item_index - self.pattern_base

    @property
    def muted(self) -> bool:
        return bool(struct.unpack_from("<H", self.raw, 18)[0] & 0x2000)

    @property
    def runtime_id(self) -> int | None:
        if self.record_size == 32:
            return None
        return struct.unpack_from("<I", self.raw, 32)[0]

    def with_runtime_id(self, runtime_id: int) -> "PlaylistItem":
        if self.record_size == 32:
            return self
        if not 0 <= runtime_id <= 0xFFFFFFFF:
            raise FLPUnsupportedError("Playlist item runtime ID exceeds FL limits")
        raw = bytearray(self.raw)
        struct.pack_into("<I", raw, 32, runtime_id)
        return PlaylistItem(bytes(raw))

    def with_muted(self, muted: bool = True) -> "PlaylistItem":
        raw = bytearray(self.raw)
        flags = struct.unpack_from("<H", raw, 18)[0]
        flags = flags | 0x2000 if muted else flags & ~0x2000
        struct.pack_into("<H", raw, 18, flags)
        return PlaylistItem(bytes(raw))

    def crop_to_window(
        self,
        window_start: int,
        window_end: int,
        *,
        ppq: int,
        destination_anchor: int = 0,
    ) -> "PlaylistItem | None":
        """Intersect one placement with a song window without rewriting opaque fields."""
        if ppq <= 0:
            raise FLPFormatError("Playlist crop PPQ must be positive")
        if window_start < 0 or window_end <= window_start:
            raise FLPFormatError("Playlist crop window must be a non-empty positive range")
        intersection_start = max(self.position, window_start)
        intersection_end = min(self.end_position, window_end)
        if intersection_end <= intersection_start:
            return None
        if self.length <= 0:
            raise FLPUnsupportedError("Playlist item has no duration")

        left = intersection_start - self.position
        right = intersection_end - self.position
        raw = bytearray(self.raw)
        normalized_position = destination_anchor + intersection_start - window_start
        if not 0 <= normalized_position <= 0xFFFFFFFF:
            raise FLPUnsupportedError("Playlist item position exceeds FL Studio limits")
        struct.pack_into("<I", raw, 0, normalized_position)
        struct.pack_into("<I", raw, 8, intersection_end - intersection_start)

        # A placement wholly inside the window retains its exact source offsets.
        if left == 0 and right == self.length:
            return PlaylistItem(bytes(raw))

        if self.is_pattern:
            start_offset, end_offset = struct.unpack_from("<II", self.raw, 24)
            if start_offset == end_offset == 0xFFFFFFFF:
                content_start = 0.0
                content_span = float(self.length)
            elif end_offset > start_offset:
                content_start = float(start_offset)
                content_span = float(end_offset - start_offset)
            else:
                raise FLPUnsupportedError(
                    "Pattern placement has unsupported crop offsets"
                )
            scale = content_span / self.length
            cropped_start = round(content_start + left * scale)
            cropped_end = round(content_start + right * scale)
            cropped_end = max(cropped_start + 1, cropped_end)
            if cropped_end > 0xFFFFFFFF:
                raise FLPUnsupportedError("Pattern crop exceeds FL Studio limits")
            struct.pack_into("<II", raw, 24, cropped_start, cropped_end)
        else:
            start_offset, end_offset = struct.unpack_from("<ff", self.raw, 24)
            untrimmed = start_offset == end_offset == -1.0
            # Early synthetic fixtures used zeroes for an untrimmed channel clip.
            legacy_untrimmed = start_offset == end_offset == 0.0
            if untrimmed or legacy_untrimmed:
                content_start = 0.0
                content_span = self.length / ppq
            elif (
                math.isfinite(start_offset)
                and math.isfinite(end_offset)
                and start_offset >= 0.0
                and end_offset > start_offset
            ):
                content_start = start_offset
                content_span = end_offset - start_offset
            else:
                raise FLPUnsupportedError(
                    "Automation or audio placement has unsupported crop offsets"
                )
            scale = content_span / self.length
            cropped_start = content_start + left * scale
            cropped_end = content_start + right * scale
            if not all(math.isfinite(value) for value in (cropped_start, cropped_end)):
                raise FLPUnsupportedError("Playlist crop offsets are not finite")
            struct.pack_into("<ff", raw, 24, cropped_start, cropped_end)
        return PlaylistItem(bytes(raw))

    def remap_channel(
        self,
        channel_iid: int,
        *,
        source_anchor: int,
        destination_anchor: int,
        source_ppq: int,
        destination_ppq: int,
    ) -> "PlaylistItem":
        if source_ppq <= 0 or destination_ppq <= 0:
            raise FLPUnsupportedError("Playlist remap PPQ must be positive")
        if not 0 <= channel_iid <= self.pattern_base:
            raise FLPUnsupportedError("destination Automation Clip ID is invalid")
        raw = bytearray(self.raw)
        relative = self.position - source_anchor
        position = destination_anchor + round(relative * destination_ppq / source_ppq)
        length = round(self.length * destination_ppq / source_ppq)
        if not 0 <= position <= 0xFFFFFFFF or not 0 < length <= 0xFFFFFFFF:
            raise FLPUnsupportedError("destination Automation Clip timing exceeds FL limits")
        struct.pack_into("<I", raw, 0, position)
        struct.pack_into("<H", raw, 6, channel_iid)
        struct.pack_into("<I", raw, 8, length)
        return PlaylistItem(bytes(raw))

    def remap_pattern(
        self,
        pattern_id: int,
        *,
        source_anchor: int,
        destination_anchor: int,
        source_ppq: int,
        destination_ppq: int,
    ) -> "PlaylistItem":
        if not self.is_pattern:
            raise FLPUnsupportedError("cannot remap a channel clip as a Pattern clip")
        if source_ppq <= 0 or destination_ppq <= 0:
            raise FLPUnsupportedError("Playlist remap PPQ must be positive")
        if not 0 < pattern_id <= 0xFFFF - self.pattern_base:
            raise FLPUnsupportedError("destination Pattern ID is invalid")
        raw = bytearray(self.raw)
        relative = self.position - source_anchor
        position = destination_anchor + round(relative * destination_ppq / source_ppq)
        length = round(self.length * destination_ppq / source_ppq)
        if not 0 <= position <= 0xFFFFFFFF or not 0 < length <= 0xFFFFFFFF:
            raise FLPUnsupportedError("destination Pattern timing exceeds FL Studio limits")
        struct.pack_into("<I", raw, 0, position)
        struct.pack_into("<H", raw, 6, self.pattern_base + pattern_id)
        struct.pack_into("<I", raw, 8, length)
        start_offset, end_offset = struct.unpack_from("<II", self.raw, 24)
        if start_offset != 0xFFFFFFFF or end_offset != 0xFFFFFFFF:
            if end_offset <= start_offset:
                raise FLPUnsupportedError(
                    "Pattern placement has unsupported crop offsets"
                )
            scaled_start = round(start_offset * destination_ppq / source_ppq)
            scaled_end = round(end_offset * destination_ppq / source_ppq)
            if scaled_end > 0xFFFFFFFF:
                raise FLPUnsupportedError("destination Pattern crop exceeds FL Studio limits")
            struct.pack_into("<II", raw, 24, scaled_start, max(scaled_start + 1, scaled_end))
        return PlaylistItem(bytes(raw))

    def as_pattern(self, pattern_id: int, *, position: int, length: int) -> "PlaylistItem":
        raw = bytearray(self.raw)
        struct.pack_into("<I", raw, 0, max(0, position))
        struct.pack_into("<H", raw, 6, self.pattern_base + pattern_id)
        struct.pack_into("<I", raw, 8, max(1, length))
        # Playlist audio/automation records store real clip offsets here, while
        # Pattern clips use FL's all-bits-set sentinel for "no offset". Keeping
        # the automation values makes FL accept the item but render no notes.
        raw[24:32] = b"\xff" * 8
        return PlaylistItem(bytes(raw))

    def adapt_size(
        self, item_size: int, *, template: "PlaylistItem | None" = None
    ) -> "PlaylistItem":
        if item_size not in PLAYLIST_ITEM_SIZES:
            raise FLPUnsupportedError("the destination Playlist item format is not supported")
        if self.record_size == item_size:
            return self
        if self.record_size > item_size:
            return PlaylistItem(self.raw[:item_size])
        if template is not None and template.record_size >= item_size:
            extension = template.raw[self.record_size:item_size]
            return PlaylistItem(self.raw + extension)
        raw = bytearray(self.raw + b"\0" * (item_size - self.record_size))
        if item_size == 88:
            # FL 26's added Playlist tail defaults to unity gain and no link
            # group. Zero-initializing the unity field silently mutes a clip.
            struct.pack_into("<d", raw, 64, 1.0)
            struct.pack_into("<I", raw, 76, 0xFFFFFFFF)
        return PlaylistItem(bytes(raw))

    @classmethod
    def synthetic_pattern(
        cls,
        pattern_id: int,
        *,
        position: int,
        length: int,
        item_size: int,
        runtime_id: int = 0,
    ) -> "PlaylistItem":
        if item_size not in PLAYLIST_ITEM_SIZES:
            raise FLPUnsupportedError("the requested Playlist item format is not supported")
        raw = bytearray(item_size)
        struct.pack_into(
            "<IHHIHH", raw, 0, max(0, position), 20_480,
            20_480 + pattern_id, max(1, length), 499, 0,
        )
        raw[16:20] = b"\x78\x00\x40\x00"
        raw[20:24] = b"\x40\x64\x80\x80"
        raw[24:32] = b"\xff" * 8
        if item_size > 32:
            struct.pack_into("<I", raw, 32, runtime_id)
        if item_size == 88:
            struct.pack_into("<d", raw, 64, 1.0)
            struct.pack_into("<I", raw, 76, 0xFFFFFFFF)
        return cls(bytes(raw))


@dataclass(frozen=True, slots=True)
class PlaylistCaptureWindow:
    start: int
    end: int
    source: str
    pattern_items: tuple[PlaylistItem, ...]

    def __post_init__(self) -> None:
        if self.start < 0 or self.end <= self.start or self.end > 0xFFFFFFFF:
            raise FLPUnsupportedError("the Playlist capture window is invalid")
        if self.source not in {"selection", "playhead", "standalone"}:
            raise FLPFormatError("the Playlist capture window source is invalid")
        if not self.pattern_items or any(
            not item.is_pattern
            or item.length <= 0
            or item.end_position > self.duration
            for item in self.pattern_items
        ):
            raise FLPUnsupportedError(
                "the Playlist capture window has invalid Pattern placements"
            )

    @property
    def duration(self) -> int:
        return self.end - self.start

    @property
    def explicit_selection(self) -> bool:
        return self.source == "selection"


@dataclass(frozen=True, slots=True)
class MixerParamRecord:
    raw: bytes

    @classmethod
    def parse_many(cls, payload: bytes) -> list["MixerParamRecord"]:
        if len(payload) % MIXER_PARAM_STRUCT.size:
            raise FLPFormatError(
                "mixer parameter payload is not a sequence of 12-byte records"
            )
        return [
            cls(payload[offset : offset + MIXER_PARAM_STRUCT.size])
            for offset in range(0, len(payload), MIXER_PARAM_STRUCT.size)
        ]

    @property
    def values(self) -> tuple[bytes, int, int, int, int]:
        return MIXER_PARAM_STRUCT.unpack(self.raw)

    @property
    def parameter_id(self) -> int:
        return self.values[1]

    @property
    def marker(self) -> int:
        return self.values[2]

    @property
    def channel_data(self) -> int:
        return self.values[3]

    @property
    def slot_index(self) -> int:
        return self.channel_data & 0x3F

    @property
    def insert_key(self) -> int:
        return (self.channel_data >> 6) & 0x7F

    @property
    def value(self) -> int:
        return self.values[4]

    def remap_insert_key(self, insert_key: int) -> "MixerParamRecord":
        if not 0 <= insert_key <= 0x7F:
            raise ValueError("mixer parameter insert key is outside the supported range")
        prefix, parameter_id, marker, channel_data, value = self.values
        channel_data = (channel_data & ~0x1FC0) | (insert_key << 6)
        return MixerParamRecord(
            MIXER_PARAM_STRUCT.pack(
                prefix, parameter_id, marker, channel_data, value
            )
        )


@dataclass(frozen=True, slots=True)
class MixerEffectSlot:
    index: int
    events: tuple[Event, ...]

    @property
    def occupied(self) -> bool:
        return bool(self.events)

    @property
    def plugin_name(self) -> str:
        internal = next(
            (
                parse_text(event.payload)
                for event in self.events
                if event.id == EVENT_PLUGIN_INTERNAL_NAME and parse_text(event.payload)
            ),
            "",
        )
        display = next(
            (
                parse_text(event.payload)
                for event in self.events
                if event.id == EVENT_PLUGIN_NAME and parse_text(event.payload)
            ),
            "",
        )
        if internal.casefold() in {
            "fruity wrapper", "wrapper", "vst wrapper", "vst3 wrapper", "clap wrapper"
        } and display:
            return display
        return internal or display or ("Unknown effect" if self.occupied else "")

    def remap_insert(self, insert_index: int) -> "MixerEffectSlot":
        if not 0 <= insert_index <= 125:
            raise ValueError("mixer insert index must be between 0 and 125")
        remapped: list[Event] = []
        for event in self.events:
            if event.id != EVENT_PLUGIN_LOCATION:
                remapped.append(event)
                continue
            if len(event.payload) < 4:
                raise FLPUnsupportedError(
                    f"effect slot {self.index} has an unsupported plugin-location record"
                )
            payload = bytearray(event.payload)
            payload[:4] = insert_index.to_bytes(4, "little")
            remapped.append(event.with_payload(bytes(payload)))
        return MixerEffectSlot(self.index, tuple(remapped))


@dataclass(frozen=True, slots=True)
class MixerInsertSection:
    index: int
    events: tuple[Event, ...]
    params: tuple[MixerParamRecord, ...]

    @property
    def flags_event(self) -> Event:
        event = next(
            (candidate for candidate in self.events if candidate.id == EVENT_INSERT_FLAGS),
            None,
        )
        if event is None:
            raise FLPFormatError(f"mixer insert {self.index} is missing its flags event")
        return event

    @property
    def routing_event(self) -> Event | None:
        return next(
            (candidate for candidate in self.events if candidate.id == EVENT_INSERT_ROUTING),
            None,
        )

    @property
    def effect_slots(self) -> tuple[MixerEffectSlot, ...]:
        flags_at = self.events.index(self.flags_event)
        route_at = next(
            (
                index
                for index, event in enumerate(self.events[flags_at + 1 :], flags_at + 1)
                if event.id == EVENT_INSERT_ROUTING
            ),
            len(self.events),
        )
        pending: list[Event] = []
        slots: list[MixerEffectSlot] = []
        for event in self.events[flags_at + 1 : route_at]:
            if event.id == EVENT_EFFECT_SLOT_INDEX:
                slots.append(MixerEffectSlot(event.scalar, tuple(pending)))
                pending = []
            else:
                pending.append(event)
        if pending:
            raise FLPFormatError(
                f"mixer insert {self.index} has unterminated effect-slot state"
            )
        return tuple(slots)

    @property
    def occupied_slot_count(self) -> int:
        return sum(slot.occupied for slot in self.effect_slots)

    def routes_to(self) -> set[int]:
        route = self.routing_event
        if route is None:
            return set()
        return {
            index for index, enabled in enumerate(route.payload) if enabled != 0
        }

    def is_pristine(self) -> bool:
        active = next(
            (event.scalar for event in self.events if event.id == EVENT_INSERT_ACTIVE),
            0,
        )
        if active != 0 or self.occupied_slot_count != 0:
            return False
        if any(
            event.id in {EVENT_INSERT_COLOR, EVENT_INSERT_ICON}
            for event in self.events
        ):
            return False
        # Windows FL Studio persists generated names such as "Insert 11" on
        # otherwise untouched inserts. Those names are not user mixer state,
        # but every custom name remains protected from allocation.
        if any(
            DEFAULT_INSERT_NAME_PATTERN.fullmatch(parse_text(event.payload)) is None
            for event in self.events
            if event.id == EVENT_INSERT_NAME
        ):
            return False
        external_io = {
            event.id: event.scalar
            for event in self.events
            if event.id in {EVENT_INSERT_INPUT, EVENT_INSERT_OUTPUT}
        }
        if external_io != {
            EVENT_INSERT_INPUT: 0xFFFFFFFF,
            EVENT_INSERT_OUTPUT: 0xFFFFFFFF,
        }:
            return False
        if self.routes_to() != {0}:
            return False
        if len(self.flags_event.payload) < 8:
            return False
        flags = int.from_bytes(self.flags_event.payload[4:8], "little")
        if flags & PORTABLE_INSERT_FLAGS_MASK != DEFAULT_PORTABLE_INSERT_FLAGS:
            return False
        portable = [
            record
            for record in self.params
            if record.marker == 31
            and record.parameter_id in PORTABLE_MIXER_PARAM_IDS
        ]
        keys = [(record.parameter_id, record.slot_index) for record in portable]
        if len(keys) != len(set(keys)) or set(keys) != REQUIRED_PORTABLE_MIXER_PARAM_KEYS:
            return False
        for record in portable:
            if record.parameter_id == MIXER_PARAM_SLOT_ENABLED and record.value != 1:
                return False
            if record.parameter_id == MIXER_PARAM_SLOT_MIX and record.value != 12_800:
                return False
            expected = DEFAULT_MIXER_PARAMS.get(record.parameter_id)
            if expected is not None and record.value != expected:
                return False
        return True


def _normalized_note_payload(notes: Sequence[NoteRecord]) -> bytes:
    # FL builds playback and Piano Roll redraw indexes from event order. Keep
    # equal-position notes stable while ensuring time never moves backwards.
    return b"".join(note.raw for note in sorted(notes, key=lambda note: note.position))


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

    @property
    def mixer_insert(self) -> int:
        for event in self.events:
            if event.id == EVENT_CHANNEL_ROUTED_TO:
                return event.scalar
        return 0

    def automation_points(self) -> list[AutomationPoint]:
        event = next((event for event in self.events if event.id == EVENT_AUTOMATION_POINTS), None)
        return AutomationPoint.parse_many(event.payload) if event is not None else []

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

    def with_mixer_insert(self, insert_index: int) -> "ChannelSection":
        if not 0 <= insert_index <= 125:
            raise ValueError("mixer insert index must be between 0 and 125")
        events = list(self.events)
        for index, event in enumerate(events):
            if event.id == EVENT_CHANNEL_ROUTED_TO:
                events[index] = event.with_scalar(insert_index)
                return ChannelSection(self.iid, tuple(events))
        insert_at = next(
            (
                index + 1
                for index, event in enumerate(events)
                if event.id == EVENT_CHANNEL_NEW
            ),
            0,
        )
        events.insert(insert_at, scalar_event(EVENT_CHANNEL_ROUTED_TO, insert_index))
        return ChannelSection(self.iid, tuple(events))

    def with_sample_path(self, path: str, *, unicode_text: bool = True) -> "ChannelSection":
        replacement = text_event(EVENT_CHANNEL_SAMPLE_PATH, path, unicode_text=unicode_text)
        events = list(self.events)
        for index, event in enumerate(events):
            if event.id == EVENT_CHANNEL_SAMPLE_PATH:
                events[index] = replacement
                return ChannelSection(self.iid, tuple(events))
        events.append(replacement)
        return ChannelSection(self.iid, tuple(events))

    def with_name(self, name: str, *, unicode_text: bool = True) -> "ChannelSection":
        name = name.strip()
        if not name:
            raise ValueError("channel name cannot be empty")
        replacement = text_event(EVENT_PLUGIN_NAME, name, unicode_text=unicode_text)
        events = list(self.events)
        for index, event in enumerate(events):
            if event.id == EVENT_PLUGIN_NAME:
                events[index] = replacement
                return ChannelSection(self.iid, tuple(events))
        insert_at = next(
            (index + 1 for index, event in enumerate(events)
             if event.id == EVENT_PLUGIN_INTERNAL_NAME),
            len(events),
        )
        events.insert(insert_at, replacement)
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
        new_channels = len(self._channel_start_indices())
        if self.format == FORMAT_PROJECT and new_channels != self.channel_count:
            raise FLPFormatError(
                f"header declares {self.channel_count} channels but event stream contains {new_channels}"
            )
        for _, event in self._pattern_note_events():
            NoteRecord.parse_many(event.payload)
        if self.format == FORMAT_INSERT_STATE:
            self.validate_mixer_insert_state()

    @property
    def fl_version(self) -> str:
        for event in self.events:
            if event.id == EVENT_FL_VERSION:
                return parse_text(event.payload)
        return ""

    @property
    def tempo_bpm(self) -> float | None:
        for event in self.events:
            if event.id == EVENT_TEMPO:
                tempo = event.scalar / 1000.0
                return tempo if 10.0 <= tempo <= 999.0 else None
        return None

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
        starts = self._channel_start_indices()
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

    def automation_bindings(self) -> dict[int, AutomationBinding]:
        automation_channels = [
            section for section in self.channel_sections() if section.channel_type == 5
        ]
        event = next(
            (candidate for candidate in self.events if candidate.id == EVENT_AUTOMATION_BINDINGS),
            None,
        )
        if not automation_channels:
            return {}
        if event is None:
            raise FLPUnsupportedError("automation channels are missing their target bindings")
        records = AutomationBinding.parse_many(event.payload)
        records_by_target: dict[int, AutomationBinding] = {}
        for record in records:
            previous = records_by_target.get(record.target_event_id)
            if previous is not None and previous.raw != record.raw:
                raise FLPUnsupportedError(
                    "automation target table contains conflicting duplicate bindings"
                )
            records_by_target[record.target_event_id] = record

        links_by_iid = self.remote_controller_links()
        result: dict[int, AutomationBinding] = {}
        linked_target_ids: set[int] = set()
        unlinked_channels: list[ChannelSection] = []
        for section in automation_channels:
            links = links_by_iid.get(section.iid, ())
            if not links:
                unlinked_channels.append(section)
                continue
            primary_target_id = links[0].target_event_id
            binding = records_by_target.get(primary_target_id)
            if binding is None:
                raise FLPUnsupportedError(
                    f'automation clip "{section.name}" references a missing target binding'
                )
            result[section.iid] = binding
            linked_target_ids.update(link.target_event_id for link in links)

        unlinked_records = [
            record
            for record in records
            if record.target_event_id not in linked_target_ids
        ]
        if unlinked_channels and len(unlinked_records) != len(unlinked_channels):
            raise FLPUnsupportedError(
                "automation target bindings cannot be safely associated with the Channel Rack"
            )
        if unlinked_channels:
            result.update(
                {
                    section.iid: record
                    for section, record in zip(
                        unlinked_channels, unlinked_records, strict=True
                    )
                }
            )
        return result

    def remote_controller_links(self) -> dict[int, list[RemoteControllerLink]]:
        """Return event-227 links grouped by their Automation Clip instance ID."""
        result: dict[int, list[RemoteControllerLink]] = {}
        for event in self.events:
            if event.id != EVENT_REMOTE_CONTROLLER:
                continue
            link = RemoteControllerLink.parse(event.payload)
            result.setdefault(link.source_automation_iid, []).append(link)
        return result

    def automation_connections(
        self,
        automation_iid: int,
        *,
        allowed_target_event_ids: set[int] | None = None,
    ) -> list[AutomationConnection]:
        """Decode and optionally sanitize every connection of one clip.

        Event 227 repeats the primary event-216 destination in FL 25/26. That
        matching record is folded into the primary connection so its opaque
        formula/mapping bytes can be restored without duplicating the target.
        If filtering removes the original primary, the first retained linked
        destination becomes the new primary while keeping its event-227 state.
        """
        candidates = self.automation_connection_records(automation_iid)
        binding = candidates[0][1]
        assert binding is not None

        seen_targets: set[int] = set()
        connections: list[AutomationConnection] = []
        for role, candidate_binding, link in candidates:
            event_id = (
                candidate_binding.target_event_id
                if candidate_binding is not None
                else link.target_event_id if link is not None else -1
            )
            if event_id in seen_targets:
                raise FLPUnsupportedError(
                    f"automation channel {automation_iid} contains duplicate target connections"
                )
            seen_targets.add(event_id)
            if (
                allowed_target_event_ids is not None
                and event_id not in allowed_target_event_ids
            ):
                continue
            target = self.classify_automation_event_id(event_id)
            if target is None:
                continue
            connections.append(
                AutomationConnection(
                    role=role,
                    target=target,
                    binding=candidate_binding,
                    remote_link=link,
                )
            )

        if connections and connections[0].role != "primary":
            promoted = connections[0]
            promoted_binding = promoted.binding
            if promoted_binding is None:
                promoted_binding = binding.with_target_event_id(
                    promoted.target_event_id
                )
            connections[0] = AutomationConnection(
                role="primary",
                target=promoted.target,
                binding=promoted_binding,
                remote_link=promoted.remote_link,
            )
        return connections

    def automation_connection_records(
        self, automation_iid: int
    ) -> list[tuple[str, AutomationBinding | None, RemoteControllerLink | None]]:
        """Resolve event-227 links through FL's deduplicated event-216 table."""
        bindings = self.automation_bindings()
        primary_binding = bindings.get(automation_iid)
        if primary_binding is None:
            raise FLPUnsupportedError(
                f"automation channel {automation_iid} is missing its target binding"
            )
        remote_links = self.remote_controller_links().get(automation_iid, ())
        if not remote_links:
            return [("primary", primary_binding, None)]
        event = next(
            candidate
            for candidate in self.events
            if candidate.id == EVENT_AUTOMATION_BINDINGS
        )
        bindings_by_target = {
            binding.target_event_id: binding
            for binding in AutomationBinding.parse_many(event.payload)
        }
        candidates: list[
            tuple[str, AutomationBinding | None, RemoteControllerLink | None]
        ] = []
        for index, link in enumerate(remote_links):
            binding = bindings_by_target.get(link.target_event_id)
            if binding is None:
                raise FLPUnsupportedError(
                    f"automation channel {automation_iid} references a missing target binding"
                )
            candidates.append(
                ("primary" if index == 0 else "linked", binding, link)
            )
        return candidates

    def classify_automation_binding(
        self, binding: AutomationBinding
    ) -> AutomationTarget | None:
        """Resolve a raw FL event id to a portable capsule target."""
        return self.classify_automation_event_id(binding.target_event_id)

    def classify_automation_event_id(
        self, event_id: int
    ) -> AutomationTarget | None:
        """Resolve a raw FL event id to a portable capsule target."""
        sections = self.channel_sections()
        known_channel_ids = {section.iid for section in sections}
        target_iid = event_id >> 16
        if target_iid not in known_channel_ids:
            target_iid = None
        low_word = event_id & 0xFFFF
        if target_iid is not None:
            if low_word in PORTABLE_GENERATOR_CONTROL_IDS:
                return AutomationTarget(
                    kind="generator_parameter",
                    source_channel_iid=target_iid,
                    control_id=low_word,
                )
            if low_word < MIXER_AUTOMATION_EFFECT_PARAMETER_BASE:
                return None
            return AutomationTarget(
                kind="generator_parameter",
                source_channel_iid=target_iid,
                parameter_index=low_word - MIXER_AUTOMATION_EFFECT_PARAMETER_BASE,
            )

        if event_id < MIXER_AUTOMATION_NAMESPACE:
            return None
        relative = event_id - MIXER_AUTOMATION_NAMESPACE
        packed_target = relative >> 16
        source_insert = packed_target // MIXER_AUTOMATION_SLOT_STRIDE
        slot_index = packed_target % MIXER_AUTOMATION_SLOT_STRIDE
        if source_insert <= 0:
            return None
        try:
            inserts = {
                section.index: section for section in self.mixer_insert_sections()
            }
        except (FLPFormatError, FLPUnsupportedError):
            return None
        insert = inserts.get(source_insert)
        if insert is None:
            return None

        if low_word >= MIXER_AUTOMATION_EFFECT_PARAMETER_BASE:
            parameter_index = low_word - MIXER_AUTOMATION_EFFECT_PARAMETER_BASE
            slots = {slot.index: slot for slot in insert.effect_slots}
            slot = slots.get(slot_index)
            if slot is None or not slot.occupied:
                return None
            return AutomationTarget(
                kind="effect_parameter",
                source_insert_index=source_insert,
                slot_index=slot_index,
                parameter_index=parameter_index,
            )

        control_id = low_word - MIXER_AUTOMATION_CONTROL_BASE
        if control_id not in PORTABLE_MIXER_PARAM_IDS:
            return None
        if control_id in {MIXER_PARAM_SLOT_ENABLED, MIXER_PARAM_SLOT_MIX}:
            slots = {slot.index: slot for slot in insert.effect_slots}
            slot = slots.get(slot_index)
            if slot is None or not slot.occupied:
                return None
            return AutomationTarget(
                kind="effect_slot_control",
                source_insert_index=source_insert,
                slot_index=slot_index,
                control_id=control_id,
            )
        if slot_index != 0:
            return None
        return AutomationTarget(
            kind="insert_control",
            source_insert_index=source_insert,
            control_id=control_id,
        )

    def automation_target_description(
        self,
        target: AutomationTarget | None,
        *,
        event_id: int | None = None,
    ) -> str:
        if target is None:
            if event_id is not None and event_id >= MIXER_AUTOMATION_NAMESPACE:
                relative = event_id - MIXER_AUTOMATION_NAMESPACE
                source_insert = (relative >> 16) // MIXER_AUTOMATION_SLOT_STRIDE
                if source_insert == 0:
                    return "Master mixer control"
                if source_insert > 0:
                    return f"unsupported control on mixer insert {source_insert}"
            if event_id == 0x40000005:
                return "global tempo control"
            return (
                f"unsupported project control 0x{event_id:08X}"
                if event_id is not None
                else "unsupported project control"
            )
        if target.kind == "generator_parameter":
            name = next(
                (
                    section.name
                    for section in self.channel_sections()
                    if section.iid == target.source_channel_iid
                ),
                f"channel {target.source_channel_iid}",
            )
            if target.control_id is not None:
                return (
                    f"{name} "
                    f"{PORTABLE_GENERATOR_CONTROL_NAMES.get(target.control_id, 'control')}"
                )
            return f"{name} parameter {target.parameter_index}"
        if target.kind == "insert_control":
            control = MIXER_AUTOMATION_CONTROL_NAMES.get(
                target.control_id, f"control {target.control_id}"
            )
            return f"mixer insert {target.source_insert_index} {control}"
        slots = {}
        try:
            insert = next(
                section
                for section in self.mixer_insert_sections()
                if section.index == target.source_insert_index
            )
            slots = {slot.index: slot for slot in insert.effect_slots}
        except (StopIteration, FLPFormatError, FLPUnsupportedError):
            pass
        plugin = slots.get(target.slot_index)
        plugin_name = plugin.plugin_name if plugin is not None else "effect"
        if target.kind == "effect_parameter":
            detail = f"parameter {target.parameter_index}"
        else:
            detail = MIXER_AUTOMATION_CONTROL_NAMES.get(
                target.control_id, f"control {target.control_id}"
            )
        return (
            f"mixer insert {target.source_insert_index}, slot "
            f"{(target.slot_index or 0) + 1} ({plugin_name}) {detail}"
        )

    def playlist_items_for_channels(
        self, channel_ids: Sequence[int]
    ) -> dict[int, list[PlaylistItem]]:
        selected = set(channel_ids)
        result = {iid: [] for iid in selected}
        for item in self.playlist_items():
            if item.item_index <= item.pattern_base and item.item_index in selected:
                result[item.item_index].append(item)
        return result

    def playlist_items(self) -> list[PlaylistItem]:
        playlist_index = self._current_playlist_event_index()
        if playlist_index is None:
            return []
        return PlaylistItem.parse_many(self.events[playlist_index].payload)

    def playlist_items_for_pattern(self, pattern_id: int) -> list[PlaylistItem]:
        return [
            item for item in self.playlist_items()
            if item.pattern_id == pattern_id
        ]

    def playlist_selection(self) -> tuple[int, int] | None:
        selections = [self.events[index] for index in self._playlist_selection_indices()]
        if not selections:
            return None
        event = selections[-1]
        if len(event.payload) != 8:
            raise FLPUnsupportedError("the saved Playlist selection format is unsupported")
        start, end = struct.unpack("<II", event.payload)
        return (start, end) if end > start else None

    def _playlist_selection_indices(self) -> list[int]:
        channel_owned = {
            id(event)
            for section in self.channel_sections()
            for event in section.events
        }
        return [
            index for index, event in enumerate(self.events)
            if event.id == EVENT_PLAYLIST_SELECTION
            and id(event) not in channel_owned
        ]

    def _set_playlist_selection(self, selection: tuple[int, int] | None) -> None:
        indices = self._playlist_selection_indices()
        if selection is None:
            if indices:
                self.events[indices[-1]] = self.events[indices[-1]].with_payload(
                    struct.pack("<II", 0, 0)
                )
            return
        start, end = selection
        if start < 0 or end <= start or end > 0xFFFFFFFF:
            raise FLPUnsupportedError("the Playlist selection is invalid")
        replacement = data_event(
            EVENT_PLAYLIST_SELECTION, struct.pack("<II", start, end)
        )
        if indices:
            self.events[indices[-1]] = replacement
            return
        # Project selection is global state. Keep a newly synthesized record
        # outside Channel Rack ownership, alongside FL's other project settings.
        channel_starts = self._channel_start_indices()
        self.events.insert(channel_starts[0] if channel_starts else 0, replacement)

    def _playlist_item_size(self) -> int:
        items = self.playlist_items()
        if items:
            return items[0].record_size
        try:
            major = int(self.fl_version.split(".", 1)[0])
        except ValueError:
            major = 25
        return 88 if major >= 26 else 60 if major >= 21 else 32

    @staticmethod
    def _next_playlist_runtime_id(items: Sequence[PlaylistItem]) -> int:
        runtime_ids = [
            item.runtime_id for item in items if item.runtime_id is not None
        ]
        next_id = max(runtime_ids, default=-1) + 1
        if next_id > 0xFFFFFFFF:
            raise FLPUnsupportedError("the Playlist has no available runtime IDs")
        return next_id

    def resolve_playlist_capture_window(
        self,
        pattern_id: int,
        *,
        playhead: int,
        pattern_length_steps: int | None = None,
        selection_start: int | None = None,
        selection_end: int | None = None,
    ) -> PlaylistCaptureWindow:
        if playhead < 0:
            raise FLPUnsupportedError("the FL Studio playhead position is invalid")
        if selection_start is None and selection_end is None:
            saved_selection = self.playlist_selection()
            if saved_selection is not None:
                selection_start, selection_end = saved_selection
        has_selection = (
            selection_start is not None
            and selection_end is not None
            and selection_start >= 0
            and selection_end > selection_start
        )
        pattern_items = self.playlist_items_for_pattern(pattern_id)

        if has_selection:
            assert selection_start is not None and selection_end is not None
            cropped = tuple(
                item
                for source in pattern_items
                if (
                    item := source.crop_to_window(
                        selection_start, selection_end, ppq=self.ppq
                    )
                ) is not None
            )
            if not cropped:
                raise FLPUnsupportedError(
                    f"the selected Playlist range contains no Pattern {pattern_id} clips"
                )
            return PlaylistCaptureWindow(
                selection_start, selection_end, "selection", cropped
            )

        if pattern_items:
            containing = [
                item for item in pattern_items
                if item.position <= playhead < item.end_position
            ]
            chosen = min(
                containing,
                key=lambda item: (item.position, item.raw[12:16]),
            ) if containing else min(
                pattern_items,
                key=lambda item: (
                    item.position - playhead
                    if playhead < item.position
                    else playhead - item.end_position,
                    item.position,
                    item.raw[12:16],
                ),
            )
            if chosen.length <= 0:
                raise FLPUnsupportedError("the selected Pattern placement has no duration")
            start, end = chosen.position, chosen.end_position
            cropped = tuple(
                item
                for source in pattern_items
                if (item := source.crop_to_window(start, end, ppq=self.ppq))
                is not None
            )
            return PlaylistCaptureWindow(start, end, "playhead", cropped)

        note_end = max(
            (
                note.position + note.length
                for note in self.pattern_notes().get(pattern_id, ())
            ),
            default=0,
        )
        step_length = (
            round(pattern_length_steps * self.ppq / 4)
            if pattern_length_steps is not None and pattern_length_steps > 0
            else 0
        )
        duration = max(step_length, note_end, self.ppq * 4 if not note_end else 1)
        if playhead + duration > 0xFFFFFFFF:
            raise FLPUnsupportedError("the standalone Pattern window exceeds FL Studio limits")
        all_items = self.playlist_items()
        pattern_template = next(
            (item for item in all_items if item.is_pattern), None
        )
        if pattern_template is not None:
            item = pattern_template.as_pattern(
                pattern_id, position=0, length=duration
            )
        else:
            item = PlaylistItem.synthetic_pattern(
                pattern_id,
                position=0,
                length=duration,
                item_size=self._playlist_item_size(),
                runtime_id=self._next_playlist_runtime_id(all_items),
            )
        return PlaylistCaptureWindow(
            playhead, playhead + duration, "standalone", (item,)
        )

    def playlist_items_for_channels_in_window(
        self,
        channel_ids: Sequence[int],
        window: PlaylistCaptureWindow,
    ) -> dict[int, list[PlaylistItem]]:
        result: dict[int, list[PlaylistItem]] = {
            iid: [] for iid in set(channel_ids)
        }
        for source in self.playlist_items():
            if source.is_pattern or source.item_index not in result:
                continue
            item = source.crop_to_window(window.start, window.end, ppq=self.ppq)
            if item is not None:
                result[source.item_index].append(item)
        return result

    def _channel_start_indices(self) -> list[int]:
        # FL 26 overloads event 64 in pre-rack project state. A real channel
        # declaration contains a channel-type event before the next rack or
        # pattern boundary; the global form does not. Preview mutation may add
        # an enabled event between the declaration and its type, so do not
        # require the pair to be immediately adjacent.
        starts: list[int] = []
        for index, event in enumerate(self.events):
            if event.id != EVENT_CHANNEL_NEW:
                continue
            end = next(
                (
                    candidate
                    for candidate in range(index + 1, len(self.events))
                    if self.events[candidate].id == EVENT_CHANNEL_NEW
                    or self.events[candidate].id == EVENT_PATTERN_NEW
                    or self.events[candidate].id in POST_CHANNEL_BOUNDARY_IDS
                ),
                len(self.events),
            )
            if any(
                candidate.id == EVENT_CHANNEL_TYPE
                for candidate in self.events[index + 1 : end]
            ):
                starts.append(index)
        return starts

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
        unsupported = [section.name for section in sections if section.channel_type == 3]
        if unsupported:
            raise FLPUnsupportedError("layer channels are not supported: " + ", ".join(unsupported))
        return sections

    def channel_state(self, section: ChannelSection) -> "FLPFile":
        normalized = section.remap(0)
        version = next((event for event in self.events if event.id == EVENT_FL_VERSION), None)
        if version is None:
            raise FLPUnsupportedError("project has no FL version event for channel-state export")
        return FLPFile(
            FORMAT_CHANNEL_STATE,
            1,
            self.ppq,
            [version, *normalized.events],
        )

    def _mixer_params_event(self) -> Event:
        flags = [
            index for index, event in enumerate(self.events)
            if event.id == EVENT_INSERT_FLAGS
        ]
        if not flags:
            raise FLPUnsupportedError("the project has no supported mixer insert state")
        event = next(
            (
                candidate
                for candidate in reversed(self.events[flags[-1] + 1 :])
                if candidate.id == EVENT_MIXER_PARAMS
                and len(candidate.payload) % MIXER_PARAM_STRUCT.size == 0
            ),
            None,
        )
        if event is None:
            raise FLPUnsupportedError("the project has no supported mixer parameter table")
        return event

    def _mixer_param_base(self) -> int:
        try:
            major = int(self.fl_version.split(".", 1)[0])
        except (ValueError, TypeError):
            major = 0
        # FL 25 moved real insert parameter keys to 64..80 and reserves a
        # separate synthetic key for the Current insert.
        return 64 if major >= 25 else 0

    def _mixer_param_key(self, insert_index: int) -> int:
        base = self._mixer_param_base()
        if base:
            return base + insert_index
        # FL 24 reserves parameter selector keys 62 and 63.
        return insert_index if insert_index < 62 else insert_index + 2

    def mixer_insert_sections(self) -> list[MixerInsertSection]:
        params_event = self._mixer_params_event()
        params_at = next(
            index for index, event in enumerate(self.events) if event is params_event
        )
        flag_indices = [
            index for index, event in enumerate(self.events[:params_at])
            if event.id == EVENT_INSERT_FLAGS
        ]
        if len(flag_indices) < 2:
            raise FLPUnsupportedError(
                "the project does not contain assignable mixer inserts"
            )

        starts: list[int] = []
        for position, flags_at in enumerate(flag_indices):
            if position == 0:
                starts.append(flags_at)
                continue
            previous_flags = flag_indices[position - 1]
            inputs = [
                index
                for index in range(previous_flags + 1, flags_at)
                if self.events[index].id == EVENT_INSERT_INPUT
            ]
            starts.append(inputs[-1] if inputs else flags_at)

        records = MixerParamRecord.parse_many(params_event.payload)
        sections: list[MixerInsertSection] = []
        # The last flagged record is FL's synthetic Current insert.
        for insert_index, start in enumerate(starts[:-1]):
            end = starts[insert_index + 1]
            key = self._mixer_param_key(insert_index)
            section_params = tuple(
                record
                for record in records
                if record.marker == 31 and record.insert_key == key
            )
            if not section_params:
                raise FLPUnsupportedError(
                    f"mixer insert {insert_index} is missing parameter state"
                )
            sections.append(
                MixerInsertSection(
                    insert_index,
                    tuple(self.events[start:end]),
                    section_params,
                )
            )
        return sections

    @staticmethod
    def _portable_flags_event(event: Event) -> Event:
        if event.id != EVENT_INSERT_FLAGS or len(event.payload) < 8:
            raise FLPUnsupportedError("the mixer insert flags format is not supported")
        payload = bytearray(event.payload)
        flags = int.from_bytes(payload[4:8], "little") & PORTABLE_INSERT_FLAGS_MASK
        payload[4:8] = flags.to_bytes(4, "little")
        return event.with_payload(bytes(payload))

    @staticmethod
    def _merge_portable_flags(source: Event, destination: Event) -> Event:
        """Apply audio flags while retaining destination-only layout metadata."""
        if (
            source.id != EVENT_INSERT_FLAGS
            or destination.id != EVENT_INSERT_FLAGS
            or len(source.payload) < 8
            or len(destination.payload) < 8
        ):
            raise FLPUnsupportedError("the mixer insert flags format is not supported")
        source_flags = int.from_bytes(source.payload[4:8], "little")
        payload = bytearray(destination.payload)
        destination_flags = int.from_bytes(payload[4:8], "little")
        combined = (
            destination_flags & ~PORTABLE_INSERT_FLAGS_MASK
        ) | (source_flags & PORTABLE_INSERT_FLAGS_MASK)
        payload[4:8] = combined.to_bytes(4, "little")
        return destination.with_payload(bytes(payload))

    def mixer_insert_state(self, insert_index: int) -> "FLPFile":
        if insert_index == 0:
            raise FLPUnsupportedError("Master mixer state cannot be saved in a capsule")
        sections = {section.index: section for section in self.mixer_insert_sections()}
        section = sections.get(insert_index)
        if section is None:
            raise FLPUnsupportedError(
                f"mixer insert {insert_index} is outside the saved project"
            )
        flags_at = section.events.index(section.flags_event)
        routing_at = next(
            (
                index
                for index, event in enumerate(section.events[flags_at + 1 :], flags_at + 1)
                if event.id == EVENT_INSERT_ROUTING
            ),
            None,
        )
        if routing_at is None:
            raise FLPUnsupportedError(
                f"mixer insert {insert_index} has no supported routing boundary"
            )
        unknown_prefix = sorted(
            {
                event.id
                for event in section.events[:flags_at]
                if event.id not in PORTABLE_INSERT_PREFIX_IDS
                and event.id not in {EVENT_INSERT_INPUT, EVENT_INSERT_OUTPUT}
            }
        )
        if unknown_prefix:
            raise FLPUnsupportedError(
                f"mixer insert {insert_index} contains unprofiled header events: "
                + ", ".join(map(str, unknown_prefix))
            )
        portable_events = [
            *(
                event
                for event in section.events[:flags_at]
                if event.id in PORTABLE_INSERT_PREFIX_IDS
            ),
            self._portable_flags_event(section.flags_event),
            *section.events[flags_at + 1 : routing_at],
        ]
        slot_indices = [slot.index for slot in section.effect_slots]
        if slot_indices != list(range(10)):
            raise FLPUnsupportedError(
                f"mixer insert {insert_index} does not contain the supported 10-slot layout"
            )
        version = next(
            (event for event in self.events if event.id == EVENT_FL_VERSION),
            None,
        )
        if version is None:
            raise FLPUnsupportedError("project has no FL version event for insert-state export")
        portable_params = [
            record.raw
            for record in section.params
            if record.parameter_id in PORTABLE_MIXER_PARAM_IDS
        ]
        if not portable_params:
            raise FLPUnsupportedError(
                f"mixer insert {insert_index} has no portable parameter state"
            )
        state = FLPFile(
            FORMAT_INSERT_STATE,
            0,
            self.ppq,
            [
                version,
                *portable_events,
                data_event(EVENT_MIXER_PARAMS, b"".join(portable_params)),
            ],
        )
        state.validate()
        return state

    def mixer_effect_slots(self) -> tuple[MixerEffectSlot, ...]:
        """Return the ordered effect slots from a portable Insert-State preset."""
        self.validate_mixer_insert_state()
        flags_at = next(
            index for index, event in enumerate(self.events)
            if event.id == EVENT_INSERT_FLAGS
        )
        params_at = next(
            index for index, event in enumerate(self.events)
            if event.id == EVENT_MIXER_PARAMS
        )
        section = MixerInsertSection(
            0, tuple(self.events[flags_at:params_at]), tuple()
        )
        return section.effect_slots

    def validate_mixer_insert_state(self) -> None:
        if self.format != FORMAT_INSERT_STATE:
            raise FLPFormatError("file is not a mixer Insert-State preset")
        if any(
            event.id in {EVENT_INSERT_INPUT, EVENT_INSERT_OUTPUT, EVENT_INSERT_ROUTING}
            for event in self.events
        ):
            raise FLPFormatError("mixer Insert-State preset contains non-portable routing")
        flags = [event for event in self.events if event.id == EVENT_INSERT_FLAGS]
        if len(flags) != 1:
            raise FLPFormatError("mixer Insert-State preset must contain one flags event")
        self._portable_flags_event(flags[0])
        if int.from_bytes(flags[0].payload[4:8], "little") & ~PORTABLE_INSERT_FLAGS_MASK:
            raise FLPFormatError("mixer Insert-State preset contains non-portable flags")
        versions = [event for event in self.events if event.id == EVENT_FL_VERSION]
        if len(versions) != 1 or versions[0] is not self.events[0]:
            raise FLPFormatError("mixer Insert-State preset has an invalid FL version event")
        slots = [
            event.scalar for event in self.events if event.id == EVENT_EFFECT_SLOT_INDEX
        ]
        if slots != list(range(10)):
            raise FLPFormatError("mixer Insert-State preset must contain slots 0 through 9")
        params = [
            event
            for event in self.events
            if event.id == EVENT_MIXER_PARAMS
            and len(event.payload) % MIXER_PARAM_STRUCT.size == 0
        ]
        if len(params) != 1 or params[0] is not self.events[-1]:
            raise FLPFormatError("mixer Insert-State preset has an invalid parameter table")
        records = MixerParamRecord.parse_many(params[0].payload)
        if not records or any(
            record.marker != 31
            or record.parameter_id not in PORTABLE_MIXER_PARAM_IDS
            for record in records
        ):
            raise FLPFormatError("mixer Insert-State preset has non-portable parameters")
        keys = [(record.parameter_id, record.slot_index) for record in records]
        if (
            len(keys) != len(set(keys))
            or set(keys) != REQUIRED_PORTABLE_MIXER_PARAM_KEYS
        ):
            raise FLPFormatError(
                "mixer Insert-State preset has incomplete or duplicate parameters"
            )

    def _replace_mixer_insert_events(
        self, old: MixerInsertSection, replacement_events: Sequence[Event]
    ) -> None:
        old_ids = {id(event) for event in old.events}
        start = next(index for index, event in enumerate(self.events) if id(event) in old_ids)
        rebuilt = self.events[:start] + list(replacement_events)
        rebuilt.extend(event for event in self.events[start:] if id(event) not in old_ids)
        self.events = rebuilt

    def _replace_mixer_params(
        self,
        target_index: int,
        state: "FLPFile",
    ) -> None:
        params_event = self._mixer_params_event()
        params_at = next(
            index for index, event in enumerate(self.events) if event is params_event
        )
        records = MixerParamRecord.parse_many(params_event.payload)
        target_key = self._mixer_param_key(target_index)
        source_event = state.events[-1]
        source_records = MixerParamRecord.parse_many(source_event.payload)
        replacement = [record.remap_insert_key(target_key) for record in source_records]
        result: list[MixerParamRecord] = []
        inserted = False
        for record in records:
            is_target = (
                record.marker == 31
                and record.insert_key == target_key
                and record.parameter_id in PORTABLE_MIXER_PARAM_IDS
            )
            if is_target:
                if not inserted:
                    result.extend(replacement)
                    inserted = True
                continue
            result.append(record)
        if not inserted:
            raise FLPUnsupportedError(
                f"destination mixer insert {target_index} has no replaceable parameter state"
            )
        self.events[params_at] = params_event.with_payload(
            b"".join(record.raw for record in result)
        )

    def restore_mixer_insert_states(
        self,
        requests: Sequence[tuple["FLPFile", Sequence[int]]],
    ) -> tuple["FLPFile", list[int]]:
        if not requests:
            return self.clone(), []
        for state, channel_ids in requests:
            state.validate_mixer_insert_state()
            if not channel_ids:
                raise FLPUnsupportedError("mixer Insert-State has no destination channels")

        sections = self.mixer_insert_sections()
        channel_sections = self.channel_sections()
        reserved = {
            section.mixer_insert
            for section in channel_sections
            if section.mixer_insert > 0
        }
        for section in sections[1:]:
            reserved.update(index for index in section.routes_to() if index > 0)
        available = sorted(
            (
                section
                for section in sections[1:]
                if section.index not in reserved and section.is_pristine()
            ),
            key=lambda section: section.index,
        )
        if len(available) < len(requests):
            raise FLPUnsupportedError(
                f"import needs {len(requests)} pristine mixer insert"
                f"{'s' if len(requests) != 1 else ''}, but only {len(available)} "
                f"{'are' if len(available) != 1 else 'is'} available"
            )

        target = self.clone()
        destinations: list[int] = []
        for (state, channel_ids), chosen in zip(
            requests, available[: len(requests)], strict=True
        ):
            current = {
                section.index: section for section in target.mixer_insert_sections()
            }[chosen.index]
            flags_at = current.events.index(current.flags_event)
            routing_at = next(
                (
                    index
                    for index, event in enumerate(
                        current.events[flags_at + 1 :], flags_at + 1
                    )
                    if event.id == EVENT_INSERT_ROUTING
                ),
                None,
            )
            if routing_at is None:
                raise FLPUnsupportedError(
                    f"destination mixer insert {chosen.index} has no routing boundary"
                )
            destination_prefix = [
                event
                for event in current.events[:flags_at]
                if event.id not in PORTABLE_INSERT_PREFIX_IDS
            ]
            destination_suffix = list(current.events[routing_at:])
            state_events = [
                event
                for event in state.events[1:-1]
                if event.id != EVENT_FL_VERSION
            ]
            remapped_effect_events = {
                id(original): replacement
                for slot in state.mixer_effect_slots()
                for original, replacement in zip(
                    slot.events,
                    slot.remap_insert(chosen.index).events,
                    strict=True,
                )
            }
            state_events = [
                self._merge_portable_flags(event, current.flags_event)
                if event.id == EVENT_INSERT_FLAGS
                else remapped_effect_events.get(id(event), event)
                for event in state_events
            ]
            target._replace_mixer_insert_events(
                current,
                [*destination_prefix, *state_events, *destination_suffix],
            )
            target._replace_mixer_params(chosen.index, state)
            destination_channels = {
                section.iid: section for section in target.channel_sections()
            }
            for channel_id in channel_ids:
                channel = destination_channels.get(channel_id)
                if channel is None:
                    raise FLPUnsupportedError(
                        f"destination channel {channel_id} does not exist"
                    )
                target._replace_channel_events(
                    channel,
                    channel.with_mixer_insert(chosen.index).events,
                )
                destination_channels = {
                    section.iid: section for section in target.channel_sections()
                }
            destinations.append(chosen.index)
        target.validate()
        return target, destinations

    def _sanitize_preview_insert_routing(self, insert_indices: set[int]) -> None:
        sections = {section.index: section for section in self.mixer_insert_sections()}
        for insert_index in sorted(insert_indices):
            section = sections.get(insert_index)
            if section is None:
                raise FLPUnsupportedError(
                    f"mixer insert {insert_index} is outside the saved project"
                )
            replacement: list[Event] = []
            for event in section.events:
                if event.id in {EVENT_INSERT_INPUT, EVENT_INSERT_OUTPUT}:
                    replacement.append(event.with_scalar(0xFFFFFFFF))
                elif event.id == EVENT_INSERT_ROUTING:
                    replacement.append(event.with_payload(b"\x01"))
                else:
                    replacement.append(event)
            self._replace_mixer_insert_events(section, replacement)
            sections = {item.index: item for item in self.mixer_insert_sections()}

    def isolated_preview_project(
        self,
        channel_ids: Sequence[int],
        pattern_id: int,
        *,
        preserve_mixer_inserts: bool = False,
        automation_target_event_ids: dict[int, Sequence[int]] | None = None,
        playlist_window: PlaylistCaptureWindow | None = None,
    ) -> "FLPFile":
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

        preview_inserts: set[int] = set()
        for section in sections:
            if section.iid in selected:
                keep_insert = (
                    preserve_mixer_inserts
                    and section.channel_type != 5
                    and section.mixer_insert > 0
                )
                if keep_insert:
                    preview_inserts.add(section.mixer_insert)
                replacement = section.with_enabled(True).remap(
                    section.iid, route_to_master=not keep_insert
                )
            else:
                replacement = section.with_enabled(False)
            target._replace_channel_events(section, replacement.events)

        if preview_inserts:
            target._sanitize_preview_insert_routing(preview_inserts)

        target._filter_pattern_notes(pattern_id, selected)
        target._set_current_pattern(pattern_id)
        automation_ids = [
            section.iid for section in sections
            if section.iid in selected and section.channel_type == 5
        ]
        if automation_ids:
            if automation_target_event_ids is not None:
                target._sanitize_automation_preview_connections(
                    {
                        iid: automation_target_event_ids[iid]
                        for iid in automation_ids
                        if iid in automation_target_event_ids
                    }
                )
        if playlist_window is not None:
            target._isolate_playlist_phrase_preview(
                automation_ids, pattern_id, playlist_window
            )
            # FL stores transport loop mode as 0 = Pattern, 1 = Song.
            target._set_scalar_event(EVENT_PROJECT_LOOP_MODE, 1)
        elif automation_ids:
            target._isolate_automation_preview(automation_ids, pattern_id)
            target._set_scalar_event(EVENT_PROJECT_LOOP_MODE, 1)
        else:
            target._set_scalar_event(EVENT_PROJECT_LOOP_MODE, 0)
        target.validate()
        return target

    def _sanitize_automation_preview_connections(
        self, allowed_by_automation_iid: dict[int, Sequence[int]]
    ) -> None:
        """Remove excluded links and retarget a filtered primary for rendering."""
        if not allowed_by_automation_iid:
            return
        automation_sections = {
            section.iid: section
            for section in self.channel_sections()
            if section.channel_type == 5
        }
        bindings = self.automation_bindings()
        links_by_iid = self.remote_controller_links()
        for automation_iid, allowed in allowed_by_automation_iid.items():
            section = automation_sections.get(automation_iid)
            if section is None:
                raise FLPUnsupportedError(
                    f"automation channel {automation_iid} is missing from the preview"
                )
            if not allowed:
                raise FLPUnsupportedError(
                    f'automation clip "{section.name}" has no retained preview target'
                )
            if (
                not links_by_iid.get(automation_iid)
                and bindings[automation_iid].target_event_id not in allowed
            ):
                raise FLPUnsupportedError(
                    f'automation clip "{section.name}" cannot promote a target without an internal-controller link'
                )

        sanitized: list[Event] = []
        for event in self.events:
            if event.id != EVENT_REMOTE_CONTROLLER:
                sanitized.append(event)
                continue
            link = RemoteControllerLink.parse(event.payload)
            allowed = allowed_by_automation_iid.get(link.source_automation_iid)
            if allowed is None or link.target_event_id in allowed:
                sanitized.append(event)
        self.events = sanitized

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

    def _current_playlist_event_index(self) -> int | None:
        starts = [
            (index, event.scalar) for index, event in enumerate(self.events)
            if event.id == EVENT_ARRANGEMENT_NEW
        ]
        if not starts:
            channel_owned = {
                id(event)
                for section in self.channel_sections()
                for event in section.events
            }
            return next(
                (
                    index for index, event in enumerate(self.events)
                    if event.id == EVENT_PLAYLIST and id(event) not in channel_owned
                ),
                None,
            )
        current = next(
            (event.scalar for event in self.events if event.id == EVENT_CURRENT_ARRANGEMENT),
            starts[0][1],
        )
        current_position = next(
            (position for position, (_, arrangement_id) in enumerate(starts)
             if arrangement_id == current),
            None,
        )
        if current_position is None:
            raise FLPUnsupportedError("the current Playlist arrangement could not be identified")
        start = starts[current_position][0]
        end = (
            starts[current_position + 1][0]
            if current_position + 1 < len(starts)
            else len(self.events)
        )
        return next(
            (
                index for index in range(start, end)
                if self.events[index].id == EVENT_PLAYLIST
            ),
            None,
        )

    def _ensure_current_playlist_event(self) -> int:
        existing = self._current_playlist_event_index()
        if existing is not None:
            return existing
        starts = [
            (index, event.scalar) for index, event in enumerate(self.events)
            if event.id == EVENT_ARRANGEMENT_NEW
        ]
        if not starts:
            arrangement_id = 0
            self.events.extend(
                [
                    scalar_event(EVENT_ARRANGEMENT_NEW, arrangement_id),
                    data_event(EVENT_PLAYLIST, b""),
                    scalar_event(EVENT_CURRENT_ARRANGEMENT, arrangement_id),
                ]
            )
            return len(self.events) - 2

        current = next(
            (event.scalar for event in self.events if event.id == EVENT_CURRENT_ARRANGEMENT),
            starts[0][1],
        )
        current_position = next(
            (position for position, (_, arrangement_id) in enumerate(starts)
             if arrangement_id == current),
            None,
        )
        if current_position is None:
            raise FLPUnsupportedError(
                "the current Playlist arrangement could not be identified"
            )
        insert_at = (
            starts[current_position + 1][0]
            if current_position + 1 < len(starts)
            else next(
                (
                    index for index in range(starts[current_position][0] + 1, len(self.events))
                    if self.events[index].id == EVENT_CURRENT_ARRANGEMENT
                ),
                len(self.events),
            )
        )
        self.events.insert(insert_at, data_event(EVENT_PLAYLIST, b""))
        return insert_at

    def _isolate_playlist_phrase_preview(
        self,
        automation_ids: Sequence[int],
        pattern_id: int,
        window: PlaylistCaptureWindow,
    ) -> None:
        selected_automation = set(automation_ids)
        items: list[PlaylistItem] = []
        found_automation = {iid: False for iid in selected_automation}
        for source in self.playlist_items():
            is_pattern = source.pattern_id == pattern_id
            is_automation = (
                not source.is_pattern and source.item_index in selected_automation
            )
            if not (is_pattern or is_automation):
                continue
            item = source.crop_to_window(window.start, window.end, ppq=self.ppq)
            if item is None:
                continue
            items.append(item)
            if is_automation:
                found_automation[source.item_index] = True

        missing = [iid for iid, found in found_automation.items() if not found]
        if missing:
            raise FLPUnsupportedError(
                "selected automation clips are not placed in the captured Playlist phrase: "
                + ", ".join(map(str, sorted(missing)))
            )
        if not any(item.pattern_id == pattern_id for item in items):
            # Standalone patterns have no source Playlist record to visit above.
            items.extend(window.pattern_items)
        if not items:
            raise FLPUnsupportedError("the captured Playlist phrase has no clips")

        actual_end = max(item.end_position for item in items)
        if actual_end < window.duration:
            boundary = window.pattern_items[0].as_pattern(
                pattern_id,
                position=window.duration - 1,
                length=1,
            ).with_muted(True).with_runtime_id(
                self._next_playlist_runtime_id(items)
            )
            items.append(boundary)
        playlist_index = self._ensure_current_playlist_event()
        self.events[playlist_index] = self.events[playlist_index].with_payload(
            b"".join(item.raw for item in items)
        )
        # Playlist clips are normalized to the start of the staged project.
        # FL's command-line renderer honors a persisted time selection, so
        # leaving the source coordinates here renders an empty range as silence.
        self._set_playlist_selection(
            (0, window.duration) if window.explicit_selection else None
        )

    def _isolate_automation_preview(
        self, automation_ids: Sequence[int], pattern_id: int
    ) -> None:
        items_by_channel = self.playlist_items_for_channels(automation_ids)
        missing = [iid for iid, items in items_by_channel.items() if not items]
        if missing:
            raise FLPUnsupportedError(
                "selected automation clips are not placed in the current Playlist arrangement: "
                + ", ".join(map(str, sorted(missing)))
            )
        playlist_index = self._current_playlist_event_index()
        if playlist_index is None:
            raise FLPUnsupportedError("the current Playlist arrangement has no clip data")
        source_items = [item for items in items_by_channel.values() for item in items]
        source_anchor = min(item.position for item in source_items)
        automation_items = [
            item.remap_channel(
                item.item_index,
                source_anchor=source_anchor,
                destination_anchor=0,
                source_ppq=self.ppq,
                destination_ppq=self.ppq,
            )
            for item in source_items
        ]
        note_end = max(
            (
                note.position + note.length
                for note in self.pattern_notes().get(pattern_id, ())
            ),
            default=self.ppq * 4,
        )
        automation_end = max(
            (item.position + item.length for item in automation_items),
            default=note_end,
        )
        pattern_item = source_items[0].as_pattern(
            pattern_id, position=0, length=max(note_end, automation_end)
        )
        self.events[playlist_index] = self.events[playlist_index].with_payload(
            b"".join(item.raw for item in (pattern_item, *automation_items))
        )

    def append_capsule(
        self,
        sections: Sequence[ChannelSection],
        notes_by_source_channel: dict[int, Sequence[NoteRecord]],
        *,
        source_ppq: int,
        pattern_name: str,
        target_pattern_id: int | None = None,
        automation_bindings: dict[int, AutomationBinding] | None = None,
        automation_targets: dict[int, AutomationTarget] | None = None,
        automation_remote_links: dict[
            int,
            Sequence[
                tuple[AutomationTarget, AutomationBinding, RemoteControllerLink]
            ],
        ] | None = None,
        automation_playlist_items: dict[int, Sequence[PlaylistItem]] | None = None,
        mixer_insert_mapping: dict[int, int] | None = None,
        playlist_anchor: int = 0,
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
                data_event(EVENT_PATTERN_NOTES, _normalized_note_payload(imported_notes)),
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
        target._append_automation_support(
            sections,
            mapping,
            automation_bindings or {},
            automation_targets or {},
            automation_remote_links or {},
            automation_playlist_items or {},
            mixer_insert_mapping=mixer_insert_mapping or {},
            source_ppq=source_ppq,
            destination_anchor=playlist_anchor,
        )
        target._set_current_pattern(pattern_id)
        target.validate()
        return target, mapping, pattern_id

    def append_automation_channels(
        self,
        sections: Sequence[ChannelSection],
        *,
        target_mapping: dict[int, int],
        bindings: dict[int, AutomationBinding],
        targets: dict[int, AutomationTarget] | None = None,
        remote_links: dict[
            int,
            Sequence[
                tuple[AutomationTarget, AutomationBinding, RemoteControllerLink]
            ],
        ] | None = None,
        mixer_insert_mapping: dict[int, int] | None = None,
        playlist_items: dict[int, Sequence[PlaylistItem]],
        source_ppq: int,
        playlist_anchor: int,
        playlist_source_anchor: int | None = None,
    ) -> tuple["FLPFile", dict[int, int]]:
        target = self.clone()
        existing_ids = [section.iid for section in target.channel_sections()]
        next_channel = max(existing_ids, default=-1) + 1
        automation_mapping = {
            section.iid: next_channel + offset
            for offset, section in enumerate(sections)
        }
        new_events: list[Event] = []
        for section in sections:
            new_events.extend(section.remap(automation_mapping[section.iid]).events)
        insert_at = target._channel_insert_index()
        target.events[insert_at:insert_at] = new_events
        target.channel_count += len(sections)
        complete_mapping = {**target_mapping, **automation_mapping}
        target._append_automation_support(
            sections,
            complete_mapping,
            bindings,
            targets or {},
            remote_links or {},
            playlist_items,
            mixer_insert_mapping=mixer_insert_mapping or {},
            source_ppq=source_ppq,
            destination_anchor=playlist_anchor,
            source_anchor=playlist_source_anchor,
        )
        target.validate()
        return target, complete_mapping

    def append_playlist_phrase(
        self,
        items: Sequence[PlaylistItem],
        *,
        source_pattern_id: int,
        destination_pattern_id: int,
        source_ppq: int,
        playlist_anchor: int,
    ) -> "FLPFile":
        """Append normalized Pattern placements relative to a destination playhead."""
        if source_ppq <= 0:
            raise FLPUnsupportedError("the capsule Playlist PPQ is invalid")
        if playlist_anchor < 0:
            raise FLPUnsupportedError("the destination Playlist playhead is invalid")
        if not items:
            raise FLPUnsupportedError("the capsule Playlist phrase has no Pattern clips")
        if any(item.pattern_id != source_pattern_id for item in items):
            raise FLPUnsupportedError(
                "the capsule Playlist phrase references an unexpected Pattern"
            )

        target = self.clone()
        playlist_index = target._ensure_current_playlist_event()
        playlist = target.events[playlist_index]
        destination_items = PlaylistItem.parse_many(playlist.payload)
        destination_template = destination_items[0] if destination_items else None
        destination_size = (
            destination_template.record_size
            if destination_template is not None
            else target._playlist_item_size()
        )
        remapped = [
            item.remap_pattern(
                destination_pattern_id,
                source_anchor=0,
                destination_anchor=playlist_anchor,
                source_ppq=source_ppq,
                destination_ppq=target.ppq,
            ).adapt_size(destination_size, template=destination_template)
            for item in items
        ]
        next_runtime_id = target._next_playlist_runtime_id(destination_items)
        remapped = [
            item.with_runtime_id(next_runtime_id + index)
            for index, item in enumerate(remapped)
        ]
        target.events[playlist_index] = playlist.with_payload(
            playlist.payload + b"".join(item.raw for item in remapped)
        )
        target.validate()
        return target

    def _append_automation_support(
        self,
        sections: Sequence[ChannelSection],
        mapping: dict[int, int],
        bindings: dict[int, AutomationBinding],
        targets: dict[int, AutomationTarget],
        remote_links: dict[
            int,
            Sequence[
                tuple[AutomationTarget, AutomationBinding, RemoteControllerLink]
            ],
        ],
        playlist_items: dict[int, Sequence[PlaylistItem]],
        *,
        mixer_insert_mapping: dict[int, int],
        source_ppq: int,
        destination_anchor: int,
        source_anchor: int | None = None,
    ) -> None:
        automation_sections = [section for section in sections if section.channel_type == 5]
        if not automation_sections:
            return
        source_channel_ids = set(mapping)
        remapped_bindings: list[tuple[AutomationBinding, bool]] = []
        for section in automation_sections:
            binding = bindings.get(section.iid)
            if binding is None:
                raise FLPUnsupportedError(
                    f'automation clip "{section.name}" is missing its target binding'
                )
            semantic_target = targets.get(section.iid)
            if semantic_target is None:
                target_source_iid = binding.target_channel_iid(source_channel_ids)
                if target_source_iid is None or target_source_iid not in mapping:
                    raise FLPUnsupportedError(
                        f'automation clip "{section.name}" has no portable target metadata'
                    )
                remapped_bindings.append(
                    (
                        binding.remap_target_channel(mapping[target_source_iid]),
                        bool(remote_links.get(section.iid)),
                    )
                )
            else:
                remapped_bindings.append(
                    (
                        binding.with_target_event_id(
                            semantic_target.target_event_id(
                                channel_mapping=mapping,
                                insert_mapping=mixer_insert_mapping,
                            )
                        ),
                        bool(remote_links.get(section.iid)),
                    )
                )
            for linked_target, linked_binding, _ in remote_links.get(
                section.iid, ()
            ):
                remapped_bindings.append(
                    (
                        linked_binding.with_target_event_id(
                            linked_target.target_event_id(
                                channel_mapping=mapping,
                                insert_mapping=mixer_insert_mapping,
                            )
                        ),
                        True,
                    )
                )

        binding_index = next(
            (
                index for index, event in enumerate(self.events)
                if event.id == EVENT_AUTOMATION_BINDINGS
            ),
            None,
        )
        existing_bindings = (
            AutomationBinding.parse_many(self.events[binding_index].payload)
            if binding_index is not None
            else []
        )
        existing_target_ids = {
            binding.target_event_id for binding in existing_bindings
        }
        unique_new_bindings: list[AutomationBinding] = []
        deduplicated_by_target: dict[int, AutomationBinding] = {}
        for binding, deduplicate in remapped_bindings:
            if not deduplicate:
                unique_new_bindings.append(binding)
                continue
            if binding.target_event_id in existing_target_ids:
                continue
            previous = deduplicated_by_target.get(binding.target_event_id)
            if previous is not None:
                if previous.raw != binding.raw:
                    raise FLPUnsupportedError(
                        "imported automation connections contain conflicting target state"
                    )
                continue
            deduplicated_by_target[binding.target_event_id] = binding
            unique_new_bindings.append(binding)
        payload = b"".join(
            binding.raw for binding in unique_new_bindings
        )
        if binding_index is None:
            insert_at = next(
                (index for index, event in enumerate(self.events) if event.id == EVENT_CHANNEL_NEW),
                0,
            )
            if payload:
                self.events.insert(
                    insert_at, data_event(EVENT_AUTOMATION_BINDINGS, payload)
                )
        elif payload:
            event = self.events[binding_index]
            self.events[binding_index] = event.with_payload(event.payload + payload)

        remapped_links: list[RemoteControllerLink] = []
        for section in automation_sections:
            for semantic_target, _, link in remote_links.get(section.iid, ()):
                remapped_links.append(
                    link.remap(
                        source_automation_iid=mapping[section.iid],
                        target_event_id=semantic_target.target_event_id(
                            channel_mapping=mapping,
                            insert_mapping=mixer_insert_mapping,
                        ),
                    )
                )
        if remapped_links:
            existing_link_indices = [
                index
                for index, event in enumerate(self.events)
                if event.id == EVENT_REMOTE_CONTROLLER
            ]
            if existing_link_indices:
                link_insert_at = existing_link_indices[-1] + 1
            else:
                link_insert_at = next(
                    (
                        index
                        for index, event in enumerate(self.events)
                        if event.id == EVENT_CHANNEL_NEW
                    ),
                    0,
                )
            self.events[link_insert_at:link_insert_at] = [
                data_event(EVENT_REMOTE_CONTROLLER, link.raw)
                for link in remapped_links
            ]

        source_items = [
            item
            for section in automation_sections
            for item in playlist_items.get(section.iid, ())
        ]
        missing_items = [
            section.name
            for section in automation_sections
            if not playlist_items.get(section.iid)
        ]
        if missing_items:
            raise FLPUnsupportedError(
                "automation clips are not placed in the captured Playlist arrangement: "
                + ", ".join(missing_items)
            )
        playlist_index = self._ensure_current_playlist_event()
        if source_anchor is None:
            source_anchor = min(item.position for item in source_items)
        remapped_items = [
            item.remap_channel(
                mapping[source_iid],
                source_anchor=source_anchor,
                destination_anchor=destination_anchor,
                source_ppq=source_ppq,
                destination_ppq=self.ppq,
            )
            for source_iid in (section.iid for section in automation_sections)
            for item in playlist_items[source_iid]
        ]
        playlist = self.events[playlist_index]
        destination_items = PlaylistItem.parse_many(playlist.payload)
        destination_template = destination_items[0] if destination_items else None
        destination_size = (
            destination_template.record_size
            if destination_template is not None
            else remapped_items[0].record_size
        )
        remapped_items = [
            item.adapt_size(destination_size, template=destination_template)
            for item in remapped_items
        ]
        next_runtime_id = self._next_playlist_runtime_id(destination_items)
        remapped_items = [
            item.with_runtime_id(next_runtime_id + index)
            for index, item in enumerate(remapped_items)
        ]
        self.events[playlist_index] = playlist.with_payload(
            playlist.payload + b"".join(item.raw for item in remapped_items)
        )

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
                self.events[index] = event.with_payload(_normalized_note_payload(kept))
                found = True
        if not found:
            raise FLPUnsupportedError(f"pattern {pattern_id} has no note event to render")

    def _replace_pattern_channel_notes(self, pattern_id: int, channels: set[int], replacement_notes: Sequence[NoteRecord]) -> None:
        note_events = {id(event) for pattern, event in self._pattern_note_events() if pattern == pattern_id}
        for index, event in enumerate(self.events):
            if id(event) in note_events:
                kept = [note for note in NoteRecord.parse_many(event.payload) if note.rack_channel not in channels]
                payload = _normalized_note_payload((*kept, *replacement_notes))
                self.events[index] = event.with_payload(payload)
                return
        self.events.extend(
            [
                scalar_event(EVENT_PATTERN_NEW, pattern_id),
                data_event(
                    EVENT_PATTERN_NOTES,
                    _normalized_note_payload(replacement_notes),
                ),
            ]
        )

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
                notes = [
                    *NoteRecord.parse_many(event.payload),
                    *imported_notes,
                ]
                self.events[index] = event.with_payload(
                    _normalized_note_payload(notes)
                )
                return
        self.events.extend(
            [
                scalar_event(EVENT_PATTERN_NEW, pattern_id),
                data_event(
                    EVENT_PATTERN_NOTES,
                    _normalized_note_payload(imported_notes),
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
