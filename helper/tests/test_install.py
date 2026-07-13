import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

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

    def test_existing_completed_windows_setup_migrates_to_legacy_external_port(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, mock.patch(
            "scripts.install.platform.system", return_value="Windows"
        ), mock.patch.dict("os.environ", {}, clear=False):
            root = Path(temporary) / "SoundCapsule"
            root.mkdir()
            (root / "settings.json").write_text(
                json.dumps({"setup_complete": True}), encoding="utf-8"
            )
            configure(root)
            settings = json.loads((root / "settings.json").read_text(encoding="utf-8"))

            self.assertEqual(settings["midi_output_mode"], "external_midi_port")
            self.assertEqual(settings["midi_external_device_name"], "Sound Capsule Control")
            self.assertIsNone(settings["midi_external_device_identifier"])
            self.assertTrue(settings["midi_setup_complete"])

    def test_windows_midi_environment_override_migrates_even_when_port_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, mock.patch(
            "scripts.install.platform.system", return_value="Windows"
        ), mock.patch.dict(
            "os.environ", {"SOUNDCAPSULE_MIDI_OUTPUT": "Studio Cable"}, clear=False
        ):
            root = Path(temporary) / "SoundCapsule"
            configure(root)
            settings = json.loads((root / "settings.json").read_text(encoding="utf-8"))

            self.assertEqual(settings["midi_output_mode"], "external_midi_port")
            self.assertEqual(settings["midi_external_device_name"], "Studio Cable")
            self.assertTrue(settings["midi_setup_complete"])


if __name__ == "__main__":
    unittest.main()
