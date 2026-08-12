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

from send2trash import send2trash

from .capsule import (
    CAPSULE_EXTENSION,
    Capsule,
    is_capsule_filename,
    unique_capsule_path,
    unique_legacy_capsule_path,
)

INDEX_VERSION = 15

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
    effect_names TEXT NOT NULL DEFAULT '[]',
    note_preview TEXT NOT NULL DEFAULT '[]',
    automation_preview TEXT NOT NULL DEFAULT '[]',
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
        self.last_migration_summary: dict[str, list[dict[str, str]]] = {
            "converted": [],
            "failed": [],
        }
        self.last_health_summary: list[dict[str, str]] = []
        self.library_dir.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self.session() as database:
            database.executescript(SCHEMA)
            columns = {row[1] for row in database.execute("PRAGMA table_info(capsules)")}
            if "note_preview" not in columns:
                database.execute("ALTER TABLE capsules ADD COLUMN note_preview TEXT NOT NULL DEFAULT '[]'")
                database.execute("UPDATE capsules SET modified_ns = -1")
            if "automation_preview" not in columns:
                database.execute(
                    "ALTER TABLE capsules ADD COLUMN automation_preview TEXT NOT NULL DEFAULT '[]'"
                )
                database.execute("UPDATE capsules SET modified_ns = -1")
            if "channel_names" not in columns:
                database.execute("ALTER TABLE capsules ADD COLUMN channel_names TEXT NOT NULL DEFAULT '[]'")
                database.execute("UPDATE capsules SET modified_ns = -1")
            if "effect_names" not in columns:
                database.execute("ALTER TABLE capsules ADD COLUMN effect_names TEXT NOT NULL DEFAULT '[]'")
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
            self.last_migration_summary = self._migrate_legacy_capsules()
            self.last_health_summary = []
            migration_failures = {
                failure["source"] for failure in self.last_migration_summary["failed"]
            }
            seen: set[str] = set()
            count = 0
            with self.session() as database:
                existing = {
                    row["path"]: row["modified_ns"]
                    for row in database.execute("SELECT path, modified_ns FROM capsules").fetchall()
                }
                for path in self._capsule_paths():
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
                    except Exception as error:
                        if str(path) not in migration_failures:
                            self.last_health_summary.append(
                                {"source": str(path), "error": str(error)}
                            )
                        continue
                    seen.add(resolved)
                    plugin_names = []
                    for channel in manifest.channels:
                        state_sections = capsule.read_channel_state(channel).channel_sections()
                        plugin_names.append(
                            state_sections[0].plugin_name if state_sections else channel.plugin_name
                        )
                    note_preview, automation_preview, midi_playback_end = self._preview_data(
                        capsule, manifest
                    )
                    effect_names = [
                        slot.plugin_name
                        for insert in manifest.mixer_inserts
                        for slot in capsule.read_mixer_insert_state(insert).mixer_effect_slots()
                        if slot.occupied
                    ]
                    database.execute(
                        """INSERT INTO capsules
                        (id, path, name, created_at, source_fl_version, plugin_names,
                         tags, favorite, channel_count, channel_names, effect_names,
                         note_preview, automation_preview, midi_playback_end, modified_ns)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET path=excluded.path, name=excluded.name,
                        created_at=excluded.created_at,
                        source_fl_version=excluded.source_fl_version,
                        plugin_names=excluded.plugin_names, tags=excluded.tags,
                        favorite=excluded.favorite,
                        channel_count=excluded.channel_count,
                        channel_names=excluded.channel_names,
                        effect_names=excluded.effect_names,
                        note_preview=excluded.note_preview,
                        automation_preview=excluded.automation_preview,
                        midi_playback_end=excluded.midi_playback_end,
                        modified_ns=excluded.modified_ns""",
                        (
                            manifest.id, resolved, manifest.name, manifest.created_at,
                            manifest.source_fl_version,
                            json.dumps(plugin_names),
                            json.dumps(manifest.tags), int(manifest.favorite), len(manifest.channels),
                            json.dumps([channel.name for channel in manifest.channels]),
                            json.dumps(effect_names),
                            json.dumps(note_preview),
                            json.dumps(automation_preview),
                            midi_playback_end,
                            modified_ns,
                        ),
                    )
                    count += 1
                for indexed_path in existing:
                    if indexed_path not in seen:
                        database.execute("DELETE FROM capsules WHERE path = ?", (indexed_path,))
            return count

    def _capsule_paths(self) -> list[Path]:
        paths = {
            path
            for pattern in (f"*{CAPSULE_EXTENSION}", "*.flcapsule")
            for path in self.library_dir.rglob(pattern)
            if path.is_file()
        }
        return sorted(paths, key=lambda path: str(path).casefold())

    def _migrate_legacy_capsules(self) -> dict[str, list[dict[str, str]]]:
        summary: dict[str, list[dict[str, str]]] = {"converted": [], "failed": []}
        for source in sorted(self.library_dir.rglob("*.flcapsule")):
            destination: Path | None = None
            installed = False
            try:
                capsule = Capsule(source)
                capsule.verify()
                if capsule.container_format != "legacy":
                    continue
                source_stat = source.stat()
                while True:
                    destination = unique_capsule_path(source.parent, source.stem)
                    try:
                        converted = capsule.convert_to_playable(destination)
                        installed = True
                        break
                    except FileExistsError:
                        continue
                os.utime(
                    destination,
                    ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns),
                )
                source.unlink()
                summary["converted"].append(
                    {
                        "source": str(source),
                        "path": str(destination),
                        "id": converted.manifest.id,
                        "name": converted.manifest.name,
                    }
                )
            except Exception as error:
                if installed and source.exists() and destination is not None:
                    try:
                        destination.unlink()
                    except OSError:
                        pass
                summary["failed"].append(
                    {"source": str(source), "error": str(error)}
                )
        return summary

    def list(
        self,
        search: str = "",
        *,
        favorites_only: bool = False,
        sort_by: str = "recent",
        descending: bool = True,
        limit: int = 1000,
        offset: int = 0,
        include_previews: bool = True,
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
        columns = "*" if include_previews else (
            "id, path, name, created_at, source_fl_version, plugin_names, tags, "
            "favorite, channel_count, channel_names, effect_names, midi_playback_end, "
            "use_count, modified_ns"
        )
        query = f"SELECT {columns} FROM capsules"
        conditions, args = self._list_conditions(search, favorites_only)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        direction = "DESC" if descending else "ASC"
        query += (
            f" ORDER BY {sort_columns[sort_by]} {direction}, "
            "name COLLATE NOCASE ASC, id ASC LIMIT ? OFFSET ?"
        )
        args = (*args, limit, offset)
        with self._lock, self.session() as database:
            rows = [dict(row) for row in database.execute(query, args).fetchall()]
        for row in rows:
            row["preview_path"] = row["path"]
        return rows

    @staticmethod
    def _list_conditions(search: str, favorites_only: bool) -> tuple[list[str], tuple]:
        conditions: list[str] = []
        args: tuple = ()
        for term in (item.strip() for item in search.split(",")):
            if not term:
                continue
            conditions.append(
                "(name LIKE ? OR plugin_names LIKE ? OR effect_names LIKE ? OR tags LIKE ?)"
            )
            wildcard = f"%{term}%"
            args = (*args, wildcard, wildcard, wildcard, wildcard)
        if favorites_only:
            conditions.append("favorite = 1")
        return conditions, args

    def count(self, search: str = "", *, favorites_only: bool = False) -> int:
        conditions, args = self._list_conditions(search, favorites_only)
        query = "SELECT COUNT(*) FROM capsules"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        with self._lock, self.session() as database:
            return int(database.execute(query, args).fetchone()[0])

    def preview_details(self, capsule_ids: list[str]) -> list[dict]:
        """Return the large MIDI/automation payload only for requested rows."""
        if not capsule_ids:
            return []
        if len(capsule_ids) > 2:
            raise ValueError("preview_details accepts at most 2 capsule ids")
        normalized = [str(capsule_id)[:200] for capsule_id in capsule_ids]
        placeholders = ",".join("?" for _ in normalized)
        with self._lock, self.session() as database:
            rows = database.execute(
                f"SELECT id, note_preview, automation_preview, midi_playback_end "
                f"FROM capsules WHERE id IN ({placeholders})",
                tuple(normalized),
            ).fetchall()
        indexed = {row["id"]: dict(row) for row in rows}
        return [indexed[capsule_id] for capsule_id in normalized if capsule_id in indexed]

    @staticmethod
    def _remove_legacy_preview(capsule: Capsule) -> None:
        if capsule.container_format != "legacy":
            return
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
    def _preview_data(
        capsule: Capsule,
        manifest,
    ) -> tuple[list[list[float | int]], list[list], float]:
        source_notes = [
            (channel_index, note)
            for channel_index, channel in enumerate(manifest.channels)
            for note in capsule.read_notes(channel)
        ]
        source_notes.sort(
            key=lambda item: (item[1].position, item[1].key, item[1].length, item[0])
        )
        indexed_notes: list[tuple[int, float, float, int]] = []
        if manifest.playlist_phrase is not None:
            for item in capsule.read_playlist_phrase():
                content_start, content_end = item.pattern_content_window()
                content_span = content_end - content_start
                timeline_scale = item.length / content_span
                for channel_index, note in source_notes:
                    note_start = float(note.position)
                    note_end = note_start + max(1, note.length)
                    visible_start = max(note_start, content_start)
                    visible_end = min(note_end, content_end)
                    if visible_end <= visible_start:
                        continue
                    indexed_notes.append(
                        (
                            channel_index,
                            item.position
                            + (visible_start - content_start) * timeline_scale,
                            (visible_end - visible_start) * timeline_scale,
                            note.key,
                        )
                    )
        else:
            indexed_notes = [
                (channel_index, float(note.position), float(max(1, note.length)), note.key)
                for channel_index, note in source_notes
            ]
        indexed_notes.sort(key=lambda item: (item[1], item[3], item[2], item[0]))
        if len(indexed_notes) > 2048:
            stride = math.ceil(len(indexed_notes) / 2048)
            indexed_notes = indexed_notes[::stride]
        note_end = max(
            (position + length for _, position, length, _ in indexed_notes), default=0
        )
        raw_automation: list[tuple[int, list[tuple[float, float, float]]]] = []
        automation_end = 0.0
        if manifest.automations:
            all_items = [
                item
                for automation in manifest.automations
                for item in capsule.read_automation_playlist(automation)
            ]
            anchor = (
                0
                if manifest.playlist_phrase is not None
                else min((item.position for item in all_items), default=0)
            )
            channel_indexes = {
                channel.source_iid: index
                for index, channel in enumerate(manifest.channels)
            }
            channels = {
                channel.source_iid: channel for channel in manifest.channels
            }
            for automation in manifest.automations:
                state = capsule.read_channel_state(channels[automation.source_iid])
                sections = state.channel_sections()
                points = sections[0].automation_points() if sections else []
                if not points:
                    continue
                source_end = max(points[-1].position * manifest.source_ppq, 1.0)
                if len(points) > 256:
                    stride = math.ceil((len(points) - 1) / 255)
                    sampled = points[::stride]
                    if sampled[-1] is not points[-1]:
                        sampled.append(points[-1])
                    points = sampled
                for item in capsule.read_automation_playlist(automation):
                    item_start = float(item.position - anchor)
                    scale = item.length / source_end
                    curve = [
                        (
                            item_start + point.position * manifest.source_ppq * scale,
                            max(0.0, min(1.0, point.value)),
                            point.tension,
                        )
                        for point in points
                    ]
                    raw_automation.append(
                        (channel_indexes[automation.source_iid], curve)
                    )
                    automation_end = max(automation_end, item_start + item.length)
        # Preview geometry is display-only. Bound it globally so a single
        # automation-heavy capsule can never recreate an unbounded RPC payload.
        if len(raw_automation) > 512:
            stride = math.ceil(len(raw_automation) / 512)
            raw_automation = raw_automation[::stride][:512]
        total_automation_points = sum(len(curve) for _, curve in raw_automation)
        if total_automation_points > 4096:
            stride = math.ceil(total_automation_points / 4096)
            sampled_automation: list[tuple[int, list[tuple[float, float, float]]]] = []
            for channel_index, curve in raw_automation:
                sampled = curve[::stride]
                if curve and sampled[-1] != curve[-1]:
                    sampled.append(curve[-1])
                sampled_automation.append((channel_index, sampled))
            raw_automation = sampled_automation
        if not indexed_notes and not raw_automation:
            return [], [], 1.0
        exact_timing = manifest.schema_version >= 2 and manifest.source_tempo_bpm is not None
        if exact_timing:
            phrase_end = (
                float(manifest.playlist_phrase.duration_ticks)
                if manifest.playlist_phrase is not None
                else 0.0
            )
            end = max(1.0, phrase_end, note_end, automation_end)
            preview_duration = capsule.preview_duration_seconds()
            # Phrase previews keep their saved trailing space in the geometry,
            # but the UI expands that geometry only through the last visible
            # MIDI/automation event.  Map that displayed endpoint to the same
            # instant in the rendered WAV; using the entire phrase duration
            # here makes repeated or cut placements visibly run ahead of audio
            # whenever the captured range has an empty tail.
            displayed_content_end = max(note_end, automation_end)
            midi_duration = displayed_content_end * 60.0 / (
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
            end = max(1.0, note_end, pattern_end, automation_end)
            playback_end = max(note_end, automation_end) / end
        low = min((key for _, _, _, key in indexed_notes), default=0)
        high = max((key for _, _, _, key in indexed_notes), default=0)
        pitch_span = max(1, high - low)
        preview = [
            [
                round(position / end, 6),
                round(length / end, 6),
                round((key - low) / pitch_span, 6),
                channel_index,
            ]
            for channel_index, position, length, key in indexed_notes
        ]
        automation_preview = [
            [
                channel_index,
                [
                    [round(position / end, 6), round(value, 6), round(tension, 6)]
                    for position, value, tension in curve
                ],
            ]
            for channel_index, curve in raw_automation
        ]
        return preview, automation_preview, playback_end

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
                    if not is_capsule_filename(source):
                        raise ValueError(
                            "file must use the .flcapsule.wav or .flcapsule extension"
                        )
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
                    warning = None
                    if copied_capsule.container_format == "legacy":
                        while True:
                            destination = unique_capsule_path(
                                self.library_dir, manifest.name
                            )
                            try:
                                copied_capsule.convert_to_playable(destination)
                                break
                            except FileExistsError:
                                continue
                            except Exception as conversion_error:
                                while True:
                                    destination = unique_legacy_capsule_path(
                                        self.library_dir, manifest.name
                                    )
                                    try:
                                        os.link(temporary, destination)
                                        break
                                    except FileExistsError:
                                        continue
                                warning = (
                                    "Legacy capsule was imported without a playable preview: "
                                    + str(conversion_error)
                                )
                                break
                    else:
                        while True:
                            destination = unique_capsule_path(
                                self.library_dir, manifest.name
                            )
                            try:
                                os.link(temporary, destination)
                                break
                            except FileExistsError:
                                continue

                    known_ids.add(manifest.id)
                    imported = {
                        "source": str(source),
                        "path": str(destination.resolve()),
                        "id": manifest.id,
                        "name": manifest.name,
                    }
                    if warning:
                        imported["warning"] = warning
                    result["imported"].append(imported)
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

    def move_to_trash(self, capsule_id: str) -> None:
        with self._lock:
            capsule = self.find(capsule_id)
            paths = [capsule.path]
            if capsule.container_format == "legacy":
                paths.append(capsule.path.with_suffix(".wav"))
            existing = [str(path) for path in paths if path.exists()]
            try:
                if existing:
                    send2trash(existing)
            finally:
                # Keep the index accurate even if a provider moves only part of a
                # multi-file legacy capsule before reporting an error.
                self.reindex()

    def delete(self, capsule_id: str) -> None:
        """Backward-compatible API that now performs a recoverable deletion."""
        self.move_to_trash(capsule_id)

    def _rewrite_manifest(self, capsule: Capsule, **changes) -> None:
        capsule.verify()
        manifest = capsule.manifest
        channel_names = changes.pop("channel_names", None)
        if channel_names is not None:
            normalized = [str(name).strip() for name in channel_names]
            if len(normalized) != len(manifest.channels) or any(not name for name in normalized):
                raise ValueError("channel names must match every capsule channel")
            for channel, name in zip(manifest.channels, normalized, strict=True):
                channel.name = name
        for key, value in changes.items():
            setattr(manifest, key, value)
        capsule.replace_manifest(manifest)
