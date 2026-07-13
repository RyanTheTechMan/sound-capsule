import json
from pathlib import Path
import tempfile
import unittest

from scripts.install import configure, record_app_path


class InstallTests(unittest.TestCase):
    def test_native_app_recording_preserves_existing_user_preferences(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "SoundCapsule"
            root.mkdir()
            (root / "settings.json").write_text(
                json.dumps({
                    "library_dir": str(root / "My Library"),
                    "check_updates_on_startup": False,
                    "undo_window_minutes": 42,
                    "midi_output_mode": "external_midi_port",
                    "midi_external_device_identifier": "obsolete-id",
                    "midi_external_device_name": "obsolete-name",
                    "midi_setup_complete": True,
                }),
                encoding="utf-8",
            )
            configure(root)
            app = Path(temporary) / "Program Files" / "Sound Capsule.exe"
            record_app_path(root, app)
            settings = json.loads((root / "settings.json").read_text(encoding="utf-8"))

            self.assertEqual(settings["app_path"], str(app))
            self.assertEqual(settings["library_dir"], str(root / "My Library"))
            self.assertFalse(settings["check_updates_on_startup"])
            self.assertEqual(settings["undo_window_minutes"], 42)
            self.assertFalse(any(key.startswith("midi_") for key in settings))


if __name__ == "__main__":
    unittest.main()
