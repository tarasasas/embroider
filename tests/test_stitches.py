"""Measures the stitches the engine makes and checks them against the settings.

Every test draws a simple shape, converts it, then measures the real needle points: row spacing,
stitch length, satin spacing and width, underlay type, short stitches, e-stitch prongs, applique
stops, knockdown, split stitches, trims and file formats.

Run:  python tests/test_stitches.py      (prints each measurement, exits non-zero on a failure)
      python tests/test_stitches.py --rebaseline   (lock in the current bear as the new best, after a better sew-out)
"""

import math
import os
import sys
import tempfile

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
import digitizer as dz  # noqa: E402
import pyembroidery  # noqa: E402
from pyembroidery import JUMP, STITCH, STOP, TRIM  # noqa: E402

RESULTS = []


def check(name, ok, detail):
    RESULTS.append((name, bool(ok)))
    print(f"{'PASS' if ok else 'FAIL'}  {name:42s} {detail}")


def shape(draw_fn, size=(600, 600)):
    """A black shape on white, drawn by draw_fn(ImageDraw)."""
    img = Image.new("RGB", size, "white")
    draw_fn(ImageDraw.Draw(img))
    return img.convert("RGBA")


def settings(**kw):
    s = dz.Settings(colors=1, underlay=False, auto_angle=False, fill_angle=0.0, pull_comp_mm=0.0,
                    cleanup=0, min_area_mm2=0.5)
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def stitches(pattern):
    """Needle points in mm as a list of (x, y, command)."""
    return [(x / 10, y / 10, c & 0xFF) for x, y, c in pattern.stitches]


def segments(pattern):
    """Stitch segments (x0, y0, x1, y1) in mm, only real stitches."""
    pts, out, prev = stitches(pattern), [], None
    for x, y, c in pts:
        if c == STITCH and prev is not None:
            out.append((prev[0], prev[1], x, y))
        prev = (x, y) if c in (STITCH, JUMP) else None if c in (TRIM, STOP) else prev
    return np.array(out) if out else np.zeros((0, 4))


def lengths(segs):
    return np.hypot(segs[:, 2] - segs[:, 0], segs[:, 3] - segs[:, 1])


def square(px=500):
    return shape(lambda d: d.rectangle((50, 50, 50 + px, 50 + px), fill="black"), (px + 100, px + 100))


# --------------------------------------------------------------------------- fills


def test_tatami_density_and_length():
    for spacing, stitch in ((0.24, 3.5), (0.40, 2.5)):
        r = dz.convert(square(), settings(width_mm=30, height_mm=30, fill_type="tatami",
                                          row_spacing_mm=spacing, stitch_length_mm=stitch))
        seg = segments(r.pattern)
        L = lengths(seg)
        along = seg[(np.abs(seg[:, 3] - seg[:, 1]) < 0.01) & (L > 0.5)]  # horizontal row stitches
        rows = np.unique(np.round(along[:, 1], 2))
        measured_spacing = float(np.median(np.diff(rows)))
        measured_len = float(np.median(lengths(along)))
        check(f"fill row spacing {spacing} mm", abs(measured_spacing - spacing) < 0.06 * spacing,
              f"measured {measured_spacing:.3f} mm")
        check(f"fill stitch length {stitch} mm", abs(measured_len - stitch) < 0.12 * stitch,
              f"median {measured_len:.2f} mm")


def test_fill_patterns():
    expected = {"brick2": 2, "brick3": 3, "brick4": 4, "diagonal": 5}
    for pattern, n_offsets in list(expected.items()) + [("random", None)]:
        r = dz.convert(square(), settings(width_mm=30, height_mm=30, fill_type="tatami",
                                          row_spacing_mm=0.4, stitch_length_mm=3.0, fill_pattern=pattern))
        pts = [(x, y) for x, y, c in stitches(r.pattern) if c == STITCH]
        by_row = {}
        for x, y in pts:
            by_row.setdefault(round(y, 2), []).append(x)
        phases = set()
        for xs in by_row.values():
            xs = sorted(xs)[1:-1]  # inner needle points (row ends sit on the edge)
            if len(xs) >= 3:
                phases.add(round((xs[1] % 3.0) / 3.0, 1) % 1.0)
        if n_offsets:
            check(f"fill pattern {pattern}", len(phases) == n_offsets, f"{len(phases)} different row offsets")
        else:
            check("fill pattern random", len(phases) >= 6, f"{len(phases)} different row offsets")


