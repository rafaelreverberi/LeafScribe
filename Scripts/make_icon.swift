import AppKit
let folder = URL(fileURLWithPath: CommandLine.arguments[1])
try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
for size in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let pixels = size * scale
        let image = NSImage(size: NSSize(width: pixels, height: pixels))
        image.lockFocus()
        let side = CGFloat(pixels)
        NSColor(calibratedRed: 0.20, green: 0.36, blue: 0.28, alpha: 1).setFill()
        NSBezierPath(roundedRect: NSRect(x: side * 0.06, y: side * 0.06, width: side * 0.88, height: side * 0.88), xRadius: side * 0.20, yRadius: side * 0.20).fill()
        let config = NSImage.SymbolConfiguration(pointSize: side * 0.52, weight: .light)
        let symbol = NSImage(systemSymbolName: "leaf", accessibilityDescription: nil)!.withSymbolConfiguration(config)!
        NSColor(calibratedRed: 0.92, green: 0.92, blue: 0.80, alpha: 1).set()
        let tinted = NSImage(size: symbol.size)
        tinted.lockFocus()
        symbol.draw(at: .zero, from: .zero, operation: .sourceOver, fraction: 1)
        NSColor(calibratedRed: 0.92, green: 0.92, blue: 0.80, alpha: 1).setFill()
        NSRect(origin: .zero, size: symbol.size).fill(using: .sourceAtop)
        tinted.unlockFocus()
        tinted.draw(in: NSRect(x: side * 0.24, y: side * 0.23, width: side * 0.54, height: side * 0.54))
        image.unlockFocus()
        let bitmap = NSBitmapImageRep(data: image.tiffRepresentation!)!
        let suffix = scale == 2 ? "@2x" : ""
        try bitmap.representation(using: .png, properties: [:])!.write(to: folder.appendingPathComponent("icon_\(size)x\(size)\(suffix).png"))
    }
}
