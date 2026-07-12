#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
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


def find_one(
    build: Path,
    name: str,
    required_part: str | None = None,
    directory: bool | None = None,
) -> Path:
    matches = [
        path for path in build.rglob(name)
        if (required_part is None or required_part in path.parts)
        and (directory is None or path.is_dir() == directory)
    ]
    if len(matches) != 1:
        raise FileNotFoundError(
            f"expected one {name!r} under {build}, found {len(matches)}"
        )
    return matches[0]


def copy_file_data(source: str | Path, destination: str | Path, *, follow_symlinks: bool = True) -> str:
    """Copy bytes and mode without macOS resource-fork/provenance metadata."""
    source_path = Path(source)
    destination_path = Path(destination)
    with source_path.open("rb") as source_file, destination_path.open("wb") as destination_file:
        shutil.copyfileobj(source_file, destination_file, length=1024 * 1024)
    shutil.copymode(source_path, destination_path, follow_symlinks=follow_symlinks)
    return os.fspath(destination_path)


def copy_artifact(source: Path, destination: Path) -> None:
    if source.is_dir():
        # Do not propagate Finder/provenance xattrs into ZIP/PKG staging. The
        # code signature is stored in bundle files, not in those attributes.
        shutil.copytree(source, destination, symlinks=True, copy_function=copy_file_data)
    else:
        copy_file_data(source, destination)


def copy_setup_payload(destination: Path, platform_name: str) -> None:
    """Stage the files used by native installers and first-launch repair."""
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        ROOT / "helper",
        destination / "helper",
        copy_function=copy_file_data,
        ignore=shutil.ignore_patterns(
            "build", "dist", "tests", ".venv", "venv", "*.egg-info",
            "__pycache__", "*.pyc", ".pytest_cache"
        ),
    )
    shutil.copytree(
        ROOT / "fl-studio",
        destination / "fl-studio",
        copy_function=copy_file_data,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (destination / "scripts").mkdir()
    copy_file_data(ROOT / "scripts" / "install.py", destination / "scripts" / "install.py")
    bootstrap = (
        ROOT / "packaging" / "windows" / "bootstrap-install.ps1"
        if platform_name == "windows"
        else ROOT / "packaging" / "macos" / "bootstrap-install.sh"
    )
    copy_file_data(bootstrap, destination / bootstrap.name)


def package_release(build: Path, output: Path, version: str, platform_name: str) -> Path:
    expected_version = project_version()
    if version != expected_version:
        raise ValueError(
            f"requested version {version} does not match project version {expected_version}"
        )

    label = "macOS" if platform_name == "macos" else "Windows"
    app_name = "Sound Capsule.app" if platform_name == "macos" else "Sound Capsule.exe"
    app = find_one(build, app_name, "Standalone")
    # A Windows VST3 bundle contains an inner binary with the same .vst3 name.
    # Package the bundle directory, not that implementation binary.
    vst3 = find_one(build, "Sound Capsule.vst3", "VST3", directory=True)
    output.mkdir(parents=True, exist_ok=True)

    package_name = f"Sound-Capsule-v{version}-{label}"
    archive = output / f"{package_name}.zip"
    with tempfile.TemporaryDirectory(prefix="sound-capsule-package-") as temporary:
        package = Path(temporary) / package_name
        package.mkdir()
        copy_artifact(app, package / app.name)
        copy_artifact(vst3, package / vst3.name)

        copy_setup_payload(package, platform_name)
        shutil.copytree(ROOT / "docs", package / "docs")
        for filename in ("README.md", "CHANGELOG.md", "LICENSE", "THIRD_PARTY_NOTICES.md"):
            shutil.copy2(ROOT / filename, package / filename)

        (package / "INSTALL.txt").write_text(
            "Sound Capsule installation\n"
            "==========================\n\n"
            "1. Install uv from https://docs.astral.sh/uv/getting-started/installation/.\n"
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
