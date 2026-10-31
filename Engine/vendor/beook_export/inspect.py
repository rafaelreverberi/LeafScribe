"""Deep inspection of exported EPUB archives for clipping and completeness."""

from __future__ import annotations

import re
import posixpath
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"
GIF_MAGIC = b"GIF8"
REMOTE_SCHEMES = {"http", "https", "mailto", "tel", "data", "javascript"}
OPF_NS = "{http://www.idpf.org/2007/opf}"
CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _is_external_ref(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme.lower() in REMOTE_SCHEMES or value.startswith("//")


def _resolve_archive_ref(base_name: str, value: str) -> str | None:
    value = value.strip()
    if unquote(value).startswith("#"):
        return None
    if not value or value.startswith("#") or _is_external_ref(value):
        return None
    split = urlsplit(value)
    if split.scheme:
        return None
    path = unquote(split.path)
    if not path:
        return None
    if path.startswith("/"):
        path = path.lstrip("/")
    else:
        path = posixpath.normpath(posixpath.join(posixpath.dirname(base_name), path))
    return path


def inspect_epub(epub_path: Path, reference: str = "", spine_count: int | None = None) -> tuple[bool, str]:
    lines: list[str] = []
    ok = True

    def fail(msg: str) -> None:
        nonlocal ok
        ok = False
        lines.append(f"FAIL: {msg}")

    def pass_(msg: str) -> None:
        lines.append(f"OK: {msg}")

    if reference:
        lines.append(f"reference: {reference}")

    if not epub_path.is_file():
        fail(f"missing file {epub_path}")
        return ok, "\n".join(lines)

    size = epub_path.stat().st_size
    lines.append(f"file: {epub_path}")
    lines.append(f"size: {size} bytes")
    if size < 1000:
        fail(f"file too small ({size}B)")

    try:
        zf = zipfile.ZipFile(epub_path)
    except zipfile.BadZipFile:
        fail("not a valid zip/epub")
        return ok, "\n".join(lines)

    names = zf.namelist()
    name_set = set(names)
    if not names:
        fail("empty archive")
        return ok, "\n".join(lines)
    duplicate_names = sorted({name for name in names if names.count(name) > 1})
    if duplicate_names:
        fail(f"duplicate archive entries: {duplicate_names[:5]}")
    else:
        pass_("no duplicate archive entries")

    if names[0] != "mimetype":
        fail(f"mimetype not first (got {names[0]})")
    else:
        pass_("mimetype first in archive")

    if zf.read("mimetype").strip() != b"application/epub+zip":
        fail("wrong mimetype content")
    else:
        pass_("mimetype content correct")

    xhtml_names = [n for n in names if n.endswith(".xhtml") or n.endswith(".html")]
    auxiliary_xhtml = {"OEBPS/nav.xhtml", "OEBPS/cover.xhtml"}
    reading_xhtml_names = [n for n in xhtml_names if n not in auxiliary_xhtml]
    lines.append(f"xhtml files: {len(xhtml_names)}")
    if spine_count is not None:
        if len(reading_xhtml_names) != spine_count:
            fail(f"reading xhtml count {len(reading_xhtml_names)} != spine {spine_count}")
        else:
            pass_(f"xhtml count matches spine ({spine_count})")

    if not xhtml_names:
        fail("no xhtml/html files")
        return ok, "\n".join(lines)

    if "META-INF/container.xml" in name_set:
        try:
            container_root = ElementTree.fromstring(zf.read("META-INF/container.xml"))
            rootfile = container_root.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile")
            if rootfile is not None and rootfile.attrib.get("full-path") == "OEBPS/content.opf":
                pass_("container.xml points to content.opf")
            else:
                fail("container.xml does not point to OEBPS/content.opf")
        except ElementTree.ParseError as exc:
            fail(f"container.xml XML parse failure: {exc}")
    else:
        fail("missing META-INF/container.xml")

    content_opf = zf.read("OEBPS/content.opf").decode("utf-8", errors="replace") if "OEBPS/content.opf" in names else ""
    opf_root: ElementTree.Element | None = None
    if "OEBPS/nav.xhtml" not in names:
        fail("missing EPUB 3 nav document")
    elif 'properties="nav"' not in content_opf:
        fail("nav document is not marked with properties=\"nav\"")
    else:
        pass_("EPUB 3 nav document present")

    if "OEBPS/toc.ncx" not in names:
        fail("missing EPUB 2 NCX navigation document")
    elif 'media-type="application/x-dtbncx+xml"' not in content_opf:
        fail("NCX is not declared in the manifest")
    elif '<spine toc="ncx">' not in content_opf:
        fail("spine is not linked to the NCX document")
    else:
        pass_("EPUB 2 NCX navigation present")

    if "<guide>" in content_opf and 'type="toc"' in content_opf:
        pass_("EPUB 2 guide landmarks present")
    elif "<guide>" not in content_opf:
        fail("missing EPUB 2 guide landmarks")

    if "<dc:creator" in content_opf:
        pass_("Dublin Core creators declared")
    if "urn:isbn:" in content_opf:
        pass_("ISBN identifier declared")

    if "OEBPS/toc.ncx" in names:
        try:
            ElementTree.fromstring(zf.read("OEBPS/toc.ncx"))
            pass_("NCX parses as XML")
        except ElementTree.ParseError as exc:
            fail(f"NCX XML parse failure: {exc}")

    if content_opf:
        try:
            opf_root = ElementTree.fromstring(content_opf)
            pass_("content.opf parses as XML")
        except ElementTree.ParseError as exc:
            fail(f"content.opf XML parse failure: {exc}")
    else:
        fail("missing OEBPS/content.opf")

    manifest_ids: set[str] = set()
    manifest_paths: set[str] = set()
    if opf_root is not None:
        manifest = opf_root.find(f"{OPF_NS}manifest")
        spine = opf_root.find(f"{OPF_NS}spine")
        if manifest is None:
            fail("missing OPF manifest")
        else:
            duplicate_ids: set[str] = set()
            for item in manifest.findall(f"{OPF_NS}item"):
                item_id = item.attrib.get("id", "")
                href = item.attrib.get("href", "")
                if item_id in manifest_ids:
                    duplicate_ids.add(item_id)
                manifest_ids.add(item_id)
                if href:
                    manifest_path = posixpath.normpath(f"OEBPS/{href}")
                    manifest_paths.add(manifest_path)
                    if manifest_path not in name_set:
                        fail(f"manifest item missing from archive: {manifest_path}")
            if duplicate_ids:
                fail(f"duplicate manifest IDs: {sorted(duplicate_ids)[:5]}")
            else:
                pass_(f"manifest IDs unique ({len(manifest_ids)})")
            if manifest_paths and all(path in name_set for path in manifest_paths):
                pass_("all manifest items exist in archive")
        if spine is None:
            fail("missing OPF spine")
        else:
            bad_spine_refs = [
                itemref.attrib.get("idref", "")
                for itemref in spine.findall(f"{OPF_NS}itemref")
                if itemref.attrib.get("idref", "") not in manifest_ids
            ]
            if bad_spine_refs:
                fail(f"spine idrefs missing from manifest: {bad_spine_refs[:5]}")
            else:
                pass_("all spine idrefs resolve to manifest items")

    if 'properties="cover-image"' in content_opf:
        pass_("EPUB cover image declared")
    elif "OEBPS/cover.xhtml" in names:
        pass_("EPUB cover page present")

    localhost_files: list[str] = []
    unclosed: list[str] = []
    xml_failures: list[str] = []
    missing_refs: list[str] = []
    all_text = ""

    for name in xhtml_names:
        data = zf.read(name)
        text = data.decode("utf-8", errors="replace")
        all_text += text + "\n"
        if "localhost" in text.lower():
            localhost_files.append(name)
        lower = text.lower()
        if "</html>" not in lower and "</body>" not in lower:
            unclosed.append(name)
        try:
            ElementTree.fromstring(data)
        except ElementTree.ParseError as exc:
            xml_failures.append(f"{name}: {exc}")
        for ref in re.findall(r'\b(?:src|href)="([^"]+)"', text, re.IGNORECASE):
            resolved = _resolve_archive_ref(name, ref)
            if resolved and resolved not in name_set:
                missing_refs.append(f"{name} -> {ref}")

    if localhost_files:
        fail(f"localhost in {len(localhost_files)} xhtml files: {localhost_files[:3]}")
    else:
        pass_("zero localhost URLs in xhtml")

    if unclosed:
        fail(f"{len(unclosed)} xhtml without </html>: {unclosed[:3]}")
    else:
        pass_("all xhtml properly closed")

    if xml_failures:
        fail(f"{len(xml_failures)} xhtml XML parse failure(s): {xml_failures[:3]}")
    else:
        pass_("all xhtml parses as XML")

    css_missing_refs: list[str] = []
    for name in names:
        if not name.startswith("OEBPS/") or not name.endswith(".css"):
            continue
        text = zf.read(name).decode("utf-8", errors="replace")
        text = CSS_COMMENT_RE.sub("", text)
        for ref in re.findall(r"url\((?:'|\")?([^'\"\)]+)(?:'|\")?\)", text, re.IGNORECASE):
            resolved = _resolve_archive_ref(name, ref)
            if resolved and resolved not in name_set:
                css_missing_refs.append(f"{name} -> {ref}")

    if missing_refs:
        fail(f"{len(missing_refs)} missing xhtml reference(s): {missing_refs[:5]}")
    else:
        pass_("all local xhtml references resolve")

    if css_missing_refs:
        fail(f"{len(css_missing_refs)} missing css reference(s): {css_missing_refs[:5]}")
    else:
        pass_("all local css references resolve")

    content_tables = 0
    unmarked_tables = 0
    for match in re.finditer(r"<table\b([^>]*)>", all_text, re.IGNORECASE):
        attrs = match.group(1)
        if "pagenavigation" in attrs.lower():
            continue
        content_tables += 1
        if "beook-export-table" not in attrs:
            unmarked_tables += 1

    if content_tables:
        if unmarked_tables:
            fail(f"{unmarked_tables}/{content_tables} content tables missing beook-export-table marker")
        else:
            pass_(f"all {content_tables} content tables marked for reader layout")

    overflow_count = all_text.count("overflow-container")
    if overflow_count:
        fail(f"{overflow_count} overflow-container wrappers remain in xhtml")
    else:
        pass_("no overflow-container scroll traps in xhtml")

    if "OEBPS/beook-export-reader.css" not in names:
        fail("missing injected reader stylesheet")
    else:
        pass_("reader stylesheet packaged")

    pass_(f"combined xhtml text length: {len(all_text)} chars")

    asset_checks = 0
    for name in names:
        if not name.startswith("OEBPS/"):
            continue
        ext = Path(name).suffix.lower()
        if ext not in (".png", ".jpg", ".jpeg", ".gif"):
            continue
        head = zf.read(name)[:8]
        asset_checks += 1
        if ext == ".png" and not head.startswith(PNG_MAGIC):
            fail(f"bad PNG magic in {name}")
        elif ext in (".jpg", ".jpeg") and not head.startswith(JPEG_MAGIC):
            fail(f"bad JPEG magic in {name}")
        elif ext == ".gif" and not head.startswith(GIF_MAGIC):
            fail(f"bad GIF magic in {name}")

    if asset_checks:
        pass_(f"checked {asset_checks} image magic bytes")

    svg_failures: list[str] = []
    for name in names:
        if Path(name).suffix.lower() != ".svg":
            continue
        try:
            ElementTree.fromstring(zf.read(name))
        except ElementTree.ParseError as exc:
            svg_failures.append(f"{name}: {exc}")
    if svg_failures:
        fail(f"{len(svg_failures)} SVG XML parse failure(s): {svg_failures[:3]}")
    elif any(Path(name).suffix.lower() == ".svg" for name in names):
        pass_("all SVG files parse as XML")

    lines.append(f"overall: {'PASS' if ok else 'FAIL'}")
    return ok, "\n".join(lines)