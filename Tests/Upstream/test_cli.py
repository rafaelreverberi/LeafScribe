"""CLI structure tests (no live export required)."""

from __future__ import annotations

import unittest
from pathlib import Path

from beook_export.cli import _format_book_metadata, build_parser
from beook_export.extractor import BookContent


class TestCliParser(unittest.TestCase):
    def test_export_all_subcommand_exists(self):
        parser = build_parser()
        args = parser.parse_args(["export-all", "--output-root", "/tmp/out"])
        self.assertEqual(args.command, "export-all")
        self.assertEqual(args.output_root, Path("/tmp/out"))

    def test_export_subcommand_exists(self):
        parser = build_parser()
        args = parser.parse_args(["export", "978-test", "-o", "book.epub"])
        self.assertEqual(args.command, "export")
        self.assertEqual(args.reference, "978-test")

    def test_inspect_subcommand_exists(self):
        parser = build_parser()
        args = parser.parse_args(["inspect", "/tmp/book.epub", "--reference", "978-test"])
        self.assertEqual(args.command, "inspect")
        self.assertEqual(args.epub, "/tmp/book.epub")
        self.assertEqual(args.reference, "978-test")

    def test_format_book_metadata_lines(self):
        book = BookContent(
            reference="978-test",
            title="Test",
            language="de",
            spine_filenames=[],
            files={},
            media_types={},
            pk_to_filename={},
            authors=("Ada Lovelace",),
            isbn="9783035520941",
            edition="2",
            series="Demo Series",
            publisher="hep Verlag AG",
            cover_image_path="OEBPS/cover.png",
        )
        lines = _format_book_metadata(book)
        self.assertIn("Authors: Ada Lovelace", lines)
        self.assertIn("ISBN: 9783035520941", lines)
        self.assertIn("Edition: 2", lines)
        self.assertIn("Series: Demo Series", lines)
        self.assertIn("Publisher: hep Verlag AG", lines)
        self.assertIn("Cover: cover.png", lines)


if __name__ == "__main__":
    unittest.main()
