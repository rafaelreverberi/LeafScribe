import Foundation

private struct EngineRequest<Arguments: Encodable>: Encodable {
    var version = 1
    var action: String
    var arguments: Arguments
}
private struct EngineEnvelope<Value: Decodable>: Decodable {
    var version: Int
    var event: String
    var result: Value?
    var message: String?
}
private struct Empty: Codable, Sendable {}
private struct DiscoverArguments: Encodable { var root: String? }
private struct ImportArguments: Encodable { var source: String; var destination: String }
private struct PrepareArguments: Encodable { var book: Book; var workspace: String; var annotations: Bool; var selectedChapters: [String]? }
private struct PagesArguments: Encodable { var pages: [PDFTextPage]; var title: String; var destination: String }

// Process is confined to the worker except terminate(), protected by the lock.
private final class ProcessHandle: @unchecked Sendable {
    let lock = NSLock()
    var process: Process?
    var cancelled = false
    func cancel() {
        lock.lock(); defer { lock.unlock() }
        cancelled = true
        if let process, process.isRunning { process.terminate() }
    }
    func launch(_ value: Process) throws {
        lock.lock(); defer { lock.unlock() }
        if cancelled { throw CancellationError() }
        process = value
        try value.run()
    }
}

public actor ExtractionEngine {
    private let executable: URL
    private let prefix: [String]
    public init(executable: URL, prefix: [String] = []) { self.executable = executable; self.prefix = prefix }

    public func discover(root: String? = nil) async throws -> DiscoveryResult {
        try await request("discover", DiscoverArguments(root: root))
    }
    public func importDocument(_ url: URL, into folder: URL) async throws -> Book {
        try await request("import", ImportArguments(source: url.path, destination: folder.path))
    }
    public func epubCover(_ url: URL, into folder: URL) async throws -> String? {
        struct Result: Decodable, Sendable { var coverPath: String? }
        let result: Result = try await request("epub_cover", ImportArguments(source: url.path, destination: folder.path))
        return result.coverPath
    }
    public func prepare(_ book: Book, workspace: URL, annotations: Bool, selectedChapters: [String]? = nil) async throws -> PreparedDocument {
        try await request("prepare", PrepareArguments(book: book, workspace: workspace.path, annotations: annotations, selectedChapters: selectedChapters))
    }
    public func epub(pages: [PDFTextPage], title: String, to url: URL) async throws {
        struct Result: Decodable, Sendable { var path: String; var chapterCount: Int }
        let _: Result = try await request("epub_from_pages", PagesArguments(pages: pages, title: title, destination: url.path))
    }
    private func request<A: Encodable, R: Decodable & Sendable>(_ action: String, _ args: A) async throws -> R {
        let input = try JSONEncoder().encode(EngineRequest(action: action, arguments: args)) + Data([10])
        let output = try await Self.run(executable: executable, arguments: prefix, input: input)
        try Task.checkCancellation()
        let decoder = JSONDecoder()
        for line in output.split(separator: 10) {
            let event = try decoder.decode(EngineEnvelope<R>.self, from: Data(line))
            guard event.version == 1 else { throw LeafError.message("Unsupported extraction protocol.") }
            if event.event == "error" { throw LeafError.message(event.message ?? "Extraction failed.") }
            if let result = event.result { return result }
        }
        throw LeafError.message("The extraction engine exited without a result.")
    }
    private static func run(executable: URL, arguments: [String], input: Data) async throws -> Data {
        let handle = ProcessHandle()
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                DispatchQueue.global(qos: .userInitiated).async {
                    do {
                        let process = Process()
                        process.executableURL = executable
                        process.arguments = arguments
                        let stdin = Pipe(), stdout = Pipe()
                        process.standardInput = stdin; process.standardOutput = stdout
                        process.standardError = FileHandle.nullDevice
                        try handle.launch(process)
                        try stdin.fileHandleForWriting.write(contentsOf: input)
                        try stdin.fileHandleForWriting.close()
                        let data = stdout.fileHandleForReading.readDataToEndOfFile()
                        process.waitUntilExit()
                        continuation.resume(returning: data)
                    } catch { continuation.resume(throwing: error) }
                }
            }
        } onCancel: { handle.cancel() }
    }
}
