from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import socketserver
import threading
import time
import uuid

from . import __version__
from .capsule import Capsule
from .config import Settings
from .library import CapsuleLibrary
from .project import CapsuleService


class SoundCapsuleRequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        self.connection.settimeout(5.0)
        raw = self.rfile.readline(2 * 1024 * 1024)
        if not raw:
            return
        try:
            if not raw.endswith(b"\n"):
                raise ValueError("request is incomplete or exceeds 2 MiB")
            request = json.loads(raw)
            response = self.server.dispatch(request)  # type: ignore[attr-defined]
            payload = {"ok": True, **response}
        except Exception as error:
            payload = {"ok": False, "error": str(error), "type": type(error).__name__}
        self.wfile.write(json.dumps(payload).encode() + b"\n")


class SoundCapsuleServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, settings: Settings):
        import ipaddress
        try:
            if not ipaddress.ip_address(settings.server_host).is_loopback:
                raise ValueError("Sound Capsule helper must bind to a loopback address")
        except ValueError as error:
            raise ValueError("server_host must be a numeric loopback address") from error
        self.settings = settings
        self.service = CapsuleService(settings)
        self.operation_lock = threading.RLock()
        self.progress_lock = threading.Lock()
        self.session_project_lock = threading.Lock()
        self.session_project_token: str | None = None
        self.session_project_path: Path | None = None
        self.session_project_resolution: str | None = None
        self.import_progress: dict = {
            "operation_id": None,
            "active": False,
            "progress": 0,
            "step": "Idle",
            "error": None,
            "updated_at": time.time(),
        }
        super().__init__((settings.server_host, settings.server_port), SoundCapsuleRequestHandler)

    @staticmethod
    def _session_resolution_token(session) -> str:
        """Identify one live FL session without using its changing heartbeat."""
        has_full_rack = (
            bool(session.channel_names)
            and session.channel_count == len(session.channel_names)
        )
        payload = {
            "midi_api_version": session.midi_api_version,
            "host_name": session.host_name,
            "host_executable": session.host_executable,
            "project_title": session.project_title,
            # Selection belongs to the UI state, not project identity. Retain
            # it only as a compatibility fallback for an older MIDI script
            # that does not yet publish the full Channel Rack signature.
            "selected_channels": [] if has_full_rack else session.selected_channels,
            "selected_channel_names": [] if has_full_rack else session.selected_channel_names,
            "channel_count": session.channel_count,
            "channel_names": session.channel_names,
            "ppq": session.ppq,
            "load_sequence": session.load_sequence,
            "save_sequence": session.save_sequence,
            "last_save_requested_at": session.last_save_requested_at,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def _resolve_session_project_in_background(self, token: str, session) -> None:
        try:
            project_path = self.service._resolve_project(
                None, session, require_clean=False
            )
        except (OSError, RuntimeError, ValueError):
            project_path = None
        with self.session_project_lock:
            if self.session_project_resolution != token:
                return
            self.session_project_token = token
            self.session_project_path = project_path
            if project_path is None:
                # FL can publish its first heartbeat before its MRU/save state
                # settles. Let a later UI poll retry without ever blocking it.
                self.session_project_resolution = None

    def _project_for_live_session(self, session) -> Path | None:
        """Return cached project identity immediately and resolve misses off-thread.

        A live MIDI heartbeat is the connection signal. Locating an untitled FLP
        may require parsing several recent projects and must never delay that
        signal long enough for the client to report a false disconnect.
        """
        token = self._session_resolution_token(session)
        with self.session_project_lock:
            if self.session_project_token == token and self.session_project_path is not None:
                return self.session_project_path
            if self.session_project_resolution == token:
                return None
            self.session_project_resolution = token
            self.session_project_token = token
            self.session_project_path = None
        threading.Thread(
            target=self._resolve_session_project_in_background,
            args=(token, session),
            name="SoundCapsuleProjectResolver",
            daemon=True,
        ).start()
        return None

    def _update_import_progress(
        self,
        operation_id: str,
        progress: int,
        step: str,
        *,
        active: bool = True,
        error: str | None = None,
    ) -> None:
        with self.progress_lock:
            self.import_progress = {
                "operation_id": operation_id,
                "active": active,
                "progress": max(0, min(100, int(progress))),
                "step": str(step)[:300],
                "error": error,
                "updated_at": time.time(),
            }

    @staticmethod
    def _file_digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _remove_empty_parents(path: Path, root: Path) -> None:
        directory = path.parent
        while directory != root and root in directory.parents:
            try:
                directory.rmdir()
            except OSError:
                break
            directory = directory.parent

    def _set_library_location(self, raw_path: object, move_existing: bool) -> dict:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("Choose a capsule save location")

        old_dir = self.settings.library_dir.resolve()
        new_dir = Path(raw_path).expanduser().resolve()
        if new_dir == old_dir:
            return {
                "library_dir": str(new_dir),
                "previous_library_dir": str(old_dir),
                "moved_count": 0,
                "not_moved_count": 0,
            }
        if old_dir in new_dir.parents or new_dir in old_dir.parents:
            raise ValueError(
                "The new capsule location cannot contain, or be inside, the current location"
            )

        try:
            new_dir.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise OSError(f"Could not create the capsule location: {error}") from error
        if not new_dir.is_dir():
            raise ValueError("The capsule save location must be a folder")

        source_paths = sorted(old_dir.rglob("*.flcapsule")) if move_existing else []
        conflicts: list[Path] = []
        candidates: list[tuple[Path, Path, Path]] = []
        staging_dir = new_dir / f".sound-capsule-move-{uuid.uuid4().hex}"
        installed: list[tuple[Path, Path]] = []
        settings_persisted = False

        try:
            # Creating the private staging folder also verifies that the chosen
            # destination is writable before settings are changed.
            staging_dir.mkdir()
            if move_existing:
                for source in source_paths:
                    relative = source.relative_to(old_dir)
                    destination = new_dir / relative
                    if destination.exists():
                        conflicts.append(source)
                        continue
                    staged = (staging_dir / relative).with_suffix(
                        relative.suffix + ".pending"
                    )
                    staged.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, staged)
                    Capsule(staged).verify()
                    if self._file_digest(source) != self._file_digest(staged):
                        raise RuntimeError(f"Copied capsule did not match its source: {relative}")
                    candidates.append((source, staged, destination))

                for source, staged, destination in candidates:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    # A file may have appeared while copies were being staged.
                    if destination.exists():
                        conflicts.append(source)
                        continue
                    staged.replace(destination)
                    installed.append((source, destination))

            current = Settings.load(self.settings.data_dir)
            current.library_dir = new_dir
            current.save()
            settings_persisted = True

            replacement = CapsuleLibrary(
                new_dir, self.settings.data_dir / "library.sqlite3"
            )
            replacement.reindex()
            self.settings.library_dir = new_dir
            self.service.library = replacement
        except Exception:
            if settings_persisted:
                try:
                    current.library_dir = old_dir
                    current.save()
                except Exception:
                    pass
            for _, destination in reversed(installed):
                try:
                    destination.unlink()
                    self._remove_empty_parents(destination, new_dir)
                except OSError:
                    pass
            raise
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)

        cleanup_failures: list[Path] = []
        for source, _ in installed:
            try:
                source.unlink()
                self._remove_empty_parents(source, old_dir)
            except OSError:
                cleanup_failures.append(source)

        return {
            "library_dir": str(new_dir),
            "previous_library_dir": str(old_dir),
            "moved_count": len(installed) - len(cleanup_failures),
            "not_moved_count": len(conflicts) + len(cleanup_failures),
        }

    def dispatch(self, request: dict) -> dict:
        if not isinstance(request, dict):
            raise ValueError("request must be a JSON object")
        command = request.get("command")
        args = request.get("args", {})
        if not isinstance(command, str) or not isinstance(args, dict):
            raise ValueError("request command must be text and args must be an object")
        if command == "ping":
            return {"version": __version__}
        if command == "session":
            session = self.service.bridge.session()
            project_path = self._project_for_live_session(session)
            undo = self.service.undo_status(project_path)
            return {
                "timestamp": session.timestamp,
                "project_title": session.project_title or (project_path.stem if project_path else ""),
                "project_path": str(project_path) if project_path else None,
                "midi_api_version": session.midi_api_version,
                "host_name": session.host_name,
                "host_executable": session.host_executable,
                "selected_channels": session.selected_channels,
                "selected_channel_names": session.selected_channel_names,
                "current_pattern": session.current_pattern,
                "pattern_name": session.pattern_name,
                "pattern_length_steps": session.pattern_length_steps,
                "ppq": session.ppq,
                "changed": session.changed,
                "save_sequence": session.save_sequence,
                "last_save_requested_at": session.last_save_requested_at,
                "load_sequence": session.load_sequence,
                "last_load_status": session.last_load_status,
                "last_load_at": session.last_load_at,
                "undo_available": undo["available"],
                "undo_remaining_seconds": undo["remaining_seconds"],
                "undo_window_minutes": undo["window_minutes"],
            }
        if command == "request_save":
            # Verify that FL's script is alive before publishing a request that
            # would otherwise sit unread until it expires.
            self.service.bridge.session()
            with self.operation_lock:
                request_id = self.service.bridge.request_save(timeout=30.0)
            return {"request_id": request_id, "timeout_seconds": 30}
        if command == "setup_status":
            current = Settings.load(self.settings.data_dir)
            return {
                "setup_complete": current.setup_complete and current.setup_version >= 2,
                "setup_version": current.setup_version,
                "undo_window_minutes": current.undo_window_minutes,
                "waveform_channels": current.waveform_channels,
                "import_destination": current.import_destination,
                "volume_display": current.volume_display,
                "check_updates_on_startup": current.check_updates_on_startup,
                "library_dir": str(current.library_dir),
                "app_path": str(current.app_path) if current.app_path else None,
                "midi_output_mode": current.midi_output_mode,
                "midi_external_device_identifier": current.midi_external_device_identifier,
                "midi_external_device_name": current.midi_external_device_name,
                "midi_setup_complete": current.midi_setup_complete,
            }
        if command == "configure_midi":
            with self.operation_lock:
                current = Settings.load(self.settings.data_dir)
                mode = str(args.get("mode", current.midi_output_mode))
                if mode not in ("not_configured", "external_midi_port"):
                    raise ValueError("Invalid MIDI output mode")
                identifier = args.get(
                    "external_device_identifier", current.midi_external_device_identifier
                )
                display_name = args.get(
                    "external_device_name", current.midi_external_device_name
                )
                current.midi_output_mode = mode
                current.midi_external_device_identifier = (
                    str(identifier).strip() if identifier else None
                )
                current.midi_external_device_name = (
                    str(display_name).strip() if display_name else None
                )
                current.midi_setup_complete = bool(
                    args.get("setup_complete", current.midi_setup_complete)
                )
                current.save()
                self.settings.midi_output_mode = current.midi_output_mode
                self.settings.midi_external_device_identifier = (
                    current.midi_external_device_identifier
                )
                self.settings.midi_external_device_name = current.midi_external_device_name
                self.settings.midi_setup_complete = current.midi_setup_complete
            return {
                "midi_output_mode": current.midi_output_mode,
                "midi_external_device_identifier": current.midi_external_device_identifier,
                "midi_external_device_name": current.midi_external_device_name,
                "midi_setup_complete": current.midi_setup_complete,
            }
        if command == "set_library_location":
            with self.operation_lock:
                return self._set_library_location(
                    args.get("path"), bool(args.get("move_existing", False))
                )
        if command == "configure_setup":
            with self.operation_lock:
                current = Settings.load(self.settings.data_dir)
                undo_window_minutes = int(args.get("undo_window_minutes", current.undo_window_minutes))
                waveform_channels = str(args.get("waveform_channels", current.waveform_channels))
                import_destination = str(
                    args.get("import_destination", current.import_destination)
                )
                volume_display = str(
                    args.get("volume_display", current.volume_display)
                )
                check_updates_on_startup = bool(
                    args.get("check_updates_on_startup", current.check_updates_on_startup)
                )
                if not 1 <= undo_window_minutes <= 1440:
                    raise ValueError("Undo Import duration must be between 1 and 1440 minutes")
                if waveform_channels not in ("mono", "stereo"):
                    raise ValueError("Waveform display must be mono or stereo")
                if import_destination not in (
                    "current_pattern", "new_pattern", "override_selection"
                ):
                    raise ValueError("Invalid default import destination")
                if volume_display not in ("percent", "db"):
                    raise ValueError("Volume display must be Percentage or dB")
                current.setup_complete = True
                current.setup_version = 2
                current.undo_window_minutes = undo_window_minutes
                current.waveform_channels = waveform_channels
                current.import_destination = import_destination
                current.volume_display = volume_display
                current.check_updates_on_startup = check_updates_on_startup
                current.auto_open_with_fl = False  # Retired process-watcher preference.
                current.save()
                # CapsuleService shares the server's Settings instance. Keep it
                # synchronized so a changed recovery window takes effect now,
                # rather than only after the helper is restarted.
                self.settings.setup_complete = current.setup_complete
                self.settings.setup_version = current.setup_version
                self.settings.undo_window_minutes = current.undo_window_minutes
                self.settings.waveform_channels = current.waveform_channels
                self.settings.import_destination = current.import_destination
                self.settings.volume_display = current.volume_display
                self.settings.check_updates_on_startup = current.check_updates_on_startup
                self.settings.auto_open_with_fl = current.auto_open_with_fl
            return {
                "setup_complete": True,
                "setup_version": current.setup_version,
                "undo_window_minutes": current.undo_window_minutes,
                "waveform_channels": current.waveform_channels,
                "import_destination": current.import_destination,
                "volume_display": current.volume_display,
                "check_updates_on_startup": current.check_updates_on_startup,
            }
        if command == "import_status":
            requested_id = str(args.get("operation_id", ""))
            with self.progress_lock:
                progress = dict(self.import_progress)
            if requested_id and progress.get("operation_id") != requested_id:
                return {
                    "operation_id": requested_id,
                    "active": False,
                    "progress": 0,
                    "step": "Waiting to start",
                    "error": None,
                }
            return progress
        if command == "list":
            search = str(args.get("search", ""))[:256]
            return {
                "capsules": self.service.library.list(
                    search,
                    favorites_only=bool(args.get("favorites_only", False)),
                    sort_by=str(args.get("sort_by", "recent")),
                    descending=bool(args.get("descending", True)),
                    limit=args.get("limit", 1000),
                    offset=args.get("offset", 0),
                )
            }
        if command == "preview":
            capsule = self.service.library.find(args["id"])
            capsule.verify()
            return {"path": str(capsule.path)}
        if command == "favorite":
            with self.operation_lock:
                self.service.library.set_favorite(args["id"], bool(args["value"]))
            return {}
        if command == "rename":
            with self.operation_lock:
                self.service.library.rename(args["id"], args["name"])
            return {}
        if command == "tags":
            with self.operation_lock:
                self.service.library.set_tags(args["id"], list(args.get("tags", [])))
            return {}
        if command == "delete":
            with self.operation_lock:
                self.service.library.delete(args["id"])
            return {}
        if command == "add_capsules":
            raw_paths = args.get("paths", [])
            if not isinstance(raw_paths, list) or not raw_paths:
                raise ValueError("add_capsules paths must be a non-empty list")
            if not all(isinstance(path, str) for path in raw_paths):
                raise ValueError("add_capsules paths must contain only strings")
            with self.operation_lock:
                return self.service.library.add_capsules(
                    [Path(path) for path in raw_paths]
                )
        if command == "capture":
            raw_tags = args.get("tags", [])
            if not isinstance(raw_tags, list):
                raise ValueError("capture tags must be a list")
            with self.operation_lock:
                capsules = self.service.capture(
                    str(args.get("name", "Sound Capsule"))[:200],
                    project_path=Path(args["project"]) if args.get("project") else None,
                    preview_wav=Path(args["preview"]) if args.get("preview") else None,
                    individually=bool(args.get("individually", False)),
                    tags=[str(tag)[:100] for tag in raw_tags][:100],
                )
            return {"capsules": [capsule.manifest.to_dict() for capsule in capsules]}
        if command == "import":
            operation_id = str(args.get("operation_id") or uuid.uuid4())[:100]
            self._update_import_progress(operation_id, 1, "Starting import")
            try:
                with self.operation_lock:
                    result = self.service.import_capsule(
                        args["id"], mode=args.get("mode", "append"),
                        project_path=Path(args["project"]) if args.get("project") else None,
                        target_channels=args.get("target_channels"),
                        pattern_id=args.get("pattern_id"),
                        import_destination=args.get("import_destination"),
                        open_project=bool(args.get("open", True)),
                        in_place=bool(args.get("in_place", True)),
                        progress_callback=lambda value, step: self._update_import_progress(
                            operation_id, value, step
                        ),
                    )
            except Exception as error:
                self._update_import_progress(
                    operation_id, 100, "Import failed", active=False, error=str(error)
                )
                raise
            self._update_import_progress(
                operation_id, 100, "Import complete", active=False
            )
            return {
                "source": str(result.source_project), "merged": str(result.merged_project),
                "mapping": result.channel_mapping, "pattern_id": result.pattern_id,
                "transaction_id": result.transaction_id,
                "in_place": result.in_place,
                "backup": str(result.backup_project) if result.backup_project else None,
                "reload_confirmed": result.reload_confirmed,
                "import_destination": result.import_destination,
                "operation_id": operation_id,
            }
        if command == "undo_import":
            with self.operation_lock:
                result = self.service.undo_last_import(
                    project_path=Path(args["project"]) if args.get("project") else None,
                    open_project=bool(args.get("open", True)),
                )
            return {
                "project": str(result.project),
                "restored_from": str(result.restored_from),
                "safety_backup": str(result.safety_backup),
                "restored_sha256": result.restored_sha256,
                "import_transaction_id": result.import_transaction_id,
                "reload_confirmed": result.reload_confirmed,
            }
        raise ValueError(f"unknown command {command!r}")


def serve(settings: Settings) -> None:
    with SoundCapsuleServer(settings) as server:
        server.serve_forever(poll_interval=0.25)
