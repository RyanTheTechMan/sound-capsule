#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]


def data_dir() -> Path:
    if platform.system() == "Windows":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "SoundCapsule"
    return Path.home() / "Library" / "Application Support" / "SoundCapsule"


def vst3_destination() -> Path:
    if platform.system() == "Windows":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return root / "Programs" / "Common" / "VST3" / "Sound Capsule.vst3"
    return Path.home() / "Library" / "Audio" / "Plug-Ins" / "VST3" / "Sound Capsule.vst3"


def app_destination() -> Path:
    if platform.system() == "Windows":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return root / "Programs" / "Sound Capsule" / "Sound Capsule.exe"
    return Path.home() / "Applications" / "Sound Capsule.app"


def default_fl_user_folder() -> Path | None:
    system = platform.system()
    if system == "Windows":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Software\Image-Line\Shared\Paths"
            ) as key:
                value, _ = winreg.QueryValueEx(key, "Shared data")
            if isinstance(value, str) and value.strip():
                shared = Path(os.path.expandvars(value)).expanduser()
                candidate = (
                    shared if shared.name.casefold() == "fl studio"
                    else shared / "FL Studio"
                )
                if candidate.is_dir():
                    return candidate
        except (ImportError, OSError):
            return None
        return None
    if system == "Darwin":
        registry = Path.home() / "Library" / "Preferences" / "Image-Line" / "reg.xml"
        try:
            root = ET.parse(registry).getroot()
        except (ET.ParseError, OSError):
            return None
        key = next(
            (item for item in root.iter("Key") if item.get("Name") == "HKEY_CURRENT_USER"),
            None,
        )
        for name in ("Software", "Image-Line", "Shared", "Paths"):
            if key is None:
                return None
            key = next(
                (item for item in key.findall("Key") if item.get("Name") == name),
                None,
            )
        if key is None:
            return None
        value_node = next(
            (item for item in key.findall("Value") if item.get("Name") == "Shared data"),
            None,
        )
        value = value_node.text if value_node is not None else None
        if isinstance(value, str) and value.strip():
            shared = Path(os.path.expandvars(value.strip())).expanduser()
            candidate = (
                shared if shared.name.casefold() == "fl studio"
                else shared / "FL Studio"
            )
            return candidate if candidate.is_dir() else None
    return None


def midi_destination(fl_user_folder: Path) -> Path:
    return (
        fl_user_folder / "Settings" /
        "Hardware" / "Sound Capsule" / "device_SoundCapsule.py"
    )


def install_midi_bridge(root: Path) -> Path | None:
    source = ROOT / "fl-studio" / "SoundCapsule" / "device_SoundCapsule.py"
    template = root / "BridgeScript" / "device_SoundCapsule.py"
    template.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, template)

    user_folder = default_fl_user_folder()
    if user_folder is None or not user_folder.is_dir():
        return None
    target = midi_destination(user_folder)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(template, target)
    return target


def find_vst3(build: Path) -> Path:
    matches = list(build.rglob("Sound Capsule.vst3"))
    if not matches:
        raise FileNotFoundError("build the VST3 first: cmake -S . -B build && cmake --build build --config Release")
    return matches[0]


def find_app(build: Path) -> Path:
    pattern = "Sound Capsule.exe" if platform.system() == "Windows" else "Sound Capsule.app"
    matches = [path for path in build.rglob(pattern) if "Standalone" in path.parts]
    if not matches:
        raise FileNotFoundError(
            "build the Sound Capsule app first with -DSOUNDCAPSULE_BUILD_PLUGIN=ON"
        )
    return matches[0]


def install_helper(root: Path) -> Path:
    helper_root = root / "Helper"
    if helper_root.exists():
        shutil.rmtree(helper_root)
    shutil.copytree(
        ROOT / "helper", helper_root,
        ignore=shutil.ignore_patterns(
            "build", "dist", "tests", ".venv", "venv", "*.egg-info",
            "__pycache__", "*.pyc", ".pytest_cache",
        ),
    )
    environment = root / "venv"
    # Source/development installs reuse their running interpreter. Native
    # releases ship a frozen helper and never call this path on user machines.
    # The helper has no third-party dependencies, so pip is omitted.
    subprocess.run(
        [
            sys.executable,
            "-m",
            "venv",
            "--clear",
            "--without-pip",
            str(environment),
        ],
        check=True,
    )
    python = environment / ("Scripts/python.exe" if platform.system() == "Windows" else "bin/python")
    site_packages = Path(
        subprocess.check_output(
            [str(python), "-c", "import site; print(site.getsitepackages()[0])"],
            text=True,
        ).strip()
    )
    # The helper has no third-party runtime dependencies. A .pth file makes
    # the copied package importable without pip or a build backend download.
    (site_packages / "soundcapsule.pth").write_text(str(helper_root) + "\n", encoding="utf-8")
    return python


