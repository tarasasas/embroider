import UIKit

enum LetteringShape: String, CaseIterable, Identifiable {
    case straight = "Straight", arcUp = "Arc up", arcDown = "Arc down"
    case circle = "Circle", wave = "Wave", slant = "Slant"
    var id: Self { self }
}

enum MotifPlacement: String, CaseIterable, Identifiable {
    case left = "Left", right = "Right", above = "Above", below = "Below"
    var id: Self { self }
}

struct LetteringDraft {
    var text = UserDefaults.standard.string(forKey: "letteringText") ?? "Bears"
    var fontName = UserDefaults.standard.string(forKey: "letteringFont") ?? "Georgia-Bold"
    var letterFonts: [Int: String] = [:]
    var alignment = 1
    var letterSpacing = 4.0
    var lineSpacing = 20.0
    var threadColor = UIColor(red: 15.0 / 255, green: 30.0 / 255, blue: 80.0 / 255, alpha: 1)
    var shape = LetteringShape.straight
    var bend = 0.5
    var outline = LetteringEffect(color: .systemOrange, amount: 0.06)
    var outside = LetteringEffect(color: .white, amount: 0.05)
    var shadow = LetteringEffect(color: .darkGray, amount: 0.05)
    var stitch = LetteringStitchStyle.auto
    var motif = ""
    var placement = MotifPlacement.right
    var motifScale = 0.6
    var motifColor = UIColor.systemOrange

    mutating func remapLetterFonts(from previousText: String) {
        let previous = Array(previousText), current = Array(text)
        let changes = current.difference(from: previous)
        let removed = Set(changes.removals.compactMap { change -> Int? in
            if case let .remove(offset, _, _) = change { return offset }
            return nil
        })
        let inserted = Set(changes.insertions.compactMap { change -> Int? in
            if case let .insert(offset, _, _) = change { return offset }
            return nil
        })
        let oldIndices = previous.indices.filter { !removed.contains($0) }
        let newIndices = current.indices.filter { !inserted.contains($0) }
        var updated: [Int: String] = [:]
        for (old, new) in zip(oldIndices, newIndices) { updated[new] = letterFonts[old] }
        letterFonts = updated
    }
}

struct LetteringEffect {
    var enabled = false
    var color: UIColor
    var amount: Double
}

enum LetteringRenderer {
    static let pointSize: CGFloat = 240
    private static let format: UIGraphicsImageRendererFormat = {
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        format.preferredRange = .standard
        return format
    }()

    static let motifs: [String: [[CGFloat]]] = {
        guard let url = Bundle.main.url(forResource: "motifs", withExtension: "json"),
              let data = try? Data(contentsOf: url) else { return [:] }
        let catalog = (try? JSONDecoder().decode([String: [[CGFloat]]].self, from: data)) ?? [:]
        return catalog.filter { _, points in points.count >= 3 && points.allSatisfy { $0.count == 2 } }
    }()

