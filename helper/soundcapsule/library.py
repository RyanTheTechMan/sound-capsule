from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading

from .capsule import Capsule, unique_capsule_path

INDEX_VERSION = 9

SCHEMA = """
CREATE TABLE IF NOT EXISTS capsules (
    id TEXT PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    source_fl_version TEXT NOT NULL DEFAULT '',
    plugin_names TEXT NOT NULL,
    tags TEXT NOT NULL,
    favorite INTEGER NOT NULL DEFAULT 0,
    channel_count INTEGER NOT NULL,
    channel_names TEXT NOT NULL DEFAULT '[]',
    note_preview TEXT NOT NULL DEFAULT '[]',
    midi_playback_end REAL NOT NULL DEFAULT 1.0,
    use_count INTEGER NOT NULL DEFAULT 0,
    modified_ns INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS capsules_name ON capsules(name COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class CapsuleLibrary:
    def __init__(self, library_dir: Path, database_path: Path):
        self._lock = threading.RLock()
        self.library_dir = library_dir
        self.database_path = database_path
        self.library_dir.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self.session() as database:
            database.executescript(SCHEMA)
            columns = {row[1] for row in database.execute("PRAGMA table_info(capsules)")}
            if "note_preview" not in columns:
                database.execute("ALTER TABLE capsules ADD COLUMN note_preview TEXT NOT NULL DEFAULT '[]'")
                database.execute("UPDATE capsules SET modified_ns = -1")
            if "channel_names" not in columns:
                database.execute("ALTER TABLE capsules ADD COLUMN channel_names TEXT NOT NULL DEFAULT '[]'")
                database.execute("UPDATE capsules SET modified_ns = -1")
            if "midi_playback_end" not in columns:
                database.execute(
                    "ALTER TABLE capsules ADD COLUMN midi_playback_end REAL NOT NULL DEFAULT 1.0"
                )
                database.execute("UPDATE capsules SET modified_ns = -1")
            if "use_count" not in columns:
                database.execute("ALTER TABLE capsules ADD COLUMN use_count INTEGER NOT NULL DEFAULT 0")
            if "source_fl_version" not in columns:
                database.execute(
                    "ALTER TABLE capsules ADD COLUMN source_fl_version TEXT NOT NULL DEFAULT ''"
                )
                database.execute("UPDATE capsules SET modified_ns = -1")
            indexed_version = database.execute(
                "SELECT value FROM metadata WHERE key = 'index_version'"
            ).fetchone()
            if indexed_version is None or int(indexed_version[0]) != INDEX_VERSION:
                database.execute("UPDATE capsules SET modified_ns = -1")
                database.execute(
                    "INSERT INTO metadata(key, value) VALUES('index_version', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(INDEX_VERSION),),
                )

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def session(self):
        database = self.connect()
        try:
            with database:
                yield database
        finally:
            database.close()

    def reindex(self) -> int:
        with self._lock:
            seen: set[str] = set()
            count = 0
            with self.session() as database:
                existing = {
                    row["path"]: row["modified_ns"]
                    for row in database.execute("SELECT path, modified_ns FROM capsules").fetchall()
                }
                for path in self.library_dir.rglob("*.flcapsule"):
                    resolved = str(path.resolve())
                    try:
                        modified_ns = path.stat().st_mtime_ns
                        if existing.get(resolved) == modified_ns:
                            self._remove_legacy_preview(Capsule(path))
                            seen.add(resolved)
                            count += 1
                            continue
                        capsule = Capsule(path)
                        capsule.verify()
                        manifest = capsule.manifest
                        self._remove_legacy_preview(capsule)
                    except Exception:
                        continue
                    seen.add(resolved)
                    plugin_names = []
                    for channel in manifest.channels:
                        state_sections = capsule.read_channel_state(channel).channel_sections()
                        plugin_names.append(
                            state_sections[0].plugin_name if state_sections else channel.plugin_name
                        )
                    note_preview, midi_playback_end = self._note_preview(capsule, manifest)
                    database.execute(
                        """INSERT INTO capsules
                        (id, path, name, created_at, source_fl_version, plugin_names,
                         tags, favorite, channel_count, channel_names, note_preview,
                         midi_playback_end, modified_ns)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET path=excluded.path, name=excluded.name,
                        created_at=excluded.created_at,
                        source_fl_version=excluded.source_fl_version,
                        plugin_names=excluded.plugin_names, tags=excluded.tags,
                        favorite=excluded.favorite,
                        channel_count=excluded.channel_count,
                        channel_names=excluded.channel_names,
                        note_preview=excluded.note_preview,
                        midi_playback_end=excluded.midi_playback_end,
                        modified_ns=excluded.modified_ns""",
                        (
                            manifest.id, resolved, manifest.name, manifest.created_at,
                            manifest.source_fl_version,
                            json.dumps(plugin_names),
                            json.dumps(manifest.tags), int(manifest.favorite), len(manifest.channels),
                            json.dumps([channel.name for channel in manifest.channels]),
                            json.dumps(note_preview),
                            midi_playback_end,
                            modified_ns,
                        ),
                    )
                    count += 1
                for indexed_path in existing:
                    if indexed_path not in seen:
                        database.execute("DELETE FROM capsules WHERE path = ?", (indexed_path,))
            return count

    def list(
        self,
        search: str = "",
        *,
        favorites_only: bool = False,
        sort_by: str = "recent",
        descending: bool = True,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[dict]:
        limit = max(1, min(int(limit), 1000))
        offset = max(0, int(offset))
        sort_columns = {
            "recent": "created_at",
            "name": "name COLLATE NOCASE",
            "uses": "use_count",
        }
        if sort_by not in sort_columns:
            raise ValueError("sort_by must be 'recent', 'name', or 'uses'")
        query = "SELECT * FROM capsules"
        conditions: list[str] = []
        args: tuple = ()
        for term in (item.strip() for item in search.split(",")):
            if not term:
                continue
            conditions.append("(name LIKE ? OR plugin_names LIKE ? OR tags LIKE ?)")
            wildcard = f"%{term}%"
            args = (*args, wildcard, wildcard, wildcard)
        if favorites_only:
            conditions.append("favorite = 1")
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        direction = "DESC" if descending else "ASC"
        query += f" ORDER BY {sort_columns[sort_by]} {direction}, name COLLATE NOCASE ASC LIMIT ? OFFSET ?"
        args = (*args, limit, offset)
        with self._lock, self.session() as database:
            rows = [dict(row) for row in database.execute(query, args).fetchall()]
        for row in rows:
            row["preview_path"] = row["path"]
        return rows

    @staticmethod
    def _remove_legacy_preview(capsule: Capsule) -> None:
        sidecar = capsule.path.with_suffix(".wav")
        if not sidecar.is_file():
            return
        try:
            digest = hashlib.sha256()
            with sidecar.open("rb") as handle:
                while block := handle.read(1024 * 1024):
                    digest.update(block)
            # Delete only the exact WAV previously exported from this capsule.
            # A user-owned WAV with the same stem is never removed.
            if digest.hexdigest() == capsule.preview_checksum():
                sidecar.unlink()
        except (OSError, KeyError, ValueError):
            return

    @staticmethod
    def _note_preview(
        capsule: Capsule,
        manifest,
    ) -> tuple[list[list[float | int]], float]:
        indexed_notes = [
            (channel_index, note)
            for channel_index, channel in enumerate(manifest.channels)
            for note in capsule.read_notes(channel)
        ]
        if not indexed_notes:
            return [], 1.0
        indexed_notes.sort(
            key=lambda item: (item[1].position, item[1].key, item[1].length, item[0])
        )
        if len(indexed_notes) > 2048:
            stride = math.ceil(len(indexed_notes) / 2048)
            indexed_notes = indexed_notes[::stride]
        notes = [note for _, note in indexed_notes]
        note_end = max(note.position + max(1, note.length) for note in notes)
        exact_timing = manifest.schema_version >= 2 and manifest.source_tempo_bpm is not None
        if exact_timing:
            end = max(1, note_end)
            preview_duration = capsule.preview_duration_seconds()
            midi_duration = note_end * 60.0 / (
                manifest.source_ppq * manifest.source_tempo_bpm
            )
            playback_end = (
                max(0.000001, min(1.0, midi_duration / preview_duration))
                if preview_duration and preview_duration > 0.0
                else 1.0
            )
        else:
            # FL 25.2's getPatternLength value uses four Channel Rack steps per
            # quarter-note beat, while FLP note positions use PPQ ticks.
            pattern_end = round(
                manifest.source_pattern_length_steps * manifest.source_ppq / 4
            ) if manifest.source_pattern_length_steps else 0
            end = max(1, note_end, pattern_end)
            playback_end = note_end / end
        low = min(note.key for note in notes)
        high = max(note.key for note in notes)
        pitch_span = max(1, high - low)
        preview = [
            [
                round(note.position / end, 6),
                round(max(1, note.length) / end, 6),
                round((note.key - low) / pitch_span, 6),
                channel_index,
            ]
            for channel_index, note in indexed_notes
        ]
        return preview, playback_end

    def find(self, capsule_id: str) -> Capsule:
        with self._lock, self.session() as database:
            row = database.execute("SELECT path FROM capsules WHERE id = ?", (capsule_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown capsule {capsule_id}")
        return Capsule(row["path"])

    def add_capsules(self, paths: list[Path]) -> dict[str, list[dict[str, str]]]:
        result: dict[str, list[dict[str, str]]] = {
            "imported": [],
            "skipped": [],
            "failed": [],
        }
        with self._lock:
            self.reindex()
            with self.session() as database:
                known_ids = {
                    str(row["id"])
                    for row in database.execute("SELECT id FROM capsules").fetchall()
                }

            for raw_path in paths:
                source = Path(raw_path)
                temporary: Path | None = None
                try:
                    source = source.expanduser().resolve()
                    if source.suffix.casefold() != ".flcapsule":
                        raise ValueError("file does not have the .flcapsule extension")
                    if not source.is_file():
                        raise FileNotFoundError("capsule file was not found")

                    capsule = Capsule(source)
                    capsule.verify()
                    manifest = capsule.manifest
                    if manifest.id in known_ids:
                        result["skipped"].append(
                            {
                                "source": str(source),
                                "id": manifest.id,
                                "name": manifest.name,
                                "reason": "capsule is already in the library",
                            }
                        )
                        continue

                    with tempfile.NamedTemporaryFile(
                        dir=self.library_dir,
                        prefix=".capsule-import-",
                        suffix=".tmp",
                        delete=False,
                    ) as target, source.open("rb") as input_file:
                        temporary = Path(target.name)
                        shutil.copyfileobj(input_file, target, length=1024 * 1024)
                        target.flush()
                        os.fsync(target.fileno())

                    # Verify the private copy too; only these exact bytes can be
                    # installed into the library.
                    copied_capsule = Capsule(temporary)
                    copied_capsule.verify()
                    manifest = copied_capsule.manifest
                    if manifest.id in known_ids:
                        result["skipped"].append(
                            {
                                "source": str(source),
                                "id": manifest.id,
                                "name": manifest.name,
                                "reason": "capsule is already in the library",
                            }
                        )
                        continue
                    while True:
                        destination = unique_capsule_path(self.library_dir, manifest.name)
                        try:
                            os.link(temporary, destination)
                            break
                        except FileExistsError:
                            continue

                    known_ids.add(manifest.id)
                    result["imported"].append(
                        {
                            "source": str(source),
                            "path": str(destination.resolve()),
                            "id": manifest.id,
                            "name": manifest.name,
                        }
                    )
                except Exception as error:
                    result["failed"].append(
                        {"source": str(source), "error": str(error)}
                    )
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)

            if result["imported"]:
                self.reindex()
        return result

    def set_favorite(self, capsule_id: str, favorite: bool) -> None:
        with self._lock:
            capsule = self.find(capsule_id)
            self._rewrite_manifest(capsule, favorite=favorite)
            self.reindex()

    def record_use(self, capsule_id: str) -> None:
        with self._lock, self.session() as database:
            cursor = database.execute(
                "UPDATE capsules SET use_count = use_count + 1 WHERE id = ?",
                (capsule_id,),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown capsule {capsule_id}")

    def rename(
        self, capsule_id: str, name: str, channel_names: list[str] | None = None
    ) -> None:
        with self._lock:
            capsule = self.find(capsule_id)
            self._rewrite_manifest(capsule, name=name, channel_names=channel_names)
            self.reindex()

    def set_tags(self, capsule_id: str, tags: list[str]) -> None:
        with self._lock:
            capsule = self.find(capsule_id)
            normalized = sorted({tag.strip() for tag in tags if tag.strip()}, key=str.casefold)
            self._rewrite_manifest(capsule, tags=normalized)
            self.reindex()

    def delete(self, capsule_id: str) -> None:
        with self._lock:
            capsule = self.find(capsule_id)
            capsule.path.with_suffix(".wav").unlink(missing_ok=True)
            capsule.path.unlink()
            self.reindex()

    def _rewrite_manifest(self, capsule: Capsule, **changes) -> None:
        import tempfile
        import zipfile
        import hashlib

        capsule.verify()
        with zipfile.ZipFile(capsule.path) as source:
            manifest = capsule.manifest
            checksums = json.loads(source.read("checksums.json"))
        channel_names = changes.pop("channel_names", None)
        if channel_names is not None:
            normalized = [str(name).strip() for name in channel_names]
            if len(normalized) != len(manifest.channels) or any(not name for name in normalized):
                raise ValueError("channel names must match every capsule channel")
            for channel, name in zip(manifest.channels, normalized, strict=True):
                channel.name = name
        for key, value in changes.items():
            setattr(manifest, key, value)
        manifest.validate()
        manifest_bytes = json.dumps(manifest.to_dict(), indent=2, sort_keys=True).encode()
        checksums["manifest.json"] = hashlib.sha256(manifest_bytes).hexdigest()
        checksum_bytes = json.dumps(checksums, indent=2, sort_keys=True).encode()
        with tempfile.NamedTemporaryFile(
            dir=capsule.path.parent, prefix=f".{capsule.path.name}.", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
        try:
            with zipfile.ZipFile(capsule.path) as source, zipfile.ZipFile(
                temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
            ) as target:
                for info in source.infolist():
                    if info.filename in {"manifest.json", "checksums.json"}:
                        continue
                    with source.open(info) as input_member, target.open(info.filename, "w") as output_member:
                        import shutil
                        shutil.copyfileobj(input_member, output_member, length=1024 * 1024)
                target.writestr("manifest.json", manifest_bytes)
                target.writestr("checksums.json", checksum_bytes)
            Capsule(temporary).verify()
            temporary.replace(capsule.path)
        finally:
            temporary.unlink(missing_ok=True)
