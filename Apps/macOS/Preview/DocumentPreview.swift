import SwiftUI
import WebKit
import PDFKit
import LeafScribeCore

struct DocumentPreview: View {
    let document: PreparedDocument
    let title: String
    let close: () -> Void
    @State private var selected: String?
    var body: some View {
        VStack(spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 4) { Text(title).font(.headline); Text("Document preview").font(.caption).foregroundStyle(.secondary) }
                Spacer()
                Button("Done", action: close).keyboardShortcut(.cancelAction)
            }.padding(18)
            Divider()
            if let path = document.pdfPath { PDFPreview(url: URL(fileURLWithPath: path)) }
            else {
                HSplitView {
                    List(document.chapters, selection: $selected) { chapter in
                        Text(chapter.title).font(.system(size: 12)).padding(.vertical, 5).tag(chapter.id)
                    }.frame(minWidth: 190, idealWidth: 230, maxWidth: 300)
                    if let chapter = document.chapters.first(where: { $0.id == selected }) ?? document.chapters.first {
                        HTMLPreview(url: URL(fileURLWithPath: chapter.path)).id(chapter.path).frame(minWidth: 480)
                    }
                }
            }
            if !document.warnings.isEmpty {
                Divider()
                Text(document.warnings.joined(separator: "\n")).font(.caption).foregroundStyle(.secondary).padding(12).frame(maxWidth: .infinity, alignment: .leading)
            }
        }.frame(minWidth: 850, minHeight: 620).tint(.leaf)
    }
}

struct PDFPreview: NSViewRepresentable {
    let url: URL
    func makeNSView(context: Context) -> PDFView {
        let view = PDFView()
        view.autoScales = true
        view.displayMode = .singlePageContinuous
        view.document = PDFDocument(url: url)
        return view
    }
    func updateNSView(_ nsView: PDFView, context: Context) {}
}
struct HTMLPreview: NSViewRepresentable {
    let url: URL
    func makeCoordinator() -> Coordinator { Coordinator() }
    func makeNSView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        config.defaultWebpagePreferences.allowsContentJavaScript = false
        let view = WKWebView(frame: .zero, configuration: config)
        view.navigationDelegate = context.coordinator
        let rules = """
        [{"trigger":{"url-filter":"^https?://"},"action":{"type":"block"}}]
        """
        Task { @MainActor in
            do {
                if let rule = try await WKContentRuleListStore.default().compileContentRuleList(forIdentifier: "LeafScribePreviewOffline", encodedContentRuleList: rules) {
                    config.userContentController.add(rule)
                }
                var root = url.deletingLastPathComponent()
                while root.path != "/" && !root.lastPathComponent.hasPrefix("leafscribe-job-") { root.deleteLastPathComponent() }
                view.loadFileURL(url, allowingReadAccessTo: root.path == "/" ? url.deletingLastPathComponent() : root)
            } catch { view.loadHTMLString("<p>Preview could not be loaded safely.</p>", baseURL: nil) }
        }
        return view
    }
    func updateNSView(_ nsView: WKWebView, context: Context) {}
    final class Coordinator: NSObject, WKNavigationDelegate {
        func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping @MainActor (WKNavigationActionPolicy) -> Void) {
            // The engine provides the initial file; subsequent links stay in the preview workspace.
            if let url = navigationAction.request.url, url.isFileURL,
               let current = webView.url {
                var root = current.deletingLastPathComponent()
                while root.path != "/" && !root.lastPathComponent.hasPrefix("leafscribe-job-") { root.deleteLastPathComponent() }
                decisionHandler(root.path != "/" && url.standardizedFileURL.path.hasPrefix(root.path + "/") ? .allow : .cancel)
            } else {
                decisionHandler(navigationAction.navigationType == .other && navigationAction.request.url?.isFileURL == true ? .allow : .cancel)
            }
        }
    }
}
