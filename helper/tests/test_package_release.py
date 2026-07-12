from pathlib import Path
import tempfile
import unittest

from scripts.package_release import find_one


class PackageReleaseTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
