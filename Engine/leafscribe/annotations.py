"""LeafScribe annotation adapter; upstream extraction stays unchanged."""
from __future__ import annotations
import re
import json
import math
import sqlite3
from dataclasses import replace
from html import escape, unescape
from xml.etree import ElementTree as ET
from beook_export.database import _strip_html_text, _coredata_to_iso, get_issues_for_reference

APPENDIX = 'OEBPS/beook-export-annotations.xhtml'


def read_annotations(conn, reference):
    issues = get_issues_for_reference(conn, reference)
    if not issues:
        return []
    columns = {row[1] for row in conn.execute('PRAGMA table_info(ZILPNOTE)')}
    extras = ', '.join('n.' + column + ' AS ' + alias if column in columns else 'NULL AS ' + alias
                       for column, alias in [('ZCOLORINDEX','color_index'), ('ZALPHA','alpha'),
                                             ('ZPRESENTATIONSTYLE','presentation')])
    try:
        rows = conn.execute('''SELECT n.Z_PK AS id, n.ZTITLE AS title, n.ZTEXT AS text, n.ZTYPE AS type,
            n.ZCOLOR AS color, n.ZDATE AS date, n.ZACCESSPATH AS range_path, ''' + extras + ''',
            i.ZISSUEID AS issue_id, i.ZTITLE AS issue_title, t.ZACCESSPATH AS topic,
            t.ZRESOURCE AS resource_pk
            FROM ZILPNOTE n JOIN ZILPTOPIC t ON n.ZTOPIC=t.Z_PK
            JOIN ZILPISSUEDEF i ON t.ZISSUE=i.Z_PK
            WHERE t.ZISSUE IN (''' + ','.join('?' for _ in issues) + ''')
            AND IFNULL(n.ZNOTEDELETED,0)=0 ORDER BY i.ZORDER,t.ZORDER,n.ZDATE,n.Z_PK''', [i.pk for i in issues]).fetchall()
    except sqlite3.OperationalError as exc:
        # Old profiles without the annotation table are supported; schema failures aren't silently swallowed.
        if 'no such table: ZILPNOTE' in str(exc):
            return []
        raise
    return [dict(id=str(r['id']), title=_strip_html_text(r['title']), text=note_text(r['text']) if r['type']=='N' else _strip_html_text(r['text']),
                 type=r['type'] or 'N', color=r['color'] or '', date=_coredata_to_iso(r['date']) or '',
                 issueID=r['issue_id'] or '', issueTitle=r['issue_title'] or '', topic=r['topic'] or '',
                 resourcePK=r['resource_pk'], rangePath=r['range_path'] or '',
                 colorIndex=r['color_index'], alpha=r['alpha'], presentation=r['presentation'] or '') for r in rows]



def note_text(value):
    """Keep paragraph spacing in note bodies; escape it when rendering."""
    text = str(value or '')
    text = re.sub(r'(?i)<br\s*/?>', '\n', text)
    text = re.sub(r'(?i)</(?:p|div)>', '\n\n', text)
    return unescape(re.sub(r'<[^>]*>', '', text)).strip()

# Beook's four annotation colors (the note paper is deliberately much paler).
PALETTE = {1: ('#f1e23e', '#fdfce9'), 2: ('#67c2d5', '#eff9fb'),
           3: ('#83c68e', '#ecf6ee'), 4: ('#e9a3bf', '#fdf5f8')}


def annotation_colors(note):
    border, paper = PALETTE.get(note.get('colorIndex'), PALETTE[1])
    custom = note.get('color', '')
    if re.fullmatch(r'#[0-9a-fA-F]{6}', custom or ''):
        border = custom
    alpha = note.get('alpha')
    alpha = alpha if isinstance(alpha, (float, int)) and math.isfinite(alpha) else .5
    alpha = max(0, min(1, alpha))
    rgb = tuple(int(border[i:i+2], 16) for i in (1,3,5))
    return border, paper, f'rgba({rgb[0]},{rgb[1]},{rgb[2]},{alpha})'


