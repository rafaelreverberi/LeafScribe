from __future__ import annotations

import hashlib
import mimetypes
import posixpath
import re
import shutil
import stat
import sqlite3
import uuid
import zipfile
from dataclasses import replace
from html import escape
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

from beook_export.database import list_books, get_book_metadata, get_issues_for_reference, get_notes_for_issues
from beook_export.discovery import find_profiles, DEFAULT_BEOOK_ROOT, resolve_course_cover_file
from beook_export.extractor import extract_book, BookContent
from beook_export.epub import write_epub
from .annotations import read_annotations, add_annotations, APPENDIX, AnnotationSource

MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_ENTRIES = 30000
NS = {'c': 'urn:oasis:names:tc:opendocument:xmlns:container', 'o': 'http://www.idpf.org/2007/opf', 'd': 'http://purl.org/dc/elements/1.1/'}
VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}


def safe_path(root: Path, name: str) -> Path:
    if '\\' in name or '\x00' in name or PurePosixPath(name).is_absolute():
        raise ValueError('Unsafe resource path')
    target = (root / name).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError('Resource escapes document directory')
    return target


def readonly(path: str):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def discover(root: str | None = None) -> dict:
    base = Path(root).expanduser() if root else DEFAULT_BEOOK_ROOT
    try:
        profiles = find_profiles(base)
    except FileNotFoundError:
        return {'books': [], 'warnings': ['No downloaded Beook profiles found. Import an EPUB, HTML, or PDF to get started.']}
    books, warnings = [], []
    for profile in profiles:
        try:
            with readonly(str(profile.db_path)) as conn:
                for book in list_books(conn, profile.profile_id):
                    metadata = get_book_metadata(conn, book.reference)
                    cover = resolve_course_cover_file(conn, book.reference, base)
                    issues = get_issues_for_reference(conn, book.reference)
                    count = len(read_annotations(conn, book.reference))
                    books.append(dict(id=f'beook:{profile.profile_id}:{book.reference}', title=book.title,
                                      author=', '.join(metadata.authors), kind='beook', path=str(profile.db_path),
                                      reference=book.reference, profile=profile.profile_id, root=str(base),
                                      coverPath=str(cover) if cover else None, chapterCount=book.chapter_count,
                                      annotationCount=count, language='und'))
        except (sqlite3.Error, OSError, ValueError) as exc:
            warnings.append(f'Profile {profile.profile_id}: {exc}')
    return {'books': books, 'warnings': warnings}


