"""Collect and rewrite book resources from Beook sqlite ZDATA blobs."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import posixpath
from collections import defaultdict
from dataclasses import dataclass
from html.entities import name2codepoint
from pathlib import Path
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from .database import (
    Issue,
    Note,
    Resource,
    get_book_metadata,
    get_book_title_for_reference,
    get_issues_for_reference,
    get_language_for_reference,
    get_notes_for_issues,
    get_resources_for_issues,
    get_spine_page_pks,
    get_topic_access_map,
    get_topic_pk_map,
    resolve_course_product_reference,
)
from .discovery import resolve_course_cover_file
from .language import detect_language, ui_string
from .naming import media_type_for_filename, oebps_name

LOCALHOST_RESOURCE_PK_RE = re.compile(
    r"http://localhost/database/resource/pk/(\d+)",
    re.IGNORECASE,
)
LOCALHOST_TOPIC_PK_RE = re.compile(
    r"http://localhost/pk/topic/(\d+)",
    re.IGNORECASE,
)
LOCALHOST_APP_RE = re.compile(
    r"http://localhost(?::\d+)?/[^\"'<> ]+",
    re.IGNORECASE,
)
LOCALHOST_ANCHOR_RE = re.compile(
    r"http://localhost/course/[^/\"'\s]+/courseReference/[^/\"'\s]+/anchor/([^\"'#?\s]+)",
    re.IGNORECASE,
)
LOCALHOST_ISSUE_TOPIC_RE = re.compile(
    r"http://localhost/course/[^/\"'\s]+/issueId/([^/\"'\s]+)/topicId/([^\"'#?\s]+)",
    re.IGNORECASE,
)
CSS_URL_PK_RE = re.compile(r"url\((['\"]?)(\d+)\1\)", re.IGNORECASE)
CSS_IMPORT_PK_RE = re.compile(
    r"@import\s+url\((['\"]?)(\d+)\1\)",
    re.IGNORECASE,
)
CSS_IMPORT_GENERIC_RE = re.compile(
    r"@import\s+url\((['\"]?)([^'\"\)]+)\1\)\s*;?",
    re.IGNORECASE,
)
CSS_URL_GENERIC_RE = re.compile(r"url\((['\"]?)([^'\"\)]+)\1\)", re.IGNORECASE)
ANCHOR_ID_RE = re.compile(r'\bid=(["\'])([^"\']+)\1', re.IGNORECASE)
OVERFLOW_OPEN_RE = re.compile(
    r'<div class="overflow-container(?:\s+beook-export-table-scroll)?">',
    re.IGNORECASE,
)
OVERFLOW_COMMENT_RE = re.compile(r"<!--\s*end of overflow-container\s*-->", re.IGNORECASE)
EMPTY_LAYOUT_DIV_RE = re.compile(
    r'<div class="idGenObject(?:Layout|StyleOverride)-\d+">\s*(?:<!--[^>]*-->\s*)*</div>\s*',
    re.IGNORECASE,
)
LERNZIELE_WRAPPER_RE = re.compile(
    r'<div class="idGenObjectStyleOverride-2">(\s*<h2 class="chapter-Lernziele")',
    re.IGNORECASE,
)
CHART_LAYOUT_BLOCK_RE = re.compile(
    r'(<div class="idGenObjectLayout-2">\s*<div class="[^"]*Grafikrahmen[^"]*beook-export-chart-panel[^"]*">.*?</div>\s*</div>)',
    re.IGNORECASE | re.DOTALL,
)
CHART_PANELS_PER_PAGE = 6
SPINE_MERGE_SHORT_CHARS = 3500
SPINE_MERGE_MAX_GROUP_CHARS = 10000
CHAPTER_SPINE_PREFIX_RE = re.compile(r"OEBPS/([A-Za-z0-9]+)_split_\d+\.xhtml$")
GLOSSARY_PAGE_ID_RE = re.compile(r"OEBPS/id(\d+)\.xhtml$")
GLOSSARY_LINK_RE = re.compile(
    r'(<a\b[^>]*\bclass="glossar-link"[^>]*\bhref=")(\d+)\.html(")',
    re.IGNORECASE,
)
GLOSSARY_HTML_LINK_RE = re.compile(r'href="(\d+)\.html"', re.IGNORECASE)
# Beook's own interactive-only UI hooks ("open glossary search", "launch
# exercise") survive extraction as anchors whose href is a bare word or a
# pseudo-anchor label rather than a real navigable target. They have no
# static destination in an offline EPUB, so they're neutralized to "#".
DEAD_ANCHOR_LABEL_RE = re.compile(r'href="#[^"]*\s[^"]*"', re.IGNORECASE)
DEAD_LINK_TAG_RE = re.compile(r"<a\b[^>]*>", re.IGNORECASE)
DEAD_LINK_CLASSES = frozenset({"web-link"})
GLOSSARY_MERGE_MAX_CHARS = 12000
GLOSSARY_MERGE_MAX_ENTRIES = 60
PDF2HTML_KERNING_SPAN_RE = re.compile(r'<span class="_ _\d+"></span>')
PDF2HTML_WHITESPACE_SPAN_RE = re.compile(r'<span class="_ _[^"]*">\s*</span>')
PDF2HTML_PF_CLASS_RE = re.compile(r'class="pf\b')
PDF2HTML_PI_DIV_RE = re.compile(r'<div class="pi"[^>]*></div>')
PDF2HTML_T_DIV_RE = re.compile(
    r'<div class="(t[^"]*)"[^>]*>(.*?)</div>',
    re.DOTALL | re.IGNORECASE,
)
PDF2HTML_C_DIV_RE = re.compile(
    r'<div class="(c[^"]*)"[^>]*>((?:<div class="t[^"]*"[^>]*>.*?</div>\s*)+)</div>',
    re.DOTALL | re.IGNORECASE,
)
PDF2HTML_PC_OPEN_RE = re.compile(r'<div class="pc[^"]*"[^>]*>', re.IGNORECASE)
PDF2HTML_PRIVATE_USE_RE = re.compile(r"[\ue000-\uf8ff]")
PDF2HTML_MAX_FIGURES_PER_PC = 2
PDF2HTML_SUPERSCRIPT_HEIGHTS = frozenset({"h13", "h14", "h15", "h16"})
PDF2HTML_SUPERSCRIPT_FONTS = frozenset({"fsb", "fsc"})
PDF2HTML_SUBSCRIPT_HEIGHTS = frozenset({"h12"})
PDF2HTML_SUBSCRIPT_FONTS = frozenset({"fsa"})
PDF2HTML_FIGURE_MIN_BYTES = 40_000
PDF2HTML_FIGURE_LEADING_RE = re.compile(
    r'^(?:<figure class="beook-export-pdf2html-figure">.*?</figure>\s*)+',
    re.DOTALL | re.IGNORECASE,
)
PDF2HTML_FORMULA_MARKERS_RE = re.compile(r"[=∙·/]| — |\+|\([slg]\)|∆|→|⇌")
PDF2HTML_TWO_COL_MIN_GAP = 0x20
PDF2HTML_FONT_SPAN_RE = re.compile(r'<span class="ff[^"]*">([^<]*)</span>', re.IGNORECASE)
PDF2HTML_HYPHEN_TAG_RE = re.compile(r"<hyphen\s*/?>|</hyphen>", re.IGNORECASE)
PDF2HTML_WS_HYPHEN_SPAN_RE = re.compile(r'<span class="ws\d+">-</span>', re.IGNORECASE)
PDF2HTML_TWO_COL_ROW_RE = re.compile(
    r'<span class="beook-export-pdf2html-col-left">(.*?)</span>\s*'
    r'<span class="beook-export-pdf2html-col-right">(.*?)</span>',
    re.DOTALL | re.IGNORECASE,
)
PAGE_NAV_MARKER_RE = re.compile(
    r"<div class=['\"]pageNavigationTable['\"]",
    re.IGNORECASE,
)
PAGE_NAV_LEFT_RE = re.compile(
    r'(<a\s+class=[\'"]pageNavigation-left[\'"][^>]*>.*?</a>)',
    re.IGNORECASE | re.DOTALL,
)
PAGE_NAV_RIGHT_RE = re.compile(
    r'(<a\s+class=[\'"]pageNavigation-right[\'"][^>]*>.*?</a>)',
    re.IGNORECASE | re.DOTALL,
)
WRAPPER_LEADING_JUNK_RE = re.compile(
    r'(<div class="idGenObjectStyleOverride-\d+">)\s*(?:<!--[^>]*-->\s*)*',
    re.IGNORECASE,
)
IMG_SRC_RE = re.compile(r'\bsrc="([^"]+)"', re.IGNORECASE)
LINK_WITH_ORIGINAL_RE = re.compile(
    r'<link\b([^>]*?)\s+href="http://localhost/database/resource/pk/(\d+)"([^>]*?)\s+data-original="([^"]*)"([^>]*?)/?>',
    re.IGNORECASE,
)
LINK_ORIGINAL_FIRST_RE = re.compile(
    r'<link\b([^>]*?)\s+data-original="([^"]*)"([^>]*?)\s+href="http://localhost/database/resource/pk/(\d+)"([^>]*?)/?>',
    re.IGNORECASE,
)
IMG_WITH_ORIGINAL_RE = re.compile(
    r'<img\b([^>]*?)\s+src="http://localhost/database/resource/pk/(\d+)"([^>]*?)\s+data-original="([^"]*)"([^>]*?)/?>',
    re.IGNORECASE,
)
IMG_ORIGINAL_FIRST_RE = re.compile(
    r'<img\b([^>]*?)\s+data-original="([^"]*)"([^>]*?)\s+src="http://localhost/database/resource/pk/(\d+)"([^>]*?)/?>',
    re.IGNORECASE,
)
DATA_ORIGINAL_ATTR_RE = re.compile(r'\s+data-original="[^"]*"', re.IGNORECASE)
NAMED_ENTITY_RE = re.compile(r"&([A-Za-z][A-Za-z0-9]+);")
VOID_CLOSING_TAG_RE = re.compile(r"</(?:link|img)\s*>", re.IGNORECASE)
TABLE_RE = re.compile(r"<table\b([^>]*)>(.*?)</table>", re.IGNORECASE | re.DOTALL)
TABLE_OPEN_RE = re.compile(r"<table\b([^>]*)>", re.IGNORECASE)
TR_RE = re.compile(r"<tr\b", re.IGNORECASE)
IMG_TAG_RE = re.compile(r"<img\b", re.IGNORECASE)
CLASS_ATTR_RE = re.compile(r'class="([^"]*)"', re.IGNORECASE)
BASE_TABLE_CLASS = "beook-export-table"
IMAGE_GRID_CLASS = "beook-export-image-grid"
DATA_TABLE_CLASS = "beook-export-data-table"
CHART_TABLE_CLASS = "beook-export-chart-table"
DATA_TABLE_MARKERS = (
    "tabelle-rahmen",
    "tabelle-linie",
    "tabelle-gruen",
    "tabelle-orange",
    "tabelle-blau",
    "einfache-tabelle",
    "texttabelle",
    "kein-tabellenformat",
    "zelle-linie",
    "alternierend",
    "tabellentext",
    "orange-hinterlegt",
)
XML_ENTITY_NAMES = frozenset({"amp", "lt", "gt", "quot", "apos"})
READER_STYLESHEET = "beook-export-reader.css"
READER_STYLESHEET_PATH = f"OEBPS/{READER_STYLESHEET}"
READER_STYLESHEET_BYTES = b"""/*
 * Reader-focused stylesheet injected by beook-export.
 * It comes last so standard EPUB readers prefer legible, reflowable pages
 * over Beook's app/print-oriented defaults.
 */
html {
  color-scheme: light;
  background-color: #ffffff !important;
}

html,
body {
  background-color: #ffffff !important;
  color: #111111 !important;
}

body {
  max-width: 46rem !important;
  margin: 0 auto !important;
  padding: 1.25rem !important;
  font-size: 1rem !important;
  line-height: 1.55 !important;
  -webkit-hyphens: auto;
  hyphens: auto;
  overflow-wrap: anywhere;
  white-space: normal !important;
  min-height: 0 !important;
  height: auto !important;
}

html,
body,
div,
section,
article,
[class*="idGenObject"],
div.Zusammenfassung,
[class*="Schluesselbegriffe"] {
  min-height: 0 !important;
  max-height: none !important;
}

.beook-export-spine-join {
  height: 0 !important;
  margin: 1.25rem 0 !important;
  padding: 0 !important;
  border: 0 !important;
  break-before: auto !important;
  page-break-before: auto !important;
}

body.beook-export-glossary {
  max-width: 46rem !important;
}

.beook-export-glossary-entry {
  margin: 0 0 0.85rem !important;
  padding: 0 0 0.65rem !important;
  border-bottom: 1px solid #dddddd !important;
  break-inside: auto !important;
  page-break-inside: auto !important;
}

.beook-export-glossary-entry:last-of-type {
  border-bottom: 0 !important;
}

.beook-export-glossary-entry h2.glossar-title,
.beook-export-glossary-entry .glossar-title {
  font-size: 1.05rem !important;
  margin: 0 0 0.3rem !important;
  line-height: 1.25 !important;
  color: #111111 !important;
}

.beook-export-glossary-entry .glossar-definition {
  margin: 0 !important;
  font-size: 0.95rem !important;
  line-height: 1.45 !important;
}

.beook-export-glossary-entry a.glossar-link {
  color: #0b5cab !important;
  text-decoration: underline !important;
}

body * {
  box-sizing: border-box;
  -webkit-column-count: 1 !important;
  -moz-column-count: 1 !important;
  column-count: 1 !important;
  -webkit-column-width: auto !important;
  -moz-column-width: auto !important;
  column-width: auto !important;
  columns: auto !important;
}

body,
p,
li,
blockquote,
dd,
dt,
td,
th {
  line-height: 1.55 !important;
}

p,
li {
  margin: 0 0 0.75em !important;
}

div,
aside,
figure,
section,
article,
table,
img,
svg,
video {
  float: none !important;
  clear: both !important;
}

h1,
h2,
h3,
h4,
h5,
h6 {
  color: #111111 !important;
  line-height: 1.2 !important;
  margin: 1.35em 0 0.55em !important;
}

a {
  color: #0b5cab !important;
  text-decoration: underline;
}

img,
svg,
video {
  display: block !important;
  max-width: 100% !important;
  height: auto !important;
  margin: 0.75rem auto !important;
}

figure,
table,
div {
  max-width: 100% !important;
}

table {
  display: table !important;
  width: 100% !important;
  border-collapse: collapse;
  table-layout: auto !important;
}

div.overflow-container,
div[class*="overflow-container"],
div[class*="Tabelle-Ueberbreite"],
div[class*="Bilder-Ueberbreite"] {
  display: block !important;
  width: 100% !important;
  max-width: 100% !important;
  height: auto !important;
  max-height: none !important;
  overflow: visible !important;
  overflow-x: visible !important;
  overflow-y: visible !important;
  margin: 1rem 0 !important;
  margin-left: 0 !important;
  flex: none !important;
}

table[class*="-overflow"],
table[class*="-Ueberbreite"],
table[class*="-ueberbreite"],
table[class*="-scrollbar"] {
  width: 100% !important;
  max-width: 100% !important;
  table-layout: auto !important;
}

table.beook-export-image-grid,
table.beook-export-image-grid tbody,
table.beook-export-image-grid tr,
table.beook-export-image-grid td {
  display: block !important;
  width: 100% !important;
  max-width: 100% !important;
}

table.beook-export-image-grid td {
  margin: 0 0 1rem !important;
  padding: 0 !important;
}

td,
th {
  padding: 0.2em 0.35em;
  vertical-align: top;
}

[class*="Marginal"],
[class*="Praxisfenster"],
[class*="Kasten"],
[class*="Bilder-"],
[class*="Bildquelle"],
[class*="idGenObjectLayout"],
[class*="idGenObjectStyleOverride"] {
  display: block !important;
  float: none !important;
  clear: both !important;
  position: static !important;
  width: auto !important;
  max-width: 100% !important;
  min-width: 0 !important;
  height: auto !important;
  margin: 0.85rem 0 !important;
  padding-left: 0 !important;
  padding-right: 0 !important;
}

[class*="Bilder-"],
[class*="Bildquelle"],
[class*="idGenObjectLayout"],
[class*="idGenObjectStyleOverride"] {
  width: 100% !important;
}

div.idGenObjectStyleOverride-1 {
  background-color: #3584c1 !important;
  color: #ffffff !important;
  padding: 0.85rem 1rem !important;
  margin: 0 0 1rem !important;
  border-radius: 4px !important;
}

div.idGenObjectStyleOverride-1 h1,
div.idGenObjectStyleOverride-1 h2,
div.idGenObjectStyleOverride-1 h3,
div.idGenObjectStyleOverride-1 p,
div.idGenObjectStyleOverride-1 li {
  color: #ffffff !important;
}

div.idGenObjectStyleOverride-3,
div.idGenObjectStyleOverride-4 {
  background-color: #f8f3dc !important;
  color: #111111 !important;
  padding: 0.85rem 1rem !important;
  margin: 0 0 1rem !important;
}

div.idGenObjectStyleOverride-3:has(.beook-export-chart-panel) {
  background-color: #ffffff !important;
  color: #111111 !important;
  padding: 0 !important;
  margin: 0 !important;
}

.beook-export-chart-page-break {
  break-before: page !important;
  page-break-before: always !important;
  height: 0 !important;
  margin: 0 !important;
  padding: 0 !important;
  border: 0 !important;
  clear: both !important;
}

.beook-export-chart-panel,
div.Einfacher-Grafikrahmen.beook-export-chart-panel,
.beook-export-chart-panel[class*="idGenObjectStyleOverride"] {
  display: block !important;
  background: #ffffff !important;
  color: #111111 !important;
  border: 1px solid #cccccc !important;
  border-radius: 6px !important;
  padding: 0.75rem 0.85rem !important;
  margin: 0 0 0.9rem !important;
  break-inside: avoid !important;
  page-break-inside: avoid !important;
}

div.idGenObjectLayout-2:has(.beook-export-chart-panel) {
  background: transparent !important;
  margin: 0 !important;
  padding: 0 !important;
}

.beook-export-chart-panel .Bilder-Textbreite,
.beook-export-chart-panel [class*="Bilder-Textbreite"],
.beook-export-chart-panel .idGenObjectLayout-1 {
  width: 100% !important;
  margin: 0 !important;
  padding: 0 !important;
  background: #ffffff !important;
}

