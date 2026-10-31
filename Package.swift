// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "LeafScribe",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "LeafScribe", targets: ["LeafScribe"])],
    targets: [
        .target(name: "LeafScribeCore", path: "Core"),
        .executableTarget(name: "LeafScribe", dependencies: ["LeafScribeCore"], path: "Apps/macOS", resources: [.copy("Resources")]),
        .testTarget(name: "LeafScribeCoreTests", dependencies: ["LeafScribeCore"], path: "Tests/LeafScribeCoreTests")
    ]
)
