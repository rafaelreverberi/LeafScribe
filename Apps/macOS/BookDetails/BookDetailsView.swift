import SwiftUI
import LeafScribeCore

struct BookDetailsView: View {
    let book: Book
    @Bindable var store: LibraryStore
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                HStack { Text("BOOK DETAILS").font(.system(size: 10, weight: .medium)).tracking(1.6).foregroundStyle(.secondary); Spacer() }
                BookCover(book: book).frame(width: 126).frame(maxWidth: .infinity).padding(.vertical, 4)
                VStack(alignment: .leading, spacing: 8) {
                    Text(book.title).font(.system(size: 23, weight: .regular, design: .serif)).textSelection(.enabled)
                    if !book.author.isEmpty { Text(book.author).font(.system(size: 13)).foregroundStyle(.secondary) }
                }
                Divider()
                Grid(alignment: .leading, horizontalSpacing: 18, verticalSpacing: 13) {
                    detail("Source", book.sourceLabel)
                    detail(book.kind == "pdf" ? "Pages" : book.isBeook ? "Chapters" : "Sections", "\(book.chapterCount)")
                    detail("Annotations", book.kind == "epub" ? "Not indexed" : "\(book.annotationCount) available")
                    if let ref = book.reference { detail("Reference", ref) }
                    if book.language != "und" { detail("Language", book.language.uppercased()) }
                }.font(.system(size: 11))
                VStack(spacing: 10) {
                    Button { store.showExport = true } label: { Label("Export book…", systemImage: "square.and.arrow.up").frame(maxWidth: .infinity) }
                        .buttonStyle(.borderedProminent).controlSize(.large).disabled(store.isExporting)
                    Button { store.preparePreview() } label: { Label(store.isPreparing ? "Preparing preview…" : "Preview content", systemImage: "doc.text.magnifyingglass").frame(maxWidth: .infinity) }
                        .controlSize(.large).disabled(store.isPreparing)
                }
                VStack(alignment: .leading, spacing: 8) {
                    Label("A format that fits", systemImage: "text.book.closed").font(.system(size: 12, weight: .medium))
                    Text("PDF for a familiar page. EPUB for flexible text, on any screen.").font(.system(size: 12)).foregroundStyle(.secondary).lineSpacing(4)
                }.padding(.top, 6)
                if book.isBeook {
                    Divider()
                    Text("Supported notes, highlights, and bookmarks are included as an appendix. Interactive exercises remain static.")
                        .font(.system(size: 11)).foregroundStyle(.secondary).lineSpacing(3)
                }
            }.padding(24)
        }.background(Color(nsColor: .textBackgroundColor).opacity(0.6))
    }
    @ViewBuilder private func detail(_ label: String, _ value: String) -> some View {
        GridRow { Text(label).foregroundStyle(.secondary); Text(value).textSelection(.enabled).lineLimit(3) }
    }
}