div.beook-export-lernziele-panel,
div.idGenObjectStyleOverride-2.beook-export-lernziele-panel {
  background-color: #3584c1 !important;
  color: #ffffff !important;
  padding: 0.85rem 1rem !important;
  margin: 0 0 1rem !important;
  border-radius: 4px !important;
}

div.beook-export-lernziele-panel h1,
div.beook-export-lernziele-panel h2,
div.beook-export-lernziele-panel h3,
div.beook-export-lernziele-panel p,
div.beook-export-lernziele-panel li,
div.beook-export-lernziele-panel [class*="Lernziele"] {
  color: #ffffff !important;
}

[class*="idGenObjectLayout"] img,
[class*="Bilder-"] img {
  max-width: min(100%, 28rem) !important;
}

.Marginalien,
[class*="Marginal"] p,
[class*="Praxisfenster"] p,
[class*="Kasten"] p {
  font-size: 0.95rem !important;
  line-height: 1.45 !important;
}

.pageNavigationTable {
  border-top: 1px solid #dddddd;
  color: #666666 !important;
  font-size: 0.8rem !important;
  margin-top: 2rem !important;
}

a.pageNavigation-left,
a.pageNavigation-right {
  color: #0b5cab !important;
}

body.beook-export-cover {
  margin: 0 !important;
  padding: 1.5rem 1rem !important;
  max-width: 40rem !important;
  text-align: center !important;
}

body.beook-export-cover .beook-export-cover-frame {
  margin: 0 auto !important;
}

body.beook-export-cover img {
  display: block !important;
  width: 100% !important;
  max-width: 100% !important;
  height: auto !important;
  margin: 0 auto !important;
}

body.beook-export-cover .beook-export-cover-title {
  margin: 1rem 0 0.35rem !important;
  font-size: 1.45rem !important;
  line-height: 1.25 !important;
  color: #111111 !important;
}

body.beook-export-cover .beook-export-cover-subtitle {
  margin: 0.35rem 0 !important;
  font-size: 1.05rem !important;
  line-height: 1.4 !important;
  color: #333333 !important;
}

body.beook-export-cover .beook-export-cover-authors,
body.beook-export-cover .beook-export-cover-publisher,
body.beook-export-cover .beook-export-cover-series,
body.beook-export-cover .beook-export-cover-details {
  margin: 0.25rem 0 !important;
  font-size: 0.95rem !important;
  line-height: 1.4 !important;
  color: #333333 !important;
}

body.beook-export-cover .beook-export-cover-authors {
  font-style: italic !important;
}

body.beook-export-cover.beook-export-cover-text-only {
  display: flex !important;
  align-items: center !important;
  justify-content: center !important;
  min-height: 70vh !important;
}

body.beook-export-cover.beook-export-cover-text-only .beook-export-cover-title {
  font-size: 1.8rem !important;
}

.beook-export-nav-subtitle {
  margin: -0.25rem 0 1rem !important;
  font-size: 0.95rem !important;
  line-height: 1.4 !important;
  color: #555555 !important;
}

body.beook-export-annotations ol {
  padding-left: 1.25rem !important;
}

body.beook-export-annotations li {
  margin: 0 0 1rem !important;
}

.beook-export-annotation-meta {
  color: #555555 !important;
  font-size: 0.85rem !important;
  margin-bottom: 0.25rem !important;
}

.beook-export-annotation-text {
  border-left: 0.25rem solid #d9e8f6 !important;
  margin: 0 !important;
  padding: 0.35rem 0 0.35rem 0.75rem !important;
}

.pageInfo {
  display: block !important;
  text-align: right !important;
  font-size: 0.75rem !important;
  color: #888888 !important;
  margin: -0.35rem 0 0.85rem !important;
}

.pageInfo::before {
  content: "(Seite " attr(data-page-label) ") ";
}

h2.chaptera,
h3.chaptera,
h4.chaptera {
  color: #0b5cab !important;
  border-bottom: 1px solid #dddddd !important;
  padding-bottom: 0.35em !important;
}

.Marginalien,
p.Marginalien {
  background: #f0f6fc !important;
  border-left: 3px solid #0b5cab !important;
  padding: 0.65rem 0.85rem !important;
  margin: 0.75rem 0 !important;
  font-style: italic !important;
  color: #333333 !important;
}

[class*="Grauer-Kasten"],
p[class*="Grauer-Kasten"] {
  background: #f5f5f5 !important;
  color: #111111 !important;
  border: 1px solid #dddddd !important;
  border-left: 3px solid #cccccc !important;
  border-radius: 4px !important;
  padding: 0.75rem 1rem !important;
  margin: 0.5rem 0 1.25rem !important;
}

div.Einfacher-Grafikrahmen,
[class*="Grafikrahmen"] {
  background: #ffffff !important;
  color: #111111 !important;
  border: 1px solid #e0e0e0 !important;
  border-radius: 4px !important;
  padding: 0.75rem 1rem !important;
  margin: 1rem 0 !important;
}

p.Grafiken-Titel,
[class*="Grafiken-Titel"] {
  font-weight: bold !important;
  text-align: center !important;
  color: #111111 !important;
  margin: 0 0 0.75rem !important;
}

div.Einfacher-Grafikrahmen img,
[class*="Grafikrahmen"] img,
[class*="Bilder-Textbreite"] img,
p.Automatischer-Zeilenabstand img,
table.beook-export-image-grid img,
table.beook-export-chart-table img,
.beook-export-chart-panel img {
  background: #ffffff !important;
  padding: 0.5rem !important;
  width: 100% !important;
  max-width: 100% !important;
  margin: 0.35rem 0 0 !important;
  border-radius: 2px !important;
}

p.Grundtext-halbe-Blindzeile {
  display: none !important;
  margin: 0 !important;
  padding: 0 !important;
  height: 0 !important;
  line-height: 0 !important;
}

div.Praxisfenster,
.Praxisfenster {
  background: #fff8f0 !important;
  border: 1px solid #e8c9a0 !important;
  border-radius: 6px !important;
  padding: 1rem 1.15rem !important;
  margin: 1.25rem 0 !important;
}

img.praxisfenster-icon,
img[class*="praxisfenster-icon"] {
  display: inline-block !important;
  width: 1.35rem !important;
  height: 1.35rem !important;
  max-width: 1.35rem !important;
  max-height: 1.35rem !important;
  margin: 0 0.35em 0 0 !important;
  padding: 0 !important;
  vertical-align: middle !important;
  float: none !important;
  clear: none !important;
}

p[class*="Praxisfenster-Titel"] {
  display: flex !important;
  align-items: center !important;
  gap: 0.35rem !important;
}

[class*="Orange-hinterlegt"] {
  background: #fff3e6 !important;
  padding: 0.15em 0.35em !important;
  border-radius: 3px !important;
}

.Tabelle-Grafik-Titel,
p.Tabelle-Grafik-Titel {
  font-weight: bold !important;
  text-align: center !important;
  margin: 1rem 0 0.75rem !important;
  font-size: 1.05rem !important;
}

.Bildquelle,
span.Bildquelle {
  display: block !important;
  font-size: 0.8rem !important;
  color: #666666 !important;
  text-align: center !important;
  margin: 0.25rem 0 0.75rem !important;
  font-style: italic !important;
}

table.beook-export-table,
table.beook-export-data-table,
table.beook-export-image-grid,
table.beook-export-chart-table {
  break-inside: auto !important;
  page-break-inside: auto !important;
  -webkit-column-break-inside: auto !important;
}

table.beook-export-table {
  display: table !important;
  width: 100% !important;
  max-width: 100% !important;
  margin: 0.75rem 0 1rem !important;
  table-layout: auto !important;
}

table.beook-export-chart-table {
  display: table !important;
  width: 100% !important;
  background: #ffffff !important;
  border: 1px solid #cccccc !important;
  border-radius: 6px !important;
  break-inside: avoid !important;
  page-break-inside: avoid !important;
  margin: 0 0 0.9rem !important;
  border-collapse: collapse !important;
}

table.beook-export-chart-table td,
table.beook-export-chart-table th {
  background: #ffffff !important;
  padding: 0.75rem !important;
  border: none !important;
  vertical-align: top !important;
}

table.beook-export-chart-table img {
  display: block !important;
  width: 100% !important;
  max-width: 100% !important;
  background: #ffffff !important;
  margin: 0 !important;
}

table[class*="Tabelle-gruen"] td[class*="Tabellenkopf"],
table[class*="Tabelle-gruen"] th,
table.beook-export-data-table[class*="Tabelle-gruen"] thead td {
  background: #e8f5e9 !important;
  color: #111111 !important;
}

table[class*="Tabelle-orange"] td[class*="Tabellenkopf"],
table[class*="Tabelle-orange"] th,
table.beook-export-data-table[class*="Tabelle-orange"] thead td {
  background: #fff3e6 !important;
  color: #111111 !important;
}

table[class*="Tabelle-blau"] td[class*="Tabellenkopf"],
table[class*="Tabelle-blau"] th {
  background: #e8f0fa !important;
  color: #111111 !important;
}

table.beook-export-data-table {
  display: table !important;
  width: 100% !important;
  max-width: 100% !important;
  table-layout: auto !important;
  font-size: 0.82rem !important;
  border-collapse: collapse !important;
  margin: 0.85rem 0 1.1rem !important;
  background: #ffffff !important;
  box-shadow: 0 0 0 1px #e6e6e6 !important;
  border-radius: 4px !important;
  overflow: hidden !important;
}

table.beook-export-data-table tbody,
table.beook-export-data-table thead {
  display: table-row-group !important;
}

table.beook-export-data-table tr {
  display: table-row !important;
  break-inside: avoid !important;
  page-break-inside: avoid !important;
}

table.beook-export-data-table thead {
  display: table-header-group !important;
}

table.beook-export-image-grid td,
table.beook-export-image-grid td div,
table.beook-export-image-grid td p {
  break-inside: auto !important;
  page-break-inside: auto !important;
}

table.beook-export-data-table td,
table.beook-export-data-table th {
  display: table-cell !important;
  white-space: normal !important;
  word-wrap: break-word !important;
  overflow-wrap: anywhere !important;
  padding: 0.3em 0.4em !important;
  border: 1px solid #dddddd !important;
  vertical-align: top !important;
  hyphens: auto !important;
}

table.beook-export-data-table td:first-child,
table.beook-export-data-table th:first-child {
  min-width: 6.5rem !important;
}

table.beook-export-data-table img {
  display: block !important;
  max-width: 100% !important;
  width: auto !important;
  height: auto !important;
  margin: 0.25rem auto !important;
}

table.beook-export-image-grid td:empty {
  display: none !important;
}

ul.nopadding,
ul[class*="nopadding"] {
  padding-left: 1.1rem !important;
  margin: 0 0 1rem !important;
}

li.Grundtext-Aufzaehlung-Punkte,
li[class*="Aufzaehlung"] {
  margin-bottom: 0.55em !important;
}

/* PDF-like POS spans: only flatten on semantic-reflow pages.
   Fixed-layout POS pages keep absolute coordinates from the book CSS. */
body:not(.beook-export-pos-fixed) [class^="POS_"],
body:not(.beook-export-pos-fixed) [class*=" POS_"] {
  position: static !important;
  display: inline !important;
  left: auto !important;
  right: auto !important;
  top: auto !important;
  bottom: auto !important;
  margin: 0 0.12em 0 0 !important;
  white-space: normal !important;
}

body:not(.beook-export-pos-fixed) [class^="FONT_"],
body:not(.beook-export-pos-fixed) [class*=" FONT_"],
body:not(.beook-export-pos-fixed) [class^="COL_"],
body:not(.beook-export-pos-fixed) [class*=" COL_"] {
  color: inherit !important;
  font-size: inherit !important;
}

body:not(.beook-export-pos-fixed) [class^="SVG_"],
body:not(.beook-export-pos-fixed) [class*=" SVG_"] {
  position: static !important;
  display: block !important;
  margin: 1rem 0 !important;
  max-width: 100% !important;
}

@media screen and (min-width: 481px) {
  body {
    width: auto !important;
    max-width: 46rem !important;
  }

  div[class*="Marginalien"],
  div[class*="Praxisfenster-klein"],
  div[class*="marginal-container-images"] {
    float: none !important;
    width: auto !important;
    max-width: 100% !important;
    margin-right: 0 !important;
  }
}

/* Fixed-layout POS pages: preserve print columns/tables. */
body.beook-export-pos-fixed {
  margin: 0 !important;
  padding: 0.5rem !important;
  background: #ffffff !important;
  color: #111111 !important;
  max-width: none !important;
  overflow: auto !important;
}

body.beook-export-pos-fixed .beook-export-pos-fixed-outer {
  position: relative !important;
  overflow: visible !important;
  margin: 0 auto !important;
  background: #ffffff !important;
}

body.beook-export-pos-fixed .beook-export-pos-fixed-page {
  position: relative !important;
  transform-origin: top left !important;
  background: #ffffff !important;
  color: #111111 !important;
  /* Beook POS font-size values are in em; give them a stable root. */
  font-size: 12px !important;
  line-height: 1.25 !important;
}

body.beook-export-pos-fixed .beook-export-pos-fixed-page {
  /* Let the original book CSS drive absolute coordinates and colors. */
}

body.beook-export-pos-fixed .flowContainer,
body.beook-export-pos-fixed .pageInfo,
body.beook-export-pos-fixed .navigationWrapper {
  display: none !important;
}

/* Absolute-position POS_/COL_/FONT_ pages reflowed into real paragraphs. */
body.beook-export-pos-reflow {
  max-width: 42rem !important;
  line-height: 1.55 !important;
  color: #111111 !important;
  background: #ffffff !important;
}

body.beook-export-pos-reflow h2.beook-export-pos-heading {
  margin: 0 0 0.75rem !important;
  font-size: 1.35rem !important;
  line-height: 1.25 !important;
  color: #8b1e1c !important;
}

body.beook-export-pos-reflow h3.beook-export-pos-subheading {
  margin: 0.9rem 0 0.4rem !important;
  font-size: 1.05rem !important;
  line-height: 1.3 !important;
  color: #8b1e1c !important;
  font-weight: 700 !important;
}

body.beook-export-pos-reflow .beook-export-pos-multicol {
  display: flex !important;
  flex-wrap: wrap !important;
  gap: 1.25rem 1.75rem !important;
  align-items: flex-start !important;
  width: 100% !important;
}

body.beook-export-pos-reflow .beook-export-pos-col {
  flex: 1 1 16rem !important;
  min-width: min(100%, 16rem) !important;
  max-width: 100% !important;
}

body.beook-export-pos-reflow p {
  margin: 0 0 0.7rem !important;
  font-size: 1rem !important;
  line-height: 1.55 !important;
  color: #111111 !important;
}

body.beook-export-pos-reflow p.beook-export-pos-callout {
  padding: 0.65rem 0.85rem !important;
  border-left: 4px solid #d5161c !important;
  background: #fff5f5 !important;
  color: #111111 !important;
}

body.beook-export-pos-reflow sup {
  font-size: 0.75em !important;
  line-height: 0 !important;
  vertical-align: super !important;
}

body.beook-export-pos-reflow ul.beook-export-pos-toc {
  margin: 0.5rem 0 1rem 1.25rem !important;
  padding: 0 !important;
}

body.beook-export-pos-reflow ul.beook-export-pos-toc li {
  margin: 0.25rem 0 !important;
  line-height: 1.4 !important;
}

/* pdf2htmlEX pages: semantic reflow output from merged line fragments. */
body.beook-export-pos-reflow,
body.beook-export-pdf2html-reflow {
  background-color: #ffffff !important;
  color: #111111 !important;
}

body.beook-export-pdf2html-reflow #page-container,
body.beook-export-pdf2html-reflow .pf,
body.beook-export-pdf2html-reflow .pc {
  position: static !important;
  display: block !important;
  width: auto !important;
  min-width: 0 !important;
  max-width: 100% !important;
  height: auto !important;
  min-height: 0 !important;
  max-height: none !important;
  overflow: visible !important;
  margin: 0 !important;
  padding: 0 !important;
  border: 0 !important;
  background: transparent !important;
  transform: none !important;
  -webkit-transform: none !important;
}

body.beook-export-pdf2html-reflow .pageInfo {
  display: none !important;
}

body.beook-export-pdf2html-reflow .beook-export-pdf2html-line {
  margin: 0 0 0.75em !important;
  line-height: 1.55 !important;
  text-align: left !important;
}

body.beook-export-pdf2html-reflow .beook-export-pdf2html-line sup {
  font-size: 0.75em !important;
  vertical-align: super !important;
  line-height: 0 !important;
}

body.beook-export-pdf2html-reflow .beook-export-pdf2html-line sub,
body.beook-export-pdf2html-reflow ul.beook-export-pdf2html-facts sub {
  font-size: 0.75em !important;
  vertical-align: sub !important;
  line-height: 0 !important;
}

body.beook-export-pdf2html-reflow .beook-export-pdf2html-heading,
body.beook-export-pdf2html-reflow h3.beook-export-pdf2html-heading {
  color: #0081b8 !important;
  font-size: 1.2rem !important;
  font-weight: 600 !important;
  margin: 1.25em 0 0.55em !important;
  line-height: 1.25 !important;
}

body.beook-export-pdf2html-reflow h3.beook-export-pdf2html-caption,
body.beook-export-pdf2html-reflow h4.beook-export-pdf2html-caption {
  color: #111111 !important;
  font-weight: 600 !important;
  margin: 1rem 0 0.5rem !important;
  line-height: 1.3 !important;
}

body.beook-export-pdf2html-reflow h3.beook-export-pdf2html-caption {
  font-size: 1.05rem !important;
}

body.beook-export-pdf2html-reflow h4.beook-export-pdf2html-caption {
  font-size: 0.95rem !important;
  font-style: italic !important;
}

body.beook-export-pdf2html-reflow ul.beook-export-pdf2html-facts {
  list-style: disc outside !important;
  margin: 0.5rem 0 1rem !important;
  padding: 0 0 0 1.25rem !important;
}

body.beook-export-pdf2html-reflow ul.beook-export-pdf2html-facts li {
  margin: 0 0 0.65rem !important;
  line-height: 1.5 !important;
}

body.beook-export-pdf2html-reflow .beook-export-pdf2html-box {
  border: 1px solid #dddddd !important;
  border-radius: 6px !important;
  padding: 0.75rem 0.9rem !important;
  margin: 0.75rem 0 1rem !important;
  background: #ffffff !important;
}

