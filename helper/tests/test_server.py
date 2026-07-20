from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from soundcapsule import __version__
from soundcapsule.bridge import (
    BridgeQueue,
    BridgeSession,
    _project_title_from_window_caption,
)
from soundcapsule.capsule import Capsule
from soundcapsule.config import Settings
from soundcapsule.flp import EVENT_FL_VERSION
from soundcapsule.server import SoundCapsuleServer
from test_flp import fixture_project, write_silence


class ServerTests(unittest.TestCase):
    @staticmethod
    def _bridge_session(**overrides) -> BridgeSession:
        values = {
            "timestamp": time.time(),
            "project_title": "Song",
            "midi_api_version": 42,
            "selected_channels": [0],
            "selected_channel_names": ["Lead"],
            "current_pattern": 1,
            "pattern_name": "Pattern 1",
            "pattern_length_steps": 16,
            "ppq": 96,
            "changed": 0,
            "save_sequence": 0,
            "last_save_requested_at": 0.0,
            "load_sequence": 0,
            "last_load_status": -1,
            "last_load_at": 0.0,
        }
        values.update(overrides)
        return BridgeSession(**values)

    @staticmethod
    def _build_capsule(path: Path, name: str, preview: Path) -> Capsule:
        return Capsule.build(
            path,
            name=name,
            project=fixture_project(),
            channel_ids=[2],
            pattern_id=3,
            pattern_length_steps=16,
            preview_wav=preview,
        )

    def test_add_capsules_command_returns_ingestion_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = root / "preview.wav"
            write_silence(preview)
            capsule = self._build_capsule(
                root / "incoming" / "Shared.flcapsule", "Shared", preview
            )
            settings = Settings(data_dir=root / "data", server_port=0)
            with SoundCapsuleServer(settings) as server:
                payload = server.dispatch(
                    {
                        "command": "add_capsules",
                        "args": {"paths": [str(capsule.path)]},
                    }
                )

            self.assertEqual(len(payload["imported"]), 1)
            self.assertFalse(payload["skipped"])
            self.assertFalse(payload["failed"])
            self.assertTrue(Path(payload["imported"][0]["path"]).is_file())

    def test_refresh_library_forgets_files_moved_to_trash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = Settings(data_dir=root / "data", server_port=0)
            settings.ensure()
            preview = root / "preview.wav"
            write_silence(preview)
            capsule = self._build_capsule(
                settings.library_dir / "Disposable.flcapsule", "Disposable", preview
            )

            with SoundCapsuleServer(settings) as server:
                self.assertEqual(len(server.dispatch({"command": "list", "args": {}})["capsules"]), 1)
                capsule.path.unlink()
                payload = server.dispatch({"command": "refresh_library", "args": {}})
                listed = server.dispatch({"command": "list", "args": {}})

            self.assertEqual(payload["count"], 0)
            self.assertFalse(listed["capsules"])

    def test_trash_command_uses_recoverable_platform_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = Settings(data_dir=root / "data", server_port=0)
            settings.ensure()
            preview = root / "preview.wav"
            write_silence(preview)
            capsule = self._build_capsule(
                settings.library_dir / "Disposable.flcapsule", "Disposable", preview
            )

            def fake_trash(paths: list[str]) -> None:
                for path in paths:
                    Path(path).unlink()

            with mock.patch("soundcapsule.library.send2trash", side_effect=fake_trash) as trash:
                with SoundCapsuleServer(settings) as server:
                    payload = server.dispatch(
                        {"command": "trash", "args": {"id": capsule.manifest.id}}
                    )
                    listed = server.dispatch({"command": "list", "args": {}})

            self.assertEqual(payload, {})
            trash.assert_called_once()
            trashed_paths = trash.call_args.args[0]
            self.assertEqual(
                [Path(path).resolve() for path in trashed_paths],
                [capsule.path.resolve()],
            )
            self.assertFalse(capsule.path.exists())
            self.assertFalse(listed["capsules"])

    def test_list_reports_non_destructive_library_migration_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = Settings(data_dir=root / "data", server_port=0)
            settings.ensure()
            legacy = settings.library_dir / "Broken.flcapsule"
            legacy.write_bytes(b"not a capsule")

            with SoundCapsuleServer(settings) as server:
                payload = server.dispatch({"command": "list", "args": {}})

            self.assertFalse(payload["migration_summary"]["converted"])
            self.assertEqual(len(payload["migration_summary"]["failed"]), 1)
            self.assertEqual(legacy.read_bytes(), b"not a capsule")

    def test_missing_bridge_has_actionable_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(RuntimeError, "enable the configured Sound Capsule MIDI input"):
                BridgeQueue(Path(temporary)).session()

    def test_fl_window_caption_yields_exact_project_filename(self) -> None:
        self.assertEqual(
            _project_title_from_window_caption(
                "temp-sound-2026.flp - FL Studio 2026"
            ),
            "temp-sound-2026",
        )
        self.assertEqual(
            _project_title_from_window_caption(
                "temp-sound-2026.flp * - FL Studio 2026"
            ),
            "temp-sound-2026",
        )
        self.assertEqual(_project_title_from_window_caption("FL Studio 2026"), "")

    def test_session_treats_fl_unsaved_project_label_as_missing_title(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            queue = BridgeQueue(Path(temporary))
            session = self._bridge_session(project_title="Unsaved Project")
            with mock.patch.object(BridgeSession, "read", return_value=session):
                resolved = queue.session()

            self.assertIs(resolved, session)
            self.assertEqual(resolved.project_title, "")

    def test_windows_session_prefers_exact_host_window_project_over_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            queue = BridgeQueue(Path(temporary))
            session = self._bridge_session(
                project_title="Previous project metadata",
                host_pid=123,
                host_executable=r"C:\Program Files\Image-Line\FL Studio 2026\FL64.exe",
            )
            with mock.patch.object(BridgeSession, "read", return_value=session), mock.patch(
                "soundcapsule.bridge.sys.platform", "win32"
            ), mock.patch(
                "soundcapsule.bridge._windows_process_is_running", return_value=True
            ), mock.patch(
                "soundcapsule.bridge._windows_project_title",
                return_value="temp-sound-2026",
            ):
                resolved = queue.session()

            self.assertIs(resolved, session)
            self.assertEqual(resolved.project_title, "temp-sound-2026")

    @unittest.skipUnless(sys.platform == "win32", "Windows process liveness is Windows-only")
    def test_stale_windows_session_remains_connected_while_exact_fl_process_is_alive(self) -> None:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        process = kernel32.OpenProcess(0x1000, False, os.getpid())
        self.assertTrue(process)
        executable = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(executable))
        self.assertTrue(
            kernel32.QueryFullProcessImageNameW(
                process, 0, executable, ctypes.byref(length)
            )
        )
        kernel32.CloseHandle(process)
        with tempfile.TemporaryDirectory() as temporary:
            queue = BridgeQueue(Path(temporary))
            session = self._bridge_session(
                timestamp=time.time() - 60,
                host_pid=os.getpid(),
                host_executable=executable.value,
                bridge_active=True,
            )
            with mock.patch.object(BridgeSession, "read", return_value=session):
                self.assertIs(queue.session(), session)

    @unittest.skipUnless(sys.platform == "win32", "Windows process liveness is Windows-only")
    def test_stale_windows_session_rejects_a_missing_process(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            queue = BridgeQueue(Path(temporary))
            session = self._bridge_session(
                timestamp=time.time() - 60,
                host_pid=0x7FFFFFFF,
                host_executable=sys.executable,
                bridge_active=True,
            )
            with mock.patch.object(BridgeSession, "read", return_value=session):
                with self.assertRaisesRegex(RuntimeError, "stale"):
                    queue.session()

    def test_explicitly_inactive_bridge_is_disconnected_even_when_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            queue = BridgeQueue(Path(temporary))
            session = self._bridge_session(bridge_active=False)
            with mock.patch.object(BridgeSession, "read", return_value=session):
                with self.assertRaisesRegex(RuntimeError, "disabled"):
                    queue.session()

    def test_non_windows_stale_session_keeps_existing_timeout_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            queue = BridgeQueue(Path(temporary))
            session = self._bridge_session(
                timestamp=time.time() - 60,
                host_pid=os.getpid(),
                host_executable=sys.executable,
                bridge_active=True,
            )
            with mock.patch.object(BridgeSession, "read", return_value=session), mock.patch(
                "soundcapsule.bridge.sys.platform", "darwin"
            ):
                with self.assertRaisesRegex(RuntimeError, "stale"):
                    queue.session()

    def test_json_line_protocol(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            settings = Settings(data_dir=Path(temporary), server_port=0)
            with SoundCapsuleServer(settings) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                with socket.create_connection(server.server_address, timeout=2) as client:
                    client.sendall(json.dumps({"command": "ping", "args": {}}).encode() + b"\n")
                    response = b""
                    while not response.endswith(b"\n"):
                        response += client.recv(4096)
                server.shutdown()
                thread.join(timeout=2)
            payload = json.loads(response)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["version"], __version__)

    def test_import_progress_can_be_polled_while_operation_is_active(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            settings = Settings(data_dir=Path(temporary), server_port=0)
            with SoundCapsuleServer(settings) as server:
                server._update_operation_progress(
                    "operation-1", 72, "Writing and validating the updated project"
                )
                payload = server.dispatch(
                    {
                        "command": "operation_status",
                        "args": {"operation_id": "operation-1"},
                    }
                )

            self.assertTrue(payload["active"])
            self.assertEqual(payload["progress"], 72)
            self.assertEqual(payload["operation_id"], "operation-1")
            self.assertIn("validating", payload["step"])

    def test_only_a_cancellable_active_operation_accepts_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            settings = Settings(data_dir=Path(temporary), server_port=0)
            with SoundCapsuleServer(settings) as server:
                server._update_operation_progress(
                    "operation-1", 28, "Rendering preview", cancellable=True
                )
                accepted = server.dispatch(
                    {
                        "command": "cancel_operation",
                        "args": {"operation_id": "operation-1"},
                    }
                )
                server.operation_cancel.clear()
                server._update_operation_progress(
                    "operation-1", 72, "Writing project", cancellable=False
                )
                rejected = server.dispatch(
                    {
                        "command": "cancel_operation",
                        "args": {"operation_id": "operation-1"},
                    }
                )

            self.assertTrue(accepted["cancel_requested"])
            self.assertFalse(rejected["cancel_requested"])

    def test_capture_progress_reports_success_and_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            settings = Settings(data_dir=Path(temporary), server_port=0)
            with SoundCapsuleServer(settings) as server:
                def successful_capture(*_args, progress_callback, **_kwargs):
                    progress_callback(18, "Reading and validating the FL Studio project")
                    progress_callback(94, "Indexing the capsule library")
                    return []

                with mock.patch.object(
                    server.service, "capture", side_effect=successful_capture
                ):
                    result = server.dispatch({
                        "command": "capture",
                        "args": {"name": "Lead", "operation_id": "capture-success"},
                    })
                success = server.dispatch({
                    "command": "operation_status",
                    "args": {"operation_id": "capture-success"},
                })
                self.assertEqual(result["operation_id"], "capture-success")
                self.assertFalse(success["active"])
                self.assertEqual(success["progress"], 100)
                self.assertEqual(success["step"], "Capsule saved")

                def failed_capture(*_args, progress_callback, **_kwargs):
                    progress_callback(24, "Reading the selected channels and pattern")
                    raise RuntimeError("render failed")

                with mock.patch.object(
                    server.service, "capture", side_effect=failed_capture
                ), self.assertRaisesRegex(RuntimeError, "render failed"):
                    server.dispatch({
                        "command": "capture",
                        "args": {"name": "Lead", "operation_id": "capture-failure"},
                    })
                failure = server.dispatch({
                    "command": "operation_status",
                    "args": {"operation_id": "capture-failure"},
                })
                self.assertFalse(failure["active"])
                self.assertEqual(failure["step"], "Capture failed")
                self.assertEqual(failure["error"], "render failed")

    def test_save_request_is_atomic_and_short_lived(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            queue = BridgeQueue(Path(temporary))
            before = time.time()
            request_id = queue.request_save(timeout=30)
            payload = json.loads(queue.command_path.read_text(encoding="utf-8"))

            self.assertEqual(payload["request_id"], request_id)
            self.assertEqual(payload["command"], "save")
            self.assertGreaterEqual(payload["created_at"], before)
            self.assertAlmostEqual(payload["expires_at"] - payload["created_at"], 30, places=2)
            self.assertFalse(list(queue.bridge_dir.glob("*.tmp")))

    def test_session_reports_save_and_reload_handshake_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            settings = Settings(data_dir=Path(temporary), server_port=0)
            settings.ensure()
            (settings.bridge_dir / "session.json").write_text(
                json.dumps(
                    {
                        "timestamp": time.time(),
                        "project_title": "Song",
                        "midi_api_version": 38,
                        "selected_channels": [1, 3],
                        "selected_channel_names": ["Lead", "Bass"],
                        "current_pattern": 4,
                        "pattern_name": "Hook",
                        "pattern_length_steps": 32,
                        "ppq": 96,
                        "changed": 1,
                        "save_sequence": 7,
                        "last_save_requested_at": 123.0,
                        "load_sequence": 9,
                        "last_load_status": 100,
                        "last_load_at": 456.0,
                    }
                ),
                encoding="utf-8",
            )
            with SoundCapsuleServer(settings) as server:
                payload = server.dispatch({"command": "session", "args": {}})

            self.assertEqual(payload["selected_channels"], [1, 3])
            self.assertEqual(payload["save_sequence"], 7)
            self.assertEqual(payload["load_sequence"], 9)
            self.assertEqual(payload["last_load_status"], 100)
            self.assertEqual(payload["pattern_length_steps"], 32)
            self.assertEqual(payload["project_fl_version"], "")

    def test_session_heartbeat_does_not_wait_for_project_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            settings = Settings(data_dir=Path(temporary), server_port=0)
            settings.ensure()
            (settings.bridge_dir / "session.json").write_text(
                json.dumps(
                    {
                        "timestamp": time.time(),
                        "project_title": "",
                        "midi_api_version": 40,
                        "selected_channels": [0],
                        "selected_channel_names": ["Lead"],
                        "current_pattern": 1,
                        "pattern_name": "Pattern 1",
                        "pattern_length_steps": 16,
                        "ppq": 96,
                        "changed": 0,
                        "save_sequence": 0,
                        "last_save_requested_at": 0.0,
                        "load_sequence": 1,
                        "last_load_status": 100,
                        "last_load_at": time.time(),
                    }
                ),
                encoding="utf-8",
            )
            resolver_started = threading.Event()
            release_resolver = threading.Event()

            def slow_resolver(*_args, **_kwargs):
                resolver_started.set()
                release_resolver.wait(2)
                return None

            with SoundCapsuleServer(settings) as server, mock.patch.object(
                server.service, "_resolve_project", side_effect=slow_resolver
            ):
                started = time.monotonic()
                payload = server.dispatch({"command": "session", "args": {}})
                elapsed = time.monotonic() - started
                self.assertTrue(resolver_started.wait(1))
                release_resolver.set()

            self.assertLess(elapsed, 0.25)
            self.assertEqual(payload["pattern_name"], "Pattern 1")
            self.assertIsNone(payload["project_path"])

    def test_full_rack_project_identity_ignores_channel_selection(self) -> None:
        session = BridgeSession(
            timestamp=time.time(), project_title="", midi_api_version=42,
            selected_channels=[0], selected_channel_names=["Lead"],
            current_pattern=1, pattern_name="Pattern 1", pattern_length_steps=16,
            ppq=96, changed=0, save_sequence=0, last_save_requested_at=0.0,
            load_sequence=1, last_load_status=100, last_load_at=time.time(),
            channel_count=2, channel_names=["Lead", "Bass"],
        )
        before = SoundCapsuleServer._session_resolution_token(session)
        session.selected_channels = [1]
        session.selected_channel_names = ["Bass"]

        self.assertEqual(
            SoundCapsuleServer._session_resolution_token(session), before
        )

    def test_session_publishes_project_after_background_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "temp.flp"
            project.write_bytes(fixture_project().to_bytes())
            settings = Settings(data_dir=root / "data", server_port=0)
            settings.ensure()
            (settings.bridge_dir / "session.json").write_text(
                json.dumps(
                    {
                        "timestamp": time.time(),
                        "project_title": "",
                        "midi_api_version": 40,
                        "selected_channels": [0],
                        "selected_channel_names": ["Serum Lead"],
                        "current_pattern": 3,
                        "pattern_name": "Verse",
                        "pattern_length_steps": 16,
                        "ppq": 96,
                        "changed": 0,
                        "save_sequence": 1,
                        "last_save_requested_at": time.time(),
                        "load_sequence": 1,
                        "last_load_status": 100,
                        "last_load_at": time.time(),
                    }
                ),
                encoding="utf-8",
            )

            with SoundCapsuleServer(settings) as server, mock.patch.object(
                server.service, "_resolve_project", return_value=project
            ):
                server.dispatch({"command": "session", "args": {}})
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline:
                    payload = server.dispatch({"command": "session", "args": {}})
                    if payload["project_path"]:
                        break
                    time.sleep(0.01)

            self.assertEqual(payload["project_title"], "temp")
            self.assertEqual(payload["project_path"], str(project))
            self.assertEqual(payload["project_fl_version"], "25.2.5.5055")

    def test_session_project_version_refreshes_after_save(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_path = root / "Song.flp"
            project_path.write_bytes(fixture_project().to_bytes())
            settings = Settings(data_dir=root / "data", server_port=0)
            settings.ensure()
            session_payload = {
                "timestamp": time.time(),
                "project_title": "Song",
                "midi_api_version": 42,
                "selected_channels": [0],
                "selected_channel_names": ["Serum Lead"],
                "current_pattern": 3,
                "pattern_name": "Verse",
                "pattern_length_steps": 16,
                "ppq": 96,
                "changed": 0,
                "save_sequence": 1,
                "last_save_requested_at": time.time(),
                "load_sequence": 1,
                "last_load_status": 100,
                "last_load_at": time.time(),
            }
            session_path = settings.bridge_dir / "session.json"
            session_path.write_text(json.dumps(session_payload), encoding="utf-8")

            with SoundCapsuleServer(settings) as server, mock.patch.object(
                server.service, "_resolve_project", return_value=project_path
            ) as resolver:
                deadline = time.monotonic() + 1
                payload = server.dispatch({"command": "session", "args": {}})
                while time.monotonic() < deadline and not payload["project_fl_version"]:
                    time.sleep(0.01)
                    payload = server.dispatch({"command": "session", "args": {}})
                self.assertEqual(payload["project_fl_version"], "25.2.5.5055")

                updated = fixture_project()
                version = next(
                    event for event in updated.events if event.id == EVENT_FL_VERSION
                )
                updated.events[updated.events.index(version)] = version.with_payload(
                    b"27.4.0.1234\0"
                )
                project_path.write_bytes(updated.to_bytes())
                session_payload["timestamp"] = time.time()
                session_payload["save_sequence"] = 2
                session_payload["last_save_requested_at"] = time.time()
                session_path.write_text(json.dumps(session_payload), encoding="utf-8")

                deadline = time.monotonic() + 1
                while time.monotonic() < deadline:
                    payload = server.dispatch({"command": "session", "args": {}})
                    if payload["project_fl_version"] == "27.4.0.1234":
                        break
                    time.sleep(0.01)

            self.assertEqual(payload["project_fl_version"], "27.4.0.1234")
            self.assertGreaterEqual(resolver.call_count, 2)

    def test_session_retries_project_discovery_after_transient_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "temp2.flp"
            project.write_bytes(fixture_project().to_bytes())
            settings = Settings(data_dir=root / "data", server_port=0)
            settings.ensure()
            (settings.bridge_dir / "session.json").write_text(
                json.dumps(
                    {
                        "timestamp": time.time(),
                        "project_title": "",
                        "midi_api_version": 42,
                        "selected_channels": [0],
                        "selected_channel_names": ["Serum Lead"],
                        "current_pattern": 3,
                        "pattern_name": "Verse",
                        "pattern_length_steps": 16,
                        "ppq": 96,
                        "changed": 0,
                        "save_sequence": 0,
                        "last_save_requested_at": 0.0,
                        "load_sequence": 0,
                        "last_load_status": -1,
                        "last_load_at": 0.0,
                    }
                ),
                encoding="utf-8",
            )

            with SoundCapsuleServer(settings) as server, mock.patch.object(
                server.service, "_resolve_project", side_effect=[None, project]
            ) as resolver:
                deadline = time.monotonic() + 1
                payload = server.dispatch({"command": "session", "args": {}})
                while time.monotonic() < deadline and not payload["project_path"]:
                    time.sleep(0.01)
                    payload = server.dispatch({"command": "session", "args": {}})

            self.assertEqual(resolver.call_count, 2)
            self.assertEqual(payload["project_title"], "temp2")
            self.assertEqual(payload["project_path"], str(project))

    def test_server_publishes_save_request_for_live_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            settings = Settings(data_dir=Path(temporary), server_port=0)
            settings.ensure()
            (settings.bridge_dir / "session.json").write_text(
                json.dumps(
                    {
                        "timestamp": time.time(),
                        "project_title": "Song",
                        "midi_api_version": 40,
                        "selected_channels": [0],
                        "selected_channel_names": ["Lead"],
                        "current_pattern": 1,
                        "pattern_name": "Pattern 1",
                        "pattern_length_steps": 16,
                        "ppq": 96,
                        "changed": 1,
                        "save_sequence": 0,
                        "last_save_requested_at": 0.0,
                        "load_sequence": 0,
                        "last_load_status": -1,
                        "last_load_at": 0.0,
                    }
                ),
                encoding="utf-8",
            )
            with SoundCapsuleServer(settings) as server:
                response = server.dispatch({"command": "request_save", "args": {}})

            command = json.loads((settings.bridge_dir / "command.json").read_text(encoding="utf-8"))
            self.assertEqual(command["request_id"], response["request_id"])
            self.assertEqual(response["timeout_seconds"], 30)

    def test_first_run_setup_preference_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            settings = Settings(data_dir=Path(temporary), server_port=0)
            app = Path(temporary) / "Sound Capsule.app"
            app.mkdir()
            settings.app_path = app
            settings.save()
            with SoundCapsuleServer(settings) as server:
                initial = server.dispatch({"command": "setup_status", "args": {}})
                server.dispatch(
                    {
                        "command": "configure_setup",
                        "args": {
                            "undo_window_minutes": 25,
                            "waveform_channels": "stereo",
                            "import_destination": "new_pattern",
                            "volume_display": "db",
                            "start_preview_at_first_audio": False,
                            "normalize_waveform_display": True,
                            "show_automation_curves": False,
                            "show_single_channel_name_in_rename": True,
                            "check_updates_on_startup": False,
                        },
                    }
                )
                runtime_minutes = server.service.settings.undo_window_minutes
                runtime_waveform = server.service.settings.waveform_channels
                runtime_destination = server.service.settings.import_destination
                runtime_volume_display = server.service.settings.volume_display
                runtime_start_at_audio = server.service.settings.start_preview_at_first_audio
                runtime_normalize = server.service.settings.normalize_waveform_display
                runtime_automation_curves = server.service.settings.show_automation_curves
                runtime_show_single = server.service.settings.show_single_channel_name_in_rename
                runtime_update_check = server.service.settings.check_updates_on_startup
                persisted = server.dispatch({"command": "setup_status", "args": {}})

            self.assertFalse(initial["setup_complete"])
            self.assertTrue(initial["check_updates_on_startup"])
            self.assertTrue(initial["start_preview_at_first_audio"])
            self.assertFalse(initial["normalize_waveform_display"])
            self.assertTrue(initial["show_automation_curves"])
            self.assertFalse(initial["show_single_channel_name_in_rename"])
            self.assertTrue(persisted["setup_complete"])
            self.assertEqual(persisted["setup_version"], 2)
            self.assertEqual(persisted["undo_window_minutes"], 25)
            self.assertEqual(runtime_minutes, 25)
            self.assertEqual(runtime_waveform, "stereo")
            self.assertEqual(persisted["waveform_channels"], "stereo")
            self.assertEqual(runtime_destination, "new_pattern")
            self.assertEqual(persisted["import_destination"], "new_pattern")
            self.assertEqual(runtime_volume_display, "db")
            self.assertEqual(persisted["volume_display"], "db")
            self.assertFalse(runtime_start_at_audio)
            self.assertFalse(persisted["start_preview_at_first_audio"])
            self.assertTrue(runtime_normalize)
            self.assertTrue(persisted["normalize_waveform_display"])
            self.assertFalse(runtime_automation_curves)
            self.assertFalse(persisted["show_automation_curves"])
            self.assertTrue(runtime_show_single)
            self.assertTrue(persisted["show_single_channel_name_in_rename"])
            self.assertFalse(runtime_update_check)
            self.assertFalse(persisted["check_updates_on_startup"])

    def test_library_location_switches_without_moving_existing_capsules(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old_library = root / "old-library"
            new_library = root / "new-library"
            preview = root / "preview.wav"
            write_silence(preview)
            capsule = self._build_capsule(old_library / "Lead.flcapsule", "Lead", preview)
            settings = Settings(
                data_dir=root / "data", library_dir=old_library, server_port=0
            )
            settings.save()

            with SoundCapsuleServer(settings) as server:
                initial = server.dispatch({"command": "setup_status", "args": {}})
                result = server.dispatch(
                    {
                        "command": "set_library_location",
                        "args": {"path": str(new_library), "move_existing": False},
                    }
                )
                visible = server.dispatch({"command": "list", "args": {}})["capsules"]

            self.assertEqual(initial["library_dir"], str(old_library))
            self.assertEqual(result["library_dir"], str(new_library.resolve()))
            self.assertEqual(result["moved_count"], 0)
            self.assertEqual(result["not_moved_count"], 0)
            self.assertTrue(capsule.path.exists())
            self.assertEqual(visible, [])
            self.assertEqual(
                Settings.load(root / "data").library_dir, new_library.resolve()
            )

    def test_registry_fl_user_folder_installs_bridge_automatically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            custom = root / "Custom FL Studio Data"
            custom.mkdir()
            settings = Settings(data_dir=data, server_port=0)
            settings.ensure()
            settings.bridge_script_template.parent.mkdir(parents=True)
            settings.bridge_script_template.write_text("# bridge template\n", encoding="utf-8")
            settings.save()

            with mock.patch(
                "soundcapsule.config.registered_fl_user_folder", return_value=custom
            ), SoundCapsuleServer(settings) as server:
                result = server.dispatch({"command": "configure_setup", "args": {}})

            target = (
                custom / "Settings" / "Hardware" / "Sound Capsule"
                / "device_SoundCapsule.py"
            )
            self.assertNotIn("fl_user_folder", result)
            self.assertNotIn("midi_bridge_path", result)
            self.assertNotIn(
                "fl_user_folder",
                json.loads((data / "settings.json").read_text(encoding="utf-8")),
            )
            self.assertEqual(target.read_text(encoding="utf-8"), "# bridge template\n")

    def test_missing_registry_fl_user_folder_has_actionable_setup_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = Settings(data_dir=root / "data", server_port=0)
            settings.ensure()
            settings.bridge_script_template.parent.mkdir(parents=True)
            settings.bridge_script_template.write_text("# bridge\n", encoding="utf-8")
            settings.save()

            with mock.patch(
                "soundcapsule.config.registered_fl_user_folder", return_value=None
            ), SoundCapsuleServer(settings) as server:
                with self.assertRaisesRegex(RuntimeError, "Image-Line's registry data"):
                    server.dispatch({
                        "command": "configure_setup",
                        "args": {},
                    })

    def test_library_location_merges_and_reports_relative_path_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old_library = root / "old-library"
            new_library = root / "new-library"
            preview = root / "preview.wav"
            write_silence(preview)
            moved = self._build_capsule(
                old_library / "nested" / "Moved.flcapsule", "Moved", preview
            )
            conflict = self._build_capsule(
                old_library / "nested" / "Conflict.flcapsule", "Old conflict", preview
            )
            destination_conflict = self._build_capsule(
                new_library / "nested" / "Conflict.flcapsule", "New conflict", preview
            )
            destination_only = self._build_capsule(
                new_library / "Destination.flcapsule", "Destination", preview
            )
            conflict_bytes = destination_conflict.path.read_bytes()
            settings = Settings(
                data_dir=root / "data", library_dir=old_library, server_port=0
            )

            with SoundCapsuleServer(settings) as server:
                result = server.dispatch(
                    {
                        "command": "set_library_location",
                        "args": {"path": str(new_library), "move_existing": True},
                    }
                )
                visible_names = {
                    row["name"]
                    for row in server.dispatch({"command": "list", "args": {}})["capsules"]
                }

            self.assertEqual(result["moved_count"], 1)
            self.assertEqual(result["not_moved_count"], 1)
            self.assertEqual(result["previous_library_dir"], str(old_library.resolve()))
            self.assertFalse(moved.path.exists())
            self.assertTrue((new_library / "nested" / "Moved.flcapsule").exists())
            self.assertTrue(conflict.path.exists())
            self.assertEqual(destination_conflict.path.read_bytes(), conflict_bytes)
            self.assertEqual(visible_names, {"Moved", "New conflict", "Destination"})
            self.assertTrue(destination_only.path.exists())

    def test_library_location_rejects_overlapping_folders(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old_library = root / "library"
            settings = Settings(
                data_dir=root / "data", library_dir=old_library, server_port=0
            )
            with SoundCapsuleServer(settings) as server:
                with self.assertRaisesRegex(ValueError, "cannot contain"):
                    server.dispatch(
                        {
                            "command": "set_library_location",
                            "args": {
                                "path": str(old_library / "nested"),
                                "move_existing": True,
                            },
                        }
                    )

    def test_library_location_copy_failure_keeps_old_library_active(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old_library = root / "old-library"
            new_library = root / "new-library"
            preview = root / "preview.wav"
            write_silence(preview)
            capsule = self._build_capsule(old_library / "Lead.flcapsule", "Lead", preview)
            existing = new_library / "keep.txt"
            existing.parent.mkdir(parents=True)
            existing.write_text("keep", encoding="utf-8")
            settings = Settings(
                data_dir=root / "data", library_dir=old_library, server_port=0
            )
            settings.save()

            with SoundCapsuleServer(settings) as server:
                with mock.patch(
                    "soundcapsule.server.shutil.copy2",
                    side_effect=OSError("simulated copy failure"),
                ):
                    with self.assertRaisesRegex(OSError, "simulated copy failure"):
                        server.dispatch(
                            {
                                "command": "set_library_location",
                                "args": {
                                    "path": str(new_library),
                                    "move_existing": True,
                                },
                            }
                        )
                visible = server.dispatch({"command": "list", "args": {}})["capsules"]

            self.assertTrue(capsule.path.exists())
            self.assertEqual(existing.read_text(encoding="utf-8"), "keep")
            self.assertEqual(Settings.load(root / "data").library_dir, old_library)
            self.assertEqual([row["name"] for row in visible], ["Lead"])
            self.assertFalse(list(new_library.glob(".sound-capsule-move-*")))

    def test_library_location_settings_failure_rolls_back_installed_capsules(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old_library = root / "old-library"
            new_library = root / "new-library"
            preview = root / "preview.wav"
            write_silence(preview)
            capsule = self._build_capsule(old_library / "Lead.flcapsule", "Lead", preview)
            settings = Settings(
                data_dir=root / "data", library_dir=old_library, server_port=0
            )
            settings.save()

            with SoundCapsuleServer(settings) as server:
                with mock.patch.object(
                    Settings, "save", side_effect=OSError("simulated settings failure")
                ):
                    with self.assertRaisesRegex(OSError, "simulated settings failure"):
                        server.dispatch(
                            {
                                "command": "set_library_location",
                                "args": {
                                    "path": str(new_library),
                                    "move_existing": True,
                                },
                            }
                        )

                self.assertEqual(server.settings.library_dir.resolve(), old_library.resolve())

            self.assertTrue(capsule.path.exists())
            self.assertFalse((new_library / "Lead.flcapsule").exists())
            self.assertEqual(
                Settings.load(root / "data").library_dir.resolve(), old_library.resolve()
            )

    def test_library_location_reindex_failure_restores_settings_and_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old_library = root / "old-library"
            new_library = root / "new-library"
            preview = root / "preview.wav"
            write_silence(preview)
            capsule = self._build_capsule(old_library / "Lead.flcapsule", "Lead", preview)
            settings = Settings(
                data_dir=root / "data", library_dir=old_library, server_port=0
            )
            settings.save()

            with SoundCapsuleServer(settings) as server:
                with mock.patch(
                    "soundcapsule.server.CapsuleLibrary.reindex",
                    side_effect=RuntimeError("simulated reindex failure"),
                ):
                    with self.assertRaisesRegex(RuntimeError, "simulated reindex failure"):
                        server.dispatch(
                            {
                                "command": "set_library_location",
                                "args": {
                                    "path": str(new_library),
                                    "move_existing": True,
                                },
                            }
                        )

                self.assertEqual(server.settings.library_dir.resolve(), old_library.resolve())

            self.assertTrue(capsule.path.exists())
            self.assertFalse((new_library / "Lead.flcapsule").exists())
            self.assertEqual(
                Settings.load(root / "data").library_dir.resolve(), old_library.resolve()
            )

    def test_library_location_cleanup_failure_reports_source_as_not_moved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old_library = root / "old-library"
            new_library = root / "new-library"
            preview = root / "preview.wav"
            write_silence(preview)
            capsule = self._build_capsule(old_library / "Lead.flcapsule", "Lead", preview)
            settings = Settings(
                data_dir=root / "data", library_dir=old_library, server_port=0
            )
            settings.save()
            original_unlink = Path.unlink

            def fail_source_unlink(path: Path, *args, **kwargs):
                if path.resolve() == capsule.path.resolve():
                    raise OSError("simulated source cleanup failure")
                return original_unlink(path, *args, **kwargs)

            with SoundCapsuleServer(settings) as server:
                with mock.patch.object(Path, "unlink", fail_source_unlink):
                    result = server.dispatch(
                        {
                            "command": "set_library_location",
                            "args": {
                                "path": str(new_library),
                                "move_existing": True,
                            },
                        }
                    )

                self.assertEqual(server.settings.library_dir.resolve(), new_library.resolve())

            self.assertEqual(result["moved_count"], 0)
            self.assertEqual(result["not_moved_count"], 1)
            self.assertTrue(capsule.path.exists())
            self.assertTrue((new_library / "Lead.flcapsule").exists())
            self.assertEqual(
                Settings.load(root / "data").library_dir.resolve(), new_library.resolve()
            )


if __name__ == "__main__":
    unittest.main()
