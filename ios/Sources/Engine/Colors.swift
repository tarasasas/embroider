import UIKit

struct RGB: Hashable {
    var r: Int
    var g: Int
    var b: Int
}

struct Reduced {
    var w: Int
    var h: Int
    var labels: [Int16]     // palette index per pixel, -1 = not stitched
    var palette: [RGB]      // largest area first
}

struct SeededRandom {
    var state: UInt64
    mutating func next() -> Double {
        state = state &* 6364136223846793005 &+ 1442695040888963407
        return Double(state >> 11) / Double(1 << 53)
    }
}

/// "Redmean" distance, close to perceived color difference.
func colorDistance(_ a: RGB, _ b: RGB) -> Double {
    let rm = Double(a.r + b.r) / 2
    let dr = Double(a.r - b.r), dg = Double(a.g - b.g), db = Double(a.b - b.b)
    return (2 + rm / 256) * dr * dr + 4 * dg * dg + (2 + (255 - rm) / 256) * db * db
}

/// Index (1...64) of the closest Brother chart thread, skipping indices in `excluding`.
func nearestBrother(_ c: RGB, excluding: Set<Int> = []) -> Int {
    var best = 1
    var bestD = Double.infinity
    for i in 1..<ThreadChart.threads.count where !excluding.contains(i) {
        let t = ThreadChart.threads[i]
        let d = colorDistance(c, RGB(r: t.r, g: t.g, b: t.b))
        if d < bestD { bestD = d; best = i }
    }
    return best
}

// MARK: - Image loading

