import SwiftUI

struct SettingsView: View {
    @AppStorage("beookRoot") private var beookRoot = ""
    @AppStorage("stretchBookCovers") private var stretchBookCovers = false
    var body: some View {
        TabView {
            Form {
                Section("Cover display") {
                    Picker("Sizing", selection: $stretchBookCovers) {
                        Text("Fit proportionally").tag(false)
                        Text("Stretch to fill").tag(true)
                    }.pickerStyle(.segmented)
                    Text(stretchBookCovers
                         ? "Covers fill the entire frame. Image proportions may change."
                         : "Show the complete cover in its original proportions, with a soft backdrop.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Section("Beook discovery") {
                    TextField("Custom data folder", text: $beookRoot, prompt: Text("Use Beook’s default folder"))
                    Text("Leave empty to discover ~/Library/Application Support/ionesoft/beook. Refresh the library after changing this path.")
                        .font(.caption).foregroundStyle(.secondary)
                    Button("Choose folder…") {
                        let panel = NSOpenPanel(); panel.canChooseFiles = false; panel.canChooseDirectories = true
                        if panel.runModal() == .OK { beookRoot = panel.url?.path ?? "" }
                    }
                }
                Section("Local library") {
                    Text("Imported documents and export history are stored in Application Support/LeafScribe. Beook databases are opened read-only. Preview and conversion block web resources.")
                        .font(.system(size: 12)).foregroundStyle(.secondary)
                    Button("Show library folder") {
                        let url = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("LeafScribe")
                        NSWorkspace.shared.open(url)
                    }
                }
            }.formStyle(.grouped).tabItem { Label("General", systemImage: "gearshape") }
            VStack(spacing: 12) {
                Image(systemName: "leaf.fill").font(.system(size: 45, weight: .light)).foregroundStyle(Color.leaf)
                Text("LeafScribe").font(.system(size: 31, design: .serif))
                Text("Your books. Your notes. Your format.").foregroundStyle(.secondary)
                Text("Version \(Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "Development") · Personal use only").font(.caption).foregroundStyle(.secondary)
                Text("Independent software. Not affiliated with Beook, its publisher, or its developers.")
                    .font(.caption).foregroundStyle(.secondary).multilineTextAlignment(.center).padding(.horizontal, 30)
                DisclosureGroup("Licenses & notices") {
                    ScrollView {
                        Text(licenses).font(.system(size: 11, design: .monospaced)).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                    }.frame(height: 150)
                }.padding(.horizontal, 25)
                Link("beook-export by JoGe07", destination: URL(string: "https://github.com/JoGe07/beook-export")!)
            }.padding(25).tabItem { Label("About", systemImage: "info.circle") }
        }.frame(width: 540, height: 480).tint(.leaf)
    }
    private var licenses: String {
        guard let url = Bundle.module.url(forResource: "THIRD_PARTY_NOTICES", withExtension: "md", subdirectory: "Resources") else { return "License file missing. See THIRD_PARTY_NOTICES.md in the source distribution." }
        var text = ""
        if let license = Bundle.main.url(forResource: "LICENSE", withExtension: nil),
           let ownTerms = try? String(contentsOf: license, encoding: .utf8) {
            text = ownTerms + "\n\n"
        }
        text += (try? String(contentsOf: url, encoding: .utf8)) ?? "Could not load licenses."
        if let directory = Bundle.main.resourceURL?.appendingPathComponent("Licenses"),
           let files = try? FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil) {
            for file in files.sorted(by: { $0.lastPathComponent < $1.lastPathComponent }) {
                if let license = try? String(contentsOf: file, encoding: .utf8) { text += "\n\n" + file.lastPathComponent + "\n\n" + license }
            }
        }
        return text
    }
}
