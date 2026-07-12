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


def render_project(project: Path, output: Path, *, fl_executable: Path | None, timeout: float = 180.0) -> Path:
    """Render a staged single-project FLP through FL Studio's CLI.

    SOUNDCAPSULE_RENDER_COMMAND can override the platform adapter. It is a shell
    template containing ``{project}`` and ``{output}`` placeholders.
    """
    override = os.environ.get("SOUNDCAPSULE_RENDER_COMMAND")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    started = time.time()
    if override:
        command: str | list[str] = override.format(project=str(project), output=str(output))
        result = subprocess.run(command, shell=True, timeout=timeout, capture_output=True, text=True)
    else:
        executable = locate_fl_studio(fl_executable)
        if executable is None:
            raise RenderError("FL Studio executable was not found; configure fl_executable")
        output_without_extension = output.with_suffix("")
        if platform.system() == "Darwin":
            command = [
                "open", "-n", "-W", str(executable), "--args",
                f"-R{output_without_extension}", "-Ewav", str(project),
            ]
        else:
            command = [str(executable), f"/R{output_without_extension}", "/Ewav", str(project)]
        result = subprocess.run(command, timeout=timeout, capture_output=True, text=True)
    if result.returncode != 0:
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
