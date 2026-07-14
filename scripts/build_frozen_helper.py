#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
HELPER_NAME = "Sound Capsule Helper"


def helper_executable(bundle: Path, system: str | None = None) -> Path:
    host = system or platform.system()
    return bundle / (f"{HELPER_NAME}.exe" if host == "Windows" else HELPER_NAME)


def _copy_build_licenses(bundle: Path, pyinstaller_module: Path) -> None:
    licenses = bundle / "Licenses"
    licenses.mkdir(parents=True, exist_ok=True)
    python_license = next(
        (
            path for path in (
                Path(sys.base_prefix) / "LICENSE.txt",
                Path(sys.base_prefix) / "LICENSE",
            )
            if path.is_file()
        ),
        None,
    )
    if python_license is None:
        raise FileNotFoundError("the build Python license file was not found")
    pyinstaller_license = next(
        (
            path for path in (
                pyinstaller_module.parent / "COPYING.txt",
                pyinstaller_module.parent / "COPYING",
                *pyinstaller_module.parent.parent.glob(
                    "pyinstaller-*.dist-info/licenses/COPYING.txt"
                ),
            )
            if path.is_file()
        ),
        None,
    )
    if pyinstaller_license is None:
        raise FileNotFoundError("the PyInstaller license file was not found")
    shutil.copy2(python_license, licenses / "Python.txt")
    shutil.copy2(pyinstaller_license, licenses / "PyInstaller.txt")


def build_frozen_helper(
    output: Path,
    *,
    target_architecture: str | None = None,
    codesign_identity: str | None = None,
) -> Path:
    try:
        import PyInstaller
        import PyInstaller.__main__
    except ImportError as error:
        raise RuntimeError(
            "PyInstaller is a build-only dependency; install PyInstaller 6.21.0"
        ) from error

    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sound-capsule-helper-build-") as temporary:
        temporary_path = Path(temporary)
        arguments = [
            "--noconfirm",
            "--clean",
            "--onedir",
            "--name", HELPER_NAME,
            "--paths", str(ROOT / "helper"),
            "--distpath", str(output),
            "--workpath", str(temporary_path / "work"),
            "--specpath", str(temporary_path / "spec"),
        ]
        if platform.system() == "Windows":
            arguments.append("--noconsole")
        elif platform.system() == "Darwin":
            arguments.extend([
                "--console",
                "--target-architecture", target_architecture or "universal2",
            ])
            if codesign_identity:
                arguments.extend(["--codesign-identity", codesign_identity])
        arguments.append(str(ROOT / "scripts" / "frozen_helper.py"))
        PyInstaller.__main__.run(arguments)

    bundle = output / HELPER_NAME
    executable = helper_executable(bundle)
    if not executable.is_file():
        raise FileNotFoundError(f"PyInstaller did not create {executable}")
    _copy_build_licenses(bundle, Path(PyInstaller.__file__).resolve())
    return bundle


def smoke_test(bundle: Path) -> None:
    executable = helper_executable(bundle)
    with tempfile.TemporaryDirectory(prefix="sound-capsule-helper-smoke-") as temporary:
        subprocess.run(
            [str(executable), "--home", temporary, "list"],
            check=True,
            timeout=60,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the self-contained Sound Capsule helper")
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "frozen-helper")
    parser.add_argument(
        "--target-architecture",
        choices=("x86_64", "arm64", "universal2"),
        help="macOS helper architecture (defaults to universal2)",
    )
    parser.add_argument(
        "--codesign-identity",
        help="macOS code-signing identity passed to PyInstaller",
    )
    args = parser.parse_args()
    bundle = build_frozen_helper(
        args.output,
        target_architecture=args.target_architecture,
        codesign_identity=args.codesign_identity,
    )
    smoke_test(bundle)
    print(bundle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
