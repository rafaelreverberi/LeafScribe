import Foundation

public struct Book: Codable, Identifiable, Hashable, Sendable {
    public var id: String
    public var title: String
    public var author: String
    public var kind: String
    public var path: String
    public var reference: String?
    public var profile: String?
    public var root: String?
    public var coverPath: String?
    public var chapterCount: Int
    public var annotationCount: Int
    public var language: String
    public var isBeook: Bool { kind == "beook" }
    public var sourceLabel: String { isBeook ? "Beook · Profile \(profile ?? "")" : "Imported \(kind.uppercased())" }
    public init(id: String, title: String, author: String = "", kind: String, path: String, reference: String? = nil, profile: String? = nil, root: String? = nil, coverPath: String? = nil, chapterCount: Int = 1, annotationCount: Int = 0, language: String = "und") {
        self.id = id; self.title = title; self.author = author; self.kind = kind; self.path = path
        self.reference = reference; self.profile = profile; self.root = root; self.coverPath = coverPath
        self.chapterCount = chapterCount; self.annotationCount = annotationCount; self.language = language
    }
}
public struct Chapter: Codable, Identifiable, Hashable, Sendable {
    public var id: String
    public var title: String
    public var path: String
    public var layout: String?
    public var sourceLabel: String?
    public var groupID: String?
    public var groupTitle: String?
    public var isAnnotation: Bool?
}
public struct PreparedDocument: Codable, Sendable {
    public var title: String?
    public var chapters: [Chapter]
    public var epubPath: String?
    public var pdfPath: String?
    public var warnings: [String]
    public var annotationCount: Int?
    public var inlineAnnotationCount: Int?
}
public struct DiscoveryResult: Codable, Sendable {
    public var books: [Book]
    public var warnings: [String]
}
public struct ExportRecord: Codable, Identifiable, Sendable {
    public var id: UUID = UUID()
    public var title: String
    public var path: String
    public var date: Date = Date()
    public var format: String
    public init(title: String, path: String, format: String) { self.title = title; self.path = path; self.format = format }
}
public struct PDFTextPage: Codable, Sendable {
    public var title: String
    public var html: String
    public init(title: String, html: String) { self.title = title; self.html = html }
}
public enum OutputFormat: String, CaseIterable, Identifiable, Sendable {
    case pdf = "PDF", epub = "EPUB"
    public var id: String { rawValue }
}
public struct ExportOptions: Sendable {
    public var format: OutputFormat = .pdf
    public var includeAnnotations = true
    public var pageSize = "A4"
    public var fontSize: Double = 12
    public var ocr = false
    public var selectedChapterIDs: [String]? = nil
    public var addPageNumbers = true
    public init() {}
}
public enum LeafError: LocalizedError, Sendable {
    case message(String)
    public var errorDescription: String? { switch self { case .message(let value): value } }
}
public enum LibraryPersistence {
    public static func read<T: Decodable>(_ type: T.Type, from url: URL) throws -> T {
        try JSONDecoder().decode(type, from: Data(contentsOf: url))
    }
    public static func write<T: Encodable>(_ value: T, to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try JSONEncoder().encode(value).write(to: url, options: .atomic)
    }
}
