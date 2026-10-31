"""Build a valid EPUB archive from extracted OEBPS content."""

from __future__ import annotations

import io
import itertools
import re
import uuid
import zipfile
from datetime import datetime, timezone
from html import unescape
from xml.sax.saxutils import escape

from .extractor import BookContent
from .language import format_author_list, ui_string
from .naming import media_type_for_filename

MIMETYPE = b"application/epub+zip"
NAV_PATH = "OEBPS/nav.xhtml"
NAV_FILENAME = "nav.xhtml"
NAV_ITEM_ID = "beook_export_nav"
COVER_PATH = "OEBPS/cover.xhtml"
COVER_FILENAME = "cover.xhtml"
COVER_ITEM_ID = "beook_export_cover"
NCX_PATH = "OEBPS/toc.ncx"
NCX_FILENAME = "toc.ncx"
NCX_ITEM_ID = "ncx"
HEADING_RE = re.compile(r"<h[1-6]\b[^>]*>(.*?)</h[1-6]>", re.IGNORECASE | re.DOTALL)
HEADING_LEVEL_RE = re.compile(
    r'<h([1-6])\b[^>]*(?:data-levelNo="(\d+)")?[^>]*>',
    re.IGNORECASE,
)
TITLE_RE = re.compile(r"<title\b[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


def _attr(value: str) -> str:
    return escape(value, {'"': "&quot;"})


def _clean_meta_text(value: str, limit: int = 2000) -> str:
    text = " ".join(value.split())
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip() + "\u2026"
    return text


def _stable_uuid(reference: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"beook-export:{reference}"))


def _metadata_xml(book: BookContent, cover_item_id: str | None) -> str:
    book_uuid = _stable_uuid(book.reference)
    lines: list[str] = [
        f'    <dc:identifier id="book-id">{escape(book.reference)}</dc:identifier>'
    ]
    if book.isbn:
        lines.append(f'    <dc:identifier id="isbn-id">urn:isbn:{escape(book.isbn)}</dc:identifier>')
        lines.append('    <meta refines="#isbn-id" property="identifier-type">ISBN</meta>')
    lines.append(f'    <dc:identifier id="book-uuid">urn:uuid:{escape(book_uuid)}</dc:identifier>')
    if book.subtitle:
        lines.append(f'    <dc:title id="title-main">{escape(book.title)}</dc:title>')
        lines.append('    <meta refines="#title-main" property="title-type">main</meta>')
        lines.append(f'    <dc:title id="title-sub">{escape(book.subtitle)}</dc:title>')
        lines.append('    <meta refines="#title-sub" property="title-type">subtitle</meta>')
    else:
        lines.append(f'    <dc:title>{escape(book.title)}</dc:title>')

    for index, author in enumerate(book.authors, start=1):
        creator_id = f"creator-{index}"
        lines.append(
            f'    <dc:creator id="{creator_id}" opf:role="aut">{escape(author)}</dc:creator>'
        )
        lines.append(
            f'    <meta refines="#{creator_id}" property="role" scheme="marc:relators">aut</meta>'
        )

    lines.append(f'    <dc:language>{escape(book.language)}</dc:language>')
    if book.publisher:
        lines.append(f'    <dc:publisher>{escape(book.publisher)}</dc:publisher>')
    if book.publish_date:
        lines.append(f'    <dc:date>{escape(book.publish_date)}</dc:date>')
    if book.description:
        lines.append(
            f'    <dc:description>{escape(_clean_meta_text(book.description))}</dc:description>'
        )
    if book.isbn:
        lines.append(f'    <dc:source>urn:isbn:{escape(book.isbn)}</dc:source>')
    if book.edition:
        lines.append(
            f'    <meta property="schema:bookEdition">{escape(book.edition)}</meta>'
        )
    if book.series:
        lines.append(f'    <meta name="calibre:series" content="{_attr(book.series)}"/>')
    lines.append(
        "    <dc:rights>Exported for personal use with beook-export. "
        "Original content remains under publisher copyright.</dc:rights>"
    )

    lines.append(
        '    <meta property="dcterms:modified">'
        f'{datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}</meta>'
    )
    if cover_item_id:
        lines.append(f'    <meta name="cover" content="{_attr(cover_item_id)}"/>')
    lines.append(
        f'    <meta name="beook-export-uuid" content="{_attr(book_uuid)}"/>'
    )
    return "\n".join(lines)


