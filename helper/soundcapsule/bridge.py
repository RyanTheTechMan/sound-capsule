from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time
import uuid


@dataclass(slots=True)
class BridgeSession:
    timestamp: float
    project_title: str
    midi_api_version: int
    selected_channels: list[int]
    selected_channel_names: list[str]
    current_pattern: int
    pattern_name: str
    pattern_length_steps: int
    ppq: int
    changed: int
    save_sequence: int
    last_save_requested_at: float
    load_sequence: int
    last_load_status: int
    last_load_at: float

    @classmethod
    def read(cls, path: Path) -> "BridgeSession":
        last_error: Exception | None = None
        for _ in range(5):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                break
            except (OSError, json.JSONDecodeError) as error:
                last_error = error
                time.sleep(0.01)
        else:
            if isinstance(last_error, FileNotFoundError):
                raise RuntimeError(
                    "FL bridge is not connected. In FL MIDI Settings, enable Sound Capsule Control "
                    "and assign Sound Capsule (user), then reload the script."
                ) from last_error
            raise RuntimeError("FL bridge session could not be read; reload the Sound Capsule MIDI script") from last_error
        payload.setdefault("midi_api_version", 0)
        payload.setdefault("save_sequence", 0)
        payload.setdefault("last_save_requested_at", 0.0)
        payload.setdefault("load_sequence", 0)
        payload.setdefault("last_load_status", -1)
        payload.setdefault("last_load_at", 0.0)
        if "pattern_length_steps" not in payload:
            payload["pattern_length_steps"] = payload.pop("pattern_length_beats", 0)
        else:
            payload.pop("pattern_length_beats", None)
        payload.pop("fl_version", None)  # Migrate sessions written by 0.1.0.
        return cls(**payload)


class BridgeQueue:
    def __init__(self, bridge_dir: Path):
        self.bridge_dir = bridge_dir
        self.session_path = bridge_dir / "session.json"
        self.command_path = bridge_dir / "command.json"
        self.bridge_dir.mkdir(parents=True, exist_ok=True)

    def session(self, *, maximum_age: float = 10.0) -> BridgeSession:
        session = BridgeSession.read(self.session_path)
        if time.time() - session.timestamp > maximum_age:
            raise RuntimeError("FL Studio bridge session is stale; enable the Sound Capsule MIDI script")
        return session

    def request_save(self, *, timeout: float = 30.0) -> str:
        """Atomically publish a short-lived Save request for FL's MIDI script."""
        if timeout <= 0:
            raise ValueError("save request timeout must be positive")
        now = time.time()
        request_id = uuid.uuid4().hex
        payload = {
            "request_id": request_id,
            "command": "save",
            "created_at": now,
            "expires_at": now + timeout,
        }
        temporary = self.command_path.with_name(f".{self.command_path.name}.{request_id}.tmp")
        try:
            temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
            temporary.replace(self.command_path)
        finally:
            temporary.unlink(missing_ok=True)
        return request_id
