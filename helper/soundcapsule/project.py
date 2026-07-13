from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import platform
import sqlite3
import tempfile
import time
import uuid

from .bridge import BridgeQueue, BridgeSession
from .capsule import Capsule, slugify, unique_capsule_path
from .compatibility import require_mutation_profile
from .config import Settings
from .flp import ChannelSection, FLPFile, FLPUnsupportedError, NoteRecord
from .library import CapsuleLibrary
from .project_locator import ProjectLocator
from .renderer import close_windows_fl_studio, render_project


@dataclass(slots=True)
class ImportResult:
    source_project: Path
    merged_project: Path
    source_sha256: str
    merged_sha256: str
    channel_mapping: dict[int, int]
    pattern_id: int
    transaction_id: str
    import_destination: str = "new_pattern"
    in_place: bool = False
    backup_project: Path | None = None
    reload_confirmed: bool | None = None


@dataclass(slots=True)
class UndoResult:
    project: Path
    restored_from: Path
    safety_backup: Path
    restored_sha256: str
    import_transaction_id: str
    reload_confirmed: bool | None = None


class CapsuleService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.settings.ensure()
        self.bridge = BridgeQueue(settings.bridge_dir)
        self.library = CapsuleLibrary(settings.library_dir, settings.data_dir / "library.sqlite3")
        self.library.reindex()
        for cached_preview in self.settings.cache_dir.glob("*.wav"):
            try:
                cached_preview.unlink(missing_ok=True)
            except OSError:
                continue
        self._cleanup_staging()

    @staticmethod
    def _rack_names_match(saved: list[str], live: list[str]) -> bool:
        if len(saved) != len(live):
            return False
        mismatches = sum(
            left.strip().casefold() != right.strip().casefold()
            for left, right in zip(saved, live, strict=True)
        )
        # FL can display a wrapper/preset-derived name while the saved channel
        # event still contains the generator name, and an unsaved channel rename
        # exists only in the live rack. One discrepancy in an otherwise-identical
        # rack of four or more channels remains a strong project signature; if
        # multiple saved FLPs match, ProjectLocator still refuses the ambiguity.
        allowed = max(1, len(saved) // 12) if len(saved) >= 4 else 0
        return mismatches <= allowed

    @staticmethod
    def _project_matches_session(path: Path, session: BridgeSession) -> bool:
        try:
            project = FLPFile.read(path)
            sections = project.channel_sections()
        except (OSError, ValueError, RuntimeError):
            return False
        if project.ppq != session.ppq:
            return False
        if session.channel_names:
            if session.channel_count != len(sections):
                return False
            if not CapsuleService._rack_names_match(
                [section.name for section in sections], session.channel_names
            ):
                return False
        else:
            if len(session.selected_channels) != len(session.selected_channel_names):
                return False
            for index, expected_name in zip(
                session.selected_channels, session.selected_channel_names, strict=True
            ):
                if not 0 <= index < len(sections):
                    return False
                if sections[index].name.strip().casefold() != expected_name.strip().casefold():
                    return False
        return True

    @staticmethod
    def _session_project_cache_key(session: BridgeSession) -> str:
        signature = {
            "load_sequence": session.load_sequence,
            "ppq": session.ppq,
            "channel_count": session.channel_count,
            "channel_names": [name.strip().casefold() for name in session.channel_names],
        }
        return "session-" + hashlib.sha256(
            json.dumps(signature, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def _resolve_project(
        self,
        explicit: Path | None,
        session: BridgeSession | None = None,
        *,
        require_clean: bool = True,
    ) -> Path:
        if explicit:
            return explicit.expanduser().resolve()
        session = session or self.bridge.session()
        if session.midi_api_version < 38:
            raise FLPUnsupportedError("Sound Capsule requires FL Studio MIDI scripting API 38 or newer")
        if require_clean and session.changed:
            raise FLPUnsupportedError("save the FL Studio project before using Sound Capsule")
        changed_after = session.last_save_requested_at
        if changed_after <= 0 or time.time() - changed_after > 5 * 60:
            changed_after = None
        return ProjectLocator(
            self.settings.project_roots,
            cache_path=self.settings.data_dir / "project-paths.json",
        ).find_current(
            session.project_title,
            changed_after=changed_after,
            candidate_validator=lambda path: self._project_matches_session(path, session),
            cache_key=self._session_project_cache_key(session),
        )

    @staticmethod
    def _session_channel_ids(project: FLPFile, session: BridgeSession) -> list[int]:
        # The documented MIDI API exposes global rack *indexes*. FLP events and
        # note records store instance IDs, which can be sparse. Resolve through
        # saved Channel Rack order instead of assuming the numbers are equal.
        sections = project.channel_sections()
        ids: list[int] = []
        for index in session.selected_channels:
            if not 0 <= index < len(sections):
                raise FLPUnsupportedError(f"selected Channel Rack index {index} is outside the saved project")
            ids.append(sections[index].iid)
        return ids

    def capture(
        self,
        name: str,
        *,
        project_path: Path | None = None,
        preview_wav: Path | None = None,
        individually: bool = False,
        tags: list[str] | None = None,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> list[Capsule]:
        def progress(value: int, step: str) -> None:
            if progress_callback is not None:
                progress_callback(value, step)

        progress(3, "Locating the current FL Studio project")
        session = self.bridge.session() if project_path is None else None
        project_path = self._resolve_project(project_path, session)
        progress(12, "Staging the saved project")
        staged_source, _ = self._stage_project(project_path, "capture")
        generated_files = [staged_source]
        try:
            progress(18, "Reading and validating the FL Studio project")
            project = FLPFile.read(staged_source)
            require_mutation_profile(project.fl_version)
            if project.tempo_bpm is None:
                raise FLPUnsupportedError(
                    "the project does not contain a supported static tempo"
                )
            progress(24, "Reading the selected channels and pattern")
            channel_ids = (
                self._session_channel_ids(project, session)
                if session
                else [section.iid for section in project.channel_sections()]
            )
            pattern_id = session.current_pattern if session else project.current_pattern
            if not channel_ids:
                raise FLPUnsupportedError("select at least one Channel Rack channel")

            render_executable = self.settings.fl_executable
            live_windows_render = (
                preview_wav is None
                and session is not None
                and platform.system() == "Windows"
            )
            if live_windows_render:
                render_executable = self._windows_host_executable(session)
                if render_executable is None:
                    raise FLPUnsupportedError(
                        "could not identify the connected FL Studio executable; "
                        "reload the updated Sound Capsule MIDI script"
                    )

            render_lifecycle = (
                self._windows_render_lifecycle(
                    session,
                    project_path,
                    render_executable,
                    progress,
                )
                if live_windows_render
                else nullcontext()
            )
            with render_lifecycle:
                captures = [[iid] for iid in channel_ids] if individually else [channel_ids]
                results: list[Capsule] = []
                for capture_index, selected in enumerate(captures):
                    start = 28 + round(capture_index * 62 / len(captures))
                    finish = 28 + round((capture_index + 1) * 62 / len(captures))
                    selected_name = (
                        name
                        if not individually
                        else next(
                            section.name
                            for section in project.channel_sections()
                            if section.iid == selected[0]
                        )
                    )
                    selected_preview = preview_wav
                    if selected_preview is None:
                        progress(start, f"Preparing preview for {selected_name}")
                        staged = self._build_preview_project(
                            project, selected, pattern_id, selected_name
                        )
                        generated_files.append(staged)
                        output = self.settings.staging_dir / f"{slugify(selected_name)}.wav"
                        generated_files.append(output)
                        progress(
                            start + max(1, (finish - start) // 8),
                            f"Rendering preview for {selected_name}",
                        )
                        selected_preview = render_project(
                            staged,
                            output,
                            fl_executable=render_executable,
                        )
                    progress(max(start + 1, finish - 8), f"Packaging {selected_name}")
                    destination = unique_capsule_path(
                        self.settings.library_dir, selected_name
                    )
                    results.append(
                        Capsule.build(
                            destination,
                            name=selected_name,
                            project=project,
                            channel_ids=selected,
                            pattern_id=pattern_id,
                            pattern_length_steps=(
                                session.pattern_length_steps if session else None
                            ),
                            preview_wav=selected_preview,
                            save_mode="individual" if individually else "group",
                            tags=tags,
                        )
                    )
                    if preview_wav is None:
                        for generated in (output, staged):
                            self._delete_staged_file(generated)
                            if not generated.exists():
                                generated_files.remove(generated)
                    progress(finish, f"Saved {selected_name}")
                progress(94, "Indexing the capsule library")
                self.library.reindex()
            progress(100, "Capsule saved")
            return results
        finally:
            for generated in reversed(generated_files):
                self._delete_staged_file(generated)

    def _build_preview_project(self, source: FLPFile, channel_ids: list[int], pattern_id: int, name: str) -> Path:
        preview = source.isolated_preview_project(channel_ids, pattern_id)
        path = self.settings.staging_dir / f"preview-{slugify(name)}-{int(time.time() * 1000)}.flp"
        self._atomic_write(path, preview.to_bytes())
        return path

    @contextmanager
    def _windows_render_lifecycle(
        self,
        session: BridgeSession,
        project_path: Path,
        executable: Path,
        progress: Callable[[int, str], None],
    ):
        progress(27, "Closing FL Studio for command-line rendering")
        close_windows_fl_studio(
            session.host_pid,
            expected_executable=executable,
        )
        operation_error: BaseException | None = None
        try:
            yield
        except BaseException as error:
            operation_error = error
            raise
        finally:
            progress(97, "Reopening the original FL Studio project")
            try:
                self._open(project_path, session)
            except Exception as reopen_error:
                if operation_error is None:
                    raise
                operation_error.add_note(
                    f"The original FL Studio project could not be reopened: {reopen_error}"
                )

    def import_capsule(
        self,
        capsule_id: str,
        *,
        mode: str,
        project_path: Path | None = None,
        target_channels: list[int] | None = None,
        pattern_id: int | None = None,
        import_destination: str | None = None,
        open_project: bool = True,
        in_place: bool = False,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> ImportResult:
        def progress(value: int, step: str) -> None:
            if progress_callback is not None:
                progress_callback(value, step)

        progress(3, "Locating the current FL Studio project")
        session = self.bridge.session() if project_path is None else None
        project_path = self._resolve_project(project_path, session)
        progress(10, "Verifying the capsule")
        capsule = self.library.find(capsule_id)
        capsule.verify()
        manifest = capsule.manifest
        progress(20, "Staging the saved project")
        staged_source, source_hash = self._stage_project(project_path, "import")
        project = FLPFile.read(staged_source)
        require_mutation_profile(project.fl_version)

        progress(32, "Restoring instruments, samples, and MIDI")
        sections: list[ChannelSection] = []
        notes_by_source: dict[int, list[NoteRecord]] = {}
        for channel in manifest.channels:
            state = capsule.read_channel_state(channel)
            state_section = state.channel_sections()[0]
            asset = capsule.extract_sample_asset(
                channel, self.settings.data_dir / "ImportedAssets" / manifest.id
            )
            if asset is not None:
                state_section = state_section.with_sample_path(str(asset), unicode_text=True)
            state_section = state_section.with_name(channel.name, unicode_text=True)
            # Restore the original ID stored in the manifest for deterministic mapping.
            section = state_section.remap(channel.source_iid)
            sections.append(section)
            notes_by_source[channel.source_iid] = capsule.read_notes(channel)

        destination_mode = import_destination or self.settings.import_destination
        if destination_mode not in (
            "current_pattern", "new_pattern", "override_selection"
        ):
            raise ValueError("invalid import destination")
        if mode == "append" and destination_mode == "override_selection":
            mode = "override"

        progress(48, "Merging channels and notes")
        if mode == "append":
            active_pattern = (
                pattern_id
                if pattern_id is not None
                else (session.current_pattern if session else project.current_pattern)
            )
            merged, mapping, new_pattern = project.append_capsule(
                sections,
                notes_by_source,
                source_ppq=manifest.source_ppq,
                pattern_name=manifest.name,
                target_pattern_id=(
                    active_pattern if destination_mode == "current_pattern" else None
                ),
            )
        elif mode == "override":
            targets = target_channels or (self._session_channel_ids(project, session) if session else [])
            active_pattern = (
                pattern_id
                if pattern_id is not None
                else (session.current_pattern if session else project.current_pattern)
            )
            merged = project.override_capsule(
                sections,
                notes_by_source,
                targets,
                source_ppq=manifest.source_ppq,
                pattern_id=active_pattern,
            )
            mapping = {section.iid: target for section, target in zip(sections, targets, strict=True)}
            new_pattern = active_pattern
        else:
            raise ValueError("mode must be 'append' or 'override'")

        progress(62, "Creating the safety backup")
        merged_bytes = merged.to_bytes()
        merged_hash = _sha256(merged_bytes)
        backup: Path | None = None
        if in_place:
            original_bytes = staged_source.read_bytes()
            backup = self._create_backup(project_path, project, original_bytes, "before-import")
            if _sha256(project_path.read_bytes()) != source_hash:
                backup.unlink(missing_ok=True)
                raise RuntimeError("source project changed during import; its staged backup was discarded")
            try:
                progress(72, "Writing and validating the updated project")
                self._atomic_write(project_path, merged_bytes)
                FLPFile.read(project_path).validate()
                if _sha256(project_path.read_bytes()) != merged_hash:
                    raise RuntimeError("in-place merged project checksum did not match")
            except Exception:
                self._atomic_write(project_path, original_bytes)
                raise
            destination = project_path
        else:
            while True:
                destination = self._versioned_import_path(project_path, manifest.name)
                try:
                    self._atomic_write(destination, merged_bytes, overwrite=False)
                    break
                except FileExistsError:
                    continue
            FLPFile.read(destination).validate()
            if _sha256(project_path.read_bytes()) != source_hash:
                destination.unlink(missing_ok=True)
                raise RuntimeError("source project changed during import; merged version was discarded")

        transaction_id = str(uuid.uuid4())
        result = ImportResult(
            source_project=project_path,
            merged_project=destination,
            source_sha256=source_hash,
            merged_sha256=merged_hash,
            channel_mapping=mapping,
            pattern_id=new_pattern,
            transaction_id=transaction_id,
            import_destination=(
                "override_selection" if mode == "override" else destination_mode
            ),
            in_place=in_place,
            backup_project=backup,
        )
        self._write_transaction(result, capsule_id, mode)
        if open_project:
            progress(86, "Reopening the project in FL Studio")
            before_load = session.load_sequence if session else None
            self._open(destination, session)
            progress(92, "Waiting for FL Studio to reconnect")
            result.reload_confirmed = self._wait_for_reload(before_load) if session else None
        # Usage is non-critical bookkeeping and must never turn a completed,
        # validated project mutation into a reported import failure.
        try:
            self.library.record_use(capsule_id)
        except (KeyError, OSError, sqlite3.Error):
            pass
        progress(100, "Import complete")
        return result

    def undo_last_import(
        self,
        *,
        project_path: Path | None = None,
        open_project: bool = True,
    ) -> UndoResult:
        session = self.bridge.session() if project_path is None else None
        project_path = self._resolve_project(project_path, session)
        record = self._latest_reversible_import(project_path)
        expires_at = float(record.get("timestamp", 0.0)) + self.settings.undo_window_minutes * 60
        if time.time() > expires_at:
            raise FLPUnsupportedError(
                f"Undo Import expired after {self.settings.undo_window_minutes} minutes"
            )
        current_bytes = project_path.read_bytes()
        backup = Path(record["backup"])
        backup_bytes = backup.read_bytes()
        if _sha256(backup_bytes) != record["source_sha256"]:
            raise RuntimeError("the import backup checksum no longer matches the transaction journal")
        original = FLPFile.from_bytes(backup_bytes)
        require_mutation_profile(original.fl_version)
        safety = self._create_backup(project_path, original, current_bytes, "before-undo")
        try:
            self._atomic_write(project_path, backup_bytes)
            FLPFile.read(project_path).validate()
            if _sha256(project_path.read_bytes()) != record["source_sha256"]:
                raise RuntimeError("restored project checksum did not match its backup")
        except Exception:
            self._atomic_write(project_path, current_bytes)
            raise

        result = UndoResult(
            project=project_path,
            restored_from=backup,
            safety_backup=safety,
            restored_sha256=record["source_sha256"],
            import_transaction_id=record["transaction_id"],
        )
        self._write_undo_transaction(result)
        if open_project:
            before_load = session.load_sequence if session else None
            self._open(project_path, session)
            result.reload_confirmed = self._wait_for_reload(before_load) if session else None
        return result

    def undo_status(self, project_path: Path | None) -> dict:
        if project_path is None:
            return {"available": False, "remaining_seconds": 0, "window_minutes": self.settings.undo_window_minutes}
        try:
            record = self._latest_reversible_import(project_path)
            backup = Path(record["backup"])
            if not backup.is_file():
                raise FileNotFoundError(backup)
            expires_at = float(record.get("timestamp", 0.0)) + self.settings.undo_window_minutes * 60
            remaining = max(0, int(expires_at - time.time()))
            return {
                "available": remaining > 0,
                "remaining_seconds": remaining,
                "expires_at": expires_at,
                "window_minutes": self.settings.undo_window_minutes,
                "transaction_id": record.get("transaction_id"),
            }
        except (OSError, ValueError, FLPUnsupportedError):
            return {"available": False, "remaining_seconds": 0, "window_minutes": self.settings.undo_window_minutes}

    def _versioned_import_path(self, source: Path, capsule_name: str) -> Path:
        stem = f"{source.stem[:120]}_capsule-{slugify(capsule_name)[:80]}"
        candidate = source.with_name(f"{stem}-001.flp")
        index = 2
        while candidate.exists():
            candidate = source.with_name(f"{stem}-{index:03d}.flp")
            index += 1
        return candidate

    def _backup_directory(self, source: Path, project: FLPFile) -> Path:
        if project.data_path:
            # FL can persist unresolved browser/project macros in this field.
            # Creating a literal "%...%" directory would make the backup hard
            # to find and could put it somewhere other than FL's project-data
            # folder, so use the conventional project-local fallback instead.
            raw = project.data_path.strip()
            if (
                raw
                and "\0" not in raw
                and "://" not in raw
                and not any(marker in raw for marker in ("%", "${", "$(", "<", ">"))
            ):
                configured = Path(raw).expanduser()
                if not configured.is_absolute():
                    configured = source.parent / configured
                return configured / "Backups" / "Sound Capsule"
        if source.parent.name.casefold() == "projects":
            return source.parent / "Backup" / "Sound Capsule"
        return source.parent / "Backups" / "Sound Capsule"

    def _create_backup(self, source: Path, project: FLPFile, data: bytes, purpose: str) -> Path:
        directory = self._backup_directory(source, project)
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        stem = f"{source.stem[:100]}-{purpose}-{timestamp}-{uuid.uuid4().hex[:8]}"
        destination = directory / f"{stem}.flp"
        self._atomic_write(destination, data, overwrite=False)
        if _sha256(destination.read_bytes()) != _sha256(data):
            destination.unlink(missing_ok=True)
            raise RuntimeError("Sound Capsule backup checksum did not match the source project")
        FLPFile.read(destination).validate()
        return destination

    def _stage_project(self, source: Path, purpose: str) -> tuple[Path, str]:
        data = source.read_bytes()
        source_hash = _sha256(data)
        name = f"{purpose}-{slugify(source.stem)}-{int(time.time() * 1000)}.flp"
        destination = self.settings.staging_dir / name
        self._atomic_write(destination, data)
        if _sha256(destination.read_bytes()) != _sha256(data):
            destination.unlink(missing_ok=True)
            raise RuntimeError("staged project checksum did not match the saved source")
        return destination, source_hash

    def _cleanup_staging(self, maximum_age: float = 7 * 24 * 60 * 60) -> None:
        cutoff = time.time() - maximum_age
        for path in self.settings.staging_dir.iterdir():
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue

    @staticmethod
    def _delete_staged_file(path: Path, attempts: int = 10) -> None:
        for attempt in range(attempts):
            try:
                path.unlink(missing_ok=True)
                return
            except OSError:
                if attempt + 1 < attempts:
                    time.sleep(0.05)

    def _atomic_write(self, path: Path, data: bytes, *, overwrite: bool = True) -> None:
        import os
        path.parent.mkdir(parents=True, exist_ok=True)
        existing_mode = path.stat().st_mode if overwrite and path.exists() else None
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if existing_mode is not None:
            os.chmod(temporary, existing_mode)
        try:
            if overwrite:
                temporary.replace(path)
            else:
                os.link(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _write_transaction(self, result: ImportResult, capsule_id: str, mode: str) -> None:
        record = {
            "action": "import", "transaction_id": result.transaction_id,
            "timestamp": time.time(), "capsule_id": capsule_id, "mode": mode,
            "import_destination": result.import_destination,
            "source": str(result.source_project), "merged": str(result.merged_project),
            "source_sha256": result.source_sha256, "merged_sha256": result.merged_sha256,
            "mapping": result.channel_mapping, "pattern_id": result.pattern_id,
            "in_place": result.in_place,
            "backup": str(result.backup_project) if result.backup_project else None,
        }
        self._append_journal(record)

    def _write_undo_transaction(self, result: UndoResult) -> None:
        self._append_journal(
            {
                "action": "undo",
                "timestamp": time.time(),
                "source": str(result.project),
                "import_transaction_id": result.import_transaction_id,
                "restored_from": str(result.restored_from),
                "safety_backup": str(result.safety_backup),
                "restored_sha256": result.restored_sha256,
            }
        )

    def _append_journal(self, record: dict) -> None:
        import os
        journal = self.settings.data_dir / "transactions.jsonl"
        with journal.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _latest_reversible_import(self, project: Path) -> dict:
        journal = self.settings.data_dir / "transactions.jsonl"
        if not journal.exists():
            raise FLPUnsupportedError("there is no Sound Capsule import to undo")
        records: list[dict] = []
        for line in journal.read_text(encoding="utf-8").splitlines():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        undone = {
            record.get("import_transaction_id")
            for record in records
            if record.get("action") == "undo"
        }
        resolved = str(project.resolve())
        for record in reversed(records):
            if (
                record.get("action") == "import"
                and record.get("in_place") is True
                and record.get("backup")
                and str(Path(record.get("source", "")).resolve()) == resolved
            ):
                if record.get("transaction_id") in undone:
                    break
                return record
        raise FLPUnsupportedError("there is no reversible in-place import for this project")

    def _wait_for_reload(self, previous_sequence: int | None, timeout: float = 30.0) -> bool:
        if previous_sequence is None:
            return False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                session = self.bridge.session(maximum_age=15.0)
                if session.load_sequence > previous_sequence:
                    if session.last_load_status == 100:
                        return True
                    if session.last_load_status == 101:
                        return False
            except (OSError, RuntimeError, ValueError):
                pass
            time.sleep(0.1)
        return False

    @staticmethod
    def _mac_host_application(session: BridgeSession) -> Path | None:
        executable = Path(session.host_executable).expanduser() if session.host_executable else None
        if executable is not None:
            for candidate in (executable, *executable.parents):
                if (
                    candidate.suffix.casefold() == ".app"
                    and candidate.name.casefold().startswith("fl studio")
                    and candidate.is_dir()
                ):
                    return candidate
        name = session.host_name.strip()
        if not name.casefold().startswith("fl studio"):
            return None
        for root in (Path("/Applications"), Path.home() / "Applications"):
            candidate = root / f"{name}.app"
            if candidate.is_dir():
                return candidate
        return None

    @staticmethod
    def _windows_host_executable(session: BridgeSession) -> Path | None:
        executable = Path(session.host_executable).expanduser() if session.host_executable else None
        if (
            executable is not None
            and executable.is_file()
            and "fl" in executable.name.casefold()
        ):
            return executable
        name = session.host_name.strip()
        if not name.casefold().startswith("fl studio"):
            return None
        import os
        for variable in ("ProgramFiles", "ProgramFiles(x86)"):
            root = os.environ.get(variable)
            if not root:
                continue
            candidate = Path(root) / "Image-Line" / name / "FL64.exe"
            if candidate.is_file():
                return candidate
        return None

    def _open(self, path: Path, session: BridgeSession | None = None) -> None:
        import subprocess
        if platform.system() == "Darwin":
            application = (
                self._mac_host_application(session)
                if session is not None
                else self.settings.fl_executable
            )
            if session is not None and application is None:
                raise FLPUnsupportedError(
                    "could not identify the connected FL Studio application; "
                    "reload the updated Sound Capsule MIDI script"
                )
            command = ["open", "-a", str(application), str(path)] if application else ["open", str(path)]
            subprocess.Popen(command)
        elif platform.system() == "Windows":
            executable = (
                self._windows_host_executable(session)
                if session is not None
                else self.settings.fl_executable
            )
            if session is not None and executable is None:
                raise FLPUnsupportedError(
                    "could not identify the connected FL Studio application; "
                    "reload the updated Sound Capsule MIDI script"
                )
            if executable is not None:
                subprocess.Popen([str(executable), str(path)])
            else:
                import os
                os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(path)])


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
