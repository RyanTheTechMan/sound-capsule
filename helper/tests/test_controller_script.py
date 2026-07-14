from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

from soundcapsule.bridge import BridgeQueue


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "fl-studio"
    / "SoundCapsule"
    / "device_SoundCapsule.py"
)


def stub_module(name: str, **attributes) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    return module


class ControllerScriptTests(unittest.TestCase):
    def load_script(self, root: Path):
        saves: list[tuple[int, int]] = []
        modules = {
            "channels": stub_module(
                "channels",
                channelCount=lambda _include_all: 1,
                getChannelName=lambda _index, _include_all: "Lead",
                isChannelSelected=lambda _index, _include_all: True,
            ),
            "general": stub_module(
                "general",
                getVersion=lambda: 42,
                getProjectTitle=lambda: "Song",
                getRecPPQ=lambda: 96,
                getChangedFlag=lambda: 0,
            ),
            "midi": stub_module("midi", FPT_Save=100),
            "patterns": stub_module(
                "patterns",
                patternNumber=lambda: 1,
                getPatternName=lambda _pattern: "Pattern 1",
                getPatternLength=lambda _pattern: 16,
            ),
            "transport": stub_module(
                "transport",
                globalTransport=lambda command, value: saves.append((command, value)),
            ),
            "ui": stub_module("ui", getProgTitle=lambda: "FL Studio 2026"),
        }
        spec = importlib.util.spec_from_file_location(
            "soundcapsule_controller_under_test", SCRIPT
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("could not load the FL controller script")
        controller = importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules, modules), mock.patch.dict(
            os.environ, {"LOCALAPPDATA": str(root)}
        ), mock.patch.object(sys, "platform", "win32"):
            spec.loader.exec_module(controller)
        return controller, saves

    @staticmethod
    def write_command(root: Path, payload: object) -> Path:
        path = root / "SoundCapsule" / "Bridge" / "command.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload, str):
            path.write_text(payload, encoding="utf-8")
        else:
            path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    @staticmethod
    def save_command(request_id: str, *, expires_at: float) -> dict:
        return {
            "request_id": request_id,
            "command": "save",
            "created_at": time.time(),
            "expires_at": expires_at,
        }

    def test_unexpired_command_present_at_startup_is_not_replayed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_command(
                root,
                self.save_command("previous-session", expires_at=time.time() + 30),
            )
            controller, saves = self.load_script(root)

            controller.OnInit()
            controller.OnIdle()

            self.assertEqual(controller._last_command_id, "previous-session")
            self.assertEqual(controller._save_sequence, 0)
            self.assertEqual(saves, [])

    def test_fresh_command_after_startup_runs_exactly_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            controller, saves = self.load_script(root)
            controller.OnInit()
            self.write_command(
                root,
                self.save_command("current-session", expires_at=time.time() + 30),
            )

            controller.OnIdle()
            controller.OnIdle()

            self.assertEqual(controller._last_command_id, "current-session")
            self.assertEqual(controller._save_sequence, 1)
            self.assertEqual(saves, [(100, 1)])

    def test_published_session_is_readable_by_helper_bridge_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            controller, _saves = self.load_script(root)

            controller.OnInit()

            session = BridgeQueue(root / "SoundCapsule" / "Bridge").session(
                maximum_age=10
            )
            self.assertEqual(session.project_title, "Song")
            self.assertEqual(session.selected_channels, [0])
            self.assertEqual(session.selected_channel_names, ["Lead"])
            self.assertEqual(session.host_name, "FL Studio 2026")
            self.assertEqual(session.midi_api_version, 42)

    def test_expired_and_malformed_commands_are_harmless(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            controller, saves = self.load_script(root)
            controller.OnInit()
            self.write_command(
                root,
                self.save_command("expired", expires_at=time.time() - 1),
            )
            controller.OnIdle()
            self.assertEqual(controller._last_command_id, "expired")

            self.write_command(root, "{malformed")
            controller.OnIdle()

            self.assertEqual(controller._save_sequence, 0)
            self.assertEqual(saves, [])


if __name__ == "__main__":
    unittest.main()
