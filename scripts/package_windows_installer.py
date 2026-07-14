#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile

from package_release import (
    copy_setup_payload,
    find_one,
    frozen_helper_bundle,
    project_version,
)


ROOT = Path(__file__).resolve().parents[1]


def package_windows_installer(build: Path, output: Path, version: str) -> Path:
    if version != project_version():
        raise ValueError(f"requested version {version} does not match project version {project_version()}")
    app = find_one(build, "Sound Capsule.exe", "Standalone", directory=False)
    vst3 = find_one(build, "Sound Capsule.vst3", "VST3", directory=True)
    helper = frozen_helper_bundle(build, "windows")
    output.mkdir(parents=True, exist_ok=True)
    destination = output / f"Sound-Capsule-v{version}-Windows-x64.msi"

    with tempfile.TemporaryDirectory(prefix="sound-capsule-msi-") as temporary:
        temporary_path = Path(temporary)
        setup = temporary_path / "Setup"
        wix_output = temporary_path / "wix-output"
        copy_setup_payload(setup, "windows", helper)
        subprocess.run(
            [
                "dotnet", "build", str(ROOT / "packaging" / "windows" / "SoundCapsule.wixproj"),
                "--configuration", "Release",
                f"-p:SoundCapsuleVersion={version}",
                f"-p:AppSource={app}",
                f"-p:SetupSource={setup}",
                f"-p:VstSource={vst3}",
                f"-p:OutputPath={wix_output}",
            ],
            check=True,
        )
        packages = list(wix_output.rglob("*.msi"))
        if len(packages) != 1:
            raise FileNotFoundError(f"expected one MSI under {wix_output}, found {len(packages)}")
        shutil.copy2(packages[0], destination)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the native Sound Capsule Windows MSI")
    parser.add_argument("--build", type=Path, default=ROOT / "build")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    print(package_windows_installer(args.build.resolve(), args.output.resolve(), args.version))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
