from __future__ import annotations

import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from soundcapsule import __version__
from soundcapsule.bridge import BridgeQueue, BridgeSession
from soundcapsule.capsule import Capsule
from soundcapsule.config import Settings
from soundcapsule.server import SoundCapsuleServer
from test_flp import fixture_project, write_silence


class ServerTests(unittest.TestCase):
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

    def test_missing_bridge_has_actionable_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(RuntimeError, "enable Sound Capsule Control"):
                BridgeQueue(Path(temporary)).session()

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
                server._update_import_progress(
                    "operation-1", 72, "Writing and validating the updated project"
                )
                payload = server.dispatch(
                    {
                        "command": "import_status",
                        "args": {"operation_id": "operation-1"},
                    }
                )

            self.assertTrue(payload["active"])
            self.assertEqual(payload["progress"], 72)
            self.assertEqual(payload["operation_id"], "operation-1")
            self.assertIn("validating", payload["step"])

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
                            "check_updates_on_startup": False,
                        },
                    }
                )
                runtime_minutes = server.service.settings.undo_window_minutes
                runtime_waveform = server.service.settings.waveform_channels
                runtime_destination = server.service.settings.import_destination
                runtime_volume_display = server.service.settings.volume_display
                runtime_update_check = server.service.settings.check_updates_on_startup
                persisted = server.dispatch({"command": "setup_status", "args": {}})

            self.assertFalse(initial["setup_complete"])
            self.assertTrue(initial["check_updates_on_startup"])
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
