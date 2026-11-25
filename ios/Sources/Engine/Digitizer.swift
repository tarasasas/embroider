import UIKit

enum Digitizer {
    private static let skitchPP1StitchesPerMinute = 400.0

    /// Image -> embroidery pattern.
    static func convert(_ image: UIImage, _ s: Settings) throws -> ConversionResult {
        let reduced = try reduceColors(image, s)
        let pattern = try digitize(reduced, s)
        let (minX, minY, maxX, maxY) = pattern.bounds
        let total = pattern.threads.reduce(0) { $0 + $1.stitches }
        return ConversionResult(
            pattern: pattern,
            widthMM: Double(maxX - minX) / UNITS_PER_MM,
            heightMM: Double(maxY - minY) / UNITS_PER_MM,
            stitchCount: total,
            trimCount: pattern.stitches.filter { $0.cmd == .trim }.count,
            minutes: Double(total) / skitchPP1StitchesPerMinute
        )
    }

    static func digitize(_ red: Reduced, _ s: Settings) throws -> EmbPattern {
        let w = red.w, h = red.h
        let px = 1 / PX_MM
        let center = Pt(Double(w) / 2, Double(h) / 2)
        let minArea = s.minAreaMM2 * px * px
        let inset = max(1, Int((0.4 * px).rounded()))
        let stitchLength = s.letteringStyle?.usesShortFillStitches == true
            ? min(2.6, s.stitchLengthMM) : s.stitchLengthMM
        let rowSpacing = s.letteringStyle == .light ? max(0.33, s.rowSpacingMM) : s.rowSpacingMM

        var pen: Pt? = nil
        var blocks: [(RGB, [[Pt]])] = []
        for (ci, color) in red.palette.enumerated() {
            let mask = red.labels.map { $0 == Int16(ci) }
            if !mask.contains(true) { continue }
            var runs: [[Pt]] = []
            if s.underlay {
                // sparse perpendicular rows, inset from the edge, stabilise the fabric
                runs += fill(mask: erode(mask, w: w, h: h, radius: inset), w: w, h: h, center: center,
                             angle: s.fillAngle + 90, spacing: 2.0 * px, stitch: 3.0 * px, pull: 0,
                             minLen: 1.0 * px, minArea: 4 * minArea, pen: &pen)
            }
            runs += fill(mask: mask, w: w, h: h, center: center, angle: s.fillAngle,
                         spacing: rowSpacing * px, stitch: stitchLength * px,
                         pull: s.pullCompMM * px, minLen: 0.5 * px, minArea: minArea, pen: &pen)
            if !runs.isEmpty { blocks.append((color, runs)) }
        }
        if blocks.isEmpty {
            throw EmbError("Nothing to stitch. Try more colors, or turn off background removal.")
        }

        let scale = PX_MM * UNITS_PER_MM
        func units(_ p: Pt) -> (Int, Int) {
            (Int(((p.x - Double(w) / 2) * scale).rounded()), Int(((p.y - Double(h) / 2) * scale).rounded()))
        }

        var pattern = EmbPattern()
        let trimPx = s.trimMM * px
        let lock = 4  // lock stitch length, 0.4 mm
        for (color, runs) in blocks {
            if !pattern.threads.isEmpty {
                pattern.stitches.append(Stitch(x: 0, y: 0, cmd: .trim))
                pattern.stitches.append(Stitch(x: 0, y: 0, cmd: .colorChange))
            }
            let chart = ThreadChart.threads[nearestBrother(color)]
            pattern.threads.append(ThreadColor(r: color.r, g: color.g, b: color.b, name: chart.name, stitches: 0))
            let thread = pattern.threads.count - 1

            var last: Pt? = nil
            for run in runs {
                if let l = last, sqrt(distSq(l, run[0])) > trimPx {
                    pattern.stitches.append(Stitch(x: 0, y: 0, cmd: .trim))
                }
                let (sx, sy) = units(run[0])
                pattern.stitches.append(Stitch(x: sx, y: sy, cmd: .jump))
                // lock stitches so the thread doesn't unravel after a trim
                for p in [(sx, sy), (sx + lock, sy), (sx, sy)] {
                    pattern.stitches.append(Stitch(x: p.0, y: p.1, cmd: .stitch))
                    pattern.threads[thread].stitches += 1
                }
                for p in run {
                    let (x, y) = units(p)
                    pattern.stitches.append(Stitch(x: x, y: y, cmd: .stitch))
                    pattern.threads[thread].stitches += 1
                }
                let (ex, ey) = units(run[run.count - 1])
                for p in [(ex + lock, ey), (ex, ey)] {
                    pattern.stitches.append(Stitch(x: p.0, y: p.1, cmd: .stitch))
                    pattern.threads[thread].stitches += 1
                }
                last = run[run.count - 1]
            }
        }
        pattern.stitches.append(Stitch(x: 0, y: 0, cmd: .trim))
        pattern.stitches.append(Stitch(x: 0, y: 0, cmd: .end))
        return pattern
    }

