import Foundation

/// Shrinks a mask with a square of `radius` pixels (outside the image counts as empty).
func erode(_ mask: [Bool], w: Int, h: Int, radius r: Int) -> [Bool] {
    if r <= 0 { return mask }
    var horizontal = [Bool](repeating: false, count: w * h)
    for y in 0..<h {
        guard w > 2 * r else { continue }
        for x in r..<(w - r) {
            var all = true
            for d in -r...r where !mask[y * w + x + d] { all = false; break }
            horizontal[y * w + x] = all
        }
    }
    var out = [Bool](repeating: false, count: w * h)
    guard h > 2 * r else { return out }
    for y in r..<(h - r) {
        for x in 0..<w {
            var all = true
            for d in -r...r where !horizontal[(y + d) * w + x] { all = false; break }
            out[y * w + x] = all
        }
    }
    return out
}

struct Seg {
    var r: Int      // row index
    var t0: Double  // start along the row
    var t1: Double  // end along the row
}

struct ScanResult {
    var segs: [Seg]
    var ds: [Double]       // offset of each row across the lattice
    var gridRows: [Int]
    var cx: Double, cy: Double
    var ux: Double, uy: Double
    var nx: Double, ny: Double
}

/// Intersects parallel rows at `angle` degrees with a mask. Rows lie on a lattice anchored
/// at `center`, so neighbouring colors line up. Returns nil if the mask is empty.
func scan(mask: [Bool], w: Int, h: Int, center: Pt, angle: Double, spacing: Double, step: Double = 0.5) -> ScanResult? {
    let a = angle * Double.pi / 180
    let ux = cos(a), uy = -sin(a)   // along a row (image y axis points down)
    let nx = -uy, ny = ux           // across rows

    var minN = Double.infinity, maxN = -Double.infinity
    var minT = Double.infinity, maxT = -Double.infinity
    var any = false
    for y in 0..<h {
        for x in 0..<w where mask[y * w + x] {
            any = true
            let px = Double(x) + 0.5 - center.x, py = Double(y) + 0.5 - center.y
            let pn = px * nx + py * ny, pt = px * ux + py * uy
            minN = min(minN, pn); maxN = max(maxN, pn)
            minT = min(minT, pt); maxT = max(maxT, pt)
        }
    }
    guard any else { return nil }

    let first = Int(floor((minN - 1) / spacing)), last = Int(ceil((maxN + 1) / spacing))
    var gridRows: [Int] = []
    var ds: [Double] = []
    for g in first...last {
        gridRows.append(g)
        ds.append(Double(g) * spacing)
    }

    let tStart = minT - 1
    let steps = Int(ceil((maxT + 1 - tStart) / step))
    var segs: [Seg] = []
    for (ri, d) in ds.enumerated() {
        var inRun = false
        var runStart = 0.0, lastT = 0.0
        for i in 0..<steps {
            let t = tStart + Double(i) * step
            let X = Int(floor(center.x + d * nx + t * ux))
            let Y = Int(floor(center.y + d * ny + t * uy))
            let inside = X >= 0 && X < w && Y >= 0 && Y < h && mask[Y * w + X]
            if inside {
                if !inRun { inRun = true; runStart = t }
                lastT = t
            } else if inRun {
                segs.append(Seg(r: ri, t0: runStart - step / 2, t1: lastT + step / 2))
                inRun = false
            }
        }
        if inRun { segs.append(Seg(r: ri, t0: runStart - step / 2, t1: lastT + step / 2)) }
    }
    return ScanResult(segs: segs, ds: ds, gridRows: gridRows, cx: center.x, cy: center.y,
                      ux: ux, uy: uy, nx: nx, ny: ny)
}

/// Appends evenly spaced stitches from the last point to `target` (exclusive).
private func travel(_ pts: inout [Pt], to target: Pt, maxLen: Double) {
    guard let from = pts.last else { return }
    let dist = (target - from).x * (target - from).x + (target - from).y * (target - from).y
    let pieces = Int(ceil(dist.squareRoot() / maxLen))
    if pieces < 2 { return }
    for i in 1..<pieces {
        let f = Double(i) / Double(pieces)
        pts.append(from + (target - from) * f)
    }
}

