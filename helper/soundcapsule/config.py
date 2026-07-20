from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import platform
import xml.etree.ElementTree as ET


APP_DIR_NAME = "SoundCapsule"


def default_data_dir() -> Path:
    override = os.environ.get("SOUNDCAPSULE_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if platform.system() == "Windows":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        root = Path.home() / "Library" / "Application Support"
    return root / APP_DIR_NAME


def default_project_roots() -> list[Path]:
    # Optional CLI overrides only. FL Studio's own Projects root is added
    # dynamically from registry data by Settings.fl_project_roots.
    return []


def registered_fl_user_folder() -> Path | None:
    system = platform.system()
    if system == "Windows":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Software\Image-Line\Shared\Paths"
            ) as key:
                value, _ = winreg.QueryValueEx(key, "Shared data")
        except (ImportError, OSError):
            return None
    elif system == "Darwin":
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
    else:
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    shared = Path(os.path.expandvars(value.strip())).expanduser()
    candidate = shared if shared.name.casefold() == "fl studio" else shared / "FL Studio"
    return candidate if candidate.is_dir() else None


def default_fl_user_folder() -> Path | None:
    # FL Studio owns this setting. Do not guess a Documents path because that
    # can silently install the bridge into an unused data tree.
    return registered_fl_user_folder()


@dataclass(slots=True)
class Settings:
    data_dir: Path = field(default_factory=default_data_dir)
    library_dir: Path | None = None
    project_roots: list[Path] = field(default_factory=default_project_roots)
    fl_executable: Path | None = None
    app_path: Path | None = None
    setup_complete: bool = False
    auto_open_with_fl: bool = False
    setup_version: int = 0
    undo_window_minutes: int = 10
    waveform_channels: str = "mono"
    import_destination: str = "current_pattern"
    volume_display: str = "percent"
    start_preview_at_first_audio: bool = True
    normalize_waveform_display: bool = False
    show_automation_curves: bool = True
    show_single_channel_name_in_rename: bool = False
    check_updates_on_startup: bool = True
    server_host: str = "127.0.0.1"
    server_port: int = 51943

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir).expanduser()
        self.library_dir = Path(self.library_dir).expanduser() if self.library_dir else self.data_dir / "Library"
        self.project_roots = [Path(path).expanduser() for path in self.project_roots]
        self.fl_executable = Path(self.fl_executable).expanduser() if self.fl_executable else None
        self.app_path = Path(self.app_path).expanduser() if self.app_path else None
        self.undo_window_minutes = int(self.undo_window_minutes)
        if not 1 <= self.undo_window_minutes <= 1440:
            raise ValueError("undo_window_minutes must be between 1 and 1440")
        if self.waveform_channels not in ("mono", "stereo"):
            raise ValueError("waveform_channels must be 'mono' or 'stereo'")
        if self.import_destination not in (
            "current_pattern", "new_pattern", "override_selection"
        ):
            raise ValueError(
                "import_destination must be 'current_pattern', 'new_pattern', or 'override_selection'"
            )
        if self.volume_display not in ("percent", "db"):
            raise ValueError("volume_display must be 'percent' or 'db'")

    @property
    def bridge_dir(self) -> Path:
        return self.data_dir / "Bridge"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "Cache"

    @property
    def staging_dir(self) -> Path:
        return self.data_dir / "Staging"

    @property
    def fl_user_folder(self) -> Path | None:
        """Return FL Studio's current user-data folder without persisting a copy."""
        return default_fl_user_folder()

    @property
    def midi_bridge_path(self) -> Path | None:
        user_folder = self.fl_user_folder
        if user_folder is None:
            return None
        return (
            user_folder / "Settings" / "Hardware" / "Sound Capsule"
            / "device_SoundCapsule.py"
        )

    @property
    def bridge_script_template(self) -> Path:
        return self.data_dir / "BridgeScript" / "device_SoundCapsule.py"

    @property
    def fl_project_roots(self) -> list[Path]:
        user_folder = self.fl_user_folder
        roots = [*([user_folder / "Projects"] if user_folder else []), *self.project_roots]
        return list(dict.fromkeys(roots))

    @property
    def config_path(self) -> Path:
        return self.data_dir / "settings.json"

    def ensure(self) -> None:
        for path in (self.data_dir, self.library_dir, self.bridge_dir, self.cache_dir, self.staging_dir):
            path.mkdir(parents=True, exist_ok=True)

    def save(self) -> None:
        self.ensure()
        payload = asdict(self)
        for key in ("data_dir", "library_dir", "fl_executable", "app_path"):
            payload[key] = str(payload[key]) if payload[key] else None
        payload["project_roots"] = [str(path) for path in self.project_roots]
        temporary = self.config_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary.replace(self.config_path)

    @classmethod
    def load(cls, data_dir: Path | None = None) -> "Settings":
        root = data_dir or default_data_dir()
        path = root / "settings.json"
        if not path.exists():
            settings = cls(data_dir=root)
            settings.ensure()
            return settings
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.pop("launch_with_fl", None)  # Removed: FL External Tools owns this preference.
        payload.pop("fl_user_folder", None)  # FL Studio's registry is the source of truth.
        for key in (
            "midi_output_mode",
            "midi_external_device_identifier",
            "midi_external_device_name",
            "midi_setup_complete",
        ):
            payload.pop(key, None)
        return cls(**payload)
