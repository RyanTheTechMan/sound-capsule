import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts.install import (
    configure,
    default_fl_user_folder,
    install_helper,
    install_midi_bridge,
    midi_destination,
    record_app_path,
)


class InstallTests(unittest.TestCase):
    def test_windows_default_fl_user_folder_comes_from_image_line_registry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            shared = Path(temporary) / "Custom Image-Line Data"
            expected = shared / "FL Studio"
            expected.mkdir(parents=True)
            registry = mock.MagicMock()
            registry.HKEY_CURRENT_USER = object()
            registry.QueryValueEx.return_value = (str(shared), 1)

            with mock.patch("scripts.install.platform.system", return_value="Windows"), mock.patch.dict(
                "sys.modules", {"winreg": registry}
            ):
                self.assertEqual(default_fl_user_folder(), expected)

            registry.OpenKey.assert_called_once_with(
                registry.HKEY_CURRENT_USER, r"Software\Image-Line\Shared\Paths"
            )
            registry.QueryValueEx.assert_called_once_with(
                registry.OpenKey.return_value.__enter__.return_value, "Shared data"
            )

    def test_native_app_recording_preserves_existing_user_preferences(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "SoundCapsule"
            fl_user_folder = Path(temporary) / "Custom FL Data"
            root.mkdir()
            (root / "settings.json").write_text(
                json.dumps({
                    "library_dir": str(root / "My Library"),
                    "check_updates_on_startup": False,
                    "undo_window_minutes": 42,
                    "fl_user_folder": str(fl_user_folder),
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
            self.assertNotIn("fl_user_folder", settings)
            self.assertFalse(settings["check_updates_on_startup"])
            self.assertEqual(settings["undo_window_minutes"], 42)
            self.assertFalse(any(key.startswith("midi_") for key in settings))

    def test_macos_default_fl_user_folder_comes_from_image_line_registry_xml(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / "home"
            shared = home / "Custom Image-Line Data"
            expected = shared / "FL Studio"
            expected.mkdir(parents=True)
            registry = home / "Library" / "Preferences" / "Image-Line" / "reg.xml"
            registry.parent.mkdir(parents=True)
            registry.write_text(
                """<?xml version="1.0" encoding="utf-8"?>
<XMLReg><Key Name="Key1"><Key Name="HKEY_CURRENT_USER">
<Key Name="Software"><Key Name="Image-Line"><Key Name="Shared"><Key Name="Paths">
<Value Name="FL Studio engine" Type="2">/Applications/FL Studio.app/FLEngine.dylib</Value>
<Value Name="Shared data" Type="2">{shared}</Value>
</Key></Key></Key></Key></Key></Key></XMLReg>""".format(shared=shared),
                encoding="utf-8",
            )

            with mock.patch("scripts.install.platform.system", return_value="Darwin"), mock.patch(
                "scripts.install.Path.home", return_value=home
            ):
                self.assertEqual(default_fl_user_folder(), expected)

    def test_midi_bridge_installs_into_registry_user_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "SoundCapsule"
            fl_user_folder = Path(temporary) / "Custom FL Data"
            fl_user_folder.mkdir()
            root.mkdir()

            with mock.patch(
                "scripts.install.default_fl_user_folder", return_value=fl_user_folder
            ):
                target = install_midi_bridge(root)

            self.assertEqual(target, midi_destination(fl_user_folder))
            self.assertTrue(target.is_file())
            self.assertEqual(
                target.read_bytes(),
                (root / "BridgeScript" / "device_SoundCapsule.py").read_bytes(),
            )

    def test_midi_bridge_does_not_create_a_folder_when_registry_data_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "SoundCapsule"
            missing = Path(temporary) / "Not The FL Folder"
            root.mkdir()

            with mock.patch("scripts.install.default_fl_user_folder", return_value=None):
                self.assertIsNone(install_midi_bridge(root))
            self.assertFalse(missing.exists())
            self.assertTrue((root / "BridgeScript" / "device_SoundCapsule.py").is_file())

    def test_helper_environment_installs_runtime_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "SoundCapsule"

            python = install_helper(root)

            self.assertTrue(python.is_file())
            version = subprocess.check_output(
                [
                    str(python), "-c",
                    "import send2trash, soundcapsule; print(soundcapsule.__version__)",
                ],
                text=True,
            ).strip()
            self.assertRegex(version, r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
