import SwiftUI
import LeafScribeCore

struct ExportSheet: View {
    @Bindable var store: LibraryStore
    @Environment(\.dismiss) private var dismiss
    @State private var options = ExportOptions()
    @State private var document: PreparedDocument?
    @State private var workspace: URL?
    @State private var loading = true
    @State private var loadError: String?
    @State private var wholeBook = true
    @State private var selected = Set<String>()
    @State private var chapterSearch = ""
    private var chapters: [Chapter] { (document?.chapters ?? []).filter { $0.isAnnotation != true } }
    private var visibleChapters: [Chapter] { chapters.filter { chapterSearch.isEmpty || $0.title.localizedCaseInsensitiveContains(chapterSearch) || ($0.groupTitle?.localizedCaseInsensitiveContains(chapterSearch) ?? false) } }
    private var groups: [String] { visibleChapters.reduce(into: [String]()) { values, chapter in
        let key = chapter.groupID ?? "contents"
        if !values.contains(key) { values.append(key) }
    } }
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Export your book").font(.system(size: 27, design: .serif))
                    Text(store.selected?.title ?? "Export").foregroundStyle(.secondary).lineLimit(2)
                }
                Spacer()
                Image(systemName: "arrow.up.doc").font(.system(size: 28, weight: .light)).foregroundStyle(Color.leaf)
            }
            Picker("Format", selection: $options.format) { ForEach(OutputFormat.allCases) { Text($0.rawValue).tag($0) } }
                .pickerStyle(.segmented).labelsHidden().controlSize(.large)
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    if options.format == .pdf, store.selected?.kind != "pdf" {
                        VStack(alignment: .leading, spacing: 12) {
                            Text("PDF layout").font(.headline)
                            Picker("Paper size", selection: $options.pageSize) { Text("A4").tag("A4"); Text("US Letter").tag("Letter") }
                            Stepper("Reading text size: \(Int(options.fontSize)) pt", value: $options.fontSize, in: 9...20)
                            Toggle("Add PDF page numbers", isOn: $options.addPageNumbers)
                            Text("Fixed-layout pages are scaled as a complete page, preserving columns and tables. Reading text size applies to flowing text.")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    if store.selected?.isBeook == true {
                        VStack(alignment: .leading, spacing: 9) {
                            Text("Annotations").font(.headline)
                            Toggle("Include notes, highlights & bookmarks", isOn: $options.includeAnnotations)
                                .disabled(loading || document?.annotationCount == 0)
                            if loading { Text("Checking stored annotations…").font(.caption).foregroundStyle(.secondary) }
                            else if let count = document?.annotationCount, count > 0 {
                                Text("\(count) stored annotations. \(document?.inlineAnnotationCount ?? 0) highlights matched in the text. All available entries also appear in an appendix.")
                                    .font(.caption).foregroundStyle(.secondary)
                            } else {
                                Text("No locally stored annotations were found for this book. Download or synchronize them in Beook first.")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                    if store.selected?.kind == "pdf" {
                        VStack(alignment: .leading, spacing: 10) {
                            if options.format == .epub {
                                Toggle("Use OCR for scanned pages", isOn: $options.ocr)
                                Text("Extracts text page by page. PDF layout and annotations are not converted to EPUB.").font(.caption).foregroundStyle(.secondary)
                            } else { Text("Original PDF pages, annotations, and text are preserved.").font(.caption).foregroundStyle(.secondary) }
                        }
                    } else {
                        Divider()
                        VStack(alignment: .leading, spacing: 12) {
                            Text("Chapters & sections").font(.headline)
                            Toggle("Export the entire book", isOn: $wholeBook)
                            if loading { ProgressView("Loading chapters…").controlSize(.small) }
                            else if let loadError {
                                Text(loadError).font(.caption).foregroundStyle(.red)
                            } else if !wholeBook {
                                HStack {
                                    Text("\(selected.count) of \(chapters.count) selected").font(.caption).foregroundStyle(.secondary)
                                    Spacer()
                                    Button("All") { selected = Set(chapters.map(\.id)) }
                                    Button("None") { selected.removeAll() }
                                }
                                TextField("Find a chapter or section", text: $chapterSearch).textFieldStyle(.roundedBorder)
                                ForEach(groups, id: \.self) { group in
                                    let members = chapters.filter { ($0.groupID ?? "contents") == group }
                                    let shown = visibleChapters.filter { ($0.groupID ?? "contents") == group }
                                    let grouped = members.first?.groupID != nil
                                    if grouped {
                                        Toggle(isOn: Binding(get: { members.allSatisfy { selected.contains($0.id) } }, set: { value in
                                            for chapter in members { if value { selected.insert(chapter.id) } else { selected.remove(chapter.id) } }
                                        })) {
                                            Text(members.first?.groupTitle ?? "Contents").font(.system(size: 12, weight: .semibold))
                                        }.toggleStyle(.checkbox).padding(.top, 9)
                                    }
                                    ForEach(shown) { chapter in
                                        Toggle(isOn: Binding(get: { selected.contains(chapter.id) }, set: { if $0 { selected.insert(chapter.id) } else { selected.remove(chapter.id) } })) {
                                            HStack(alignment: .firstTextBaseline) {
                                                Text(chapter.title).font(.system(size: 12)).fixedSize(horizontal: false, vertical: true)
                                                Spacer(minLength: 6)
                                                if let label = chapter.sourceLabel { Text(label).font(.caption).foregroundStyle(.secondary) }
                                            }
                                        }.toggleStyle(.checkbox).padding(.vertical, 3).padding(.leading, grouped ? 16 : 0)
                                    }
                                }
                            } else { Text("All \(chapters.count) sections in reading order.").font(.caption).foregroundStyle(.secondary) }
                        }
                    }
                }.padding(.vertical, 4).padding(.trailing, 12)
            }.frame(maxWidth: .infinity, maxHeight: .infinity)
            Divider()
            HStack {
                Text("Saved locally.").font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Choose destination…") {
                    options.selectedChapterIDs = wholeBook ? nil : Array(selected)
                    store.startExport(options)
                }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                    .disabled(loading || loadError != nil || (!wholeBook && selected.isEmpty))
            }
        }.padding(26).frame(width: 540, height: 650).tint(.leaf)
        .task {
            guard let book = store.selected else { return }
            let job = LibraryStore.workspace(); workspace = job
            do {
                let result = try await store.engine.prepare(book, workspace: job, annotations: true)
                try Task.checkCancellation()
                document = result
                selected = Set(result.chapters.filter { $0.isAnnotation != true }.map(\.id))
            } catch is CancellationError { try? FileManager.default.removeItem(at: job) }
            catch { loadError = error.localizedDescription }
            loading = false
        }
        .onDisappear { if let workspace { try? FileManager.default.removeItem(at: workspace) } }
    }
}
