from __future__ import annotations

import os
from pathlib import Path
import platform
import subprocess
import struct
import time


class RenderError(RuntimeError):
    pass


def locate_fl_studio(configured: Path | None = None) -> Path | None:
    if configured and configured.exists():
        return configured
    if platform.system() == "Darwin":
        candidates = sorted(Path("/Applications").glob("FL Studio*.app"), reverse=True)
    elif platform.system() == "Windows":
        candidates = sorted(Path(r"C:\Program Files\Image-Line").glob(r"FL Studio*\FL64.exe"), reverse=True)
    else:
        candidates = []
    return candidates[0] if candidates else None


def render_project(
    project: Path,
    output: Path,
    *,
    fl_executable: Path | None,
    host_pid: int | None = None,
    timeout: float = 180.0,
) -> Path:
    """Render a staged single-project FLP through the platform FL adapter.

    SOUNDCAPSULE_RENDER_COMMAND can override the platform adapter. It is a shell
    template containing ``{project}`` and ``{output}`` placeholders.
    """
    override = os.environ.get("SOUNDCAPSULE_RENDER_COMMAND")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    started = time.time()
    result: subprocess.CompletedProcess | None = None
    if override:
        command: str | list[str] = override.format(project=str(project), output=str(output))
        result = subprocess.run(command, shell=True, timeout=timeout, capture_output=True, text=True)
    else:
        executable = locate_fl_studio(fl_executable)
        if executable is None:
            raise RenderError("FL Studio executable was not found; configure fl_executable")
        output_without_extension = output.with_suffix("")
        system = platform.system()
        if system == "Darwin":
            command = [
                "open", "-n", "-W", str(executable), "--args",
                f"-R{output_without_extension}", "-Ewav", str(project),
            ]
            result = subprocess.run(command, timeout=timeout, capture_output=True, text=True)
        elif system == "Windows" and host_pid:
            _render_windows_separate_instance(
                project,
                output,
                executable=executable,
                host_pid=host_pid,
                timeout=timeout,
            )
        else:
            command = [str(executable), f"/R{output_without_extension}", "/Ewav", str(project)]
            result = subprocess.run(command, timeout=timeout, capture_output=True, text=True)
    if result is not None and result.returncode != 0:
        raise RenderError(f"FL Studio render failed ({result.returncode}): {result.stderr.strip()}")

    if not output.exists():
        candidates = sorted(
            (path for path in output.parent.glob("*.wav") if path.stat().st_mtime >= started - 1.0),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        if not candidates:
            raise RenderError("FL Studio exited without producing a WAV file")
        candidates[0].replace(output)
    _validate_rendered_wave(output)
    return output


def _render_windows_separate_instance(
    project: Path,
    output: Path,
    *,
    executable: Path,
    host_pid: int,
    timeout: float,
) -> None:
    """Render through a second interactive FL process without closing the host.

    FL Studio 2026 exits its Windows ``/R`` command-line process when another
    interactive instance is already running. A normal second instance is
    supported, however, and its export UI can be driven without sending input
    to the connected project. All window discovery is constrained to the PID
    created here; ``host_pid`` is used only to restore the original window.
    """
    ui = _WindowsFlUi()
    deadline = time.monotonic() + timeout
    # Passing the FLP on FL Studio's Windows command line forwards it into an
    # already-running process. Start a blank process first, then target that
    # PID's own Open dialog so the connected project can never be replaced.
    process = subprocess.Popen([str(executable)])
    renderer_window = 0
    try:
        if process.pid == host_pid:
            raise RenderError("FL Studio did not create a separate preview-rendering process")
        renderer_window = ui.wait_for_main_window(process, deadline=deadline)
        ui.wait_for_ui_settle(process, deadline=deadline, duration=1.5)
        ui.send_hotkey(renderer_window, 0x11, ord("O"))  # Ctrl+O
        open_dialog, filename_edit, open_button = ui.wait_for_file_dialog(
            process, filename_control_id=1148, deadline=deadline
        )
        ui.set_text(filename_edit, str(project))
        ui.click_button(open_button)
        ui.wait_until_closed(open_dialog, process, deadline=deadline)
        renderer_window = ui.wait_for_project_window(
            process, project.name, deadline=deadline
        )
        ui.wait_for_ui_settle(process, deadline=deadline, duration=0.5)
        ui.send_hotkey(renderer_window, 0x11, ord("R"))  # Ctrl+R

        save_dialog, filename_edit, save_button = ui.wait_for_file_dialog(
            process, filename_control_id=1001, deadline=deadline
        )
        ui.set_text(filename_edit, str(output))
        ui.click_button(save_button)
        ui.wait_until_closed(save_dialog, process, deadline=deadline)

        render_dialog = ui.wait_for_render_dialog(
            process,
            renderer_window,
            output.name,
            deadline=deadline,
        )
        ui.send_key(render_dialog, 0x0D)  # Enter activates FL's default Start button.
        ui.wait_for_render_complete(
            process,
            output,
            renderer_window=renderer_window,
            deadline=deadline,
        )
    finally:
        ui.close_process_window(process, renderer_window)
        ui.restore_process_window(host_pid)


class _WindowsFlUi:
    """Small Win32 adapter used only by the Windows standalone helper."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.wintypes = wintypes
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.enum_proc_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
        )

        self.user32.EnumWindows.argtypes = [self.enum_proc_type, wintypes.LPARAM]
        self.user32.EnumWindows.restype = wintypes.BOOL
        self.user32.EnumChildWindows.argtypes = [
            wintypes.HWND, self.enum_proc_type, wintypes.LPARAM
        ]
        self.user32.EnumChildWindows.restype = wintypes.BOOL
        self.user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
        ]
        self.user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self.user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        self.user32.GetWindowTextLengthW.restype = ctypes.c_int
        self.user32.GetWindowTextW.argtypes = [
            wintypes.HWND, wintypes.LPWSTR, ctypes.c_int
        ]
        self.user32.GetWindowTextW.restype = ctypes.c_int
        self.user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        self.user32.GetWindow.restype = wintypes.HWND
        self.user32.GetDlgCtrlID.argtypes = [wintypes.HWND]
        self.user32.GetDlgCtrlID.restype = ctypes.c_int
        self.user32.IsWindow.argtypes = [wintypes.HWND]
        self.user32.IsWindow.restype = wintypes.BOOL
        self.user32.IsWindowVisible.argtypes = [wintypes.HWND]
        self.user32.IsWindowVisible.restype = wintypes.BOOL
        self.user32.IsIconic.argtypes = [wintypes.HWND]
        self.user32.IsIconic.restype = wintypes.BOOL
        self.user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.ShowWindow.restype = wintypes.BOOL
        self.user32.BringWindowToTop.argtypes = [wintypes.HWND]
        self.user32.BringWindowToTop.restype = wintypes.BOOL
        self.user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        self.user32.SetForegroundWindow.restype = wintypes.BOOL
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.GetLastActivePopup.argtypes = [wintypes.HWND]
        self.user32.GetLastActivePopup.restype = wintypes.HWND
        self.user32.SendMessageW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        ]
        self.user32.SendMessageW.restype = ctypes.c_ssize_t
        self.user32.PostMessageW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        ]
        self.user32.PostMessageW.restype = wintypes.BOOL
        self.kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        self.user32.AttachThreadInput.argtypes = [
            wintypes.DWORD, wintypes.DWORD, wintypes.BOOL
        ]
        self.user32.AttachThreadInput.restype = wintypes.BOOL

        class KeybdInput(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", wintypes.WPARAM),
            ]

        class MouseInput(ctypes.Structure):
            _fields_ = [
                ("dx", wintypes.LONG),
                ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", wintypes.WPARAM),
            ]

        class HardwareInput(ctypes.Structure):
            _fields_ = [
                ("uMsg", wintypes.DWORD),
                ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD),
            ]

        class InputUnion(ctypes.Union):
            _fields_ = [
                ("mi", MouseInput),
                ("ki", KeybdInput),
                ("hi", HardwareInput),
            ]

        class Input(ctypes.Structure):
            _anonymous_ = ("value",)
            _fields_ = [("type", wintypes.DWORD), ("value", InputUnion)]

        self.KeybdInput = KeybdInput
        self.InputUnion = InputUnion
        self.Input = Input
        self.user32.SendInput.argtypes = [
            wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int
        ]
        self.user32.SendInput.restype = wintypes.UINT

    def _window_text(self, window: int) -> str:
        length = self.user32.GetWindowTextLengthW(window)
        if length <= 0:
            return ""
        buffer = self.ctypes.create_unicode_buffer(length + 1)
        self.user32.GetWindowTextW(window, buffer, len(buffer))
        return buffer.value

    def _process_windows(self, process_id: int) -> list[int]:
        matches: list[int] = []

        @self.enum_proc_type
        def collect(window, _parameter):
            owner = self.wintypes.DWORD()
            self.user32.GetWindowThreadProcessId(window, self.ctypes.byref(owner))
            if owner.value == process_id and self.user32.IsWindowVisible(window):
                matches.append(int(window))
            return True

        self.user32.EnumWindows(collect, 0)
        return matches

    def _main_window(self, process_id: int, title_fragment: str = "") -> int:
        fragment = title_fragment.casefold()
        for window in self._process_windows(process_id):
            if self.user32.GetWindow(window, 4):  # GW_OWNER
                continue
            title = self._window_text(window)
            if "fl studio" not in title.casefold():
                continue
            if fragment and fragment not in title.casefold():
                continue
            return window
        return 0

    @staticmethod
    def _check_process(process: subprocess.Popen) -> None:
        code = process.poll()
        if code is not None:
            raise RenderError(
                f"the separate FL Studio rendering instance exited unexpectedly ({code})"
            )

    def wait_for_project_window(
        self,
        process: subprocess.Popen,
        project_name: str,
        *,
        deadline: float,
    ) -> int:
        while time.monotonic() < deadline:
            self._check_process(process)
            window = self._main_window(process.pid, project_name)
            if window:
                return window
            time.sleep(0.1)
        raise RenderError("the separate FL Studio instance did not load the preview project")

    def wait_for_main_window(
        self, process: subprocess.Popen, *, deadline: float
    ) -> int:
        while time.monotonic() < deadline:
            self._check_process(process)
            window = self._main_window(process.pid)
            if window:
                return window
            time.sleep(0.1)
        raise RenderError("the separate FL Studio rendering instance did not open")

    def wait_for_ui_settle(
        self,
        process: subprocess.Popen,
        *,
        deadline: float,
        duration: float,
    ) -> None:
        """Let FL finish installing accelerators after its window first appears."""
        end = min(deadline, time.monotonic() + duration)
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                return
            self._check_process(process)
            time.sleep(min(0.05, remaining))

    def _child_with_id(self, parent: int, control_id: int) -> int:
        match = 0

        @self.enum_proc_type
        def collect(window, _parameter):
            nonlocal match
            if self.user32.GetDlgCtrlID(window) == control_id:
                match = int(window)
                return False
            return True

        self.user32.EnumChildWindows(parent, collect, 0)
        return match

    def wait_for_file_dialog(
        self,
        process: subprocess.Popen,
        *,
        filename_control_id: int,
        deadline: float,
    ) -> tuple[int, int, int]:
        while time.monotonic() < deadline:
            self._check_process(process)
            for window in self._process_windows(process.pid):
                filename_edit = self._child_with_id(window, filename_control_id)
                action_button = self._child_with_id(window, 1)
                if filename_edit and action_button:
                    return window, filename_edit, action_button
            time.sleep(0.05)
        raise RenderError("FL Studio did not open the expected project or WAV dialog")

    def wait_until_closed(
        self, window: int, process: subprocess.Popen, *, deadline: float
    ) -> None:
        while time.monotonic() < deadline:
            self._check_process(process)
            if not self.user32.IsWindow(window) or not self.user32.IsWindowVisible(window):
                return
            time.sleep(0.05)
        raise RenderError("FL Studio did not accept the selected project or WAV destination")

    def wait_for_render_dialog(
        self,
        process: subprocess.Popen,
        renderer_window: int,
        output_name: str,
        *,
        deadline: float,
    ) -> int:
        expected = output_name.casefold()
        while time.monotonic() < deadline:
            self._check_process(process)
            for window in self._process_windows(process.pid):
                if window != renderer_window and expected in self._window_text(window).casefold():
                    return window
            popup = int(self.user32.GetLastActivePopup(renderer_window))
            if popup and popup != renderer_window and self.user32.IsWindowVisible(popup):
                return popup
            time.sleep(0.05)
        raise RenderError("FL Studio did not open its preview rendering dialog")

    def _activate(self, window: int) -> None:
        if self.user32.IsIconic(window):
            self.user32.ShowWindow(window, 9)  # SW_RESTORE
        foreground = self.user32.GetForegroundWindow()
        current_thread = self.kernel32.GetCurrentThreadId()
        foreground_thread = (
            self.user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
        )
        attached = bool(
            foreground_thread
            and foreground_thread != current_thread
            and self.user32.AttachThreadInput(current_thread, foreground_thread, True)
        )
        try:
            self.user32.BringWindowToTop(window)
            self.user32.SetForegroundWindow(window)
        finally:
            if attached:
                self.user32.AttachThreadInput(current_thread, foreground_thread, False)
        if self.user32.GetForegroundWindow() != window:
            raise RenderError("Windows could not focus the separate FL Studio rendering instance")

    def _send_keys(self, window: int, keys: list[tuple[int, bool]]) -> None:
        self._activate(window)
        inputs = (self.Input * len(keys))()
        for index, (key, released) in enumerate(keys):
            flags = 0x0002 if released else 0
            inputs[index].type = 1  # INPUT_KEYBOARD
            inputs[index].ki = self.KeybdInput(key, 0, flags, 0, 0)
        sent = self.user32.SendInput(
            len(inputs), inputs, self.ctypes.sizeof(self.Input)
        )
        if sent != len(inputs):
            raise RenderError("Windows could not control the separate FL Studio rendering instance")

    def send_hotkey(self, window: int, modifier: int, key: int) -> None:
        self._send_keys(
            window,
            [(modifier, False), (key, False), (key, True), (modifier, True)],
        )

    def send_key(self, window: int, key: int) -> None:
        self._send_keys(window, [(key, False), (key, True)])

    def set_text(self, control: int, value: str) -> None:
        wm_settext = 0x000C
        buffer = self.ctypes.create_unicode_buffer(value)
        result = self.user32.SendMessageW(
            control,
            wm_settext,
            0,
            self.ctypes.addressof(buffer),
        )
        if not result:
            raise RenderError("Windows could not set the preview WAV destination")

    def click_button(self, control: int) -> None:
        self.user32.SendMessageW(control, 0x00F5, 0, 0)  # BM_CLICK

    def wait_for_render_complete(
        self,
        process: subprocess.Popen,
        output: Path,
        *,
        renderer_window: int,
        deadline: float,
    ) -> None:
        previous_size = -1
        stable_since = 0.0
        while time.monotonic() < deadline:
            self._check_process(process)
            try:
                size = output.stat().st_size
            except OSError:
                size = -1
            popup = int(self.user32.GetLastActivePopup(renderer_window))
            dialog_open = bool(
                popup
                and popup != renderer_window
                and self.user32.IsWindowVisible(popup)
            )
            if size > 44 and size == previous_size:
                if stable_since == 0.0:
                    stable_since = time.monotonic()
                elif not dialog_open and time.monotonic() - stable_since >= 0.5:
                    return
            else:
                previous_size = size
                stable_since = 0.0
            time.sleep(0.1)
        raise RenderError("the separate FL Studio instance did not finish rendering the preview")

    def close_process_window(
        self, process: subprocess.Popen, preferred_window: int
    ) -> None:
        if process.poll() is not None:
            return
        windows = self._process_windows(process.pid)
        window = preferred_window if preferred_window in windows else self._main_window(process.pid)
        if window:
            self.user32.PostMessageW(window, 0x0010, 0, 0)  # WM_CLOSE
            try:
                process.wait(timeout=10.0)
                return
            except subprocess.TimeoutExpired:
                pass
        process.terminate()
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5.0)

    def restore_process_window(self, process_id: int) -> None:
        window = self._main_window(process_id)
        if not window:
            return
        if self.user32.IsIconic(window):
            self.user32.ShowWindow(window, 9)  # SW_RESTORE
        self.user32.SetForegroundWindow(window)


def _validate_rendered_wave(path: Path) -> None:
    with path.open("rb") as handle:
        header = handle.read(12)
        if len(header) != 12 or header[:4] not in {b"RIFF", b"RF64"} or header[8:] != b"WAVE":
            raise RenderError("FL Studio output is not a WAVE file")
        format_tag = channels = block_align = bits = 0
        data_offset = data_size = 0
        while chunk_header := handle.read(8):
            if len(chunk_header) != 8:
                break
            chunk_id, chunk_size = struct.unpack("<4sI", chunk_header)
            chunk_start = handle.tell()
            if chunk_id == b"fmt ":
                payload = handle.read(min(chunk_size, 64))
                if len(payload) >= 16:
                    format_tag, channels, _, _, block_align, bits = struct.unpack_from("<HHIIHH", payload)
                    if format_tag == 0xFFFE and len(payload) >= 26:
                        format_tag = struct.unpack_from("<H", payload, 24)[0]
            elif chunk_id == b"data":
                data_offset, data_size = handle.tell(), chunk_size
                break
            handle.seek(chunk_start + chunk_size + (chunk_size & 1))

        if not data_offset or not data_size or channels <= 0 or block_align <= 0:
            raise RenderError("FL Studio produced an empty or malformed WAV file")
        if data_size // block_align <= 0:
            raise RenderError("FL Studio produced a zero-length WAV file")
        handle.seek(data_offset)
        peak = 0.0
        remaining = data_size
        read_size = 1024 * 1024 - (1 if bits == 24 else 0)
        while remaining > 0 and peak < 1.0e-5:
            block = handle.read(min(read_size, remaining))
            if not block:
                break
            remaining -= len(block)
            if format_tag == 3 and bits == 32:
                usable = len(block) - len(block) % 4
                for (sample,) in struct.iter_unpack("<f", block[:usable]):
                    peak = max(peak, abs(sample))
            elif format_tag == 1 and bits == 16:
                usable = len(block) - len(block) % 2
                for (sample,) in struct.iter_unpack("<h", block[:usable]):
                    peak = max(peak, abs(sample) / 32768.0)
            elif format_tag == 1 and bits == 24:
                usable = len(block) - len(block) % 3
                for offset in range(0, usable, 3):
                    sample = int.from_bytes(block[offset : offset + 3], "little", signed=True)
                    peak = max(peak, abs(sample) / 8388608.0)
            elif format_tag == 1 and bits == 32:
                usable = len(block) - len(block) % 4
                for (sample,) in struct.iter_unpack("<i", block[:usable]):
                    peak = max(peak, abs(sample) / 2147483648.0)
            else:
                raise RenderError(f"unsupported rendered WAV format tag={format_tag}, bits={bits}")
        if peak < 1.0e-5:
            raise RenderError("FL Studio rendered silence; the capsule was left as a retryable draft")
