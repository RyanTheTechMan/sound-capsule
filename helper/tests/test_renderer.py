from __future__ import annotations

import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from soundcapsule.renderer import render_project


def write_audible_wave(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(44100)
        output.writeframes((1000).to_bytes(2, "little", signed=True) * 128)


class RendererTests(unittest.TestCase):
    def test_windows_live_host_uses_a_separate_interactive_instance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "FL64.exe"
            project = root / "preview.flp"
            output = root / "preview.wav"
            executable.write_bytes(b"")
            project.write_bytes(b"FLhd")

            def fake_windows_render(
                actual_project,
                actual_output,
                *,
                executable,
                host_pid,
                timeout,
            ):
                self.assertEqual(actual_project, project)
                self.assertEqual(actual_output, output)
                self.assertEqual(executable, root / "FL64.exe")
                self.assertEqual(host_pid, 321)
                self.assertEqual(timeout, 12.0)
                write_audible_wave(actual_output)

            with mock.patch("platform.system", return_value="Windows"), mock.patch(
                "soundcapsule.renderer._render_windows_separate_instance",
                side_effect=fake_windows_render,
            ) as separate_render, mock.patch(
                "soundcapsule.renderer.subprocess.run"
            ) as cli_render:
                result = render_project(
                    project,
                    output,
                    fl_executable=executable,
                    host_pid=321,
                    timeout=12.0,
                )

            self.assertEqual(result, output)
            separate_render.assert_called_once()
            cli_render.assert_not_called()

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
                    host_pid=321,
                )

            self.assertEqual(result, output)
            command = launch.call_args.args[0]
            self.assertEqual(command[:4], ["open", "-n", "-W", str(application)])
            self.assertIn(str(project), command)


if __name__ == "__main__":
    unittest.main()
