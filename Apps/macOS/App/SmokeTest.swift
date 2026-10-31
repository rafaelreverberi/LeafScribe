import AppKit
import Foundation
import PDFKit
import LeafScribeCore

@MainActor
enum SmokeTest {
    static func renderPrepared(input: String, output: String) async {
        do {
            let document = try JSONDecoder().decode(PreparedDocument.self, from: Data(contentsOf: URL(fileURLWithPath: input)))
            let renderer = DocumentExporter()
            try await renderer.exportPDF(document, title: document.title ?? "LeafScribe layout verification", options: ExportOptions(), to: URL(fileURLWithPath: output)) { value, _ in
                if Int(value * 100) % 20 == 0 { print("Rendering: \(Int(value * 100))%") }
            }
            print("PASS: rendered \(document.chapters.count) sections")
            NSApp.terminate(nil)
        } catch { print("FAIL: \(error.localizedDescription)"); exit(1) }
    }
    private static func layoutRegression(engine: ExtractionEngine, folder: URL) async throws {
        let root = folder.appendingPathComponent("layout-fixtures")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let fixed = root.appendingPathComponent("fixed.html")
        let html = """
        <!DOCTYPE html><html><head><title>Fixed regression</title></head><body class="beook-export-pos-fixed">
        <div class="beook-export-pos-fixed-outer" style="width:780.0px;height:1100.0px;">
        <div class="beook-export-pos-fixed-page" style="position:relative;width:2000px;height:2820px;transform:scale(.39);transform-origin:top left;font-size:32px;">
        <span style="position:absolute;left:100px;top:100px;">LEFT_COLUMN</span>
        <span style="position:absolute;left:1890px;top:100px;white-space:nowrap;">RIGHT_EDGE_COMPLETE</span>
        <span style="position:absolute;left:100px;top:2700px;">BOTTOM_EDGE_COMPLETE</span>
        </div></div></body></html>
        """
        try html.write(to: fixed, atomically: true, encoding: .utf8)
        let book = try await engine.importDocument(fixed, into: folder.appendingPathComponent("imports"))
        let prepared = try await engine.prepare(book, workspace: folder.appendingPathComponent("leafscribe-job-fixed-regression"), annotations: false)
        let output = folder.appendingPathComponent("fixed-regression.pdf")
        let renderer = DocumentExporter()
        try await renderer.exportPDF(prepared, title: "Fixed regression", options: ExportOptions(), to: output) { _, _ in }
        guard let pdf = PDFDocument(url: output), pdf.pageCount == 1,
              pdf.string?.contains("RIGHT_EDGE_COMPLETE") == true,
              pdf.string?.contains("BOTTOM_EDGE_COMPLETE") == true else {
            throw LeafError.message("Fixed-layout regression: a complete canvas must fit one PDF page.")
        }
        guard let page = pdf.page(at: 0), let selection = pdf.findString("RIGHT_EDGE_COMPLETE", withOptions: []).first,
              page.bounds(for: .mediaBox).insetBy(dx: 20, dy: 20).contains(selection.bounds(for: page)) else {
            throw LeafError.message("Fixed-layout regression: rightmost text was clipped.")
        }
        let table = root.appendingPathComponent("table.html")
        let rows = (0..<60).map { "<tr><td>Row \($0)</td><td>Text crossing the source width</td><td>Another column</td><td>\($0 == 59 ? "LASTROWEND" : "Cell value")</td></tr>" }.joined()
        try ("<html><head><title>Table regression</title><style>div{height:100px;overflow:hidden}table{width:1800px}td{height:90px}.pageInfo{position:fixed;bottom:0}</style></head><body><span class=\"pageInfo\" data-page-label=\"77\"></span><div><table>" + rows + "</table></div></body></html>").write(to: table, atomically: true, encoding: .utf8)
        let imported = try await engine.importDocument(table, into: folder.appendingPathComponent("imports"))
        var flow = try await engine.prepare(imported, workspace: folder.appendingPathComponent("leafscribe-job-table-regression"), annotations: false)
        flow.chapters[0].layout = "beook-flow"
        let tableOutput = folder.appendingPathComponent("table-regression.pdf")
        try await renderer.exportPDF(flow, title: "Table regression", options: ExportOptions(), to: tableOutput) { _, _ in }
        guard let paginated = PDFDocument(url: tableOutput), paginated.pageCount > 1,
              (paginated.string ?? "").filter { !$0.isWhitespace }.contains("LASTROWEND"),
              paginated.string?.contains("Originalseite 77") == true else {
            throw LeafError.message("Reflow regression: table content or source page label was lost.")
        }
    }
    static func run(in folder: URL) async {
        do {
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            let helper = Bundle.main.bundleURL.appendingPathComponent("Contents/Helpers/leafscribe-engine")
            guard FileManager.default.isExecutableFile(atPath: helper.path) else { throw LeafError.message("Bundled helper missing") }
            let engine = ExtractionEngine(executable: helper)
            let sample = Bundle.module.url(forResource: "Welcome", withExtension: "html", subdirectory: "Resources")!
            let book = try await engine.importDocument(sample, into: folder.appendingPathComponent("imports"))
            let workspace = folder.appendingPathComponent("leafscribe-job-smoke")
            let document = try await engine.prepare(book, workspace: workspace, annotations: true)
            let renderer = DocumentExporter()
            let pdfURL = folder.appendingPathComponent("sample.pdf")
            try await renderer.exportPDF(document, title: book.title, options: ExportOptions(), to: pdfURL) { _, _ in }
            guard let pdf = PDFDocument(url: pdfURL), pdf.pageCount >= 2,
                  pdf.string?.contains("Make room to return") == true,
                  pdf.string?.contains("An unfamiliar idea") == true,
                  pdf.outlineRoot?.numberOfChildren == 1 else {
                throw LeafError.message("PDF failed pagination, searchable text, table, or outline verification")
            }
            let epubURL = folder.appendingPathComponent("sample.epub")
            try FileManager.default.copyItem(atPath: document.epubPath!, toPath: epubURL.path)
            let importedEPUB = try await engine.importDocument(epubURL, into: folder.appendingPathComponent("imports"))
            let roundtrip = try await engine.prepare(importedEPUB, workspace: folder.appendingPathComponent("leafscribe-job-roundtrip"), annotations: true)
            guard !roundtrip.chapters.isEmpty else { throw LeafError.message("EPUB round-trip lost its spine") }
            try await layoutRegression(engine: engine, folder: folder)
            let pages = try await DocumentExporter.extractPDFText(pdfURL, ocr: false)
            let converted = folder.appendingPathComponent("pdf-to-epub.epub")
            try await engine.epub(pages: pages, title: book.title, to: converted)
            // A plain PDF copy must keep native annotations and original bytes.
            let annotation = PDFAnnotation(bounds: CGRect(x: 50, y: 50, width: 150, height: 25), forType: .text, withProperties: nil)
            annotation.contents = "Smoke test note"
            pdf.page(at: 0)?.addAnnotation(annotation)
            let annotated = folder.appendingPathComponent("annotated.pdf")
            guard pdf.write(to: annotated) else { throw LeafError.message("Cannot write annotated fixture") }
            let importedPDF = try await engine.importDocument(annotated, into: folder.appendingPathComponent("imports"))
            let pdfPrepared = try await engine.prepare(importedPDF, workspace: folder.appendingPathComponent("leafscribe-job-pdf"), annotations: true)
            let preserved = folder.appendingPathComponent("preserved.pdf")
            try await renderer.exportPDF(pdfPrepared, title: book.title, options: ExportOptions(), to: preserved) { _, _ in }
            guard try Data(contentsOf: annotated) == Data(contentsOf: preserved) else { throw LeafError.message("PDF preservation changed source bytes") }
            // Exercise Vision against an image-only PDF, not existing text.
            let raster = NSImage(size: NSSize(width: 1000, height: 1400))
            raster.lockFocus()
            NSColor.white.setFill(); NSRect(x: 0, y: 0, width: 1000, height: 1400).fill()
            ("LeafScribe OCR verification" as NSString).draw(at: NSPoint(x: 70, y: 1160), withAttributes: [.font: NSFont.systemFont(ofSize: 44), .foregroundColor: NSColor.black])
            ("Searchable study notes on a scanned page." as NSString).draw(at: NSPoint(x: 70, y: 1060), withAttributes: [.font: NSFont.systemFont(ofSize: 30), .foregroundColor: NSColor.black])
            raster.unlockFocus()
            let scanned = PDFDocument(); scanned.insert(PDFPage(image: raster)!, at: 0)
            let scanURL = folder.appendingPathComponent("scanned.pdf")
            guard scanned.write(to: scanURL) else { throw LeafError.message("Could not create OCR fixture") }
            let recognized = try await DocumentExporter.extractPDFText(scanURL, ocr: true)
            guard recognized.first?.html.contains("OCR verification") == true else { throw LeafError.message("Vision OCR did not recover the scanned text") }
            if CommandLine.arguments.contains("--live-beook") {
                let found = try await engine.discover()
                guard let installed = found.books.min(by: { $0.chapterCount < $1.chapterCount }) else { throw LeafError.message("No Beook fixture available") }
                let live = try await engine.prepare(installed, workspace: folder.appendingPathComponent("leafscribe-job-live"), annotations: true)
                let liveURL = folder.appendingPathComponent("live-beook.pdf")
                try await renderer.exportPDF(live, title: installed.title, options: ExportOptions(), to: liveURL) { _, _ in }
                guard let verified = PDFDocument(url: liveURL), verified.pageCount >= live.chapters.count,
                      verified.outlineRoot?.numberOfChildren == live.chapters.count,
                      (verified.string?.count ?? 0) > 1000 else { throw LeafError.message("Live PDF failed page, outline, or text checks") }
                try "PASS: \(live.chapters.count) source sections, \(verified.pageCount) PDF pages, selectable text and complete section outline.\n".write(to: folder.appendingPathComponent("live-result.txt"), atomically: true, encoding: .utf8)
            }
            let summary = "PASS: bundled Python helper, HTML import, paginated searchable PDF (\(pdf.pageCount) pages), table text, PDF outline, EPUB import/export, PDF-to-EPUB, byte-identical PDF annotation preservation, Vision OCR of an image-only PDF, unclipped fixed canvas, complete paginated wide table.\n"
            try summary.write(to: folder.appendingPathComponent("result.txt"), atomically: true, encoding: .utf8)
            print(summary)
            NSApp.terminate(nil)
        } catch {
            let message = "FAIL: \(error.localizedDescription)\n"
            try? message.write(to: folder.appendingPathComponent("result.txt"), atomically: true, encoding: .utf8)
            print(message)
            exit(1)
        }
    }
}
