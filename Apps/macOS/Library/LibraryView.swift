import SwiftUI
import UniformTypeIdentifiers
import LeafScribeCore

struct LibraryView: View {
    @Bindable var store: LibraryStore
    @State private var listMode = false
    @State private var inspector = true
    @State private var hovering: String?
    @State private var dropTarget = false
    @State private var showNotices = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        NavigationSplitView {
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 9) {
                    Image(systemName: "leaf.fill").font(.title2).foregroundStyle(Color.leaf)
                    Text("LeafScribe").font(.system(size: 21, weight: .semibold, design: .serif))
                }.padding(.horizontal, 20).padding(.top, 22).padding(.bottom, 28)
                Text("WORKSPACE").font(.system(size: 10, weight: .semibold)).tracking(1.5).foregroundStyle(.secondary).padding(.horizontal, 20).padding(.bottom, 8)
                List(selection: $store.section) {
                    Label("All books", systemImage: "books.vertical").badge(store.books.count).tag("library")
                    Label("Beook", systemImage: "externaldrive").badge(store.books.filter(\.isBeook).count).tag("beook")
                    Label("Imported", systemImage: "tray.and.arrow.down").tag("imports")
                    Section {
                        Label("Export history", systemImage: "clock.arrow.circlepath").tag("exports")
                    }
                }.listStyle(.sidebar)
                Spacer()
                VStack(alignment: .leading, spacing: 8) {
                    Label("Made for your library", systemImage: "lock.shield").font(.system(size: 11, weight: .medium))
                    Text("Your books stay on this Mac.").font(.system(size: 11)).foregroundStyle(.secondary)
                }.padding(20)
                Divider().padding(.horizontal, 16)
                SettingsLink { Label("Settings", systemImage: "gearshape").font(.system(size: 12)) }
                    .buttonStyle(.plain).padding(20)
            }.navigationSplitViewColumnWidth(min: 190, ideal: 210, max: 250)
        } detail: {
            VStack(spacing: 0) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(heading).font(.system(size: 32, weight: .regular, design: .serif))
                        Text(store.section == "exports" ? "Ready for wherever you read next." : "Your books. Your notes. Your format.")
                            .font(.system(size: 13)).foregroundStyle(.secondary)
                    }
                    Spacer()
                    if store.section != "exports" {
                        Picker("Library layout", selection: $listMode) {
                            Image(systemName: "square.grid.2x2").tag(false)
                            Image(systemName: "list.bullet").tag(true)
                        }.pickerStyle(.segmented).labelsHidden().frame(width: 72).help("Switch library layout")
                        Button { inspector.toggle() } label: { Image(systemName: "sidebar.right") }.help("Toggle book details")
                    }
                }.padding(.horizontal, 30).padding(.top, 28).padding(.bottom, 22)
                Divider()
                if store.section == "exports" { history }
                else {
                    HSplitView {
                        libraryContent.frame(minWidth: 350, maxWidth: .infinity, maxHeight: .infinity)
                        if inspector, let book = store.selected {
                            BookDetailsView(book: book, store: store).frame(minWidth: 270, idealWidth: 300, maxWidth: 340)
                                .transition(.move(edge: .trailing).combined(with: .opacity))
                        }
                    }
                }
                Divider()
                statusBar
            }
            .background(Color(nsColor: .controlBackgroundColor).opacity(0.45))
            .animation(reduceMotion ? nil : .easeInOut(duration: 0.18), value: inspector)
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button { Task { await store.refresh() } } label: { Image(systemName: "arrow.clockwise") }
                        .disabled(store.isLoading).help("Refresh Beook library (⌘R)")
                }
                ToolbarItem(placement: .primaryAction) {
                    Button { store.chooseImports() } label: { Label("Import books", systemImage: "plus") }
                        .disabled(store.isLoading).help("Import EPUB, HTML, or PDF (⌘O)")
                }
            }
            .searchable(text: $store.search, placement: .toolbar, prompt: "Search your library")
            .onDrop(of: [.fileURL], isTargeted: $dropTarget) { providers in
                Task { @MainActor in
                    var urls: [URL] = []
                    for provider in providers {
                        let url: URL? = await withCheckedContinuation { continuation in
                            _ = provider.loadObject(ofClass: URL.self) { url, _ in continuation.resume(returning: url) }
                        }
                        if let url { urls.append(url) }
                    }
                    await store.importURLs(urls)
                }
                return true
            }
            .overlay { if dropTarget { RoundedRectangle(cornerRadius: 12).stroke(Color.leaf, style: StrokeStyle(lineWidth: 3, dash: [8])).padding(10).allowsHitTesting(false) } }
        }
        .tint(.leaf)
        .sheet(isPresented: $store.showExport) { ExportSheet(store: store) }
        .sheet(isPresented: Binding(get: { store.preview != nil }, set: { if !$0 { store.closePreview() } })) {
            if let document = store.preview { DocumentPreview(document: document, title: store.selected?.title ?? "Preview", close: store.closePreview) }
        }
        .alert("LeafScribe couldn’t complete that", isPresented: Binding(get: { store.error != nil }, set: { if !$0 { store.error = nil } })) {
            Button("OK") { store.error = nil }
        } message: { Text(store.error ?? "") }
        .onChange(of: store.section) { _, _ in
            if store.section != "exports", !store.filteredBooks.contains(where: { $0.id == store.selection }) { store.selection = nil }
        }
        .task { await store.refresh() }
    }
    private var heading: String {
        switch store.section { case "beook": "Beook library"; case "imports": "Imported books"; case "exports": "Export history"; default: "Your library" }
    }
    @ViewBuilder private var libraryContent: some View {
        if store.isLoading, store.books.isEmpty {
            VStack(spacing: 14) { ProgressView(); Text("Finding your books…").foregroundStyle(.secondary) }.frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if store.filteredBooks.isEmpty {
            VStack(spacing: 16) {
                Image(systemName: store.search.isEmpty ? "book.closed" : "magnifyingglass").font(.system(size: 46, weight: .ultraLight)).foregroundStyle(Color.leaf)
                Text(store.search.isEmpty ? "A home for your books" : "No matching books").font(.system(size: 25, design: .serif))
                Text(store.search.isEmpty ? "Import an EPUB, HTML, or PDF, or download\nyour Beook books for offline use." : "Try another title, author, or reference.")
                    .multilineTextAlignment(.center).foregroundStyle(.secondary).font(.system(size: 13)).lineSpacing(4)
                if store.search.isEmpty {
                    Button("Import books…") { store.chooseImports() }.buttonStyle(.borderedProminent).controlSize(.large)
                    Button("Explore with a sample book") { store.openSample() }.buttonStyle(.link)
                }
            }.padding(40).frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if listMode {
            List(store.filteredBooks, selection: $store.selection) { book in
                HStack(spacing: 14) {
                    BookCover(book: book).frame(width: 38, height: 54)
                    VStack(alignment: .leading, spacing: 5) { Text(book.title).fontWeight(.medium); Text(book.author.isEmpty ? book.sourceLabel : book.author).font(.caption).foregroundStyle(.secondary) }
                    Spacer()
                    Text(book.kind.uppercased()).font(.caption).foregroundStyle(.secondary)
                    Text("\(book.chapterCount)").monospacedDigit().frame(width: 38)
                }.padding(.vertical, 6).tag(book.id).contextMenu { bookMenu(book) }
            }.listStyle(.inset)
        } else {
            ScrollView {
                HStack {
                    Text("\(store.filteredBooks.count) \(store.filteredBooks.count == 1 ? "BOOK" : "BOOKS")").font(.system(size: 10, weight: .medium)).tracking(1.3)
                    Spacer()
                    Text("TITLE A–Z").font(.system(size: 10)).tracking(1)
                }.foregroundStyle(.secondary).padding(.bottom, 20)
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 142, maximum: 190), spacing: 28, alignment: .top)], alignment: .leading, spacing: 28) {
                    ForEach(store.filteredBooks) { book in
                        Button { store.selection = book.id } label: {
                            VStack(alignment: .leading, spacing: 11) {
                                BookCover(book: book).padding(8)
                                    .background(store.selection == book.id ? Color.leaf.opacity(0.09) : .clear, in: RoundedRectangle(cornerRadius: 8))
                                    .overlay { RoundedRectangle(cornerRadius: 8).stroke(store.selection == book.id ? Color.leaf.opacity(0.6) : .clear, lineWidth: 1.5) }
                                Text(book.title).font(.system(size: 13, weight: .medium)).lineLimit(2).multilineTextAlignment(.leading).foregroundStyle(.primary)
                                Text(book.author.isEmpty ? book.sourceLabel : book.author).font(.system(size: 11)).foregroundStyle(.secondary).lineLimit(1)
                                Text(book.kind.uppercased()).font(.system(size: 9, weight: .medium)).tracking(1).foregroundStyle(Color.leaf)
                            }
                            .contentShape(Rectangle())
                            .offset(y: hovering == book.id ? -3 : 0)
                            .animation(reduceMotion ? nil : .easeOut(duration: 0.16), value: hovering == book.id)
                        }.buttonStyle(.plain).onHover { hovering = $0 ? book.id : nil }.contextMenu { bookMenu(book) }
                    }
                }
            }.padding(28)
        }
    }
    @ViewBuilder private func bookMenu(_ book: Book) -> some View {
        Button("Preview") { store.selection = book.id; store.preparePreview() }
        Button("Export…") { store.selection = book.id; store.showExport = true }.disabled(store.isExporting)
        if !book.isBeook { Divider(); Button("Remove imported copy", role: .destructive) { store.remove(book) } }
    }
    private var statusBar: some View {
        HStack(spacing: 9) {
            if store.isLoading || store.isPreparing { ProgressView().controlSize(.small) }
            else { Circle().fill(Color.leaf.opacity(0.7)).frame(width: 5, height: 5) }
            Text(store.status).font(.system(size: 11)).lineLimit(1)
            if !store.warnings.isEmpty {
                Button { showNotices.toggle() } label: { Label("Notices", systemImage: "info.circle") }
                    .buttonStyle(.plain).font(.system(size: 11)).help("View library and export notices")
                    .popover(isPresented: $showNotices) {
                        VStack(alignment: .leading, spacing: 12) {
                            Text("Library & export notices").font(.headline)
                            ForEach(Array(store.warnings.enumerated()), id: \.offset) { _, warning in
                                Text(warning).font(.system(size: 12)).fixedSize(horizontal: false, vertical: true)
                            }
                        }.padding(20).frame(width: 360)
                    }
            }
            Spacer()
            if store.isExporting {
                ProgressView(value: store.progress).frame(width: 120)
                Button("Cancel") { store.exportTask?.cancel() }.font(.caption)
            } else if store.isPreparing {
                Button("Cancel") { store.preparationTask?.cancel() }.font(.caption)
            } else { Text("LOCAL FIRST").font(.system(size: 9, weight: .medium)).tracking(1.4).foregroundStyle(.tertiary) }
        }.foregroundStyle(.secondary).padding(.horizontal, 22).frame(height: 36)
    }
    private var history: some View {
        Group {
            if store.exports.isEmpty {
                ContentUnavailableView("Your exports appear here", systemImage: "arrow.up.doc", description: Text("Select a book in your library to create your first export."))
            } else {
                List(store.exports) { record in
                    HStack(spacing: 16) {
                        Image(systemName: record.format == "PDF" ? "doc.richtext" : "book.closed").font(.title2).foregroundStyle(Color.leaf)
                        VStack(alignment: .leading, spacing: 5) {
                            Text(record.title).fontWeight(.medium)
                            Text("\(record.format) · \(record.date.formatted(date: .abbreviated, time: .shortened))").font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button("Open") { NSWorkspace.shared.open(URL(fileURLWithPath: record.path)) }.disabled(!FileManager.default.fileExists(atPath: record.path))
                        Button { NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: record.path)]) } label: { Image(systemName: "folder") }.help("Show in Finder")
                    }.padding(.vertical, 10)
                }.listStyle(.inset)
            }
        }.frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