def test_pull_compensation():
    for pull in (0.0, 0.3):
        r = dz.convert(square(), settings(width_mm=30, height_mm=30, fill_type="tatami", pull_comp_mm=pull))
        xs = [x for x, y, c in stitches(r.pattern) if c == STITCH]
        width = max(xs) - min(xs)
        check(f"pull compensation {pull} mm", abs(width - (30 + 2 * pull)) < 0.25, f"fill is {width:.2f} mm wide")


def test_fill_underlay():
    plain = dz.convert(square(), settings(width_mm=30, height_mm=30, fill_type="tatami"))
    under = dz.convert(square(), settings(width_mm=30, height_mm=30, fill_type="tatami", underlay=True))
    seg = segments(under.pattern)
    L = lengths(seg)
    vertical = seg[(np.abs(seg[:, 2] - seg[:, 0]) < 0.05) & (L > 1.0)]  # rows at 90 degrees to the 0-degree top
    xs = np.unique(np.round(vertical[:, 0], 1)) if len(vertical) else np.array([])
    spacing = float(np.median(np.diff(xs))) if len(xs) > 2 else 0
    check("fill underlay: 90-degree rows ~1.0 mm apart (Brother)", len(vertical) > 20 and abs(spacing - 1.0) < 0.12,
          f"{len(vertical)} underlay stitches, rows {spacing:.2f} mm apart")


# --------------------------------------------------------------------------- satin


def bar(width_px, length_px=500):
    return shape(lambda d: d.rectangle((50, 50, 50 + length_px, 50 + width_px), fill="black"),
                 (length_px + 100, width_px + 100))


def satin_of(width_mm, **kw):
    length_mm = 40.0
    img = bar(int(round(500 * width_mm / length_mm)))
    kw.setdefault("fill_type", "auto")
    return dz.convert(img, settings(width_mm=length_mm, height_mm=length_mm, **kw))


def top_satin(seg, width_mm):
    L = lengths(seg)
    return seg[(np.abs(seg[:, 3] - seg[:, 1]) > 0.8 * width_mm) & (L < 1.6 * width_mm)]


def test_satin_spacing_and_width():
    for spacing in (0.4, 0.3):
        r = satin_of(3.0, satin_spacing_mm=spacing, pull_comp_mm=0.2)
        seg = top_satin(segments(r.pattern), 3.0)
        ends = seg[:, 2]  # x of each stitch's end; same-side ends are every other one
        pitch = float(np.median(np.abs(np.diff(ends[::2]))))
        width = float(np.median(np.abs(seg[:, 3] - seg[:, 1])))
        check(f"satin spacing {spacing} mm", abs(pitch - spacing) < 0.1 * spacing, f"same-side pitch {pitch:.3f} mm")
        check(f"satin width 3.0 + pull 0.2x2", abs(width - 3.4) < 0.25, f"stitches {width:.2f} mm across")


def test_satin_underlay_by_width():
    for width_mm, kind in ((2.0, "centre run"), (3.2, "edge run"), (5.0, "edge run + zigzag")):
        r = satin_of(width_mm, underlay=True)
        seg = segments(r.pattern)
        ys = [y for x, y, c in stitches(r.pattern) if c == STITCH]
        mid = (max(ys) + min(ys)) / 2
        L = lengths(seg)
        along = seg[(np.abs(seg[:, 3] - seg[:, 1]) < 0.3 * np.maximum(L, 1e-9)) & (L > 1.0)]
        offs = np.abs((along[:, 1] + along[:, 3]) / 2 - mid)
        centre = int((offs < 0.3).sum())
        edge = int((offs > max(0.5, width_mm / 2 - 1.2)).sum())  # edge runs sit up to 20% of the width inside
        diag = seg[(np.abs(seg[:, 2] - seg[:, 0]) > 0.8) & (np.abs(seg[:, 3] - seg[:, 1]) > 0.5 * width_mm)
                   & (L < 1.2 * width_mm)]
        if kind == "centre run":
            ok = centre > 5 and edge == 0
        elif kind == "edge run":
            ok = edge > 5 and centre == 0 and len(diag) < 3
        else:
            ok = edge > 5 and len(diag) > 5
        check(f"satin underlay {width_mm} mm -> {kind}", ok,
              f"centre-run {centre}, edge-run {edge}, zigzag {len(diag)} stitches")


