#!/usr/bin/env python3
"""One versioned JSON request on stdin; NDJSON events and one result on stdout."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / 'vendor'))
from leafscribe.documents import discover, import_document, prepare, epub_from_pages, unpack_epub


def emit(value):
    print(json.dumps({'version': 1, **value}, ensure_ascii=False), flush=True)


def main():
    try:
        request = json.loads(sys.stdin.buffer.readline(16 * 1024 * 1024))
        if request.get('version') != 1:
            raise ValueError('Unsupported protocol version')
        action = request.get('action')
        args = request.get('arguments', {})
        emit({'event': 'progress', 'stage': action, 'fraction': 0})
        if action == 'discover':
            result = discover(args.get('root'))
        elif action == 'import':
            result = import_document(args['source'], args['destination'])
        elif action == 'epub_cover':
            info = unpack_epub(Path(args['source']), Path(args['destination']))
            result = {'coverPath': info['coverPath']}
        elif action == 'prepare':
            result = prepare(args['book'], args['workspace'], args.get('annotations', True), args.get('selectedChapters'))
        elif action == 'epub_from_pages':
            result = epub_from_pages(args['pages'], args['title'], args['destination'])
        elif action == 'health':
            result = {'engine': 'LeafScribe', 'protocol': 1, 'upstream': '0.1.27'}
        else:
            raise ValueError('Unknown engine action')
        emit({'event': 'result', 'result': result})
        return 0
    except Exception as exc:
        code = 'INVALID_DOCUMENT' if isinstance(exc, (ValueError, KeyError)) else 'ENGINE_ERROR'
        emit({'event': 'error', 'code': code, 'message': str(exc)})
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
