from pathlib import Path
import tempfile
import unittest

from scripts.package_release import copy_setup_payload, find_one


ROOT = Path(__file__).resolve().parents[2]


class PackageReleaseTests(unittest.TestCase):
    def test_windows_custom_actions_run_frozen_helper_without_powershell(self) -> None:
        source = (ROOT / "packaging/windows/Package.wxs").read_text(encoding="utf-8")
        self.assertNotIn("powershell", source.casefold())
        self.assertIn('Helper\\Sound Capsule Helper.exe" setup', source)
        self.assertIn('Helper\\Sound Capsule Helper.exe" uninstall', source)
        self.assertIn("No Python or uv installation is required", source)

    def test_find_one_can_select_vst3_bundle_over_inner_windows_binary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            build = Path(temporary)
            bundle = build / "plugin" / "Release" / "VST3" / "Sound Capsule.vst3"
            binary = bundle / "Contents" / "x86_64-win" / "Sound Capsule.vst3"
            binary.parent.mkdir(parents=True)
            binary.touch()

            self.assertEqual(
                find_one(build, "Sound Capsule.vst3", "VST3", directory=True),
                bundle,
            )

    def test_setup_payload_contains_installer_and_runtime_without_tests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "Setup"
            helper = root / "frozen-helper" / "Sound Capsule Helper"
            helper.mkdir(parents=True)
            (helper / "Sound Capsule Helper.exe").write_bytes(b"helper")
            (helper / "_internal").mkdir()
            (helper / "_internal" / "python312.dll").write_bytes(b"python")
            copy_setup_payload(destination, "windows", helper)

            self.assertTrue((destination / "Helper" / "Sound Capsule Helper.exe").is_file())
            self.assertTrue((destination / "Helper" / "_internal" / "python312.dll").is_file())
            self.assertTrue(
                (destination / "fl-studio" / "SoundCapsule" / "device_SoundCapsule.py").is_file()
            )
            self.assertFalse((destination / "Helper" / "soundcapsule").exists())
            self.assertFalse((destination / "scripts").exists())


if __name__ == "__main__":
    unittest.main()
