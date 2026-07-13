from __future__ import annotations

import json
import hashlib
import shutil
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from soundcapsule.capsule import Capsule
from soundcapsule.library import CapsuleLibrary
from test_flp import (
    fixture_project,
    make_legacy_capsule,
    write_float_silence,
    write_silence,
)


class LibraryTests(unittest.TestCase):
    def test_reindex_atomically_upgrades_legacy_capsules(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library_dir = root / "library"
            preview = root / "preview.wav"
            write_silence(preview)
            legacy = Capsule.build(
                library_dir / "Lead.flcapsule", name="Lead",
                project=fixture_project(), channel_ids=[2], pattern_id=3,
                preview_wav=preview,
            )
            make_legacy_capsule(legacy)
            capsule_id = legacy.manifest.id
            library = CapsuleLibrary(library_dir, root / "index.sqlite3")

            self.assertEqual(library.reindex(), 1)

            upgraded = library_dir / "Lead.flcapsule.wav"
            self.assertFalse(legacy.path.exists())
            self.assertTrue(upgraded.exists())
            self.assertEqual(Capsule(upgraded).container_format, "playable")
            self.assertEqual(Capsule(upgraded).manifest.id, capsule_id)
            self.assertEqual(len(library.last_migration_summary["converted"]), 1)
            self.assertFalse(library.last_migration_summary["failed"])

    def test_failed_legacy_upgrade_leaves_the_source_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library_dir = root / "library"
            library_dir.mkdir()
            legacy = library_dir / "Broken.flcapsule"
            original = b"not a capsule"
            legacy.write_bytes(original)
            library = CapsuleLibrary(library_dir, root / "index.sqlite3")

            self.assertEqual(library.reindex(), 0)

            self.assertEqual(legacy.read_bytes(), original)
            self.assertFalse(list(library_dir.glob("*.flcapsule.wav")))
            self.assertEqual(len(library.last_migration_summary["failed"]), 1)

    def test_legacy_cleanup_failure_rolls_back_the_playable_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library_dir = root / "library"
            preview = root / "preview.wav"
            write_silence(preview)
            legacy = Capsule.build(
                library_dir / "Lead.flcapsule", name="Lead",
                project=fixture_project(), channel_ids=[2], pattern_id=3,
                preview_wav=preview,
            )
            make_legacy_capsule(legacy)
            library = CapsuleLibrary(library_dir, root / "index.sqlite3")
            original_unlink = Path.unlink

            def fail_source_unlink(path: Path, *args, **kwargs):
                if path == legacy.path:
                    raise OSError("simulated cleanup failure")
                return original_unlink(path, *args, **kwargs)

            with patch.object(Path, "unlink", new=fail_source_unlink):
                self.assertEqual(library.reindex(), 1)

            self.assertTrue(legacy.path.exists())
            self.assertFalse(list(library_dir.glob("*.flcapsule.wav")))
            self.assertEqual(len(library.last_migration_summary["failed"]), 1)

    def test_legacy_upgrade_uses_a_collision_safe_playable_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library_dir = root / "library"
            preview = root / "preview.wav"
            write_silence(preview)
            Capsule.build(
                library_dir / "Lead.flcapsule.wav", name="Existing",
                project=fixture_project(), channel_ids=[5], pattern_id=3,
                preview_wav=preview,
            )
            legacy = Capsule.build(
                library_dir / "Lead.flcapsule", name="Migrated",
                project=fixture_project(), channel_ids=[2], pattern_id=3,
                preview_wav=preview,
            )
            make_legacy_capsule(legacy)
            library = CapsuleLibrary(library_dir, root / "index.sqlite3")

            self.assertEqual(library.reindex(), 2)

            self.assertTrue((library_dir / "Lead.flcapsule.wav").exists())
            self.assertTrue((library_dir / "Lead-2.flcapsule.wav").exists())
            self.assertFalse(legacy.path.exists())

    def test_imported_legacy_capsule_is_stored_as_playable_wav(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = root / "preview.wav"
            write_silence(preview)
            legacy = Capsule.build(
                root / "incoming" / "Shared.flcapsule", name="Shared",
                project=fixture_project(), channel_ids=[2], pattern_id=3,
                preview_wav=preview,
            )
            make_legacy_capsule(legacy)
            library = CapsuleLibrary(root / "library", root / "index.sqlite3")

            result = library.add_capsules([legacy.path])

            imported = Path(result["imported"][0]["path"])
            self.assertTrue(imported.name.endswith(".flcapsule.wav"))
            self.assertEqual(Capsule(imported).container_format, "playable")
            self.assertTrue(legacy.path.exists())

    def test_add_capsules_validates_copies_and_reports_partial_results(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            incoming = root / "incoming"
            library_dir = root / "library"
            preview = root / "preview.wav"
            write_silence(preview)
            source = Capsule.build(
                incoming / "Shared Lead.flcapsule",
                name="Shared Lead",
                project=fixture_project(),
                channel_ids=[2],
                pattern_id=3,
                pattern_length_steps=16,
                preview_wav=preview,
            )
            original = source.path.read_bytes()
            corrupt = incoming / "Broken.flcapsule"
            corrupt.write_bytes(b"not a capsule")
            library = CapsuleLibrary(library_dir, root / "index.sqlite3")

            first = library.add_capsules([source.path, corrupt])

            self.assertEqual(len(first["imported"]), 1)
            self.assertEqual(len(first["failed"]), 1)
            self.assertFalse(first["skipped"])
            destination = Path(first["imported"][0]["path"])
            self.assertEqual(destination.read_bytes(), original)
            self.assertEqual(source.path.read_bytes(), original)
            self.assertEqual(library.list()[0]["id"], source.manifest.id)
            self.assertFalse(list(library_dir.glob(".capsule-import-*.tmp")))

            duplicate = library.add_capsules([source.path])

            self.assertFalse(duplicate["imported"])
            self.assertEqual(len(duplicate["skipped"]), 1)
            self.assertIn("already", duplicate["skipped"][0]["reason"])
            self.assertEqual(destination.read_bytes(), original)

    def test_add_capsules_uses_collision_safe_manifest_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = root / "preview.wav"
            write_silence(preview)
            first = Capsule.build(
                root / "incoming" / "First.flcapsule",
                name="Same Name",
                project=fixture_project(),
                channel_ids=[2],
                pattern_id=3,
                pattern_length_steps=16,
                preview_wav=preview,
            )
            second = Capsule.build(
                root / "incoming" / "Second.flcapsule",
                name="Same Name",
                project=fixture_project(),
                channel_ids=[5],
                pattern_id=3,
                pattern_length_steps=16,
                preview_wav=preview,
            )
            library = CapsuleLibrary(root / "library", root / "index.sqlite3")

            result = library.add_capsules([first.path, second.path])

            self.assertEqual(len(result["imported"]), 2)
            self.assertEqual(
                {Path(item["path"]).name for item in result["imported"]},
                {"Same-Name.flcapsule.wav", "Same-Name-2.flcapsule.wav"},
            )
            self.assertEqual(len(library.list()), 2)

    def test_index_search_and_metadata_updates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library_dir = root / "library"
            preview = root / "preview.wav"
            write_silence(preview)
            project = fixture_project()
            capsule = Capsule.build(
                library_dir / "Lead.flcapsule",
                name="Lead",
                project=project,
                channel_ids=[2],
                pattern_id=3,
                pattern_length_steps=16,
                preview_wav=preview,
            )
            shutil.copy2(preview, library_dir / "Lead.wav")
            library = CapsuleLibrary(library_dir, root / "index.sqlite3")
            self.assertEqual(library.reindex(), 1)
            self.assertTrue((library_dir / "Lead.wav").exists())
            self.assertEqual(len(library.list("Serum")), 1)

            library.set_favorite(capsule.manifest.id, True)
            library.set_tags(capsule.manifest.id, ["bass", "dark"])
            library.rename(capsule.manifest.id, "Dark Lead")
            row = library.list("dark")[0]
            self.assertEqual(row["name"], "Dark Lead")
            self.assertEqual(row["source_fl_version"], project.fl_version)
            self.assertEqual(row["favorite"], 1)
            self.assertIn("bass", row["tags"])
            self.assertEqual(len(library.list("bass, dark")), 1)
            self.assertEqual(Path(row["preview_path"]), library_dir.resolve() / "Lead.flcapsule")
            note_preview = json.loads(row["note_preview"])
            self.assertTrue(note_preview)
            self.assertTrue(all(len(note) == 4 for note in note_preview))
            self.assertEqual({note[3] for note in note_preview}, {0})
            source_note = sorted(
                (note for note in project.pattern_notes()[3] if note.rack_channel == 2),
                key=lambda note: (note.position, note.key),
            )[0]
            note_end = source_note.position + source_note.length
            self.assertAlmostEqual(note_preview[0][0], source_note.position / note_end, places=6)
            self.assertEqual(json.loads(row["channel_names"]), ["Lead"])

    def test_v2_midi_preview_uses_tempo_and_audio_tail_duration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = root / "preview.wav"
            write_silence(preview, duration_seconds=4.0)
            project = fixture_project()
            capsule = Capsule.build(
                root / "library" / "Lead.flcapsule", name="Lead",
                project=project, channel_ids=[2], pattern_id=3,
                pattern_length_steps=16, preview_wav=preview,
            )
            library = CapsuleLibrary(root / "library", root / "index.sqlite3")
            library.reindex()

            row = next(item for item in library.list() if item["id"] == capsule.manifest.id)
            notes = json.loads(row["note_preview"])
            note_end = 24 + 96
            midi_seconds = note_end * 60.0 / (project.ppq * project.tempo_bpm)

            self.assertAlmostEqual(notes[0][0], 24 / note_end, places=6)
            self.assertAlmostEqual(notes[0][1], 96 / note_end, places=6)
            self.assertAlmostEqual(row["midi_playback_end"], midi_seconds / 4.0, places=6)

    def test_v2_midi_preview_uses_float_render_duration_on_mac(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = root / "preview.wav"
            write_float_silence(preview, duration_seconds=4.0)
            project = fixture_project()
            capsule = Capsule.build(
                root / "library" / "Lead.flcapsule", name="Lead",
                project=project, channel_ids=[2], pattern_id=3,
                pattern_length_steps=16, preview_wav=preview,
            )
            library = CapsuleLibrary(root / "library", root / "index.sqlite3")
            library.reindex()

            row = next(item for item in library.list() if item["id"] == capsule.manifest.id)
            midi_seconds = 120 * 60.0 / (project.ppq * project.tempo_bpm)

            self.assertAlmostEqual(row["midi_playback_end"], midi_seconds / 4.0, places=6)

    def test_v1_midi_preview_keeps_legacy_pattern_length_timing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = root / "preview.wav"
            write_silence(preview, duration_seconds=4.0)
            capsule = Capsule.build(
                root / "library" / "Legacy.flcapsule", name="Legacy",
                project=fixture_project(), channel_ids=[2], pattern_id=3,
                pattern_length_steps=16, preview_wav=preview,
            )
            make_legacy_capsule(capsule)
            with zipfile.ZipFile(capsule.path) as source:
                members = {
                    name: source.read(name)
                    for name in source.namelist() if name != "checksums.json"
                }
            manifest = json.loads(members["manifest.json"])
            manifest["schema_version"] = 1
            manifest.pop("source_tempo_bpm", None)
            members["manifest.json"] = json.dumps(manifest).encode()
            checksums = {
                name: hashlib.sha256(data).hexdigest() for name, data in members.items()
            }
            with zipfile.ZipFile(capsule.path, "w") as target:
                for name, data in members.items():
                    target.writestr(name, data)
                target.writestr("checksums.json", json.dumps(checksums))

            capsule_id = capsule.manifest.id
            library = CapsuleLibrary(root / "library", root / "index.sqlite3")
            library.reindex()
            row = next(item for item in library.list() if item["id"] == capsule_id)
            notes = json.loads(row["note_preview"])
            legacy_end = 16 * fixture_project().ppq / 4

            self.assertAlmostEqual(notes[0][0], 24 / legacy_end, places=6)
            self.assertAlmostEqual(row["midi_playback_end"], 120 / legacy_end, places=6)

    def test_grouped_rename_updates_title_and_every_channel_independently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = root / "preview.wav"
            write_silence(preview)
            capsule = Capsule.build(
                root / "library" / "Group.flcapsule", name="Group",
                project=fixture_project(), channel_ids=[2, 5], pattern_id=3,
                preview_wav=preview,
            )
            library = CapsuleLibrary(root / "library", root / "index.sqlite3")
            library.reindex()

            library.rename(capsule.manifest.id, "New title", ["Lead layer", "Kick layer"])

            updated = library.find(capsule.manifest.id).manifest
            self.assertEqual(updated.name, "New title")
            self.assertEqual(
                [channel.name for channel in updated.channels],
                ["Lead layer", "Kick layer"],
            )
            with self.assertRaisesRegex(ValueError, "match every capsule channel"):
                library.rename(capsule.manifest.id, "Invalid", ["Only one"])

    def test_grouped_note_preview_retains_channel_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = root / "preview.wav"
            write_silence(preview)
            project = fixture_project()
            capsule = Capsule.build(
                root / "library" / "Group.flcapsule",
                name="Group",
                project=project,
                channel_ids=[2, 5],
                pattern_id=3,
                pattern_length_steps=16,
                preview_wav=preview,
            )
            library = CapsuleLibrary(root / "library", root / "index.sqlite3")
            library.reindex()

            row = next(item for item in library.list() if item["id"] == capsule.manifest.id)
            notes = json.loads(row["note_preview"])

            self.assertEqual({note[3] for note in notes}, {0, 1})

    def test_favorite_filter_and_explicit_sorting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library_dir = root / "library"
            preview = root / "preview.wav"
            write_silence(preview)
            project = fixture_project()
            created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
            with patch("soundcapsule.capsule.datetime") as capsule_datetime:
                capsule_datetime.now.side_effect = [
                    created_at,
                    created_at + timedelta(seconds=1),
                ]
                alpha = Capsule.build(
                    library_dir / "Alpha.flcapsule",
                    name="Alpha",
                    project=project,
                    channel_ids=[2],
                    pattern_id=3,
                    pattern_length_steps=16,
                    preview_wav=preview,
                )
                Capsule.build(
                    library_dir / "Beta.flcapsule",
                    name="Beta",
                    project=project,
                    channel_ids=[2],
                    pattern_id=3,
                    pattern_length_steps=16,
                    preview_wav=preview,
                )
            library = CapsuleLibrary(library_dir, root / "index.sqlite3")
            library.reindex()
            library.record_use(alpha.manifest.id)
            library.record_use(alpha.manifest.id)
            library.set_favorite(alpha.manifest.id, True)

            self.assertEqual([row["name"] for row in library.list()], ["Beta", "Alpha"])
            self.assertEqual(
                [row["name"] for row in library.list(favorites_only=True)],
                ["Alpha"],
            )
            self.assertEqual(
                [row["name"] for row in library.list(sort_by="name", descending=False)],
                ["Alpha", "Beta"],
            )
            self.assertEqual(
                [row["name"] for row in library.list(sort_by="name", descending=True)],
                ["Beta", "Alpha"],
            )
            by_uses = library.list(sort_by="uses", descending=True)
            self.assertEqual(by_uses[0]["name"], "Alpha")
            self.assertEqual(by_uses[0]["use_count"], 2)
            with self.assertRaisesRegex(ValueError, "sort_by"):
                library.list(sort_by="favorite")


if __name__ == "__main__":
    unittest.main()