/* Nested comparison tables (e.g. Geografie atmosphere composition). */
table.beook-export-data-table table {
  width: 100% !important;
  margin: 0 !important;
  font-size: inherit !important;
  box-shadow: none !important;
}

body.beook-export-pdf2html-reflow table.beook-export-pdf2html-table {
  display: table !important;
  width: 100% !important;
  border-collapse: collapse !important;
  margin: 0.75rem 0 1rem !important;
  font-size: 0.92rem !important;
}

body.beook-export-pdf2html-reflow table.beook-export-pdf2html-table td {
  display: table-cell !important;
  border: 1px solid #dddddd !important;
  padding: 0.45rem 0.55rem !important;
  vertical-align: top !important;
  line-height: 1.45 !important;
}

body.beook-export-pdf2html-reflow table.beook-export-pdf2html-table tr:first-child td {
  background: #fff3e6 !important;
  font-weight: 600 !important;
}

body.beook-export-pdf2html-reflow .beook-export-pdf2html-figure-group {
  margin: 1rem 0 1.25rem !important;
  break-inside: avoid !important;
  page-break-inside: avoid !important;
}

body.beook-export-pdf2html-reflow figure.beook-export-pdf2html-figure {
  display: block !important;
  margin: 0.75rem 0 1rem !important;
  padding: 0 !important;
  text-align: center !important;
}

body.beook-export-pdf2html-reflow img.beook-export-pdf2html-figure-img,
body.beook-export-pdf2html-reflow img.bi {
  position: static !important;
  display: block !important;
  width: 100% !important;
  max-width: min(100%, 42rem) !important;
  height: auto !important;
  margin: 0 auto !important;
}

body.beook-export-pdf2html-reflow img.bf {
  display: none !important;
}

body.beook-export-pdf2html-reflow .t,
body.beook-export-pdf2html-reflow .c,
body.beook-export-pdf2html-reflow .d {
  display: none !important;
}

body.beook-export-pdf2html-reflow .pi {
  display: none !important;
}
"""


@dataclass(frozen=True)
class BookContent:
    reference: str
    title: str
    language: str
    spine_filenames: list[str]
    files: dict[str, bytes]
    media_types: dict[str, str]
    pk_to_filename: dict[int, str]
    cover_image_path: str | None = None
    authors: tuple[str, ...] = ()
    subtitle: str | None = None
    isbn: str | None = None
    publish_date: str | None = None
    edition: str | None = None
    description: str | None = None
    series: str | None = None
    publisher: str | None = None


def _note_type_label(value: str | None, language: str) -> str:
    mapping = {
        "H": ui_string(language, "highlight"),
        "N": ui_string(language, "note"),
        "B": ui_string(language, "bookmark"),
    }
    return mapping.get(value or "", value or ui_string(language, "annotation"))


def _annotations_xhtml(notes: list[Note], language: str) -> bytes:
    heading = ui_string(language, "annotations")
    topic_label = ui_string(language, "topic")
    items: list[str] = []
    for note in notes:
        heading_parts = [
            part
            for part in (
                note.issue_id,
                note.issue_title,
                f"{topic_label} {note.topic_access}" if note.topic_access else None,
            )
            if part
        ]
        meta_parts = [_note_type_label(note.type, language), *heading_parts]
        if note.date:
            meta_parts.append(note.date)
        if note.color:
            meta_parts.append(note.color)
        title = f"<h2>{escape(note.title)}</h2>" if note.title else ""
        text = escape(note.text or "")
        items.append(
            "      <li>\n"
            f'        <p class="beook-export-annotation-meta">{" · ".join(escape(part) for part in meta_parts)}</p>\n'
            f"        {title}\n"
            f'        <blockquote class="beook-export-annotation-text">{text}</blockquote>\n'
            "      </li>"
        )
    body = "\n".join(items)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" lang="{escape(language)}" xml:lang="{escape(language)}">
  <head>
    <meta charset="utf-8" />
    <title>{escape(heading)}</title>
    <link href="{READER_STYLESHEET}" rel="stylesheet" type="text/css" />
  </head>
  <body class="beook-export-annotations">
    <h1>{escape(heading)}</h1>
    <ol>
{body}
    </ol>
  </body>
</html>
""".encode("utf-8")


def _disambiguated_name(base: str, pk: int) -> str:
    if "." in base:
        stem, ext = base.rsplit(".", 1)
        return f"{stem}_{pk}.{ext}"
    return f"{base}_{pk}"


def _build_pk_to_filename(resources: list[Resource]) -> dict[int, str]:
    # Beook reuses plain numeric-style identifiers (e.g. "id223") across
    # unrelated resources of different media types (an XHTML page, a
    # plain-text index blob, an embedded font subset can all share the same
    # identifier). Grouping by identifier alone would pick one resource's
    # media type for the whole group, corrupting filenames/extensions for
    # every other member and silently breaking cross-references that expect
    # e.g. "id223.xhtml" to exist. Group by (identifier, media_type) instead
    # so each resource kind gets its own correctly-typed filename.
    by_key: dict[tuple[str, str], list[Resource]] = defaultdict(list)
    for resource in resources:
        if resource.data is not None:
            by_key[(resource.identifier, resource.media_type)].append(resource)

    pk_to_filename: dict[int, str] = {}
    for (identifier, media_type), group in by_key.items():
        base = oebps_name(identifier, media_type)
        hash_to_filename: dict[bytes, str] = {}
        unique_hashes = {
            hashlib.sha256(resource.data).digest()
            for resource in group
            if resource.data is not None
        }
        for resource in group:
            if resource.data is None:
                continue
            digest = hashlib.sha256(resource.data).digest()
            if digest in hash_to_filename:
                pk_to_filename[resource.pk] = hash_to_filename[digest]
                continue
            if len(unique_hashes) == 1:
                filename = base
            else:
                filename = _disambiguated_name(base, resource.pk)
            hash_to_filename[digest] = filename
            pk_to_filename[resource.pk] = filename
    return pk_to_filename


def _resource_filename(
    pk_to_filename: dict[int, str],
    pk: int,
    fallback: str,
) -> str:
    return pk_to_filename.get(pk, fallback)


def _dedupe_resources(resources: list[Resource]) -> list[Resource]:
    seen: set[str] = set()
    unique: list[Resource] = []
    for resource in resources:
        if resource.identifier in seen:
            continue
        seen.add(resource.identifier)
        unique.append(resource)
    return unique


def _book_title(issues: list[Issue]) -> str:
    preface_titles = {"vorspann", "impressum", "anhang", "glossar"}
    for issue in issues:
        if issue.order == 0:
            continue
        issue_id = issue.issue_id.upper()
        if issue_id.endswith(("00", "98", "99")):
            continue
        if issue.title.lower().strip() in preface_titles:
            continue
        return issue.title
    for issue in issues:
        if issue.order > 0:
            return issue.title
    return issues[0].title


def _empty_element(tag: str, primary_attr: str, value: str, *attr_groups: str) -> str:
    attrs = [group.strip() for group in attr_groups if group.strip()]
    attrs.insert(0, f'{primary_attr}="{value}"')
    return f"<{tag} {' '.join(attrs)} />"