def remove_legacy_autostart() -> None:
    if platform.system() == "Darwin":
        label = "com.soundcapsule.helper"
        destination = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(destination)], check=False, capture_output=True)
        destination.unlink(missing_ok=True)
    elif platform.system() == "Windows":
        startup = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        (startup / "Sound Capsule Helper.cmd").unlink(missing_ok=True)


def install_cli_launcher(root: Path, python: Path) -> Path:
    if platform.system() == "Windows":
        launcher = root / "soundcapsule.cmd"
        launcher.write_text(f'@"{python}" -m soundcapsule %*\n', encoding="utf-8")
    else:
        launcher = root / "soundcapsule"
        launcher.write_text(f'#!/bin/sh\nexec "{python}" -m soundcapsule "$@"\n', encoding="utf-8")
        launcher.chmod(0o755)
    return launcher


def configure(root: Path) -> None:
    settings_path = root / "settings.json"
    existing = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    existing.pop("launch_with_fl", None)
    for key in (
        "fl_user_folder",
        "midi_output_mode",
        "midi_external_device_identifier",
        "midi_external_device_name",
        "midi_setup_complete",
    ):
        existing.pop(key, None)

    existing.update({
        "data_dir": str(root),
        "library_dir": existing.get("library_dir", str(root / "Library")),
        # Kept only as an optional legacy fallback. Current projects are found
        # from FL's MRU/save activity and cached exact paths.
        "project_roots": existing.get("project_roots", []),
        "fl_executable": existing.get("fl_executable"),
        "app_path": existing.get("app_path"),
        "setup_complete": existing.get("setup_complete", False),
        "auto_open_with_fl": existing.get("auto_open_with_fl", False),
        "setup_version": existing.get("setup_version", 0),
        "undo_window_minutes": existing.get("undo_window_minutes", 10),
        "waveform_channels": existing.get("waveform_channels", "mono"),
        "import_destination": existing.get("import_destination", "current_pattern"),
        "volume_display": existing.get("volume_display", "percent"),
        "check_updates_on_startup": existing.get("check_updates_on_startup", True),
        "server_host": "127.0.0.1",
        "server_port": 51943,
    })
    root.mkdir(parents=True, exist_ok=True)
    temporary = settings_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    temporary.replace(settings_path)


def record_app_path(root: Path, path: Path) -> None:
    settings_path = root / "settings.json"
    payload = json.loads(settings_path.read_text(encoding="utf-8"))
    payload["app_path"] = str(path)
    temporary = settings_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(settings_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Install Sound Capsule for the current user")
    parser.add_argument("--build", type=Path, default=ROOT / "build")
    parser.add_argument(
        "--installed-app", type=Path,
        help="record an app already installed by a native package instead of copying one",
    )
    parser.add_argument("--with-vst", action="store_true", help="install the optional in-FL library VST3")
    parser.add_argument("--no-app", action="store_true", help="install only the helper and FL script")
    args = parser.parse_args()
    if platform.system() not in ("Darwin", "Windows"):
        parser.error("Sound Capsule currently supports macOS and Windows")
    if args.installed_app is not None and args.no_app:
        parser.error("--installed-app and --no-app cannot be used together")

    root = data_dir()
    if sys.version_info < (3, 10):
        parser.error("run the installer with `uv run --python 3.12 scripts/install.py`")
    configure(root)
    script_target = install_midi_bridge(root)
    python = install_helper(root)
    cli_launcher = install_cli_launcher(root, python)
    installed_app = None
    if args.installed_app is not None:
        installed_app = args.installed_app.resolve()
        if not installed_app.exists():
            parser.error(f"installed app does not exist: {installed_app}")
        record_app_path(root, installed_app)
    elif not args.no_app:
        try:
            app_source = find_app(args.build)
        except FileNotFoundError:
            app_source = None
        if app_source is not None:
            installed_app = app_destination()
            installed_app.parent.mkdir(parents=True, exist_ok=True)
            if installed_app.exists():
                if installed_app.is_dir():
                    shutil.rmtree(installed_app)
                else:
                    installed_app.unlink()
            if app_source.is_dir():
                shutil.copytree(app_source, installed_app)
            else:
                shutil.copy2(app_source, installed_app)
            record_app_path(root, installed_app)
    plugin_target = None
    if args.with_vst:
        plugin_source = find_vst3(args.build)
        plugin_target = vst3_destination()
        plugin_target.parent.mkdir(parents=True, exist_ok=True)
        if plugin_target.exists():
            shutil.rmtree(plugin_target)
        shutil.copytree(plugin_source, plugin_target)
    # Older development builds installed a login helper. The standalone app
    # now owns the helper lifetime, so nothing starts at system login.
    remove_legacy_autostart()
    print(f"VST3: {plugin_target if plugin_target else 'not installed (optional)'}")
    print(
        f"MIDI bridge: {script_target}"
        if script_target is not None
        else "MIDI bridge: FL Studio user data folder was not found"
    )
    print(f"Application: {installed_app if installed_app else 'not installed'}")
    print(f"CLI: {cli_launcher}")
    print(f"Library: {root / 'Library'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
