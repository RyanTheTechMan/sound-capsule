from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import sys
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
    channel_count: int = 0
    channel_names: list[str] = field(default_factory=list)
    host_name: str = ""
    host_executable: str = ""
    host_pid: int = 0
    bridge_active: bool = True

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
                    "FL bridge is not connected. In FL MIDI Settings, enable the configured "
                    "Sound Capsule MIDI input and assign Sound Capsule (user), then reload the script."
                ) from last_error
            raise RuntimeError("FL bridge session could not be read; reload the Sound Capsule MIDI script") from last_error
        payload.setdefault("midi_api_version", 0)
        payload.setdefault("save_sequence", 0)
        payload.setdefault("last_save_requested_at", 0.0)
        payload.setdefault("load_sequence", 0)
        payload.setdefault("last_load_status", -1)
        payload.setdefault("last_load_at", 0.0)
        payload.setdefault("channel_count", 0)
        payload.setdefault("channel_names", [])
        payload.setdefault("host_name", "")
        payload.setdefault("host_executable", "")
        payload.setdefault("host_pid", 0)
        payload.setdefault("bridge_active", True)
        if "pattern_length_steps" not in payload:
            payload["pattern_length_steps"] = payload.pop("pattern_length_beats", 0)
        else:
            payload.pop("pattern_length_beats", None)
        payload.pop("fl_version", None)  # Migrate sessions written by 0.1.0.
        return cls(**payload)


def _windows_process_is_running(process_id: int, expected_executable: str) -> bool:
    """Check one Windows process without affecting non-Windows bridge behavior."""
    if sys.platform != "win32" or process_id <= 0:
        return False

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    process_query_limited_information = 0x1000
    synchronize = 0x00100000
    wait_timeout = 0x00000102
    handle = kernel32.OpenProcess(
        process_query_limited_information | synchronize, False, process_id
    )
    if not handle:
        return False
    try:
        if kernel32.WaitForSingleObject(handle, 0) != wait_timeout:
            return False
        if expected_executable:
            buffer = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(buffer))
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
                return False
            return os.path.normcase(os.path.abspath(buffer.value)) == os.path.normcase(
                os.path.abspath(expected_executable)
            )
        return True
    finally:
        kernel32.CloseHandle(handle)


def _project_title_from_window_caption(caption: str) -> str:
    """Extract the saved FLP filename from FL Studio's main-window caption."""
    lowered = caption.casefold()
    extension = lowered.rfind(".flp")
    if extension < 0:
        return ""
    suffix = caption[extension + 4 :].lstrip(" *")
    if not suffix.startswith("-") or "fl studio" not in suffix.casefold():
        return ""
    filename = caption[: extension + 4].strip().lstrip("*").strip()
    return filename[:-4].strip()


def _windows_project_title(process_id: int) -> str:
    """Read the project filename from the top-level window owned by one FL process."""
    if sys.platform != "win32" or process_id <= 0:
        return ""

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    enum_windows_proc = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )
    user32.EnumWindows.argtypes = [enum_windows_proc, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int

    matches: list[str] = []

    @enum_windows_proc
    def visit(window, _parameter):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(window, ctypes.byref(owner))
        if owner.value != process_id:
            return True
        length = user32.GetWindowTextLengthW(window)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        if user32.GetWindowTextW(window, buffer, len(buffer)) <= 0:
            return True
        title = _project_title_from_window_caption(buffer.value)
        if not title:
            return True
        matches.append(title)
        return False

    try:
        user32.EnumWindows(visit, 0)
    except (OSError, ValueError):
        return ""
    return matches[0] if matches else ""


class BridgeQueue:
    def __init__(self, bridge_dir: Path):
        self.bridge_dir = bridge_dir
        self.session_path = bridge_dir / "session.json"
        self.command_path = bridge_dir / "command.json"
        self.bridge_dir.mkdir(parents=True, exist_ok=True)

    def session(self, *, maximum_age: float = 10.0) -> BridgeSession:
        session = BridgeSession.read(self.session_path)
        windows_host_alive = sys.platform == "win32" and _windows_process_is_running(
            session.host_pid, session.host_executable
        )
        if not session.bridge_active:
            raise RuntimeError("FL Studio bridge is disabled; enable the Sound Capsule MIDI script")
        if time.time() - session.timestamp > maximum_age and not windows_host_alive:
            raise RuntimeError("FL Studio bridge session is stale; enable the Sound Capsule MIDI script")
        if windows_host_alive:
            # general.getProjectTitle() exposes optional project metadata, not
            # reliably the current FLP filename. The exact script-host process
            # owns a caption such as "Song.flp - FL Studio 2026", which avoids
            # mistaking a rack-similar previous project for the current one.
            window_title = _windows_project_title(session.host_pid)
            if window_title:
                session.project_title = window_title
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
