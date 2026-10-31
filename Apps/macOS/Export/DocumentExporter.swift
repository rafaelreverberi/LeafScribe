import AppKit
import CoreText
import PDFKit
import Vision
import WebKit
import LeafScribeCore

@MainActor
final class DocumentExporter: NSObject, WKNavigationDelegate {
    private var webView: WKWebView?
    private var navigation: CheckedContinuation<Void, Error>?
    private var loadTimeout: Task<Void, Never>?
    private var permittedRoot: URL?
    private var printing: CheckedContinuation<Void, Error>?
    private var renderWindow: NSWindow?

    func exportPDF(_ document: PreparedDocument, title: String, options: ExportOptions, to destination: URL,
                   progress: @escaping (Double, String) -> Void) async throws {
        if let pdf = document.pdfPath {
            try FileManager.default.copyItem(atPath: pdf, toPath: destination.path)
            return
        }
        let combined = PDFDocument()
        var sourceLabels: [String?] = []
        let outline = PDFOutline()
        outline.label = title
        for (index, chapter) in document.chapters.enumerated() {
            try Task.checkCancellation()
            progress(Double(index) / Double(max(1, document.chapters.count)), "Laying out section \(index + 1) of \(document.chapters.count)")
            let source = URL(fileURLWithPath: chapter.path)
            let printed = source.deletingLastPathComponent().appendingPathComponent(".leafscribe-\(UUID().uuidString).pdf")
            defer { try? FileManager.default.removeItem(at: printed) }
            try await render(source, chapter: chapter, options: options, to: printed)
            guard let fragment = PDFDocument(url: printed), fragment.pageCount > 0 else {
                throw LeafError.message("WebKit produced no pages for \(chapter.title).")
            }
            let firstPage = combined.pageCount
            for pageIndex in 0..<fragment.pageCount {
                if let page = fragment.page(at: pageIndex) { combined.insert(page, at: combined.pageCount); sourceLabels.append(chapter.layout == "fixed" ? chapter.sourceLabel : nil) }
            }
            if let page = combined.page(at: firstPage) {
                let item = PDFOutline()
                item.label = chapter.title
                item.destination = PDFDestination(page: page, at: CGPoint(x: 0, y: page.bounds(for: .mediaBox).height))
                outline.insertChild(item, at: outline.numberOfChildren)
            }
            // Yield between chapters so cancellation and progress remain responsive.
            await Task.yield()
        }
        try Task.checkCancellation()
        if options.addPageNumbers {
            for index in 0..<combined.pageCount {
                guard let page = combined.page(at: index) else { continue }
                let box = page.bounds(for: .mediaBox)
                let footer = PDFAnnotation(bounds: CGRect(x: 44, y: 13, width: box.width - 88, height: 16), forType: .freeText, withProperties: nil)
                footer.contents = "\(index + 1) / \(combined.pageCount)" + (sourceLabels[index].map { " · Beook-Seite \($0)" } ?? "")
                footer.font = NSFont.systemFont(ofSize: 8)
                footer.fontColor = .darkGray; footer.color = .clear; footer.alignment = .center
                let border = PDFBorder(); border.lineWidth = 0; footer.border = border
                page.addAnnotation(footer)
            }
        }
        combined.outlineRoot = outline
        combined.documentAttributes = [PDFDocumentAttribute.titleAttribute: title,
                                       PDFDocumentAttribute.creatorAttribute: "LeafScribe"]
        guard combined.pageCount > 0, combined.write(to: destination) else { throw LeafError.message("Could not save the PDF.") }
        progress(1, "PDF ready")
    }

