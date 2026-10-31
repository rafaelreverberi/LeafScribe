import AppKit
import SwiftUI

@main
struct LeafScribeApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @State private var store = LibraryStore()
    var body: some Scene {
        WindowGroup {
            LibraryView(store: store).frame(minWidth: 920, minHeight: 640)
        }.defaultSize(width: 1240, height: 820)
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("Import books…") { store.chooseImports() }.keyboardShortcut("o")
            }
            CommandMenu("Library") {
                Button("Refresh Beook library") { Task { await store.refresh() } }.keyboardShortcut("r").disabled(store.isLoading)
                Button("Preview selected book") { store.preparePreview() }.keyboardShortcut("p", modifiers: [.command, .shift]).disabled(store.selected == nil || store.isPreparing)
                Button("Export selected book…") { store.showExport = true }.keyboardShortcut("e").disabled(store.selected == nil || store.isExporting)
            }
        }
        Settings { SettingsView() }
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        if let index = CommandLine.arguments.firstIndex(of: "--render-document"), CommandLine.arguments.count > index + 2 {
            let input = CommandLine.arguments[index + 1], output = CommandLine.arguments[index + 2]
            Task { await SmokeTest.renderPrepared(input: input, output: output) }
        } else if let index = CommandLine.arguments.firstIndex(of: "--smoke-test"), CommandLine.arguments.count > index + 1 {
            let folder = URL(fileURLWithPath: CommandLine.arguments[index + 1])
            Task { await SmokeTest.run(in: folder) }
        } else { NSApp.activate(ignoringOtherApps: true) }
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}