def test_short_stitches():
    # a tight ring: the inside edge is much shorter than the outside, so satin crowds there
    img = shape(lambda d: d.ellipse((50, 50, 550, 550), outline="black", width=160))
    s = settings(width_mm=20, height_mm=20, fill_type="auto", satin_max_mm=7.0)
    r = dz.convert(img, s)
    pts = np.array([(x, y) for x, y, c in stitches(r.pattern) if c == STITCH])
    c = (pts.max(axis=0) + pts.min(axis=0)) / 2
    rad = np.hypot(*(pts - c).T)
    outer_r, inner_r = rad.max(), np.percentile(rad, 1)
    on_inner = int((rad < inner_r + 0.25).sum())
    on_outer = int((rad > outer_r - 0.25).sum())
    short = int(((rad > inner_r + 0.6) & (rad < inner_r + 0.35 * (outer_r - inner_r) + 0.3)).sum())
    check("satin short stitches on tight curves", short > 10 and on_inner < on_outer,
          f"{on_inner} needle points on the inner edge, {on_outer} on the outer, {short} short stitches")


def test_split_long_satin():
    r = satin_of(10.0, fill_type="satin")
    seg = segments(r.pattern)
    L = lengths(seg)
    check("long satin stitches split (max 7 mm)", L.max() <= 7.05, f"longest stitch {L.max():.2f} mm")
    pts = np.array([(x, y) for x, y, c in stitches(r.pattern) if c == STITCH])
    ys = pts[:, 1]
    inner = ys[(ys > ys.min() + 1) & (ys < ys.max() - 1)]
    spread = float(np.ptp(inner)) if inner.size else 0.0
    check("split points staggered", spread > 0.8, f"split points spread over {spread:.2f} mm")


# --------------------------------------------------------------------------- lines, applique, knockdown


def test_estitch_edge():
    r = dz.convert(square(), settings(width_mm=30, height_mm=30, fill_type="tatami", edge="estitch",
                                      edge_width_mm=1.5))
    pts = stitches(r.pattern)
    xs = [x for x, y, c in pts if c == STITCH]
    x0, x1 = min(xs), max(xs)
    tips = [(x, y) for x, y, c in pts if c == STITCH]
    seg = segments(r.pattern)
    prongs = seg[np.abs(lengths(seg) - 1.5) < 0.1]
    inward = sum(1 for a in prongs if x0 - 0.05 <= a[2] <= x1 + 0.05)
    check("e-stitch prongs 1.5 mm, pointing inward", len(prongs) > 30 and inward == len(prongs),
          f"{len(prongs)} prongs, {inward} inside the shape")


def test_applique():
    img = square()
    s = settings(width_mm=30, height_mm=30, applique={(0, 0, 0)})
    r = dz.convert(img, s)
    # applique keys are the palette colors; find the one actually used and mark it
    key = r.threads[0]["keys"][0]
    r = dz.convert(img, settings(width_mm=30, height_mm=30, applique={key}))
    with tempfile.TemporaryDirectory() as tmp:  # the pauses must survive in the saved file
        back = pyembroidery.read(dz.save(r.pattern, os.path.join(tmp, "a.pes")))
    # same-color thread changes make the machine pause; pyembroidery reads them back as STOP
    stops = sum(1 for x, y, c in back.stitches if c & 0xFF in (pyembroidery.COLOR_CHANGE, STOP))
    pts = np.array([(x, y) for x, y, c in stitches(r.pattern) if c == STITCH])
    c = (pts.max(axis=0) + pts.min(axis=0)) / 2
    interior = int((np.abs(pts - c).max(axis=1) < 10).sum())  # well inside: no fill there
    check("applique: placement, 2 stops, tack-down, cover", stops == 2 and interior == 0,
          f"{stops} machine stops in the saved .pes, {interior} stitches inside the fabric area")


def test_knockdown():
    img = square()
    base = dz.convert(img, settings(width_mm=30, height_mm=30, fill_type="tatami"))
    kd = dz.convert(img, settings(width_mm=30, height_mm=30, fill_type="tatami", knockdown=True))
    xs = [x for x, y, c in stitches(kd.pattern) if c == STITCH]
    bx = [x for x, y, c in stitches(base.pattern) if c == STITCH]
    grows = (max(xs) - min(xs)) - (max(bx) - min(bx))
    check("knockdown: open cross-hatch 1 mm past the design", 1.6 < grows < 2.4 and
          len(xs) > len(bx), f"reaches {grows / 2:.2f} mm further, +{len(xs) - len(bx)} stitches")


# --------------------------------------------------------------------------- whole designs