/// Scales the image to the working resolution. Returns (width, height, rgb, opaque).
func loadPixels(_ image: UIImage, _ s: Settings) throws -> (Int, Int, [UInt8], [Bool]) {
    let format = UIGraphicsImageRendererFormat()
    format.scale = 1
    let upright = UIGraphicsImageRenderer(size: image.size, format: format).image { _ in
        image.draw(at: .zero)
    }
    guard let cg = upright.cgImage, cg.width > 0, cg.height > 0 else {
        throw EmbError("Could not read that image.")
    }
    let mmPerSrcPx = min(s.widthMM / Double(cg.width), s.heightMM / Double(cg.height))
    let w = max(1, Int((Double(cg.width) * mmPerSrcPx / PX_MM).rounded()))
    let h = max(1, Int((Double(cg.height) * mmPerSrcPx / PX_MM).rounded()))

    var data = [UInt8](repeating: 0, count: w * h * 4)
    data.withUnsafeMutableBytes { buf in
        guard let ctx = CGContext(
            data: buf.baseAddress, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else { return }
        ctx.interpolationQuality = .high
        ctx.draw(cg, in: CGRect(x: 0, y: 0, width: w, height: h))
    }

    var rgb = [UInt8](repeating: 0, count: w * h * 3)
    var opaque = [Bool](repeating: false, count: w * h)
    for i in 0..<(w * h) {
        let a = Int(data[i * 4 + 3])
        if a >= 128 {
            opaque[i] = true
            for c in 0..<3 {
                rgb[i * 3 + c] = UInt8(min(255, Int(data[i * 4 + c]) * 255 / a))
            }
        }
    }
    return (w, h, rgb, opaque)
}

// MARK: - k-means

private func nearestCenter(_ p: SIMD3<Float>, _ centers: [SIMD3<Float>]) -> Int {
    var best = 0
    var bestD = Float.infinity
    for (i, c) in centers.enumerated() {
        let d = c - p
        let dd = d.x * d.x + d.y * d.y + d.z * d.z
        if dd < bestD { bestD = dd; best = i }
    }
    return best
}

/// Best of several k-means++ runs, so small but distinct colors get their own cluster.
func kmeans(_ pixels: [SIMD3<Float>], k wanted: Int, attempts: Int = 3, iterations: Int = 25) -> [SIMD3<Float>] {
    var rng = SeededRandom(state: 12345)
    var sample = pixels
    if sample.count > 40_000 {
        let step = sample.count / 40_000
        sample = stride(from: 0, to: pixels.count, by: step).map { pixels[$0] }
    }
    let distinct = Set(sample.map { RGB(r: Int($0.x), g: Int($0.y), b: Int($0.z)) }).count
    let k = max(1, min(wanted, distinct))

    var best: [SIMD3<Float>] = []
    var bestCost = Float.infinity
    for _ in 0..<attempts {
        var centers = [sample[min(sample.count - 1, Int(rng.next() * Double(sample.count)))]]
        var d2 = sample.map { p -> Float in
            let d = p - centers[0]
            return d.x * d.x + d.y * d.y + d.z * d.z
        }
        while centers.count < k {
            let total = d2.reduce(0, +)
            var pick = 0
            if total > 0 {
                var r = Float(rng.next()) * total
                for (i, d) in d2.enumerated() {
                    r -= d
                    if r <= 0 { pick = i; break }
                }
            }
            let chosen = sample[pick]
            centers.append(chosen)
            for i in 0..<sample.count {
                let d = sample[i] - chosen
                d2[i] = min(d2[i], d.x * d.x + d.y * d.y + d.z * d.z)
            }
        }

        for _ in 0..<iterations {
            var sums = [SIMD3<Float>](repeating: .zero, count: k)
            var counts = [Int](repeating: 0, count: k)
            for p in sample {
                let c = nearestCenter(p, centers)
                sums[c] += p
                counts[c] += 1
            }
            var moved: Float = 0
            for i in 0..<k where counts[i] > 0 {
                let updated = sums[i] / Float(counts[i])
                let d = updated - centers[i]
                moved = max(moved, abs(d.x), abs(d.y), abs(d.z))
                centers[i] = updated
            }
            if moved < 0.5 { break }
        }

        var cost: Float = 0
        for p in sample {
            let d = p - centers[nearestCenter(p, centers)]
            cost += d.x * d.x + d.y * d.y + d.z * d.z
        }
        if cost < bestCost { bestCost = cost; best = centers }
    }
    return best
}

// MARK: - Reduction

private func modeFilter(_ labels: [Int16], w: Int, h: Int, radius: Int, count: Int) -> [Int16] {
    var out = labels
    var tally = [Int](repeating: 0, count: count + 1)  // index = label + 1
    for y in 0..<h {
        let y0 = max(0, y - radius), y1 = min(h - 1, y + radius)
        for x in 0..<w {
            let x0 = max(0, x - radius), x1 = min(w - 1, x + radius)
            for i in 0..<tally.count { tally[i] = 0 }
            for yy in y0...y1 {
                for xx in x0...x1 { tally[Int(labels[yy * w + xx]) + 1] += 1 }
            }
            var best = Int(labels[y * w + x]) + 1
            for i in 0..<tally.count where tally[i] > tally[best] { best = i }
            out[y * w + x] = Int16(best - 1)
        }
    }
    return out
}

func reduceColors(_ image: UIImage, _ s: Settings) throws -> Reduced {
    let (w, h, rgb, opaque) = try loadPixels(image, s)
    let opaqueCount = opaque.filter { $0 }.count
    if opaqueCount == 0 { throw EmbError("The image is completely transparent.") }
    let hasTransparency = opaqueCount != w * h
    let extraBackground = (s.removeBackground && !hasTransparency) ? 1 : 0

    func px(_ i: Int) -> SIMD3<Float> {
        SIMD3<Float>(Float(rgb[i * 3]), Float(rgb[i * 3 + 1]), Float(rgb[i * 3 + 2]))
    }

    // Pick colors from flat areas only: anti-aliased edge pixels are blends of two colors
    // and would otherwise steal clusters from small, distinct details.
    var flat: [SIMD3<Float>] = []
    var all: [SIMD3<Float>] = []
    for y in 0..<h {
        for x in 0..<w {
            let i = y * w + x
            guard opaque[i] else { continue }
            let p = px(i)
            all.append(p)
            var isFlat = true
            for (dx, dy) in [(1, 0), (-1, 0), (0, 1), (0, -1)] {
                let nx = x + dx, ny = y + dy
                guard nx >= 0, nx < w, ny >= 0, ny < h else { continue }
                let d = p - px(ny * w + nx)
                if max(abs(d.x), abs(d.y), abs(d.z)) > 30 { isFlat = false; break }
            }
            if isFlat { flat.append(p) }
        }
    }
    let fit = flat.count * 5 >= opaqueCount ? flat : all  // photos have few flat pixels
    let wanted = s.colors > 0 ? s.colors : 4  // "Auto" is decided by the PC; the phone fallback uses 4
    let centers = kmeans(fit, k: wanted + extraBackground)

    var labels = [Int16](repeating: -1, count: w * h)
    for i in 0..<(w * h) where opaque[i] {
        labels[i] = Int16(nearestCenter(px(i), centers))
    }

    if s.cleanup > 0 {
        labels = modeFilter(labels, w: w, h: h, radius: s.cleanup, count: centers.count)
    }

    if extraBackground == 1 {
        // the color that dominates the image border is the background
        var tally = [Int](repeating: 0, count: centers.count)
        func vote(_ i: Int) { if labels[i] >= 0 { tally[Int(labels[i])] += 1 } }
        for x in 0..<w { vote(x); vote((h - 1) * w + x) }
        for y in 0..<h { vote(y * w); vote(y * w + w - 1) }
        if let top = tally.indices.max(by: { tally[$0] < tally[$1] }), tally[top] > 0 {
            for i in 0..<(w * h) where labels[i] == Int16(top) { labels[i] = -1 }
        }
    }

    // palette, optionally snapped to Brother threads, with identical colors merged
    var colors = centers.map { RGB(r: Int($0.x.rounded()), g: Int($0.y.rounded()), b: Int($0.z.rounded())) }
    if s.brotherColors {
        colors = colors.map { c in
            let t = ThreadChart.threads[nearestBrother(c)]
            return RGB(r: t.r, g: t.g, b: t.b)
        }
    }
    var unique: [RGB] = []
    var remap = [Int16](repeating: -1, count: colors.count)
    for (i, c) in colors.enumerated() {
        if let j = unique.firstIndex(of: c) {
            remap[i] = Int16(j)
        } else {
            remap[i] = Int16(unique.count)
            unique.append(c)
        }
    }
    for i in 0..<(w * h) where labels[i] >= 0 { labels[i] = remap[Int(labels[i])] }

    // largest color first: big areas are sewn before the details on top of them
    var area = [Int](repeating: 0, count: unique.count)
    for l in labels where l >= 0 { area[Int(l)] += 1 }
    let order = unique.indices.filter { area[$0] > 0 }.sorted { area[$0] > area[$1] }
    var sortMap = [Int16](repeating: -1, count: unique.count)
    for (newIndex, old) in order.enumerated() { sortMap[old] = Int16(newIndex) }
    for i in 0..<(w * h) where labels[i] >= 0 { labels[i] = sortMap[Int(labels[i])] }

    return Reduced(w: w, h: h, labels: labels, palette: order.map { unique[$0] })
}
