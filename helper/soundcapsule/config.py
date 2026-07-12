from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import platform


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
    return [Path.home() / "Documents" / "Image-Line" / "FL Studio" / "Projects"]


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
        return cls(**payload)