def _guide_xml(book: BookContent) -> str:
    references = [
        f'    <reference type="cover" title="{_attr(ui_string(book.language, "cover"))}" '
        f'href="{COVER_FILENAME}"/>',
        f'    <reference type="toc" title="{_attr(ui_string(book.language, "toc"))}" '
        f'href="{NAV_FILENAME}"/>',
    ]
    return "  <guide>\n" + "\n".join(references) + "\n  </guide>\n"


def _content_opf(book: BookContent) -> str:
    manifest_items: list[str] = []
    spine_items: list[str] = []
    used_ids: set[str] = set()
    filename_to_id: dict[str, str] = {}
    cover_item_id: str | None = None

    for path in sorted(book.files):
        filename = path.split("/", 1)[1]
        item_id = _safe_id(filename)
        while item_id in used_ids:
            item_id = f"{item_id}_{len(used_ids)}"
        used_ids.add(item_id)
        filename_to_id[filename] = item_id
        media_type = book.media_types.get(path) or media_type_for_filename(filename)
        properties = ""
        if book.cover_image_path and path == book.cover_image_path:
            properties = ' properties="cover-image"'
            cover_item_id = item_id
        manifest_items.append(
            f'    <item id="{item_id}" href="{escape(filename)}" '
            f'media-type="{media_type}"{properties}/>'
        )

    # Always include a cover/title document as the first spine item.
    manifest_items.append(
        f'    <item id="{COVER_ITEM_ID}" href="{COVER_FILENAME}" '
        'media-type="application/xhtml+xml"/>'
    )

    manifest_items.append(
        f'    <item id="{NAV_ITEM_ID}" href="{NAV_FILENAME}" '
        'media-type="application/xhtml+xml" properties="nav"/>'
    )
    manifest_items.append(
        f'    <item id="{NCX_ITEM_ID}" href="{NCX_FILENAME}" '
        'media-type="application/x-dtbncx+xml"/>'
    )

    spine_items.append(f'    <itemref idref="{COVER_ITEM_ID}"/>')

    for path in book.spine_filenames:
        filename = path.split("/", 1)[1]
        item_id = filename_to_id.get(filename) or _safe_id(filename)
        spine_items.append(f'    <itemref idref="{item_id}"/>')

    metadata_block = _metadata_xml(book, cover_item_id)
    guide_block = _guide_xml(book)

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" xmlns:opf="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
{metadata_block}
  </metadata>
  <manifest>
{chr(10).join(manifest_items)}
  </manifest>
  <spine toc="{NCX_ITEM_ID}">
{chr(10).join(spine_items)}
  </spine>
{guide_block}</package>
"""


def _cover_meta_blocks(book: BookContent) -> list[str]:
    meta_blocks: list[str] = []
    if book.subtitle:
        meta_blocks.append(
            f'      <p class="beook-export-cover-subtitle">{escape(book.subtitle)}</p>'
        )
    if book.authors:
        authors = escape(format_author_list(book.authors, book.language))
        meta_blocks.append(f'      <p class="beook-export-cover-authors">{authors}</p>')
    if book.publisher:
        meta_blocks.append(
            f'      <p class="beook-export-cover-publisher">{escape(book.publisher)}</p>'
        )
    if book.series:
        meta_blocks.append(
            f'      <p class="beook-export-cover-series">{escape(book.series)}</p>'
        )
    details: list[str] = []
    if book.edition:
        details.append(f"{ui_string(book.language, 'edition')} {escape(book.edition)}")
    if book.isbn:
        details.append(f"ISBN {escape(book.isbn)}")
    if book.publish_date:
        details.append(
            escape(book.publish_date[:4] if len(book.publish_date) >= 4 else book.publish_date)
        )
    if details:
        meta_blocks.append(
            f'      <p class="beook-export-cover-details">{" · ".join(details)}</p>'
        )
    return meta_blocks


def _cover_xhtml(book: BookContent) -> str:
    stylesheet_link = ""
    if "OEBPS/beook-export-reader.css" in book.files:
        stylesheet_link = (
            '    <link href="beook-export-reader.css" rel="stylesheet" type="text/css" />\n'
        )
    image_block = ""
    if book.cover_image_path:
        cover_filename = book.cover_image_path.split("/", 1)[1]
        image_block = (
            f'      <img src="{escape(cover_filename)}" alt="{escape(book.title)}" />\n'
        )
    meta_html = "\n".join(_cover_meta_blocks(book))
    if meta_html:
        meta_html = "\n" + meta_html
    body_class = "beook-export-cover"
    if not book.cover_image_path:
        body_class += " beook-export-cover-text-only"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="{escape(book.language)}" xml:lang="{escape(book.language)}">
  <head>
    <meta charset="utf-8" />
    <title>{escape(book.title)}</title>
{stylesheet_link.rstrip()}
  </head>
  <body class="{body_class}" epub:type="cover">
    <section class="beook-export-cover-frame" epub:type="cover">
{image_block}      <h1 class="beook-export-cover-title">{escape(book.title)}</h1>
{meta_html}
    </section>
  </body>
</html>
"""


