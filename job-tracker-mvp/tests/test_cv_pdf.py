from __future__ import annotations

import io
import unittest

from pypdf import PdfReader

from jobtracker.pdf import FONT_BOLD, _CV_LAYOUTS, _cv_styles, pdf_page_count, render_cv_pdf, safe_filename


class CvPdfTests(unittest.TestCase):
    def test_safe_filename_handles_mixed_separators_and_reserved_names(self) -> None:
        self.assertEqual(safe_filename(r"..\..\nested/../report.pdf"), "report.pdf")
        self.assertEqual(safe_filename("CON.txt"), "_CON.txt")
        self.assertEqual(safe_filename("LPT1"), "_LPT1")

    def test_render_cv_pdf_supports_minimal_markdown_and_one_page(self) -> None:
        pdf = render_cv_pdf(
            """# Summary
I build **reliable** software and write *clear* docs with [proof](https://example.com).

## Experience
**Example GmbH** - Backend Engineer
- Shipped factual work

## Education
**Example University** - MSc Computer Science

## Skills
- **ML/Data:** Python, pandas
- **Backend:** FastAPI

Unsupported table-ish text | should | flatten.
""",
            applicant_name="Jane Example",
            job_title="Backend Engineer",
        )

        self.assertEqual(pdf_page_count(pdf), 1)
        text = PdfReader(io.BytesIO(pdf)).pages[0].extract_text() or ""
        self.assertIn("Jane Example", text)
        self.assertIn("Backend Engineer", text)
        self.assertIn("Summary", text)
        self.assertIn("Experience", text)
        self.assertIn("Education", text)
        self.assertIn("Skills", text)
        self.assertIn("reliable", text)
        self.assertIn("clear", text)
        self.assertIn("proof", text)
        self.assertIn("Example GmbH", text)
        self.assertIn("Example University", text)
        self.assertIn("ML/Data", text)
        self.assertIn("Backend", text)
        self.assertIn("flatten", text)
        self.assertLess(text.index("Summary"), text.index("Experience"))
        self.assertLess(text.index("Experience"), text.index("Education"))
        self.assertLess(text.index("Education"), text.index("Skills"))

    def test_cv_heading_style_is_bold_13pt(self) -> None:
        for layout in _CV_LAYOUTS:
            heading = _cv_styles(layout)["heading"]
            self.assertEqual(heading.fontName, FONT_BOLD)
            self.assertEqual(heading.fontSize, 13)

    def test_render_cv_pdf_rejects_overflow(self) -> None:
        markdown = "\n".join(
            f"- Bullet {index}: enough text to push the CV over one page when repeated."
            for index in range(80)
        )

        with self.assertRaisesRegex(ValueError, "CV content does not fit one page"):
            render_cv_pdf(markdown, applicant_name="Jane Example", job_title="Backend Engineer")


if __name__ == "__main__":
    unittest.main()