def resolve_path(root, path):
    """Resolve Beook's restricted XPath, including DOM text() nodes, without eval."""
    parts = path.split('/')
    if not parts or parts[0] != 'html':
        return None
    node = root
    for index, part in enumerate(parts[1:]):
        text = re.fullmatch(r'text\(\)\[(\d+)\]', part)
        if text:
            if index != len(parts) - 2:
                return None
            slots = ([(node, False)] if node.text else []) + [(c, True) for c in node if c.tail]
            n = int(text[1])
            return slots[n-1] if 0 < n <= len(slots) else None
        match = re.fullmatch(r'([A-Za-z][A-Za-z0-9_-]*)\[(\d+)\]', part)
        if not match:
            return None
        children = [c for c in node if c.tag.split('}')[-1] == match[1]]
        n = int(match[2])
        if not 0 < n <= len(children):
            return None
        node = children[n-1]
    return node


def text_slots(root):
    slots, bounds, length = [], {}, 0
    def visit(node):
        nonlocal length
        start = length
        if node.text:
            slots.append((node, False, length, node.text)); length += len(node.text)
        for child in node:
            visit(child)
            if child.tail:
                slots.append((child, True, length, child.tail)); length += len(child.tail)
        bounds[node] = (start, length)
    visit(root)
    return slots, bounds


def dom_boundary(root, path, offset, slots, bounds):
    target = resolve_path(root, path)
    if target is None:
        return None
    if isinstance(target, tuple):
        node, tail = target
        for owner, is_tail, base, text in slots:
            if owner is node and is_tail == tail:
                # WebKit/JavaScript offsets count UTF-16 code units, not Python characters.
                units = 0
                for i, char in enumerate(text):
                    if units == offset: return base + i
                    units += 2 if ord(char) > 0xffff else 1
                return base + len(text) if units == offset else None
        return None
    # DOM element offsets count childNodes, including text and whitespace nodes.
    children = []
    if target.text: children.append(bounds[target][0])
    for child in target:
        children.append(bounds[child][0])
        if child.tail: children.append(next(base for owner, tail, base, _ in slots if owner is child and tail))
    return children[offset] if 0 <= offset < len(children) else bounds[target][1] if offset == len(children) else None


def note_element(note, ns):
    border, paper, _ = annotation_colors(note)
    try: presentation = json.loads(note.get('presentation') or '{}')
    except (ValueError, TypeError): presentation = {}
    if not isinstance(presentation, dict): presentation = {}
    width = presentation.get('width', 489)
    if not isinstance(width, (int,float)) or not math.isfinite(width): width = 489
    width = max(80, min(2000, width))
    aside = ET.Element(ns+'aside', {'class':'leafscribe-inline-note', 'data-leafscribe-note':note['id'],
        'style':f'--note-border:{border};--note-paper:{paper};width:{width}px;max-width:100%;box-sizing:border-box;'
                f'border:2px solid {border};background:{paper};border-radius:6px;padding:16px;'
                'margin:1em 0;box-shadow:2px 3px 5px #0003;font-family:Arial,sans-serif;'
                'line-height:1.5;position:relative;break-inside:avoid;white-space:pre-wrap;'})
    if note['title']:
        ET.SubElement(aside,ns+'header',{'style':'font-size:1.2em;font-weight:bold;color:#000;margin:0 0 .7em;'}).text=note['title']
    ET.SubElement(aside,ns+'section',{'class':'leafscribe-note-text'}).text=note['text']
    ET.SubElement(aside,ns+'i',{'aria-hidden':'true','style':f'position:absolute;right:4px;bottom:4px;width:0;height:0;border-left:24px solid transparent;border-bottom:24px solid {border};'})
    return aside


