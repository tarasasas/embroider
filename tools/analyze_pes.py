"""Takes an embroidery file apart and measures how it was digitized.

Splits every color into passes (fill, sparse fill = underlay, satin, running) and reports:
fill row spacing / stitch length / angle, the underlay under each fill and satin, satin width,
spacing and short stitches, overlap between neighbouring pieces, lock stitches and trims.

Run:  python tools/analyze_pes.py FILE.pes [FILE2.pes ...]
"""

import math
import os
import sys
from collections import Counter

import numpy as np
import pyembroidery
from PIL import Image, ImageDraw, ImageFilter
from pyembroidery import COLOR_CHANGE, END, JUMP, STITCH, STOP, TRIM

PX = 10  # pixels per mm for overlap measurements


def blocks_of(pattern):
    """[(thread rgb, [points])] where points are (x_mm, y_mm, kind) with kind S(titch)/J(ump)/T(rim)."""
    blocks, cur = [], []
    colors = [(t.get_red(), t.get_green(), t.get_blue()) for t in pattern.threadlist] or [(0, 0, 0)]
    for x, y, c in pattern.stitches:
        c &= 0xFF
        if c in (COLOR_CHANGE, STOP):
            blocks.append(cur)
            cur = []
        elif c == END:
            break
        elif c == STITCH:
            cur.append((x / 10, y / 10, "S"))
        elif c == JUMP:
            cur.append((x / 10, y / 10, "J"))
        elif c == TRIM:
            cur.append((x / 10, y / 10, "T"))
    blocks.append(cur)
    blocks = [b for b in blocks if any(k == "S" for _, _, k in b)]
    return [(colors[min(i, len(colors) - 1)], b) for i, b in enumerate(blocks)]


def segments(pts):
    seg = []
    for (x0, y0, _), (x1, y1, k) in zip(pts, pts[1:]):
        L = math.hypot(x1 - x0, y1 - y0)
        seg.append(dict(a=(x0, y0), b=(x1, y1), L=L, u=((x1 - x0) / L, (y1 - y0) / L) if L else (0, 0), k=k))
    return seg


def _dot(s, t):
    return s["u"][0] * t["u"][0] + s["u"][1] * t["u"][1]


def objects_in(seg):
    """Splits one color's stitches into objects: fill (rows with turn-arounds), satin (zigzag) or run.

    A fill row is a chain of long collinear stitches; between rows the needle takes one or two short
    turn stitches and the next row runs back the other way. Row spacing is the perpendicular
    distance between consecutive rows."""
    out, i, n = [], 0, len(seg)
    while i < n:
        if seg[i]["k"] != "S":
            i += 1
            continue
        # satin: every stitch reverses the previous one
        j = i + 1
        while j < n and seg[j]["k"] == "S" and seg[j]["L"] > 0.6 and _dot(seg[j], seg[j - 1]) < -0.8:
            j += 1
        if j - i >= 8 and seg[i]["L"] > 0.6:
            out.append({"type": "satin", "idx": list(range(i, j))})
            i = j
            continue
        # fill: rows of collinear long stitches joined by short turns, alternating direction
        rows, k = [], i
        while k < n and seg[k]["k"] == "S":
            if seg[k]["L"] < 0.8:
                break
            r0, axis = k, seg[k]
            while k < n and seg[k]["k"] == "S" and seg[k]["L"] >= 0.8 and _dot(seg[k], axis) > 0.985:
                k += 1
            rows.append((r0, k))
            t = k  # up to two short turn stitches
            while t < n and t - k < 2 and seg[t]["k"] == "S" and seg[t]["L"] < 1.5:
                t += 1
            if t >= n or seg[t]["k"] != "S" or seg[t]["L"] < 0.8 or _dot(seg[t], axis) > -0.97:
                break
            k = t
        if len(rows) >= 4:
            end = rows[-1][1]
            out.append({"type": "row", "idx": list(range(i, end)), "rows": rows})
            i = end
            continue
        # running stitch until something else starts
        j = i + 1
        while j < n and seg[j]["k"] == "S" and not (seg[j]["L"] > 0.6 and j + 1 < n and _dot(seg[j], seg[j - 1]) < -0.8
                                                   and _dot(seg[j + 1], seg[j]) < -0.8):
            if seg[j]["L"] >= 0.8 and j + 1 < n and _dot(seg[j + 1], seg[j]) > 0.985 and seg[j + 1]["L"] >= 0.8:
                # could be the start of a fill: stop the run here if enough rows follow
                break
            j += 1
        out.append({"type": "run", "idx": list(range(i, max(j, i + 1)))})
        i = max(j, i + 1)
    return out


