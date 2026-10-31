# Extraction protocol v1

One UTF-8 JSON request, followed by a newline, is written to stdin of a fresh helper process. Requests use fixed executable arguments; filenames are only JSON data, never shell code. The helper exits after one request. NDJSON events go to stdout. Human diagnostics are not used as a protocol. No remote API is involved.

```json
{"version":1,"action":"prepare","arguments":{"book":{"id":"…","title":"…","author":"…","kind":"epub","path":"/absolute/book.epub","chapterCount":1,"annotationCount":0,"language":"en"},"workspace":"/private/tmp/leafscribe-job-UUID","annotations":true}}
```

Events:

```json
{"version":1,"event":"progress","stage":"prepare","fraction":0}
{"version":1,"event":"result","result":{"chapters":[{"id":"0","title":"Chapter","path":"/absolute/chapter.xhtml"}],"epubPath":"/absolute/book.epub","warnings":[]}}
{"version":1,"event":"error","code":"INVALID_DOCUMENT","message":"…"}
```

Exactly one terminal result or error follows progress. Successful processes return zero; errors return one. `INVALID_DOCUMENT` covers invalid arguments/documents/version/action; `ENGINE_ERROR` covers filesystem, archive/parser, SQLite and other runtime failures. The Swift client validates the version and surfaces error messages. It currently reads progress events at process completion; native progress reports extraction start and actual per-section rendering progress, not fabricated per-resource percentages.

Actions:

- `health`: no arguments; reports engine/protocol/upstream versions.
- `discover`: optional `root` Beook data folder. Returns `books` and profile `warnings`. No profiles is a normal empty result.
- `import`: `source` and managed library `destination` folder. Returns a Book. The app persists metadata, inspects PDFs with PDFKit, and owns removal of managed imports.
- `epub_cover`: `source` managed EPUB archive and `destination` private cache folder. Returns optional `coverPath`, resolving EPUB 3 cover properties, EPUB 2 metadata and local guide cover pages. Used to update existing imports without reimporting them.
- `prepare`: `book`, unique `workspace`, and `annotations` (default true), plus optional `selectedChapters` section IDs (null/omitted means the whole book; an empty array is an error). Returns ordered `chapters`, optional `epubPath` or `pdfPath`, and warnings. Paths are local absolute paths. `annotations` controls both Beook inline annotations and the annotation appendix.
- `epub_from_pages`: ordered `{title,html}` `pages`, document `title`, and staged `destination`. Packages native PDF/OCR extraction as an EPUB. Returns `path` and `chapterCount`.

Book fields: `id`, `title`, `author`, `kind` (`beook|epub|html|pdf`), `path`, `chapterCount`, `annotationCount`, `language`; optional `reference`, `profile`, `root`, `coverPath`. Beook identity includes profile and reference. Native imports use UUIDs. Unknown JSON fields are ignored for forward compatibility. v1 limits requests to 16 MB.

The helper is trusted local code; the app supplies workspaces and library paths. This is a subprocess contract, not a network service or multi-user access-control boundary. Source data is untrusted: archive paths and sizes are validated; Beook DBs are opened read-only; WebKit disables content scripts and blocks remote loads. Swift handles task cancellation by terminating the helper. Workspaces and staged exports are cleaned on completion/failure/cancellation. An app crash can leave temporary jobs, which macOS eventually clears; no crash-recovery queue is implemented.

Additive v1 result fields: `title`, `annotationCount`, `inlineAnnotationCount`. Chapters can include `layout` (`fixed`, `beook-flow`, or `flow`), `sourceLabel`, `groupID`, `groupTitle`, and `isAnnotation`. Section IDs exclude the automatically generated annotation appendix from user selections. Group IDs let frontends select a main chapter and all its sections together. A selected EPUB is repackaged; whole imported EPUBs still round-trip unchanged.