def annotate_source(data, notes):
    """Resolve every range against the immutable original DOM before inserting any marks."""
    try: root = ET.fromstring(data)
    except ET.ParseError: return data
    slots, bounds = text_slots(root)
    parents = {c:p for p in root.iter() for c in p}
    ranges, anchors = [], []
    ns = '{http://www.w3.org/1999/xhtml}' if root.tag.startswith('{') else ''
    flat = ''.join(text for _, _, _, text in slots)
    for note in notes:
        parts = note['rangePath'].split('|')
        if note['type'] == 'H' and len(parts) == 4:
            try:
                start = dom_boundary(root, parts[0], int(parts[1]), slots, bounds)
                end = dom_boundary(root, parts[2], int(parts[3]), slots, bounds)
            except ValueError: continue
            valid = start is not None and end is not None and end > start
            # Persisted paths can include temporary Beook highlight wrappers or stale offsets.
            # A stored quote must agree before trusting such a path.
            if valid and note['text']:
                valid = normalized(flat[start:end])[0].strip() == normalized(note['text'])[0].strip()
            if not valid and note['text']:
                # A Beook-added highlight span can shift later XPath indexes. Keep the
                # original paragraph as context so repeated quotes elsewhere stay distinct.
                common = []
                for left, right in zip(parts[0].split('/'), parts[2].split('/')):
                    if left != right or left.startswith('text()'): break
                    common.append(left)
                scopes = []
                while common:
                    ancestor = resolve_path(root, '/'.join(common))
                    if ancestor is not None and not isinstance(ancestor, tuple):
                        scopes.append(bounds[ancestor]); break
                    common.pop()
                scopes.append((0,len(flat)))
                needle = normalized(note['text'])[0].strip()
                for lo, hi in scopes:
                    haystack, offsets = normalized(flat[lo:hi])
                    if needle and haystack.count(needle) == 1:
                        at = haystack.index(needle)
                        start, end = lo+offsets[at], lo+offsets[at+len(needle)-1]+1
                        valid = True; break
            if valid:
                ranges.append((start, end, note))
        elif note['type'] == 'N' and (note['title'] or note['text']):
            anchor = resolve_path(root, parts[0])
            if anchor is not None and not isinstance(anchor, tuple):
                # Notes anchored to an inline span/image appear after its enclosing paragraph.
                while anchor in parents and anchor.tag.split('}')[-1] not in ('p','div','td','th','li','section','article'):
                    anchor = parents[anchor]
                if anchor in parents: anchors.append((anchor,note))
    for node, tail, base, text in slots:
        applicable = [(max(0,a-base),min(len(text),b-base),n) for a,b,n in ranges if a < base+len(text) and b > base]
        if not applicable: continue
        cuts = sorted({0,len(text)} | {x for a,b,n in applicable for x in (a,b)})
        pieces = []
        for lo, hi in zip(cuts,cuts[1:]):
            active = [n for a,b,n in applicable if a <= lo and b >= hi]
            if active:
                outer = current = ET.Element(ns+'mark')
                for index,n in enumerate(active):
                    if index: current = ET.SubElement(current,ns+'mark')
                    current.attrib.update({'data-leafscribe-annotation':n['id'], 'style':f'background:{annotation_colors(n)[2]}!important;color:inherit!important;'})
                current.text = text[lo:hi]; pieces.append(outer)
            else: pieces.append(text[lo:hi])
        if tail:
            parent=parents[node]; at=list(parent).index(node)+1; node.tail=''; previous=node
        else:
            parent=node; at=0; node.text=''; previous=None
        for piece in pieces:
            if isinstance(piece,str):
                if previous is None: parent.text=(parent.text or '')+piece
                else: previous.tail=(previous.tail or '')+piece
            else:
                parent.insert(at,piece); at+=1; previous=piece
    # Reverse insertion keeps multiple notes at one anchor in their stored order.
    for anchor,note in reversed(anchors):
        parent=parents[anchor]; aside=note_element(note,ns)
        aside.tail=anchor.tail; anchor.tail=None
        parent.insert(list(parent).index(anchor)+1,aside)
    if not ranges and not anchors: return data
    ET.register_namespace('', 'http://www.w3.org/1999/xhtml')
    return ET.tostring(root,encoding='utf-8',xml_declaration=True)


class AnnotationSource:
    """Read-only view of the pinned upstream resource query; only returned bytes change.

    No SQL is added or rewritten. Other queries are delegated unchanged to the original
    read-only connection. Annotations therefore follow the upstream resource transforms
    and chapter consolidation without patching the vendor or editing the database.
    """
    def __init__(self, connection, notes):
        self.connection = connection
        self.by_resource = {}
        for note in notes: self.by_resource.setdefault(note['resourcePK'], []).append(note)

    def execute(self, sql, parameters=()):
        cursor = self.connection.execute(sql, parameters)
        columns = {c[0] for c in cursor.description or []}
        if 'FROM ZILPRESOURCE' in sql and {'pk','data','media_type','plain_data'} <= columns:
            rows = []
            for row in cursor.fetchall():
                row = dict(row)
                if row['pk'] in self.by_resource and row['media_type'] in ('text/html','application/xhtml+xml'):
                    row['data'] = annotate_source(row['data'], self.by_resource[row['pk']])
                rows.append(row)
            class Rows:
                def fetchall(self): return rows
            return Rows()
        return cursor


