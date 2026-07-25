from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jobtracker.markdown import read_markdown_with_frontmatter


class ReadMarkdownWithFrontmatterTests(unittest.TestCase):
    def read(self, content: str) -> tuple[dict, str]:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "job.md"
            path.write_text(content, encoding="utf-8", newline="")
            return read_markdown_with_frontmatter(path)

    def test_url_containing_delimiter_sequence_remains_intact(self) -> None:
        metadata, body = self.read(
            "---\n"
            "url: https://example.test/jobs/Ground-Segment---AI-Software-Engineer\n"
            "source: careers\n"
            "status: applied\n"
            "---\n\n"
            "# Job\n\nDescription preserved.\n"
        )

        self.assertEqual(
            metadata,
            {
                "url": "https://example.test/jobs/Ground-Segment---AI-Software-Engineer",
                "source": "careers",
                "status": "applied",
            },
        )
        self.assertEqual(body, "# Job\n\nDescription preserved.\n")

    def test_trimmed_complete_line_is_closing_delimiter(self) -> None:
        metadata, body = self.read(
            "---\r\n"
            "title: Example\r\n"
            "note: value---still-yaml\r\n"
            "  ---  \r\n\r\n"
            "First line\r\nSecond line\r\n"
        )

        self.assertEqual(metadata, {"title": "Example", "note": "value---still-yaml"})
        self.assertEqual(body, "First line\nSecond line\n")

    def test_unclosed_frontmatter_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unclosed frontmatter"):
            self.read("---\ntitle: Example\n# Body without closing delimiter\n")

    def test_non_mapping_frontmatter_is_rejected(self) -> None:
        for frontmatter in ("- one\n- two\n", "plain scalar\n"):
            with self.subTest(frontmatter=frontmatter):
                with self.assertRaisesRegex(ValueError, "Invalid frontmatter object"):
                    self.read(f"---\n{frontmatter}---\nBody\n")

    def test_markdown_without_frontmatter_is_unchanged(self) -> None:
        content = "# Plain Markdown\n\nText with --- inside.\n"

        metadata, body = self.read(content)

        self.assertEqual(metadata, {})
        self.assertEqual(body, content)

    def test_body_content_and_trailing_newlines_are_preserved(self) -> None:
        metadata, body = self.read(
            "---\n"
            "title: Example\n"
            "---\n\n\n"
            "# Heading\n\n"
            "Paragraph with --- inline.\n\n"
        )

        self.assertEqual(metadata, {"title": "Example"})
        self.assertEqual(body, "# Heading\n\nParagraph with --- inline.\n\n")


if __name__ == "__main__":
    unittest.main()
