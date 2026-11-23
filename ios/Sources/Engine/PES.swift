import Foundation

/// Writes a Brother .pes file ("truncated" PES v1: a short PES header followed by the PEC
/// section, which is what the machine actually sews from). Port of pyembroidery's PecWriter.
enum PES {
    static func data(for pattern: EmbPattern, name: String = "Untitled") -> Data {
        var out = [UInt8]()
        out += Array("#PES0001".utf8)
        out += [0x16] + [UInt8](repeating: 0, count: 13)  // PEC section starts at byte 0x16
        writePEC(pattern, name: name, to: &out)
        return Data(out)
    }

    // MARK: - PEC

    private static func writePEC(_ pattern: EmbPattern, name: String, to out: inout [UInt8]) {
        // header
        let label = String(name.prefix(8)).padding(toLength: 16, withPad: " ", startingAt: 0)
        out += Array("LA:\(label)\r".utf8)
        out += [UInt8](repeating: 0x20, count: 12) + [0xFF, 0x00]
        out += [6, 38]  // icon stride in bytes, icon height

        // each thread maps to its own Brother chart index
        var used = Set<Int>()
        var indexes: [UInt8] = []
        for t in pattern.threads {
            var i = nearestBrother(RGB(r: t.r, g: t.g, b: t.b), excluding: used)
            if used.count >= ThreadChart.threads.count - 1 { i = nearestBrother(RGB(r: t.r, g: t.g, b: t.b)) }
            used.insert(i)
            indexes.append(UInt8(i))
        }
        out += [UInt8](repeating: 0x20, count: 12)
        out.append(UInt8(max(0, indexes.count - 1)))
        out += indexes
        out += [UInt8](repeating: 0x20, count: max(0, 463 - indexes.count))

        // stitch block
        let (minX, minY, maxX, maxY) = pattern.bounds
        let blockStart = out.count
        out += [0x00, 0x00, 0x00, 0x00, 0x00]  // 2 spare bytes + 24-bit length, patched below
        out += [0x31, 0xFF, 0xF0]
        le16(maxX - minX, &out)
        le16(maxY - minY, &out)
        le16(0x1E0, &out)
        le16(0x1B0, &out)
        encodeStitches(pattern, &out)
        let length = out.count - blockStart
        out[blockStart + 2] = UInt8(length & 0xFF)
        out[blockStart + 3] = UInt8((length >> 8) & 0xFF)
        out[blockStart + 4] = UInt8((length >> 16) & 0xFF)

        // thumbnails: whole design, then one per color
        var colorOf: [[Stitch]] = [[]]
        for s in pattern.stitches {
            if s.cmd == .colorChange { colorOf.append([]) }
            else if s.cmd == .stitch { colorOf[colorOf.count - 1].append(s) }
        }
        out += icon(colorOf.flatMap { $0 }, bounds: (minX, minY, maxX, maxY))
        for stitches in colorOf { out += icon(stitches, bounds: (minX, minY, maxX, maxY)) }
    }

    private static func le16(_ v: Int, _ out: inout [UInt8]) {
        let u = UInt16(truncatingIfNeeded: v)
        out.append(UInt8(u & 0xFF))
        out.append(UInt8(u >> 8))
    }

    /// One value: a single byte if small, else two bytes with a flag in the top bits.
    private static func value(_ v: Int, long: Bool, flag: Int, _ out: inout [UInt8]) {
        if !long && v > -64 && v < 63 {
            out.append(UInt8(v & 0x7F))
        } else {
            let packed = (v & 0x0FFF) | 0x8000 | (flag << 8)
            out.append(UInt8((packed >> 8) & 0xFF))
            out.append(UInt8(packed & 0xFF))
        }
    }

    private static func encodeStitches(_ pattern: EmbPattern, _ out: inout [UInt8]) {
        let jumpCode = 0x10, trimCode = 0x20
        var colorTwo = true
        var jumping = true
        var first = true
        var xx = 0, yy = 0
        for s in pattern.stitches {
            switch s.cmd {
            case .stitch, .jump:
                let dx = s.x - xx, dy = s.y - yy
                xx += dx
                yy += dy
                if s.cmd == .stitch {
                    if jumping {
                        if dx != 0 && dy != 0 {
                            value(0, long: false, flag: 0, &out)
                            value(0, long: false, flag: 0, &out)
                        }
                        jumping = false
                    }
                    value(dx, long: false, flag: 0, &out)
                    value(dy, long: false, flag: 0, &out)
                } else {
                    jumping = true
                    let flag = first ? jumpCode : trimCode
                    value(dx, long: true, flag: flag, &out)
                    value(dy, long: true, flag: flag, &out)
                }
            case .colorChange:
                if jumping {
                    value(0, long: false, flag: 0, &out)
                    value(0, long: false, flag: 0, &out)
                    jumping = false
                }
                out += [0xFE, 0xB0, colorTwo ? 0x02 : 0x01]
                colorTwo.toggle()
            case .trim:
                break
            case .end:
                out.append(0xFF)
                return
            }
            first = false
        }
    }

    /// 48x38 monochrome thumbnail (6 bytes per row, least significant bit first).
    private static func icon(_ stitches: [Stitch], bounds: (Int, Int, Int, Int)) -> [UInt8] {
        var bits = [UInt8](repeating: 0, count: 6 * 38)
        let (minX, minY, maxX, maxY) = bounds
        let w = Double(max(1, maxX - minX)), h = Double(max(1, maxY - minY))
        let scale = min(42.0 / w, 32.0 / h)
        let offX = (48.0 - w * scale) / 2, offY = (38.0 - h * scale) / 2
        for s in stitches {
            let x = Int(offX + Double(s.x - minX) * scale)
            let y = Int(offY + Double(s.y - minY) * scale)
            if x >= 0 && x < 48 && y >= 0 && y < 38 {
                bits[y * 6 + x / 8] |= UInt8(1 << (x % 8))
            }
        }
        return bits
    }
}
