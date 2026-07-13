from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from soundcapsule.capsule import Capsule
from soundcapsule.bridge import BridgeSession
from soundcapsule.compatibility import require_mutation_profile
from soundcapsule.config import Settings
from soundcapsule.flp import (
    EVENT_FL_VERSION,
    EVENT_PLUGIN_INTERNAL_NAME,
    EVENT_PROJECT_DATA_PATH,
    FLPFile,
    FLPUnsupportedError,
    parse_text,
    text_event,
)
from soundcapsule.project import CapsuleService, ProjectLocator
from soundcapsule.project_locator import (
    _windows_browser_recent_projects,
    _windows_indexed_projects,
)
from soundcapsule.renderer import RenderError
from test_flp import fixture_project, write_silence


class ProjectServiceTests(unittest.TestCase):
    def test_windows_capture_uses_connected_fl_executable_and_cleans_staging(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Song.flp"
            source.write_bytes(fixture_project().to_bytes())
            connected_executable = root / "FL Studio 2025" / "FL64.exe"
            connected_executable.parent.mkdir()
            connected_executable.write_bytes(b"connected FL")
            configured_executable = root / "FL Studio 2026" / "FL64.exe"
            configured_executable.parent.mkdir()
            configured_executable.write_bytes(b"configured latest FL")
            settings = Settings(
                data_dir=root / "data", project_roots=[root],
                fl_executable=configured_executable,
            )
            service = CapsuleService(settings)
            session = BridgeSession(
                timestamp=time.time(), project_title="Song", midi_api_version=42,
                selected_channels=[0], selected_channel_names=["Serum Lead"],
                current_pattern=3, pattern_name="Verse", pattern_length_steps=16,
                ppq=96, changed=0, save_sequence=1,
                last_save_requested_at=time.time(), load_sequence=1,
                last_load_status=100, last_load_at=time.time(),
                host_name="FL Studio 2025", host_executable=str(connected_executable),
                host_pid=1234,
            )

            generated: list[Path] = []

            def fake_render(project, output, *, fl_executable):
                self.assertEqual(fl_executable, connected_executable)
                self.assertNotEqual(fl_executable, configured_executable)
                generated.extend((project, output))
                write_silence(output)
                return output

            with mock.patch.object(service.bridge, "session", return_value=session), mock.patch(
                "soundcapsule.project.platform.system", return_value="Windows"
            ), mock.patch(
                "soundcapsule.project.close_windows_fl_studio"
            ) as close_fl, mock.patch.object(service, "_open") as reopen, mock.patch(
                "soundcapsule.project.render_project", side_effect=fake_render
            ):
                capsules = service.capture("Lead")

            self.assertEqual(len(capsules), 1)
            close_fl.assert_called_once_with(
                session.host_pid, expected_executable=connected_executable
            )
            reopen.assert_called_once_with(source.resolve(), session)
            self.assertTrue(generated)
            self.assertTrue(all(not path.exists() for path in generated))
            self.assertEqual(list(settings.staging_dir.iterdir()), [])

    def test_windows_render_failure_reopens_original_project(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Song.flp"
            source.write_bytes(fixture_project().to_bytes())
            executable = root / "FL Studio 2025" / "FL64.exe"
            executable.parent.mkdir()
            executable.write_bytes(b"FL")
            settings = Settings(data_dir=root / "data", project_roots=[root])
            service = CapsuleService(settings)
            session = BridgeSession(
                timestamp=time.time(), project_title="Song", midi_api_version=42,
                selected_channels=[0], selected_channel_names=["Serum Lead"],
                current_pattern=3, pattern_name="Verse", pattern_length_steps=16,
                ppq=96, changed=0, save_sequence=1,
                last_save_requested_at=time.time(), load_sequence=1,
                last_load_status=100, last_load_at=time.time(),
                host_name="FL Studio 2025", host_executable=str(executable),
                host_pid=4321,
            )

            with mock.patch.object(service.bridge, "session", return_value=session), mock.patch(
                "soundcapsule.project.platform.system", return_value="Windows"
            ), mock.patch(
                "soundcapsule.project.close_windows_fl_studio"
            ) as close_fl, mock.patch.object(service, "_open") as reopen, mock.patch(
                "soundcapsule.project.render_project",
                side_effect=RenderError("CLI render failed"),
            ):
                with self.assertRaisesRegex(RenderError, "CLI render failed"):
                    service.capture("Lead")

            close_fl.assert_called_once()
            reopen.assert_called_once_with(source.resolve(), session)
            self.assertEqual(list(settings.staging_dir.iterdir()), [])

    def test_windows_close_failure_does_not_render_or_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Song.flp"
            source.write_bytes(fixture_project().to_bytes())
            executable = root / "FL Studio 2025" / "FL64.exe"
            executable.parent.mkdir()
            executable.write_bytes(b"FL")
            settings = Settings(data_dir=root / "data", project_roots=[root])
            service = CapsuleService(settings)
            session = BridgeSession(
                timestamp=time.time(), project_title="Song", midi_api_version=42,
                selected_channels=[0], selected_channel_names=["Serum Lead"],
                current_pattern=3, pattern_name="Verse", pattern_length_steps=16,
                ppq=96, changed=0, save_sequence=1,
                last_save_requested_at=time.time(), load_sequence=1,
                last_load_status=100, last_load_at=time.time(),
                host_name="FL Studio 2025", host_executable=str(executable),
                host_pid=4321,
            )

            with mock.patch.object(service.bridge, "session", return_value=session), mock.patch(
                "soundcapsule.project.platform.system", return_value="Windows"
            ), mock.patch(
                "soundcapsule.project.close_windows_fl_studio",
                side_effect=RenderError("did not close safely"),
            ), mock.patch.object(service, "_open") as reopen, mock.patch(
                "soundcapsule.project.render_project"
            ) as render:
                with self.assertRaisesRegex(RenderError, "did not close safely"):
                    service.capture("Lead")

            render.assert_not_called()
            reopen.assert_not_called()
            self.assertEqual(list(settings.staging_dir.iterdir()), [])

    def test_windows_browser_recent_projects_reads_current_fl_studio_list(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            documents = root / "Documents"
            recent = (
                documents
                / "Image-Line"
                / "FL Studio"
                / "Settings"
                / "Browser"
                / "Recent files.scr"
            )
            recent.parent.mkdir(parents=True)
            project = root / "temp.flp"
            project.write_bytes(fixture_project().to_bytes())
            recent.write_text(
                f"{project}\n{root / 'preview.wav'}\n{project}\n",
                encoding="utf-8",
            )

            self.assertEqual(
                _windows_browser_recent_projects([documents]), [project]
            )

    def test_windows_browser_recent_projects_ignores_sound_capsule_previews(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            documents = root / "Documents"
            recent = (
                documents
                / "Image-Line"
                / "FL Studio"
                / "Settings"
                / "Browser"
                / "Recent files.scr"
            )
            recent.parent.mkdir(parents=True)
            project = root / "temp.flp"
            preview = root / "LocalAppData" / "SoundCapsule" / "Staging" / "preview.flp"
            project.write_bytes(fixture_project().to_bytes())
            preview.parent.mkdir(parents=True)
            preview.write_bytes(fixture_project().to_bytes())
            recent.write_text(f"{preview}\n{project}\n", encoding="utf-8")

            with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(root / "LocalAppData")}):
                self.assertEqual(
                    _windows_browser_recent_projects([documents]), [project]
                )

    def test_reopen_targets_the_connected_macos_fl_application(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            application = root / "FL Studio 2025.app"
            executable = application / "Contents" / "MacOS" / "OsxFL"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"")
            project = root / "temp.flp"
            project.write_bytes(fixture_project().to_bytes())
            session = BridgeSession(
                timestamp=time.time(), project_title="", midi_api_version=40,
                selected_channels=[0], selected_channel_names=["Lead"],
                current_pattern=1, pattern_name="Pattern 1",
                pattern_length_steps=16, ppq=96, changed=0,
                save_sequence=0, last_save_requested_at=0.0,
                load_sequence=1, last_load_status=100, last_load_at=time.time(),
                host_name="FL Studio 2025", host_executable=str(executable),
            )
            service = CapsuleService(Settings(data_dir=root / "data"))

            with mock.patch("platform.system", return_value="Darwin"), mock.patch(
                "subprocess.Popen"
            ) as launch:
                service._open(project, session)

            launch.assert_called_once_with(
                ["open", "-a", str(application), str(project)]
            )

    def test_live_reopen_never_falls_back_to_the_default_fl_application(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "temp.flp"
            project.write_bytes(fixture_project().to_bytes())
            session = BridgeSession(
                timestamp=time.time(), project_title="", midi_api_version=40,
                selected_channels=[0], selected_channel_names=["Lead"],
                current_pattern=1, pattern_name="Pattern 1",
                pattern_length_steps=16, ppq=96, changed=0,
                save_sequence=0, last_save_requested_at=0.0,
                load_sequence=1, last_load_status=100, last_load_at=time.time(),
            )
            service = CapsuleService(Settings(data_dir=root / "data"))

            with mock.patch("platform.system", return_value="Darwin"), mock.patch(
                "subprocess.Popen"
            ) as launch, self.assertRaisesRegex(
                FLPUnsupportedError, "identify the connected FL Studio"
            ):
                service._open(project, session)

            launch.assert_not_called()

    def test_bridge_global_positions_resolve_to_sparse_flp_ids(self) -> None:
        session = BridgeSession(
            timestamp=0, project_title="Song", midi_api_version=38, selected_channels=[1],
            selected_channel_names=["Kick"], current_pattern=3, pattern_name="Verse",
            pattern_length_steps=16, ppq=96, changed=0,
            save_sequence=0, last_save_requested_at=0.0,
            load_sequence=0, last_load_status=-1, last_load_at=0.0,
        )
        self.assertEqual(CapsuleService._session_channel_ids(fixture_project(), session), [5])

    def test_dirty_bridge_session_requires_normal_fl_save(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            session = BridgeSession(
                timestamp=0, project_title="Song", midi_api_version=38, selected_channels=[0],
                selected_channel_names=["Lead"], current_pattern=3, pattern_name="Verse",
                pattern_length_steps=16, ppq=96, changed=1,
                save_sequence=0, last_save_requested_at=0.0,
                load_sequence=0, last_load_status=-1, last_load_at=0.0,
            )
            service = CapsuleService(Settings(data_dir=Path(temporary)))
            with self.assertRaisesRegex(FLPUnsupportedError, "save the FL Studio project"):
                service._resolve_project(None, session)

    def test_project_locator_refuses_an_unrelated_recent_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "Different Song.flp").write_bytes(fixture_project().to_bytes())
            with self.assertRaises(FileNotFoundError):
                ProjectLocator(
                    [root], recent_provider=lambda: [], indexed_provider=lambda _: []
                ).find_recent("Unsaved Idea")

    def test_project_locator_prefers_exact_title_over_generated_import(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Song.flp"
            generated = root / "Song_capsule-Lead-001.flp"
            source.write_bytes(fixture_project().to_bytes())
            generated.write_bytes(fixture_project().to_bytes())
            self.assertEqual(
                ProjectLocator(
                    [root], recent_provider=lambda: [], indexed_provider=lambda _: []
                ).find_recent("Song"),
                source.resolve(),
            )

    def test_project_locator_uses_fl_recent_order_without_a_configured_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            current = root / "current" / "Song.flp"
            duplicate = root / "duplicate" / "Song.flp"
            current.parent.mkdir()
            duplicate.parent.mkdir()
            current.write_bytes(fixture_project().to_bytes())
            duplicate.write_bytes(fixture_project().to_bytes())
            locator = ProjectLocator(
                [],
                cache_path=root / "cache.json",
                recent_provider=lambda: [current, duplicate],
                indexed_provider=lambda _: [],
            )

            self.assertEqual(locator.find_current("Song"), current.resolve())
            cache = json.loads((root / "cache.json").read_text(encoding="utf-8"))
            self.assertEqual(cache["song"]["path"], str(current.resolve()))

    def test_project_locator_uses_first_fl_recent_when_metadata_title_is_blank(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            current = root / "temp2.flp"
            current.write_bytes(fixture_project().to_bytes())
            locator = ProjectLocator(
                [], recent_provider=lambda: [current], indexed_provider=lambda _: []
            )
            self.assertEqual(locator.find_current(""), current.resolve())

    def test_windows_project_search_finds_exact_current_flp_in_standard_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            documents = Path(temporary) / "Documents"
            project = (
                documents
                / "Image-Line"
                / "FL Studio"
                / "Projects"
                / "temp-sound-2026"
                / "temp-sound-2026.flp"
            )
            project.parent.mkdir(parents=True)
            project.write_bytes(fixture_project().to_bytes())

            self.assertEqual(
                _windows_indexed_projects(
                    "temp-sound-2026", document_roots=[documents]
                ),
                [project],
            )

    def test_project_locator_filters_blank_title_recent_files_by_live_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stale = root / "stale.flp"
            current = root / "temp2.flp"
            stale.write_bytes(fixture_project().to_bytes())
            current.write_bytes(fixture_project().to_bytes())
            locator = ProjectLocator(
                [],
                recent_provider=lambda: [stale, current],
                indexed_provider=lambda _: [],
            )

            self.assertEqual(
                locator.find_current(
                    "", candidate_validator=lambda path: path.name == "temp2.flp"
                ),
                current.resolve(),
            )

    def test_project_locator_rejects_ambiguous_blank_title_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.flp"
            second = root / "second.flp"
            first.write_bytes(fixture_project().to_bytes())
            second.write_bytes(fixture_project().to_bytes())
            locator = ProjectLocator(
                [],
                recent_provider=lambda: [first, second],
                indexed_provider=lambda _: [],
            )

            with self.assertRaisesRegex(FLPUnsupportedError, "save the current project"):
                locator.find_current("", candidate_validator=lambda _: True)

    def test_live_channel_names_reject_a_stale_recent_project(self) -> None:
        session = BridgeSession(
            timestamp=0, project_title="", midi_api_version=42,
            selected_channels=[1], selected_channel_names=["Kick"],
            current_pattern=3, pattern_name="Verse", pattern_length_steps=16,
            ppq=96, changed=0, save_sequence=0, last_save_requested_at=0.0,
            load_sequence=0, last_load_status=-1, last_load_at=0.0,
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "temp2.flp"
            path.write_bytes(fixture_project().to_bytes())
            self.assertTrue(CapsuleService._project_matches_session(path, session))
            session.selected_channel_names = ["Wrong project"]
            self.assertFalse(CapsuleService._project_matches_session(path, session))

    def test_full_live_rack_signature_rejects_an_identical_selected_channel(self) -> None:
        session = BridgeSession(
            timestamp=0, project_title="", midi_api_version=42,
            selected_channels=[1], selected_channel_names=["Kick"],
            current_pattern=3, pattern_name="Verse", pattern_length_steps=16,
            ppq=96, changed=0, save_sequence=0, last_save_requested_at=0.0,
            load_sequence=4, last_load_status=100, last_load_at=0.0,
            channel_count=2, channel_names=["Serum Lead", "Kick"],
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "temp2.flp"
            path.write_bytes(fixture_project().to_bytes())
            self.assertTrue(CapsuleService._project_matches_session(path, session))
            session.channel_names[0] = "Different rack"
            self.assertFalse(CapsuleService._project_matches_session(path, session))

    def test_large_rack_identity_tolerates_one_dynamic_wrapper_name(self) -> None:
        saved = [f"Channel {index}" for index in range(13)]
        live = list(saved)
        live[-1] = "Preset-derived wrapper name"

        self.assertTrue(CapsuleService._rack_names_match(saved, live))
        live[-2] = "Another mismatch"
        self.assertFalse(CapsuleService._rack_names_match(saved, live))

    def test_seven_channel_rack_identity_tolerates_one_unsaved_rename(self) -> None:
        saved = ["Sampler", "Wa", "Vital", "FLEX", "FLEX", "Cling On Keys", "TYea"]
        live = list(saved)
        live[-1] = "broken"

        self.assertTrue(CapsuleService._rack_names_match(saved, live))
        live[1] = "Second unsaved rename"
        self.assertFalse(CapsuleService._rack_names_match(saved, live))

    def test_save_confirmed_blank_title_path_is_cached_by_session_signature(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "paths.json"
            old = root / "old.flp"
            saved = root / "temp2.flp"
            old.write_bytes(fixture_project().to_bytes())
            saved.write_bytes(fixture_project().to_bytes())
            started = time.time()
            os.utime(old, (started - 60, started - 60))
            os.utime(saved, (started + 1, started + 1))
            locator = ProjectLocator(
                [], cache_path=cache,
                recent_provider=lambda: [old, saved], indexed_provider=lambda _: [],
            )

            self.assertEqual(
                locator.find_current(
                    "", changed_after=started,
                    candidate_validator=lambda _: True, cache_key="rack-signature",
                ),
                saved.resolve(),
            )
            self.assertEqual(
                locator.find_current(
                    "", candidate_validator=lambda _: True, cache_key="rack-signature"
                ),
                saved.resolve(),
            )

    def test_project_locator_uses_the_flp_modified_by_the_save(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = root / "old" / "Song.flp"
            saved = root / "saved" / "Song.flp"
            old.parent.mkdir()
            saved.parent.mkdir()
            old.write_bytes(fixture_project().to_bytes())
            saved.write_bytes(fixture_project().to_bytes())
            started = time.time()
            os.utime(old, (started - 60, started - 60))
            os.utime(saved, (started + 1, started + 1))
            locator = ProjectLocator(
                [],
                recent_provider=lambda: [old, saved],
                indexed_provider=lambda _: [],
            )

            self.assertEqual(
                locator.find_current("Song.flp", changed_after=started), saved.resolve()
            )

    def test_save_time_shortlists_before_expensive_session_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = root / "old.flp"
            saved = root / "saved.flp"
            old.write_bytes(fixture_project().to_bytes())
            saved.write_bytes(fixture_project().to_bytes())
            started = time.time()
            os.utime(old, (started - 60, started - 60))
            os.utime(saved, (started + 1, started + 1))
            inspected: list[Path] = []
            locator = ProjectLocator(
                [],
                recent_provider=lambda: [old, saved],
                indexed_provider=lambda _: [],
            )

            selected = locator.find_current(
                "",
                changed_after=started,
                candidate_validator=lambda path: not inspected.append(path),
            )

            self.assertEqual(selected, saved.resolve())
            self.assertEqual(inspected, [saved.resolve()])

    def test_import_writes_new_version_and_never_changes_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = Settings(data_dir=root / "data", project_roots=[root])
            settings.ensure()
            source = root / "Song.flp"
            source.write_bytes(fixture_project().to_bytes())
            before = source.read_bytes()
            preview = root / "preview.wav"
            write_silence(preview)
            capsule = Capsule.build(
                settings.library_dir / "Lead.flcapsule",
                name="Lead", project=fixture_project(), channel_ids=[2], pattern_id=3, preview_wav=preview,
            )
            service = CapsuleService(settings)
            service.library.reindex()

            result = service.import_capsule(
                capsule.manifest.id, mode="append", project_path=source, open_project=False
            )

            self.assertEqual(source.read_bytes(), before)
            self.assertNotEqual(result.merged_project, source)
            self.assertTrue(result.merged_project.name.startswith("Song_capsule-Lead-001"))
            merged = FLPFile.read(result.merged_project)
            self.assertEqual(merged.channel_count, 3)
            self.assertEqual(result.channel_mapping, {2: 6})
            self.assertEqual(merged.channel_sections()[-1].name, "Lead")

    def test_import_applies_saved_channel_names_for_append_and_override(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = Settings(data_dir=root / "data")
            settings.ensure()
            preview = root / "preview.wav"
            write_silence(preview)
            capsule = Capsule.build(
                settings.library_dir / "Layers.flcapsule", name="Layer capsule",
                project=fixture_project(), channel_ids=[2, 5], pattern_id=3,
                preview_wav=preview,
            )
            service = CapsuleService(settings)
            service.library.reindex()
            service.library.rename(
                capsule.manifest.id, "Layer capsule", ["Saved lead", "Saved kick"]
            )

            append_target = root / "Append.flp"
            append_target.write_bytes(fixture_project().to_bytes())
            appended = service.import_capsule(
                capsule.manifest.id, mode="append", project_path=append_target,
                open_project=False,
            )
            appended_sections = FLPFile.read(appended.merged_project).channel_sections()
            self.assertEqual(
                [section.name for section in appended_sections[-2:]],
                ["Saved lead", "Saved kick"],
            )

            override_target = root / "Override.flp"
            override_target.write_bytes(fixture_project().to_bytes())
            overridden = service.import_capsule(
                capsule.manifest.id, mode="override", project_path=override_target,
                target_channels=[2, 5], pattern_id=3, open_project=False,
            )
            overridden_sections = FLPFile.read(overridden.merged_project).channel_sections()
            self.assertEqual(
                [section.name for section in overridden_sections],
                ["Saved lead", "Saved kick"],
            )
            self.assertEqual(
                [
                    next(
                        parse_text(event.payload) for event in section.events
                        if event.id == EVENT_PLUGIN_INTERNAL_NAME
                    )
                    for section in overridden_sections
                ],
                [
                    next(
                        parse_text(event.payload) for event in section.events
                        if event.id == EVENT_PLUGIN_INTERNAL_NAME
                    )
                    for section in fixture_project().channel_sections()
                ],
            )

    def test_newer_fl_capsule_can_be_tried_after_ui_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = Settings(data_dir=root / "data")
            settings.ensure()
            target = root / "FL25.flp"
            target.write_bytes(fixture_project().to_bytes())
            newer = fixture_project()
            newer.events[0] = text_event(
                EVENT_FL_VERSION, "26.1.0.5294", unicode_text=False
            )
            preview = root / "preview.wav"
            write_silence(preview)
            capsule = Capsule.build(
                settings.library_dir / "FL26.flcapsule",
                name="FL 26 Lead", project=newer, channel_ids=[2], pattern_id=3,
                preview_wav=preview,
            )
            service = CapsuleService(settings)
            service.library.reindex()

            result = service.import_capsule(
                capsule.manifest.id, mode="append", project_path=target,
                open_project=False,
            )

            self.assertEqual(capsule.manifest.source_fl_version, "26.1.0.5294")
            self.assertEqual(FLPFile.read(result.merged_project).channel_count, 3)

    def test_in_place_import_creates_backup_and_custom_undo_restores_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = Settings(data_dir=root / "data", project_roots=[root])
            source = root / "Song.flp"
            source.write_bytes(fixture_project().to_bytes())
            original = source.read_bytes()
            preview = root / "preview.wav"
            write_silence(preview)
            capsule = Capsule.build(
                settings.library_dir / "Lead.flcapsule",
                name="Lead", project=fixture_project(), channel_ids=[2], pattern_id=3, preview_wav=preview,
            )
            service = CapsuleService(settings)

            imported = service.import_capsule(
                capsule.manifest.id, mode="append", project_path=source,
                open_project=False, in_place=True,
            )

            merged_bytes = source.read_bytes()
            self.assertEqual(imported.merged_project, source.resolve())
            self.assertTrue(imported.backup_project.is_file())
            self.assertEqual(imported.backup_project.read_bytes(), original)
            self.assertNotEqual(merged_bytes, original)

            undone = service.undo_last_import(project_path=source, open_project=False)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(undone.safety_backup.read_bytes(), merged_bytes)
            with self.assertRaises(FLPUnsupportedError):
                service.undo_last_import(project_path=source, open_project=False)

    def test_default_import_appends_to_current_pattern_and_reports_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = Settings(data_dir=root / "data")
            source = root / "Song.flp"
            source.write_bytes(fixture_project().to_bytes())
            preview = root / "preview.wav"
            write_silence(preview)
            capsule = Capsule.build(
                settings.library_dir / "Lead.flcapsule",
                name="Lead", project=fixture_project(), channel_ids=[2],
                pattern_id=3, preview_wav=preview,
            )
            service = CapsuleService(settings)
            updates: list[tuple[int, str]] = []

            result = service.import_capsule(
                capsule.manifest.id,
                mode="append",
                project_path=source,
                open_project=False,
                in_place=True,
                progress_callback=lambda value, step: updates.append((value, step)),
            )

            merged = FLPFile.read(source)
            self.assertEqual(result.import_destination, "current_pattern")
            self.assertEqual(result.pattern_id, 3)
            self.assertEqual(merged.max_pattern_id(), 3)
            self.assertEqual([note.rack_channel for note in merged.pattern_notes()[3]], [2, 6, 5])
            self.assertEqual(updates[-1], (100, "Import complete"))
            self.assertGreaterEqual(len(updates), 6)

    def test_undo_does_not_fall_back_to_an_older_import(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = Settings(data_dir=root / "data")
            source = root / "Song.flp"
            source.write_bytes(fixture_project().to_bytes())
            preview = root / "preview.wav"
            write_silence(preview)
            capsule = Capsule.build(
                settings.library_dir / "Lead.flcapsule",
                name="Lead", project=fixture_project(), channel_ids=[2],
                pattern_id=3, preview_wav=preview,
            )
            service = CapsuleService(settings)
            for _ in range(2):
                service.import_capsule(
                    capsule.manifest.id, mode="append", project_path=source,
                    open_project=False, in_place=True,
                )

            service.undo_last_import(project_path=source, open_project=False)

            self.assertFalse(service.undo_status(source)["available"])
            with self.assertRaises(FLPUnsupportedError):
                service.undo_last_import(project_path=source, open_project=False)

    def test_undo_changed_project_within_window_creates_safety_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = Settings(data_dir=root / "data")
            source = root / "Song.flp"
            source.write_bytes(fixture_project().to_bytes())
            preview = root / "preview.wav"
            write_silence(preview)
            capsule = Capsule.build(
                settings.library_dir / "Lead.flcapsule",
                name="Lead", project=fixture_project(), channel_ids=[2], pattern_id=3, preview_wav=preview,
            )
            service = CapsuleService(settings)
            service.import_capsule(
                capsule.manifest.id, mode="append", project_path=source,
                open_project=False, in_place=True,
            )
            changed = FLPFile.read(source)
            changed._set_current_pattern(2)
            source.write_bytes(changed.to_bytes())
            changed_bytes = source.read_bytes()

            undone = service.undo_last_import(project_path=source, open_project=False)
            self.assertEqual(source.read_bytes(), fixture_project().to_bytes())
            self.assertEqual(undone.safety_backup.read_bytes(), changed_bytes)

    def test_undo_expires_after_configured_window(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = Settings(data_dir=root / "data", undo_window_minutes=1)
            source = root / "Song.flp"
            source.write_bytes(fixture_project().to_bytes())
            preview = root / "preview.wav"
            write_silence(preview)
            capsule = Capsule.build(
                settings.library_dir / "Lead.flcapsule",
                name="Lead", project=fixture_project(), channel_ids=[2], pattern_id=3, preview_wav=preview,
            )
            service = CapsuleService(settings)
            service.import_capsule(
                capsule.manifest.id, mode="append", project_path=source,
                open_project=False, in_place=True,
            )
            journal = settings.data_dir / "transactions.jsonl"
            record = json.loads(journal.read_text(encoding="utf-8"))
            record["timestamp"] = time.time() - 61
            journal.write_text(json.dumps(record) + "\n", encoding="utf-8")

            self.assertFalse(service.undo_status(source)["available"])
            with self.assertRaisesRegex(FLPUnsupportedError, "expired"):
                service.undo_last_import(project_path=source, open_project=False)

    def test_unknown_fl_version_is_library_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = Settings(data_dir=root / "data")
            project = fixture_project()
            version = next(event for event in project.events if event.id == 199)
            project.events[project.events.index(version)] = version.with_payload(b"26.0.0\0")
            source = root / "Future.flp"
            source.write_bytes(project.to_bytes())
            preview = root / "preview.wav"
            write_silence(preview)
            service = CapsuleService(settings)
            with self.assertRaises(FLPUnsupportedError):
                service.capture("Future", project_path=source, preview_wav=preview)

    def test_fl25_windows_range_allows_capture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = fixture_project()
            version = next(
                event for event in project.events if event.id == EVENT_FL_VERSION
            )
            project.events[project.events.index(version)] = version.with_payload(
                b"25.2.5.5319\0"
            )
            source = root / "FL25-Windows.flp"
            source.write_bytes(project.to_bytes())
            preview = root / "preview.wav"
            write_silence(preview)

            capsules = CapsuleService(Settings(data_dir=root / "data")).capture(
                "FL 25 Windows", project_path=source, preview_wav=preview
            )

            self.assertEqual(len(capsules), 1)

    def test_failed_render_cleans_capture_project_preview_and_wave(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Song.flp"
            source.write_bytes(fixture_project().to_bytes())
            settings = Settings(data_dir=root / "data")
            service = CapsuleService(settings)
            generated: list[Path] = []

            def fail_render(project, output, *, fl_executable):
                generated.extend((project, output))
                output.write_bytes(b"partial render")
                raise RuntimeError("native command-line render failed")

            with mock.patch(
                "soundcapsule.project.render_project", side_effect=fail_render
            ):
                with self.assertRaisesRegex(RuntimeError, "command-line render failed"):
                    service.capture("Lead", project_path=source)

            self.assertTrue(generated)
            self.assertTrue(all(not path.exists() for path in generated))
            self.assertEqual(list(settings.staging_dir.iterdir()), [])

    def test_grouped_and_individual_generated_renders_are_cleaned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Song.flp"
            source.write_bytes(fixture_project().to_bytes())
            settings = Settings(data_dir=root / "data")
            service = CapsuleService(settings)
            generated: list[Path] = []

            def fake_render(project, output, *, fl_executable):
                self.assertTrue(all(not path.exists() for path in generated))
                generated.extend((project, output))
                write_silence(output)
                return output

            with mock.patch(
                "soundcapsule.project.render_project", side_effect=fake_render
            ):
                grouped = service.capture("Group", project_path=source)
                individual = service.capture(
                    "Ignored", project_path=source, individually=True
                )

            self.assertEqual(len(grouped), 1)
            self.assertEqual(len(individual), 2)
            self.assertEqual(len(generated), 6)
            self.assertTrue(all(not path.exists() for path in generated))
            self.assertEqual(list(settings.staging_dir.iterdir()), [])

    def test_windows_capture_never_falls_back_to_another_fl_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Song.flp"
            source.write_bytes(fixture_project().to_bytes())
            configured_executable = root / "FL Studio 2026" / "FL64.exe"
            configured_executable.parent.mkdir()
            configured_executable.write_bytes(b"configured latest FL")
            service = CapsuleService(Settings(
                data_dir=root / "data", project_roots=[root],
                fl_executable=configured_executable,
            ))
            session = BridgeSession(
                timestamp=time.time(), project_title="Song", midi_api_version=42,
                selected_channels=[0], selected_channel_names=["Serum Lead"],
                current_pattern=3, pattern_name="Verse", pattern_length_steps=16,
                ppq=96, changed=0, save_sequence=1,
                last_save_requested_at=time.time(), load_sequence=1,
                last_load_status=100, last_load_at=time.time(),
                host_name="", host_executable=str(root / "missing" / "FL64.exe"),
                host_pid=1234,
            )

            with mock.patch.object(service.bridge, "session", return_value=session), mock.patch(
                "soundcapsule.project.platform.system", return_value="Windows"
            ), mock.patch("soundcapsule.project.render_project") as render:
                with self.assertRaisesRegex(
                    FLPUnsupportedError, "connected FL Studio executable"
                ):
                    service.capture("Lead")

            render.assert_not_called()

    def test_capture_reports_detailed_progress_and_single_channel_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Song.flp"
            source.write_bytes(fixture_project().to_bytes())
            preview = root / "preview.wav"
            write_silence(preview)
            updates: list[tuple[int, str]] = []

            service = CapsuleService(Settings(data_dir=root / "data"))
            session = BridgeSession(
                timestamp=time.time(), project_title="Song", midi_api_version=42,
                selected_channels=[0], selected_channel_names=["Serum Lead"],
                current_pattern=3, pattern_name="Verse", pattern_length_steps=16,
                ppq=96, changed=0, save_sequence=1,
                last_save_requested_at=time.time(), load_sequence=1,
                last_load_status=100, last_load_at=time.time(), channel_count=2,
                channel_names=["Serum Lead", "Kick"],
            )
            with mock.patch.object(service.bridge, "session", return_value=session), mock.patch(
                "soundcapsule.project.ProjectLocator.find_current",
                return_value=source.resolve(),
            ):
                capsules = service.capture(
                    "Custom capture", preview_wav=preview,
                    progress_callback=lambda value, step: updates.append((value, step)),
                )

            self.assertEqual(updates[0], (3, "Locating the current FL Studio project"))
            self.assertIn("validating", " ".join(step for _, step in updates))
            self.assertIn("Packaging", " ".join(step for _, step in updates))
            self.assertEqual(updates[-1], (100, "Capsule saved"))
            self.assertEqual(capsules[0].manifest.name, "Custom capture")
            self.assertEqual(capsules[0].manifest.channels[0].name, "Custom capture")

            individual_updates: list[tuple[int, str]] = []
            individual = service.capture(
                "Ignored group title", project_path=source, preview_wav=preview,
                individually=True,
                progress_callback=lambda value, step: individual_updates.append(
                    (value, step)
                ),
            )
            individual_steps = " ".join(step for _, step in individual_updates)
            self.assertIn("Packaging Serum Lead", individual_steps)
            self.assertIn("Packaging Kick", individual_steps)
            self.assertEqual(
                [capsule.manifest.name for capsule in individual],
                ["Serum Lead", "Kick"],
            )

    def test_mutation_profiles_cover_verified_major_ranges(self) -> None:
        self.assertEqual(require_mutation_profile("25.2.5.5319").name, "fl25")
        self.assertEqual(require_mutation_profile("25.9.0.1").name, "fl25")
        self.assertEqual(require_mutation_profile("26.1.0.5530").name, "fl26")
        self.assertEqual(require_mutation_profile("26.9.0.1").name, "fl26")
        for unsupported in ("25.2.5.5054", "26.1.0.5293", "27.0.0", "unknown"):
            with self.subTest(unsupported=unsupported), self.assertRaises(
                FLPUnsupportedError
            ):
                require_mutation_profile(unsupported)

    def test_backup_uses_project_data_folder_but_rejects_unresolved_macros(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Projects" / "Song.flp"
            source.parent.mkdir()
            service = CapsuleService(Settings(data_dir=root / "data"))
            project = fixture_project()
            project.events.insert(0, text_event(EVENT_PROJECT_DATA_PATH, "Song data"))
            self.assertEqual(
                service._backup_directory(source, project),
                source.parent / "Song data" / "Backups" / "Sound Capsule",
            )

            macro_project = fixture_project()
            macro_project.events.insert(
                0, text_event(EVENT_PROJECT_DATA_PATH, "%FLStudioData%/Projects/Song")
            )
            self.assertEqual(
                service._backup_directory(source, macro_project),
                source.parent / "Backup" / "Sound Capsule",
            )


if __name__ == "__main__":
    unittest.main()