def _nav_heading(book: BookContent) -> str:
    if book.subtitle:
        return (
            f"      <h1>{escape(book.title)}</h1>\n"
            f'      <p class="beook-export-nav-subtitle">{escape(book.subtitle)}</p>'
        )
    return f"      <h1>{escape(book.title)}</h1>"


def _landmarks_nav(book: BookContent) -> str:
    items = [
        f'        <li><a epub:type="cover" href="{COVER_FILENAME}">'
        f'{escape(ui_string(book.language, "cover"))}</a></li>',
        f'        <li><a epub:type="toc" href="{NAV_FILENAME}">'
        f'{escape(ui_string(book.language, "toc"))}</a></li>',
    ]
    if book.spine_filenames:
        first = book.spine_filenames[0].split("/", 1)[1]
        items.append(
            f'        <li><a epub:type="bodymatter" href="{escape(first)}">'
            f'{escape(ui_string(book.language, "start"))}</a></li>'
        )
    return (
        '    <nav epub:type="landmarks" id="landmarks" hidden="hidden">\n'
        f'      <h2>{escape(ui_string(book.language, "guide"))}</h2>\n'
        "      <ol>\n"
        + "\n".join(items)
        + "\n      </ol>\n"
        "    </nav>"
    )


def _nav_xhtml(book: BookContent) -> str:
    nav_body = _nested_nav_list(book)
    nav_heading = _nav_heading(book)
    landmarks = _landmarks_nav(book)
    stylesheet_link = ""
    if "OEBPS/beook-export-reader.css" in book.files:
        stylesheet_link = (
            '    <link href="beook-export-reader.css" rel="stylesheet" type="text/css" />\n'
        )
    nav_title = f"{book.title} - {ui_string(book.language, 'navigation')}"

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="{escape(book.language)}" xml:lang="{escape(book.language)}">
  <head>
    <meta charset="utf-8" />
    <title>{escape(nav_title)}</title>
{stylesheet_link.rstrip()}
  </head>
  <body>
    <nav epub:type="toc" id="toc">
{nav_heading}
{nav_body}
    </nav>
{landmarks}
  </body>