def test_colors_tuck_under():
    """A color sewn first carries on under the color sewn on top of it (no gap between colors)."""
    img = shape(lambda d: (d.rectangle((50, 50, 550, 550), fill=(200, 30, 30)),
                           d.rectangle((250, 250, 350, 350), fill=(30, 30, 200))))
    r = dz.convert(img, dz.Settings(width_mm=40, height_mm=40, colors=0, underlay=False))
    blocks, cur = [], []
    for x, y, c in r.pattern.stitches:
        c &= 0xFF
        if c == pyembroidery.COLOR_CHANGE:
            blocks.append(cur)
            cur = []
        elif c == STITCH:
            cur.append((x / 10, y / 10))
    blocks.append(cur)
    red, blue = np.array(blocks[0]), np.array(blocks[1])
    cx, cy = blue.mean(axis=0)
    half = (blue[:, 0].max() - blue[:, 0].min()) / 2
    inside = red[(np.abs(red[:, 0] - cx) < half - 0.5) & (np.abs(red[:, 1] - cy) < half - 0.5)]
    reach = half - float(np.min(np.maximum(np.abs(inside[:, 0] - cx), np.abs(inside[:, 1] - cy)))) if len(inside) else 0
    check("base color tucks ~2 mm under the color on top", 1.5 < reach < 2.6, f"red reaches {reach:.2f} mm under the blue")


def test_whole_designs():
    img = shape(lambda d: (d.ellipse((60, 60, 540, 540), fill=(200, 30, 30)),
                           d.rectangle((220, 220, 380, 380), fill=(30, 30, 200)),
                           d.line((80, 570, 520, 570), fill=(0, 140, 0), width=14)), (600, 620))
    s = dz.Settings(width_mm=80, height_mm=80, colors=0)
    r = dz.convert(img, s)
    seg = segments(r.pattern)
    L = lengths(seg)
    pts = stitches(r.pattern)
    long_jumps = 0
    prev, trimmed = None, False
    for x, y, c in pts:
        if c == TRIM:
            trimmed = True
        if c == JUMP and prev is not None and math.dist(prev, (x, y)) > s.trim_mm and not trimmed:
            long_jumps += 1
        if c == STITCH:
            trimmed = False
        if c in (STITCH, JUMP):
            prev = (x, y)
    check("no stitch over 7 mm", L.max() <= 7.05, f"longest {L.max():.2f} mm")
    check("every jump over 1 mm is trimmed", long_jumps == 0, f"{long_jumps} untrimmed jumps")
    check("auto colors found 3 threads", len(r.pattern.threadlist) == 3,
          ", ".join(t["name"] for t in r.info["threads"]))
    with tempfile.TemporaryDirectory() as tmp:
        sizes = {}
        for ext in (".pes", ".dst", ".jef", ".exp", ".vp3"):
            path = dz.save(r.pattern, os.path.join(tmp, "t" + ext))
            back = pyembroidery.read(path)
            sizes[ext] = len(back.stitches) if back else 0
        check("writes and reads back 5 machine formats", all(v > 100 for v in sizes.values()),
              " ".join(f"{k}:{v}" for k, v in sizes.items()))


def test_coverage_no_gaps():
    """No fabric may show inside the artwork: draw every stitch at thread width and look for holes."""
    import coverage as cov
    font = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "arialbd.ttf")
    designs = {
        "line art (strokes meeting fills)": shape(lambda d: (d.ellipse((40, 40, 560, 560), outline="black", width=60),
                                                             d.rectangle((200, 200, 400, 400), fill="black"),
                                                             d.line((300, 100, 300, 500), fill="black", width=30))),
        "lettering": dz.make_text_image("Bears 23", font),
    }
    vector = os.path.join(os.path.dirname(__file__), "..", "source_images", "bear_head_black.png")
    if os.path.exists(vector):
        designs["bear logo"] = dz.load_image(vector)
    for name, img in designs.items():
        s = dz.Settings(width_mm=400, height_mm=63.5, colors=0)
        for k, v in dz.FABRICS["T-shirt / knit"][0].items():
            setattr(s, k, v)
        art, pattern, center = cov.art_and_result(img, s)
        share, _ = cov.coverage(art, pattern, center)
        check(f"coverage: {name}", share < 0.006, f"{100 * share:.2f}% of the art without thread")
        # and the reverse: no stitch may run across bare fabric (shows as a stray black strand)
        import shapely
        inside = art.buffer(s.pull_comp_mm + 0.15)  # shapes are sewn bigger by the pull compensation on purpose
        shapely.prepare(inside)
        prev, worst, count = None, 0.0, 0
        for x, y, c in pattern.stitches:
            c &= 0xFF
            q = (x / 10 + center[0], y / 10 + center[1])
            if c == STITCH and prev is not None and math.dist(prev, q) > 0.5:
                n = max(2, int(math.dist(prev, q) / 0.2))
                t = np.linspace(0, 1, n)
                out = ~shapely.contains_xy(inside, prev[0] + (q[0] - prev[0]) * t, prev[1] + (q[1] - prev[1]) * t)
                over = float(out.mean()) * math.dist(prev, q)
                if over > 0.3:
                    count += 1
                worst = max(worst, over)
            if c in (STITCH, JUMP):
                prev = q
        check(f"no stray strands across fabric: {name}", count == 0,
              f"{count} stitches cross bare fabric (worst {worst:.2f} mm)")


