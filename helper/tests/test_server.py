from __future__ import annotations

import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from soundcapsule.bridge import BridgeQueue
from soundcapsule.config import Settings
from soundcapsule.server import SoundCapsuleServer


class ServerTests(unittest.TestCase):
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
            self.assertEqual(payload["version"], "0.1.0")

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
                        },
                    }
                )
                runtime_minutes = server.service.settings.undo_window_minutes
                runtime_waveform = server.service.settings.waveform_channels
                runtime_destination = server.service.settings.import_destination
                runtime_volume_display = server.service.settings.volume_display
                persisted = server.dispatch({"command": "setup_status", "args": {}})

            self.assertFalse(initial["setup_complete"])
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


if __name__ == "__main__":
    unittest.main()
