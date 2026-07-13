from __future__ import annotations

from pathlib import Path
import platform
import subprocess
import struct


class RenderError(RuntimeError):
    pass


def close_windows_fl_studio(
    process_id: int,
    *,
    expected_executable: Path,
    timeout: float = 30.0,
) -> None:
    """Close the connected FL window cleanly and wait for its process to exit."""
    if platform.system() != "Windows" or process_id <= 0:
        raise RenderError("the connected Windows FL Studio process is unavailable")

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    process_query_limited_information = 0x1000
    synchronize = 0x00100000
    wait_object_0 = 0
    handle = kernel32.OpenProcess(
        process_query_limited_information | synchronize, False, process_id
    )
    if not handle:
        raise RenderError("the connected FL Studio process is no longer running")
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(buffer))
        if not kernel32.QueryFullProcessImageNameW(
            handle, 0, buffer, ctypes.byref(length)
        ):
            raise RenderError("could not verify the connected FL Studio process")
        if Path(buffer.value).resolve() != expected_executable.resolve():
            raise RenderError("the connected FL Studio process changed before rendering")

        enum_windows_proc = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
        )
        user32.EnumWindows.argtypes = [enum_windows_proc, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        ]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.PostMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.PostMessageW.restype = wintypes.BOOL

        windows: list[tuple[int, str]] = []

        @enum_windows_proc
        def visit(window, _parameter):
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(window, ctypes.byref(owner))
            if owner.value != process_id:
                return True
            title_length = user32.GetWindowTextLengthW(window)
            if title_length <= 0:
                return True
            title = ctypes.create_unicode_buffer(title_length + 1)
            user32.GetWindowTextW(window, title, len(title))
            if "fl studio" in title.value.casefold():
                windows.append((window, title.value))
            return True

        user32.EnumWindows(visit, 0)
        if not windows:
            raise RenderError("could not find the connected FL Studio window")
        window, _ = next(
            (candidate for candidate in windows if ".flp" in candidate[1].casefold()),
            windows[0],
        )
        if not user32.PostMessageW(window, 0x0010, 0, 0):  # WM_CLOSE
            raise RenderError("could not ask FL Studio to close")
        wait_result = kernel32.WaitForSingleObject(handle, max(1, round(timeout * 1000)))
        if wait_result != wait_object_0:
            raise RenderError(
                "FL Studio did not close safely; finish any open dialog and retry"
            )
    finally:
        kernel32.CloseHandle(handle)


def locate_fl_studio(configured: Path | None = None) -> Path | None:
    if configured and configured.exists():
        return configured
    if platform.system() == "Darwin":
        candidates = sorted(Path("/Applications").glob("FL Studio*.app"), reverse=True)
    elif platform.system() == "Windows":
        candidates = sorted(
            Path(r"C:\Program Files\Image-Line").glob(r"FL Studio*\FL64.exe"),
            reverse=True,
        )
    else:
        candidates = []
    return candidates[0] if candidates else None


def render_project(
    project: Path,
    output: Path,
    *,
    fl_executable: Path | None,
    timeout: float = 180.0,
) -> Path:
    """Render a staged single-project FLP through FL Studio's native CLI."""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    executable = locate_fl_studio(fl_executable)
    if executable is None:
        raise RenderError(
            "FL Studio executable was not found; configure fl_executable"
        )
    output_without_extension = output.with_suffix("")
    if platform.system() == "Darwin":
        command = [
            "open", "-n", "-W", str(executable), "--args",
            f"-R{output_without_extension}", "-Ewav", str(project),
        ]
    else:
        command = [
            str(executable), f"/R{output_without_extension}",
            "/Ewav", str(project),
        ]

    try:
        result = subprocess.run(
            command,
            shell=False,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired as error:
        raise RenderError(
            f"FL Studio render timed out after {timeout:g} seconds"
        ) from error
    except OSError as error:
        raise RenderError(f"FL Studio render could not start: {error}") from error
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no error details"
        raise RenderError(f"FL Studio render failed ({result.returncode}): {detail}")
    if not output.is_file():
        raise RenderError("FL Studio exited without producing the requested WAV file")
    _validate_rendered_wave(output)
    return output


def _validate_rendered_wave(path: Path) -> None:
    with path.open("rb") as handle:
        header = handle.read(12)
        if (
            len(header) != 12
            or header[:4] not in {b"RIFF", b"RF64"}
            or header[8:] != b"WAVE"
        ):
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
                    format_tag, channels, _, _, block_align, bits = struct.unpack_from(
                        "<HHIIHH", payload
                    )
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
                    sample = int.from_bytes(
                        block[offset : offset + 3], "little", signed=True
                    )
                    peak = max(peak, abs(sample) / 8388608.0)
            elif format_tag == 1 and bits == 32:
                usable = len(block) - len(block) % 4
                for (sample,) in struct.iter_unpack("<i", block[:usable]):
                    peak = max(peak, abs(sample) / 2147483648.0)
            else:
                raise RenderError(
                    f"unsupported rendered WAV format tag={format_tag}, bits={bits}"
                )
        if peak < 1.0e-5:
            raise RenderError(
                "FL Studio rendered silence; the capture was not saved"
            )
