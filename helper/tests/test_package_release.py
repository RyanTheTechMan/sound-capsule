from pathlib import Path
import tempfile
import unittest

from scripts.package_release import copy_setup_payload, find_one


ROOT = Path(__file__).resolve().parents[2]


class PackageReleaseTests(unittest.TestCase):
    def test_native_bootstraps_never_download_or_install_uv(self) -> None:
        instructions = "https://docs.astral.sh/uv/getting-started/installation/"
        for relative in (
            "packaging/macos/bootstrap-install.sh",
            "packaging/windows/bootstrap-install.ps1",
        ):
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn(instructions, source)
            self.assertNotIn("astral.sh/uv/install.", source)

    def test_windows_custom_actions_do_not_quote_a_trailing_setup_directory(self) -> None:
        source = (ROOT / "packaging/windows/Package.wxs").read_text(encoding="utf-8")
        self.assertNotIn('-SetupRoot "[SETUPFOLDER]"', source)
        self.assertEqual(source.count("-WindowStyle Hidden"), 2)
        self.assertIn('-File "[SETUPFOLDER]bootstrap-install.ps1"', source)

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
            destination = Path(temporary) / "Setup"
            copy_setup_payload(destination, "windows")

            self.assertTrue((destination / "scripts" / "install.py").is_file())
            self.assertTrue((destination / "bootstrap-install.ps1").is_file())
            self.assertTrue((destination / "helper" / "soundcapsule" / "server.py").is_file())
            self.assertTrue(
                (destination / "fl-studio" / "SoundCapsule" / "device_SoundCapsule.py").is_file()
            )
            self.assertFalse((destination / "helper" / "tests").exists())


if __name__ == "__main__":
    unittest.main()
