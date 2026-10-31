# LeafScribe user guide

**Your books. Your notes. Your format.**

LeafScribe is a native, local-first macOS document export studio. Browse downloaded Beook books or import authorized DRM-free EPUB, HTML, and PDF documents, preview their content, and export PDF or EPUB.

This is a functional 0.2.0 implementation, with a Swift 6 / SwiftUI interface, a bundled Python extraction helper, WebKit pagination, PDFKit preview and assembly, and optional Vision OCR for scanned PDF-to-EPUB conversion. macOS 14+ is the deployment target. The packaged application does **not** require a separately installed Python.

## Run the application

Open `dist/LeafScribe.app` after building, or move it to Applications. The local build is ad-hoc signed and is not notarized. The current build targets Apple Silicon. Intel requires a separate build on an Intel Mac with a matching Python runtime; no Intel or macOS 14 hardware validation has been performed.

1. Launch LeafScribe. Downloaded Beook profiles are discovered automatically.
2. Use **Import books** (⌘O), or drag in EPUB, HTML, or PDF files. EPUB imports accept both ZIP archives and unpacked `.epub` folder packages. Imports are copied into a managed local library; source documents are never changed.
3. Select a book for metadata and **Preview content**. The preview has chapter navigation for HTML/EPUB and native PDFKit controls for PDFs.
4. Choose **Export book** (⌘E), select PDF or EPUB, and choose a destination. The scrollable export settings offer A4 or US Letter, reading text size, PDF page numbers, and a chapter/section selection. Main chapter checkboxes select all their sections. Beook annotations can be included inline where supported and in an appendix.
5. Open completed files or reveal them in Finder from **Export history**.

Library covers preserve their aspect ratio inside the portrait frame, with a subtle image backdrop for wide covers. Settings → General → Cover display can instead stretch images to fill the frame; the choice is saved and applies immediately throughout the library. EPUB covers are detected from EPUB 3 properties, EPUB 2 metadata and local guide cover pages; existing EPUB imports are updated on refresh. Books without a usable cover keep the generated book design.

The empty library includes an original sample book to exercise the complete workflow. Settings contains a custom Beook data path and **About → Open Source Licenses**.

## Build

