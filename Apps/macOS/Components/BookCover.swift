import SwiftUI
import AppKit
import PDFKit
import LeafScribeCore

extension Color {
    static let leaf = Color(nsColor: NSColor(name: "LeafScribeAccent") { appearance in
        appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
            ? NSColor(calibratedRed: 0.57, green: 0.74, blue: 0.62, alpha: 1)
            : NSColor(calibratedRed: 0.22, green: 0.40, blue: 0.31, alpha: 1)
    })
    static let paper = Color(nsColor: .textBackgroundColor)
}

struct BookCover: View {
    let book: Book
    @State private var image: NSImage?
    @AppStorage("stretchBookCovers") private var stretchBookCovers = false
    private var tone: Color {
        let hues: [Color] = [Color(red: 0.24, green: 0.35, blue: 0.30), Color(red: 0.32, green: 0.38, blue: 0.44), Color(red: 0.48, green: 0.31, blue: 0.23), Color(red: 0.42, green: 0.35, blue: 0.42)]
        let value = book.id.utf8.reduce(0) { ($0 + Int($1)) % hues.count }
        return hues[value]
    }
    var body: some View {
        GeometryReader { proxy in
            ZStack(alignment: .leading) {
                if let image {
                    if stretchBookCovers {
                        Image(nsImage: image).resizable().interpolation(.high)
                            .frame(width: proxy.size.width, height: proxy.size.height)
                    } else {
                        Image(nsImage: image).resizable().interpolation(.high)
                            .aspectRatio(contentMode: .fit)
                            .frame(width: proxy.size.width, height: proxy.size.height)
                            .background {
                                ZStack {
                                    Color(nsColor: .controlBackgroundColor)
                                    Image(nsImage: image).resizable().interpolation(.high)
                                        .aspectRatio(contentMode: .fill)
                                        .frame(width: proxy.size.width, height: proxy.size.height)
                                        .blur(radius: max(8, proxy.size.width * 0.10))
                                        .opacity(0.24)
                                }
                                .frame(width: proxy.size.width, height: proxy.size.height)
                                .clipped()
                            }
                    }
                } else {
                    tone
                    VStack(alignment: .leading, spacing: 12) {
                        Text(book.kind == "beook" ? "BEOOK LIBRARY" : "LEAFSCRIBE LIBRARY")
                            .font(.system(size: max(7, proxy.size.width * 0.045), weight: .medium)).tracking(2)
                            .foregroundStyle(.white.opacity(0.7))
                        Rectangle().fill(.white.opacity(0.5)).frame(width: 28, height: 1)
                        Text(book.title).font(.system(size: max(13, proxy.size.width * 0.12), weight: .regular, design: .serif))
                            .foregroundStyle(.white).lineLimit(5).minimumScaleFactor(0.65)
                        Spacer(minLength: 4)
                        Text(book.author.isEmpty ? "\(book.kind.uppercased()) DOCUMENT" : book.author)
                            .font(.system(size: max(8, proxy.size.width * 0.055))).foregroundStyle(.white.opacity(0.75)).lineLimit(2)
                        Image(systemName: "leaf").font(.system(size: max(16, proxy.size.width * 0.13))).foregroundStyle(.white.opacity(0.5))
                    }.padding(proxy.size.width * 0.13)
                    LinearGradient(colors: [.black.opacity(0.2), .clear, .white.opacity(0.08), .clear], startPoint: .leading, endPoint: .trailing)
                        .frame(width: 15)
                }
            }.frame(width: proxy.size.width, height: proxy.size.height)
                .clipShape(RoundedRectangle(cornerRadius: 3))
                .overlay(alignment: .leading) { Rectangle().fill(.black.opacity(0.08)).frame(width: 4) }
        }
        .aspectRatio(0.70, contentMode: .fit)
        .shadow(color: .black.opacity(0.12), radius: 5, x: 1, y: 4)
        .task(id: "\(book.id)|\(book.coverPath ?? book.path)") {
            image = nil
            if let path = book.coverPath { image = NSImage(contentsOfFile: path) }
            else if book.kind == "pdf", let pdf = PDFDocument(url: URL(fileURLWithPath: book.path)), let page = pdf.page(at: 0) {
                image = page.thumbnail(of: NSSize(width: 560, height: 800), for: .mediaBox)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Cover of \(book.title)")
    }
}