    private func render(_ source: URL, chapter: Chapter, options: ExportOptions, to output: URL) async throws {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = false
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = false
        // Network resources cannot leave the device during preview or print.
        let rules = """
        [{"trigger":{"url-filter":"^https?://"},"action":{"type":"block"}}]
        """
        let rule = try await WKContentRuleListStore.default().compileContentRuleList(forIdentifier: "LeafScribeOffline", encodedContentRuleList: rules)
        if let rule { configuration.userContentController.add(rule) }
        let paper = options.pageSize == "Letter" ? CGSize(width: 612, height: 792) : CGSize(width: 595.28, height: 841.89)
        let isFixed = chapter.layout == "fixed"
        var html = try String(contentsOf: source, encoding: .utf8)
        var canvas = CGSize(width: (paper.width - 88) * 4 / 3, height: (paper.height - 88) * 4 / 3)
        if isFixed, let expression = try? NSRegularExpression(pattern: #"class="beook-export-pos-fixed-outer"\s+style="width:([0-9.]+)px;height:([0-9.]+)px"#),
           let match = expression.firstMatch(in: html, range: NSRange(html.startIndex..., in: html)),
           let w = Range(match.range(at: 1), in: html), let h = Range(match.range(at: 2), in: html),
           let width = Double(html[w]), let height = Double(html[h]) {
            canvas = CGSize(width: width, height: height)
        }
        let view = WKWebView(frame: NSRect(origin: .zero, size: canvas), configuration: configuration)
        view.navigationDelegate = self
        webView = view
        let window = NSWindow(contentRect: view.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = view
        renderWindow = window
        permittedRoot = source.deletingLastPathComponent()
        var css = ""
        if isFixed {
            css = "html,body {margin:0!important;padding:0!important;width:\(canvas.width)px!important;height:\(canvas.height)px!important;overflow:visible!important;} .beook-export-pos-fixed-outer{margin:0!important;} body.beook-export-pos-fixed *{font-variant-ligatures:none!important;font-feature-settings:\"liga\" 0,\"clig\" 0!important;}"
        } else if chapter.layout == "beook-flow" {
            // The EPUB reader stylesheet is for screens. Its break/box rules must not drive print pagination.
            if let expression = try? NSRegularExpression(pattern: #"<link[^>]*href=["'][^"']*beook-export-reader\.css["'][^>]*>"#, options: .caseInsensitive) {
                html = expression.stringByReplacingMatches(in: html, range: NSRange(html.startIndex..., in: html), withTemplate: "")
            }
            if let expression = try? NSRegularExpression(pattern: #"<body\b([^>]*)>"#, options: .caseInsensitive) {
                html = expression.stringByReplacingMatches(in: html, range: NSRange(html.startIndex..., in: html), withTemplate: "<body class=\"leafscribe-print-flow\"> ")
            }
            if let url = Bundle.module.url(forResource: "Print", withExtension: "css", subdirectory: "Resources") {
                css = try String(contentsOf: url, encoding: .utf8)
            }
            css += "html{--leafscribe-font-size:\(Int(options.fontSize))pt;}"
        } else {
            css = "@media print {body{font-size:\(Int(options.fontSize))pt;} img,svg{max-width:100%;}table{break-inside:avoid;}tr{break-inside:avoid;}h1,h2,h3{break-after:avoid;}p{orphans:3;widows:3;}}"
        }
        let style = "<style>" + css + "</style>"
        if let range = html.range(of: "</head>", options: .caseInsensitive) { html.insert(contentsOf: style, at: range.lowerBound) }
        else { html = style + html }
        // Keep relative assets next to a private, disposable rendering document.
        let renderURL = source.deletingLastPathComponent().appendingPathComponent(".leafscribe-print-\(UUID().uuidString).\(source.pathExtension == "xhtml" ? "xhtml" : "html")")
        try html.write(to: renderURL, atomically: true, encoding: .utf8)
        defer { try? FileManager.default.removeItem(at: renderURL); webView = nil; permittedRoot = nil; renderWindow?.close(); renderWindow = nil }
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                navigation = continuation
                view.loadFileURL(renderURL, allowingReadAccessTo: resourceRoot(source))
                loadTimeout = Task { @MainActor [weak self] in
                    try? await Task.sleep(for: .seconds(45))
                    guard !Task.isCancelled else { return }
                    self?.finishLoading(.failure(LeafError.message("A section took too long to render.")))
                }
            }
        } onCancel: {
            Task { @MainActor [weak self] in self?.webView?.stopLoading(); self?.finishLoading(.failure(CancellationError())) }
        }
        try Task.checkCancellation()
        // App-injected script runs in an isolated content world; source scripts stay disabled.
        let geometry: String = try await withCheckedThrowingContinuation { continuation in
            view.callAsyncJavaScript("""
                await document.fonts.ready;
                await Promise.all(Array.from(document.images).map(image => image.complete ? Promise.resolve() : new Promise(resolve => { image.onload=resolve; image.onerror=resolve; })));
                const page = document.querySelector('.beook-export-pos-fixed-page');
                let width=0,height=0;
                if(page) for(const element of [page,...page.querySelectorAll('*')]) {
                    if(getComputedStyle(element).display==='none') continue;
                    const rect=element.getBoundingClientRect();
                    width=Math.max(width,rect.right); height=Math.max(height,rect.bottom);
                }
                return JSON.stringify([Math.ceil(width),Math.ceil(height)]);
                """, arguments: [:], in: nil, in: .defaultClient) { result in
                do {
                    guard let value = try result.get() as? String else { throw LeafError.message("Could not measure document layout.") }
                    continuation.resume(returning: value)
                } catch { continuation.resume(throwing: error) }
            }
        }
        if isFixed {
            if let bytes = geometry.data(using: .utf8), let bounds = try? JSONDecoder().decode([Double].self, from: bytes), bounds.count == 2 {
                canvas.width = max(canvas.width, bounds[0]); canvas.height = max(canvas.height, bounds[1])
                view.setFrameSize(canvas)
            }
            let capture = WKPDFConfiguration(); capture.rect = CGRect(origin: .zero, size: canvas)
            let data: Data = try await withCheckedThrowingContinuation { continuation in
                view.createPDF(configuration: capture) { continuation.resume(with: $0) }
            }
            guard let provider = CGDataProvider(data: data as CFData), let document = CGPDFDocument(provider), let page = document.page(at: 1) else {
                throw LeafError.message("Could not capture the fixed-layout page.")
            }
            var box = CGRect(origin: .zero, size: paper)
            guard let context = CGContext(output as CFURL, mediaBox: &box, nil) else { throw LeafError.message("Could not create PDF canvas.") }
            context.beginPDFPage(nil)
            context.setFillColor(NSColor.white.cgColor); context.fill(box)
            let bounds = page.getBoxRect(.mediaBox)
            let scale = min((paper.width - 60) / bounds.width, (paper.height - 76) / bounds.height)
            context.translateBy(x: (paper.width - bounds.width * scale) / 2, y: 38 + (paper.height - 76 - bounds.height * scale) / 2)
            context.scaleBy(x: scale, y: scale)
            context.drawPDFPage(page)
            context.endPDFPage(); context.closePDF()
            return
        }
        let info = NSPrintInfo.shared.copy() as! NSPrintInfo
        info.paperSize = options.pageSize == "Letter" ? NSSize(width: 612, height: 792) : NSSize(width: 595.28, height: 841.89)
        info.topMargin = 44; info.bottomMargin = 44; info.leftMargin = 44; info.rightMargin = 44
        info.horizontalPagination = .fit
        info.scalingFactor = 1
        info.verticalPagination = .automatic
        info.isVerticallyCentered = false
        info.isHorizontallyCentered = false
        info.jobDisposition = .save
        info.dictionary()[NSPrintInfo.AttributeKey.jobSavingURL] = output
        let operation = view.printOperation(with: info)
        operation.showsPrintPanel = false
        operation.showsProgressPanel = false
        // WKWebView must print with a running run loop. The synchronous run()
        // can print indefinitely before WebKit has supplied its page count.
        try await withCheckedThrowingContinuation { continuation in
            printing = continuation
            operation.runModal(for: window, delegate: self, didRun: #selector(printDidRun(_:success:contextInfo:)), contextInfo: nil)
        }
    }
    @objc nonisolated private func printDidRun(_ operation: NSPrintOperation, success: Bool, contextInfo: UnsafeMutableRawPointer?) {
        Task { @MainActor in self.finishPrinting(success: success) }
    }
    private func finishPrinting(success: Bool) {
        let pending = printing; printing = nil
        if success { pending?.resume() }
        else { pending?.resume(throwing: LeafError.message("Printing this section failed.")) }
    }
    private func resourceRoot(_ source: URL) -> URL {
        // Engine workspaces contain all chapters and cross-chapter assets.
        var current = source.deletingLastPathComponent()
        while current.path != "/" {
            if current.lastPathComponent.hasPrefix("leafscribe-job-") { return current }
            current.deleteLastPathComponent()
        }
        return source.deletingLastPathComponent()
    }
    private func finishLoading(_ result: Result<Void, Error>) {
        loadTimeout?.cancel(); loadTimeout = nil
        let pending = navigation; navigation = nil
        pending?.resume(with: result)
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) { finishLoading(.success(())) }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { finishLoading(.failure(error)) }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { finishLoading(.failure(error)) }
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping @MainActor (WKNavigationActionPolicy) -> Void) {
        let url = navigationAction.request.url
        decisionHandler(url?.isFileURL == true && navigationAction.navigationType == .other ? .allow : .cancel)
    }

