"""Tests for EPUB packaging and URL rewriting."""

from __future__ import annotations

import io
import re
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from beook_export.database import (
    BookMetadata,
    Note,
    Resource,
    connect,
    get_book_metadata,
    list_books,
)
from beook_export.discovery import DEFAULT_BEOOK_ROOT, resolve_db_path
from beook_export.epub import NAV_PATH, NCX_PATH, build_epub_bytes, write_epub
from beook_export.extractor import (
    BookContent,
    READER_STYLESHEET,
    READER_STYLESHEET_BYTES,
    READER_STYLESHEET_PATH,
    _annotations_xhtml,
    extract_book,
    _build_pk_to_filename,
    _consolidate_glossary_pages,
    _consolidate_spine_pages,
    _is_title_cover_identifier,
    _rewrite_css,
    _rewrite_xhtml,
)
from beook_export.naming import oebps_name


class TestDiscovery(unittest.TestCase):
    def test_default_beook_root_targets_ionesoft(self):
        self.assertIn("ionesoft/beook", str(DEFAULT_BEOOK_ROOT))


class TestNaming(unittest.TestCase):
    def test_bare_identifier_gets_xhtml_extension(self):
        self.assertEqual(oebps_name("id1", "application/xhtml+xml"), "id1.xhtml")


class TestRewriteXhtml(unittest.TestCase):
    _EMPTY_LINK_MAPS = ({}, {}, {})

    def _rewrite(self, content: bytes, pk_map: dict | None = None, **kwargs) -> bytes:
        pk_map = pk_map or {}
        anchor_map = kwargs.get("anchor_map", {})
        topic_access = kwargs.get("topic_access", {})
        topic_pk = kwargs.get("topic_pk", {})
        return _rewrite_xhtml(content, pk_map, topic_pk, anchor_map, topic_access)

    def test_rewrites_link_with_data_original(self):
        content = (
            b'<link href="http://localhost/database/resource/pk/42" '
            b'data-original="style.css" rel="stylesheet" type="text/css" />'
        )
        result = self._rewrite(content, {42: "style.css"}).decode()
        self.assertIn('href="style.css"', result)
        self.assertIn("/>", result)
        self.assertNotIn("localhost", result)
        self.assertNotIn("data-original", result)

    def test_rewrites_img_with_data_original(self):
        content = b'<img src="http://localhost/database/resource/pk/66" data-original="18.jpg" />'
        result = self._rewrite(content, {66: "18.jpg"}).decode()
        self.assertIn('src="18.jpg"', result)
        self.assertIn("/>", result)
        self.assertNotIn("localhost", result)

    def test_rewritten_head_links_remain_xml_parseable(self):
        content = b"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head>
    <link href="http://localhost/database/resource/pk/42" data-original="style.css" rel="stylesheet" type="text/css" />
  </head>
  <body><img src="http://localhost/database/resource/pk/66" data-original="image.png" /></body>
</html>"""
        result = self._rewrite(content, {42: "style.css", 66: "image.png"})
        ElementTree.fromstring(result)
        text = result.decode()
        self.assertIn('<link href="style.css" rel="stylesheet" type="text/css" />', text)
        self.assertIn('<img src="image.png" />', text)
        self.assertIn(READER_STYLESHEET, text)

    def test_rewrites_html_named_entities_to_xml_numeric_entities(self):
        content = b"<html><body><p>foo&nbsp;bar &amp; baz</p></body></html>"
        result = self._rewrite(content)
        ElementTree.fromstring(result)
        self.assertIn(b"foo&#160;bar &amp; baz", result)

    def test_removes_void_closing_tags_after_rewrite(self):
        content = b"""<html>
<head><link rel="stylesheet" href="http://localhost/database/resource/pk/42" data-original="style.css"></link></head>
<body><img src="http://localhost/database/resource/pk/66" data-original="image.png"></img></body>
</html>"""
        result = self._rewrite(content, {42: "style.css", 66: "image.png"})
        ElementTree.fromstring(result)
        self.assertNotIn(b"</link>", result)
        self.assertNotIn(b"</img>", result)

    def test_marks_image_tables_for_stacked_reader_layout(self):
        content = b"""<html><body>
<table class="Tabelle-ohne-Rahmen-overflow"><tr><td><img src="http://localhost/database/resource/pk/66" data-original="image.png" /></td></tr></table>
</body></html>"""
        result = self._rewrite(content, {66: "image.png"})
        ElementTree.fromstring(result)
        self.assertIn(b"beook-export-table", result)
        self.assertIn(b"beook-export-image-grid", result)

    def test_marks_colored_data_tables(self):
        content = b"""<html><body>
<table class="Tabelle-gruen TableOverride-1"><tr><td class="Tabellenkopf">A</td><td>B</td></tr>
<tr><td>1</td><td>2</td></tr></table>
</body></html>"""
        result = self._rewrite(content, {})
        ElementTree.fromstring(result)
        self.assertIn(b"beook-export-table", result)
        self.assertIn(b"beook-export-data-table", result)

    def test_marks_nested_invisible_data_tables(self):
        content = b"""<html><body>
<table class="Tabelle-gruen"><tr><td>
<table class="unsichtbar"><tr><td class="Tabellentext">A</td><td class="Tabellentext">1</td></tr></table>
</td></tr></table>
</body></html>"""
        result = self._rewrite(content, {})
        ElementTree.fromstring(result)
        self.assertEqual(result.count(b"beook-export-table"), 2)
        self.assertEqual(result.count(b"beook-export-data-table"), 2)

    def test_marks_chart_wrapper_tables(self):
        content = b"""<html><body>
