from __future__ import annotations

import unittest

from jobtracker.models import format_date_display, normalize_date_input


class DateDisplayTests(unittest.TestCase):
    def test_formats_iso_date_and_datetime_for_display(self) -> None:
        self.assertEqual(format_date_display("2026-06-25"), "25-06-2026")
        self.assertEqual(format_date_display("2026-07-15 20:42"), "15-07-2026 20:42")

    def test_keeps_empty_and_legacy_values_safe(self) -> None:
        self.assertEqual(format_date_display(""), "")
        self.assertEqual(format_date_display("unknown"), "unknown")

    def test_normalizes_european_and_iso_cli_input(self) -> None:
        self.assertEqual(normalize_date_input("25-06-2026"), "2026-06-25")
        self.assertEqual(normalize_date_input("2026-06-25"), "2026-06-25")
        with self.assertRaisesRegex(ValueError, "DD-MM-YYYY"):
            normalize_date_input("06/25/2026")


if __name__ == "__main__":
    unittest.main()
