from __future__ import annotations

from pathlib import Path
import shutil

from .config import Settings


def install_runtime(
    bridge_script: Path,
    *,
    app_path: Path | None = None,
    data_dir: Path | None = None,
) -> Path:
    """Configure the current user's runtime and refresh FL's controller script."""
    source = bridge_script.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Sound Capsule MIDI bridge payload is missing: {source}")

    settings = Settings.load(data_dir)
    if app_path is not None:
        resolved_app = app_path.expanduser().resolve()
        if not resolved_app.exists():
            raise FileNotFoundError(f"Sound Capsule application is missing: {resolved_app}")
        settings.app_path = resolved_app
    settings.save()

    target = settings.midi_bridge_path
    if target is None:
        raise RuntimeError(
            "FL Studio's user data folder was not found in Image-Line's registry data. "
            "Open FL Studio once, then restart Sound Capsule."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    try:
        shutil.copy2(source, temporary)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def uninstall_runtime(*, data_dir: Path | None = None) -> Path | None:
    """Remove the generated FL controller while preserving settings and capsules."""
    settings = Settings.load(data_dir)
    target = settings.midi_bridge_path
    if target is not None:
        target.unlink(missing_ok=True)
    return target