def normalized(text):
    out, offsets = [], []
    for i, char in enumerate(text):
        if char in ('\u00ad', '\u200b'):
            continue
        if char.isspace():
            if out and out[-1] != ' ':
                out.append(' '); offsets.append(i)
        else:
            out.append(char); offsets.append(i)
    return ''.join(out), offsets


def mark_quote(data: bytes, quote: str, identity: str, color: str = '#fff1a6') -> tuple[bytes, bool]:
    """Mark a stored quote across inline spans without replacing or deleting source text."""
    try:
        tree = ET.fromstring(data)
    except ET.ParseError:
        return data, False
    body = next((e for e in tree.iter() if e.tag.split('}')[-1] == 'body'), None)
    if body is None or not quote.strip():
        return data, False
    segments, full = [], []
    length = 0
    def visit(node):
        nonlocal length
        if node.tag.split('}')[-1] in ('script','style'):
            return
        if node.text:
            segments.append((node, False, length, node.text)); full.append(node.text); length += len(node.text)
        for child in list(node):
            visit(child)
            if child.tail:
                segments.append((child, True, length, child.tail)); full.append(child.tail); length += len(child.tail)
    visit(body)
    flat, offsets = normalized(''.join(full))
    needle = normalized(quote)[0].strip()
    start = flat.find(needle)
    if start < 0 or not needle or flat.count(needle) != 1:
        return data, False
    end = offsets[start + len(needle) - 1] + 1
    start = offsets[start]
    parents = {c:p for p in body.iter() for c in p}
    ns = '{http://www.w3.org/1999/xhtml}' if body.tag.startswith('{') else ''
    for node, tail, base, text in segments:
        lo, hi = max(0,start-base), min(len(text),end-base)
        if hi <= lo:
            continue
        mark = ET.Element(ns+'mark', {'data-leafscribe-annotation':identity, 'style':f'background:{color}!important;color:inherit!important;'})
        mark.text = text[lo:hi]; mark.tail = text[hi:]
        if tail:
            node.tail = text[:lo]
            parent = parents[node]; parent.insert(list(parent).index(node)+1, mark)
        else:
            node.text = text[:lo]; node.insert(0,mark)
    ET.register_namespace('', 'http://www.w3.org/1999/xhtml')
    return ET.tostring(tree, encoding='utf-8', xml_declaration=True), True