<table class="Tabelle-blau TableOverride-1"><tr><td><img src="http://localhost/database/resource/pk/66" data-original="chart.png" /></td></tr></table>
</body></html>"""
        result = self._rewrite(content, {66: "chart.png"})
        ElementTree.fromstring(result)
        self.assertIn(b"beook-export-table", result)
        self.assertIn(b"beook-export-chart-table", result)
        self.assertNotIn(b"beook-export-data-table", result)

    def test_consolidates_short_spine_pages(self):
        def page(title: str, prev_href: str = "", next_href: str = "") -> bytes:
            prev = f'<a class="pageNavigation-left" href="{prev_href}">Prev</a>' if prev_href else ""
            nxt = f'<a class="pageNavigation-right" href="{next_href}">Next</a>' if next_href else ""
            nav = f'<div class="pageNavigationTable"><nav>{prev}{nxt}</nav></div>'
            return f"<html><body><h2>{title}</h2><p>{title} text.</p>{nav}</body></html>".encode()

        files = {
            "OEBPS/BWL99_split_001.xhtml": page("One", next_href="BWL99_split_002.xhtml"),
            "OEBPS/BWL99_split_002.xhtml": page("Two", prev_href="BWL99_split_001.xhtml"),
        }
        spine = list(files.keys())
        new_spine, merged_files, redirect = _consolidate_spine_pages(spine, files)
        self.assertEqual(new_spine, ["OEBPS/BWL99_split_001.xhtml"])
        self.assertEqual(redirect["OEBPS/BWL99_split_002.xhtml"], "OEBPS/BWL99_split_001.xhtml")
        merged = merged_files["OEBPS/BWL99_split_001.xhtml"].decode()
        self.assertIn("One", merged)
        self.assertIn("Two", merged)
        self.assertIn("beook-export-spine-join", merged)
        ElementTree.fromstring(merged_files["OEBPS/BWL99_split_001.xhtml"])

    def test_keeps_data_tables_out_of_image_grid_layout(self):
        content = b"""<html><body>
