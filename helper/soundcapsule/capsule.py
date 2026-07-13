from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid
import wave
import zipfile

from .flp import FLPFile, NoteRecord


CAPSULE_SCHEMA_VERSION = 2
MAX_ARCHIVE_MEMBERS = 4096
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 8 * 1024 * 1024 * 1024
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_PREVIEW_BYTES = 512 * 1024 * 1024
MAX_CHANNEL_STATE_BYTES = 512 * 1024 * 1024
MAX_NOTES_BYTES = 256 * 1024 * 1024


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
    def manifest(self) -> CapsuleManifest:
        with zipfile.ZipFile(self.path) as archive:
            _validate_archive_directory(archive)
            payload = json.loads(_read_limited(archive, "manifest.json", MAX_METADATA_BYTES))
        return CapsuleManifest.from_dict(payload)

    def verify(self) -> None:
        with zipfile.ZipFile(self.path) as archive:
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
            required = {"manifest.json", manifest.preview_path}
            for channel in manifest.channels:
                required.update({channel.state_path, channel.notes_path})
                if channel.sample_asset:
                    required.add(channel.sample_asset)
            missing = required - names
            if missing:
                raise ValueError("capsule is missing required members: " + ", ".join(sorted(missing)))
            expected_checksum_members = names - {"checksums.json"}
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
                actual = _sha256_archive_member(archive, member)
                if not hmac.compare_digest(actual, expected):
                    raise ValueError(f"checksum mismatch for {member}")

            # Parse every structured member during verification; import then
            # operates only on already-proven event and note streams.
            for channel in manifest.channels:
                FLPFile.from_bytes(_read_limited(archive, channel.state_path, MAX_CHANNEL_STATE_BYTES))
                NoteRecord.parse_many(_read_limited(archive, channel.notes_path, MAX_NOTES_BYTES))
            _validate_wave_member(archive, manifest.preview_path)

    def extract_preview(self, cache_dir: Path) -> Path:
        manifest = self.manifest
        destination = cache_dir / f"{manifest.id}.wav"
        return self.export_preview(destination)

    def preview_checksum(self) -> str:
        manifest = self.manifest
        with zipfile.ZipFile(self.path) as archive:
            return str(json.loads(archive.read("checksums.json"))[manifest.preview_path])

    def preview_duration_seconds(self) -> float | None:
        manifest = self.manifest
        try:
            with zipfile.ZipFile(self.path) as archive, archive.open(manifest.preview_path) as source:
                with wave.open(source, "rb") as reader:
                    rate = reader.getframerate()
                    return reader.getnframes() / rate if rate > 0 else None
        except (KeyError, OSError, ValueError, wave.Error):
            return None

    def export_preview(self, destination: Path) -> Path:
        manifest = self.manifest
        if not destination.exists() or destination.stat().st_mtime < self.path.stat().st_mtime:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(self.path) as archive, archive.open(manifest.preview_path) as source:
                with tempfile.NamedTemporaryFile(
                    dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp", delete=False
                ) as target:
                    temporary = Path(target.name)
                    shutil.copyfileobj(source, target)
                try:
                    temporary.replace(destination)
                finally:
                    temporary.unlink(missing_ok=True)
        return destination

    def read_channel_state(self, channel: ChannelManifest) -> FLPFile:
        with zipfile.ZipFile(self.path) as archive:
            return FLPFile.from_bytes(_read_limited(archive, channel.state_path, MAX_CHANNEL_STATE_BYTES))

    def read_notes(self, channel: ChannelManifest) -> list[NoteRecord]:
        with zipfile.ZipFile(self.path) as archive:
            raw = _read_limited(archive, channel.notes_path, MAX_NOTES_BYTES)
        return NoteRecord.parse_many(raw)

    def extract_sample_asset(self, channel: ChannelManifest, destination_dir: Path) -> Path | None:
        if not channel.sample_asset:
            return None
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / Path(channel.sample_asset).name
        with zipfile.ZipFile(self.path) as archive:
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
        files[manifest.preview_path] = preview_wav
        checksums = {
            member: _sha256_path(data) if isinstance(data, Path) else hashlib.sha256(data).hexdigest()
            for member, data in files.items()
        }
        files["checksums.json"] = json.dumps(checksums, indent=2, sort_keys=True).encode()

        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
        try:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                for member, data in files.items():
                    if isinstance(data, Path):
                        # PCM WAV data gains little from DEFLATE but costs CPU
                        # every time a preview is preloaded. Keep the preview
                        # uncompressed inside the capsule for faster playback.
                        archive.write(
                            data,
                            member,
                            compress_type=(
                                zipfile.ZIP_STORED
                                if member == manifest.preview_path
                                else zipfile.ZIP_DEFLATED
                            ),
                        )
                    else:
                        archive.writestr(member, data)
            cls(temporary).verify()
            os.link(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return cls(destination)


def unique_capsule_path(library_dir: Path, name: str) -> Path:
    base = slugify(name)[:120]
    candidate = library_dir / f"{base}.flcapsule"
    counter = 2
    while candidate.exists():
        candidate = library_dir / f"{base}-{counter}.flcapsule"
        counter += 1
    return candidate


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
    with path.open("rb") as handle:
        header = handle.read(12)
    if size < 12 or size > MAX_PREVIEW_BYTES or header[:4] not in {b"RIFF", b"RF64"} or header[8:12] != b"WAVE":
        raise ValueError("preview is not a valid-size RIFF/RF64 WAVE file")


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
