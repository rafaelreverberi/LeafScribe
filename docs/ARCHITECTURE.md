# Architecture

LeafScribe separates source extraction from native rendering:

```
SwiftUI library / details / export / preview / settings
    ↓ observable LibraryStore, async tasks, atomic JSON persistence
LeafScribeCore Book / PreparedDocument / ExtractionEngine actor
    ↓ versioned JSON over pipes, fixed subprocess executable
Frozen Python helper
    ├── independently implemented import / validation / adapter
    └── unchanged beook-export core, pinned revision
    ↓ ordered chapters + local assets + EPUB package
WebKit fixed-canvas capture / async print → PDFKit assembly / outlines / metadata / page numbers
PDFKit text extraction + optional Vision OCR → Python EPUB writer
```

LibraryStore is main-actor isolated. The extraction actor uses a worker queue for blocking subprocess I/O and a lock-protected process handle for cancellation. It drains stdout before waiting so extraction cannot deadlock on a full output pipe. stderr is discarded rather than leaking book content into user-facing logs. Swift owns operation status and errors.

WebKit uses ephemeral data storage, content JavaScript disabled, scoped file access, and network block rules. Reflow sections use a dedicated print stylesheet instead of upstream's EPUB screen stylesheet, wait for fonts/images, and print asynchronously with NSPrintOperation so WebKit's page-count IPC has a running run loop. POS fixed-layout sections are measured in the DOM, vector-captured with createPDF, and scaled into one paper page using Core Graphics. This avoids chopping an oversized positioned page into printable tiles. Reading text uses system fonts with ligatures disabled to avoid malformed publisher-font Unicode mappings. PDFKit then assembles pages and adds section outlines. Source HTML and assets remain in a private temporary workspace. Local copies of source PDFs are exported byte-for-byte.

Exports use a unique sibling staging file. Only a successfully completed operation moves/replaces the chosen destination; errors and cancellation remove staging. Imports copy into UUID folders, roll back failed operations, and persist metadata with atomic JSON writes. The user's original files and Beook database are never edited.

The initial package is outside the App Sandbox to access Beook's existing filesystem layout and launch its helper. A public release needs Developer ID signing/notarization; a sandboxed release would need security-scoped bookmarks and a deliberate permissions design. No credentials, analytics, cloud service, account, or updater is included.

## Portable backend and gradual Rust migration

Protocol v1 carries simple strings, numeric counts, document identity and chapter paths. It does not expose SwiftUI, Core Data APIs, or WebKit objects. Windows can package the same Python stdlib core and supply a WinUI 3 library, WebView2-based print service, PDF viewer, and Windows OCR adapter. Source-specific Beook discovery paths would require actual Windows installation research.

A Rust helper can first replace archive validation, library import, and resource staging while preserving messages. Mature Python reconstruction can remain a subprocess or embedded module until conformance fixtures establish parity. Protocol fixtures and source-to-output text/image counts should gate each migration. macOS rendering remains behind DocumentExporter; only its API/results need a Windows equivalent.

The adapter supplies stable section IDs, source page labels, and chapter group IDs/titles. Chapter selection filters both the reading order and the EPUB content files. Annotations are fetched without dropping empty-text entries, routed to the selected sections, and preserved in an appendix. The read-only AnnotationSource view adapts the returned bytes of the pinned upstream resource query, resolving all DOM ranges and note anchors against the original XML before any insertion. Text boundaries count JavaScript UTF-16 units; element boundaries count DOM child nodes. Existing quotes validate ranges and uniquely matched quotes recover stale ranges. Marks retain color/opacity and notes retain width/color/paragraph spacing at their paragraph anchors. The upstream transformations then carry these elements through rewriting and consolidation. Surviving annotation IDs route chapter selections without guessing another page; unresolved entries remain in the appendix. The database and vendored modules stay unchanged. The export sheet has a single scrollable content area and fixed destination/cancel actions.
