## Cover sizing preference — 2026-10-31

- Settings → General → Cover display offers proportional fit (the existing default) and stretch to fill. The latter changes image proportions to cover the full frame. The shared AppStorage preference applies immediately to library/inspector covers and persists across launches; generated placeholders stay unchanged.
- The release app was rebuilt and both Swift tests passed. Both choices were exercised in the native settings window; stretched book covers were visually checked in the library, then proportional fit was restored.

## Library cover presentation — 2026-10-31

- Cover images use explicit container bounds and proportional fit with high-quality interpolation; wide covers have a softly blurred backdrop while their complete foreground remains visible. The portrait frame and generated fallback design stay intact.
- EPUB cover resolution supports EPUB 3 `cover-image`, EPUB 2 metadata IDs and local guide cover pages with HTML/SVG image references. Missing or remote/escaping guide images retain the fallback. Existing EPUB imports missing cover metadata are refreshed through the JSON v1 `epub_cover` action into the private cache, without requiring reimport.
- 33 engine tests and 2 Swift tests pass; 68 isolated upstream tests pass with 13 skipped. The bundled app's PDF/EPUB/OCR/layout verification passes. The existing imported EPUB's cover was recovered and displayed in the native library; its source package was not changed.

## Unpacked EPUB import — 2026-10-31

- EPUB imports accept ZIP files and `.epub` directory packages. The latter are packaged into a managed archive with uncompressed `mimetype` first; the existing manifest/spine/encryption validation applies to both forms. Package traversal rejects symlinks, special files, escaping names and oversized trees. Failed imports remove their managed directory.
- 31 engine tests pass, including package immutability, source removal after import, archive round-trip, missing resources, symlinks, size limits and nested destinations. The two Swift tests and isolated upstream suite also pass (68 executed, 13 skipped).
- The rebuilt self-contained app successfully imported the user's unpacked EPUB through JSON v1 and rendered all 30 spine sections to an 86-page PDF. All 1,130 source text blocks were recovered within their respective PDF sections after normalization; all 204,001 measured glyph bounds lie within the paper. Representative cover/title/body/final pages were rendered and visually reviewed. All 40 original package files retained their SHA-256 hashes.
- `Scripts/verify_bundle.sh` passed the packaged helper, PDF/EPUB, OCR and layout checks. The final native import dialog accepts the Apple Books package type (which does not conform to UTType.epub); importing the user package through the dialog succeeded and persisted it as a 30-section EPUB in the app library. Private book files and generated exports remain ignored.

## Annotation placement repair — 2026-10-31

- 27 engine tests pass, including overlapping ranges, UTF-16 offsets, DOM element/text boundaries, stale Beook wrapper indexes, quote validation, empty ranges, note position/width/colors/paragraph spacing, read-only resource adaptation and selected consolidated pages.
- 2 Swift tests and the isolated upstream suite pass (68 executed, 13 skipped).
- The release bundle was rebuilt and `Scripts/verify_bundle.sh` passed in the graphical session, including the bundled helper, searchable PDF/EPUB workflow, native PDF annotation preservation, OCR and layout fixtures.
- Opt-in inspection of the installed library through the rebuilt helper's JSON v1 interface found 65 text highlights and all 5 text notes rendered inline. Eleven remaining highlight records contain no stored quote: ten have zero-length ranges and one has an unresolvable stored path. They remain in the appendix; the prepared document warns about these entries.
- The screenshot's source section was exported with the rebuilt native renderer and visually checked. Yellow/pink highlights and the yellow note occur at their paragraph anchors, with stored note width and paragraph spacing. PDF pagination follows paper size; this does not establish pixel-identical Beook screen layout or all-publisher compatibility.
- Source databases were opened read-only. Private documents and generated exports remain ignored; no vendor modules were modified.

# Validation — 29 October 2026

## 0.1.1 — corrections after reviewing the user's exports

The original successful traversal/count checks did not establish good layout. The downloaded French and history PDFs were reviewed and the following corrections were implemented and validated:

- Fixed POS pages use a measured vector canvas scaled into one paper page; they are no longer tiled by WebKit printing. Font and image loading completes before capture. The French export now contains 91 pages instead of 155; source corner text and a synthetic overflow fixture remain inside the PDF page. PDF page numbers are separate from database-provided Beook page labels; anchor IDs are not treated as original print page numbers.
- Flowing Beook text uses a dedicated print stylesheet, with normal table pagination, explicit image/credit visibility, static source-page markers, no viewport-height cover offsets, and no EPUB reader box/break rules. The final history export contains 1,277 pages; its digital content remains long, and section selection is available.
- Publisher-font ligature mappings encoded visible `ff`, `fi`, `fl`, and `tt` combinations as incorrect Unicode characters. System reading fonts and disabled ligatures corrected this: all 147 sampled long paragraphs now match the source after whitespace/punctuation normalization (the earlier pass matched 73). This checks those samples, not every word/formula in the book.
- History: all 53 stored annotation records are included, versus 43 accepted by upstream's nonempty-text filter. All 43 stored nonempty annotation texts were recovered from the PDF appendix. 38 unique text highlights were also matched inline, and the available text note is shown with its section. Empty or ambiguous entries keep their appendix/location information. French: zero locally stored annotation records were found; the UI explains this rather than promising to include absent notes.
- Native bundle regression covers a fixed page wider than the viewport, complete right/bottom text capture on one paper page, and a 60-row, overwide, height-clipped source table with its final row and source page marker preserved after pagination.
- 19 LeafScribe Python tests, 68 isolated upstream tests, and 2 Swift tests pass; 13 edition-specific upstream cases remain explicitly skipped. The actual bundled helper, OCR and native PDF/EPUB smoke workflow pass.
- Glyph bounds were checked on nine spread-out pages per book: no sampled text glyph lay outside the physical page. Representative French, history text/image/table, and sample table pages were rendered and inspected. This is a visual sample, not a claim that all 1,277 history pages were manually inspected.
- Partial EPUB tests ensure excluded chapters are removed from the archive, not merely omitted from its spine, and that annotations stay with selected content. The export dialog now scrolls, leaves destination/cancel buttons visible, and offers main chapter and individual section checkboxes. Native UI validation selected a main chapter (35 of 170 sections) and an individual section (1 of 170), then exported the latter through the save dialog. The final 16-page excerpt contains its two attributable annotation entries; notes from a known unselected page do not fall back to a different section. Aggregate verification results are retained in ignored output/verification/export-repair-summary.json; temporary paid-content working copies were removed.

