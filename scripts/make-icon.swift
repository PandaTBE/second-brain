import AppKit
import Foundation

let destination = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: destination, withIntermediateDirectories: true)

func render(size: Int, name: String) throws {
    let dimension = CGFloat(size)
    let image = NSImage(size: NSSize(width: dimension, height: dimension))
    image.lockFocus()
    let inset = dimension * 0.065
    let bounds = NSRect(x: inset, y: inset, width: dimension - inset * 2, height: dimension - inset * 2)
    let background = NSBezierPath(roundedRect: bounds, xRadius: dimension * 0.19,
                                  yRadius: dimension * 0.19)
    NSGradient(starting: NSColor(srgbRed: 0.16, green: 0.49, blue: 0.38, alpha: 1),
               ending: NSColor(srgbRed: 0.05, green: 0.28, blue: 0.23, alpha: 1))?
        .draw(in: background, angle: -90)
    let configuration = NSImage.SymbolConfiguration(pointSize: dimension * 0.53, weight: .regular)
        .applying(NSImage.SymbolConfiguration(paletteColors: [.white]))
    if let symbol = NSImage(systemSymbolName: "brain.head.profile", accessibilityDescription: nil)?
        .withSymbolConfiguration(configuration) {
        let frame = NSRect(x: dimension * 0.235, y: dimension * 0.22,
                           width: dimension * 0.53, height: dimension * 0.56)
        symbol.draw(in: frame)
    }
    image.unlockFocus()
    guard let bitmap = NSBitmapImageRep(
        bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size, bitsPerSample: 8,
        samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
        bytesPerRow: 0, bitsPerPixel: 0
    ), let context = NSGraphicsContext(bitmapImageRep: bitmap) else {
        throw NSError(domain: "SecondBrainIcon", code: 1)
    }
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = context
    image.draw(in: NSRect(x: 0, y: 0, width: dimension, height: dimension))
    NSGraphicsContext.restoreGraphicsState()
    guard let data = bitmap.representation(using: .png, properties: [:]) else {
        throw NSError(domain: "SecondBrainIcon", code: 2)
    }
    try data.write(to: destination.appendingPathComponent(name))
}

for size in [16, 32, 128, 256, 512] {
    try render(size: size, name: "icon_\(size)x\(size).png")
    try render(size: size * 2, name: "icon_\(size)x\(size)@2x.png")
}
