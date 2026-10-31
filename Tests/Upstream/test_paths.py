"""Tests for output path helpers."""

from __future__ import annotations

import unittest
from pathlib import Path

from beook_export.paths import (
    book_folder_name,
    book_output_dir,
    reference_slug,
    resolve_book_output_path,
    title_slug,
)


class TestOutputPaths(unittest.TestCase):
    def test_reference_slug(self):
        self.assertEqual(reference_slug("978-3-0355-1741-5"), "978-3-0355-1741-5")

    def test_title_slug_from_product_name(self):
        self.assertEqual(
            title_slug("Chemie für das Gymnasium"),
            "chemie-fuer-das-gymnasium",
        )

    def test_book_folder_name(self):
        name = book_folder_name("978-3-0355-1741-5", "Chemie für das Gymnasium")
        self.assertEqual(name, "978-3-0355-1741-5-chemie-fuer-das-gymnasium")

    def test_resolve_default_output_path(self):
        path = resolve_book_output_path(
            "978-3-0355-1741-5",
            "Chemie für das Gymnasium",
            output_root=Path("/tmp/beook-test-output"),
        )
        self.assertEqual(
            path,
            Path(
                "/tmp/beook-test-output/"
                "978-3-0355-1741-5-chemie-fuer-das-gymnasium/"
                "chemie-fuer-das-gymnasium.epub"
            ),
        )

    def test_book_output_dir(self):
        folder = book_output_dir("978-test", "Betriebswirtschaftslehre")
        self.assertEqual(folder, Path("output/978-test-betriebswirtschaftslehre"))


if __name__ == "__main__":
    unittest.main()