def describe_pass(p, seg):
    S = [seg[i] for i in p["idx"]]
    L = np.array([s["L"] for s in S])
    d = {"type": p["type"], "n": len(S)}
    if p["type"] == "row":
        rows = p["rows"]
        ang = np.array([math.degrees(math.atan2(-seg[r0]["u"][1], seg[r0]["u"][0])) % 180 for r0, _ in rows])
        spac = []
        for (a0, a1), (c0, c1) in zip(rows, rows[1:]):
            A, C = seg[a0], seg[c0]
            nx, ny = -A["u"][1], A["u"][0]
            mid_a = ((seg[a0]["a"][0] + seg[a1 - 1]["b"][0]) / 2, (seg[a0]["a"][1] + seg[a1 - 1]["b"][1]) / 2)
            mid_c = ((seg[c0]["a"][0] + seg[c1 - 1]["b"][0]) / 2, (seg[c0]["a"][1] + seg[c1 - 1]["b"][1]) / 2)
            spac.append(abs((mid_c[0] - mid_a[0]) * nx + (mid_c[1] - mid_a[1]) * ny))
        inrow = [seg[q]["L"] for r0, r1 in rows for q in range(r0, r1)]
        inner = [seg[q]["L"] for r0, r1 in rows for q in range(r0 + 1, r1 - 1)] or inrow  # skip the cut row ends
        d.update(len=float(np.median(inner)), angle=float(np.median(ang)), spacing=float(np.median(spac)),
                 turns=len(rows) - 1)
    elif p["type"] == "satin":
        pitch = [math.dist(S[k]["b"], S[k - 2]["b"]) for k in range(2, len(S))]
        short = sum(1 for k in range(1, len(S) - 1) if S[k]["L"] < 0.75 * (S[k - 1]["L"] + S[k + 1]["L"]) / 2)
        d.update(width=float(np.median(L)), pitch=float(np.median(pitch)) if pitch else 0, short=short)
    else:
        d.update(len=float(np.median(L)) if len(L) else 0)
    d["pts"] = [S[0]["a"]] + [s["b"] for s in S]
    return d


def kind_of(d):
    """Names a pass the way digitizers do."""
    if d["type"] == "row":
        return "underlay fill" if d["spacing"] >= 0.8 else "fill"
    if d["type"] == "satin":
        return "satin" if d["n"] >= 8 else "zigzag"
    return "run"


def coverage_mask(pts, bounds, width_mm=0.4):
    x0, y0, x1, y1 = bounds
    w, h = int((x1 - x0 + 2) * PX), int((y1 - y0 + 2) * PX)
    img = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(img)
    xy = [((x - x0 + 1) * PX, (y - y0 + 1) * PX) for x, y in pts]
    if len(xy) > 1:
        d.line(xy, fill=255, width=max(1, round(width_mm * PX)))
    return np.asarray(img) > 0


