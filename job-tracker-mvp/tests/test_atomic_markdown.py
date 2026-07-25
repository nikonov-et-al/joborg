from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jobtracker.markdown import (
    AtomicWriteError,
    append_to_markdown_body,
    atomic_write_text,
    read_markdown_with_frontmatter,
    update_markdown_frontmatter,
    write_markdown_with_frontmatter,
)


class AtomicMarkdownWriteTests(unittest.TestCase):
    def test_successful_new_direct_text_write_preserves_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "nested" / "notes.md"
            content = "Grüße aus Köln — 申请\n"

            atomic_write_text(destination, content)

            self.assertEqual(destination.read_text(encoding="utf-8"), content)
            self.assertEqual(list(destination.parent.iterdir()), [destination])

    def test_successful_direct_text_replacement_replaces_existing_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "job.md"
            destination.write_bytes(b"original bytes\r\n")

            atomic_write_text(destination, "replacement\n")

            self.assertEqual(destination.read_text(encoding="utf-8"), "replacement\n")
            self.assertNotIn(b"original", destination.read_bytes())
            self.assertEqual(list(destination.parent.iterdir()), [destination])

    def test_success_replaces_from_sibling_temp_and_leaves_no_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "job.md"
            original_replace = Path.replace
            replacements: list[tuple[Path, Path]] = []

            def record_replace(source: Path, target: Path) -> Path:
                replacements.append((source, Path(target)))
                return original_replace(source, target)

            with (
                patch.object(Path, "replace", autospec=True, side_effect=record_replace),
                patch("jobtracker.markdown.os.fsync", wraps=os.fsync) as fsync,
            ):
                write_markdown_with_frontmatter(
                    destination,
                    {"company": "Example", "status": "applied"},
                    "First line\n\nSecond line",
                )

            self.assertEqual(len(replacements), 1)
            fsync.assert_called_once()
            source, target = replacements[0]
            self.assertEqual(source.parent, destination.parent)
            self.assertNotEqual(source, destination)
            self.assertEqual(target, destination)
            self.assertEqual(
                read_markdown_with_frontmatter(destination),
                (
                    {"company": "Example", "status": "applied"},
                    "First line\n\nSecond line\n",
                ),
            )
            self.assertEqual(list(destination.parent.iterdir()), [destination])

    def test_temporary_file_creation_failure_reports_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "job.md"

            with patch(
                "jobtracker.markdown.NamedTemporaryFile",
                side_effect=OSError("injected temporary file creation failure"),
            ):
                with self.assertRaises(AtomicWriteError) as caught:
                    atomic_write_text(destination, "new content")

            self.assertEqual(caught.exception.path, destination)
            self.assertEqual(caught.exception.stage, "temporary_file_creation")
            self.assertEqual(
                str(caught.exception),
                f"Could not create a temporary file for {destination}",
            )
            self.assertFalse(destination.exists())
            self.assertEqual(list(destination.parent.iterdir()), [])

    def test_write_failure_preserves_existing_destination_and_cleans_temp(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "job.md"
            original = b"original bytes\r\n"
            destination.write_bytes(original)
            temporary_file = tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=destination.parent,
                delete=False,
            )

            with (
                patch("jobtracker.markdown.NamedTemporaryFile", return_value=temporary_file),
                patch.object(
                    temporary_file,
                    "write",
                    side_effect=OSError("injected write failure"),
                ),
            ):
                with self.assertRaises(AtomicWriteError) as caught:
                    atomic_write_text(destination, "changed")

            self.assertEqual(caught.exception.stage, "write")
            self.assertEqual(destination.read_bytes(), original)
            self.assertEqual(list(destination.parent.iterdir()), [destination])

    def test_flush_and_fsync_failures_preserve_existing_destination_and_clean_temp(self) -> None:
        for operation in ("flush", "fsync"):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as temp_dir:
                destination = Path(temp_dir) / "job.md"
                original = b"original bytes\r\n"
                destination.write_bytes(original)
                temporary_file = tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=destination.parent,
                    delete=False,
                )
                if operation == "flush":
                    failure_patch = patch.object(
                        temporary_file,
                        "flush",
                        side_effect=OSError("injected flush failure"),
                    )
                else:
                    failure_patch = patch(
                        "jobtracker.markdown.os.fsync",
                        side_effect=OSError("injected fsync failure"),
                    )

                with (
                    patch("jobtracker.markdown.NamedTemporaryFile", return_value=temporary_file),
                    failure_patch,
                ):
                    with self.assertRaises(AtomicWriteError) as caught:
                        atomic_write_text(destination, "changed")

                self.assertEqual(caught.exception.stage, "flush")
                self.assertEqual(destination.read_bytes(), original)
                self.assertEqual(list(destination.parent.iterdir()), [destination])

    def test_replace_failure_preserves_existing_destination_and_cleans_temp(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "job.md"
            original = b"existing bytes\r\nremain exact\r\n"
            destination.write_bytes(original)
            original_replace = Path.replace

            def fail_destination_replace(source: Path, target: Path) -> Path:
                target = Path(target)
                if source.parent == destination.parent and source != destination and target == destination:
                    raise OSError("injected replace failure")
                return original_replace(source, target)

            with patch.object(Path, "replace", autospec=True, side_effect=fail_destination_replace):
                with self.assertRaises(AtomicWriteError) as caught:
                    write_markdown_with_frontmatter(
                        destination,
                        {"company": "Changed"},
                        "Changed body",
                    )

            self.assertEqual(caught.exception.stage, "replace")
            self.assertEqual(
                str(caught.exception),
                f"Could not replace the destination file for {destination}",
            )
            self.assertEqual(destination.read_bytes(), original)
            self.assertEqual(list(destination.parent.iterdir()), [destination])

    def test_replace_failure_for_new_destination_leaves_no_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "nested" / "job.md"
            original_replace = Path.replace

            def fail_destination_replace(source: Path, target: Path) -> Path:
                target = Path(target)
                if source.parent == destination.parent and source != destination and target == destination:
                    raise OSError("injected replace failure")
                return original_replace(source, target)

            with patch.object(Path, "replace", autospec=True, side_effect=fail_destination_replace):
                with self.assertRaises(AtomicWriteError) as caught:
                    write_markdown_with_frontmatter(
                        destination,
                        {"company": "Example"},
                        "Body",
                    )

            self.assertEqual(caught.exception.stage, "replace")
            self.assertFalse(destination.exists())
            self.assertTrue(destination.parent.is_dir())
            self.assertEqual(list(destination.parent.iterdir()), [])

    def test_each_write_uses_unique_sibling_temp_without_touching_collision(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "job.md"
            collision = destination.parent / ".job.md.existing.tmp"
            collision.write_bytes(b"sentinel")
            original_replace = Path.replace
            temporary_paths: list[Path] = []

            def record_replace(source: Path, target: Path) -> Path:
                temporary_paths.append(source)
                return original_replace(source, target)

            with patch.object(Path, "replace", autospec=True, side_effect=record_replace):
                atomic_write_text(destination, "first")
                atomic_write_text(destination, "second")

            self.assertEqual(len(temporary_paths), 2)
            self.assertEqual(len(set(temporary_paths)), 2)
            self.assertTrue(all(path.parent == destination.parent for path in temporary_paths))
            self.assertTrue(all(path != collision for path in temporary_paths))
            self.assertEqual(collision.read_bytes(), b"sentinel")
            self.assertEqual(destination.read_text(encoding="utf-8"), "second")
            self.assertEqual(set(destination.parent.iterdir()), {collision, destination})

    def test_update_and_append_keep_existing_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "job.md"
            write_markdown_with_frontmatter(
                destination,
                {"company": "Example", "status": "draft"},
                "Original body",
            )

            update_markdown_frontmatter(destination, {"status": "applied"})
            append_to_markdown_body(destination, "Added note")

            self.assertEqual(
                read_markdown_with_frontmatter(destination),
                (
                    {"company": "Example", "status": "applied"},
                    "Original body\n\nAdded note\n",
                ),
            )
            self.assertEqual(list(destination.parent.iterdir()), [destination])


if __name__ == "__main__":
    unittest.main()