    nonisolated static func extractPDFText(_ source: URL, ocr: Bool) async throws -> [PDFTextPage] {
        let task = Task.detached(priority: .userInitiated) {
            guard let pdf = PDFDocument(url: source), !pdf.isLocked else { throw LeafError.message("This PDF is locked or unreadable.") }
            var pages: [PDFTextPage] = []
            for i in 0..<pdf.pageCount {
                try Task.checkCancellation()
                guard let page = pdf.page(at: i) else { throw LeafError.message("Cannot read PDF page \(i + 1).") }
                var text = page.string ?? ""
                if text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, ocr {
                    let size = page.bounds(for: .mediaBox).size
                    let image = page.thumbnail(of: NSSize(width: size.width * 2, height: size.height * 2), for: .mediaBox)
                    var rect = NSRect(origin: .zero, size: image.size)
                    if let cg = image.cgImage(forProposedRect: &rect, context: nil, hints: nil) {
                        let request = VNRecognizeTextRequest()
                        request.recognitionLevel = .accurate
                        request.usesLanguageCorrection = true
                        request.automaticallyDetectsLanguage = true
                        try VNImageRequestHandler(cgImage: cg).perform([request])
                        text = (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }.joined(separator: "\n")
                    }
                }
                guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                    throw LeafError.message("Page \(i + 1) has no text. Enable OCR for scanned pages. No partial EPUB was saved.")
                }
                let escaped = text.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;").replacingOccurrences(of: ">", with: "&gt;")
                let paragraphs = escaped.components(separatedBy: "\n").filter { !$0.isEmpty }.map { "<p>\($0)</p>" }.joined()
                pages.append(PDFTextPage(title: "Page \(i + 1)", html: paragraphs))
            }
            return pages
        }
        return try await withTaskCancellationHandler { try await task.value } onCancel: { task.cancel() }
    }
}