def unpack_epub(source: Path, target: Path) -> dict:
    target = target.resolve()
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_ENTRIES or sum(i.file_size for i in entries) > MAX_ARCHIVE_BYTES:
            raise ValueError('EPUB exceeds the 1 GB / 30,000 resource limit')
        if len({i.filename for i in entries}) != len(entries):
            raise ValueError('EPUB contains duplicate archive paths')
        for item in entries:
            safe_path(target, item.filename)
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('EPUB symlinks are not supported')
        if archive.read('mimetype').strip() != b'application/epub+zip':
            raise ValueError('Not an EPUB archive')
        if 'META-INF/encryption.xml' in archive.namelist():
            encryption = ET.fromstring(archive.read('META-INF/encryption.xml'))
            if any(e.tag.endswith('EncryptedData') for e in encryption.iter()):
                raise ValueError('Encrypted EPUB resources are not supported (including obfuscated fonts)')
        archive.extractall(target)
    container = ET.parse(target / 'META-INF/container.xml')
    package = container.find('c:rootfiles/c:rootfile', NS)
    if package is None:
        raise ValueError('EPUB has no package document')
    opf_path = safe_path(target, package.attrib['full-path'])
    opf = ET.parse(opf_path)
    manifest = {}
    cover = None
    for item in opf.findall('o:manifest/o:item', NS):
        href = unquote(item.attrib.get('href', ''))
        if urlsplit(href).scheme:
            continue
        path = safe_path(target, posixpath.join(str(opf_path.parent.relative_to(target)), href.split('#')[0]))
        if not path.is_file():
            raise ValueError('EPUB manifest references a missing local resource')
        manifest[item.attrib['id']] = (path, item.attrib.get('media-type', ''))
        if 'cover-image' in item.attrib.get('properties', '').split() and item.attrib.get('media-type','').startswith('image/'):
            cover = str(path)
    # EPUB 2 declares its cover with a metadata ID rather than EPUB 3 properties.
    if cover is None:
        for meta in opf.findall('o:metadata/o:meta', NS):
            if meta.get('name', '').lower() == 'cover':
                item = manifest.get(meta.get('content'))
                if item and item[1].startswith('image/'):
                    cover = str(item[0]); break
    if cover is None:
        images = {path for path, media in manifest.values() if media.startswith('image/')}
        for ref in opf.findall('o:guide/o:reference', NS):
            if ref.get('type', '').lower() != 'cover': continue
            link = urlsplit(ref.get('href', ''))
            if link.scheme or link.netloc: continue
            try:
                page = safe_path(target, posixpath.join(str(opf_path.parent.relative_to(target)), unquote(link.path)))
                if page in images:
                    cover = str(page); break
                tree = ET.parse(page)
                for element in tree.iter():
                    tag = element.tag.split('}')[-1]
                    href = element.get('src') if tag == 'img' else (element.get('href') or element.get('{http://www.w3.org/1999/xlink}href')) if tag == 'image' else None
                    if not href: continue
                    link = urlsplit(href)
                    if link.scheme or link.netloc: continue
                    candidate = safe_path(target, posixpath.join(str(page.parent.relative_to(target)), unquote(link.path)))
                    if candidate in images:
                        cover = str(candidate); break
            except (ValueError, OSError, ET.ParseError):
                continue  # Cover metadata is optional; retain the generated fallback.
            if cover: break
    chapters = []
    for ref in opf.findall('o:spine/o:itemref', NS):
        item = manifest.get(ref.attrib.get('idref'))
        if not item or not item[0].is_file():
            raise ValueError('EPUB spine references a missing resource')
        if item[1] not in ('application/xhtml+xml', 'text/html'):
            raise ValueError(f'Unsupported EPUB spine media type: {item[1]}')
        chapters.append(chapter(item[0], len(chapters)))
    if not chapters:
        raise ValueError('EPUB contains no readable chapters')
    def meta(name, default=''):
        return opf.findtext(f'o:metadata/d:{name}', default, NS)
    return dict(title=meta('title', source.stem), author=', '.join(e.text or '' for e in opf.findall('o:metadata/d:creator', NS)),
                language=meta('language', 'und'), chapters=chapters, coverPath=cover)


def chapter(path: Path, index: int) -> dict:
    html = path.read_text(encoding='utf-8', errors='replace')
    match = re.search(r'<h[1-6]\b[^>]*>(.*?)</h[1-6]>', html, re.I | re.S) or re.search(r'<title\b[^>]*>(.*?)</title>', html, re.I | re.S)
    from html import unescape
    title = unescape(re.sub('<[^>]+>', '', next((g for g in match.groups() if g is not None), '')).strip()) if match else ''
    fixed = 'beook-export-pos-fixed-page' in html
    return {'id': str(index), 'title': title or f'Section {index + 1}', 'path': str(path), 'layout': 'fixed' if fixed else 'flow', 'isAnnotation': path.name == 'beook-export-annotations.xhtml'}


