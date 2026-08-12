from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import struct
import tempfile
import uuid
import zipfile

from .flp import (
    AutomationBinding,
    AutomationConnection,
    AutomationTarget,
    FLPFile,
    FLPUnsupportedError,
    MIXER_PARAM_SLOT_ENABLED,
    MIXER_PARAM_SLOT_MIX,
    NoteRecord,
    PlaylistItem,
    PORTABLE_GENERATOR_CONTROL_IDS,
    PORTABLE_MIXER_PARAM_IDS,
)


CAPSULE_SCHEMA_VERSION = 5
MAX_ARCHIVE_MEMBERS = 4096
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 8 * 1024 * 1024 * 1024
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_PREVIEW_BYTES = 512 * 1024 * 1024
MAX_CHANNEL_STATE_BYTES = 512 * 1024 * 1024
MAX_NOTES_BYTES = 256 * 1024 * 1024
MAX_AUTOMATION_METADATA_BYTES = 64 * 1024 * 1024
MAX_MIXER_INSERT_STATE_BYTES = 512 * 1024 * 1024
CAPSULE_EXTENSION = ".flcapsule.wav"
LEGACY_CAPSULE_EXTENSION = ".flcapsule"
SCAP_CHUNK_ID = b"SCAP"
SCAP_MAGIC = b"FLCAPS01"
# SCAP data: 8-byte magic, uint64 little-endian encoded-ZIP length,
# 32-byte SHA-256 of the encoded bytes, then the encoded ZIP payload.
SCAP_HEADER_SIZE = 8 + 8 + 32
# Keep the embedded ZIP from making the whole WAV a generic ZIP polyglot. This
# is a reversible container encoding, not encryption or a security boundary.
SCAP_XOR_BYTE = 0xA5
SCAP_XOR_TABLE = bytes.maketrans(
    bytes(range(256)), bytes(value ^ SCAP_XOR_BYTE for value in range(256))
)
MAX_RIFF_SIZE = 0xFFFFFFFF


def is_capsule_filename(path: Path | str) -> bool:
    name = Path(path).name.casefold()
    return name.endswith(CAPSULE_EXTENSION) or name.endswith(LEGACY_CAPSULE_EXTENSION)


@dataclass(frozen=True, slots=True)
class WaveContainerInfo:
    riff_kind: bytes
    file_size: int
    scap_offset: int
    scap_size: int
    payload_offset: int
    payload_size: int
    payload_digest: bytes
    ds64_riff_size_offset: int | None = None


class _FileSlice:
    """Seekable bounded view used by ZipFile without loading the payload."""

    def __init__(
        self, path: Path, offset: int, length: int, *, xor_byte: int | None = None
    ):
        self._file = path.open("rb")
        self._offset = offset
        self._length = length
        self._position = 0
        self._xor_byte = xor_byte

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = self._length - self._position
        size = min(size, self._length - self._position)
        if size <= 0:
            return b""
        self._file.seek(self._offset + self._position)
        data = self._file.read(size)
        self._position += len(data)
        if self._xor_byte is None:
            return data
        return data.translate(SCAP_XOR_TABLE)

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        if whence == os.SEEK_SET:
            position = offset
        elif whence == os.SEEK_CUR:
            position = self._position + offset
        elif whence == os.SEEK_END:
            position = self._length + offset
        else:
            raise ValueError("invalid seek mode")
        if position < 0:
            raise ValueError("negative seek position")
        self._position = min(position, self._length)
        return self._position

    def tell(self) -> int:
        return self._position

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def close(self) -> None:
        self._file.close()

    @property
    def closed(self) -> bool:
        return self._file.closed

    def __enter__(self) -> "_FileSlice":
        return self

    def __exit__(self, *args) -> None:
        self.close()