Requires Xcode / Command Line Tools with Swift 6, a Python 3.12 build interpreter, and [uv](https://docs.astral.sh/uv/). These are developer tools only.

```sh
Scripts/build_app.sh
open dist/LeafScribe.app
```

The script freezes the Python helper with pinned PyInstaller 6.16.0 and hook collection 2026.9, builds the Swift app, generates its icon, assembles resources and licenses, and ad-hoc signs and verifies the app. It produces a self-contained helper executable under `Contents/Helpers/leafscribe-engine`. It never installs anything in Applications or uploads documents.

`CONFIGURATION=debug Scripts/build_app.sh` produces a faster development build. `PYTHON_FOR_BUILD=/path/to/python3.12` selects the interpreter when creating the build environment. A distributor must use the appropriate runtime architecture, update runtime notices if changing Python, and apply Developer ID signing / notarization for public distribution.

For Swift-only iteration:

```sh
swift build
LEAFSCRIBE_PYTHON=/path/to/python3.12 swift run LeafScribe
```

Open `Package.swift` in Xcode for native debugging. No external Swift packages are required.

## Verification

```sh
python3 -m unittest discover -s Tests/Engine -v
python3 Scripts/test_upstream.py
swift test
Scripts/verify_bundle.sh
```

The bundle verification runs the **actual packaged app** with a restricted PATH. It exercises bundled helper execution, HTML import, multi-page searchable PDF output, table text, PDF outline navigation, EPUB round-trip, PDF-to-EPUB, byte-identical annotated PDF preservation, and Vision OCR against an image-only PDF. Verification artifacts use original sample content only and stay in ignored `output/`.

Optional live Beook adapter check (reads local books, produces only aggregate output, removes temporary exports):

```sh
python3 Scripts/verify_live.py
```

See [validation](VALIDATION.md) for measured results and upstream live-test boundaries.

## Export behavior and limits

| Source | PDF | EPUB |
| --- | --- | --- |
| Beook | Complete fixed-page canvases or paginated reading layout, selectable text, PDF page numbers, chapter outline; optional highlights and annotation appendix | Upstream EPUB 3 writer with EPUB 2 navigation fallbacks and optional annotation appendix |
| EPUB | Spine in reading order, rendered section by section | Entire book: original package copied unchanged. Chapter selection: EPUB 3 repackaged with the selected spine and required resources |
| HTML | Paginated local document, text, tables and bundled assets | XHTML-normalized document and referenced local resources, packaged with upstream writer |
| PDF | Byte-identical copy, preserving native annotations and text | Page-by-page extracted text; optional Vision OCR for pages without a text layer |

- Unpacked `.epub` folder packages are copied into a standard EPUB archive in the managed library; source files remain unchanged. Package symlinks, special files, missing resources and oversized packages are rejected.
- Encrypted EPUB resources are rejected, including font obfuscation. There is no DRM removal or authentication bypass.
- HTML import uses UTF-8 and imports one entry document; linked resources must exist under its containing folder. Missing assets or paths outside that folder produce an error. Images, CSS, fonts, SVG, and linked local files are copied; HTML does not define a book-wide reading order for other linked pages.
- Preview and conversion disable source JavaScript, use a nonpersistent WebKit store, and block HTTP(S) requests. Remote assets are unavailable; download/localize them before importing for complete offline rendering. Source EPUBs copied to EPUB retain their original remote references.
- Interactive Beook exercises remain static. Upstream fixed-layout and pdf2htmlEX reconstruction is heuristic and may differ from print. Complex tables, CSS, fonts, and very large content need visual review. Images of text stay images unless converting a PDF to EPUB with OCR.
- Beook reading layout uses system text fonts and disables problematic ligatures so copied text stays correct. Fixed POS pages keep their positioned source fonts and are vector-captured as complete canvases, scaled to fit the chosen paper. Original page labels are shown separately from optional PDF page numbers. Generic imported HTML/EPUB source styles can override the base PDF font size. The PDF is searchable when the source has a text layer; there is no claim of PDF/A or tagged-PDF accessibility conformance.
- PDF-to-EPUB is text-first. It does not reconstruct images, columns, original layout, or PDF annotations. A page without recoverable text fails the export instead of silently producing a partial book. OCR accuracy should be reviewed.
- Beook annotations are read by the LeafScribe adapter, including entries without stored text. Stored DOM ranges are resolved on the original resource before layout conversion (including UTF-16 offsets and ranges across inline elements). Highlights retain their Beook color and opacity. Text notes appear after their anchored paragraph or container with their stored width, color and paragraph spacing; note height grows to avoid clipping. Stale ranges use a unique normalized quote when available; unresolved entries remain in the appendix with their location metadata. Empty cursor ranges do not create invented highlights. These are rendered marks, not editable native PDF highlight rectangles. Imported EPUB annotation counts are not indexed; EPUB-to-EPUB retains the source package.
- Export cancellation interrupts extraction and is checked between rendered sections; a print already in progress finishes its current section before cancellation takes effect. No partial destination is committed. Exporting is one book at a time; chapter/section selections are supported for Beook and EPUB, while batch queues and annotation editing are not implemented.
- Archive import is limited to 1 GB uncompressed / 30,000 members. Symlinks, duplicate archive names, escaping paths, and invalid or missing spine resources are rejected. The JSON request limit is 16 MB.
- Library data lives in `~/Library/Application Support/LeafScribe`. Remove an imported book to delete its managed copy; the original file is kept. Export history does not move exported files. Library persistence failures are surfaced; automatic recovery and background folder watching are not implemented.

## Architecture and Windows path

See [architecture](ARCHITECTURE.md) and [protocol](PROTOCOL.md). `Core/` contains Codable document models, persistence, and a cancellable subprocess client. `Apps/macOS/` owns SwiftUI and Apple rendering frameworks. `Engine/leafscribe/` adapts source documents; `Engine/vendor/beook_export/` is the preserved upstream core.

The versioned JSON contract and document model are portable. A future WinUI 3 frontend can use the same Python helper, replace platform PDF rendering/OCR, and migrate backend operations incrementally to Rust. This repository does not yet implement a Windows frontend or runtime.

## Attribution and license

LeafScribe’s independently authored code is licensed for private, non-commercial use under the [Personal Use License](../LICENSE). The upstream components retain their MIT license. Its Beook discovery, extraction, resource reconstruction, annotations, and EPUB writer reuse **[beook-export by JoGe07](https://github.com/JoGe07/beook-export)**, Copyright (c) 2026 JoGe07. These modules are vendored unchanged at the revision in `Engine/vendor/UPSTREAM_REVISION`; the adapter and native app are independently implemented. This extraction implementation was not written from scratch by LeafScribe.

Full upstream MIT terms are preserved, with precise reuse details and bundled runtime notices in [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md), [Licenses/](../Licenses/), and the app’s About settings.

LeafScribe is independent and unaffiliated with Beook, its publisher, or its developers. Export only content you are authorized to use. Book content and annotations remain under their original rights; a software license does not grant redistribution rights.