class XHTML(HTMLParser):
    """Normalize ordinary HTML to XML while removing executable content."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = ET.Element('html', {'xmlns': 'http://www.w3.org/1999/xhtml'})
        self.stack = [self.root]
        self.blocked = 0
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'iframe', 'object', 'embed'):
            if tag not in VOID:
                self.blocked += 1
            return
        if self.blocked or tag == 'html':
            return
        if tag == 'base':
            return
        cleaned = {}
        for key, value in attrs:
            if key.startswith('on') or key in ('xmlns', 'srcdoc'):
                continue
            value = value or ''
            if key in ('href', 'src', 'action', 'poster', 'xlink:href') and urlsplit(value.strip()).scheme.lower() in ('javascript', 'file'):
                continue
            if tag == 'svg' or any(node.tag == 'svg' for node in self.stack):
                key = {'viewbox': 'viewBox', 'preserveaspectratio': 'preserveAspectRatio', 'gradientunits': 'gradientUnits', 'gradienttransform': 'gradientTransform', 'markerwidth': 'markerWidth', 'markerheight': 'markerHeight', 'refx': 'refX', 'refy': 'refY', 'textlength': 'textLength'}.get(key, key)
            cleaned[key] = value
        if tag == 'svg':
            cleaned['xmlns'] = 'http://www.w3.org/2000/svg'
        # Basic optional-end-tag handling keeps paragraphs and list items readable.
        if tag in ('p', 'li', 'tr', 'td', 'th') and self.stack[-1].tag == tag:
            self.stack.pop()
        node = ET.SubElement(self.stack[-1], tag, cleaned)
        if tag not in VOID:
            self.stack.append(node)
    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)
    def handle_endtag(self, tag):
        if tag in ('script', 'iframe', 'object') and self.blocked:
            self.blocked -= 1
            return
        if self.blocked:
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                self.stack = self.stack[:i]
                return
    def handle_data(self, data):
        if self.blocked:
            return
        node = self.stack[-1]
        if len(node):
            node[-1].tail = (node[-1].tail or '') + data
        else:
            node.text = (node.text or '') + data


def normalize_html(raw: str, title: str) -> bytes:
    parser = XHTML()
    parser.feed(raw)
    root = parser.root
    head = root.find('head')
    if head is None:
        head = ET.Element('head')
        root.insert(0, head)
    if head.find('title') is None:
        ET.SubElement(head, 'title').text = title
    if root.find('body') is None:
        body = ET.Element('body')
        for node in list(root):
            if node is not head:
                root.remove(node)
                body.append(node)
        root.append(body)
        body.text, root.text = root.text, None
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def copy_html(source: Path, target: Path):
    base = source.parent.resolve()
    pending = [source.resolve()]
    visited = set()
    while pending:
        item = pending.pop()
        if item in visited:
            continue
        visited.add(item)
        if len(visited) > MAX_ENTRIES:
            raise ValueError('Too many HTML resources')
        if not item.is_relative_to(base):
            raise ValueError('HTML resource is outside the imported document folder')
        dest = safe_path(target, item.relative_to(base).as_posix())
        dest.parent.mkdir(parents=True, exist_ok=True)
        if item.suffix.lower() in ('.html', '.htm', '.xhtml'):
            raw = item.read_text(encoding='utf-8', errors='replace')
            dest.write_bytes(normalize_html(raw, item.stem))
        else:
            shutil.copyfile(item, dest)
        if sum(p.stat().st_size for p in visited if p.is_file()) > MAX_ARCHIVE_BYTES:
            raise ValueError('HTML bundle exceeds 1 GB')
        if item.suffix.lower() not in ('.html', '.htm', '.xhtml', '.css', '.svg'):
            continue
        raw = dest.read_text(encoding='utf-8', errors='replace')
        refs = re.findall(r'(?:src|href|poster)\s*=\s*["\']([^"\']+)|url\(\s*["\']?([^\)"\']+)|@import\s+["\']([^"\']+)', raw, re.I)
        for groups in refs:
            ref = next(g for g in groups if g).strip()
            parsed = urlsplit(ref)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            candidate = (item.parent / unquote(parsed.path)).resolve()
            if not candidate.is_relative_to(base):
                raise ValueError('HTML references a resource outside its folder')
            if candidate.is_file():
                pending.append(candidate)
            else:
                raise ValueError(f'Missing local HTML resource: {parsed.path}')
    return target / source.name


def package_epub_directory(source: Path, destination: Path):
    """Copy an unpacked EPUB package into a standard archive; never edit the source."""
    files, total = [], 0
    for count, item in enumerate(source.rglob('*'), start=1):
        if count > MAX_ENTRIES:
            raise ValueError('EPUB exceeds the 1 GB / 30,000 resource limit')
        name = item.relative_to(source).as_posix()
        mode = item.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise ValueError('EPUB symlinks are not supported')
        safe_path(source, name)
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ValueError('EPUB packages may contain only regular files and folders')
        total += item.stat().st_size
        files.append((name, item))
        if len(files) > MAX_ENTRIES or total > MAX_ARCHIVE_BYTES:
            raise ValueError('EPUB exceeds the 1 GB / 30,000 resource limit')
    names = {name for name, _ in files}
    if not {'mimetype', 'META-INF/container.xml'} <= names:
        raise ValueError('Choose an EPUB package containing mimetype and META-INF/container.xml')
    if (source / 'mimetype').read_bytes().strip() != b'application/epub+zip':
        raise ValueError('Not an EPUB package')
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('mimetype', b'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        for name, item in sorted(files):
            if name != 'mimetype':
                archive.write(item, name)


def import_document(source: str, destination: str) -> dict:
    path = Path(source).expanduser().resolve(strict=True)
    kind = path.suffix.lower().lstrip('.')
    if kind in ('htm', 'xhtml'):
        kind = 'html'
    if kind not in ('html', 'epub', 'pdf'):
        raise ValueError('Choose an EPUB, HTML, or PDF document')
    if path.is_dir() and kind != 'epub':
        raise ValueError('Only EPUB packages can be imported as folders')
    if path.is_dir() and Path(destination).resolve().is_relative_to(path):
        raise ValueError('The managed library must be outside the source EPUB package')
    identity = str(uuid.uuid4())
    folder = Path(destination) / identity
    folder.mkdir(parents=True, exist_ok=False)
    try:
        info = dict(title=path.stem, author='', chapterCount=1, language='und', coverPath=None)
        if kind == 'html':
            info['title'] = chapter(path, 0)['title']
            saved = copy_html(path, folder)
        else:
            saved = folder / path.name
            if kind == 'epub' and path.is_dir():
                package_epub_directory(path, saved)
            else:
                shutil.copyfile(path, saved)
        if kind == 'epub':
            info.update(unpack_epub(saved, folder / 'unpacked'))
            info['chapterCount'] = len(info.pop('chapters'))
        return dict(id=identity, kind=kind, path=str(saved), annotationCount=0, **info)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise



def subset_imported_epub(book, root, chapters, selected, output):
    chosen = [c for c in chapters if c['id'] in selected]
    if not chosen:
        raise ValueError('Select at least one chapter')
    excluded = {Path(c['path']).resolve() for c in chapters if c['id'] not in selected}
    selected_paths = {Path(c['path']).resolve() for c in chosen}
    files, media = {}, {}
    for source in root.rglob('*'):
        if not source.is_file() or source.resolve() in excluded:
            continue
        relative = source.relative_to(root).as_posix()
        if source.suffix in ('.opf','.ncx') or relative == 'mimetype' or relative.startswith('META-INF/'):
            continue
        if source.name in ('nav.xhtml','toc.xhtml') and source.resolve() not in selected_paths:
            continue
        data = source.read_bytes()
        if source.suffix.lower() in ('.html','.htm','.xhtml'):
            text = data.decode('utf-8',errors='replace')
            def link(match):
                ref = urlsplit(match.group(3))
                if not ref.scheme and ref.path and (source.parent / unquote(ref.path)).resolve() in excluded:
                    return match.group(1) + ' aria-disabled="true"'
                return match.group(0)
            text = re.sub(r'(<a\b[^>]*?)\s+href=(["\'])(.*?)\2', link, text, flags=re.I)
            data = text.encode()
        key = 'OEBPS/source/'+relative
        files[key] = data
        media[key] = 'application/xhtml+xml' if source.suffix.lower() in ('.html','.htm','.xhtml') else mimetypes.guess_type(relative)[0] or 'application/octet-stream'
    content = BookContent(reference=book['id']+':selection', title=book['title'], language=book.get('language','und'),
                          spine_filenames=['OEBPS/source/'+Path(c['path']).relative_to(root).as_posix() for c in chosen],
                          files=files, media_types=media, pk_to_filename={}, authors=(book['author'],) if book.get('author') else ())
    write_epub(content,str(output))
    return chosen


def prepare(book: dict, workspace: str, annotations: bool = True, selected_chapters: list[str] | None = None) -> dict:
    target = Path(workspace)
    target.mkdir(parents=True, exist_ok=True)
    kind = book['kind']
    warnings = []
    annotation_count, inline_count = 0, 0
    if kind == 'beook':
        with readonly(book['path']) as conn:
            notes = read_annotations(conn, book['reference'])
            content = extract_book(AnnotationSource(conn, notes) if annotations else conn, book['reference'], Path(book['root']))
            issues_by_pk = {i.pk: i for i in get_issues_for_reference(conn, book['reference'])}
            page_labels = {}
            if 'ZPAGENUMBER' in {row[1] for row in conn.execute('PRAGMA table_info(ZILPTOPIC)')}:
                for row in conn.execute('SELECT ZRESOURCE,ZPAGENUMBER FROM ZILPTOPIC WHERE ZPAGENUMBER IS NOT NULL ORDER BY ZORDER'):
                    if row['ZRESOURCE'] not in page_labels:
                        page_labels[row['ZRESOURCE']] = str(row['ZPAGENUMBER'])
            resource_issues = {}
            raw_resources = {}
            for r in conn.execute("SELECT Z_PK,ZDATA,ZISSUE FROM ZILPRESOURCE WHERE ZMEDIATYPE IN ('text/html','application/xhtml+xml') AND ZDATA IS NOT NULL"):
                raw_resources[r['Z_PK']] = r['ZDATA']
                resource_issues[r['Z_PK']] = r['ZISSUE']
        available = [p for p in content.spine_filenames if p != APPENDIX]
        selected_paths = None
        if selected_chapters is not None:
            selected_paths = {p for i,p in enumerate(available) if str(i) in selected_chapters}
            if not selected_paths:
                raise ValueError('Select at least one chapter')
        if annotations:
            content, annotation_count, inline_count = add_annotations(content, notes, selected_paths)
        elif selected_paths is not None:
            content, _, _ = add_annotations(content, [], selected_paths)
        if not annotations:
            appendix = 'OEBPS/beook-export-annotations.xhtml'
            content = replace(content, spine_filenames=[p for p in content.spine_filenames if p != appendix],
                              files={k:v for k,v in content.files.items() if k != appendix},
                              media_types={k:v for k,v in content.media_types.items() if k != appendix})
        for name, data in content.files.items():
            dest = safe_path(target, name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        epub_path = target / 'book.epub'
        write_epub(content, str(epub_path))
        chapters = []
        inverse = {'OEBPS/'+name: pk for pk,name in content.pk_to_filename.items()}
        for name in content.spine_filenames:
            i = available.index(name) if name in available else len(available)
            entry = chapter(safe_path(target, name), i)
            if entry['layout'] == 'flow': entry['layout'] = 'beook-flow'
            resource_pk = inverse.get(name)
            raw = raw_resources.get(resource_pk,b'').decode('utf-8',errors='replace')
            issue = issues_by_pk.get(resource_issues.get(resource_pk))
            entry['groupID'] = issue.issue_id if issue else None
            entry['groupTitle'] = issue.title if issue else None
            labels = list(dict.fromkeys(re.findall(r'id=[\"\']page-([^\"\']+)', raw)))
            entry['sourceLabel'] = page_labels.get(resource_pk) if entry['layout'] == 'fixed' else ('–'.join(labels) if labels else None)
            chapters.append(entry)
        warnings.append('Interactive exercises are static. Fixed-layout sections retain their page canvas. Highlights retain their stored colors. Text notes follow their paragraph anchors. Annotations are also preserved in an appendix.' if annotations else 'Interactive exercises are static. Annotations are excluded.')
        unresolved = content.files.get(APPENDIX,b'').count(b'data-leafscribe-placement="unresolved"')
        if annotations and unresolved:
            warnings.append(f'{unresolved} annotations are retained only in the appendix because their stored positions are empty or could not be recovered unambiguously.')
        if annotations and not notes:
            warnings.append('No locally stored annotations were found for this book.')
    elif kind == 'epub':
        epub_path = Path(book['path'])
        info = unpack_epub(epub_path, target / 'unpacked')
        chapters = info['chapters']
        if selected_chapters is not None:
            epub_path = target / 'selected.epub'
            chapters = subset_imported_epub(book, (target / 'unpacked').resolve(), chapters, set(selected_chapters), epub_path)
    elif kind == 'html':
        # Library copies are immutable; preparation receives a private working copy.
        source = Path(book['path'])
        shutil.copytree(source.parent, target / 'html', dirs_exist_ok=True)
        html = target / 'html' / source.name
        chapters = [chapter(html, 0)]
        if selected_chapters is not None and '0' not in selected_chapters:
            raise ValueError('Select at least one chapter')
        files = {f'OEBPS/{p.relative_to(html.parent).as_posix()}': p.read_bytes() for p in html.parent.rglob('*') if p.is_file()}
        content = BookContent(reference=book['id'], title=book['title'], language=book.get('language', 'und'),
                              spine_filenames=[f'OEBPS/{source.name}'], files=files,
                              media_types={p: ('application/xhtml+xml' if p.lower().endswith(('.html', '.htm', '.xhtml')) else mimetypes.guess_type(p)[0] or 'application/octet-stream') for p in files}, pk_to_filename={},
                              authors=(book['author'],) if book.get('author') else ())
        epub_path = target / 'book.epub'
        write_epub(content, str(epub_path))
    elif kind == 'pdf':
        return {'chapters': [], 'pdfPath': book['path'], 'warnings': []}
    else:
        raise ValueError('Unsupported document kind')
    if any(re.search(r'(?:src|href|url)\s*[=(]\s*["\']?https?://', Path(c['path']).read_text(encoding='utf-8', errors='replace'), re.I) for c in chapters):
        warnings.append('This document contains remote references. Web resources are blocked during preview and PDF export; localize required media before importing.')
    return dict(title=book['title'], chapters=chapters, epubPath=str(epub_path), warnings=warnings, annotationCount=annotation_count, inlineAnnotationCount=inline_count)


def epub_from_pages(pages: list[dict], title: str, destination: str):
    if not pages:
        raise ValueError('No extractable text. Enable OCR for scanned PDFs.')
    files, spine = {}, []
    for i, page in enumerate(pages):
        name = f'OEBPS/page-{i+1}.xhtml'
        files[name] = normalize_html(f'<html><head><title>{escape(page["title"])}</title></head><body><h1>{escape(page["title"])}</h1>{page["html"]}</body></html>', page['title'])
        spine.append(name)
    content = BookContent(reference=f'leafscribe:{uuid.uuid4()}', title=title, language='und', spine_filenames=spine,
                          files=files, media_types={k: 'application/xhtml+xml' for k in files}, pk_to_filename={})
    write_epub(content, destination)
    return {'path': destination, 'chapterCount': len(spine)}
