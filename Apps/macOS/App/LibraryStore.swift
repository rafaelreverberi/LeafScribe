import AppKit
import Foundation
import Observation
import PDFKit
import UniformTypeIdentifiers
import LeafScribeCore

@MainActor @Observable
final class LibraryStore {
    var books: [Book] = []
    var exports: [ExportRecord] = []
    var selection: String?
    var section = "library"
    var search = ""
    var isLoading = false
    var isExporting = false
    var progress: Double = 0
    var status = "Your library, on your Mac."
    var error: String?
    var warnings: [String] = []
    var showExport = false
    var preview: PreparedDocument?
    var previewWorkspace: URL?
    var isPreparing = false
    var exportTask: Task<Void, Never>?
    var preparationTask: Task<Void, Never>?
    let engine: ExtractionEngine
    let dataRoot: URL
    private let exporter = DocumentExporter()
    private var checkedEPUBCovers: Set<String> = []

    init() {
        let fm = FileManager.default
        dataRoot = fm.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("LeafScribe", isDirectory: true)
        let bundled = Bundle.main.bundleURL.appendingPathComponent("Contents/Helpers/leafscribe-engine")
        if fm.isExecutableFile(atPath: bundled.path) {
            engine = ExtractionEngine(executable: bundled)
        } else {
            let sourceRoot = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            let developerPython = ProcessInfo.processInfo.environment["LEAFSCRIBE_PYTHON"] ?? "/usr/bin/python3"
            engine = ExtractionEngine(executable: URL(fileURLWithPath: developerPython), prefix: [sourceRoot.appendingPathComponent("Engine/main.py").path])
        }
        do {
            try fm.createDirectory(at: dataRoot, withIntermediateDirectories: true)
            if fm.fileExists(atPath: libraryFile.path) { books = try LibraryPersistence.read([Book].self, from: libraryFile) }
            if fm.fileExists(atPath: historyFile.path) { exports = try LibraryPersistence.read([ExportRecord].self, from: historyFile) }
        } catch { self.error = "The saved library could not be loaded: \(error.localizedDescription)" }
    }
    var libraryFile: URL { dataRoot.appendingPathComponent("library.json") }
    var historyFile: URL { dataRoot.appendingPathComponent("exports.json") }
    var selected: Book? { books.first { $0.id == selection } }
    var filteredBooks: [Book] {
        books.filter { book in
            (section != "beook" || book.isBeook) && (section != "imports" || !book.isBeook) &&
            (search.isEmpty || "\(book.title) \(book.author) \(book.reference ?? "")".localizedCaseInsensitiveContains(search))
        }.sorted { $0.title.localizedStandardCompare($1.title) == .orderedAscending }
    }
    func saveLibrary() {
        do { try LibraryPersistence.write(books, to: libraryFile) } catch { self.error = error.localizedDescription }
    }
    func refresh() async {
        guard !isLoading else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            let custom = UserDefaults.standard.string(forKey: "beookRoot")
            let result = try await engine.discover(root: custom?.isEmpty == false ? custom : nil)
            books = books.filter { !$0.isBeook } + result.books
            warnings = result.warnings
            for index in books.indices where books[index].kind == "epub" {
                let book = books[index]
                guard book.coverPath.map({ FileManager.default.fileExists(atPath: $0) }) != true,
                      checkedEPUBCovers.insert(book.id).inserted else { continue }
                let cache = dataRoot.appendingPathComponent("Covers").appendingPathComponent(book.id)
                if let path = try? await engine.epubCover(URL(fileURLWithPath: book.path), into: cache),
                   let current = books.firstIndex(where: { $0.id == book.id }) {
                    books[current].coverPath = path
                }
            }
            saveLibrary()
            status = result.books.isEmpty ? "Ready to import your documents" : "\(result.books.count) Beook books found · Local access only"
        } catch { self.error = error.localizedDescription; status = "Discovery could not finish" }
    }
    func chooseImports() {
        let panel = NSOpenPanel()
        // Apple Books packages use com.apple.ibooks.epub, which does not conform to UTType.epub.
        panel.allowedContentTypes = [.pdf, .epub, .init(filenameExtension: "epub")!, .html,
                                     .init(filenameExtension: "xhtml")!, .package, .folder]
        panel.allowsMultipleSelection = true
        panel.canChooseFiles = true
        panel.canChooseDirectories = true
        panel.treatsFilePackagesAsDirectories = false
        panel.message = "Import EPUB files or .epub folders, HTML, or PDF. LeafScribe keeps a local copy."
        panel.begin { response in
            guard response == .OK else { return }
            Task { @MainActor in await self.importURLs(panel.urls) }
        }
    }
    func importURLs(_ urls: [URL]) async {
        guard !isLoading else { return }
        isLoading = true
        defer { isLoading = false }
        var failures: [String] = []
        for url in urls {
            do {
                var book = try await engine.importDocument(url, into: dataRoot.appendingPathComponent("Documents"))
                if book.kind == "pdf" {
                    guard let document = PDFDocument(url: URL(fileURLWithPath: book.path)), !document.isLocked else {
                        try? FileManager.default.removeItem(at: URL(fileURLWithPath: book.path).deletingLastPathComponent())
                        throw LeafError.message("The PDF is locked or unreadable.")
                    }
                    book.chapterCount = document.pageCount
                    if let title = document.documentAttributes?[PDFDocumentAttribute.titleAttribute] as? String, !title.isEmpty { book.title = title }
                    book.author = document.documentAttributes?[PDFDocumentAttribute.authorAttribute] as? String ?? ""
                    book.annotationCount = (0..<document.pageCount).reduce(0) { $0 + (document.page(at: $1)?.annotations.count ?? 0) }
                }
                books.append(book); selection = book.id; section = "library"
                status = "Imported \(book.title)"
                saveLibrary()
            } catch { failures.append("\(url.lastPathComponent): \(error.localizedDescription)") }
        }
        if !failures.isEmpty { error = failures.joined(separator: "\n\n") }
    }
    func openSample() {
        let sample = Bundle.module.url(forResource: "Welcome", withExtension: "html", subdirectory: "Resources")!
        Task { await importURLs([sample]) }
    }
    func remove(_ book: Book) {
        guard !book.isBeook else { return }
        books.removeAll { $0.id == book.id }
        if selection == book.id { selection = nil }
        saveLibrary()
        // Remove only managed copies. Original files are never removed.
        let url = URL(fileURLWithPath: book.path).deletingLastPathComponent()
        let managed = dataRoot.appendingPathComponent("Documents").standardizedFileURL.path + "/"
        if url.standardizedFileURL.path.hasPrefix(managed) { try? FileManager.default.removeItem(at: url) }
    }
    func preparePreview() {
        guard let book = selected, !isPreparing else { return }
        isPreparing = true
        preparationTask = Task {
            let workspace = Self.workspace()
            do {
                let document = try await engine.prepare(book, workspace: workspace, annotations: true)
                try Task.checkCancellation()
                previewWorkspace = workspace; preview = document
            } catch is CancellationError { try? FileManager.default.removeItem(at: workspace) }
            catch { try? FileManager.default.removeItem(at: workspace); self.error = error.localizedDescription }
            isPreparing = false
        }
    }
    func closePreview() {
        preview = nil
        if let url = previewWorkspace { try? FileManager.default.removeItem(at: url) }
        previewWorkspace = nil
    }
    func startExport(_ options: ExportOptions) {
        guard let book = selected, !isExporting else { return }
        let panel = NSSavePanel()
        panel.allowedContentTypes = options.format == .pdf ? [.pdf] : [.epub]
        panel.nameFieldStringValue = book.title.replacingOccurrences(of: "/", with: "-") + "." + options.format.rawValue.lowercased()
        panel.canCreateDirectories = true
        panel.begin { response in
            guard response == .OK, let url = panel.url else { return }
            self.showExport = false
            self.exportTask = Task { @MainActor in await self.performExport(book, options: options, to: url) }
        }
    }
    func performExport(_ book: Book, options: ExportOptions, to destination: URL) async {
        isExporting = true; progress = 0.02; status = "Preparing \(book.title)…"
        let workspace = Self.workspace()
        let staging = destination.deletingLastPathComponent().appendingPathComponent(".leafscribe-\(UUID().uuidString).\(options.format.rawValue.lowercased())")
        defer {
            isExporting = false
            try? FileManager.default.removeItem(at: workspace)
            try? FileManager.default.removeItem(at: staging)
        }
        do {
            let document = try await engine.prepare(book, workspace: workspace, annotations: options.includeAnnotations, selectedChapters: options.selectedChapterIDs)
            try Task.checkCancellation()
            warnings = document.warnings
            progress = 0.15
            if options.format == .pdf {
                try await exporter.exportPDF(document, title: book.title, options: options, to: staging) { value, label in
                    self.progress = 0.15 + value * 0.8; self.status = label
                }
            } else if let path = document.epubPath {
                try FileManager.default.copyItem(atPath: path, toPath: staging.path)
            } else if let pdf = document.pdfPath {
                status = options.ocr ? "Recognizing text on scanned pages…" : "Extracting PDF text…"
                let pages = try await DocumentExporter.extractPDFText(URL(fileURLWithPath: pdf), ocr: options.ocr)
                try await engine.epub(pages: pages, title: book.title, to: staging)
            } else { throw LeafError.message("No exportable content found.") }
            try Task.checkCancellation()
            if FileManager.default.fileExists(atPath: destination.path) {
                _ = try FileManager.default.replaceItemAt(destination, withItemAt: staging)
            } else { try FileManager.default.moveItem(at: staging, to: destination) }
            exports.insert(ExportRecord(title: book.title, path: destination.path, format: options.format.rawValue), at: 0)
            try LibraryPersistence.write(exports, to: historyFile)
            progress = 1; status = "Exported \(destination.lastPathComponent)"
        } catch is CancellationError { status = "Export cancelled" }
        catch { self.error = error.localizedDescription; status = "Export could not finish" }
    }
    static func workspace() -> URL { FileManager.default.temporaryDirectory.appendingPathComponent("leafscribe-job-\(UUID().uuidString)", isDirectory: true) }
}
