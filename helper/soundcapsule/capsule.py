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

from .flp import FLPFile, NoteRecord


CAPSULE_SCHEMA_VERSION = 2
MAX_ARCHIVE_MEMBERS = 4096
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 8 * 1024 * 1024 * 1024
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_PREVIEW_BYTES = 512 * 1024 * 1024
MAX_CHANNEL_STATE_BYTES = 512 * 1024 * 1024
MAX_NOTES_BYTES = 256 * 1024 * 1024
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
            source_pattern_length_steps=pattern_length_steps,
            source_tempo_bpm=project.tempo_bpm,
        )

    def to_dict(self) -> dict:
        return asdict(self)

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
        channels = [ChannelManifest(**item) for item in channels_payload]
        manifest = cls(channels=channels, **values)
        manifest.validate()
        return manifest

    def validate(self) -> None:
        if self.schema_version not in {1, CAPSULE_SCHEMA_VERSION}:
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
        paths = [self.preview_path]
        for channel in self.channels:
            paths.extend([channel.state_path, channel.notes_path])
            if channel.sample_asset:
                paths.append(channel.sample_asset)
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
    ) -> "Capsule":
        sections = project.extract_channels(channel_ids)
        all_notes = project.pattern_notes().get(pattern_id, [])
        notes_by_channel = {iid: [note for note in all_notes if note.rack_channel == iid] for iid in channel_ids}
        channel_manifests: list[ChannelManifest] = []
        files: dict[str, bytes | Path] = {}

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

        if len(channel_manifests) == 1:
            channel_manifests[0].name = name

        manifest = CapsuleManifest.create(
            name=name,
            project=project,
            pattern_id=pattern_id,
            pattern_length_steps=pattern_length_steps,
            save_mode=save_mode,
            channels=channel_manifests,
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