BASELINE = os.path.join(os.path.dirname(__file__), "baseline_bear_v8.json")
BEAR_SOURCE = os.path.join(os.path.dirname(__file__), "..", "source_images", "bear_original.jpg")


def measure_bear_v8():
    """The bear that sewed out best (v8 D: original JPEG art, 2.5 in, T-shirt settings), measured."""
    import json
    import shapely
    import coverage as cov
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
    import analyze_pes
    s = dz.Settings(width_mm=400, height_mm=63.5, colors=0, brother_colors=True)
    for k, v in dz.FABRICS["T-shirt / knit"][0].items():
        setattr(s, k, v)
    art, pattern, center = cov.art_and_result(dz.load_image(BEAR_SOURCE), s)
    share, _ = cov.coverage(art, pattern, center)
    inside = art.buffer(s.pull_comp_mm + 0.15)
    shapely.prepare(inside)
    prev, strays = None, 0
    for x, y, c in pattern.stitches:
        c &= 0xFF
        q = (x / 10 + center[0], y / 10 + center[1])
        if c == STITCH and prev is not None and math.dist(prev, q) > 0.5:
            t = np.linspace(0, 1, max(2, int(math.dist(prev, q) / 0.2)))
            out = ~shapely.contains_xy(inside, prev[0] + (q[0] - prev[0]) * t, prev[1] + (q[1] - prev[1]) * t)
            strays += float(out.mean()) * math.dist(prev, q) > 0.3
        if c in (STITCH, JUMP):
            prev = q
    info = dz.describe(pattern)
    with tempfile.TemporaryDirectory() as tmp:
        rep = analyze_pes.analyze(dz.save(pattern, os.path.join(tmp, "b.pes")))
    return {"height_in": round(info["height_mm"] / 25.4, 3), "stitches": info["stitches"], "trims": info["trims"],
            "uncovered_pct": round(100 * share, 3), "strays": int(strays),
            "fill_spacing_mm": round(rep["fill_spacing"], 3), "underlay_spacing_mm": round(rep["underlay_spacing"], 3)}


def test_baseline_bear_v8():
    """Locked baseline: changes must not make the best sew-out (v8 D) worse."""
    import json
    if not (os.path.exists(BEAR_SOURCE) and os.path.exists(BASELINE)):
        print("SKIP  baseline bear v8 (source image not on this computer)")
        return
    base, now = json.load(open(BASELINE)), measure_bear_v8()
    rules = [
        ("size stays 2.5 in", abs(now["height_in"] - base["height_in"]) <= 0.03, f"{now['height_in']} in"),
        ("density (stitch count) within 12%", abs(now["stitches"] - base["stitches"]) <= 0.12 * base["stitches"],
         f"{now['stitches']:,} vs {base['stitches']:,}"),
        ("trims no more than +5", now["trims"] <= base["trims"] + 5, f"{now['trims']} vs {base['trims']}"),
        ("uncovered art no worse", now["uncovered_pct"] <= base["uncovered_pct"] + 0.15,
         f"{now['uncovered_pct']}% vs {base['uncovered_pct']}%"),
        ("stray strands no more", now["strays"] <= base["strays"], f"{now['strays']} vs {base['strays']}"),
        ("fill rows within 0.02 mm", abs(now["fill_spacing_mm"] - base["fill_spacing_mm"]) <= 0.02,
         f"{now['fill_spacing_mm']} vs {base['fill_spacing_mm']} mm"),
        ("underlay rows within 0.1 mm", abs(now["underlay_spacing_mm"] - base["underlay_spacing_mm"]) <= 0.1,
         f"{now['underlay_spacing_mm']} vs {base['underlay_spacing_mm']} mm"),
    ]
    for name, ok, detail in rules:
        check(f"baseline v8 bear: {name}", ok, detail)


if __name__ == "__main__":
    if "--rebaseline" in sys.argv:  # after a sew-out that's better still: lock the new result in
        import json
        json.dump(measure_bear_v8(), open(BASELINE, "w"), indent=1)
        print("baseline updated:", open(BASELINE).read())
        sys.exit(0)
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as exc:  # a crash is a failure too
                check(name, False, f"crashed: {type(exc).__name__}: {exc}")
    failed = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)} of {len(RESULTS)} checks passed")
    sys.exit(1 if failed else 0)
