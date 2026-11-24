import Foundation

let PX_MM = 0.2          // working resolution: millimetres per pixel
let UNITS_PER_MM = 10.0  // embroidery coordinates are 0.1 mm
let MIN_STITCH_MM = 0.4  // shorter stitches just knot the thread

typealias Pt = SIMD2<Double>

struct Settings {
    var widthMM = 90.0
    var heightMM = 90.0
    var colors = 4
    var cleanup = 1              // 0-3
    var removeBackground = true
    var boldMM = 0.0              // bolder lines: thickens every shape (made on the PC)
    var brotherColors = true
    var rowSpacingMM = 0.20      // fill density; smaller = denser
    var stitchLengthMM = 4.0
    var fillAngle = 45.0
    var pullCompMM = 0.15
    var underlay = true
    var trimMM = 1.0             // jumps longer than this get a trim
    var minAreaMM2 = 1.0         // smaller regions are skipped
}

enum StitchCommand {
    case stitch, jump, trim, colorChange, end
}

struct Stitch {
    var x: Int   // 0.1 mm units
    var y: Int
    var cmd: StitchCommand
}

struct ThreadColor {
    var r: Int
    var g: Int
    var b: Int
    var name: String
    var stitches: Int
}

struct EmbPattern {
    var stitches: [Stitch] = []
    var threads: [ThreadColor] = []

    /// (minX, minY, maxX, maxY) of everything that moves the needle.
    var bounds: (Int, Int, Int, Int) {
        var minX = Int.max, minY = Int.max, maxX = Int.min, maxY = Int.min
        for s in stitches where s.cmd == .stitch || s.cmd == .jump {
            minX = min(minX, s.x); maxX = max(maxX, s.x)
            minY = min(minY, s.y); maxY = max(maxY, s.y)
        }
        if minX == Int.max { return (0, 0, 0, 0) }
        return (minX, minY, maxX, maxY)
    }
}

struct ConversionResult {
    var pattern: EmbPattern
    var widthMM: Double
    var heightMM: Double
    var stitchCount: Int
    var trimCount: Int
    var minutes: Double
}

struct EmbError: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

extension Array {
    subscript(safe index: Int) -> Element? {
        indices.contains(index) ? self[index] : nil
    }
}

@inline(__always) func distSq(_ a: Pt, _ b: Pt) -> Double {
    let d = a - b
    return d.x * d.x + d.y * d.y
}