def analyze(path, verbose=False):
    pattern = pyembroidery.read(path)
    bx0, by0, bx1, by1 = (v / 10 for v in pattern.bounds())
    bounds = (bx0, by0, bx1, by1)
    report = {"file": os.path.basename(path), "size_in": ((bx1 - bx0) / 25.4, (by1 - by0) / 25.4)}
    objects, lock_starts, trims = [], Counter(), 0
    for ci, (rgb, pts) in enumerate(blocks_of(pattern)):
        trims += sum(1 for _, _, k in pts if k == "T")
        seg = segments(pts)
        # how each sewing run starts: the first few stitch lengths after a jump/trim
        for i, s in enumerate(seg):
            if s["k"] == "S" and (i == 0 or seg[i - 1]["k"] != "S"):
                first = [round(seg[j]["L"], 1) for j in range(i, min(i + 4, len(seg))) if seg[j]["k"] == "S"]
                tiny = sum(1 for v in first if v < 0.9)
                lock_starts["short starting stitches (lock)" if tiny >= 2 else "no lock at start"] += 1
        for p in objects_in(seg):
            if len(p["idx"]) < 4:
                continue
            d = describe_pass(p, seg)
            d["kind"], d["color"], d["rgb"] = kind_of(d), ci, rgb
            objects.append(d)

    # underlay under each top object: what was sewn in the same color just before it
    for i, d in enumerate(objects):
        if d["kind"] not in ("fill", "satin"):
            continue
        before = []
        for e in reversed(objects[:i]):
            if e["color"] != d["color"] or e["kind"] in ("fill", "satin"):
                break
            before.append(e["kind"])
        d["underlay"] = "+".join(sorted(set(before))) or "none"

    # overlap where top objects meet (raster masks at thread width)
    tops = [d for d in objects if d["kind"] in ("fill", "satin") and d["n"] >= 20]
    masks = [coverage_mask(d["pts"], bounds) for d in tops]
    overlaps = []
    for i in range(len(tops)):
        grown = np.asarray(Image.fromarray(masks[i].astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(7))) > 0
        for j in range(i + 1, len(tops)):
            touch = grown & masks[j]
            if touch.sum() < 30:
                continue
            inter = masks[i] & masks[j]
            # depth = overlapping area / length of the contact zone (in mm)
            contact_len = max(1.0, touch.sum() / PX / (6 / PX * PX))
            overlaps.append((tops[i]["kind"], tops[j]["kind"], tops[i]["color"] == tops[j]["color"],
                             inter.sum() / PX / PX / max(contact_len / PX * 1.0, 1e-6) if inter.sum() else 0.0))

    fills = [d for d in objects if d["kind"] == "fill"]
    unders = [d for d in objects if d["kind"] == "underlay fill"]
    satins = [d for d in objects if d["kind"] == "satin"]
    w = lambda ds, key: float(np.average([d[key] for d in ds], weights=[d["n"] for d in ds])) if ds else 0  # noqa: E731
    report.update(
        colors=len({d["color"] for d in objects}),
        stitches=sum(d["n"] for d in objects), trims=trims,
        share={k: round(100 * sum(d["n"] for d in objects if d["kind"] == k) / max(1, sum(d["n"] for d in objects)))
               for k in ("fill", "underlay fill", "satin", "zigzag", "run")},
        fill_spacing=w([f for f in fills if f["spacing"]], "spacing"), fill_len=w(fills, "len"),
        fill_angles=sorted({round(f["angle"] / 15) * 15 % 180 for f in fills if f["n"] > 40}),
        underlay_spacing=w([u for u in unders if u["spacing"]], "spacing"), underlay_len=w(unders, "len"),
        underlay_vs_top=[round(((u2["angle"] - f["angle"] + 90) % 180) - 90) for f in fills if f["n"] > 100
                         for u2 in [max((u for u in unders if u["color"] == f["color"]), key=lambda u: u["n"], default=None)]
                         if u2],
        fill_underlay=Counter(f["underlay"] for f in fills if f["n"] > 40),
        satin_width=w(satins, "width"), satin_pitch=w(satins, "pitch"),
        satin_short=sum(s["short"] for s in satins), satin_stitches=sum(s["n"] for s in satins),
        satin_underlay=Counter(s["underlay"] for s in satins),
        overlap_same=float(np.median([o[3] for o in overlaps if o[2]])) if any(o[2] for o in overlaps) else None,
        overlap_diff=float(np.median([o[3] for o in overlaps if not o[2]])) if any(not o[2] for o in overlaps) else None,
        locks=dict(lock_starts),
    )
    return report


def print_report(r):
    print(f"\n== {r['file']}: {r['size_in'][0]:.2f} x {r['size_in'][1]:.2f} in, {r['colors']} colors, "
          f"{r['stitches']:,} stitches, {r['trims']} trims")
    print(f"   stitch share %: " + ", ".join(f"{k} {v}" for k, v in r["share"].items() if v))
    print(f"   fill: rows {r['fill_spacing']:.2f} mm, stitch {r['fill_len']:.1f} mm, angles {r['fill_angles']}")
    print(f"   fill underlay: {dict(r['fill_underlay'])}; underlay rows {r['underlay_spacing']:.2f} mm, "
          f"stitch {r['underlay_len']:.1f} mm, angle vs top {r['underlay_vs_top'][:8]}")
    print(f"   satin: width {r['satin_width']:.2f} mm, same-side pitch {r['satin_pitch']:.2f} mm, "
          f"short stitches {r['satin_short']} of {r['satin_stitches']}, underlay {dict(r['satin_underlay'])}")
    fmt = lambda v: "n/a" if v is None else f"{v:.2f} mm"  # noqa: E731
    print(f"   overlap where pieces meet: same color {fmt(r['overlap_same'])}, different colors {fmt(r['overlap_diff'])}")
    print(f"   run starts: {r['locks']}")


if __name__ == "__main__":
    for f in sys.argv[1:]:
        print_report(analyze(f))
