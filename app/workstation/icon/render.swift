// Renders an SVG to a square PNG with AppKit's built-in SVG support (no third-party tools).
// usage: swift render.swift <in.svg> <pixels> <out.png>
import AppKit

let args = CommandLine.arguments
guard args.count == 4, let pixels = Int(args[2]), let image = NSImage(contentsOfFile: args[1]) else {
    FileHandle.standardError.write("usage: render.swift <in.svg> <pixels> <out.png>\n".data(using: .utf8)!)
    exit(2)
}
guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels, bitsPerSample: 8, samplesPerPixel: 4,
                                 hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0) else { exit(1) }
rep.size = NSSize(width: pixels, height: pixels)
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
NSGraphicsContext.current?.imageInterpolation = .high
image.draw(in: NSRect(x: 0, y: 0, width: pixels, height: pixels), from: .zero, operation: .copy, fraction: 1)
NSGraphicsContext.restoreGraphicsState()
guard let png = rep.representation(using: .png, properties: [:]) else { exit(1) }
try png.write(to: URL(fileURLWithPath: args[3]))