</html>
"""


def _build_nav_nodes(book: BookContent) -> list[dict[str, object]]:
    """Build the nested table-of-contents tree shared by nav.xhtml and toc.ncx."""
    nodes: list[dict[str, object]] = []
    stack: list[tuple[list[dict[str, object]], int]] = [(nodes, 0)]
    for index, path in enumerate(book.spine_filenames, start=1):
        filename = path.split("/", 1)[1]
        label = _nav_label(book, path, filename, index)
        level = _nav_level(book, path)
        while len(stack) > 1 and stack[-1][1] >= level:
            stack.pop()
        parent, _ = stack[-1]
        node: dict[str, object] = {"href": filename, "label": label, "children": []}
        parent.append(node)
        stack.append((node["children"], level))
    return nodes


def _nested_nav_list(book: BookContent) -> str:
    nodes = _build_nav_nodes(book)
    lines = ["      <ol>"]
    lines.extend(_render_nav_nodes(nodes, indent=2))
    lines.append("      </ol>")
    return "\n".join(lines)


def _nav_tree_depth(nodes: list[dict[str, object]]) -> int:
    depth = 0
    for node in nodes:
        children = node["children"]
        assert isinstance(children, list)
        depth = max(depth, 1 + _nav_tree_depth(children))
    return depth


def _render_ncx_points(
    nodes: list[dict[str, object]],
    counter: itertools.count,
) -> list[str]:
    lines: list[str] = []
    for node in nodes:
        order = next(counter)
        children = node["children"]
        assert isinstance(children, list)
        lines.append(f'    <navPoint id="navPoint-{order}" playOrder="{order}">')
        lines.append(
            f'      <navLabel><text>{escape(str(node["label"]))}</text></navLabel>'
        )
        lines.append(f'      <content src="{_attr(str(node["href"]))}"/>')
        if children:
            lines.extend(_render_ncx_points(children, counter))
        lines.append("    </navPoint>")
    return lines


def _toc_ncx(book: BookContent) -> str:
    nodes = _build_nav_nodes(book)
    depth = max(1, _nav_tree_depth(nodes))
    book_uuid = _stable_uuid(book.reference)
    nav_map = "\n".join(_render_ncx_points(nodes, itertools.count(1)))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1" xml:lang="{_attr(book.language)}">
  <head>
    <meta name="dtb:uid" content="urn:uuid:{_attr(book_uuid)}"/>
    <meta name="dtb:depth" content="{depth}"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle>
    <text>{escape(book.title)}</text>
  </docTitle>
  <navMap>
{nav_map}
  </navMap>
</ncx>
"""


def _render_nav_nodes(nodes: list[dict[str, object]], indent: int) -> list[str]:
    pad = "  " * indent
    lines: list[str] = []
    for node in nodes:
        children = node["children"]
        assert isinstance(children, list)
        lines.append(
            f'{pad}<li><a href="{escape(str(node["href"]))}">{escape(str(node["label"]))}</a>'
        )
        if children:
            lines.append(f"{pad}  <ol>")
            lines.extend(_render_nav_nodes(children, indent + 2))
            lines.append(f"{pad}  </ol>")
        lines.append(f"{pad}</li>")
    return lines


def _nav_level(book: BookContent, path: str) -> int:
    html = book.files.get(path, b"").decode("utf-8", errors="replace")
    match = HEADING_LEVEL_RE.search(html)
    if match:
        if match.group(2):
            return max(1, min(int(match.group(2)), 6))
        return int(match.group(1))
    return 3


def _nav_label(book: BookContent, path: str, filename: str, index: int) -> str:
    html = book.files.get(path, b"").decode("utf-8", errors="replace")
    match = HEADING_RE.search(html) or TITLE_RE.search(html)
    if match:
        label = _clean_nav_text(match.group(1))
        if label:
            return label[:120]
    stem = filename.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").strip()
    return stem or f"Section {index}"


def _clean_nav_text(value: str) -> str:
    return SPACE_RE.sub(" ", unescape(TAG_RE.sub(" ", value))).strip()


def _safe_id(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0]
    safe = "".join(c if c.isalnum() else "_" for c in stem)
    return safe or "item"


def build_epub_bytes(book: BookContent) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("mimetype", MIMETYPE, compress_type=zipfile.ZIP_STORED)
        zf.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
""",
            compress_type=zipfile.ZIP_DEFLATED,
        )
        zf.writestr("OEBPS/content.opf", _content_opf(book), compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr(COVER_PATH, _cover_xhtml(book), compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr(NAV_PATH, _nav_xhtml(book), compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr(NCX_PATH, _toc_ncx(book), compress_type=zipfile.ZIP_DEFLATED)
        for path, data in book.files.items():
            zf.writestr(path, data, compress_type=zipfile.ZIP_DEFLATED)
    return buffer.getvalue()


def write_epub(book: BookContent, output_path: str) -> int:
    data = build_epub_bytes(book)
    with open(output_path, "wb") as fh:
        fh.write(data)
    return len(data)