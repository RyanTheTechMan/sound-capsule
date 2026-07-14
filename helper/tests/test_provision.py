from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from soundcapsule.config import Settings
from soundcapsule.provision import install_runtime, uninstall_runtime


class ProvisionTests(unittest.TestCase):
    def test_setup_installs_bridge_at_registry_folder_and_records_app(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            fl_user_folder = root / "Image-Line" / "FL Studio"
            fl_user_folder.mkdir(parents=True)
            bridge = root / "device_SoundCapsule.py"
            bridge.write_text("# controller\n", encoding="utf-8")
            app = root / "Sound Capsule.exe"
            app.write_bytes(b"app")

            with mock.patch(
                "soundcapsule.config.registered_fl_user_folder",
                return_value=fl_user_folder,
            ):
                target = install_runtime(bridge, app_path=app, data_dir=data)

            self.assertEqual(
                target,
                fl_user_folder / "Settings" / "Hardware" / "Sound Capsule"
                / "device_SoundCapsule.py",
            )
            self.assertEqual(target.read_text(encoding="utf-8"), "# controller\n")
            settings = json.loads((data / "settings.json").read_text(encoding="utf-8"))
            self.assertEqual(settings["app_path"], str(app.resolve()))
            self.assertNotIn("fl_user_folder", settings)

    def test_uninstall_removes_bridge_but_preserves_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            fl_user_folder = root / "FL Studio"
            target = (
                fl_user_folder / "Settings" / "Hardware" / "Sound Capsule"
                / "device_SoundCapsule.py"
            )
            target.parent.mkdir(parents=True)
            target.write_text("# controller\n", encoding="utf-8")
            Settings(data_dir=data).save()

            with mock.patch(
                "soundcapsule.config.registered_fl_user_folder",
                return_value=fl_user_folder,
            ):
                removed = uninstall_runtime(data_dir=data)

            self.assertEqual(removed, target)
            self.assertFalse(target.exists())
            self.assertTrue((data / "settings.json").is_file())

    def test_setup_reports_missing_fl_registry_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bridge = root / "device_SoundCapsule.py"
            bridge.write_text("# controller\n", encoding="utf-8")

            with mock.patch(
                "soundcapsule.config.registered_fl_user_folder", return_value=None
            ), self.assertRaisesRegex(RuntimeError, "Open FL Studio once"):
                install_runtime(bridge, data_dir=root / "data")


if __name__ == "__main__":
    unittest.main()
