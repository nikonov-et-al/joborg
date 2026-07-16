from __future__ import annotations

import io
import unittest

from pypdf import PdfReader
from reportlab.pdfbase import pdfmetrics

from jobtracker.pdf import (
    FONT_BOLD,
    FONT_ITALIC,
    FONT_REGULAR,
    pdf_page_count,
    register_pdf_fonts,
    render_basic_pdf,
    safe_filename,
    validate_pdf_page_count,
)


class PdfFoundationTests(unittest.TestCase):
    def test_font_registration_is_idempotent(self) -> None:
        register_pdf_fonts()
        first = tuple(
            pdfmetrics.getFont(name).fontName for name in (FONT_REGULAR, FONT_BOLD, FONT_ITALIC)
        )

        register_pdf_fonts()
        second = tuple(
            pdfmetrics.getFont(name).fontName for name in (FONT_REGULAR, FONT_BOLD, FONT_ITALIC)
        )

        self.assertEqual(first, second)

    def test_basic_pdf_keeps_text_order_and_one_page(self) -> None:
        pdf = render_basic_pdf(
            "Überblick",
            [
                "Grüße aus Köln.",
                "Работа с данными и отчетами.",
            ],
        )

        self.assertEqual(pdf_page_count(pdf), 1)
        validate_pdf_page_count(pdf, 1)

        text = PdfReader(io.BytesIO(pdf)).pages[0].extract_text()
        self.assertIsNotNone(text)
        assert text is not None
        self.assertIn("Überblick", text)
        self.assertIn("Grüße aus Köln.", text)
        self.assertIn("Работа с данными и отчетами.", text)
        self.assertLess(text.index("Überblick"), text.index("Grüße aus Köln."))
        self.assertLess(text.index("Grüße aus Köln."), text.index("Работа с данными и отчетами."))

    def test_safe_filename_sanitizes_path_traversal(self) -> None:
        self.assertEqual(safe_filename(r"..\Prüfung:cv?.pdf"), "Prüfung_cv_.pdf")


if __name__ == "__main__":
    unittest.main()