    static func colorCount(_ draft: LetteringDraft, motifOnly: Bool) -> Int {
        var colors = Set<UIColor>()
        if !motifOnly && !draft.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            colors.insert(draft.threadColor)
            for effect in [draft.outline, draft.outside, draft.shadow] where effect.enabled {
                colors.insert(effect.color)
            }
        }
        if motifs[draft.motif] != nil { colors.insert(draft.motifColor) }
        return colors.count
    }

    static func render(_ draft: LetteringDraft, font: UIFont, motifOnly: Bool) -> UIImage? {
        let characters = Array(draft.text)
        let first = characters.firstIndex { !$0.isWhitespace }
        let last = characters.lastIndex { !$0.isWhitespace }
        var letters: UIImage?
        if !motifOnly, let first, let last {
            let glyphs = (first...last).map { index in
                let face = draft.letterFonts[index].flatMap { UIFont(name: $0, size: font.pointSize) } ?? font
                return Glyph(character: characters[index], font: face)
            }
            letters = crop(renderText(glyphs, draft, font))
        }
        guard let points = motifs[draft.motif], let first = points.first else { return letters }

        let side = (pointSize * (letters == nil ? 1 : draft.motifScale)).rounded()
        let gap = pointSize * 0.08
        let ink = letters?.size ?? .zero
        var size = CGSize(width: side, height: side)
        var textOrigin = CGPoint.zero
        var motifOrigin = CGPoint.zero
        if letters != nil {
            switch draft.placement {
            case .left, .right:
                size = CGSize(width: ink.width + side + gap, height: max(ink.height, side))
                textOrigin = CGPoint(x: draft.placement == .left ? side + gap : 0, y: (size.height - ink.height) / 2)
                motifOrigin = CGPoint(x: draft.placement == .right ? ink.width + gap : 0, y: (size.height - side) / 2)
            case .above, .below:
                size = CGSize(width: max(ink.width, side), height: ink.height + side + gap)
                textOrigin = CGPoint(x: (size.width - ink.width) / 2, y: draft.placement == .above ? side + gap : 0)
                motifOrigin = CGPoint(x: (size.width - side) / 2, y: draft.placement == .below ? ink.height + gap : 0)
            }
        }
        return UIGraphicsImageRenderer(size: size, format: format).image { renderer in
            letters?.draw(at: textOrigin)
            func point(_ pair: [CGFloat]) -> CGPoint {
                CGPoint(x: motifOrigin.x + pair[0] * side, y: motifOrigin.y + pair[1] * side)
            }
            let context = renderer.cgContext
            context.move(to: point(first))
            for pair in points.dropFirst() { context.addLine(to: point(pair)) }
            context.closePath()
            context.setFillColor(draft.motifColor.cgColor)
            context.fillPath()
        }
    }

    private struct Glyph {
        let character: Character
        let font: UIFont
    }

    private static func renderText(_ text: [Glyph], _ draft: LetteringDraft, _ font: UIFont) -> UIImage {
        let paragraph = NSMutableParagraphStyle()
        paragraph.alignment = [NSTextAlignment.left, .center, .right][draft.alignment]
        paragraph.lineSpacing = draft.lineSpacing
        let attributes: [NSAttributedString.Key: Any] = [
            .font: font, .kern: draft.letterSpacing, .paragraphStyle: paragraph,
        ]
        let options: NSStringDrawingOptions = [.usesLineFragmentOrigin, .usesFontLeading]
        let firstWidth = draft.outline.enabled ? draft.outline.amount : 0
        let outerWidth = firstWidth + (draft.outside.enabled ? draft.outside.amount : 0)
        let offset = draft.shadow.enabled ? pointSize * draft.shadow.amount : 0

        func attributed(_ glyphs: [Glyph], attributes: [NSAttributedString.Key: Any]) -> NSMutableAttributedString {
            let result = NSMutableAttributedString(string: "")
            for glyph in glyphs {
                var style = attributes
                style[.font] = glyph.font
                result.append(NSAttributedString(string: String(glyph.character), attributes: style))
            }
            return result
        }

        func draw(_ glyphs: [Glyph], in rect: CGRect) {
            func layer(_ color: UIColor, _ width: Double, _ offset: CGFloat = 0) {
                var style = attributes
                style[.foregroundColor] = color
                style[.strokeColor] = color
                // A negative stroke width draws both the fill and the stroke, in percent of font size.
                style[.strokeWidth] = -200 * width
                attributed(glyphs, attributes: style).draw(
                    with: rect.offsetBy(dx: offset, dy: offset), options: options, context: nil)
            }
            if draft.shadow.enabled { layer(draft.shadow.color, outerWidth, offset) }
            if draft.outside.enabled { layer(draft.outside.color, outerWidth) }
            if draft.outline.enabled { layer(draft.outline.color, firstWidth) }
            layer(draft.threadColor, 0)
        }

        var line: [Glyph] = []
        for word in text.split(whereSeparator: { $0.character.isWhitespace }) {
            if !line.isEmpty { line.append(Glyph(character: " ", font: font)) }
            line.append(contentsOf: word)
        }
        if [.arcUp, .arcDown, .circle].contains(draft.shape), line.count > 1 {
            let widths = line.map { (String($0.character) as NSString).size(withAttributes: [.font: $0.font]).width }
            let advances = widths.map { max(1, $0 + draft.letterSpacing) }
            let total = advances.reduce(0, +)
            let minRadius = pointSize * (draft.shape == .arcDown ? 3.5 : 1.2)
            let sweep = draft.shape == .circle ? 330 * CGFloat.pi / 180
                : min((40 + 220 * draft.bend) * .pi / 180, total / minRadius)
            let radius = max(pointSize * 0.35, total / sweep)
            let sign: CGFloat = draft.shape == .arcDown ? -1 : 1
            var cursor = -total / 2
            var points: [(CGPoint, CGFloat)] = []
            for advance in advances {
                let angle = (cursor + advance / 2) * sweep / total
                points.append((CGPoint(x: radius * sin(angle), y: -sign * radius * cos(angle)), sign * angle))
                cursor += advance
            }
            let minX = points.map { $0.0.x }.min() ?? 0, maxX = points.map { $0.0.x }.max() ?? 0
            let minY = points.map { $0.0.y }.min() ?? 0, maxY = points.map { $0.0.y }.max() ?? 0
            let lineHeight = line.map { $0.font.lineHeight }.max() ?? font.lineHeight
            let pad = lineHeight + outerWidth * pointSize + offset
            let size = CGSize(width: maxX - minX + 2 * pad, height: maxY - minY + 2 * pad)
            return UIGraphicsImageRenderer(size: size, format: format).image { renderer in
                let context = renderer.cgContext
                for (index, glyph) in line.enumerated() {
                    let (point, turn) = points[index]
                    context.saveGState()
                    context.translateBy(x: point.x - minX + pad, y: point.y - minY + pad)
                    context.rotate(by: turn)
                    draw([glyph], in: CGRect(x: -widths[index] / 2, y: -glyph.font.ascender,
                                             width: max(widths[index], advances[index]) + 2,
                                             height: glyph.font.lineHeight + 2))
                    context.restoreGState()
                }
            }
        }

        let string = draft.shape == .wave ? line : text
        let measured = attributed(string, attributes: attributes)
        let bounds = measured.boundingRect(with: CGSize(width: 20_000, height: 20_000),
                                           options: options, context: nil).integral
        let pad = pointSize * (0.35 + outerWidth) + offset
        let size = CGSize(width: bounds.width + 2 * pad + 2, height: bounds.height + 2 * pad + 2)
        let rect = CGRect(x: pad, y: pad, width: bounds.width + 2, height: bounds.height + 2)
        let block = UIGraphicsImageRenderer(size: size, format: format).image { _ in draw(string, in: rect) }
        switch draft.shape {
        case .slant:
            let shear = 0.15 + 0.45 * draft.bend
            let extra = ceil(shear * size.height)
            return UIGraphicsImageRenderer(size: CGSize(width: size.width + extra, height: size.height),
                                            format: format).image { renderer in
                renderer.cgContext.concatenate(CGAffineTransform(a: 1, b: 0, c: -shear, d: 1, tx: extra, ty: 0))
                block.draw(at: .zero)
            }
        case .wave:
            let amplitude = pointSize * (0.08 + 0.3 * draft.bend)
            let wavelength = max(size.width / 1.5, pointSize * 3)
            let height = size.height + 2 * amplitude
            return UIGraphicsImageRenderer(size: CGSize(width: size.width, height: height), format: format).image { renderer in
                var x: CGFloat = 0
                let context = renderer.cgContext
                while x < size.width {
                    context.saveGState()
                    context.clip(to: CGRect(x: x, y: 0, width: 4, height: height))
                    let y = amplitude * (1 + sin(2 * .pi * (x + 2) / wavelength))
                    block.draw(at: CGPoint(x: 0, y: y))
                    context.restoreGState()
                    x += 4
                }
            }
        default:
            return block
        }
    }

    private static func crop(_ image: UIImage) -> UIImage? {
        guard let cg = image.cgImage else { return nil }
        let w = cg.width, h = cg.height
        guard let mask = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
                                   space: nil, bitmapInfo: CGImageAlphaInfo.alphaOnly.rawValue),
              let bytes = mask.data?.assumingMemoryBound(to: UInt8.self) else { return image }
        mask.draw(cg, in: CGRect(x: 0, y: 0, width: CGFloat(w), height: CGFloat(h)))
        var left = w, right = -1, top = h, bottom = -1
        for y in 0..<h {
            let row = bytes + y * mask.bytesPerRow
            for x in 0..<w where row[x] > 32 {
                left = min(left, x)
                right = max(right, x)
                top = min(top, y)
                bottom = max(bottom, y)
            }
        }
        guard left <= right, top <= bottom else { return nil }
        let box = CGRect(x: CGFloat(left), y: CGFloat(top),
                         width: CGFloat(right - left + 1), height: CGFloat(bottom - top + 1))
        return cg.cropping(to: box).map { UIImage(cgImage: $0) }
    }
}
