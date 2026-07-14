#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from package_release import (
    copy_artifact,
    copy_setup_payload,
    find_one,
    frozen_helper_bundle,
    project_version,
)


ROOT = Path(__file__).resolve().parents[1]


def run(*arguments: str) -> None:
    environment = os.environ.copy()
    environment["COPYFILE_DISABLE"] = "1"
    subprocess.run(arguments, check=True, env=environment)


def package_macos_installer(
    build: Path,
    output: Path,
    version: str,
    identity: str | None,
    keychain: str | None,
) -> Path:
    if version != project_version():
        raise ValueError(f"requested version {version} does not match project version {project_version()}")
    app = find_one(build, "Sound Capsule.app", "Standalone")
    vst3 = find_one(build, "Sound Capsule.vst3", "VST3", directory=True)
    helper = frozen_helper_bundle(build, "macos")
    output.mkdir(parents=True, exist_ok=True)
    destination = output / f"Sound-Capsule-v{version}-macOS.pkg"

    with tempfile.TemporaryDirectory(prefix="sound-capsule-pkg-") as temporary:
        temporary_path = Path(temporary)
        packages = temporary_path / "packages"
        packages.mkdir()

        app_root = temporary_path / "app-root"
        copy_artifact(app, app_root / "Applications" / app.name)
        setup_root = temporary_path / "setup-root" / "Library" / "Application Support" / "SoundCapsule" / "Setup"
        copy_setup_payload(setup_root, "macos", helper)
        vst_root = temporary_path / "vst-root" / "Library" / "Audio" / "Plug-Ins" / "VST3"
        copy_artifact(vst3, vst_root / vst3.name)
        scripts = temporary_path / "pkg-scripts"
        scripts.mkdir()
        postinstall = scripts / "postinstall"
        postinstall.write_text(
            "#!/bin/sh\n"
            "helper='/Library/Application Support/SoundCapsule/Setup/Helper/Sound Capsule Helper'\n"
            "bridge='/Library/Application Support/SoundCapsule/Setup/fl-studio/SoundCapsule/device_SoundCapsule.py'\n"
            "app='/Applications/Sound Capsule.app'\n"
            "console_user=$(stat -f '%Su' /dev/console 2>/dev/null || true)\n"
            "if [ -n \"$console_user\" ] && [ \"$console_user\" != root ] && [ \"$console_user\" != loginwindow ]; then\n"
            "  console_uid=$(id -u \"$console_user\")\n"
            "  console_home=$(dscl . -read \"/Users/$console_user\" NFSHomeDirectory 2>/dev/null | sed 's/^[^ ]* //')\n"
            "  launchctl asuser \"$console_uid\" sudo -H -u \"$console_user\" env HOME=\"$console_home\" \"$helper\" setup --bridge-script \"$bridge\" --app-path \"$app\" || true\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        postinstall.chmod(0o755)

        run("pkgbuild", "--root", str(app_root), "--identifier", "com.soundcapsule.fl.pkg.app",
            "--version", version, str(packages / "app.pkg"))
        run("pkgbuild", "--root", str(setup_root.parents[3]), "--scripts", str(scripts),
            "--identifier", "com.soundcapsule.fl.pkg.setup", "--version", version,
            str(packages / "setup.pkg"))
        run("pkgbuild", "--root", str(vst_root.parents[3]), "--identifier", "com.soundcapsule.fl.pkg.vst3",
            "--version", version, str(packages / "vst3.pkg"))

        distribution = temporary_path / "Distribution.xml"
        template = (ROOT / "packaging" / "macos" / "Distribution.xml.in").read_text(encoding="utf-8")
        distribution.write_text(template.replace("@VERSION@", version), encoding="utf-8")
        command = [
            "productbuild", "--distribution", str(distribution),
            "--package-path", str(packages),
            "--resources", str(ROOT / "packaging" / "macos"),
        ]
        if identity:
            command.extend(["--sign", identity])
            if keychain:
                command.extend(["--keychain", keychain])
        command.append(str(destination))
        run(*command)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the native Sound Capsule macOS PKG")
    parser.add_argument("--build", type=Path, default=ROOT / "build")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--version", required=True)
    parser.add_argument("--identity", default=os.environ.get("MACOS_INSTALLER_IDENTITY"))
    parser.add_argument("--keychain", default=os.environ.get("MACOS_INSTALLER_KEYCHAIN"))
    args = parser.parse_args()
    package = package_macos_installer(
        args.build.resolve(), args.output.resolve(), args.version,
        args.identity, args.keychain,
    )
    print(package)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
