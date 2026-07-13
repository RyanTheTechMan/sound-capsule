from __future__ import annotations

import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from soundcapsule.renderer import RenderError, render_project


def write_audible_wave(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(44100)
        output.writeframes((1000).to_bytes(2, "little", signed=True) * 128)


class RendererTests(unittest.TestCase):
    def test_windows_uses_native_command_line_renderer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "FL64.exe"
            project = root / "preview.flp"
            output = root / "preview.wav"
            executable.write_bytes(b"")
            project.write_bytes(b"FLhd")

            def fake_run(command, **_kwargs):
                write_audible_wave(output)
                return subprocess.CompletedProcess(command, 0, "", "")

            with mock.patch("platform.system", return_value="Windows"), mock.patch(
                "soundcapsule.renderer.subprocess.run", side_effect=fake_run
            ) as launch:
                result = render_project(
                    project,
                    output,
                    fl_executable=executable,
                    timeout=12.0,
                )

            self.assertEqual(result, output)
            self.assertEqual(
                launch.call_args.args[0],
                [str(executable), f"/R{output.with_suffix('')}", "/Ewav", str(project)],
            )
            self.assertFalse(launch.call_args.kwargs["shell"])
            self.assertEqual(launch.call_args.kwargs["timeout"], 12.0)

    def test_macos_still_forces_a_new_application_instance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            application = root / "FL Studio 2026.app"
            application.mkdir()
            project = root / "preview.flp"
            output = root / "preview.wav"
            project.write_bytes(b"FLhd")

            def fake_run(command, **_kwargs):
                write_audible_wave(output)
                return subprocess.CompletedProcess(command, 0, "", "")

            with mock.patch("platform.system", return_value="Darwin"), mock.patch(
                "soundcapsule.renderer.subprocess.run", side_effect=fake_run
            ) as launch:
                result = render_project(
                    project,
                    output,
                    fl_executable=application,
                )

            self.assertEqual(result, output)
            command = launch.call_args.args[0]
            self.assertEqual(command[:4], ["open", "-n", "-W", str(application)])
            self.assertIn(str(project), command)

    def test_timeout_is_reported_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "FL64.exe"
            executable.write_bytes(b"")
            with mock.patch("platform.system", return_value="Windows"), mock.patch(
                "soundcapsule.renderer.subprocess.run",
                side_effect=subprocess.TimeoutExpired("FL64.exe", 3),
            ):
                with self.assertRaisesRegex(RenderError, "timed out after 3 seconds"):
                    render_project(
                        root / "preview.flp",
                        root / "preview.wav",
                        fl_executable=executable,
                        timeout=3,
                    )

    def test_nonzero_exit_is_reported_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "FL64.exe"
            executable.write_bytes(b"")
            completed = subprocess.CompletedProcess([], 7, "", "render rejected")
            with mock.patch("platform.system", return_value="Windows"), mock.patch(
                "soundcapsule.renderer.subprocess.run", return_value=completed
            ):
                with self.assertRaisesRegex(RenderError, r"failed \(7\).*render rejected"):
                    render_project(
                        root / "preview.flp",
                        root / "preview.wav",
                        fl_executable=executable,
                    )

    def test_missing_output_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "FL64.exe"
            executable.write_bytes(b"")
            completed = subprocess.CompletedProcess([], 0, "", "")
            with mock.patch("platform.system", return_value="Windows"), mock.patch(
                "soundcapsule.renderer.subprocess.run", return_value=completed
            ):
                with self.assertRaisesRegex(RenderError, "requested WAV"):
                    render_project(
                        root / "preview.flp",
                        root / "preview.wav",
                        fl_executable=executable,
                    )

    def test_malformed_or_silent_output_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "FL64.exe"
            output = root / "preview.wav"
            executable.write_bytes(b"")

            def malformed(command, **_kwargs):
                output.write_bytes(b"not a wave")
                return subprocess.CompletedProcess(command, 0, "", "")

            with mock.patch("platform.system", return_value="Windows"), mock.patch(
                "soundcapsule.renderer.subprocess.run", side_effect=malformed
            ):
                with self.assertRaisesRegex(RenderError, "not a WAVE"):
                    render_project(
                        root / "preview.flp", output, fl_executable=executable
                    )

    def test_silent_wave_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "FL64.exe"
            output = root / "preview.wav"
            executable.write_bytes(b"")

            def silent(command, **_kwargs):
                output.parent.mkdir(parents=True, exist_ok=True)
                with wave.open(str(output), "wb") as rendered:
                    rendered.setnchannels(1)
                    rendered.setsampwidth(2)
                    rendered.setframerate(44100)
                    rendered.writeframes(b"\0\0" * 128)
                return subprocess.CompletedProcess(command, 0, "", "")

            with mock.patch("platform.system", return_value="Windows"), mock.patch(
                "soundcapsule.renderer.subprocess.run", side_effect=silent
            ):
                with self.assertRaisesRegex(RenderError, "rendered silence"):
                    render_project(
                        root / "preview.flp", output, fl_executable=executable
                    )


if __name__ == "__main__":
    unittest.main()