<table class="Tabelle-Rahmen-ou-overflow"><tr><td><img src="http://localhost/database/resource/pk/66" data-original="a.jpg" /></td><td>1</td></tr>
<tr><td>2</td><td>3</td></tr><tr><td>4</td><td>5</td></tr><tr><td>6</td><td>7</td></tr></table>
</body></html>"""
        result = self._rewrite(content, {66: "a.jpg"})
        ElementTree.fromstring(result)
        self.assertIn(b"beook-export-data-table", result)
        self.assertNotIn(b"beook-export-image-grid", result)

    def test_rewrites_topic_pk_links(self):
        content = b'<a href="http://localhost/pk/topic/2">Vorwort</a>'
        result = self._rewrite(content, topic_pk={2: "BWL00_split_001.xhtml"}).decode()
        self.assertIn('href="BWL00_split_001.xhtml"', result)

    def test_rewrites_anchor_cross_links(self):
        content = (
            b'<a class="cross-link" '
            b'href="http://localhost/course/BWL/courseReference/978-3-0355-2094-1/anchor/BWL01_idTextAnchor010">'
            b"vgl. NWA</a>"
        )
        result = self._rewrite(
            content,
            anchor_map={"BWL01_idTextAnchor010": "BWL01_split_012.xhtml"},
        ).decode()
        self.assertIn('href="BWL01_split_012.xhtml#BWL01_idTextAnchor010"', result)

    def test_rewrites_issue_topic_cross_links(self):
        content = (
            b'<a class="cross-link" '
            b'href="http://localhost/course/BWL/issueId/BWL05/topicId/5.2">'
            b"Beschaffung</a>"
        )
        result = self._rewrite(
            content,
            topic_access={("BWL05", "5.2"): "BWL05_split_003.xhtml"},
        ).decode()
        self.assertIn('href="BWL05_split_003.xhtml"', result)

    def test_rewrites_css_pk_urls(self):
        content = b".pageNavigation-left:before { content: url(26); }"
        result = _rewrite_css(content, {26: "assets_Navi-Pfeil-links.svg"}, "hep_pageNavigation.css")
        self.assertIn(b"url(assets_Navi-Pfeil-links.svg)", result)

    def test_rewrites_css_literal_asset_fallback_urls(self):
        content = b"@font-face { src: url(25), url('xassets/Calluna-Semibold.otf'); }"
        result = _rewrite_css(
            content,
            {25: "assets_Calluna-Semibold.otf"},
            "hep_fonts_calluna.css",
        )
        self.assertIn(b"url(assets_Calluna-Semibold.otf)", result)
        self.assertIn(b"url('assets_Calluna-Semibold.otf')", result)

    def test_removes_unresolved_local_css_imports(self):
        content = b'@import url("xchapter.css"); @import url(GES-SERIES_EXERCISES_4.css);'
        result = _rewrite_css(
            content,
            {1: "xassets/GES-SERIES_EXERCISES_4.css"},
            "xassets/ILPExerciseAdditions.css",
        )
        self.assertNotIn(b"xchapter.css", result)
        self.assertIn(b"@import url(GES-SERIES_EXERCISES_4.css);", result)

    def test_renders_annotations_appendix(self):
        result = _annotations_xhtml(
            [
                Note(
                    title="Reminder",
                    text="Important <formula> & note",
                    type="H",
                    color="yellow",
                    date="2026-07-06",
                    issue_id="CFG09",
                    issue_title="Salze",
                    topic_access="9.1",
                )
            ],
            "de",
        )
        ElementTree.fromstring(result)
        self.assertIn(b"Annotationen", result)
        self.assertIn(b"Markierung", result)
        self.assertIn(b"Thema 9.1", result)
        self.assertIn(b"Important &lt;formula&gt; &amp; note", result)

    def test_neutralizes_dead_web_link_anchors(self):
        content = (
            b'<a class="web-link" href="Preis">das magische Dreieck</a>'
            b'<a class="web-link" href="">S. 101</a>'
        )
        result = self._rewrite(content).decode()
        ElementTree.fromstring(f"<html><body>{result}</body></html>")
        self.assertIn('href="#"', result)
        self.assertNotIn('href="Preis"', result)

    def test_neutralizes_dead_exercise_link_pseudo_anchors(self):
        content = (
            b'<a class="exercise-link" href="# Le pronom possessif">'
            b"Exercices</a>"
        )
        result = self._rewrite(content).decode()
        ElementTree.fromstring(f"<html><body>{result}</body></html>")
        self.assertIn('href="#"', result)
        self.assertNotIn("Le pronom possessif\"", result)

    def test_rewrites_bare_glossary_html_links_only_when_target_exists(self):
        content = (
            b'<a class="glossar-link" href="223.html">term</a>'
            b'<a class="glossar-link" href="6.html">other</a>'
        )
        result = self._rewrite(content, {8073: "id223.xhtml"}).decode()
        self.assertIn('href="id223.xhtml"', result)
        self.assertIn('href="#"', result)
        self.assertNotIn('href="id6.xhtml"', result)

    def test_disambiguates_colliding_resource_filenames_by_pk(self):
        content = (
            b'<img src="http://localhost/database/resource/pk/49" data-original="2.jpg" />'
        )
        result = self._rewrite(content, {49: "2_49.jpg"}).decode()
        self.assertIn('src="2_49.jpg"', result)

    def test_shared_identifier_across_media_types_gets_distinct_typed_filenames(self):
        # Beook reuses bare identifiers like "id223" across unrelated resource
        # kinds (an XHTML glossary page, a plain-text index blob, a font
        # subset). Each must keep its own correct extension/filename instead
        # of inheriting one arbitrary group member's media type.
        resources = [
            Resource(
                pk=4853,
                identifier="id223",
                media_type="text/plain",
                issue_pk=1,
                order=0,
                data=b"a",
                plain_data=None,
            ),
            Resource(
                pk=7519,
                identifier="id223",
                media_type="application/font-woff",
                issue_pk=1,
                order=1,
                data=b"fontbytes",
                plain_data=None,
            ),
            Resource(
                pk=8073,
                identifier="id223",
                media_type="application/xhtml+xml",
                issue_pk=1,
                order=2,
                data=b"<html><body>Glossary entry</body></html>",
                plain_data=None,
            ),
        ]
        pk_to_filename = _build_pk_to_filename(resources)
        self.assertEqual(pk_to_filename[8073], "id223.xhtml")
        self.assertEqual(pk_to_filename[7519], "id223.woff")
        self.assertEqual(pk_to_filename[4853], "id223.txt")

    def test_unwraps_overflow_table_wrappers(self):
        content = b"""<html><body><div class="overflow-container"><table class="Tabelle-Rahmen-ou-overflow beook-export-data-table"><tr><td>A</td></tr></table></div><!-- end of overflow-container --></body></html>"""
        result = _rewrite_xhtml(content, {}, {}, {}, {}).decode()
        self.assertNotIn("overflow-container", result)
        self.assertNotIn("end of overflow-container", result)
        self.assertIn("beook-export-data-table", result)

    def test_marks_lernziele_panel(self):
        content = b"""<html><body><div class="idGenObjectStyleOverride-2"><h2 class="chapter-Lernziele">Lernziele</h2></div></body></html>"""
        result = _rewrite_xhtml(content, {}, {}, {}, {}).decode()
        self.assertIn("beook-export-lernziele-panel", result)

    def test_marks_chart_panels_and_page_breaks(self):
        panels = "".join(
            f'<div class="idGenObjectLayout-2"><div class="Einfacher-Grafikrahmen _idGenObjectStyleOverride-3">'
            f'<p class="Grafiken-Titel">{label}</p></div></div>'
            for label in "ABCDEFG"
        )
        content = f"<html><body>{panels}</body></html>".encode()
        result = _rewrite_xhtml(content, {}, {}, {}, {}).decode()
        self.assertIn("beook-export-chart-panel", result)
        self.assertIn("beook-export-chart-page-break", result)

    def test_trims_leading_wrapper_junk(self):
        content = b"""<html><body><div class="idGenObjectStyleOverride-3">
			
			
			<h2 class="chaptera">Title</h2></div></body></html>"""
        result = _rewrite_xhtml(content, {}, {}, {}, {}).decode()
        self.assertIn("<h2 class=\"chaptera\">Title</h2>", result)
        self.assertLess(result.count("\n\n\n"), 3)

    def test_strips_empty_layout_divs(self):
        content = b"""<html><body><div class="idGenObjectLayout-2">
			
		</div><p>Hello</p></body></html>"""
        result = _rewrite_xhtml(content, {}, {}, {}, {}).decode()
        self.assertNotIn("idGenObjectLayout-2", result)
        self.assertIn("Hello", result)

    def test_reflows_pdf2html_pages(self):
        content = (
            '<html><head></head><body>\n'
            '<div id="page-container"><div id="pf1" class="pf w0 h0">\n'
            '<div class="pc pc1 w0 h0"><img src="bg.svg" class="bf" alt="" />\n'
            '<div class="t m0 x0 h2 y1 ff2 fs1 fc0">G\u00fcn<span class="_ _1"></span>ter Baars</div>\n'
            '<div class="t m0 x0 h2 y2 ff2 fs1 fc0">Gymnasium</div>\n'
            '</div></div></div>\n'
            '</body></html>'
        ).encode()
        result = self._rewrite(content).decode()
        self.assertIn('class="beook-export-pdf2html-reflow"', result)
        self.assertNotIn('<span class="_ _1"></span>', result)
        self.assertIn("Günter Baars", result)
        self.assertIn(READER_STYLESHEET, result)
        self.assertIn("beook-export-pdf2html-reflow", READER_STYLESHEET_BYTES.decode())

    def test_consolidates_short_pdf2html_spine_pages(self):
        from beook_export.extractor import _consolidate_pdf2html_spine_pages

        def pdf_page(text: str) -> bytes:
            return (
                f'<html><body class="beook-export-pdf2html-reflow">'
                f'<div id="page-container"><div class="pf w0 h0"><div class="pc">'
                f'<div class="t">{text}</div></div></div></div></body></html>'
            ).encode()

        files = {
            "OEBPS/a.xhtml": pdf_page("Title page"),
            "OEBPS/b.xhtml": pdf_page("Impressum " * 20),
            "OEBPS/c.xhtml": pdf_page("Long chapter " * 400),
        }
        spine, merged_files, redirect = _consolidate_pdf2html_spine_pages(
            ["OEBPS/a.xhtml", "OEBPS/b.xhtml", "OEBPS/c.xhtml"],
            dict(files),
        )
        self.assertEqual(spine, ["OEBPS/a.xhtml", "OEBPS/c.xhtml"])
        self.assertEqual(redirect["OEBPS/b.xhtml"], "OEBPS/a.xhtml")
        self.assertIn(b"Impressum", merged_files["OEBPS/a.xhtml"])
        self.assertIn(b"beook-export-spine-join", merged_files["OEBPS/a.xhtml"])


    def test_pdf2html_splits_glued_german_labels(self):
        from beook_export.extractor import _pdf2html_split_glued_words, _pdf2html_polish_html

        self.assertEqual(
            _pdf2html_split_glued_words("BeiträgeJeder Gesellschafter"),
            "Beiträge Jeder Gesellschafter",
        )
        self.assertEqual(
            _pdf2html_split_glued_words("RechtsformBeispieleAnzahl"),
            "Rechtsform Beispiele Anzahl",
        )
        self.assertEqual(
            _pdf2html_split_glued_words("EinzelunternehmenGesellschaftenStiftung"),
            "Einzelunternehmen Gesellschaften Stiftung",
        )
        polished = _pdf2html_polish_html(
            '<p class="beook-export-pdf2html-line">WirkungErläuterungArtikel</p>'
        )
        self.assertIn("Wirkung Erläuterung", polished)
        self.assertIn("Erläuterung Artikel", polished)

    def test_pdf2html_soft_wrap_rejoins_compounds(self):
        from beook_export.extractor import _pdf2html_compact_blocks

        blocks = [
            '<p class="beook-export-pdf2html-line">Gesell</p>',
            '<p class="beook-export-pdf2html-line">schaftsform ist subsidiär.</p>',
        ]
        compacted = _pdf2html_compact_blocks(blocks)
        self.assertEqual(len(compacted), 1)
        self.assertIn("Gesellschaftsform ist subsidiär.", compacted[0])

    def test_pdf2html_merge_includes_superscripts(self):
        from beook_export.database import connect
        from beook_export.discovery import resolve_db_path
        from xml.etree import ElementTree

        conn = connect(resolve_db_path(profile="1"))
        try:
            book = extract_book(conn, "978-3-0355-1741-5")
        finally:
            conn.close()
        page = book.files["OEBPS/id2_1214.xhtml"].decode()
        self.assertIn("beook-export-pdf2html-facts", page)
        self.assertIn("N/m <sup>2</sup>", page)
        self.assertIn("beook-export-pdf2html-figure", page)
        self.assertNotIn('<div class="t m0 x4 he y76', page)
        self.assertNotIn("\ue983", page)
        self.assertNotIn(" — F", page)

        grav = book.files["OEBPS/id3_1215.xhtml"].decode()
        self.assertNotIn(" — F", grav)
        self.assertLessEqual(grav.count("beook-export-pdf2html-figure"), 12)

        invalid: list[str] = []
        for path, data in book.files.items():
            if not path.endswith(".xhtml"):
                continue
            try:
                ElementTree.fromstring(data)
            except ElementTree.ParseError:
                invalid.append(path)
        self.assertEqual(invalid, [], f"Invalid XHTML: {invalid[:5]}")


class TestEpubStructure(unittest.TestCase):
    def test_build_epub_includes_cover_when_present(self):
        book = BookContent(
            reference="978-test",
            title="Test Book",
            language="de",
            spine_filenames=["OEBPS/page.xhtml"],
            files={
                "OEBPS/page.xhtml": b"<html><body>Hello</body></html>",
                "OEBPS/cover.jpg": b"fakejpeg",
            },
            media_types={
                "OEBPS/page.xhtml": "application/xhtml+xml",
                "OEBPS/cover.jpg": "image/jpeg",
            },
            pk_to_filename={1: "page.xhtml", 2: "cover.jpg"},
            cover_image_path="OEBPS/cover.jpg",
        )
        with zipfile.ZipFile(io.BytesIO(build_epub_bytes(book))) as zf:
            opf = zf.read("OEBPS/content.opf").decode()
            self.assertIn('properties="cover-image"', opf)
            self.assertIn('href="cover.xhtml"', opf)
            self.assertIn("OEBPS/cover.xhtml", zf.namelist())
            ElementTree.fromstring(zf.read("OEBPS/cover.xhtml"))

    def test_build_epub_has_required_files(self):
        book = BookContent(
            reference="978-test",
            title="Test Book",
            language="de",
            spine_filenames=["OEBPS/page.xhtml"],
            files={"OEBPS/page.xhtml": b"<html><head><title>Fallback</title></head><body><h1>Chapter &amp; One</h1>Hello</body></html>"},
            media_types={"OEBPS/page.xhtml": "application/xhtml+xml"},
            pk_to_filename={1: "page.xhtml"},
        )
        with zipfile.ZipFile(io.BytesIO(build_epub_bytes(book))) as zf:
            self.assertEqual(zf.namelist()[0], "mimetype")
            self.assertIn("OEBPS/content.opf", zf.namelist())
            self.assertIn(NAV_PATH, zf.namelist())
            opf = zf.read("OEBPS/content.opf").decode()
            self.assertIn('properties="nav"', opf)
            self.assertIn('href="nav.xhtml"', opf)
            nav = zf.read(NAV_PATH)
            ElementTree.fromstring(nav)
            self.assertIn(b"Chapter &amp; One", nav)
            self.assertIn(b"<ol>", nav)

    def _minimal_book(self, **overrides) -> BookContent:
        base = dict(
            reference="978-test",
            title="Test Book",
            language="de",
            spine_filenames=["OEBPS/page.xhtml"],
            files={
                "OEBPS/page.xhtml": b"<html><body><h1>One</h1>Hi</body></html>"
            },
            media_types={"OEBPS/page.xhtml": "application/xhtml+xml"},
            pk_to_filename={1: "page.xhtml"},
        )
        base.update(overrides)
        return BookContent(**base)

    def test_build_epub_includes_ncx_for_epub2_readers(self):
        with zipfile.ZipFile(io.BytesIO(build_epub_bytes(self._minimal_book()))) as zf:
            self.assertIn(NCX_PATH, zf.namelist())
            opf = zf.read("OEBPS/content.opf").decode()
            self.assertIn('media-type="application/x-dtbncx+xml"', opf)
            self.assertIn('<spine toc="ncx">', opf)
            ncx = zf.read(NCX_PATH)
            root = ElementTree.fromstring(ncx)
            ns = "{http://www.daisy.org/z3986/2005/ncx/}"
            self.assertEqual(root.tag, f"{ns}ncx")
            nav_points = root.findall(f".//{ns}navPoint")
            self.assertEqual(len(nav_points), 1)
            self.assertIn(b"page.xhtml", ncx)

    def test_build_epub_includes_guide_landmarks(self):
        book = self._minimal_book(
            files={
                "OEBPS/page.xhtml": b"<html><body>Hi</body></html>",
                "OEBPS/cover.jpg": b"fakejpeg",
            },
            media_types={
                "OEBPS/page.xhtml": "application/xhtml+xml",
                "OEBPS/cover.jpg": "image/jpeg",
            },
            cover_image_path="OEBPS/cover.jpg",
        )
        with zipfile.ZipFile(io.BytesIO(build_epub_bytes(book))) as zf:
            opf = zf.read("OEBPS/content.opf").decode()
            ElementTree.fromstring(opf)
            self.assertIn("<guide>", opf)
            self.assertIn('<reference type="cover"', opf)
            self.assertIn('<reference type="toc"', opf)

    def test_build_epub_renders_rich_metadata(self):
        book = self._minimal_book(
            authors=("Ada Lovelace", "Alan Turing"),
            subtitle="An Introduction",
            isbn="9783035520941",
            publish_date="2022-01-01",
            edition="5",
            description="A concise <b>overview</b> & study guide.",
            series="Test Series",
        )
        with zipfile.ZipFile(io.BytesIO(build_epub_bytes(book))) as zf:
            opf = zf.read("OEBPS/content.opf").decode()
            ElementTree.fromstring(opf)
            self.assertIn("<dc:creator id=\"creator-1\" opf:role=\"aut\">Ada Lovelace</dc:creator>", opf)
            self.assertIn("Alan Turing", opf)
            self.assertIn('property="title-type">subtitle</meta>', opf)
            self.assertIn("<dc:identifier id=\"isbn-id\">urn:isbn:9783035520941</dc:identifier>", opf)
            self.assertIn('property="identifier-type">ISBN</meta>', opf)
            self.assertIn("<dc:date>2022-01-01</dc:date>", opf)
            self.assertIn("<dc:source>urn:isbn:9783035520941</dc:source>", opf)
            self.assertIn('property="schema:bookEdition">5</meta>', opf)
            self.assertIn('name="calibre:series" content="Test Series"', opf)
            self.assertIn("<dc:description>", opf)
            self.assertIn("<dc:rights>", opf)

    def test_build_epub_renders_nav_subtitle(self):
        book = self._minimal_book(subtitle="An Introduction")
        with zipfile.ZipFile(io.BytesIO(build_epub_bytes(book))) as zf:
            nav = zf.read(NAV_PATH).decode()
            self.assertIn('class="beook-export-nav-subtitle"', nav)
            self.assertIn("An Introduction", nav)

    def test_build_epub_renders_cover_subtitle(self):
        book = self._minimal_book(
            subtitle="An Introduction",
            authors=("Ada Lovelace",),
            series="Test Series",
            isbn="9783035520941",
            edition="5",
            publish_date="2022-01-01",
            cover_image_path="OEBPS/cover.jpg",
            files={
                "OEBPS/page.xhtml": b"<html><body>Hi</body></html>",
                "OEBPS/cover.jpg": b"fakejpeg",
            },
            media_types={
                "OEBPS/page.xhtml": "application/xhtml+xml",
                "OEBPS/cover.jpg": "image/jpeg",
            },
        )
        with zipfile.ZipFile(io.BytesIO(build_epub_bytes(book))) as zf:
            cover = zf.read("OEBPS/cover.xhtml").decode()
            ElementTree.fromstring(cover)
            self.assertIn('class="beook-export-cover-subtitle"', cover)
            self.assertIn("An Introduction", cover)
            self.assertIn('class="beook-export-cover-title"', cover)
            self.assertIn("Ada Lovelace", cover)
            self.assertIn("Test Series", cover)
            self.assertIn("ISBN 9783035520941", cover)
            self.assertIn('epub:type="cover"', cover)

    def test_build_epub_includes_landmarks_nav(self):
        book = self._minimal_book(
            cover_image_path="OEBPS/cover.jpg",
            files={
                "OEBPS/page.xhtml": b"<html><body>Hi</body></html>",
                "OEBPS/cover.jpg": b"fakejpeg",
            },
            media_types={
                "OEBPS/page.xhtml": "application/xhtml+xml",
                "OEBPS/cover.jpg": "image/jpeg",
            },
        )
        with zipfile.ZipFile(io.BytesIO(build_epub_bytes(book))) as zf:
            nav = zf.read(NAV_PATH).decode()
            ElementTree.fromstring(nav)
            self.assertIn('epub:type="landmarks"', nav)
            self.assertIn('epub:type="cover"', nav)
            self.assertIn('epub:type="bodymatter"', nav)
            self.assertIn('href="cover.xhtml"', nav)
            self.assertIn('href="page.xhtml"', nav)
            self.assertIn("Umschlag", nav)
            self.assertIn("Inhaltsverzeichnis", nav)

    def test_build_epub_always_includes_title_page(self):
        book = self._minimal_book(
            authors=("Ada Lovelace",) * 8,
            publisher="hep Verlag AG",
            language="de",
        )
        with zipfile.ZipFile(io.BytesIO(build_epub_bytes(book))) as zf:
            self.assertIn("OEBPS/cover.xhtml", zf.namelist())
            cover = zf.read("OEBPS/cover.xhtml").decode()
            ElementTree.fromstring(cover)
            opf = zf.read("OEBPS/content.opf").decode()
            self.assertIn("<dc:publisher>hep Verlag AG</dc:publisher>", opf)
            self.assertIn("<dc:rights>", opf)
            self.assertIn("beook-export-cover-text-only", cover)
            self.assertIn("und 2 weitere", cover)
            self.assertIn("hep Verlag AG", cover)

    def test_build_epub_uuid_is_deterministic(self):
        book = self._minimal_book(isbn="9783035520941")
        first = zipfile.ZipFile(io.BytesIO(build_epub_bytes(book))).read("OEBPS/content.opf").decode()
        second = zipfile.ZipFile(io.BytesIO(build_epub_bytes(book))).read("OEBPS/content.opf").decode()
        ncx = zipfile.ZipFile(io.BytesIO(build_epub_bytes(book))).read(NCX_PATH).decode()
        uuid_re = re.compile(r'beook-export-uuid" content="([0-9a-f-]+)"')
        book_uuid = uuid_re.search(first).group(1)
        self.assertEqual(book_uuid, uuid_re.search(second).group(1))
        self.assertIn(f"urn:uuid:{book_uuid}", first)
        self.assertIn(f'dtb:uid" content="urn:uuid:{book_uuid}"', ncx)


class TestTitleCoverDetection(unittest.TestCase):
    def test_recognizes_german_and_english_title_assets(self):
        self.assertTrue(_is_title_cover_identifier("OEBPS_2_Titel.png"))
        self.assertTrue(_is_title_cover_identifier("OEBPS_Geo_Titelbild_1.jpg"))
        self.assertTrue(_is_title_cover_identifier("assets/Titelblatt.jpg"))
        self.assertTrue(_is_title_cover_identifier("cover_RE_HEP-RE-5DE@2x.png"))
        self.assertFalse(_is_title_cover_identifier("OEBPS_3-7.png"))
        self.assertFalse(_is_title_cover_identifier("assets_Navi-Pfeil-links.svg"))


class TestPosSpanReflow(unittest.TestCase):
    def test_collapses_highlighted_letter_fragments(self):
        from beook_export.extractor import _pos_collapse_fragments

        self.assertEqual(_pos_collapse_fragments("appe l er"), "appeler")
        self.assertEqual(_pos_collapse_fragments("je je tt e"), "je jette")
        self.assertIn("pas de", _pos_collapse_fragments("pasde sport"))

    def test_detects_pos_span_pages(self):
        from beook_export.extractor import _is_pos_span_page_text

        spans = " ".join(f'class="COL_1 FONT_1 POS_{i} "' for i in range(10))
        self.assertTrue(_is_pos_span_page_text(f"<body>{spans}</body>"))
        self.assertFalse(_is_pos_span_page_text("<body><p>normal page</p></body>"))

    def test_pos_font_size_em_converts_to_px(self):
        from beook_export.extractor import _parse_pos_css_positions

        css = (
            ".POS_1{top: 10px; left: 20px; font-size: 3.675em; position: absolute;}\n"
            ".POS_2{top: 50px; left: 80px; font-size: 12px; position: absolute;}\n"
        )
        positions = _parse_pos_css_positions(css)
        self.assertAlmostEqual(positions[1][2], 3.675 * 12.0, places=3)
        self.assertAlmostEqual(positions[2][2], 12.0, places=3)

    def test_merges_midword_pos_fragments_for_tables(self):
        from beook_export.extractor import _merge_pos_midword_spans_html

        # Mimic Faire le point conjugation headers: Par + t + ir / Dor + m + ir
        html = """
        <span class="COL_1 FONT_1 POS_1 ">Par</span>
        <span class="COL_1 FONT_1 POS_2 ">t</span>
        <span class="COL_1 FONT_1 POS_3 ">ir</span>
        <span class="COL_1 FONT_1 POS_4 ">Dor</span>
        <span class="COL_1 FONT_1 POS_5 ">m</span>
        <span class="COL_1 FONT_1 POS_6 ">ir</span>
        <span class="COL_1 FONT_1 POS_7 ">Cueillir</span>
        <span class="COL_1 FONT_1 POS_8 ">je cueille</span>
        <span class="COL_1 FONT_1 POS_9 ">Il</span>
        <span class="COL_1 FONT_1 POS_10 ">vient</span>
        """
        positions = {
            1: (100.0, 100.0, 44.0),
            2: (100.0, 180.0, 44.0),
            3: (100.0, 200.0, 44.0),
            4: (100.0, 400.0, 44.0),
            5: (100.0, 490.0, 44.0),
            6: (100.0, 520.0, 44.0),
            7: (100.0, 700.0, 44.0),
            8: (100.0, 920.0, 44.0),
            9: (100.0, 1200.0, 44.0),
            10: (100.0, 1260.0, 44.0),
        }
        merged = _merge_pos_midword_spans_html(html, positions)
        self.assertIn(">Partir<", merged)
        self.assertIn(">Dormir<", merged)
        self.assertIn(">Cueillir<", merged)
        self.assertIn(">je cueille<", merged)
        self.assertIn(">Il<", merged)
        self.assertIn(">vient<", merged)
        self.assertNotIn(">Par<", merged)
        self.assertNotIn(">Cueillirje", merged)
        self.assertNotIn("Ilvient", merged)

    def test_repairs_leading_glued_subject_pronouns(self):
        from beook_export.extractor import _pos_repair_glued_token

        self.assertEqual(_pos_repair_glued_token("Ilvient"), "Il vient")
        self.assertEqual(_pos_repair_glued_token("Ilsontperdu"), "Ils ont perdu")
        self.assertEqual(_pos_repair_glued_token("Il s"), "Ils")
        self.assertEqual(_pos_repair_glued_token("Elle s"), "Elles")
        self.assertEqual(_pos_repair_glued_token("Partir"), "Partir")
        self.assertEqual(_pos_repair_glued_token("D'autr es"), "D'autres")
        self.assertIn("comme", _pos_repair_glued_token("Verbescomme«"))
        self.assertIn(" «", _pos_repair_glued_token("Verbescomme«"))
        self.assertEqual(_pos_repair_glued_token("cueill es"), "cueilles")
        self.assertEqual(_pos_repair_glued_token("Il sortmême"), "Il sort même")

    def test_fix_pos_css_units_includes_word_spacing(self):
        from beook_export.extractor import _fix_pos_css_units

        fixed = _fix_pos_css_units("word-spacing: -4.17; letter-spacing: 0.0;")
        self.assertIn("word-spacing: -4.17px", fixed)
        self.assertIn("letter-spacing: 0.0px", fixed)


class TestBookMetadataParsing(unittest.TestCase):
    def test_parse_authors_from_html(self):
        from beook_export.database import _parse_authors

        self.assertEqual(
            _parse_authors("<p>Corinne Müller</p>"), ("Corinne Müller",)
        )
        self.assertEqual(
            _parse_authors("Guthoff Andrea, Imseng Ruth"),
            ("Guthoff Andrea", "Imseng Ruth"),
        )
        self.assertEqual(
            _parse_authors("<p>A. Autor</p><p>B. Beispiel</p>"),
            ("A. Autor", "B. Beispiel"),
        )
        self.assertEqual(
            _parse_authors("<p>Martin Hasler (Hrsg.)<br>Sabin Bieri, Georg Bill</p>"),
            ("Martin Hasler (Hrsg.)", "Sabin Bieri", "Georg Bill"),
        )

    def test_resolve_course_product_reference_for_series_chapters(self):
        from beook_export.database import resolve_course_product_reference

        try:
            db_path = resolve_db_path("1")
        except FileNotFoundError:
            self.skipTest("Beook profile not available")
        with connect(db_path) as conn:
            self.assertEqual(
                resolve_course_product_reference(conn, "RE01-HEP-RE-5DE"),
                "HEP-RE-5DE",
            )
            self.assertEqual(
                resolve_course_product_reference(conn, "GWV03-HEP-GWV-7DE"),
                "HEP-GWV-7DE",
            )
            self.assertEqual(
                resolve_course_product_reference(conn, "978-3-0355-2094-1"),
                "978-3-0355-2094-1",
            )
            meta = get_book_metadata(conn, "RE01-HEP-RE-5DE")
            self.assertTrue(meta.authors)
            self.assertEqual(meta.series, "Recht")
            self.assertIsNotNone(meta.subtitle)

    def test_coredata_date_conversion(self):
        from beook_export.database import _coredata_to_iso

        # 662688000 seconds after 2001-01-01 UTC == 2022-01-01.
        self.assertEqual(_coredata_to_iso(662688000), "2022-01-01")
        self.assertIsNone(_coredata_to_iso(None))
        self.assertIsNone(_coredata_to_iso(0))

    def test_isbn_normalization_and_detection(self):
        from beook_export.database import _looks_like_isbn, _normalize_isbn

        self.assertEqual(_normalize_isbn("978-3-0355-2094-1"), "9783035520941")
        self.assertIsNone(_normalize_isbn("not-an-isbn"))
        self.assertTrue(_looks_like_isbn("978-3-0355-2094-1"))
        self.assertFalse(_looks_like_isbn("GWV01-HEP-GWV-7DE"))

    def test_default_metadata_is_empty(self):
        meta = BookMetadata()
        self.assertEqual(meta.authors, ())
        self.assertIsNone(meta.isbn)


class TestLiveExtraction(unittest.TestCase):
    REFERENCE = "978-3-0355-2094-1"

    @classmethod
    def setUpClass(cls):
        try:
            cls.db_path = resolve_db_path(profile="1")
        except FileNotFoundError:
            cls.db_path = None

    def setUp(self):
        if self.db_path is None:
            self.skipTest("Beook profile 1 database not available")

    def _extract(self) -> BookContent:
        conn = connect(self.db_path)
        try:
            return extract_book(conn, self.REFERENCE)
        finally:
            conn.close()

    def test_title_uses_product_name_not_first_chapter(self):
        book = self._extract()
        self.assertEqual(book.title, "Betriebswirtschaftslehre")

    def test_extracted_xhtml_has_no_localhost(self):
        book = self._extract()
        combined = b"".join(
            data for path, data in book.files.items()
            if book.media_types.get(path) == "application/xhtml+xml"
        )
        self.assertGreater(len(combined), 1000)
        self.assertNotIn(b"localhost", combined)

    def test_consolidates_glossary_pages(self):
        def glossary_page(term: str, num: int, prev_num: int | None = None, next_num: int | None = None) -> bytes:
            prev = (
                f'<a class="pageNavigation-left" href="id{prev_num}.xhtml">Prev</a>'
                if prev_num is not None
                else ""
            )
            nxt = (
                f'<a class="pageNavigation-right" href="id{next_num}.xhtml">Next</a>'
                if next_num is not None
                else ""
            )
            nav = f'<div class="pageNavigationTable"><nav>{prev}{nxt}</nav></div>'
            body = (
                f'<h2 class="glossar-title">{term}</h2>'
                f'<div class="glossar-definition">Definition of {term}. '
                f'See also <a class="glossar-link" href="{max(1, num - 1)}.html">other</a>.</div>'
            )
            return f"<html><body>{body}{nav}</body></html>".encode()

        files = {
            "OEBPS/id1.xhtml": glossary_page("Alpha", 1, next_num=2),
            "OEBPS/id2.xhtml": glossary_page("Beta", 2, prev_num=1, next_num=3),
            "OEBPS/id3.xhtml": glossary_page("Gamma", 3, prev_num=2),
        }
        spine = list(files.keys())
        new_spine, merged_files, redirect = _consolidate_glossary_pages(spine, files)
        self.assertEqual(new_spine, ["OEBPS/glossary-001.xhtml"])
        self.assertEqual(len(redirect), 3)
        merged = merged_files["OEBPS/glossary-001.xhtml"].decode()
        self.assertIn("Alpha", merged)
        self.assertIn("Beta", merged)
        self.assertIn("Gamma", merged)
        self.assertIn("beook-export-glossary-entry", merged)
        self.assertIn('href="#glossar-id1"', merged)
        ElementTree.fromstring(merged_files["OEBPS/glossary-001.xhtml"])

    def test_consolidates_short_spine_for_live_export(self):
        book = self._extract()
        self.assertGreater(len(book.spine_filenames), 60)
        self.assertLess(len(book.spine_filenames), 90)
        glossary_pages = [
            path for path in book.spine_filenames if path.startswith("OEBPS/glossary-")
        ]
        self.assertGreaterEqual(len(glossary_pages), 4)
        self.assertLessEqual(len(glossary_pages), 8)

    def test_write_epub_produces_valid_archive(self):
        book = self._extract()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "test.epub"
            size = write_epub(book, str(out))
            self.assertGreater(size, 10_000)
            with zipfile.ZipFile(out) as zf:
                xhtml_files = [n for n in zf.namelist() if n.endswith(".xhtml")]
                self.assertGreater(len(xhtml_files), 70)
                combined = b"".join(zf.read(n) for n in xhtml_files)
                self.assertIn(b"Betriebswirtschaftslehre", combined)
                self.assertIn(b"Impressum", combined)
                self.assertIn(READER_STYLESHEET_PATH, zf.namelist())
                self.assertIn(b"Reader-focused stylesheet", zf.read(READER_STYLESHEET_PATH))
                self.assertIn(READER_STYLESHEET.encode(), combined)
                self.assertIsNotNone(book.cover_image_path)
                self.assertIn("cover_BWL", book.cover_image_path or "")
                self.assertNotIn("titelblatt", book.cover_image_path or "")
                split003 = book.files["OEBPS/BWL01_split_003.xhtml"].decode()
                self.assertIn("2_49.jpg", split003)
                self.assertNotRegex(split003, r'src="2\.jpg"')
                split012 = book.files["OEBPS/BWL01_split_012.xhtml"].decode()
                self.assertNotIn("overflow-container", split012)
                self.assertNotIn("end of overflow-container", split012)
                stats_page = next(
                    data.decode()
                    for path, data in book.files.items()
                    if path.endswith(".xhtml")
                    and "Struktur der Schweizer Wirtschaft" in data.decode()
                )
                self.assertIn("beook-export-chart-panel", stats_page)
                self.assertIn("beook-export-chart-page-break", stats_page)
                nav_css_name = next(
                    n for n in zf.namelist() if "hep_pageNavigation" in n and n.endswith(".css")
                )
                nav_css = zf.read(nav_css_name)
                self.assertIn(b"assets_Navi-Pfeil-links.svg", nav_css)
                self.assertNotIn(b"url(26)", nav_css)
                opf = zf.read("OEBPS/content.opf").decode()
                self.assertIn('properties="cover-image"', opf)
                cross_link_page = zf.read("OEBPS/BWL01_split_012.xhtml").decode()
                self.assertIn("BWL01_split_012.xhtml#BWL01_idTextAnchor010", cross_link_page)

    def test_german_book_language(self):
        self.assertEqual(self._extract().language, "de")

    def test_live_book_metadata_from_database(self):
        book = self._extract()
        self.assertEqual(
            book.authors,
            (
                "Rahel Balmer-Zahnd",
                "Vera Friedli",
                "Renato C. Müller Vasquez Callo",
            ),
        )
        self.assertEqual(book.subtitle, "Zusammenhänge verstehen")
        self.assertEqual(book.isbn, "9783035520941")
        self.assertEqual(book.edition, "5")
        self.assertEqual(book.publish_date, "2022-01-01")
        self.assertEqual(book.series, "Betriebswirtschaftslehre")
        self.assertIsNotNone(book.description)
        self.assertGreater(len(book.description or ""), 100)

    def test_live_epub_includes_database_metadata(self):
        book = self._extract()
        with zipfile.ZipFile(io.BytesIO(build_epub_bytes(book))) as zf:
            opf = zf.read("OEBPS/content.opf").decode()
            ElementTree.fromstring(opf)
            self.assertIn("Rahel Balmer-Zahnd", opf)
            self.assertIn("urn:isbn:9783035520941", opf)
            self.assertIn("Zusammenhänge verstehen", opf)
            self.assertIn(NCX_PATH, zf.namelist())

    def test_french_book_language(self):
        conn = connect(self.db_path)
        try:
            book = extract_book(conn, "978-3-0355-1376-9")
        finally:
            conn.close()
        self.assertEqual(book.language, "fr")

    def test_list_uses_product_title_for_chemistry(self):
        conn = connect(self.db_path)
        try:
            books = list_books(conn, "1")
        finally:
            conn.close()
        chem = next(b for b in books if b.reference == "978-3-0355-1741-5")
        self.assertEqual(chem.title, "Chemie für das Gymnasium")

    def test_list_books_never_returns_none_titles(self):
        conn = connect(self.db_path)
        try:
            books = list_books(conn, "1")
        finally:
            conn.close()
        for book in books:
            self.assertIsNotNone(book.title)
            self.assertNotIn("None", book.title)


if __name__ == "__main__":
    unittest.main()