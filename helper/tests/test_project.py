from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from soundcapsule.capsule import Capsule
from soundcapsule.bridge import BridgeSession
from soundcapsule.config import Settings
from soundcapsule.flp import EVENT_PROJECT_DATA_PATH, FLPFile, FLPUnsupportedError, text_event
from soundcapsule.project import CapsuleService, ProjectLocator
from test_flp import fixture_project, write_silence


class ProjectServiceTests(unittest.TestCase):
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
            self.assertIn(str(current.resolve()), (root / "cache.json").read_text())

    def test_project_locator_uses_first_fl_recent_when_metadata_title_is_blank(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            current = root / "temp2.flp"
            current.write_bytes(fixture_project().to_bytes())
            locator = ProjectLocator(
                [], recent_provider=lambda: [current], indexed_provider=lambda _: []
            )
            self.assertEqual(locator.find_current(""), current.resolve())

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

    def test_newer_minor_capsule_version_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = CapsuleService(Settings(data_dir=Path(temporary)))
            with self.assertRaises(FLPUnsupportedError):
                service._check_version_compatibility("25.3.1", "25.2.9")

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
            self.assertEqual([note.rack_channel for note in merged.pattern_notes()[3]], [2, 5, 6])
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