    /// Draws the stitches as thread lines.
    static func preview(_ pattern: EmbPattern, maxPx: CGFloat = 1000, fixedHoop: Bool = false) -> UIImage {
        var (minX, minY, maxX, maxY) = pattern.bounds
        if fixedHoop {
            let half = Int(Hoop.fourByFour.sizeMM.w * UNITS_PER_MM / 2)
            minX = min(minX, -half)
            minY = min(minY, -half)
            maxX = max(maxX, half)
            maxY = max(maxY, half)
        }
        let wMM = Double(maxX - minX) / UNITS_PER_MM, hMM = Double(maxY - minY) / UNITS_PER_MM
        let ppm = CGFloat(max(2.0, min(12.0, Double(maxPx) / max(wMM, hMM, 1))))
        let margin: CGFloat = 4
        let size = CGSize(width: (CGFloat(wMM) + 2 * margin) * ppm, height: (CGFloat(hMM) + 2 * margin) * ppm)
        let k = ppm / CGFloat(UNITS_PER_MM)

        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        return UIGraphicsImageRenderer(size: size, format: format).image { ctx in
            UIColor(red: 0.93, green: 0.91, blue: 0.88, alpha: 1).setFill()
            ctx.fill(CGRect(origin: .zero, size: size))
            let g = ctx.cgContext
            g.setLineWidth(max(1, 0.55 * ppm))  // thread looks a bit wider than 0.4 mm once it puffs up
            g.setLineCap(.round)
            g.setLineJoin(.round)

            func screen(_ s: Stitch) -> CGPoint {
                CGPoint(x: margin * ppm + CGFloat(s.x - minX) * k, y: margin * ppm + CGFloat(s.y - minY) * k)
            }
            func setColor(_ i: Int) {
                let t = pattern.threads[min(i, pattern.threads.count - 1)]
                g.setStrokeColor(UIColor(red: CGFloat(t.r) / 255, green: CGFloat(t.g) / 255,
                                         blue: CGFloat(t.b) / 255, alpha: 1).cgColor)
            }
            if fixedHoop {
                let half = Int(Hoop.fourByFour.sizeMM.w * UNITS_PER_MM / 2)
                let topLeft = screen(Stitch(x: -half, y: -half, cmd: .jump))
                let field = CGFloat(2 * half) * k
                g.setLineWidth(1)
                g.setStrokeColor(UIColor.systemGray.cgColor)
                g.stroke(CGRect(origin: topLeft, size: CGSize(width: field, height: field)))
                g.setLineWidth(max(1, 0.55 * ppm))
            }
            var colorIndex = 0
            var pos: CGPoint? = nil
            setColor(0)
            for s in pattern.stitches {
                switch s.cmd {
                case .stitch:
                    let p = screen(s)
                    if let from = pos {
                        g.move(to: from)
                        g.addLine(to: p)
                        g.strokePath()
                    }
                    pos = p
                case .jump:
                    pos = screen(s)
                case .colorChange:
                    colorIndex += 1
                    setColor(colorIndex)
                case .trim, .end:
                    break
                }
            }
        }
    }
}
