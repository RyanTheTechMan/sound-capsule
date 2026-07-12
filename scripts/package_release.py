#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import platform as host_platform
import re
import shutil
import tempfile


ROOT = Path(__file__).resolve().parents[1]
VERSION_PATTERN = re.compile(r'^__version__\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"$', re.MULTILINE)


def project_version() -> str:
    source = (ROOT / "helper" / "soundcapsule" / "__init__.py").read_text(encoding="utf-8")
    match = VERSION_PATTERN.search(source)
    if match is None:
        raise ValueError("could not read the Sound Capsule version")
    return match.group(1)


def find_one(build: Path, name: str, required_part: str | None = None) -> Path:
    matches = [
        path for path in build.rglob(name)
        if required_part is None or required_part in path.parts
    ]
    if len(matches) != 1:
        raise FileNotFoundError(
            f"expected one {name!r} under {build}, found {len(matches)}"
        )
    return matches[0]


def copy_artifact(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination, symlinks=True)
    else:
        shutil.copy2(source, destination)


def package_release(build: Path, output: Path, version: str, platform_name: str) -> Path:
    expected_version = project_version()
    if version != expected_version:
        raise ValueError(
            f"requested version {version} does not match project version {expected_version}"
        )

    label = "macOS" if platform_name == "macos" else "Windows"
    app_name = "Sound Capsule.app" if platform_name == "macos" else "Sound Capsule.exe"
    app = find_one(build, app_name, "Standalone")
    vst3 = find_one(build, "Sound Capsule.vst3")
    output.mkdir(parents=True, exist_ok=True)

    package_name = f"Sound-Capsule-v{version}-{label}"
    archive = output / f"{package_name}.zip"
    with tempfile.TemporaryDirectory(prefix="sound-capsule-package-") as temporary:
        package = Path(temporary) / package_name
        package.mkdir()
        copy_artifact(app, package / app.name)
        copy_artifact(vst3, package / vst3.name)

        shutil.copytree(
            ROOT / "helper",
            package / "helper",
            ignore=shutil.ignore_patterns(
                "build", "dist", "tests", ".venv", "venv", "*.egg-info",
                "__pycache__", "*.pyc", ".pytest_cache"
            ),
        )
        shutil.copytree(
            ROOT / "fl-studio",
            package / "fl-studio",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        (package / "scripts").mkdir()
        shutil.copy2(ROOT / "scripts" / "install.py", package / "scripts" / "install.py")
        for filename in ("README.md", "CHANGELOG.md", "LICENSE", "THIRD_PARTY_NOTICES.md"):
            shutil.copy2(ROOT / filename, package / filename)

        (package / "INSTALL.txt").write_text(
            "Sound Capsule installation\n"
            "==========================\n\n"
            "1. Install uv from https://docs.astral.sh/uv/.\n"
            "2. Open a terminal in this extracted folder.\n"
            "3. Run: uv run --python 3.12 scripts/install.py --build .\n\n"
            "Add --with-vst to install the optional VST3 as well. The standalone app,\n"
            "FL Studio MIDI bridge, and local helper are installed for the current user.\n"
            "See README.md for FL Studio setup and complete usage instructions.\n",
            encoding="utf-8",
        )
        archive.unlink(missing_ok=True)
        shutil.make_archive(
            str(archive.with_suffix("")), "zip", root_dir=package.parent, base_dir=package.name
        )

    return archive


def main() -> int:
    default_platform = "windows" if host_platform.system() == "Windows" else "macos"
    parser = argparse.ArgumentParser(description="Create a downloadable Sound Capsule release ZIP")
    parser.add_argument("--build", type=Path, default=ROOT / "build")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--version", required=True)
    parser.add_argument("--platform", choices=("macos", "windows"), default=default_platform)
    args = parser.parse_args()
    archive = package_release(
        args.build.resolve(), args.output.resolve(), args.version, args.platform
    )
    print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