/// Fills a mask with parallel rows sewn back and forth (tatami): needle points every
/// `stitch` along each row, staggered between rows. Returns runs of points sewn
/// continuously; consecutive runs are joined with a jump. `pen` tracks the needle.
func fill(mask: [Bool], w: Int, h: Int, center: Pt, angle: Double, spacing: Double, stitch: Double,
          pull: Double, minLen: Double, minArea: Double, pen: inout Pt?) -> [[Pt]] {
    guard let rows = scan(mask: mask, w: w, h: h, center: center, angle: angle, spacing: spacing) else { return [] }
    let segs = rows.segs.filter { $0.t1 - $0.t0 >= minLen }
    let n = segs.count
    if n == 0 { return [] }

    func point(_ r: Int, _ t: Double) -> Pt {
        let d = rows.ds[r]
        return Pt(rows.cx + d * rows.nx + t * rows.ux, rows.cy + d * rows.ny + t * rows.uy)
    }
    let end0 = segs.map { point($0.r, $0.t0) }
    let end1 = segs.map { point($0.r, $0.t1) }
    var byRow = [[Int]](repeating: [], count: rows.ds.count)
    for (i, s) in segs.enumerated() { byRow[s.r].append(i) }

    var visited = [Bool](repeating: false, count: n)
    var left = n
    let minPx = MIN_STITCH_MM / PX_MM
    var runs: [[Pt]] = []

    while left > 0 {
        // start the next run at the unvisited row end closest to the needle
        var cur = 0
        var fwd = true
        if let p = pen {
            var bestD = Double.infinity
            for i in 0..<n where !visited[i] {
                let d0 = distSq(end0[i], p), d1 = distSq(end1[i], p)
                if d0 < bestD { bestD = d0; cur = i; fwd = true }
                if d1 < bestD { bestD = d1; cur = i; fwd = false }
            }
        } else {
            cur = visited.firstIndex(of: false) ?? 0
        }

        // serpentine: keep stepping to an overlapping row segment next door
        var chain: [(Int, Bool)] = []
        var sweep = 1
        while true {
            visited[cur] = true
            left -= 1
            chain.append((cur, fwd))
            let endT = fwd ? segs[cur].t1 : segs[cur].t0
            var best: (cost: Double, index: Int)? = nil
            for dr in [sweep, -sweep] {
                let rr = segs[cur].r + dr
                if rr >= 0 && rr < byRow.count {
                    for j in byRow[rr] {
                        if visited[j] || segs[j].t0 >= segs[cur].t1 || segs[j].t1 <= segs[cur].t0 { continue }
                        let cost = min(abs(segs[j].t0 - endT), abs(segs[j].t1 - endT))
                        if best == nil || cost < best!.cost { best = (cost, j) }
                    }
                }
                if best != nil { sweep = dr; break }
            }
            guard let next = best else { break }
            cur = next.index
            fwd = abs(segs[cur].t0 - endT) <= abs(segs[cur].t1 - endT)
        }

        let (last, lastFwd) = chain[chain.count - 1]
        pen = lastFwd ? end1[last] : end0[last]
        var total = 0.0
        for (c, _) in chain { total += segs[c].t1 - segs[c].t0 }
        if total * spacing < minArea { continue }

        var pts: [Pt] = []
        for (idx, forward) in chain {
            let s = segs[idx]
            let t0 = s.t0 - pull, t1 = s.t1 + pull
            let phase = Double(((rows.gridRows[s.r] % 3) + 3) % 3) * stitch / 3.0  // brick offset hides row lines
            let k0 = Int(ceil((t0 + minPx - phase) / stitch))
            let k1 = Int(floor((t1 - minPx - phase) / stitch))
            var inner: [Double] = k0 <= k1 ? (k0...k1).map { phase + Double($0) * stitch } : []
            if !forward { inner.reverse() }
            let start = forward ? t0 : t1
            let end = forward ? t1 : t0
            let rowPts = ([start] + inner + [end]).map { point(s.r, $0) }
            travel(&pts, to: rowPts[0], maxLen: stitch)
            pts.append(contentsOf: rowPts)
        }
        runs.append(pts)
    }
    return runs
}