Original downloads were left untouched. Corrected PDFs are in ignored `output/pdf/`; private book content and analysis assets are not committed.

## Initial 0.1.0 validation (historical)

Test host: Apple Silicon Mac, macOS 27.2 (26B123), Apple Swift 6.4. Target: macOS 14.0. Bundled helper: CPython 3.12.8, PyInstaller 6.16.0. These results establish behavior on this host; they do not establish Intel or macOS 14 device compatibility.

## Passed

- `swift build -c release`: compiled the native app using Swift 6 language mode. The final UI build completed without warnings.
- `swift test`: 2 tests passed (Codable library identity/persistence, structured engine failure).
- `python3 -m unittest discover -s Tests/Engine -v`: 12 tests passed. Coverage includes HTML/EPUB round-trip, resource graphs, Unicode, SVG namespace/viewBox, copied source independence, archive traversal, encryption rejection, missing-asset rollback, out-of-folder references, PDF page-to-EPUB packaging, no-profile state, annotation appendix inclusion/exclusion, and versioned errors.
- `python3 Scripts/test_upstream.py`: 68 unchanged upstream tests passed; 13 edition-specific live cases explicitly skipped. The runner still executes malformed-EPUB inspection tests without requiring a local database.
- All 11 vendored Python source modules match upstream revision `198577f47b0d2d0af6f728c70caa9e6e41c247e5` byte-for-byte. The `.pyc` caches are generated locally and ignored.
- `codesign --verify --deep --strict dist/LeafScribe.app`: succeeded for the ad-hoc signed, self-contained release bundle. No Python executable is resolved from PATH in the packaged app.
- Actual release `.app` launched with `PATH=/usr/bin:/bin` and `--smoke-test <local verification folder> --live-beook`: passed bundled-helper execution, managed HTML import, two-page searchable A4 PDF, table text, section-outline entry, EPUB import/export round-trip, PDF-to-EPUB, byte-identical native PDF preservation including annotation, and Vision OCR on an image-only PDF.
- Live Beook adapter: discovered 3 books. A complete installed book produced 91 parseable spine documents and a 6,900,559-byte EPUB. Upstream structural inspection passed. A deliberately attempted write through the read-only connection was rejected.
- Native live PDF pipeline: all 91 source sections rendered to 155 pages, with selectable text and all 91 PDF outline entries. This checks the complete path, but does not prove every formula, image, or layout matches the publisher's print edition.
- Sample PDF pages were rendered to PNG with Poppler and visually inspected. The final layout preserves the vector illustration and keeps the sample table header with its rows. The sample uses original public-domain demonstration content.
- Native UI checks: launch, discovery, Imported empty state, sample import, persisted library after relaunch, book inspector, chapter preview with rendered SVG/table/text, export configuration, native save dialog, sample PDF export, export history entry, and full About/license disclosure. The layout review caught and fixed a visible picker label that enlarged the header, and improved dark-appearance accent contrast.

Verification artifacts are ignored under `output/verification/`. Private live-book artifacts from the native full-book check were removed after verification; its aggregate result was retained. Do not add them to version control. The sample PDF may be shared.

## Original upstream live-suite baseline

Before adapting any code, the upstream author's unmodified full suite ran 81 tests against this machine's library and had 2 errors and 2 failures. Two tests requested a chemistry title not installed here; one series test expected a geography product not present; another expected a hardcoded image resource filename from a different installed edition. LeafScribe has not changed those tests or the upstream extraction implementation to make those assumptions pass. The isolated upstream runner and LeafScribe's aggregate live checks serve separate purposes.

## Boundaries

- No Intel machine, macOS 14 host, Windows frontend, or signed/notarized public release was tested.
- Automated GitHub Actions workflow was added but has not run remotely because this workspace has no configured GitHub remote.
- Beook compatibility varies by publisher/source format. The live PDF check establishes a complete section traversal with searchable output, not pixel fidelity, annotation-coordinate mapping, or a comprehensive visual audit of 155 paid-content pages.
- OCR recovered the synthetic scan's expected phrase; it does not guarantee arbitrary language or scan accuracy.
- The app does not claim PDF/A, tagged PDF, DRM removal, full interactive exercise support, batch exports, or PDF-layout-to-EPUB reconstruction. See README.md for supported conversion behavior.