def add_annotations(content, annotations, selected_paths=None):
    files, media = dict(content.files), dict(content.media_types)
    spine = [p for p in content.spine_filenames if p != APPENDIX]
    if selected_paths is not None:
        spine = [p for p in spine if p in selected_paths]
    included, inline = [], 0
    for note in annotations:
        original = content.pk_to_filename.get(note['resourcePK'])
        original_path = 'OEBPS/'+original if original else None
        all_pages = [p for p in content.spine_filenames if p != APPENDIX]
        marker = ('data-leafscribe-annotation' if note['type']=='H' else 'data-leafscribe-note') + '="' + escape(note['id']) + '"'
        located = [p for p in all_pages if marker.encode() in files[p]]
        if located:
            candidates = [p for p in located if p in spine]
        elif original_path in all_pages:
            # A known, unselected page must never fall back to a different page in its chapter.
            candidates = [original_path] if original_path in spine else []
        else:
            group_pages = [p for p in all_pages if p.split('/')[-1].startswith(note['issueID'])]
            candidates = [p for p in group_pages if p in spine]
            if selected_paths is not None and not set(group_pages).issubset(set(spine)):
                # Consolidation may absorb an original page. Route it only by a unique stored quote.
                quoted_pages = []
                if note['text']:
                    quote = normalized(note['text'])[0].strip()
                    for path in group_pages:
                        try: plain = ''.join(ET.fromstring(files[path]).itertext())
                        except ET.ParseError: continue
                        if quote in normalized(plain)[0]: quoted_pages.append(path)
                candidates = quoted_pages if len(quoted_pages)==1 and quoted_pages[0] in spine else []
        if selected_paths is not None and not candidates:
            continue
        note = dict(note)
        note['inline'] = bool(located and candidates)
        if note['inline'] and note['type']=='H': inline += 1
        if not note['inline'] and note['type'] == 'H' and note['text']:
            for path in candidates:
                result, found = mark_quote(files[path], note['text'], note['id'], annotation_colors(note)[2])
                if found:
                    files[path] = result; note['inline'] = True; inline += 1; break
        if not note['inline'] and note['type'] == 'N' and not note['rangePath'] and candidates and (note['title'] or note['text']):
            path = candidates[0]
            try:
                root = ET.fromstring(files[path])
                body = next((e for e in root.iter() if e.tag.split('}')[-1] == 'body'),None)
                if body is not None:
                    ns = '{http://www.w3.org/1999/xhtml}' if body.tag.startswith('{') else ''
                    aside = ET.SubElement(body,ns+'aside',{'class':'leafscribe-inline-note','data-leafscribe-note':note['id'], 'style':'border-left:3px solid #607f69;padding:8px 12px;margin:16px 0;'})
                    ET.SubElement(aside,ns+'strong').text = 'Notiz' + (' · Abschnitt '+note['topic'] if note['topic'] else '')
                    if note['title']: ET.SubElement(aside,ns+'h4').text=note['title']
                    ET.SubElement(aside,ns+'p').text=note['text'] or 'Kein zusätzlicher Text gespeichert.'
                    ET.register_namespace('', 'http://www.w3.org/1999/xhtml')
                    files[path] = ET.tostring(root,encoding='utf-8',xml_declaration=True)
                    note['inline'] = True
            except ET.ParseError:
                pass  # The appendix remains the lossless fallback.
        included.append(note)
    if selected_paths is not None:
        excluded = set(content.spine_filenames) - set(spine) - {APPENDIX}
        for path in excluded:
            files.pop(path, None); media.pop(path, None)
        from urllib.parse import urlsplit, unquote
        import posixpath
        for path in spine:
            text = files[path].decode('utf-8',errors='replace')
            def link(match):
                ref = urlsplit(match.group(3))
                target = posixpath.normpath(posixpath.join(posixpath.dirname(path),unquote(ref.path)))
                return match.group(1) + ' aria-disabled="true"' if not ref.scheme and ref.path and target in excluded else match.group(0)
            files[path] = re.sub(r'(<a\b[^>]*?)\s+href=(["\'])(.*?)\2', link, text, flags=re.I).encode()
    if included:
        entries = []
        labels = {'H':'Markierung', 'N':'Notiz', 'B':'Lesezeichen'}
        for note in included:
            location = ' · '.join(x for x in (note['issueTitle'], 'Abschnitt '+note['topic'] if note['topic'] else '', note['date']) if x)
            text = note['text'] or ('Kein markierter Text gespeichert; die Position ist im Beook-Dokument hinterlegt.' if note['type']=='H' else 'Kein zusätzlicher Text gespeichert.')
            placement = 'inline' if note['inline'] else 'unresolved'
            status = '' if note['inline'] else ' · Nur im Anhang: gespeicherte Position nicht eindeutig wiederherstellbar'
            entries.append(f'<li id="note-{escape(note["id"])}" data-leafscribe-placement="{placement}"><p class="note-meta">{escape(labels.get(note["type"],"Anmerkung"))} · {escape(location)}{status}</p>'
                           + (f'<h3>{escape(note["title"])}</h3>' if note['title'] else '')
                           + f'<blockquote>{escape(text)}</blockquote></li>')
        appendix = ('<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml" lang="de"><head><meta charset="utf-8"/>'
                    '<title>Notizen und Markierungen</title><style>body{font-family:Georgia,serif;line-height:1.45}li{margin-bottom:1.3em}.note-meta{font:10pt sans-serif;color:#555}blockquote{margin:.4em 0;padding-left:1em;border-left:2px solid #798b78;white-space:pre-wrap}</style></head>'
                    f'<body class="leafscribe-annotations"><h1>Notizen und Markierungen</h1><p>{len(included)} Anmerkungen; {inline} Markierungen zusätzlich im Text hervorgehoben.</p><ol>'+''.join(entries)+'</ol></body></html>')
        files[APPENDIX] = appendix.encode(); media[APPENDIX] = 'application/xhtml+xml'; spine.append(APPENDIX)
    else:
        files.pop(APPENDIX,None); media.pop(APPENDIX,None)
    return replace(content, files=files, media_types=media, spine_filenames=spine), len(included), inline
