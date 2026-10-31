import Foundation
import Testing
@testable import LeafScribeCore

@Test func libraryRoundtripPreservesSourceIdentity() throws {
    let url = FileManager.default.temporaryDirectory.appendingPathComponent("leafscribe-model-\(UUID().uuidString).json")
    defer { try? FileManager.default.removeItem(at: url) }
    let book = Book(id: "beook:1:test", title: "Café & Notes", author: "A. Author", kind: "beook", path: "/tmp/db", reference: "test", profile: "1", annotationCount: 4)
    try LibraryPersistence.write([book], to: url)
    let result = try LibraryPersistence.read([Book].self, from: url)
    #expect(result == [book])
    #expect(result[0].sourceLabel == "Beook · Profile 1")
}
@Test func engineFailureIsStructured() async throws {
    let engine = ExtractionEngine(executable: URL(fileURLWithPath: "/usr/bin/false"))
    await #expect(throws: LeafError.self) { try await engine.discover() }
}
