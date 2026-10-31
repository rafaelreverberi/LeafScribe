#!/usr/bin/env python3
"""Opt-in live adapter validation; reports counts only, never book text or notes."""
import json
import sys
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET
sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'Engine'), str(Path(__file__).resolve().parents[1] / 'Engine/vendor')]
from leafscribe.documents import discover, prepare, readonly
from beook_export.inspect import inspect_epub

def main():
    result = discover()
    if not result['books']:
        print('SKIP: no downloaded Beook books available')
        return
    book = min(result['books'], key=lambda b: b['chapterCount'])
    with tempfile.TemporaryDirectory(prefix='leafscribe-live-') as folder:
        document = prepare(book, folder)
        assert document['chapters'], 'Extraction returned no readable chapters'
        parsed = 0
        for chapter in document['chapters']:
            ET.parse(chapter['path'])
            parsed += 1
        okay, report = inspect_epub(Path(document['epubPath']), book['reference'], len(document['chapters']))
        assert okay, 'Upstream structural inspection failed; inspect locally without publishing book content'
        with readonly(book['path']) as conn:
            try:
                conn.execute('CREATE TABLE leafscribe_must_not_write (value TEXT)')
            except Exception as exc:
                assert 'readonly' in str(exc).lower()
            else:
                raise AssertionError('Live connection allowed a write')
        print(json.dumps({'result':'PASS', 'discovered_books':len(result['books']), 'parsed_spine_documents':parsed,
                          'epub_bytes':Path(document['epubPath']).stat().st_size, 'read_only_verified':True,
                          'structural_inspection_passed':okay}))
if __name__ == '__main__': main()
