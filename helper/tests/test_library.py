from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from soundcapsule.capsule import Capsule
from soundcapsule.library import CapsuleLibrary
from test_flp import fixture_project, write_silence


class LibraryTests(unittest.TestCase):
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
                {"Same-Name.flcapsule", "Same-Name-2.flcapsule"},
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
            self.assertFalse((library_dir / "Lead.wav").exists())
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
            self.assertAlmostEqual(note_preview[0][0], source_note.position / (4 * project.ppq), places=6)

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
