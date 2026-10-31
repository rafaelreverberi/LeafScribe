"""Tests for EPUB inspection (structure and completeness)."""

from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from beook_export.database import connect
from beook_export.discovery import resolve_db_path
from beook_export.epub import write_epub
from beook_export.extractor import extract_book
from beook_export.inspect import inspect_epub


class TestInspectEpub(unittest.TestCase):
    REFERENCE = "978-3-0355-2094-1"

    @classmethod
    def setUpClass(cls):
        try:
            cls.db_path = resolve_db_path(profile="1")
        except FileNotFoundError:
            cls.db_path = None

    def _export_sample(self) -> tuple[Path, int]:
        conn = connect(self.db_path)
        try:
            book = extract_book(conn, self.REFERENCE)
        finally:
            conn.close()
        tmp = tempfile.NamedTemporaryFile(suffix=".epub", delete=False)
        tmp.close()
        path = Path(tmp.name)
        write_epub(book, str(path))
        return path, len(book.spine_filenames)

    def setUp(self):
        if self.db_path is None:
            self.skipTest("Beook profile 1 database not available")

    def test_live_bwl_export_passes_inspection(self):
        path, spine = self._export_sample()
        try:
            ok, report = inspect_epub(path, self.REFERENCE, spine)
            self.assertTrue(ok, report)
            self.assertIn("zero localhost", report)
            self.assertIn("all xhtml properly closed", report)
            self.assertIn("all xhtml parses as XML", report)
            self.assertIn("EPUB 3 nav document present", report)
            self.assertIn("EPUB 2 NCX navigation present", report)
            self.assertIn("EPUB 2 guide landmarks present", report)
            self.assertIn("Dublin Core creators declared", report)
            self.assertIn("ISBN identifier declared", report)
            self.assertIn("content.opf parses as XML", report)
            self.assertIn("content tables marked", report)
            self.assertIn("reader stylesheet packaged", report)
        finally:
            path.unlink(missing_ok=True)

    def test_detects_localhost_in_fake_epub(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
            zf.writestr(
                "OEBPS/bad.xhtml",
                b'<html><body><a href="http://localhost/foo">x</a></body></html>',
            )
        tmp = tempfile.NamedTemporaryFile(suffix=".epub", delete=False)
        tmp.write(buf.getvalue())
        tmp.close()
        path = Path(tmp.name)
        try:
            ok, report = inspect_epub(path, "test")
            self.assertFalse(ok)
            self.assertIn("localhost", report)
        finally:
            path.unlink(missing_ok=True)

    def test_detects_malformed_xhtml_in_fake_epub(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
            zf.writestr(
                "OEBPS/bad.xhtml",
                b"<html><head><link href=\"style.css\"></head><body></body></html>",
            )
        tmp = tempfile.NamedTemporaryFile(suffix=".epub", delete=False)
        tmp.write(buf.getvalue())
        tmp.close()
        path = Path(tmp.name)
        try:
            ok, report = inspect_epub(path, "test")
            self.assertFalse(ok)
            self.assertIn("XML parse failure", report)
        finally:
            path.unlink(missing_ok=True)

    def test_detects_unmarked_content_tables(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
            zf.writestr(
                "OEBPS/bad.xhtml",
                b'<html><body><table class="Tabelle-gruen"><tr><td>A</td></tr></table></body></html>',
            )
            zf.writestr("OEBPS/beook-export-reader.css", b"table {}")
        tmp = tempfile.NamedTemporaryFile(suffix=".epub", delete=False)
        tmp.write(buf.getvalue())
        tmp.close()
        path = Path(tmp.name)
        try:
            ok, report = inspect_epub(path, "test")
            self.assertFalse(ok)
            self.assertIn("beook-export-table", report)
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
