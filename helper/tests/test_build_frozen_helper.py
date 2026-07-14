from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts.build_frozen_helper import _copy_build_licenses, _sign_macos_frameworks


class BuildFrozenHelperTests(unittest.TestCase):
    def _pyinstaller_module(self, root: Path) -> Path:
        module = root / "site-packages" / "PyInstaller" / "__init__.py"
        module.parent.mkdir(parents=True)
        module.touch()
        license_file = (
            root
            / "site-packages"
            / "pyinstaller-6.21.0.dist-info"
            / "licenses"
            / "COPYING.txt"
        )
        license_file.parent.mkdir(parents=True)
        license_file.write_text("PyInstaller license\n", encoding="utf-8")
        return module

    def _copy_licenses(self, root: Path, base_prefix: Path, stdlib: Path) -> Path:
        bundle = root / "bundle"
        pyinstaller_module = self._pyinstaller_module(root)
        with (
            mock.patch("scripts.build_frozen_helper.sys.base_prefix", str(base_prefix)),
            mock.patch(
                "scripts.build_frozen_helper.sysconfig.get_path",
                return_value=str(stdlib),
            ),
        ):
            _copy_build_licenses(bundle, pyinstaller_module)
        return bundle / "Licenses"

    def test_copies_license_from_python_prefix_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base_prefix = root / "Python"
            base_prefix.mkdir()
            (base_prefix / "LICENSE.txt").write_text(
                "Python prefix license\n", encoding="utf-8"
            )

            licenses = self._copy_licenses(root, base_prefix, root / "stdlib")

            self.assertEqual(
                (licenses / "Python.txt").read_text(encoding="utf-8"),
                "Python prefix license\n",
            )
            self.assertEqual(
                (licenses / "PyInstaller.txt").read_text(encoding="utf-8"),
                "PyInstaller license\n",
            )

    def test_copies_license_from_macos_standard_library_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base_prefix = root / "Python.framework" / "Versions" / "3.12"
            stdlib = base_prefix / "lib" / "python3.12"
            stdlib.mkdir(parents=True)
            (stdlib / "LICENSE.txt").write_text(
                "Python framework license\n", encoding="utf-8"
            )

            licenses = self._copy_licenses(root, base_prefix, stdlib)

            self.assertEqual(
                (licenses / "Python.txt").read_text(encoding="utf-8"),
                "Python framework license\n",
            )
            self.assertEqual(
                (licenses / "PyInstaller.txt").read_text(encoding="utf-8"),
                "PyInstaller license\n",
            )

    def test_missing_python_license_still_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (
                mock.patch(
                    "scripts.build_frozen_helper.sys.base_prefix",
                    str(root / "Python"),
                ),
                mock.patch(
                    "scripts.build_frozen_helper.sysconfig.get_path",
                    return_value=str(root / "stdlib"),
                ),
            ):
                with self.assertRaisesRegex(
                    FileNotFoundError, "build Python license file was not found"
                ):
                    _copy_build_licenses(
                        root / "bundle", self._pyinstaller_module(root)
                    )

    def test_signs_reconstructed_macos_framework_as_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "Sound Capsule Helper"
            framework = bundle / "_internal" / "Python.framework"
            framework.mkdir(parents=True)

            with mock.patch("scripts.build_frozen_helper.subprocess.run") as run:
                _sign_macos_frameworks(bundle, "DEVELOPER-ID")

            self.assertEqual(run.call_count, 2)
            self.assertEqual(
                run.call_args_list[0],
                mock.call(
                    [
                        "/usr/bin/codesign",
                        "--force",
                        "--deep",
                        "--all-architectures",
                        "--sign",
                        "DEVELOPER-ID",
                        "--timestamp",
                        "--options",
                        "runtime",
                        str(framework),
                    ],
                    check=True,
                ),
            )
            self.assertEqual(
                run.call_args_list[1],
                mock.call(
                    [
                        "/usr/bin/codesign",
                        "--verify",
                        "--deep",
                        "--strict",
                        "--verbose=2",
                        str(framework),
                    ],
                    check=True,
                ),
            )


if __name__ == "__main__":
    unittest.main()