def _normalize_named_entities(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in XML_ENTITY_NAMES:
            return match.group(0)
        codepoint = name2codepoint.get(name)
        if codepoint is None:
            return match.group(0)
        return f"&#{codepoint};"

    return NAMED_ENTITY_RE.sub(replace, text)


def _reader_stylesheet_href(filename: str) -> str:
    directory = posixpath.dirname(filename)
    return posixpath.relpath(READER_STYLESHEET, directory or ".")


def _relative_href(from_filename: str, to_filename: str) -> str:
    directory = posixpath.dirname(from_filename)
    return posixpath.relpath(to_filename, directory or ".")


def _build_anchor_map(
    resources: list[Resource],
    pk_to_filename: dict[int, str],
) -> dict[str, str]:
    anchor_to_filename: dict[str, str] = {}
    for resource in resources:
        if resource.media_type not in ("application/xhtml+xml", "text/html") or not resource.data:
            continue
        filename = pk_to_filename.get(resource.pk)
        if not filename:
            continue
        text = resource.data.decode("utf-8", errors="replace")
        for match in ANCHOR_ID_RE.finditer(text):
            anchor_to_filename[match.group(2)] = filename
    return anchor_to_filename


def _anchor_href(anchor_id: str, anchor_to_filename: dict[str, str]) -> str:
    filename = anchor_to_filename.get(anchor_id)
    if not filename:
        return "#"
    return f"{filename}#{anchor_id}"


def _is_remote_href(value: str) -> bool:
    return value.startswith(("#", "//")) or re.match(r"^[a-z][a-z0-9+.-]*:", value, re.IGNORECASE) is not None


def _rewrite_css(content: bytes, pk_to_filename: dict[int, str], css_filename: str) -> bytes:
    text = content.decode("utf-8")
    filename_by_basename = {
        filename.rsplit("/", 1)[-1]: filename
        for filename in sorted(set(pk_to_filename.values()))
    }

    def _pk_href(pk: int) -> str | None:
        target = pk_to_filename.get(pk)
        if not target:
            return None
        return _relative_href(css_filename, target)

    def _asset_href(value: str) -> str | None:
        if _is_remote_href(value):
            return None
        path = value.split("#", 1)[0].split("?", 1)[0].strip()
        if not path or path.isdigit():
            return None
        basename = path.rsplit("/", 1)[-1]
        target = filename_by_basename.get(basename) or filename_by_basename.get(
            f"assets_{basename}"
        )
        if not target:
            return None
        return _relative_href(css_filename, target)

    def _url_replacer(match: re.Match[str]) -> str:
        pk = int(match.group(2))
        href = _pk_href(pk)
        if href is None:
            return match.group(0)
        quote = match.group(1)
        return f"url({quote}{href}{quote})"

    text = CSS_URL_PK_RE.sub(_url_replacer, text)
    text = CSS_IMPORT_PK_RE.sub(
        lambda m: (
            f"@import url({m.group(1)}{_pk_href(int(m.group(2)))}{m.group(1)})"
            if _pk_href(int(m.group(2)))
            else m.group(0)
        ),
        text,
    )
    text = CSS_IMPORT_GENERIC_RE.sub(
        lambda m: (
            f"@import url({m.group(1)}{_asset_href(m.group(2))}{m.group(1)});"
            if _asset_href(m.group(2))
            else (m.group(0) if _is_remote_href(m.group(2)) else "")
        ),
        text,
    )
    text = CSS_URL_GENERIC_RE.sub(
        lambda m: (
            f"url({m.group(1)}{_asset_href(m.group(2))}{m.group(1)})"
            if _asset_href(m.group(2))
            else m.group(0)
        ),
        text,
    )
    text = LOCALHOST_RESOURCE_PK_RE.sub(
        lambda m: _pk_href(int(m.group(1))) or m.group(0),
        text,
    )
    return text.encode("utf-8")


def _inject_course_cover(
    conn: sqlite3.Connection,
    reference: str,
    beook_root: Path | None,
    files: dict[str, bytes],
    media_types: dict[str, str],
) -> str | None:
    cover_path = resolve_course_cover_file(conn, reference, beook_root)
    if cover_path is None:
        return None

    filename = oebps_name(cover_path.name, "image/png")
    epub_path = f"OEBPS/{filename}"
    files[epub_path] = cover_path.read_bytes()
    media_types[epub_path] = media_type_for_filename(filename, "image/png")
    return epub_path


def _is_title_cover_identifier(identifier: str) -> bool:
    """True for Beook title/cover asset names (German and English variants)."""
    name = identifier.lower().replace("\\", "/")
    stem = name.rsplit("/", 1)[-1]
    markers = (
        "titelblatt",
        "titelbild",
        "titlepage",
        "title-page",
        "title_page",
        "bookcover",
        "book-cover",
        "book_cover",
    )
    if any(marker in stem for marker in markers):
        return True
    # Chapter title plates such as OEBPS_2_Titel.png / Geo_Titelbild_1.jpg
    if re.search(r"(^|[_\-\s.])titel(bild|blatt)?([_\-\s.]|$)", stem):
        return True
    if re.search(r"(^|[_\-\s.])cover([_\-\s.]|$)", stem) and "navi" not in stem:
        return True
    return False


def _find_cover_image_path(
    resources: list[Resource],
    pk_to_filename: dict[int, str],
    issues: list[Issue],
    spine_filenames: list[str],
    files: dict[str, bytes],
) -> str | None:
    first_chapter_issues = [
        issue
        for issue in issues
        if issue.order > 0 and not issue.issue_id.upper().endswith(("00", "98", "99"))
    ]
    preferred_issue_pks = {issue.pk for issue in first_chapter_issues[:1]} or {
        issue.pk for issue in issues
    }

    candidates: list[tuple[int, int, Resource]] = []
    for resource in resources:
        if not resource.media_type.startswith("image/"):
            continue
        if resource.media_type == "image/svg+xml":
            continue
        if not resource.data or resource.pk not in pk_to_filename:
            continue
        if not _is_title_cover_identifier(resource.identifier):
            continue
        # Prefer real title plates over tiny icons; still accept modest covers.
        if len(resource.data) < 8_000:
            continue
        priority = 0 if resource.issue_pk in preferred_issue_pks else 1
        candidates.append((priority, -len(resource.data), resource))

    if candidates:
        candidates.sort(key=lambda item: (item[0], item[1]))
        chosen = candidates[0][2]
        return f"OEBPS/{pk_to_filename[chosen.pk]}"

    for path in spine_filenames:
        payload = files.get(path)
        if not payload:
            continue
        text = payload.decode("utf-8", errors="replace")
        lower = text.lower()
        if (
            "bild-titel" not in lower
            and "titelblatt" not in lower
            and "titelbild" not in lower
            and 'class="titel' not in lower
        ):
            continue
        for match in IMG_SRC_RE.finditer(text):
            src = match.group(1)
            if _is_title_cover_identifier(src) or "titel" in src.lower():
                return f"OEBPS/{src}" if "/" not in src else src

    return None


def _inject_reader_stylesheet(text: str, href: str) -> str:
    if READER_STYLESHEET in text:
        return text
    link = f'<link href="{href}" rel="stylesheet" type="text/css" />'
    return re.sub(r"</head\s*>", f"\t\t{link}\n\t</head>", text, count=1, flags=re.IGNORECASE)


def _table_plain_text(body: str) -> str:
    text = re.sub(r"<[^>]+>", " ", body)
    return re.sub(r"\s+", " ", text).strip()


def _is_navigation_table(attrs: str) -> bool:
    return "pagenavigation" in attrs.lower()


def _is_chart_table(attrs: str, body: str) -> bool:
    lower_attrs = attrs.lower()
    if "tabelle-ohne-rahmen" in lower_attrs:
        return False
    if not IMG_TAG_RE.search(body):
        return False
    tr_count = len(TR_RE.findall(body))
    if tr_count > 3:
        return False
    if any(marker in lower_attrs for marker in ("tabelle-blau", "tabelle-grafik", "tabelle-orange")):
        return len(_table_plain_text(body)) < 120 or tr_count <= 2
    img_count = len(IMG_TAG_RE.findall(body))
    if img_count == 1 and tr_count <= 2 and len(_table_plain_text(body)) < 80:
        return True
    return False


def _is_data_table(attrs: str, body: str) -> bool:
    if _is_chart_table(attrs, body):
        return False
    lower_attrs = attrs.lower()
    lower_body = body.lower()
    if any(marker in lower_attrs or marker in lower_body for marker in DATA_TABLE_MARKERS):
        return True
    tr_count = len(TR_RE.findall(body))
    if tr_count >= 4:
        return True
    if len(re.findall(r'class="[^"]*tabellentext', body, re.IGNORECASE)) >= 3:
        return True
    td_count = len(re.findall(r"<td\b", body, re.IGNORECASE))
    return tr_count >= 2 and td_count >= 2


def _is_image_layout_table(attrs: str, body: str) -> bool:
    if not IMG_TAG_RE.search(body):
        return False
    if _is_chart_table(attrs, body) or _is_data_table(attrs, body):
        return False
    lower_attrs = attrs.lower()
    lower_body = body.lower()
    if "tabelle-ohne-rahmen" in lower_attrs:
        return True
    if "grauer-kasten" in lower_body:
        return True
    img_count = len(IMG_TAG_RE.findall(body))
    tr_count = len(TR_RE.findall(body))
    return img_count >= 2 and tr_count <= 3


def _append_table_class(attrs: str, class_name: str) -> str:
    if class_name in attrs:
        return attrs
    class_match = CLASS_ATTR_RE.search(attrs)
    if class_match:
        classes = f"{class_match.group(1)} {class_name}"
        return CLASS_ATTR_RE.sub(f'class="{classes}"', attrs, count=1)
    return f'{attrs} class="{class_name}"'


def _strip_empty_layout_divs(text: str) -> str:
    prev = None
    while prev != text:
        prev = text
        text = EMPTY_LAYOUT_DIV_RE.sub("", text)
    return text


def _unwrap_overflow_containers(text: str) -> str:
    while True:
        match = OVERFLOW_OPEN_RE.search(text)
        if not match:
            return text
        start = match.start()
        inner_start = match.end()
        depth = 1
        pos = inner_start
        closed = False
        while pos < len(text):
            next_open = text.find("<div", pos)
            next_close = text.find("</div>", pos)
            if next_close == -1:
                break
            if next_open != -1 and next_open < next_close:
                depth += 1
                pos = next_open + 4
                continue
            depth -= 1
            if depth == 0:
                inner = text[inner_start:next_close]
                text = text[:start] + inner + text[next_close + len("</div>") :]
                closed = True
                break
            pos = next_close + len("</div>")
        if not closed:
            return text


def _strip_overflow_comments(text: str) -> str:
    return OVERFLOW_COMMENT_RE.sub("", text)


def _mark_chart_panels(text: str) -> str:
    def replacer(match: re.Match[str]) -> str:
        opening = match.group(0)
        if "beook-export-chart-panel" in opening:
            return opening
        return CLASS_ATTR_RE.sub(
            lambda class_match: f'class="{class_match.group(1)} beook-export-chart-panel"',
            opening,
            count=1,
        )

    return re.sub(
        r'<div class="[^"]*Grafikrahmen[^"]*"',
        replacer,
        text,
        flags=re.IGNORECASE,
    )


def _insert_chart_page_breaks(text: str) -> str:
    if "beook-export-chart-panel" not in text:
        return text
    panel_count = 0

    def replacer(match: re.Match[str]) -> str:
        nonlocal panel_count
        panel_count += 1
        prefix = ""
        if panel_count > 1 and (panel_count - 1) % CHART_PANELS_PER_PAGE == 0:
            prefix = '<div class="beook-export-chart-page-break" aria-hidden="true"></div>\n\t\t\t'
        return prefix + match.group(0)

    return CHART_LAYOUT_BLOCK_RE.sub(replacer, text)


def _mark_lernziele_panels(text: str) -> str:
    if "chapter-Lernziele" not in text:
        return text
    return LERNZIELE_WRAPPER_RE.sub(
        r'<div class="idGenObjectStyleOverride-2 beook-export-lernziele-panel">\1',
        text,
        count=1,
    )


def _trim_leading_wrapper_junk(text: str) -> str:
    def replacer(match: re.Match[str]) -> str:
        tail = text[match.end() :]
        if re.match(r"\s*(?:<!--[^>]*-->\s*)*(?:<h[1-6]|<p|<table|<ul|<ol|<div class=\"(?:Einfacher|Marginal|Praxis|Bilder))", tail, re.IGNORECASE):
            return f"{match.group(1)}\n\t\t\t"
        return match.group(0)

    return WRAPPER_LEADING_JUNK_RE.sub(replacer, text, count=1)


def _strip_trailing_body_whitespace(text: str) -> str:
    for marker in ("<div class='pageNavigationTable'>", '<div class="pageNavigationTable">'):
        idx = text.rfind(marker)
        if idx != -1:
            return text[:idx].rstrip() + "\n\n\t" + text[idx:]
    return text


def _classify_tables(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        attrs, body = match.group(1), match.group(2)
        if _is_navigation_table(attrs):
            return match.group(0)
        attrs = _append_table_class(attrs, BASE_TABLE_CLASS)
        if _is_chart_table(attrs, body):
            attrs = _append_table_class(attrs, CHART_TABLE_CLASS)
        elif _is_data_table(attrs, body):
            attrs = _append_table_class(attrs, DATA_TABLE_CLASS)
        elif _is_image_layout_table(attrs, body):
            attrs = _append_table_class(attrs, IMAGE_GRID_CLASS)
        elif re.search(r"tabelle|tabellenformat", attrs, re.IGNORECASE):
            attrs = _append_table_class(attrs, DATA_TABLE_CLASS)
        return f"<table{attrs}>{body}</table>"

    return _classify_remaining_table_openings(TABLE_RE.sub(replace, text))


def _classify_remaining_table_openings(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        attrs = match.group(1)
        if _is_navigation_table(attrs):
            return match.group(0)
        attrs = _append_table_class(attrs, BASE_TABLE_CLASS)
        has_specific_reader_class = any(
            class_name in attrs
            for class_name in (DATA_TABLE_CLASS, IMAGE_GRID_CLASS, CHART_TABLE_CLASS)
        )
        if not has_specific_reader_class and re.search(
            r"unsichtbar|tabelle|tabellenformat",
            attrs,
            re.IGNORECASE,
        ):
            attrs = _append_table_class(attrs, DATA_TABLE_CLASS)
        return f"<table{attrs}>"

    return TABLE_OPEN_RE.sub(replace, text)


def _is_pdf2html_page_text(text: str) -> bool:
    return 'id="page-container"' in text and PDF2HTML_PF_CLASS_RE.search(text) is not None


def _is_pdf2html_page_data(data: bytes) -> bool:
    return b'id="page-container"' in data and b'class="pf' in data


def _add_body_class(text: str, class_name: str) -> str:
    match = re.search(r"<body\b([^>]*)>", text, re.IGNORECASE)
    if not match:
        return text
    attrs = match.group(1) or ""
    class_match = re.search(r'\bclass="([^"]*)"', attrs, re.IGNORECASE)
    if class_match:
        classes = class_match.group(1).split()
        if class_name not in classes:
            classes.append(class_name)
        new_attrs = re.sub(
            r'\bclass="[^"]*"',
            f'class="{" ".join(classes)}"',
            attrs,
            count=1,
            flags=re.IGNORECASE,
        )
    else:
        new_attrs = f'{attrs} class="{class_name}"'
    return text[: match.start()] + f"<body{new_attrs}>" + text[match.end() :]


def _strip_pdf2html_kerning_spans(text: str) -> str:
    text = PDF2HTML_KERNING_SPAN_RE.sub("", text)
    return PDF2HTML_WHITESPACE_SPAN_RE.sub("", text)


def _pdf2html_hex_sort_key(token: str) -> int:
    if not token or len(token) < 2:
        return 0
    try:
        return int(token[1:], 16)
    except ValueError:
        return 0


def _pdf2html_pos_tokens(classes: str) -> tuple[str | None, str | None]:
    tokens = classes.split()
    y_token = next((token for token in tokens if re.fullmatch(r"y[0-9a-f]+", token)), None)
    x_token = next((token for token in tokens if re.fullmatch(r"x[0-9a-f]+", token)), None)
    return y_token, x_token


def _pdf2html_clean_inline(html: str) -> str:
    html = _strip_pdf2html_kerning_spans(html)
    html = PDF2HTML_PRIVATE_USE_RE.sub("", html)
    html = PDF2HTML_HYPHEN_TAG_RE.sub("", html)
    html = re.sub(r"([a-zäöüß])(Zeichen:)", r"\1 \2", html, flags=re.IGNORECASE)
    html = re.sub(r"\s+", " ", html).strip()
    return html


def _pdf2html_plain(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _pdf2html_is_subscript_line(
    classes: str,
    plain: str,
    row_parts: list[tuple[str, str, str]],
) -> bool:
    if plain == "_":
        return False
    if plain not in {"m", "b", "s"}:
        return False
    tokens = set(classes.split())
    if not tokens.intersection(PDF2HTML_SUBSCRIPT_HEIGHTS | PDF2HTML_SUBSCRIPT_FONTS):
        return False
    previous = _pdf2html_plain(" ".join(part for _, part, _ in row_parts))
    return bool(re.search(r"\b[tr]\s*$", previous))


def _pdf2html_is_superscript_line(
    classes: str,
    plain: str,
    row_parts: list[tuple[str, str, str]] | None = None,
) -> bool:
    if _pdf2html_is_subscript_line(classes, plain, row_parts or []):
        return False
    if not plain or len(plain) > 4:
        return False
    tokens = set(classes.split())
    if tokens.intersection(PDF2HTML_SUPERSCRIPT_HEIGHTS):
        return True
    if tokens.intersection(PDF2HTML_SUPERSCRIPT_FONTS) and plain.isdigit():
        return True
    return plain in {"2", "3", "(", ")"} and bool(tokens.intersection(PDF2HTML_SUPERSCRIPT_HEIGHTS))


def _pdf2html_is_fact_line(plain: str) -> bool:
    compact = plain.replace(" ", "")
    if "Zeichen:" in plain or "Zeichen:" in compact:
        return True
    stripped = plain.strip()
    return stripped.startswith(("Kraft", "Gravitationsgesetz")) and len(stripped) < 160


def _pdf2html_is_caption_line(plain: str) -> bool:
    normalized = PDF2HTML_PRIVATE_USE_RE.sub("", plain).strip()
    return bool(re.match(r"^(Tabelle|Abb\.?)\s*\d", normalized, re.IGNORECASE))


def _pdf2html_is_heading_classes(classes: str) -> bool:
    tokens = set(classes.split())
    return bool(tokens.intersection({"h7", "h9", "h10", "h2"})) and "fc0" in tokens


def _pdf2html_should_split_columns(combined_plain: str) -> bool:
    if _pdf2html_is_fact_line(combined_plain):
        return False
    if _pdf2html_is_caption_line(combined_plain):
        return False
    if PDF2HTML_FORMULA_MARKERS_RE.search(combined_plain):
        return False
    if re.search(r"\b(Energie|reagier|exotherm|endotherm|Silber|Schwefel)\b", combined_plain, re.I):
        return False
    return True


# German legal / textbook labels often arrive glued after pdf2html reflow.
_PDF2HTML_GLUE_LABELS = (
    "Rechtsform",
    "Beispiele",
    "Anzahl",
    "Artikel",
    "Wirkung",
    "Erläuterung",
    "Beiträge",
    "Jeder",
    "Gewinn",
    "Verlust",
    "Gesellschaft",
    "Gesellschafter",
    "Mitglieder",
    "Haftung",
    "Einzelunternehmen",
    "Stiftung",
    "Verein",
    "GmbH",
    "AG",
    "Konstitutive",
    "Deklaratorische",
    "Geschäftsführung",
    "Gesellschaftsbeschlüsse",
    "Gesellschaftsbeschluss",
)


def _pdf2html_split_glued_words(text: str) -> str:
    """Insert spaces into lowerUPPER glues and known German label compounds."""
    if not text:
        return text
    # lowerUPPER → lower UPPER. Loop because one pass only splits the first
    # boundary in chains like EinzelunternehmenGesellschaftenStiftung.
    for _ in range(6):
        updated = re.sub(
            r"([a-zäöüß]{2,})([A-ZÄÖÜ][a-zäöüß]{2,})",
            r"\1 \2",
            text,
        )
        if updated == text:
            break
        text = updated
    # Known label heads glued to the next capitalised word.
    for label in sorted(_PDF2HTML_GLUE_LABELS, key=len, reverse=True):
        text = re.sub(
            rf"({re.escape(label)})([A-ZÄÖÜ])",
            r"\1 \2",
            text,
        )
    # "Gewinn und VerlustJeder" style after partial spaces already exist.
    text = re.sub(
        r"\b(Verlust|Gewinn|Beitrag|Beiträge|Beschlüsse|Beschluss)(Jeder|Jeder|aller|alle)\b",
        r"\1 \2",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s{2,}", " ", text)
    return text


def _pdf2html_flatten_markup(html: str) -> str:
    html = PDF2HTML_WS_HYPHEN_SPAN_RE.sub("-", html)
    col_spans: list[str] = []

    def stash_col(match: re.Match[str]) -> str:
        col_spans.append(match.group(0))
        return f"__PDF2HTML_COL_{len(col_spans) - 1}__"

    html = PDF2HTML_TWO_COL_ROW_RE.sub(stash_col, html)
    prev = None
    while prev != html:
        prev = html
        html = PDF2HTML_FONT_SPAN_RE.sub(r"\1", html)
        html = re.sub(r'<span class="ls\d+[^"]*">([^<]*)</span>', r"\1", html, flags=re.IGNORECASE)
    html = re.sub(r'<span class="(?:ff|fs|fc|ls|ws|sc|_|ligature|resolvedligature)[^"]*"[^>]*>', "", html, flags=re.IGNORECASE)
    html = re.sub(r'<span[^>]*\bdata-ligature[^>]*>', "", html, flags=re.IGNORECASE)
    html = re.sub(r"</span>", "", html)
    while html.count("<a ") > html.count("</a>"):
        html += "</a>"
    for index, span_html in enumerate(col_spans):
        html = html.replace(f"__PDF2HTML_COL_{index}__", span_html)
    return html


def _pdf2html_sanitize_inline_html(html: str) -> str:
    return _pdf2html_flatten_markup(html).strip()


def _pdf2html_split_col_block(line_html: str) -> list[str]:
    col_match = PDF2HTML_TWO_COL_ROW_RE.search(line_html)
    if not col_match:
        return [line_html]
    return [
        _pdf2html_sanitize_inline_html(part)
        for part in (col_match.group(1), col_match.group(2))
        if _pdf2html_plain(part)
    ]


def _pdf2html_emit_caption_block(line_html: str, line_plain: str) -> str:
    tag = "h4" if re.search(r"\bAbb", line_plain, re.IGNORECASE) else "h3"
    return (
        f'<{tag} class="beook-export-pdf2html-caption">'
        f"{_pdf2html_sanitize_inline_html(line_html)}</{tag}>"
    )


def _pdf2html_join_row_parts(row: list[tuple[str, str, str]]) -> str:
    row.sort(key=lambda item: _pdf2html_hex_sort_key(item[0]))
    parts = [part.strip() for _, part, _ in row if part.strip()]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    x_positions = [_pdf2html_hex_sort_key(x) for x, part, _ in row if part.strip()]
    if len(x_positions) >= 2:
        gap = x_positions[-1] - x_positions[0]
        combined_plain = _pdf2html_plain(" ".join(parts))
        left_plain = _pdf2html_plain(parts[0])
        right_plain = _pdf2html_plain(parts[1]) if len(parts) > 1 else ""
        # Legal textbooks often put a short label left and Art. / body right.
        legal_pair = bool(
            re.search(r"\bArt\.?\s*\d", right_plain, re.IGNORECASE)
            or (
                4 <= len(left_plain) <= 48
                and len(right_plain) >= 12
                and not left_plain.endswith((".", ":", ";"))
            )
        )
        if gap >= PDF2HTML_TWO_COL_MIN_GAP and (
            _pdf2html_should_split_columns(combined_plain) or legal_pair
        ):
            if len(left_plain) >= 4 and len(right_plain) >= 4:
                return (
                    f'<span class="beook-export-pdf2html-col-left">{parts[0]}</span>'
                    f'<span class="beook-export-pdf2html-col-right">{parts[1]}</span>'
                )
    return " ".join(parts)


def _pdf2html_wrap_table_blocks(blocks: list[str]) -> list[str]:
    result: list[str] = []
    table_rows: list[tuple[str, str]] = []

    def flush_table() -> None:
        nonlocal table_rows
        if not table_rows:
            return
        result.append('<table class="beook-export-pdf2html-table beook-export-table"><tbody>')
        for left, right in table_rows:
            left = _pdf2html_sanitize_inline_html(left)
            right = _pdf2html_sanitize_inline_html(right)
            result.append(f"<tr><td>{left}</td><td>{right}</td></tr>")
        result.append("</tbody></table>")
        table_rows = []

    for block in blocks:
        col_match = PDF2HTML_TWO_COL_ROW_RE.search(block)
        if col_match and block.strip().startswith("<p"):
            table_rows.append((col_match.group(1).strip(), col_match.group(2).strip()))
            continue
        flush_table()
        result.append(block)
    flush_table()
    return result


def _pdf2html_block_needs_hyphen_merge(block: str) -> bool:
    inner = block.removesuffix("</p>").rstrip()
    if inner.endswith("-") or inner.endswith("-</span>"):
        return True
    return bool(re.search(r"(?:<hyphen\s*/?>|</hyphen>|-)\s*$", inner, re.IGNORECASE))


def _pdf2html_compact_blocks(blocks: list[str]) -> list[str]:
    compacted: list[str] = []
    soft_wrap_stops = {
        "der",
        "die",
        "das",
        "den",
        "dem",
        "des",
        "und",
        "oder",
        "mit",
        "von",
        "zu",
        "im",
        "in",
        "am",
        "an",
        "auf",
        "für",
        "als",
        "bei",
        "nach",
        "auch",
        "nicht",
        "ein",
        "eine",
        "einer",
        "einem",
        "einen",
    }
    for block in blocks:
        plain = _pdf2html_plain(block)
        if (
            compacted
            and block.startswith('<p class="beook-export-pdf2html-line">')
            and compacted[-1].startswith('<p class="beook-export-pdf2html-line">')
        ):
            if _pdf2html_block_needs_hyphen_merge(compacted[-1]):
                prev_inner = (
                    compacted[-1]
                    .removesuffix("</p>")
                    .removesuffix("<hyphen></hyphen>")
                    .removesuffix("<hyphen/>")
                    .rstrip("-")
                    .rstrip()
                )
                compacted[-1] = f"{prev_inner}{plain}</p>"
                continue
            if plain in {"m", "_", "V"} or (plain and len(plain) <= 2):
                compacted[-1] = compacted[-1].removesuffix("</p>") + plain + "</p>"
                continue
            prev_plain = _pdf2html_plain(compacted[-1])
            # Soft-wrap rejoin: "Gesell" + "schaftsform" (no hyphen in source).
            if (
                prev_plain
                and plain
                and prev_plain[-1].isalpha()
                and plain[0].islower()
                and prev_plain[-1] not in ".!?:;"
            ):
                prev_word = re.split(r"\s+", prev_plain)[-1]
                next_word = re.split(r"\s+", plain, maxsplit=1)[0]
                if (
                    len(prev_word) >= 3
                    and len(next_word) >= 3
                    and prev_word.lower() not in soft_wrap_stops
                    and next_word.lower() not in soft_wrap_stops
                ):
                    prev_inner = compacted[-1].removesuffix("</p>").rstrip()
                    compacted[-1] = f"{prev_inner}{plain}</p>"
                    continue
        if (
            compacted
            and block.startswith('<h3 class="beook-export-pdf2html-heading">')
            and compacted[-1].startswith('<h3 class="beook-export-pdf2html-heading">')
        ):
            prev_plain = _pdf2html_plain(compacted[-1])
            compacted[-1] = (
                '<h3 class="beook-export-pdf2html-heading">'
                f"{prev_plain} {plain}</h3>"
            )
            continue
        compacted.append(block)
    return compacted


def _pdf2html_polish_html(html: str) -> str:
    html = PDF2HTML_HYPHEN_TAG_RE.sub("", html)
    html = _pdf2html_flatten_markup(html)
    html = html.replace("m _V", "m/V").replace("m <sub>_</sub>V", "m/V")
    html = re.sub(r"\s+([,.;:!?])", r"\1", html)

    def polish_text_run(match: re.Match[str]) -> str:
        opening, body, closing = match.group(1), match.group(2), match.group(3)
        body = _pdf2html_sanitize_inline_html(body)
        body = _pdf2html_split_glued_words(body)
        return f"{opening}{body}{closing}"

    html = re.sub(
        r'(<(?:p|li|td|th|h3|h4)\b[^>]*>)(.*?)(</(?:p|li|td|th|h3|h4)>)',
        polish_text_run,
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    return html


def _pdf2html_has_abb_label(html: str) -> bool:
    return bool(re.search(r"\bAbb\.?\s*\d", PDF2HTML_PRIVATE_USE_RE.sub("", html), re.IGNORECASE))


def _pdf2html_split_leading_figures(inner: str) -> tuple[str, str]:
    match = PDF2HTML_FIGURE_LEADING_RE.match(inner.strip())
    if not match:
        return "", inner.strip()
    return match.group(0).strip(), inner.strip()[match.end() :].strip()


def _pdf2html_group_abb_blocks(blocks: list[str]) -> list[str]:
    grouped: list[str] = []
    index = 0
    while index < len(blocks):
        block = blocks[index]
        if (
            block.startswith('<h3 class="beook-export-pdf2html-heading">')
            and _pdf2html_has_abb_label(block)
        ):
            group = [block]
            index += 1
            while index < len(blocks):
                next_block = blocks[index]
                if next_block.startswith('<h3 class="beook-export-pdf2html-heading">') and _pdf2html_has_abb_label(
                    next_block
                ):
                    group.append(next_block)
                    index += 1
                    continue
                if next_block.startswith('<p class="beook-export-pdf2html-line">'):
                    group.append(next_block)
                    index += 1
                    continue
                break
            if len(group) > 1:
                grouped.append(
                    '<div class="beook-export-pdf2html-figure-group">\n'
                    + "\n".join(group)
                    + "\n</div>"
                )
                continue
        grouped.append(block)
        index += 1
    return grouped


def _pdf2html_merge_t_divs(t_divs: list[tuple[str, str]]) -> str:
    if not t_divs:
        return ""

    rows: list[list[tuple[str, str, str]]] = []
    y_to_row_index: dict[str, int] = {}

    for classes, inner in t_divs:
        cleaned = _pdf2html_clean_inline(inner)
        plain = _pdf2html_plain(cleaned)
        if not plain:
            continue

        y_token, x_token = _pdf2html_pos_tokens(classes)
        row_snapshot = rows[-1] if rows else []
        if plain in {"m", "b", "s"} and rows:
            tokens = set(classes.split())
            if tokens.intersection(PDF2HTML_SUBSCRIPT_HEIGHTS | PDF2HTML_SUBSCRIPT_FONTS):
                previous_plain = _pdf2html_plain(_pdf2html_join_row_parts(rows[-1]))
                if re.search(r"Zeichen:\s*t\s*$", previous_plain) or previous_plain.endswith(" t"):
                    rows[-1].append((x_token or "", f"<sub>{plain}</sub>", classes))
                    continue
        if _pdf2html_is_subscript_line(classes, plain, row_snapshot) and rows:
            rows[-1].append((x_token or "", f"<sub>{plain}</sub>", classes))
            continue

        if _pdf2html_is_superscript_line(classes, plain, row_snapshot) and rows:
            rows[-1].append((x_token or "", f"<sup>{plain}</sup>", classes))
            continue

        y_key = y_token or f"_row_{len(rows)}"
        if y_key not in y_to_row_index:
            y_to_row_index[y_key] = len(rows)
            rows.append([])
        rows[y_to_row_index[y_key]].append((x_token or "", cleaned, classes))

    blocks: list[str] = []
    in_list = False

    for row in rows:
        line_html = _pdf2html_join_row_parts(row)
        line_plain = _pdf2html_plain(line_html)
        if not line_plain:
            continue

        row_classes = " ".join(classes for _, _, classes in row)

        if _pdf2html_is_fact_line(line_plain):
            if not in_list:
                blocks.append('<ul class="beook-export-pdf2html-facts">')
                in_list = True
            blocks.append(f"<li>{_pdf2html_sanitize_inline_html(line_html)}</li>")
            continue

        if in_list:
            blocks.append("</ul>")
            in_list = False

        col_parts = _pdf2html_split_col_block(line_html)
        if len(col_parts) > 1 and (
            _pdf2html_is_caption_line(line_plain)
            or _pdf2html_is_heading_classes(row_classes)
            or any(_pdf2html_is_caption_line(_pdf2html_plain(part)) for part in col_parts)
        ):
            for part in col_parts:
                part_plain = _pdf2html_plain(part)
                if _pdf2html_is_caption_line(part_plain):
                    blocks.append(_pdf2html_emit_caption_block(part, part_plain))
                elif _pdf2html_is_heading_classes(row_classes):
                    blocks.append(
                        f'<h3 class="beook-export-pdf2html-heading">{part}</h3>'
                    )
            continue

        if _pdf2html_is_caption_line(line_plain):
            blocks.append(_pdf2html_emit_caption_block(line_html, line_plain))
        elif _pdf2html_is_heading_classes(row_classes):
            blocks.append(f'<h3 class="beook-export-pdf2html-heading">{line_html}</h3>')
        else:
            blocks.append(f'<p class="beook-export-pdf2html-line">{line_html}</p>')

    if in_list:
        blocks.append("</ul>")

    blocks = _pdf2html_wrap_table_blocks(_pdf2html_compact_blocks(blocks))
    blocks = _pdf2html_group_abb_blocks(blocks)
    return _pdf2html_polish_html("\n".join(blocks))


def _pdf2html_svg_is_content_figure(epub_files: dict[str, bytes], src: str) -> bool:
    path = src if src.startswith("OEBPS/") else f"OEBPS/{src}"
    data = epub_files.get(path, b"")
    if len(data) < PDF2HTML_FIGURE_MIN_BYTES or b"<image" not in data:
        return False
    text = data.decode("utf-8", errors="replace")
    return text.count("<path") <= 60


def _tokenize_pdf2html_pc(inner: str) -> list[tuple]:
    tokens: list[tuple] = []
    pos = 0
    while pos < len(inner):
        chunk = inner[pos:]
        img_match = re.match(r"<img\b[^>]*/>", chunk, re.IGNORECASE)
        if img_match:
            tokens.append(("img", img_match.group(0)))
            pos += img_match.end()
            continue
        c_match = PDF2HTML_C_DIV_RE.match(chunk)
        if c_match:
            tokens.append(("c", c_match.group(2)))
            pos += c_match.end()
            continue
        t_match = PDF2HTML_T_DIV_RE.match(chunk)
        if t_match:
            tokens.append(("t", t_match.group(1), t_match.group(2)))
            pos += t_match.end()
            continue
        whitespace = re.match(r"\s+", chunk)
        if whitespace:
            pos += whitespace.end()
            continue
        break
    return tokens


def _reflow_pdf2html_pc_inner(inner: str, epub_files: dict[str, bytes]) -> str:
    parts: list[str] = []
    pending_t: list[tuple[str, str]] = []
    figure_budget = PDF2HTML_MAX_FIGURES_PER_PC

    def flush_t() -> None:
        nonlocal pending_t
        if not pending_t:
            return
        merged = _pdf2html_merge_t_divs(pending_t)
        if merged:
            parts.append(merged)
        pending_t = []

    for token in _tokenize_pdf2html_pc(inner):
        kind = token[0]
        if kind == "img":
            flush_t()
            img_html = token[1]
            if 'class="bi"' in img_html:
                parts.append(f'<figure class="beook-export-pdf2html-figure">{img_html}</figure>')
                continue
            if 'class="bf"' not in img_html:
                continue
            src_match = re.search(r'src="([^"]+)"', img_html, re.IGNORECASE)
            if (
                figure_budget > 0
                and src_match
                and _pdf2html_svg_is_content_figure(epub_files, src_match.group(1))
            ):
                src = src_match.group(1)
                figure_budget -= 1
                parts.append(
                    '<figure class="beook-export-pdf2html-figure">'
                    f'<img src="{src}" class="beook-export-pdf2html-figure-img" alt="" />'
                    "</figure>"
                )
            continue
        if kind == "c":
            flush_t()
            c_inner = token[1]
            t_divs = [(match.group(1), match.group(2)) for match in PDF2HTML_T_DIV_RE.finditer(c_inner)]
            box = _pdf2html_merge_t_divs(t_divs)
            if box:
                parts.append(f'<div class="beook-export-pdf2html-box">{box}</div>')
            continue
        if kind == "t":
            pending_t.append((token[1], token[2]))

    flush_t()
    return "\n".join(parts)


def _find_div_block(text: str, start: int) -> tuple[int, int] | None:
    if start >= len(text) or not text.startswith("<div", start):
        return None
    tag_end = text.find(">", start)
    if tag_end == -1:
        return None
    depth = 1
    pos = tag_end + 1
    while pos < len(text):
        next_open = text.find("<div", pos)
        next_close = text.find("</div>", pos)
        if next_close == -1:
            return None
        if next_open != -1 and next_open < next_close:
            depth += 1
            pos = next_open + 4
            continue
        depth -= 1
        pos = next_close + len("</div>")
        if depth == 0:
            return start, pos
    return None


def _reflow_pdf2html_page(text: str, epub_files: dict[str, bytes]) -> str:
    if not _is_pdf2html_page_text(text):
        return text

    text = _strip_pdf2html_kerning_spans(text)
    text = _add_body_class(text, "beook-export-pdf2html-reflow")
    text = PDF2HTML_PI_DIV_RE.sub("", text)

    matches = list(PDF2HTML_PC_OPEN_RE.finditer(text))
    if not matches:
        return text

    pc_chunks: list[tuple[str, str, str]] = []
    pos = 0
    for match in matches:
        block = _find_div_block(text, match.start())
        if block is None:
            return text
        start, end = block
        inner_start = text.find(">", start) + 1
        inner = text[inner_start : end - len("</div>")]
        opening = text[start:inner_start]
        reflowed = _reflow_pdf2html_pc_inner(inner, epub_files)
        pc_chunks.append((text[pos : match.start()], opening, reflowed))
        pos = end

    for index in range(1, len(pc_chunks)):
        previous_inner = pc_chunks[index - 1][2]
        current_inner = pc_chunks[index][2]
        if not _pdf2html_has_abb_label(previous_inner):
            continue
        figures, remainder = _pdf2html_split_leading_figures(current_inner)
        if not figures:
            continue
        pc_chunks[index - 1] = (
            pc_chunks[index - 1][0],
            pc_chunks[index - 1][1],
            f"{previous_inner}\n{figures}",
        )
        pc_chunks[index] = (pc_chunks[index][0], pc_chunks[index][1], remainder)

    parts: list[str] = []
    for prefix, opening, reflowed in pc_chunks:
        parts.append(prefix)
        parts.append(f"{opening}{reflowed}</div>")
    parts.append(text[pos:])
    return "".join(parts)


def _finalize_pdf2html_pages(files: dict[str, bytes]) -> None:
    for path, data in list(files.items()):
        if not path.endswith((".xhtml", ".html")) or not _is_pdf2html_page_data(data):
            continue
        text = data.decode("utf-8", errors="replace")
        updated = _reflow_pdf2html_page(text, files)
        try:
            ElementTree.fromstring(updated.encode("utf-8"))
        except ElementTree.ParseError:
            updated = _reflow_pdf2html_html(text)
        if updated != text:
            files[path] = updated.encode("utf-8")


# ---------------------------------------------------------------------------
# POS_/FONT_/COL_ absolute-position span pages (e.g. Faire le point theory)
# ---------------------------------------------------------------------------

POS_SPAN_CLASS_RE = re.compile(r'class="[^"]*POS_\d+', re.IGNORECASE)
POS_ID_RE = re.compile(r"POS_(\d+)", re.IGNORECASE)
COL_ID_RE = re.compile(r"COL_(\d+)", re.IGNORECASE)
FONT_ID_RE = re.compile(r"FONT_(\d+)", re.IGNORECASE)
CSS_POS_RULE_RE = re.compile(r"\.POS_(\d+)\s*\{([^}]+)\}", re.IGNORECASE)
KERNING_EMPTY_SPAN_RE = re.compile(
    r'<span[^>]*style="[^"]*position:\s*relative[^"]*"[^>]*>\s*</span>',
    re.IGNORECASE,
)
ORDINAL_SUP_RE = re.compile(r"^(re|er|e|ème|eme|nd|st|th)$", re.IGNORECASE)
WORD_CHAR_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]")
POS_FRAGMENT_TRIPLE_RE = re.compile(
    r"\b([A-Za-zÀ-ÖØ-öø-ÿ]{1,10})\s+([A-Za-zÀ-ÖØ-öø-ÿ]{1,2})\s+([A-Za-zÀ-ÖØ-öø-ÿ]{1,10})\b"
)
POS_DOUBLE_STEM_RE = re.compile(
    r"\b([A-Za-zÀ-ÖØ-öø-ÿ]+)\s+\1\s+([A-Za-zÀ-ÖØ-öø-ÿ]{1,2})\s+([A-Za-zÀ-ÖØ-öø-ÿ]+)\b"
)
POS_FRAGMENT_STOPWORDS = frozenset(
    {
        "a",
        "à",
        "au",
        "ce",
        "de",
        "des",
        "du",
        "en",
        "et",
        "il",
        "je",
        "la",
        "le",
        "les",
        "me",
        "ne",
        "ni",
        "nos",
        "on",
        "ou",
        "sa",
        "se",
        "ses",
        "son",
        "te",
        "tu",
        "un",
        "une",
        "vos",
        "y",
    }
)
# Tokens that start a new table cell / phrase, not a mid-word fragment.
POS_CELL_START_WORDS = frozenset(
    {
        "je",
        "tu",
        "il",
        "elle",
        "on",
        "nous",
        "vous",
        "ils",
        "elles",
        "le",
        "la",
        "les",
        "un",
        "une",
        "des",
        "du",
        "de",
        "et",
        "ou",
        "au",
        "en",
        "ce",
        "se",
        "ne",
        "me",
        "te",
        "y",
        "a",
        "à",
        "as",
        "ai",
        "est",
        "eu",
        "que",
        "qui",
        "son",
        "ses",
        "nos",
        "vos",
        "aux",
        "j'",
        "l'",
        "d'",
        "n'",
        "c'",
        "s'",
        "m'",
        "t'",
        "qu'",
    }
)
# Complete subject pronouns — never glue these to a following verb stem.
POS_SUBJECT_PRONOUNS = frozenset(
    {
        "je",
        "tu",
        "il",
        "elle",
        "on",
        "nous",
        "vous",
        "ils",
        "elles",
        "j'",
    }
)
# Common auxiliary / short verbs that must stay separate after a subject.
POS_AUX_OR_SHORT_VERBS = frozenset(
    {
        "est",
        "sont",
        "a",
        "ont",
        "ai",
        "as",
        "avons",
        "avez",
        "était",
        "étaient",
        "avait",
        "avaient",
        "sera",
        "seront",
        "aura",
        "auront",
        "suis",
        "es",
        "sommes",
        "êtes",
        "va",
        "vont",
        "vais",
        "vas",
        "allons",
        "allez",
        "vient",
        "viennent",
        "venons",
        "venez",
        "viens",
    }
)


def _is_pos_span_page_text(text: str) -> bool:
    count = len(POS_SPAN_CLASS_RE.findall(text))
    if count >= 8:
        return True
    # Chapter intro/TOC cards only have a few title spans plus flowContainer nav.
    return count >= 3 and "flowContainer" in text and "POS_" in text


def _is_pos_span_page_data(data: bytes) -> bool:
    sample = data[:12000].decode("utf-8", errors="replace")
    return _is_pos_span_page_text(sample)


def _pos_font_size_px(props: str, root_px: float = 12.0) -> float:
    """Convert Beook POS font-size (usually em) to CSS pixels."""
    fs_m = re.search(r"font-size:\s*([\d.]+)\s*(em|px|pt|%)?", props, re.IGNORECASE)
    if not fs_m:
        return root_px
    value = float(fs_m.group(1))
    unit = (fs_m.group(2) or "").lower()
    if unit == "px":
        return value
    if unit == "pt":
        return value * (96.0 / 72.0)
    if unit == "%":
        return root_px * value / 100.0
    # em or bare number (Beook stores em sizes like 3.675)
    if unit == "em" or unit == "" or value < 15.0:
        return value * root_px
    return value


def _parse_pos_css_positions(css_text: str) -> dict[int, tuple[float, float, float]]:
    positions: dict[int, tuple[float, float, float]] = {}
    for match in CSS_POS_RULE_RE.finditer(css_text):
        props = match.group(2)
        if re.search(r"display\s*:\s*none", props, re.IGNORECASE):
            continue
        top_m = re.search(r"top:\s*([\d.]+)", props)
        left_m = re.search(r"left:\s*([\d.]+)", props)
        if not top_m or not left_m:
            continue
        positions[int(match.group(1))] = (
            float(top_m.group(1)),
            float(left_m.group(1)),
            _pos_font_size_px(props),
        )
    return positions


def _css_text_for_html(html: str, files: dict[str, bytes]) -> str:
    chunks: list[str] = []
    for match in re.finditer(r'href="([^"]+\.css)"', html, re.IGNORECASE):
        href = match.group(1)
        # href is relative to OEBPS/
        name = href.split("/")[-1]
        for path, data in files.items():
            if path.endswith(name) or path.endswith("/" + href) or path.endswith(href):
                chunks.append(data.decode("utf-8", errors="replace"))
                break
    return "\n".join(chunks)


def _extract_pos_spans(
    html: str,
    positions: dict[int, tuple[float, float, float]],
) -> list[dict[str, object]]:
    html = KERNING_EMPTY_SPAN_RE.sub("", html)
    html = re.split(r'<div class="flowContainer"', html, maxsplit=1)[0]
    html = re.sub(
        r'<span class="SVG_[^"]*"[\s\S]*?</span>',
        "",
        html,
        flags=re.IGNORECASE,
    )
    html = re.sub(r'<a\b[^>]*\bid="page-[^"]*"[^>]*>\s*</a>', "", html, flags=re.IGNORECASE)

    spans: list[dict[str, object]] = []
    for match in re.finditer(r'<span class="([^"]*)"[^>]*>(.*?)</span>', html, re.IGNORECASE | re.DOTALL):
        classes = match.group(1)
        pos_m = POS_ID_RE.search(classes)
        if not pos_m:
            continue
        pid = int(pos_m.group(1))
        if pid not in positions:
            continue
        top, left, font_size = positions[pid]
        inner = match.group(2)
        text = re.sub(r"<[^>]+>", "", inner)
        text = text.replace("\u200d", "").replace("\xa0", " ")
        col_m = COL_ID_RE.search(classes)
        spans.append(
            {
                "pid": pid,
                "top": top,
                "left": left,
                "fs": font_size,
                "text": text,
                "col": int(col_m.group(1)) if col_m else 0,
            }
        )
    return spans


def _pos_collapse_fragments(text: str) -> str:
    """Join mid-word letter highlights and repair common spacing damage."""

    def triple_repl(match: re.Match[str]) -> str:
        middle = match.group(2).lower()
        if middle in POS_FRAGMENT_STOPWORDS:
            return match.group(0)
        return f"{match.group(1)}{match.group(2)}{match.group(3)}"

    for _ in range(4):
        updated = POS_DOUBLE_STEM_RE.sub(r"\1 \1\2\3", text)
        updated = POS_FRAGMENT_TRIPLE_RE.sub(triple_repl, updated)
        if updated == text:
            break
        text = updated

    text = re.sub(
        r"\b(beaucoup|assez|trop|peu|plein|moins|pas|besoin)d(['’e])",
        r"\1 d\2",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\b(de)l(['’])", r"\1 l\2", text, flags=re.IGNORECASE)
    # a.Verbes / Ex.:Je / missing space after punctuation
    text = re.sub(r"([.:;!?])([A-Za-zÀ-ÖØ-öø-ÿ])", r"\1 \2", text)
    # lowerUPPER word boundary (rare but useful)
    text = re.sub(r"([a-zà-ÿ])([A-ZÀ-Ö])", r"\1 \2", text)
    # CapitalizedWordde → CapitalizedWord de (not certitude → certitu de)
    text = re.sub(
        r"\b([A-ZÀ-Ö][a-zà-ÿ]{3,})(de|des|du)\b",
        r"\1 \2",
        text,
    )
    # Je/regrette glued after examples
    text = re.sub(
        r"\b(Je|Tu|Il|Elle|On|Nous|Vous|Ils|Elles)([a-zà-ÿ]{3,})",
        r"\1 \2",
        text,
    )
    # Section labels: a. Verbes / b. Verbes
    text = re.sub(r"\b([a-g])\.(\s*)([A-Za-zÀ-Ö])", r"\1. \3", text)
    # Rejoin words broken mid-token: "Expr essions", "Supe rlatif", "Conj onctions"
    _split_stop = {
        "Le",
        "La",
        "Les",
        "Un",
        "Une",
        "Je",
        "Tu",
        "Il",
        "On",
        "Ce",
        "De",
        "Du",
        "Des",
        "Au",
        "En",
        "Et",
        "Ou",
        "Si",
        "Sa",
        "Se",
        "Ma",
        "Ta",
        "Ex",
    }

    def _rejoin_split_word(match: re.Match[str]) -> str:
        left, right = match.group(1), match.group(2)
        if left in _split_stop:
            return match.group(0)
        if len(left) <= 4 and right[:1].islower():
            return left + right
        return match.group(0)

    text = re.sub(
        r"\b([A-ZÀ-Ö][a-zà-ÿ]{1,4})\s+([a-zà-ÿ]{2,12})\b",
        _rejoin_split_word,
        text,
    )
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def _pos_horizontal_clusters(
    spans: list[dict[str, object]],
    line_tol: float,
) -> list[list[dict[str, object]]]:
    ordered = sorted(spans, key=lambda s: (float(s["top"]), float(s["left"])))
    clusters: list[list[dict[str, object]]] = []
    current: list[dict[str, object]] = []
    current_top: float | None = None
    for span in ordered:
        top = float(span["top"])
        if current_top is None or abs(top - current_top) > line_tol:
            if current:
                clusters.append(current)
            current = [span]
            current_top = top
        else:
            current.append(span)
    if current:
        clusters.append(current)
    return clusters


def _pos_detect_column_split(spans: list[dict[str, object]]) -> float | None:
    """Return a 2-column split X, or None for single-column pages."""
    if len(spans) < 20:
        return None
    font_sizes = sorted(float(s["fs"]) for s in spans)
    body_fs = font_sizes[len(font_sizes) // 2]
    line_tol = max(14.0, body_fs * 2.8)
    clusters = _pos_horizontal_clusters(spans, line_tol)
    page_left = min(float(s["left"]) for s in spans)
    page_right = max(float(s["left"]) for s in spans)
    page_width = max(page_right - page_left, 1.0)

    votes: list[float] = []
    for cluster in clusters:
        row = sorted(cluster, key=lambda s: float(s["left"]))
        if len(row) < 2:
            continue
        best_gap = 0.0
        best_mid = 0.0
        for index in range(len(row) - 1):
            left_a = float(row[index]["left"])
            left_b = float(row[index + 1]["left"])
            # Approximate end of previous token
            prev_fs = float(row[index]["fs"])
            prev_end = left_a + max(len(str(row[index]["text"])), 1) * prev_fs * 0.5
            gap = left_b - prev_end
            if gap > best_gap:
                best_gap = gap
                best_mid = (prev_end + left_b) / 2.0
        # Require a wide gutter roughly in the middle of the page.
        if best_gap < max(200.0, body_fs * 8):
            continue
        rel = (best_mid - page_left) / page_width
        if 0.28 <= rel <= 0.72:
            votes.append(best_mid)

    # Need a consistent gutter on enough lines.
    if len(votes) < max(4, len(clusters) // 5):
        return None
    votes.sort()
    return votes[len(votes) // 2]


def _reflow_pos_spans_to_lines(
    spans: list[dict[str, object]],
) -> list[tuple[float, int, str]]:
    """Reflow one column of spans into (font_size, color_id, html_line) rows."""
    usable = [s for s in spans if str(s["text"]) != ""]
    if not usable:
        return []

    font_sizes = sorted(float(s["fs"]) for s in usable)
    body_fs = font_sizes[len(font_sizes) // 2]
    line_tol = max(14.0, body_fs * 2.8)

    ordered = sorted(usable, key=lambda s: (float(s["top"]), float(s["left"])))
    clusters: list[list[dict[str, object]]] = []
    current: list[dict[str, object]] = []
    current_top: float | None = None
    for span in ordered:
        top = float(span["top"])
        if current_top is None or abs(top - current_top) > line_tol:
            if current:
                clusters.append(current)
            current = [span]
            current_top = top
        else:
            current.append(span)
    if current:
        clusters.append(current)

    lines: list[tuple[float, int, str]] = []
    highlight_cols = {0, 1, 5, 125}
    for cluster in clusters:
        cluster = sorted(cluster, key=lambda s: float(s["left"]))
        parts: list[str] = []
        prev: dict[str, object] | None = None
        for span in cluster:
            text = str(span["text"])
            fs = float(span["fs"])
            col = int(span["col"])
            if prev is None:
                if ORDINAL_SUP_RE.match(text.strip()) and fs < body_fs * 0.8:
                    parts.append(f"<sup>{escape(text.strip())}</sup>")
                else:
                    parts.append(escape(text))
                prev = span
                continue

            prev_text = str(prev["text"])
            prev_col = int(prev["col"])
            is_sup = fs < body_fs * 0.75 and ORDINAL_SUP_RE.match(text.strip())
            is_insert = (
                len(text.strip()) <= 2
                and col not in highlight_cols
                and WORD_CHAR_RE.search(text) is not None
            )
            prev_insert = (
                len(prev_text.strip()) <= 2
                and prev_col not in highlight_cols
                and WORD_CHAR_RE.search(prev_text) is not None
            )
            mid_word = _pos_should_join_fragments(prev, span)
            join_tight = is_sup or is_insert or prev_insert or mid_word

            if is_sup:
                parts.append(f"<sup>{escape(text.strip())}</sup>")
            elif join_tight:
                parts.append(escape(text))
            else:
                parts.append(" " + escape(text))
            prev = span

        raw = "".join(parts)
        plain_join = _pos_collapse_fragments(re.sub(r"<[^>]+>", "", raw))
        if "<sup>" in raw:
            # Re-apply spacing fixes on a stripped copy, then keep sup tags if still valid
            line_html = re.sub(r"\s{2,}", " ", raw).strip()
            line_html = re.sub(r"\s+(</sup>)", r"\1", line_html)
            line_html = re.sub(r"(<sup>)\s+", r"\1", line_html)
            # Still run punctuation spacing on text nodes lightly
            line_html = re.sub(r"([.:;!?])([A-Za-zÀ-ÖØ-öø-ÿ])", r"\1 \2", line_html)
        else:
            line_html = escape(plain_join)
        if not line_html.strip():
            continue
        lines.append((float(cluster[0]["fs"]), int(cluster[0]["col"]), line_html))
    return lines


def _pos_lines_to_blocks(lines: list[tuple[float, int, str]]) -> list[str]:
    if not lines:
        return []
    font_sizes = sorted(fs for fs, _, _ in lines)
    body_fs = font_sizes[len(font_sizes) // 2]
    blocks: list[str] = []
    prev_plain = ""
    for fs, col, html in lines:
        plain = re.sub(r"<[^>]+>", "", html).strip()
        plain_key = plain.lower()
        if not plain or plain in {"!", "！", "|", "•"}:
            continue
        if plain_key and plain_key == prev_plain:
            continue
        # a. / b. section labels → subheading
        if re.match(r"^[a-g]\.\s+\S", plain, re.IGNORECASE):
            blocks.append(f'    <h3 class="beook-export-pos-subheading">{html}</h3>')
        elif (fs >= body_fs * 1.25 or col in (5, 125)) and len(plain) >= 3:
            blocks.append(f'    <h2 class="beook-export-pos-heading">{html}</h2>')
        elif col in (31, 35) and len(plain) > 24:
            blocks.append(f'    <p class="beook-export-pos-callout">{html}</p>')
        else:
            blocks.append(f"    <p>{html}</p>")
        prev_plain = plain_key
    return _merge_soft_wrapped_blocks(blocks)


def _pos_lines_to_body_html(lines: list[tuple[float, int, str]]) -> str:
    return "\n".join(_pos_lines_to_blocks(lines))


def _merge_soft_wrapped_blocks(blocks: list[str]) -> list[str]:
    """Join lines that were hard-wrapped by the original page layout."""
    if not blocks:
        return blocks
    merged: list[str] = []
    for block in blocks:
        if not merged:
            merged.append(block)
            continue
        prev = merged[-1]
        prev_is_p = prev.strip().startswith("<p")
        cur_is_p = block.strip().startswith("<p")
        if not (prev_is_p and cur_is_p):
            merged.append(block)
            continue
        prev_plain = re.sub(r"<[^>]+>", "", prev).strip()
        cur_plain = re.sub(r"<[^>]+>", "", block).strip()
        if not prev_plain or prev_plain[-1] in ".!?…:":
            merged.append(block)
            continue
        if cur_plain[:1].isupper() and not prev_plain.endswith((",", ";", "–", "-")):
            merged.append(block)
            continue
        prev_inner = re.sub(r"^<p[^>]*>|</p>\s*$", "", prev.strip())
        cur_inner = re.sub(r"^<p[^>]*>|</p>\s*$", "", block.strip())
        cls = ' class="beook-export-pos-callout"' if "callout" in prev else ""
        merged[-1] = f"    <p{cls}>{prev_inner} {cur_inner}</p>"
    return merged


def _extract_pos_toc_list(html: str) -> str:
    """Turn Beook pageNavigation-toc anchors into a real list."""
    items: list[str] = []
    for match in re.finditer(
        r"<a\b[^>]*class=['\"]pageNavigation-toc['\"][^>]*>(.*?)</a>",
        html,
        re.IGNORECASE | re.DOTALL,
    ):
        label = re.sub(r"<[^>]+>", " ", match.group(1))
        label = re.sub(r"\s+", " ", label).strip()
        if label:
            items.append(f"      <li>{escape(label)}</li>")
    if not items:
        return ""
    return (
        '    <ul class="beook-export-pos-toc">\n'
        + "\n".join(items)
        + "\n    </ul>"
    )


def _pos_fixed_page_metrics(
    positions: dict[int, tuple[float, float, float]],
) -> tuple[float, float]:
    if not positions:
        return 1200.0, 1600.0
    tops = [top for top, _left, _fs in positions.values()]
    lefts = [left for _top, left, _fs in positions.values()]
    font_sizes = [fs for _top, _left, fs in positions.values()]
    # Leave room for the rightmost glyph runs (positions mark left edges only).
    pad = max(font_sizes) * 12.0 if font_sizes else 160.0
    width = max(lefts) + max(pad, 180.0)
    height = max(tops) + max(pad, 160.0)
    return max(width, 800.0), max(height, 1000.0)


def _pos_repair_glued_token(text: str) -> str:
    """Repair common POS fragment damage inside a single token."""
    if not text:
        return text
    # Beook often stores plural subjects as "Il s" / "Elle s" for styling.
    text = re.sub(r"\b(Il|Elle)\s+s\b", r"\1s", text)
    text = re.sub(r"\b(il|elle)\s+s\b", r"\1s", text)
    # Mid-stem splits that survived geometric join: D'autr es → D'autres.
    text = re.sub(r"\b([Dd]['’]autr)\s+(es)\b", r"\1\2", text)
    text = re.sub(r"\b(autr)\s+(es)\b", r"\1\2", text, flags=re.IGNORECASE)
    # Phrase glue: Verbescomme« → Verbes comme «
    text = re.sub(
        r"([a-zà-ÿ])(comme)\b",
        r"\1 \2",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"([a-zà-ÿ«])(«)", r"\1 \2", text)
    text = re.sub(r"(«)([A-Za-zÀ-ÖØ-öø-ÿ])", r"\1 \2", text)
    text = re.sub(r"([A-Za-zÀ-ÖØ-öø-ÿ])(»)", r"\1 \2", text)
    # COCOS-style spaced endings after a stem: cueill es → cueilles
    text = re.sub(
        r"\b([a-zà-ÿ]{3,})\s+(e|es|ons|ez|ent|ai|ais|ait)\b(?=\s*[,.]|$)",
        r"\1\2",
        text,
        flags=re.IGNORECASE,
    )
    # Verb glued to même / aussi / encore (Il sortmême → Il sort même)
    text = re.sub(
        r"\b([A-Za-zÀ-ÿ]{3,})(même|aussi|encore|toujours|jamais)\b",
        r"\1 \2",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s{2,}", " ", text)
    # Complete pronouns are done.
    if re.match(r"(?i)^(ils|elles|nous|vous|je|tu|il|elle|on)$", text.strip()):
        return text
    # Leading pronoun glued to a verb (Ilsontperdu → Ils ont perdu).
    # Require ≥2 following letters so "Ils" is not re-split into "Il" + "s".
    match = re.match(
        r"(?i)^(ils|elles|nous|vous|elle|je|tu|il|on)(?=[a-zà-öø-ÿ]{2,})",
        text,
    )
    if not match:
        return text
    head = text[: match.end(1)]
    tail = text[match.end(1) :]
    aux = re.match(
        r"(?i)(sont|étaient|étaient|avaient|avons|avez|était|avait|"
        r"seront|auront|sommes|êtes|vient|viennent|venons|venez|"
        r"vont|allons|allez|est|ont|ai|as|a|suis|es|va|vais|vas|viens)(.*)$",
        tail,
    )
    if aux:
        verb, rest = aux.group(1), aux.group(2)
        if rest and re.match(r"[a-zà-öø-ÿ]", rest):
            return f"{head} {verb} {rest}"
        return f"{head} {verb}{rest}"
    return f"{head} {tail}"


def _fix_pos_css_units(css_text: str) -> str:
    """Browsers ignore unitless top/left; Beook CSS often omits px.

    Must not mangle values that already have units (including glued forms
    like ``0.0em`` / ``30.27em``) — a naive digit match backtracks into
    ``0.px0em`` and destroys layout.
    """

    def add_px(match: re.Match[str]) -> str:
        return f"{match.group(1)}: {match.group(2)}px"

    # Only pure lengths with no unit token immediately after the number.
    css_text = re.sub(
        r"(?i)(?<![a-z-])(top|left|right|bottom|width|height|letter-spacing|word-spacing)"
        r"\s*:\s*"
        r"(-?(?:\d+\.\d+|\d+|\.\d+))"
        r"(?![0-9.])"
        r"(?!\s*(?:px|em|%|pt|rem|vh|vw|ex|ch))",
        add_px,
        css_text,
    )
    # Repair damage from older builds if present.
    css_text = re.sub(r"(\d)\.px(\d+em)", r"\1.\2", css_text)
    css_text = re.sub(r"(\d+\.\d+)px(\d+em)", r"\1\2", css_text)
    css_text = re.sub(r"(\d+)px(\d+%)", r"\1\2", css_text)
    return css_text


def _pos_estimated_end(left: float, text: str, font_size: float) -> float:
    plain = re.sub(r"\s+", " ", text).strip()
    if not plain:
        return left
    # Average glyph width for the textbook subset fonts (~0.52em).
    return left + max(len(plain), 1) * font_size * 0.52


def _pos_should_join_fragments(
    prev: dict[str, object],
    curr: dict[str, object],
) -> bool:
    """Whether two same-line POS spans are mid-word fragments to glue."""
    prev_text = str(prev.get("text", ""))
    curr_text = str(curr.get("text", ""))
    if not curr_text.strip():
        return True
    if not prev_text.strip():
        return True
    # Explicit whitespace at the boundary means separate words/cells.
    if prev_text[-1].isspace() or curr_text[0].isspace():
        return False

    prev_plain = prev_text.strip()
    curr_plain = curr_text.strip()
    prev_ch = prev_plain[-1]
    curr_ch = curr_plain[0]
    # Punctuation boundaries are never mid-word (a. | Verbes, « | finir).
    if prev_ch in ".:;!?»«)]}|" or curr_ch in "«»":
        return False

    prev_fs = float(prev.get("fs") or 12.0)
    curr_fs = float(curr.get("fs") or 12.0)
    body_fs = max((prev_fs + curr_fs) / 2.0, 12.0)
    # Measure from the last *non-empty* absorbed piece so multi-step joins
    # (Dor + m + ir) do not accumulate width-estimate error.
    anchor_left = float(prev.get("last_left", prev["left"]))
    anchor_text = str(prev.get("last_text", prev_plain))
    anchor_fs = float(prev.get("last_fs", prev_fs))
    est_end = _pos_estimated_end(anchor_left, anchor_text, anchor_fs)
    gap = float(curr["left"]) - est_end
    left_delta = float(curr["left"]) - anchor_left

    def _glyph_join() -> bool:
        if WORD_CHAR_RE.search(prev_ch) and WORD_CHAR_RE.search(curr_ch):
            return True
        if WORD_CHAR_RE.search(prev_ch) and curr_ch == "-":
            return True
        if prev_ch == "-" and WORD_CHAR_RE.search(curr_ch):
            return True
        return False

    if not _glyph_join():
        return False

    # Pronouns / articles mark cell boundaries even when the geometric gap is
    # small (Cueillir | je cueille, Je | regrette).
    first = re.split(r"[\s,;:]", curr_plain, maxsplit=1)[0].lower()
    first_key = (
        first[:2]
        if first.startswith(("j'", "l'", "d'", "n'", "c'", "s'", "m'", "t'", "qu'"))
        else first
    )
    if first in POS_CELL_START_WORDS or first_key in POS_CELL_START_WORDS:
        return False

    prev_token = prev_plain.lower()
    # "Il" + "s" / "Elle" + "s" are plural pronouns, not Il+verb.
    if prev_token in {"il", "elle"} and curr_plain.lower() == "s":
        return gap < body_fs * 1.2
    # Complete subject pronoun → never absorb the following verb/word.
    # Fixes Ilvient / Ilsont / Nousvenons from over-eager geometric joins.
    if prev_token in POS_SUBJECT_PRONOUNS:
        return False
    if first in POS_AUX_OR_SHORT_VERBS and len(prev_plain) >= 2:
        return False

    # Gap from the previous glyph run is the reliable signal. Absolute
    # left-delta is misleading for long stems ("nous cueill" + "ons").
    if gap < body_fs * 0.9:
        # Two substantial words with a clearly word-sized gap are neighbors.
        # Keep threshold high enough that highlight endings like "eons"/"çons"
        # still merge onto "mang"/"commen".
        if (
            len(prev_plain) >= 3
            and len(curr_plain) >= 4
            and gap > body_fs * 0.5
            and curr_ch.islower()
            and prev_ch.islower()
            and not re.match(
                r"(?i)^(eons|ions|iez|ent|ons|ais|ait|aient|ées?|és?|ant|çons)$",
                curr_plain,
            )
        ):
            return False
        return True

    # Highlighted French conjugation endings are often parked farther right
    # (mang + e + ons, commen + ç + ons).
    if re.match(
        r"(?i)^(eons|çons|ions|iez|ent|ons|ez|es|ais|ait|aient|e|ç)\b",
        curr_plain,
    ) or (
        len(curr_plain) <= 2
        and WORD_CHAR_RE.search(curr_ch)
        and len(prev_plain.rstrip(" ,;")) >= 4
        and WORD_CHAR_RE.search(prev_plain.rstrip(" ,;")[-1:])
    ):
        return gap < body_fs * 3.5

    # Slightly looser packing: only short continuation pieces (-s / ss / ons).
    if gap > body_fs * 1.1:
        return False
    if len(curr_plain) <= 3 or curr_ch == "-" or prev_ch == "-":
        # Avoid turning "Il" + "s" style noise into false pronouns; single
        # letters after long stems (cueill+e) still join.
        if len(curr_plain) == 1 and len(prev_plain) <= 2:
            return False
        return True
    return False


def _pos_merge_line_fragments(
    spans: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Merge mid-word POS fragments on one horizontal line."""
    if not spans:
        return []
    ordered = sorted(spans, key=lambda s: float(s["left"]))
    merged: list[dict[str, object]] = []
    for span in ordered:
        if merged and _pos_should_join_fragments(merged[-1], span):
            prev = merged[-1]
            prev_text = str(prev["text"])
            curr_text = str(span["text"])
            if not prev_text.strip():
                # Empty anchor span — take the real token's origin.
                prev["left"] = span["left"]
                prev["fs"] = span["fs"]
                prev["text"] = curr_text
                prev["col"] = span.get("col", prev.get("col", 0))
                if curr_text.strip():
                    prev["last_left"] = span["left"]
                    prev["last_text"] = curr_text
                    prev["last_fs"] = span["fs"]
            else:
                prev["text"] = prev_text + curr_text
                # Empty spacer spans must not move the width anchor.
                if curr_text.strip():
                    prev["last_left"] = span["left"]
                    prev["last_text"] = curr_text
                    prev["last_fs"] = span["fs"]
            absorbed = prev.setdefault("absorbed_pids", [])
            if isinstance(absorbed, list):
                absorbed.append(int(span["pid"]))
                for extra in span.get("absorbed_pids") or []:
                    absorbed.append(int(extra))
            continue
        item = dict(span)
        item.setdefault("absorbed_pids", [])
        item["last_left"] = span["left"]
        item["last_text"] = span["text"]
        item["last_fs"] = span["fs"]
        merged.append(item)
    return merged


def _merge_pos_midword_spans_html(
    html: str,
    positions: dict[int, tuple[float, float, float]],
) -> str:
    """Glue mid-word POS fragments so fixed layout no longer depends on exact fonts.

    Beook splits words across absolutely positioned spans (``Par`` + ``t`` + ``ir``).
    When the subset font metrics differ slightly from the original engine, those
    fragments render with holes. Merging them into one span keeps the table grid
    while making words readable.
    """
    html = KERNING_EMPTY_SPAN_RE.sub("", html)
    span_re = re.compile(
        r'<span class="([^"]*)"([^>]*)>(.*?)</span>',
        re.IGNORECASE | re.DOTALL,
    )
    matches: list[dict[str, object]] = []
    for match in span_re.finditer(html):
        classes = match.group(1)
        pos_m = POS_ID_RE.search(classes)
        if not pos_m:
            continue
        pid = int(pos_m.group(1))
        if pid not in positions:
            continue
        top, left, font_size = positions[pid]
        inner = match.group(3)
        text = re.sub(r"<[^>]+>", "", inner)
        text = text.replace("\u200d", "").replace("\xa0", " ")
        col_m = COL_ID_RE.search(classes)
        matches.append(
            {
                "pid": pid,
                "top": top,
                "left": left,
                "fs": font_size,
                "text": text,
                "col": int(col_m.group(1)) if col_m else 0,
                "start": match.start(),
                "end": match.end(),
                "classes": classes,
                "attrs": match.group(2),
                "inner": inner,
            }
        )
    if len(matches) < 2:
        return html

    font_sizes = sorted(float(m["fs"]) for m in matches)
    body_fs = font_sizes[len(font_sizes) // 2] if font_sizes else 12.0
    line_tol = max(14.0, body_fs * 0.35)

    ordered = sorted(matches, key=lambda s: (float(s["top"]), float(s["left"])))
    lines: list[list[dict[str, object]]] = []
    current: list[dict[str, object]] = []
    current_top: float | None = None
    for span in ordered:
        top = float(span["top"])
        if current_top is None or abs(top - current_top) > line_tol:
            if current:
                lines.append(current)
            current = [span]
            current_top = top
        else:
            current.append(span)
    if current:
        lines.append(current)

    # Map pid -> replacement text for keepers; pids to delete when absorbed.
    replacements: dict[int, str] = {}
    delete_pids: set[int] = set()
    for line in lines:
        merged = _pos_merge_line_fragments(line)
        for token in merged:
            pid = int(token["pid"])
            absorbed = [int(x) for x in (token.get("absorbed_pids") or [])]
            text = str(token["text"])
            repaired = _pos_repair_glued_token(text)
            if absorbed:
                replacements[pid] = repaired
                delete_pids.update(absorbed)
            elif repaired != str(
                next(m["text"] for m in line if int(m["pid"]) == pid)
            ):
                replacements[pid] = repaired

    if not replacements and not delete_pids:
        return html

    # Rewrite from the end so offsets stay valid.
    pieces: list[str] = []
    cursor = len(html)
    for span in sorted(matches, key=lambda s: int(s["start"]), reverse=True):
        pid = int(span["pid"])
        start = int(span["start"])
        end = int(span["end"])
        pieces.append(html[end:cursor])
        if pid in delete_pids:
            cursor = start
            continue
        if pid in replacements:
            new_text = escape(replacements[pid])
            pieces.append(
                f'<span class="{span["classes"]}"{span["attrs"]}>{new_text}</span>'
            )
        else:
            pieces.append(html[start:end])
        cursor = start
    pieces.append(html[:cursor])
    pieces.reverse()
    return "".join(pieces)


def _prepare_pos_fixed_stylesheet(
    css_text: str,
    page_width: float,
    page_height: float,
) -> str:
    css_text = _fix_pos_css_units(css_text)
    # Neutralize Beook's tiny em page box — coordinates are pixel-based.
    css_text = re.sub(
        r"(?i)body\s*\{[^}]*\}",
        "body{margin:0;padding:0;background:#ffffff;color:#111111;}",
        css_text,
        count=1,
    )
    css_text += (
        "\n/* beook-export fixed layout helpers */\n"
        "html,body{width:auto!important;max-width:none!important;"
        "min-width:0!important;margin:0!important;padding:0!important;"
        "background:#ffffff!important;color:#111111!important;}\n"
        f".beook-export-pos-fixed-page{{width:{page_width:.1f}px!important;"
        f"height:{page_height:.1f}px!important;position:relative!important;"
        "overflow:visible!important;background:#ffffff!important;"
        "font-size:12px!important;}}\n"
        # After mid-word fragment merge, drop PDF-like tracking so glyphs
        # are not crushed / over-spaced by leftover letter-spacing values.
        "[class*='POS_']{position:absolute!important;white-space:pre!important;"
        "letter-spacing:normal!important;word-spacing:normal!important;}\n"
        "[class*='SVG_']{position:absolute!important;}\n"
    )
    return css_text


def _preserve_pos_fixed_page(
    html: str,
    files: dict[str, bytes],
    language: str,
    positions: dict[int, tuple[float, float, float]],
) -> str:
    """Keep absolute POS layout so columns/tables match the printed page."""
    width, height = _pos_fixed_page_metrics(positions)
    # Fit the design canvas into a readable EPUB width.
    scale = min(1.0, 780.0 / width)

    body_match = re.search(r"<body[^>]*>([\s\S]*)</body>", html, re.IGNORECASE)
    body_inner = body_match.group(1) if body_match else html
    body_inner = re.split(r'<div class="flowContainer"', body_inner, maxsplit=1)[0]
    body_inner = re.sub(
        r'<span class="pageInfo"[^>]*>[\s\S]*?</span>',
        "",
        body_inner,
        flags=re.IGNORECASE,
    )
    body_inner = re.sub(
        r'<a\b[^>]*\bid="page-[^"]*"[^>]*>\s*</a>',
        "",
        body_inner,
        flags=re.IGNORECASE,
    )
    # Glue mid-word fragments (Par+t+ir → Partir) before emitting fixed HTML.
    body_inner = _merge_pos_midword_spans_html(body_inner, positions)
    body_inner = body_inner.strip()

    css_links: list[str] = []
    for match in re.finditer(r'href="([^"]+\.css)"', html, re.IGNORECASE):
        href = match.group(1)
        if READER_STYLESHEET in href:
            continue
        name = href.split("/")[-1]
        for path, data in list(files.items()):
            if path.endswith(name) or path.endswith(href):
                fixed_css = _prepare_pos_fixed_stylesheet(
                    data.decode("utf-8", errors="replace"),
                    width,
                    height,
                )
                files[path] = fixed_css.encode("utf-8")
                break
        css_links.append(
            f'    <link href="{escape(href)}" rel="stylesheet" type="text/css" />'
        )
    css_links.append(
        f'    <link href="{READER_STYLESHEET}" rel="stylesheet" type="text/css" />'
    )

    title = "Page"
    plain_bits = re.findall(r">([^<]{2,80})<", body_inner)
    for bit in plain_bits:
        bit = bit.replace("\u200d", "").strip()
        if len(bit) >= 3 and not bit.startswith("Seite"):
            title = bit
            break

    lang = language or "fr"
    # Inline critical positioning so layout works even if reader CSS is aggressive.
    wrapper = (
        f'    <div class="beook-export-pos-fixed-outer" '
        f'style="width:{width * scale:.1f}px;height:{height * scale:.1f}px;'
        f'overflow:visible;margin:0 auto;background:#ffffff;">\n'
        f'      <div class="beook-export-pos-fixed-page" '
        f'style="width:{width:.1f}px;height:{height:.1f}px;'
        f'position:relative;transform:scale({scale:.4f});'
        f'transform-origin:top left;background:#ffffff;font-size:12px;">\n'
        f"{body_inner}\n"
        "      </div>\n"
        "    </div>"
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml" lang="{escape(lang)}" '
        f'xml:lang="{escape(lang)}">\n'
        "  <head>\n"
        '    <meta charset="utf-8" />\n'
        f"    <title>{escape(title[:120])}</title>\n"
        + "\n".join(css_links)
        + "\n  </head>\n"
        '  <body class="beook-export-pos-fixed" style="margin:0;padding:0.5rem;'
        'background:#ffffff;color:#111111;">\n'
        f"{wrapper}\n"
        "  </body>\n"
        "</html>\n"
    )


def _reflow_pos_span_page(html: str, files: dict[str, bytes], language: str = "fr") -> str:
    if not _is_pos_span_page_text(html):
        return html
    css_text = _css_text_for_html(html, files)
    if not css_text:
        return html
    positions = _parse_pos_css_positions(css_text)
    if len(positions) < 3:
        return html
    spans = _extract_pos_spans(html, positions)
    usable = [s for s in spans if str(s["text"]) != ""]
    if not usable:
        return html

    # Multi-column / dense layout pages: keep absolute positioning so tables
    # and columns match the printed book. Simple pages get semantic reflow.
    split_x = _pos_detect_column_split(usable)
    dense = len(usable) >= 80
    if split_x is not None or dense:
        return _preserve_pos_fixed_page(html, files, language, positions)

    body_html = _pos_lines_to_body_html(_reflow_pos_spans_to_lines(usable))
    toc_html = _extract_pos_toc_list(html)
    if toc_html:
        body_html = (body_html + "\n" + toc_html).strip()
    if not body_html.strip():
        return html

    lang = language or "fr"
    title_match = re.search(r"<h2[^>]*>(.*?)</h2>", body_html, re.IGNORECASE | re.DOTALL)
    if title_match:
        page_title = re.sub(r"<[^>]+>", "", title_match.group(1)).strip()
    else:
        page_title = "Page"
    page_title = escape(page_title[:120] or "Page")
    reader_link = f'<link href="{READER_STYLESHEET}" rel="stylesheet" type="text/css" />'

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml" lang="{escape(lang)}" '
        f'xml:lang="{escape(lang)}">\n'
        "  <head>\n"
        '    <meta charset="utf-8" />\n'
        f"    <title>{page_title}</title>\n"
        f"    {reader_link}\n"
        "  </head>\n"
        '  <body class="beook-export-pos-reflow">\n'
        f"{body_html}\n"
        "  </body>\n"
        "</html>\n"
    )


def _finalize_pos_span_pages(files: dict[str, bytes], language: str = "fr") -> None:
    for path, data in list(files.items()):
        if not path.endswith((".xhtml", ".html")):
            continue
        if path.endswith(("nav.xhtml", "cover.xhtml", "beook-export-annotations.xhtml")):
            continue
        if not _is_pos_span_page_data(data):
            continue
        text = data.decode("utf-8", errors="replace")
        updated = _reflow_pos_span_page(text, files, language=language)
        if updated == text:
            continue
        try:
            ElementTree.fromstring(updated.encode("utf-8"))
        except ElementTree.ParseError:
            continue
        files[path] = updated.encode("utf-8")


def _reflow_pdf2html_html(text: str) -> str:
    if not _is_pdf2html_page_text(text):
        return text
    text = _strip_pdf2html_kerning_spans(text)
    text = _add_body_class(text, "beook-export-pdf2html-reflow")
    text = PDF2HTML_PI_DIV_RE.sub("", text)
    return text


def _normalize_visual_blocks(text: str) -> str:
    """Prepare Beook layout blocks (tables, overflow wrappers, panels) for EPUB readers."""
    text = _reflow_pdf2html_html(text)
    text = _strip_empty_layout_divs(text)
    text = _trim_leading_wrapper_junk(text)
    text = _classify_tables(text)
    text = _unwrap_overflow_containers(text)
    text = _strip_overflow_comments(text)
    text = _mark_lernziele_panels(text)
    text = _mark_chart_panels(text)
    text = _insert_chart_page_breaks(text)
    text = _strip_trailing_body_whitespace(text)
    return text


def _xhtml_plain_text_length(data: bytes) -> int:
    text = data.decode("utf-8", errors="replace")
    body = text.split("pageNavigationTable")[0]
    plain = re.sub(r"<[^>]+>", " ", body)
    return len(re.sub(r"\s+", " ", plain).strip())


def _chapter_spine_prefix(path: str) -> str | None:
    match = CHAPTER_SPINE_PREFIX_RE.match(path)
    return match.group(1) if match else None


def _extract_xhtml_body_inner(text: str) -> str:
    match = re.search(r"<body\b[^>]*>(.*)</body>", text, re.IGNORECASE | re.DOTALL)
    if not match:
        return text.strip()
    inner = match.group(1)
    nav = PAGE_NAV_MARKER_RE.search(inner)
    if nav:
        inner = inner[: nav.start()]
    return inner.strip()


def _extract_xhtml_nav(text: str) -> str:
    nav = PAGE_NAV_MARKER_RE.search(text)
    if not nav:
        return ""
    tail = text[nav.start() :]
    end = tail.lower().find("</body>")
    return tail[:end].strip() if end != -1 else tail.strip()


def _merge_page_navigation(first_text: str, last_text: str) -> str:
    last_nav = _extract_xhtml_nav(last_text)
    if not last_nav:
        return _extract_xhtml_nav(first_text)
    first_nav = _extract_xhtml_nav(first_text)
    if not first_nav:
        return last_nav
    prev_link = PAGE_NAV_LEFT_RE.search(first_nav)
    next_link = PAGE_NAV_RIGHT_RE.search(last_nav)
    if prev_link:
        last_nav = PAGE_NAV_LEFT_RE.sub(prev_link.group(1), last_nav, count=1)
    if next_link:
        last_nav = PAGE_NAV_RIGHT_RE.sub(next_link.group(1), last_nav, count=1)
    return last_nav


def _merge_xhtml_documents(paths: list[str], files: dict[str, bytes]) -> bytes:
    first_text = files[paths[0]].decode("utf-8", errors="replace")
    head_match = re.search(r"<head\b[^>]*>.*?</head>", first_text, re.IGNORECASE | re.DOTALL)
    head = head_match.group(0) if head_match else "<head></head>"
    html_open = re.search(r"<html\b[^>]*>", first_text, re.IGNORECASE)
    html_tag = html_open.group(0) if html_open else '<html xmlns="http://www.w3.org/1999/xhtml">'
    body_match = re.search(r"<body\b([^>]*)>", first_text, re.IGNORECASE)
    body_attrs = body_match.group(1) if body_match else ' class="hyphenate"'

    parts: list[str] = []
    joiner = '<div class="beook-export-spine-join" aria-hidden="true"></div>'
    for index, path in enumerate(paths):
        if index:
            parts.append(joiner)
        parts.append(_extract_xhtml_body_inner(files[path].decode("utf-8", errors="replace")))

    nav = _merge_page_navigation(
        files[paths[0]].decode("utf-8", errors="replace"),
        files[paths[-1]].decode("utf-8", errors="replace"),
    )
    xml_decl = "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n" if first_text.lstrip().startswith("<?xml") else ""
    merged = (
        f"{xml_decl}{html_tag}\n"
        f"{head}\n"
        f"<body{body_attrs}>\n"
        f"{chr(10).join(parts)}\n\n\t"
        f"{nav}\n"
        f"</body>\n</html>"
    )
    return merged.encode("utf-8")


def _consolidate_spine_pages(
    spine_filenames: list[str],
    files: dict[str, bytes],
) -> tuple[list[str], dict[str, bytes], dict[str, str]]:
    redirect: dict[str, str] = {}
    new_spine: list[str] = []
    index = 0
    while index < len(spine_filenames):
        path = spine_filenames[index]
        prefix = _chapter_spine_prefix(path)
        if not prefix or path not in files:
            new_spine.append(path)
            index += 1
            continue

        group = [path]
        total_chars = _xhtml_plain_text_length(files[path])
        next_index = index + 1
        while next_index < len(spine_filenames):
            next_path = spine_filenames[next_index]
            if _chapter_spine_prefix(next_path) != prefix or next_path not in files:
                break
            next_chars = _xhtml_plain_text_length(files[next_path])
            should_merge = (
                total_chars < SPINE_MERGE_SHORT_CHARS or next_chars < SPINE_MERGE_SHORT_CHARS
            ) and total_chars + next_chars <= SPINE_MERGE_MAX_GROUP_CHARS
            if not should_merge:
                break
            group.append(next_path)
            total_chars += next_chars
            next_index += 1

        if len(group) == 1:
            new_spine.append(path)
        else:
            merged_path = group[0]
            files[merged_path] = _merge_xhtml_documents(group, files)
            new_spine.append(merged_path)
            for absorbed in group[1:]:
                redirect[absorbed] = merged_path
        index = next_index

    return new_spine, files, redirect


def _consolidate_pdf2html_spine_pages(
    spine_filenames: list[str],
    files: dict[str, bytes],
) -> tuple[list[str], dict[str, bytes], dict[str, str]]:
    redirect: dict[str, str] = {}
    new_spine: list[str] = []
    index = 0
    while index < len(spine_filenames):
        path = spine_filenames[index]
        data = files.get(path)
        if (
            not path.endswith(".xhtml")
            or not data
            or not _is_pdf2html_page_data(data)
            or _is_glossary_page(data)
        ):
            new_spine.append(path)
            index += 1
            continue

        group = [path]
        total_chars = _xhtml_plain_text_length(data)
        next_index = index + 1
        while next_index < len(spine_filenames):
            next_path = spine_filenames[next_index]
            next_data = files.get(next_path)
            if (
                not next_path.endswith(".xhtml")
                or not next_data
                or not _is_pdf2html_page_data(next_data)
                or _is_glossary_page(next_data)
            ):
                break
            next_chars = _xhtml_plain_text_length(next_data)
            combined_chars = total_chars + next_chars
            should_merge = combined_chars <= SPINE_MERGE_MAX_GROUP_CHARS and (
                (total_chars < SPINE_MERGE_SHORT_CHARS and next_chars < SPINE_MERGE_SHORT_CHARS)
                or combined_chars <= SPINE_MERGE_SHORT_CHARS
            )
            if not should_merge:
                break
            group.append(next_path)
            total_chars += next_chars
            next_index += 1

        if len(group) == 1:
            new_spine.append(path)
        else:
            merged_path = group[0]
            files[merged_path] = _merge_xhtml_documents(group, files)
            new_spine.append(merged_path)
            for absorbed in group[1:]:
                redirect[absorbed] = merged_path
        index = next_index

    return new_spine, files, redirect


def _is_glossary_page(data: bytes) -> bool:
    return b"glossar-title" in data


def _glossary_numeric_id(path: str) -> str | None:
    match = GLOSSARY_PAGE_ID_RE.match(path)
    return match.group(1) if match else None


def _glossary_anchor_id(path: str) -> str:
    numeric_id = _glossary_numeric_id(path)
    return f"glossar-id{numeric_id}" if numeric_id else f"glossar-{Path(path).stem}"


def _rewrite_glossary_links(
    text: str,
    numeric_to_anchor: dict[str, str],
    numeric_to_filename: dict[str, str],
    current_filename: str,
) -> str:
    def replacer(match: re.Match[str]) -> str:
        numeric_id = match.group(2)
        target_filename = numeric_to_filename.get(numeric_id)
        if target_filename is None:
            # The referenced glossary term was never resolved to a known
            # page (e.g. its numeric id collided with another resource and
            # got a disambiguated filename). Guessing "id{N}.xhtml" would
            # point at a file that doesn't exist, so neutralize instead.
            return f'{match.group(1)}#{match.group(3)}'
        anchor = numeric_to_anchor.get(numeric_id, f"glossar-id{numeric_id}")
        if target_filename == current_filename:
            return f'{match.group(1)}#{anchor}{match.group(3)}'
        return f'{match.group(1)}{target_filename}#{anchor}{match.group(3)}'

    return GLOSSARY_LINK_RE.sub(replacer, text)


def _neutralize_dead_interactive_links(text: str) -> str:
    text = DEAD_ANCHOR_LABEL_RE.sub('href="#"', text)

    def replace_tag(match: re.Match[str]) -> str:
        tag = match.group(0)
        class_match = re.search(r'class="([^"]*)"', tag, re.IGNORECASE)
        if not class_match or not DEAD_LINK_CLASSES.intersection(class_match.group(1).split()):
            return tag
        href_match = re.search(r'href="([^"]*)"', tag, re.IGNORECASE)
        if not href_match:
            return tag
        href = href_match.group(1)
        if href.startswith(("http://", "https://", "#")) or "/" in href or "." in href:
            return tag
        return tag[: href_match.start()] + 'href="#"' + tag[href_match.end() :]

    return DEAD_LINK_TAG_RE.sub(replace_tag, text)


def _extract_glossary_entry(text: str, path: str) -> str:
    inner = _extract_xhtml_body_inner(text)
    anchor = _glossary_anchor_id(path)
    return f'<article class="beook-export-glossary-entry" id="{anchor}">\n{inner}\n</article>'


def _plan_glossary_groups(
    spine_filenames: list[str],
    files: dict[str, bytes],
) -> list[list[str]]:
    groups: list[list[str]] = []
    index = 0
    while index < len(spine_filenames):
        path = spine_filenames[index]
        if path not in files or not _is_glossary_page(files[path]):
            index += 1
            continue
        group = [path]
        total_chars = _xhtml_plain_text_length(files[path])
        next_index = index + 1
        while next_index < len(spine_filenames):
            next_path = spine_filenames[next_index]
            if next_path not in files or not _is_glossary_page(files[next_path]):
                break
            next_chars = _xhtml_plain_text_length(files[next_path])
            if len(group) >= GLOSSARY_MERGE_MAX_ENTRIES or total_chars + next_chars > GLOSSARY_MERGE_MAX_CHARS:
                break
            group.append(next_path)
            total_chars += next_chars
            next_index += 1
        groups.append(group)
        index = next_index
    return groups


def _build_merged_glossary(
    group: list[str],
    files: dict[str, bytes],
    merged_path: str,
    numeric_to_anchor: dict[str, str],
    numeric_to_filename: dict[str, str],
) -> bytes:
    first_text = files[group[0]].decode("utf-8", errors="replace")
    head_match = re.search(r"<head\b[^>]*>.*?</head>", first_text, re.IGNORECASE | re.DOTALL)
    head = head_match.group(0) if head_match else "<head></head>"
    html_open = re.search(r"<html\b[^>]*>", first_text, re.IGNORECASE)
    html_tag = html_open.group(0) if html_open else '<html xmlns="http://www.w3.org/1999/xhtml">'
    merged_filename = merged_path.split("/")[-1]

    entries: list[str] = []
    for path in group:
        entry = _extract_glossary_entry(files[path].decode("utf-8", errors="replace"), path)
        entry = _rewrite_glossary_links(
            entry,
            numeric_to_anchor,
            numeric_to_filename,
            merged_filename,
        )
        entries.append(entry)

    nav = _merge_page_navigation(
        files[group[0]].decode("utf-8", errors="replace"),
        files[group[-1]].decode("utf-8", errors="replace"),
    )
    nav = _rewrite_glossary_links(nav, numeric_to_anchor, numeric_to_filename, merged_filename)
    xml_decl = "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n" if first_text.lstrip().startswith("<?xml") else ""
    merged = (
        f"{xml_decl}{html_tag}\n"
        f"{head}\n"
        f'<body class="beook-export-glossary">\n'
        f"{chr(10).join(entries)}\n\n\t"
        f"{nav}\n"
        f"</body>\n</html>"
    )
    return merged.encode("utf-8")


def _consolidate_glossary_pages(
    spine_filenames: list[str],
    files: dict[str, bytes],
) -> tuple[list[str], dict[str, bytes], dict[str, str]]:
    groups = _plan_glossary_groups(spine_filenames, files)
    if not groups:
        return spine_filenames, files, {}

    redirect: dict[str, str] = {}
    numeric_to_anchor: dict[str, str] = {}
    numeric_to_filename: dict[str, str] = {}
    for index, group in enumerate(groups, start=1):
        merged_path = f"OEBPS/glossary-{index:03d}.xhtml"
        merged_filename = merged_path.split("/")[-1]
        for path in group:
            redirect[path] = merged_path
            numeric_id = _glossary_numeric_id(path)
            if numeric_id:
                numeric_to_anchor[numeric_id] = _glossary_anchor_id(path)
                numeric_to_filename[numeric_id] = merged_filename

    for index, group in enumerate(groups, start=1):
        merged_path = f"OEBPS/glossary-{index:03d}.xhtml"
        files[merged_path] = _build_merged_glossary(
            group,
            files,
            merged_path,
            numeric_to_anchor,
            numeric_to_filename,
        )

    new_spine: list[str] = []
    for path in spine_filenames:
        if path in redirect:
            merged_path = redirect[path]
            if merged_path not in new_spine:
                new_spine.append(merged_path)
            continue
        new_spine.append(path)

    return new_spine, files, redirect


def _apply_spine_redirects(files: dict[str, bytes], redirect: dict[str, str]) -> None:
    if not redirect:
        return
    resolved: dict[str, str] = {}
    for old_path, new_path in redirect.items():
        target = new_path
        seen: set[str] = set()
        while target in redirect and target not in seen:
            seen.add(target)
            target = redirect[target]
        resolved[old_path] = target

    filename_map = {old.split("/")[-1]: new.split("/")[-1] for old, new in resolved.items()}
    for path, data in list(files.items()):
        if not path.endswith(".xhtml"):
            continue
        text = data.decode("utf-8", errors="replace")
        updated = text
        for old_name, new_name in filename_map.items():
            updated = updated.replace(old_name, new_name)
        if updated != text:
            files[path] = updated.encode("utf-8")


def _rewrite_xhtml(
    content: bytes,
    pk_to_filename: dict[int, str],
    topic_pk_to_filename: dict[int, str],
    anchor_to_filename: dict[str, str],
    topic_access_map: dict[tuple[str, str], str],
    reader_stylesheet_href: str = READER_STYLESHEET,
) -> bytes:
    text = content.decode("utf-8")

    def _link_replacer(prefix: str, pk: str, middle: str, original: str, suffix: str) -> str:
        href = _resource_filename(pk_to_filename, int(pk), original)
        return _empty_element("link", "href", href, prefix, middle, suffix)

    text = LINK_WITH_ORIGINAL_RE.sub(
        lambda m: _link_replacer(m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)),
        text,
    )
    text = LINK_ORIGINAL_FIRST_RE.sub(
        lambda m: _link_replacer(m.group(1), m.group(4), m.group(3), m.group(2), m.group(5)),
        text,
    )

    def _img_replacer(prefix: str, pk: str, middle: str, original: str, suffix: str) -> str:
        src = _resource_filename(pk_to_filename, int(pk), original)
        return _empty_element("img", "src", src, prefix, middle, suffix)

    text = IMG_WITH_ORIGINAL_RE.sub(
        lambda m: _img_replacer(m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)),
        text,
    )
    text = IMG_ORIGINAL_FIRST_RE.sub(
        lambda m: _img_replacer(m.group(1), m.group(4), m.group(3), m.group(2), m.group(5)),
        text,
    )

    text = LOCALHOST_RESOURCE_PK_RE.sub(
        lambda m: pk_to_filename.get(int(m.group(1)), m.group(0)),
        text,
    )
    text = LOCALHOST_TOPIC_PK_RE.sub(
        lambda m: topic_pk_to_filename.get(int(m.group(1)), "#"),
        text,
    )
    text = LOCALHOST_ANCHOR_RE.sub(
        lambda m: _anchor_href(m.group(1), anchor_to_filename),
        text,
    )
    text = LOCALHOST_ISSUE_TOPIC_RE.sub(
        lambda m: topic_access_map.get((m.group(1), m.group(2)), "#"),
        text,
    )
    text = LOCALHOST_APP_RE.sub("#", text)

    valid_filenames = set(pk_to_filename.values())

    def _glossary_link_replacer(match: re.Match[str]) -> str:
        target = f"id{match.group(1)}.xhtml"
        # Beook reuses low, chapter-local numeric ids across issues, so a
        # target may exist for a *different* chapter than the one
        # containing this link. Only rewrite when the bare filename is a
        # real, unambiguous target in this book; otherwise neutralize
        # rather than link to a nonexistent or unrelated page.
        if target in valid_filenames:
            return f'href="{target}"'
        return 'href="#"'

    text = GLOSSARY_HTML_LINK_RE.sub(_glossary_link_replacer, text)
    text = _neutralize_dead_interactive_links(text)
    text = DATA_ORIGINAL_ATTR_RE.sub("", text)
    text = VOID_CLOSING_TAG_RE.sub("", text)
    text = _normalize_visual_blocks(text)
    text = _inject_reader_stylesheet(text, reader_stylesheet_href)
    text = _normalize_named_entities(text)
    return text.encode("utf-8")


def extract_book(
    conn: sqlite3.Connection,
    reference: str,
    beook_root: Path | None = None,
) -> BookContent:
    issues = get_issues_for_reference(conn, reference)
    if not issues:
        raise ValueError(f"No issues found for reference: {reference}")

    issue_pks = [issue.pk for issue in issues]
    resources = get_resources_for_issues(conn, issue_pks)
    if not resources:
        raise ValueError(f"No resources with data found for reference: {reference}")

    topic_pk_to_filename = get_topic_pk_map(conn, issue_pks)
    topic_access_map = get_topic_access_map(conn, issue_pks)
    pk_to_filename = _build_pk_to_filename(resources)
    anchor_to_filename = _build_anchor_map(resources, pk_to_filename)

    files: dict[str, bytes] = {}
    media_types: dict[str, str] = {}

    for resource in resources:
        filename = pk_to_filename.get(resource.pk)
        if not filename or resource.data is None:
            continue
        epub_path = f"OEBPS/{filename}"
        payload = resource.data
        if resource.media_type in ("application/xhtml+xml", "text/html"):
            payload = _rewrite_xhtml(
                payload,
                pk_to_filename,
                topic_pk_to_filename,
                anchor_to_filename,
                topic_access_map,
                _reader_stylesheet_href(filename),
            )
        elif resource.media_type == "text/css":
            payload = _rewrite_css(payload, pk_to_filename, filename)
        if epub_path in files:
            continue
        files[epub_path] = payload
        media_types[epub_path] = resource.media_type

    _finalize_pdf2html_pages(files)

    files[READER_STYLESHEET_PATH] = READER_STYLESHEET_BYTES
    media_types[READER_STYLESHEET_PATH] = "text/css"

    spine_filenames: list[str] = []
    for issue in issues:
        for pk in get_spine_page_pks(conn, issue.pk):
            filename = pk_to_filename.get(pk)
            if not filename:
                continue
            epub_path = f"OEBPS/{filename}"
            if epub_path in files and epub_path not in spine_filenames:
                spine_filenames.append(epub_path)

    if not spine_filenames:
        spine_filenames = sorted(
            path
            for path, media in media_types.items()
            if media in ("application/xhtml+xml", "text/html")
        )

    language = get_language_for_reference(conn, reference) or detect_language(files, media_types)
    _finalize_pos_span_pages(files, language=language)

    spine_filenames, files, spine_redirect = _consolidate_spine_pages(spine_filenames, files)
    spine_filenames, files, pdf2html_redirect = _consolidate_pdf2html_spine_pages(
        spine_filenames, files
    )
    spine_filenames, files, glossary_redirect = _consolidate_glossary_pages(spine_filenames, files)
    spine_redirect = {**spine_redirect, **pdf2html_redirect, **glossary_redirect}
    _apply_spine_redirects(files, spine_redirect)
    for absorbed_path in spine_redirect:
        files.pop(absorbed_path, None)
        media_types.pop(absorbed_path, None)
    notes = get_notes_for_issues(conn, issue_pks)
    if notes:
        annotations_path = "OEBPS/beook-export-annotations.xhtml"
        files[annotations_path] = _annotations_xhtml(notes, language)
        media_types[annotations_path] = "application/xhtml+xml"
        spine_filenames.append(annotations_path)

    cover_image_path = _inject_course_cover(
        conn, reference, beook_root, files, media_types
    )
    if not cover_image_path:
        cover_image_path = _find_cover_image_path(
            resources, pk_to_filename, issues, spine_filenames, files
        )

    # Prefer the stable course-product key for multi-issue series so output
    # folders are one book (HEP-RE-5DE) rather than dozens of chapter refs.
    export_reference = resolve_course_product_reference(conn, reference) or reference
    metadata = get_book_metadata(conn, export_reference)

    return BookContent(
        reference=export_reference,
        title=get_book_title_for_reference(conn, export_reference),
        language=language,
        spine_filenames=spine_filenames,
        files=files,
        media_types=media_types,
        pk_to_filename=pk_to_filename,
        cover_image_path=cover_image_path,
        authors=metadata.authors,
        subtitle=metadata.subtitle,
        isbn=metadata.isbn,
        publish_date=metadata.publish_date,
        edition=metadata.edition,
        description=metadata.description,
        series=metadata.series,
        publisher=metadata.publisher,
    )