def slugify(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return value or "capsule"


@dataclass(slots=True)
class ChannelManifest:
    source_iid: int
    name: str
    plugin_name: str
    channel_type: int | None
    state_path: str
    notes_path: str
    sample_asset: str | None = None


@dataclass(slots=True)
class AutomationTargetManifest:
    role: str
    target_kind: str
    state_path: str
    source_channel_iid: int | None = None
    source_insert_index: int | None = None
    slot_index: int | None = None
    parameter_index: int | None = None
    control_id: int | None = None

    def to_flp_target(self) -> AutomationTarget:
        return AutomationTarget(
            kind=self.target_kind,
            source_channel_iid=self.source_channel_iid,
            source_insert_index=self.source_insert_index,
            slot_index=self.slot_index,
            parameter_index=self.parameter_index,
            control_id=self.control_id,
        )


@dataclass(slots=True)
class AutomationManifest:
    source_iid: int
    playlist_path: str
    targets: list[AutomationTargetManifest] = field(default_factory=list)
    # Schemas 1-4 stored a singular Channel Rack target directly here.
    target_source_iid: int | None = None
    binding_path: str | None = None


@dataclass(slots=True)
class MixerInsertManifest:
    source_index: int
    channel_source_iids: list[int]
    state_path: str


@dataclass(slots=True)
class CapsuleManifest:
    id: str
    schema_version: int
    name: str
    created_at: str
    source_fl_version: str
    source_ppq: int
    source_pattern: int
    save_mode: str
    channels: list[ChannelManifest]
    automations: list[AutomationManifest] = field(default_factory=list)
    mixer_inserts: list[MixerInsertManifest] = field(default_factory=list)
    source_pattern_length_steps: int | None = None
    source_tempo_bpm: float | None = None
    preview_path: str = "preview.wav"
    tags: list[str] = field(default_factory=list)
    favorite: bool = False
    draft: bool = False

    @classmethod
    def create(
        cls,
        *,
        name: str,
        project: FLPFile,
        pattern_id: int,
        pattern_length_steps: int | None,
        save_mode: str,
        channels: list[ChannelManifest],
        automations: list[AutomationManifest] | None = None,
        mixer_inserts: list[MixerInsertManifest] | None = None,
    ) -> "CapsuleManifest":
        return cls(
            id=str(uuid.uuid4()),
            schema_version=CAPSULE_SCHEMA_VERSION,
            name=name,
            created_at=datetime.now(timezone.utc).isoformat(),
            source_fl_version=project.fl_version,
            source_ppq=project.ppq,
            source_pattern=pattern_id,
            save_mode=save_mode,
            channels=channels,
            automations=list(automations or []),
            mixer_inserts=list(mixer_inserts or []),
            source_pattern_length_steps=pattern_length_steps,
            source_tempo_bpm=project.tempo_bpm,
        )

    def to_dict(self) -> dict:
        payload = asdict(self)
        for automation in payload["automations"]:
            if self.schema_version >= 5:
                automation.pop("target_source_iid", None)
                automation.pop("binding_path", None)
            else:
                automation.pop("targets", None)
        return payload

    @classmethod
    def from_dict(cls, payload: dict) -> "CapsuleManifest":
        if not isinstance(payload, dict):
            raise ValueError("capsule manifest must be a JSON object")
        values = dict(payload)
        if "source_pattern_length_steps" not in values:
            values["source_pattern_length_steps"] = values.pop("source_pattern_length_beats", None)
        else:
            values.pop("source_pattern_length_beats", None)
        channels_payload = values.pop("channels", None)
        if not isinstance(channels_payload, list):
            raise ValueError("capsule manifest channels must be a list")
        automations_payload = values.pop("automations", [])
        if not isinstance(automations_payload, list):
            raise ValueError("capsule manifest automations must be a list")
        mixer_inserts_payload = values.pop("mixer_inserts", [])
        if not isinstance(mixer_inserts_payload, list):
            raise ValueError("capsule manifest mixer_inserts must be a list")
        channels = [ChannelManifest(**item) for item in channels_payload]
        automations: list[AutomationManifest] = []
        for item in automations_payload:
            if not isinstance(item, dict):
                raise ValueError("capsule automation metadata must be an object")
            automation_values = dict(item)
            targets_payload = automation_values.pop("targets", [])
            if not isinstance(targets_payload, list):
                raise ValueError("capsule automation targets must be a list")
            targets = [AutomationTargetManifest(**target) for target in targets_payload]
            automations.append(
                AutomationManifest(targets=targets, **automation_values)
            )
        mixer_inserts = [
            MixerInsertManifest(**item) for item in mixer_inserts_payload
        ]
        manifest = cls(
            channels=channels,
            automations=automations,
            mixer_inserts=mixer_inserts,
            **values,
        )
        manifest.validate()
        return manifest

    def validate(self) -> None:
        if self.schema_version not in set(range(1, CAPSULE_SCHEMA_VERSION + 1)):
            relation = "newer" if self.schema_version > CAPSULE_SCHEMA_VERSION else "unsupported legacy"
            raise ValueError(f"{relation} capsule schema {self.schema_version}; supported schema is {CAPSULE_SCHEMA_VERSION}")
        try:
            uuid.UUID(self.id)
        except (ValueError, TypeError) as error:
            raise ValueError("capsule manifest has an invalid id") from error
        if not self.name.strip():
            raise ValueError("capsule name cannot be empty")
        if self.source_ppq <= 0:
            raise ValueError("capsule PPQ must be positive")
        if self.source_pattern_length_steps is not None and self.source_pattern_length_steps <= 0:
            raise ValueError("capsule pattern length must be positive")
        if self.schema_version >= 2 and (
            self.source_tempo_bpm is None or not 10.0 <= self.source_tempo_bpm <= 999.0
        ):
            raise ValueError("capsule source tempo must be between 10 and 999 BPM")
        if self.save_mode not in {"group", "individual"}:
            raise ValueError("capsule save_mode must be group or individual")
        if not self.channels:
            raise ValueError("capsule must contain at least one channel")
        source_ids = [channel.source_iid for channel in self.channels]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("capsule contains duplicate source channel ids")
        channels_by_id = {channel.source_iid: channel for channel in self.channels}
        paths = [self.preview_path]
        for channel in self.channels:
            paths.extend([channel.state_path, channel.notes_path])
            if channel.sample_asset:
                paths.append(channel.sample_asset)
        automation_ids = [automation.source_iid for automation in self.automations]
        if len(automation_ids) != len(set(automation_ids)):
            raise ValueError("capsule contains duplicate automation metadata")
        for automation in self.automations:
            if automation.source_iid not in source_ids:
                raise ValueError("automation metadata references a missing channel")
            if channels_by_id[automation.source_iid].channel_type != 5:
                raise ValueError("automation metadata references a non-automation channel")
            paths.append(automation.playlist_path)
            if self.schema_version < 5:
                if automation.targets:
                    raise ValueError("legacy capsule schemas cannot contain automation target lists")
                if automation.target_source_iid not in source_ids:
                    raise ValueError("automation target is not included in the capsule")
                if channels_by_id[automation.target_source_iid].channel_type == 5:
                    raise ValueError("automation target must be a generator or sampler channel")
                if not automation.binding_path:
                    raise ValueError("automation target binding is missing")
                paths.append(automation.binding_path)
                continue
            if automation.target_source_iid is not None or automation.binding_path is not None:
                raise ValueError("schema-5 automation cannot use legacy singular target fields")
            if not automation.targets:
                raise ValueError("schema-5 automation requires at least one target")
            if automation.targets[0].role != "primary" or sum(
                target.role == "primary" for target in automation.targets
            ) != 1:
                raise ValueError(
                    "schema-5 automation requires exactly one primary connection"
                )
            target_paths: list[str] = []
            target_identities: list[tuple[object, ...]] = []
            for target in automation.targets:
                if target.role not in {"primary", "linked"}:
                    raise ValueError("unsupported automation connection role")
                if target.target_kind == "generator_parameter":
                    channel = channels_by_id.get(target.source_channel_iid)
                    if channel is None or channel.channel_type == 5:
                        raise ValueError("automation generator target is not included")
                    if target.source_insert_index is not None or target.slot_index is not None:
                        raise ValueError("automation generator target contains mixer identity")
                    plugin_parameter = (
                        target.parameter_index is not None
                        and 0 <= target.parameter_index <= 0x7FFF
                        and target.control_id is None
                    )
                    channel_control = (
                        target.parameter_index is None
                        and target.control_id in PORTABLE_GENERATOR_CONTROL_IDS
                    )
                    if not (plugin_parameter or channel_control):
                        raise ValueError("automation generator parameter is invalid")
                elif target.target_kind in {
                    "insert_control", "effect_parameter", "effect_slot_control"
                }:
                    if target.source_insert_index is None or target.source_insert_index <= 0:
                        raise ValueError("automation mixer target cannot reference Master")
                    if target.source_channel_iid is not None:
                        raise ValueError("automation mixer target contains channel identity")
                    if target.target_kind == "insert_control":
                        if (
                            target.slot_index is not None
                            or target.control_id
                            not in PORTABLE_MIXER_PARAM_IDS
                            - {MIXER_PARAM_SLOT_ENABLED, MIXER_PARAM_SLOT_MIX}
                            or target.parameter_index is not None
                        ):
                            raise ValueError("automation insert control target is invalid")
                    elif target.slot_index is None or not 0 <= target.slot_index < 10:
                        raise ValueError("automation effect slot target is invalid")
                    elif target.target_kind == "effect_parameter" and (
                        target.parameter_index is None
                        or not 0 <= target.parameter_index <= 0x7FFF
                        or target.control_id is not None
                    ):
                        raise ValueError("automation effect parameter is invalid")
                    elif target.target_kind == "effect_slot_control" and (
                        target.control_id
                        not in {MIXER_PARAM_SLOT_ENABLED, MIXER_PARAM_SLOT_MIX}
                        or target.parameter_index is not None
                    ):
                        raise ValueError("automation effect-slot control is invalid")
                else:
                    raise ValueError("automation target kind is unsupported")
                if not re.fullmatch(r"automation/[A-Za-z0-9._-]+\.bin", target.state_path):
                    raise ValueError("automation target has an invalid state path")
                target_paths.append(target.state_path)
                target_identities.append(
                    (
                        target.target_kind,
                        target.source_channel_iid,
                        target.source_insert_index,
                        target.slot_index,
                        target.parameter_index,
                        target.control_id,
                    )
                )
            if len(target_paths) != len(set(target_paths)):
                raise ValueError("automation target paths must be unique")
            if len(target_identities) != len(set(target_identities)):
                raise ValueError("automation targets must be unique")
            paths.extend(target_paths)
        if self.schema_version >= 3 and set(automation_ids) != {
            channel.source_iid for channel in self.channels if channel.channel_type == 5
        }:
            raise ValueError("capsule automation channels require matching automation metadata")
        mixer_indices = [insert.source_index for insert in self.mixer_inserts]
        if len(mixer_indices) != len(set(mixer_indices)):
            raise ValueError("capsule contains duplicate source mixer inserts")
        if mixer_indices != sorted(mixer_indices):
            raise ValueError("capsule mixer inserts must be ordered by source index")
        associated_channels: list[int] = []
        for insert in self.mixer_inserts:
            if insert.source_index <= 0:
                raise ValueError("capsule mixer inserts cannot include Master")
            if not insert.channel_source_iids:
                raise ValueError("capsule mixer insert has no associated channels")
            if not re.fullmatch(r"mixer/[A-Za-z0-9._-]+\.fst", insert.state_path):
                raise ValueError("capsule mixer insert has an invalid state path")
            if len(insert.channel_source_iids) != len(set(insert.channel_source_iids)):
                raise ValueError("capsule mixer insert contains duplicate channel associations")
            for source_iid in insert.channel_source_iids:
                channel = channels_by_id.get(source_iid)
                if channel is None:
                    raise ValueError("capsule mixer insert references a missing channel")
                if channel.channel_type == 5:
                    raise ValueError("capsule mixer insert cannot target an automation channel")
            associated_channels.extend(insert.channel_source_iids)
            paths.append(insert.state_path)
        if len(associated_channels) != len(set(associated_channels)):
            raise ValueError("capsule channel is associated with multiple mixer inserts")
        if self.schema_version < 4 and self.mixer_inserts:
            raise ValueError("legacy capsule schemas cannot contain mixer insert state")
        if self.schema_version >= 5:
            saved_insert_indices = set(mixer_indices)
            for automation in self.automations:
                for target in automation.targets:
                    if (
                        target.source_insert_index is not None
                        and target.source_insert_index not in saved_insert_indices
                    ):
                        raise ValueError(
                            "automation mixer target does not reference a saved insert"
                        )
        if len(paths) != len(set(paths)):
            raise ValueError("capsule manifest reuses a member path")


class Capsule:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    @property
    def container_format(self) -> str:
        return _capsule_format(self.path)

    @property
    def manifest(self) -> CapsuleManifest:
        with _open_capsule_archive(self.path) as archive:
            _validate_archive_directory(archive)
            payload = json.loads(_read_limited(archive, "manifest.json", MAX_METADATA_BYTES))
        return CapsuleManifest.from_dict(payload)

    def verify(self) -> None:
        container_format = self.container_format
        container_info = (
            _parse_wave_container(self.path) if container_format == "playable" else None
        )
        if container_info is not None:
            _verify_payload_digest(self.path, container_info)
        with _open_capsule_archive(self.path, container_info) as archive:
            names = _validate_archive_directory(archive)
            corrupt = archive.testzip()
            if corrupt is not None:
                raise ValueError(f"corrupt ZIP member {corrupt}")
            manifest = CapsuleManifest.from_dict(
                json.loads(_read_limited(archive, "manifest.json", MAX_METADATA_BYTES))
            )
            checksums = json.loads(_read_limited(archive, "checksums.json", MAX_METADATA_BYTES))
            if not isinstance(checksums, dict):
                raise ValueError("checksums.json must contain an object")
            required = {"manifest.json"}
            if container_format == "legacy":
                required.add(manifest.preview_path)
            for channel in manifest.channels:
                required.update({channel.state_path, channel.notes_path})
                if channel.sample_asset:
                    required.add(channel.sample_asset)
            for automation in manifest.automations:
                required.add(automation.playlist_path)
                if manifest.schema_version < 5:
                    assert automation.binding_path is not None
                    required.add(automation.binding_path)
                else:
                    required.update(target.state_path for target in automation.targets)
            for insert in manifest.mixer_inserts:
                required.add(insert.state_path)
            missing = required - names
            if missing:
                raise ValueError("capsule is missing required members: " + ", ".join(sorted(missing)))
            expected_checksum_members = names - {"checksums.json"}
            if container_format == "playable":
                expected_checksum_members.add(manifest.preview_path)
            if set(checksums) != expected_checksum_members:
                missing_sums = expected_checksum_members - set(checksums)
                extra_sums = set(checksums) - expected_checksum_members
                details = []
                if missing_sums:
                    details.append("missing " + ", ".join(sorted(missing_sums)))
                if extra_sums:
                    details.append("unknown " + ", ".join(sorted(extra_sums)))
                raise ValueError("invalid checksum coverage: " + "; ".join(details))
            for member, expected in checksums.items():
                if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
                    raise ValueError(f"invalid SHA-256 value for {member}")
                actual = (
                    _sha256_playable_preview(self.path, container_info)
                    if container_info is not None and member == manifest.preview_path
                    else _sha256_archive_member(archive, member)
                )
                if not hmac.compare_digest(actual, expected):
                    raise ValueError(f"checksum mismatch for {member}")

            # Parse every structured member during verification; import then
            # operates only on already-proven event and note streams.
            for channel in manifest.channels:
                FLPFile.from_bytes(_read_limited(archive, channel.state_path, MAX_CHANNEL_STATE_BYTES))
                NoteRecord.parse_many(_read_limited(archive, channel.notes_path, MAX_NOTES_BYTES))
            for automation in manifest.automations:
                if manifest.schema_version < 5:
                    assert automation.binding_path is not None
                    bindings = AutomationBinding.parse_many(
                        _read_limited(
                            archive,
                            automation.binding_path,
                            MAX_AUTOMATION_METADATA_BYTES,
                        )
                    )
                    if len(bindings) != 1:
                        raise ValueError("automation metadata must contain exactly one target binding")
                    known_ids = {channel.source_iid for channel in manifest.channels}
                    if bindings[0].target_channel_iid(known_ids) != automation.target_source_iid:
                        raise ValueError("automation target binding does not match the manifest")
                else:
                    identity_channels = {
                        channel.source_iid: channel.source_iid
                        for channel in manifest.channels
                    }
                    identity_inserts = {
                        insert.source_index: insert.source_index
                        for insert in manifest.mixer_inserts
                    }
                    for target in automation.targets:
                        connection = AutomationConnection.from_bytes(
                            _read_limited(
                                archive,
                                target.state_path,
                                MAX_AUTOMATION_METADATA_BYTES,
                            ),
                            role=target.role,
                            target=target.to_flp_target(),
                        )
                        expected_event_id = target.to_flp_target().target_event_id(
                            channel_mapping=identity_channels,
                            insert_mapping=identity_inserts,
                        )
                        if connection.target_event_id != expected_event_id:
                            raise ValueError(
                                "automation target binding does not match the manifest"
                            )
                        if (
                            connection.remote_link is not None
                            and connection.remote_link.source_automation_iid
                            != automation.source_iid
                        ):
                            raise ValueError(
                                "automation controller link does not match its source clip"
                            )
                playlist = PlaylistItem.parse_many(
                    _read_limited(
                        archive,
                        automation.playlist_path,
                        MAX_AUTOMATION_METADATA_BYTES,
                    )
                )
                if not playlist or any(
                    item.item_index != automation.source_iid for item in playlist
                ):
                    raise ValueError("automation Playlist metadata does not match its channel")
            for insert in manifest.mixer_inserts:
                state = FLPFile.from_bytes(
                    _read_limited(
                        archive,
                        insert.state_path,
                        MAX_MIXER_INSERT_STATE_BYTES,
                    )
                )
                state.validate_mixer_insert_state()
                if manifest.schema_version >= 5:
                    occupied_slots = {
                        slot.index for slot in state.mixer_effect_slots() if slot.occupied
                    }
                    for automation in manifest.automations:
                        for target in automation.targets:
                            if (
                                target.source_insert_index == insert.source_index
                                and target.target_kind in {
                                    "effect_parameter", "effect_slot_control"
                                }
                                and target.slot_index not in occupied_slots
                            ):
                                raise ValueError(
                                    "automation target references an empty effect slot"
                                )
            if container_format == "legacy":
                _validate_wave_member(archive, manifest.preview_path)
            elif container_info is None or container_info.scap_offset > MAX_PREVIEW_BYTES:
                raise ValueError("preview exceeds the safety limit")

    def extract_preview(self, cache_dir: Path) -> Path:
        manifest = self.manifest
        destination = cache_dir / f"{manifest.id}.wav"
        return self.export_preview(destination)

    def preview_checksum(self) -> str:
        manifest = self.manifest
        with _open_capsule_archive(self.path) as archive:
            return str(json.loads(archive.read("checksums.json"))[manifest.preview_path])

    def preview_duration_seconds(self) -> float | None:
        manifest = self.manifest
        try:
            if self.container_format == "playable":
                info = _parse_wave_container(self.path)
                with self.path.open("rb") as source:
                    return _wave_duration_seconds(source, info.scap_offset)
            with _open_capsule_archive(self.path) as archive:
                info = archive.getinfo(manifest.preview_path)
                with archive.open(info) as source:
                    return _wave_duration_seconds(source, info.file_size)
        except (KeyError, OSError, ValueError, struct.error):
            return None

    def export_preview(self, destination: Path) -> Path:
        if (
            not destination.exists()
            or destination.stat().st_size < 12
            or destination.stat().st_mtime < self.path.stat().st_mtime
        ):
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp", delete=False
            ) as target:
                temporary = Path(target.name)
                if self.container_format == "playable":
                    _copy_playable_preview(self.path, target, _parse_wave_container(self.path))
                else:
                    manifest = self.manifest
                    with _open_capsule_archive(self.path) as archive, archive.open(
                        manifest.preview_path
                    ) as source:
                        shutil.copyfileobj(source, target)
            try:
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
        return destination

    def read_channel_state(self, channel: ChannelManifest) -> FLPFile:
        with _open_capsule_archive(self.path) as archive:
            return FLPFile.from_bytes(_read_limited(archive, channel.state_path, MAX_CHANNEL_STATE_BYTES))

    def read_notes(self, channel: ChannelManifest) -> list[NoteRecord]:
        with _open_capsule_archive(self.path) as archive:
            raw = _read_limited(archive, channel.notes_path, MAX_NOTES_BYTES)
        return NoteRecord.parse_many(raw)

    def read_mixer_insert_state(self, insert: MixerInsertManifest) -> FLPFile:
        with _open_capsule_archive(self.path) as archive:
            raw = _read_limited(
                archive, insert.state_path, MAX_MIXER_INSERT_STATE_BYTES
            )
        state = FLPFile.from_bytes(raw)
        state.validate_mixer_insert_state()
        return state

    def read_automation_binding(
        self, automation: AutomationManifest
    ) -> AutomationBinding:
        path = automation.binding_path
        if path is None:
            primary = next(
                (target for target in automation.targets if target.role == "primary"),
                None,
            )
            if primary is None:
                raise ValueError("automation metadata has no primary target")
            with _open_capsule_archive(self.path) as archive:
                connection = AutomationConnection.from_bytes(
                    _read_limited(
                        archive,
                        primary.state_path,
                        MAX_AUTOMATION_METADATA_BYTES,
                    ),
                    role=primary.role,
                    target=primary.to_flp_target(),
                )
            assert connection.binding is not None
            return connection.binding
        with _open_capsule_archive(self.path) as archive:
            raw = _read_limited(
                archive, path, MAX_AUTOMATION_METADATA_BYTES
            )
        records = AutomationBinding.parse_many(raw)
        if len(records) != 1:
            raise ValueError("automation metadata must contain exactly one target binding")
        return records[0]

    @staticmethod
    def read_automation_target(
        automation: AutomationManifest,
    ) -> AutomationTarget:
        if automation.targets:
            primary = next(
                (target for target in automation.targets if target.role == "primary"),
                None,
            )
            if primary is None:
                raise ValueError("automation metadata has no primary target")
            return primary.to_flp_target()
        if automation.target_source_iid is None:
            raise ValueError("legacy automation target is missing")
        # Legacy bindings retain their exact low event word and are remapped by
        # the compatibility path in FLPFile rather than this placeholder.
        return AutomationTarget(
            kind="generator_parameter",
            source_channel_iid=automation.target_source_iid,
            parameter_index=0,
        )

    def read_automation_connections(
        self, automation: AutomationManifest
    ) -> list[AutomationConnection]:
        if not automation.targets:
            return []
        connections: list[AutomationConnection] = []
        with _open_capsule_archive(self.path) as archive:
            for target in automation.targets:
                connections.append(
                    AutomationConnection.from_bytes(
                        _read_limited(
                            archive,
                            target.state_path,
                            MAX_AUTOMATION_METADATA_BYTES,
                        ),
                        role=target.role,
                        target=target.to_flp_target(),
                    )
                )
        return connections

    def read_automation_playlist(
        self, automation: AutomationManifest
    ) -> list[PlaylistItem]:
        with _open_capsule_archive(self.path) as archive:
            raw = _read_limited(
                archive, automation.playlist_path, MAX_AUTOMATION_METADATA_BYTES
            )
        return PlaylistItem.parse_many(raw)

    def extract_sample_asset(self, channel: ChannelManifest, destination_dir: Path) -> Path | None:
        if not channel.sample_asset:
            return None
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / Path(channel.sample_asset).name
        with _open_capsule_archive(self.path) as archive:
            expected = json.loads(archive.read("checksums.json"))[channel.sample_asset]
            if destination.exists() and _sha256_path(destination) == expected:
                return destination
            with archive.open(channel.sample_asset) as source, tempfile.NamedTemporaryFile(
                dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp", delete=False
            ) as target:
                temporary = Path(target.name)
                digest = hashlib.sha256()
                while block := source.read(1024 * 1024):
                    digest.update(block)
                    target.write(block)
            if not hmac.compare_digest(digest.hexdigest(), expected):
                temporary.unlink(missing_ok=True)
                raise ValueError(f"checksum mismatch for {channel.sample_asset}")
            try:
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
        return destination

    def convert_to_playable(self, destination: Path) -> "Capsule":
        self.verify()
        if self.container_format != "legacy":
            raise ValueError("capsule is already in the playable format")
        destination.parent.mkdir(parents=True, exist_ok=True)
        preview: Path | None = None
        payload: Path | None = None
        output: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=destination.parent, prefix=".capsule-preview-", suffix=".wav", delete=False
            ) as handle:
                preview = Path(handle.name)
            self.export_preview(preview)
            payload = _copy_archive_payload(
                self.path, destination.parent, omit={self.manifest.preview_path}
            )
            with tempfile.NamedTemporaryFile(
                dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp", delete=False
            ) as handle:
                output = Path(handle.name)
            _write_playable_container(preview, payload, output)
            Capsule(output).verify()
            os.link(output, destination)
            return Capsule(destination)
        finally:
            for temporary in (preview, payload, output):
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    def replace_manifest(self, manifest: CapsuleManifest) -> None:
        self.verify()
        manifest.validate()
        manifest_bytes = json.dumps(manifest.to_dict(), indent=2, sort_keys=True).encode()
        with _open_capsule_archive(self.path) as source:
            checksums = json.loads(source.read("checksums.json"))
        checksums["manifest.json"] = hashlib.sha256(manifest_bytes).hexdigest()
        checksum_bytes = json.dumps(checksums, indent=2, sort_keys=True).encode()

        payload: Path | None = None
        preview: Path | None = None
        output: Path | None = None
        try:
            payload = _copy_archive_payload(
                self.path,
                self.path.parent,
                replacements={
                    "manifest.json": manifest_bytes,
                    "checksums.json": checksum_bytes,
                },
            )
            with tempfile.NamedTemporaryFile(
                dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp", delete=False
            ) as handle:
                output = Path(handle.name)
            if self.container_format == "playable":
                with tempfile.NamedTemporaryFile(
                    dir=self.path.parent, prefix=".capsule-preview-", suffix=".wav", delete=False
                ) as handle:
                    preview = Path(handle.name)
                self.export_preview(preview)
                _write_playable_container(preview, payload, output)
            else:
                shutil.copyfile(payload, output)
            Capsule(output).verify()
            output.replace(self.path)
        finally:
            for temporary in (payload, preview, output):
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    @classmethod
    def build(
        cls,
        destination: Path,
        *,
        name: str,
        project: FLPFile,
        channel_ids: list[int],
        pattern_id: int,
        pattern_length_steps: int | None = None,
        preview_wav: Path,
        save_mode: str = "group",
        tags: list[str] | None = None,
        embed_sampler_assets: bool = True,
        include_mixer_insert: bool = True,
    ) -> "Capsule":
        sections = project.extract_channels(channel_ids)
        all_notes = project.pattern_notes().get(pattern_id, [])
        notes_by_channel = {iid: [note for note in all_notes if note.rack_channel == iid] for iid in channel_ids}
        channel_manifests: list[ChannelManifest] = []
        automation_manifests: list[AutomationManifest] = []
        mixer_insert_manifests: list[MixerInsertManifest] = []
        files: dict[str, bytes | Path] = {}

        selected_automation_ids = [
            section.iid for section in sections if section.channel_type == 5
        ]
        automation_items = project.playlist_items_for_channels(selected_automation_ids)
        selected_generator_ids = {
            section.iid for section in sections if section.channel_type != 5
        }
        captured_insert_indices = {
            section.mixer_insert
            for section in sections
            if section.channel_type != 5 and section.mixer_insert > 0
        } if include_mixer_insert else set()

        for index, section in enumerate(sections):
            state_path = f"channels/{index:03d}.fst"
            notes_path = f"notes/{index:03d}.bin"
            files[state_path] = project.channel_state(section).to_bytes()
            files[notes_path] = b"".join(note.raw for note in notes_by_channel[section.iid])
            asset_path = None
            sample_path = section.sample_path
            if embed_sampler_assets and sample_path:
                source = Path(sample_path)
                if source.is_file():
                    asset_path = f"assets/{index:03d}-{source.name}"
                    files[asset_path] = source
            channel_manifests.append(
                ChannelManifest(
                    source_iid=section.iid,
                    name=section.name,
                    plugin_name=section.plugin_name,
                    channel_type=section.channel_type,
                    state_path=state_path,
                    notes_path=notes_path,
                    sample_asset=asset_path,
                )
            )
            if section.channel_type == 5:
                allowed_target_event_ids: set[int] = set()
                for _, binding, remote_link in project.automation_connection_records(
                    section.iid
                ):
                    event_id = (
                        binding.target_event_id
                        if binding is not None
                        else remote_link.target_event_id
                        if remote_link is not None
                        else -1
                    )
                    target = project.classify_automation_event_id(event_id)
                    if target is None:
                        continue
                    if (
                        target.kind == "generator_parameter"
                        and target.source_channel_iid in selected_generator_ids
                    ) or (
                        target.kind != "generator_parameter"
                        and target.source_insert_index in captured_insert_indices
                    ):
                        allowed_target_event_ids.add(event_id)
                connections = project.automation_connections(
                    section.iid,
                    allowed_target_event_ids=allowed_target_event_ids,
                )
                if not connections:
                    raise FLPUnsupportedError(
                        f'automation clip "{section.name}" target is not portable '
                        "with the selected generators"
                    )
                playlist = automation_items.get(section.iid, [])
                if not playlist:
                    raise FLPUnsupportedError(
                        f'automation clip "{section.name}" is not placed in the current Playlist arrangement'
                    )
                playlist_path = f"automation/{index:03d}-playlist.bin"
                files[playlist_path] = b"".join(item.raw for item in playlist)
                target_manifests: list[AutomationTargetManifest] = []
                for connection_index, connection in enumerate(connections):
                    state_path = (
                        f"automation/{index:03d}-target-{connection_index:03d}.bin"
                    )
                    files[state_path] = connection.to_bytes()
                    target = connection.target
                    target_manifests.append(
                        AutomationTargetManifest(
                            role=connection.role,
                            target_kind=target.kind,
                            state_path=state_path,
                            source_channel_iid=target.source_channel_iid,
                            source_insert_index=target.source_insert_index,
                            slot_index=target.slot_index,
                            parameter_index=target.parameter_index,
                            control_id=target.control_id,
                        )
                    )
                automation_manifests.append(
                    AutomationManifest(
                        source_iid=section.iid,
                        playlist_path=playlist_path,
                        targets=target_manifests,
                    )
                )

        if include_mixer_insert:
            channels_by_insert: dict[int, list[int]] = {}
            for section in sections:
                if section.channel_type == 5 or section.mixer_insert == 0:
                    continue
                channels_by_insert.setdefault(section.mixer_insert, []).append(
                    section.iid
                )
            for mixer_index, (source_index, source_channels) in enumerate(
                sorted(channels_by_insert.items())
            ):
                state_path = f"mixer/{mixer_index:03d}.fst"
                files[state_path] = project.mixer_insert_state(source_index).to_bytes()
                mixer_insert_manifests.append(
                    MixerInsertManifest(
                        source_index=source_index,
                        channel_source_iids=source_channels,
                        state_path=state_path,
                    )
                )

        if len(channel_manifests) == 1:
            channel_manifests[0].name = name

        manifest = CapsuleManifest.create(
            name=name,
            project=project,
            pattern_id=pattern_id,
            pattern_length_steps=pattern_length_steps,
            save_mode=save_mode,
            channels=channel_manifests,
            automations=automation_manifests,
            mixer_inserts=mixer_insert_manifests,
        )
        manifest.tags = sorted(
            {tag.strip() for tag in (tags or []) if tag.strip()}, key=str.casefold
        )
        files["manifest.json"] = json.dumps(manifest.to_dict(), indent=2, sort_keys=True).encode()
        _validate_wave_file(preview_wav)
        checksums = {
            member: _sha256_path(data) if isinstance(data, Path) else hashlib.sha256(data).hexdigest()
            for member, data in files.items()
        }
        checksums[manifest.preview_path] = _sha256_path(preview_wav)
        files["checksums.json"] = json.dumps(checksums, indent=2, sort_keys=True).encode()

        destination.parent.mkdir(parents=True, exist_ok=True)
        payload: Path | None = None
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
        try:
            with tempfile.NamedTemporaryFile(
                dir=destination.parent, prefix=".capsule-payload-", suffix=".zip", delete=False
            ) as handle:
                payload = Path(handle.name)
            with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                for member, data in files.items():
                    if isinstance(data, Path):
                        archive.write(data, member, compress_type=zipfile.ZIP_DEFLATED)
                    else:
                        archive.writestr(member, data)
            _write_playable_container(preview_wav, payload, temporary)
            cls(temporary).verify()
            os.link(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
            if payload is not None:
                payload.unlink(missing_ok=True)
        return cls(destination)


def unique_capsule_path(library_dir: Path, name: str) -> Path:
    base = slugify(name)[:120]
    candidate = library_dir / f"{base}{CAPSULE_EXTENSION}"
    counter = 2
    while candidate.exists():
        candidate = library_dir / f"{base}-{counter}{CAPSULE_EXTENSION}"
        counter += 1
    return candidate


def unique_legacy_capsule_path(library_dir: Path, name: str) -> Path:
    base = slugify(name)[:120]
    candidate = library_dir / f"{base}{LEGACY_CAPSULE_EXTENSION}"
    counter = 2
    while candidate.exists():
        candidate = library_dir / f"{base}-{counter}{LEGACY_CAPSULE_EXTENSION}"
        counter += 1
    return candidate


def _capsule_format(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            signature = handle.read(12)
    except OSError:
        raise
    if signature.startswith(b"PK\x03\x04"):
        return "legacy"
    if len(signature) == 12 and signature[:4] in {b"RIFF", b"RF64"} and signature[8:] == b"WAVE":
        _parse_wave_container(path)
        return "playable"
    raise ValueError("file is not a Sound Capsule")


@contextmanager
def _open_capsule_archive(
    path: Path, info: WaveContainerInfo | None = None
):
    if info is None and _capsule_format(path) == "legacy":
        with zipfile.ZipFile(path) as archive:
            yield archive
        return
    info = info or _parse_wave_container(path)
    with _FileSlice(
        path, info.payload_offset, info.payload_size, xor_byte=SCAP_XOR_BYTE
    ) as source:
        with zipfile.ZipFile(source) as archive:
            yield archive


def _scan_wave(path: Path) -> tuple[bytes, int, int | None, list[tuple[bytes, int, int, int]]]:
    file_size = path.stat().st_size
    if file_size < 12:
        raise ValueError("WAV file is truncated")
    chunks: list[tuple[bytes, int, int, int]] = []
    ds64_riff_size_offset: int | None = None
    ds64_data_size: int | None = None
    with path.open("rb") as handle:
        header = handle.read(12)
        riff_kind = header[:4]
        if riff_kind not in {b"RIFF", b"RF64"} or header[8:] != b"WAVE":
            raise ValueError("file is not a RIFF/RF64 WAVE")
        declared_size = struct.unpack_from("<I", header, 4)[0]
        if riff_kind == b"RIFF" and declared_size + 8 != file_size:
            raise ValueError("WAV RIFF size does not match the file size")
        if riff_kind == b"RF64" and declared_size != MAX_RIFF_SIZE:
            raise ValueError("RF64 file does not use the required size marker")

        offset = 12
        while offset < file_size:
            if offset + 8 > file_size:
                raise ValueError("WAV has a truncated chunk header")
            handle.seek(offset)
            chunk_header = handle.read(8)
            chunk_id = chunk_header[:4]
            raw_size = struct.unpack_from("<I", chunk_header, 4)[0]
            data_offset = offset + 8
            chunk_size = raw_size
            if raw_size == MAX_RIFF_SIZE:
                if chunk_id != b"data" or ds64_data_size is None:
                    raise ValueError("unsupported oversized RF64 chunk")
                chunk_size = ds64_data_size
            padded_end = data_offset + chunk_size + (chunk_size & 1)
            if padded_end > file_size:
                raise ValueError("WAV chunk exceeds the file size")
            chunks.append((chunk_id, data_offset, chunk_size, padded_end))
            if chunk_id == b"ds64":
                if chunk_size < 28:
                    raise ValueError("RF64 ds64 chunk is truncated")
                handle.seek(data_offset)
                payload = handle.read(28)
                riff_size, ds64_data_size = struct.unpack_from("<QQ", payload, 0)
                ds64_riff_size_offset = data_offset
                if riff_kind != b"RF64" or riff_size + 8 != file_size:
                    raise ValueError("RF64 size does not match the file size")
            offset = padded_end

    if offset != file_size:
        raise ValueError("WAV contains trailing bytes outside RIFF")
    if not any(item[0] == b"fmt " for item in chunks) or not any(
        item[0] == b"data" for item in chunks
    ):
        raise ValueError("WAV is missing fmt or data audio chunks")
    if riff_kind == b"RF64" and ds64_riff_size_offset is None:
        raise ValueError("RF64 file is missing ds64")
    return riff_kind, file_size, ds64_riff_size_offset, chunks


def _parse_wave_container(path: Path) -> WaveContainerInfo:
    riff_kind, file_size, ds64_offset, chunks = _scan_wave(path)
    scap_chunks = [item for item in chunks if item[0] == SCAP_CHUNK_ID]
    if len(scap_chunks) != 1:
        if not scap_chunks:
            raise ValueError("WAV does not contain Sound Capsule data")
        raise ValueError("WAV contains duplicate SCAP chunks")
    _, data_offset, chunk_size, padded_end = scap_chunks[0]
    scap_offset = data_offset - 8
    if padded_end != file_size:
        raise ValueError("SCAP must be the final WAV chunk")
    if chunk_size < SCAP_HEADER_SIZE:
        raise ValueError("SCAP header is truncated")
    with path.open("rb") as handle:
        handle.seek(data_offset)
        header = handle.read(SCAP_HEADER_SIZE)
    if header[:8] != SCAP_MAGIC:
        raise ValueError("unsupported SCAP container version")
    payload_size = struct.unpack_from("<Q", header, 8)[0]
    if payload_size != chunk_size - SCAP_HEADER_SIZE:
        raise ValueError("SCAP payload length does not match the chunk")
    if scap_offset < 12 or scap_offset > MAX_PREVIEW_BYTES:
        raise ValueError("preview exceeds the safety limit")
    return WaveContainerInfo(
        riff_kind=riff_kind,
        file_size=file_size,
        scap_offset=scap_offset,
        scap_size=chunk_size,
        payload_offset=data_offset + SCAP_HEADER_SIZE,
        payload_size=payload_size,
        payload_digest=header[16:48],
        ds64_riff_size_offset=ds64_offset,
    )


def _verify_payload_digest(path: Path, info: WaveContainerInfo) -> None:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        handle.seek(info.payload_offset)
        remaining = info.payload_size
        while remaining:
            block = handle.read(min(1024 * 1024, remaining))
            if not block:
                raise ValueError("SCAP payload is truncated")
            digest.update(block)
            remaining -= len(block)
    if not hmac.compare_digest(digest.digest(), info.payload_digest):
        raise ValueError("SCAP payload checksum mismatch")


def _preview_header_patches(info: WaveContainerInfo) -> dict[int, bytes]:
    if info.riff_kind == b"RIFF":
        return {4: struct.pack("<I", info.scap_offset - 8)}
    if info.ds64_riff_size_offset is None:
        raise ValueError("RF64 file is missing ds64")
    return {info.ds64_riff_size_offset: struct.pack("<Q", info.scap_offset - 8)}


def _sha256_playable_preview(path: Path, info: WaveContainerInfo | None) -> str:
    if info is None:
        raise ValueError("playable capsule metadata is missing")
    digest = hashlib.sha256()
    patches = _preview_header_patches(info)
    with path.open("rb") as handle:
        offset = 0
        while offset < info.scap_offset:
            data = bytearray(handle.read(min(1024 * 1024, info.scap_offset - offset)))
            if not data:
                raise ValueError("playable capsule preview is truncated")
            end = offset + len(data)
            for patch_offset, patch in patches.items():
                patch_end = patch_offset + len(patch)
                if patch_offset < end and patch_end > offset:
                    source_start = max(offset, patch_offset)
                    source_end = min(end, patch_end)
                    data[source_start - offset : source_end - offset] = patch[
                        source_start - patch_offset : source_end - patch_offset
                    ]
            digest.update(data)
            offset = end
    return digest.hexdigest()


def _copy_playable_preview(path: Path, target, info: WaveContainerInfo) -> None:
    with path.open("rb") as source:
        remaining = info.scap_offset
        while remaining:
            block = source.read(min(1024 * 1024, remaining))
            if not block:
                raise ValueError("playable capsule preview is truncated")
            target.write(block)
            remaining -= len(block)
    for offset, patch in _preview_header_patches(info).items():
        target.seek(offset)
        target.write(patch)
    target.seek(0, os.SEEK_END)


def _write_playable_container(preview: Path, payload: Path, destination: Path) -> None:
    _validate_wave_file(preview)
    riff_kind, preview_size, ds64_offset, chunks = _scan_wave(preview)
    if any(item[0] == SCAP_CHUNK_ID for item in chunks):
        raise ValueError("preview already contains Sound Capsule data")
    payload_size = payload.stat().st_size
    scap_size = SCAP_HEADER_SIZE + payload_size
    if scap_size > MAX_RIFF_SIZE:
        raise ValueError("capsule payload is too large for a SCAP chunk")
    final_size = preview_size + 8 + scap_size + (scap_size & 1)
    if riff_kind == b"RIFF" and final_size - 8 > MAX_RIFF_SIZE:
        raise ValueError("capsule is too large for a RIFF WAVE container")
    digest = hashlib.sha256()
    with payload.open("rb") as payload_source:
        while block := payload_source.read(1024 * 1024):
            digest.update(block.translate(SCAP_XOR_TABLE))
    with preview.open("rb") as source, destination.open("r+b") as target:
        target.seek(0)
        shutil.copyfileobj(source, target, length=1024 * 1024)
        if riff_kind == b"RIFF":
            target.seek(4)
            target.write(struct.pack("<I", final_size - 8))
        else:
            if ds64_offset is None:
                raise ValueError("RF64 preview is missing ds64")
            target.seek(ds64_offset)
            target.write(struct.pack("<Q", final_size - 8))
        target.seek(preview_size)
        target.write(SCAP_CHUNK_ID)
        target.write(struct.pack("<I", scap_size))
        target.write(SCAP_MAGIC)
        target.write(struct.pack("<Q", payload_size))
        target.write(digest.digest())
        with payload.open("rb") as payload_source:
            while block := payload_source.read(1024 * 1024):
                target.write(block.translate(SCAP_XOR_TABLE))
        if scap_size & 1:
            target.write(b"\0")
        target.truncate()
        target.flush()
        os.fsync(target.fileno())


def _copy_archive_payload(
    source_path: Path,
    directory: Path,
    *,
    omit: set[str] | None = None,
    replacements: dict[str, bytes] | None = None,
) -> Path:
    omitted = set(omit or ())
    replacement_values = dict(replacements or {})
    with tempfile.NamedTemporaryFile(
        dir=directory, prefix=".capsule-payload-", suffix=".zip", delete=False
    ) as handle:
        temporary = Path(handle.name)
    try:
        with _open_capsule_archive(source_path) as source, zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as target:
            for info in source.infolist():
                if info.filename in omitted or info.filename in replacement_values:
                    continue
                with source.open(info) as input_member, target.open(info, "w") as output_member:
                    shutil.copyfileobj(input_member, output_member, length=1024 * 1024)
            for name, data in replacement_values.items():
                target.writestr(name, data)
        return temporary
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _validate_archive_directory(archive: zipfile.ZipFile) -> set[str]:
    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_MEMBERS:
        raise ValueError(f"capsule has too many ZIP members ({len(infos)})")
    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        raise ValueError("capsule contains duplicate ZIP member names")
    if "manifest.json" not in names or "checksums.json" not in names:
        raise ValueError("capsule is missing manifest.json or checksums.json")
    total = 0
    for info in infos:
        path = Path(info.filename)
        if info.is_dir() or path.is_absolute() or ".." in path.parts or "\\" in info.filename:
            raise ValueError(f"unsafe ZIP member name {info.filename!r}")
        total += info.file_size
        if total > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise ValueError("capsule uncompressed size exceeds the safety limit")
    return set(names)


def _read_limited(archive: zipfile.ZipFile, member: str, limit: int) -> bytes:
    info = archive.getinfo(member)
    if info.file_size > limit:
        raise ValueError(f"{member} exceeds the {limit}-byte metadata limit")
    return archive.read(member)


def _validate_wave_file(path: Path) -> None:
    size = path.stat().st_size
    if size < 12 or size > MAX_PREVIEW_BYTES:
        raise ValueError("preview is not a valid-size RIFF/RF64 WAVE file")
    try:
        _scan_wave(path)
    except ValueError as error:
        raise ValueError("preview is not a valid-size RIFF/RF64 WAVE file") from error


def _validate_wave_member(archive: zipfile.ZipFile, member: str) -> None:
    info = archive.getinfo(member)
    with archive.open(info) as handle:
        header = handle.read(12)
    if (
        info.file_size < 12
        or info.file_size > MAX_PREVIEW_BYTES
        or header[:4] not in {b"RIFF", b"RF64"}
        or header[8:12] != b"WAVE"
    ):
        raise ValueError("preview is not a valid-size RIFF/RF64 WAVE file")


def _wave_duration_seconds(source, file_size: int) -> float | None:
    """Read PCM/IEEE-float WAVE timing without decoding the audio payload.

    Python's wave module rejects IEEE-float WAVE files (format 3), which is the
    format FL Studio renders on macOS. Duration only depends on the RIFF chunk
    sizes, sample rate, and block alignment, so parsing those fields also keeps
    RF64 previews working without loading their audio into memory.
    """
    header = source.read(12)
    if (
        file_size < 12
        or len(header) != 12
        or header[:4] not in {b"RIFF", b"RF64"}
        or header[8:12] != b"WAVE"
    ):
        return None

    offset = 12
    sample_rate: int | None = None
    block_align: int | None = None
    rf64_data_size: int | None = None
    while offset + 8 <= file_size:
        chunk_header = source.read(8)
        if len(chunk_header) != 8:
            return None
        chunk_id = chunk_header[:4]
        chunk_size = struct.unpack_from("<I", chunk_header, 4)[0]
        offset += 8

        if chunk_id == b"ds64":
            if chunk_size < 28 or offset + chunk_size > file_size:
                return None
            payload = source.read(28)
            if len(payload) != 28:
                return None
            rf64_data_size = struct.unpack_from("<Q", payload, 8)[0]
            consumed = 28
        elif chunk_id == b"fmt ":
            if chunk_size < 16 or offset + chunk_size > file_size:
                return None
            payload = source.read(16)
            if len(payload) != 16:
                return None
            _, _, sample_rate, _, block_align, _ = struct.unpack("<HHIIHH", payload)
            consumed = 16
        elif chunk_id == b"data":
            data_size = rf64_data_size if chunk_size == 0xFFFFFFFF else chunk_size
            if (
                data_size is None
                or offset + data_size > file_size
                or not sample_rate
                or not block_align
            ):
                return None
            return data_size / (sample_rate * block_align)
        else:
            if offset + chunk_size > file_size:
                return None
            consumed = 0

        skip = chunk_size - consumed
        if skip:
            source.seek(skip, 1)
        offset += chunk_size
        if chunk_size & 1:
            if offset >= file_size:
                return None
            source.seek(1, 1)
            offset += 1
    return None


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_archive_member(archive: zipfile.ZipFile, member: str) -> str:
    digest = hashlib.sha256()
    with archive.open(member) as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
