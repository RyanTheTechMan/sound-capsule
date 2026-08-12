from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from soundcapsule.config import (
    HELPER_TOKEN_FILENAME,
    Settings,
    load_or_create_helper_token,
    registered_fl_user_folder,
)


class ConfigTests(unittest.TestCase):
    def test_mixer_insert_capture_defaults_on_and_survives_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary) / "data"
            data.mkdir()
            (data / "settings.json").write_text(
                json.dumps({"data_dir": str(data)}), encoding="utf-8"
            )

            settings = Settings.load(data)
            self.assertTrue(settings.save_mixer_insert)
            self.assertFalse(settings.include_related_automation)
            settings.save_mixer_insert = False
            settings.include_related_automation = True
            settings.save()

            self.assertFalse(Settings.load(data).save_mixer_insert)
            self.assertTrue(Settings.load(data).include_related_automation)

    def test_helper_token_is_private_and_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary) / "data"
            first = load_or_create_helper_token(data)
            second = load_or_create_helper_token(data)
            token_path = data / HELPER_TOKEN_FILENAME

            self.assertEqual(first, second)
            self.assertGreaterEqual(len(first), 32)
            self.assertEqual(token_path.read_text(encoding="utf-8").strip(), first)
            if os.name != "nt":
                self.assertEqual(token_path.stat().st_mode & 0o777, 0o600)

    def test_windows_fl_user_folder_comes_from_image_line_registry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            shared = Path(temporary) / "Image-Line Data"
            expected = shared / "FL Studio"
            expected.mkdir(parents=True)
            registry = mock.MagicMock()
            registry.HKEY_CURRENT_USER = object()
            registry.QueryValueEx.return_value = (str(shared), 2)

            with mock.patch(
                "soundcapsule.config.platform.system", return_value="Windows"
            ), mock.patch.dict("sys.modules", {"winreg": registry}):
                self.assertEqual(registered_fl_user_folder(), expected)

            registry.OpenKey.assert_called_once_with(
                registry.HKEY_CURRENT_USER, r"Software\Image-Line\Shared\Paths"
            )
            registry.QueryValueEx.assert_called_once_with(
                registry.OpenKey.return_value.__enter__.return_value, "Shared data"
            )

    def test_macos_fl_user_folder_comes_from_image_line_registry_xml(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / "home"
            shared = home / "Image-Line Data"
            expected = shared / "FL Studio"
            expected.mkdir(parents=True)
            registry = home / "Library" / "Preferences" / "Image-Line" / "reg.xml"
            registry.parent.mkdir(parents=True)
            registry.write_text(
                f"""<?xml version="1.0" encoding="utf-8"?>
<XMLReg>
  <Key Name="Key1">
    <Key Name="HKEY_CURRENT_USER">
      <Key Name="Software">
        <Key Name="Image-Line">
          <Key Name="Shared">
            <Key Name="Paths">
              <Value Name="FL Studio engine" Type="2">ignored</Value>
              <Value Name="Shared data" Type="2">{shared}</Value>
            </Key>
          </Key>
        </Key>
      </Key>
    </Key>
  </Key>
</XMLReg>""",
                encoding="utf-8",
            )

            with mock.patch(
                "soundcapsule.config.platform.system", return_value="Darwin"
            ), mock.patch("soundcapsule.config.Path.home", return_value=home):
                self.assertEqual(registered_fl_user_folder(), expected)

    def test_invalid_registry_folder_has_no_guessed_fallback(self) -> None:
        registry = mock.MagicMock()
        registry.HKEY_CURRENT_USER = object()
        registry.QueryValueEx.return_value = (r"C:\missing\Image-Line", 2)

        with mock.patch(
            "soundcapsule.config.platform.system", return_value="Windows"
        ), mock.patch.dict("sys.modules", {"winreg": registry}):
            self.assertIsNone(registered_fl_user_folder())

    def test_settings_migrates_away_from_persisted_fl_user_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary) / "data"
            data.mkdir()
            (data / "settings.json").write_text(
                json.dumps({
                    "data_dir": str(data),
                    "fl_user_folder": "C:/old/FL Studio",
                }),
                encoding="utf-8",
            )
            current = Path(temporary) / "current" / "FL Studio"
            current.mkdir(parents=True)

            with mock.patch(
                "soundcapsule.config.registered_fl_user_folder", return_value=current
            ):
                settings = Settings.load(data)
                self.assertEqual(settings.fl_user_folder, current)
                settings.save()

            self.assertNotIn(
                "fl_user_folder",
                (data / "settings.json").read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
