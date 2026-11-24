"""Coverage check: which parts of the artwork does no thread cover?

Draws every stitch at real thread width over the artwork's shapes and reports the share of the art
left uncovered, plus a picture with uncovered spots in red.

Run:  python tests/coverage.py IMAGE [height_mm] [out.png]
"""

import math
import os
import sys

import numpy as np
from PIL import Image, ImageDraw
from shapely.ops import unary_union

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import digitizer as dz  # noqa: E402
from pyembroidery import JUMP, STITCH, STOP, TRIM, COLOR_CHANGE  # noqa: E402

THREAD_MM = 0.35  # a 40 wt thread lies about 0.35-0.4 mm wide once sewn
PX = 20  # pixels per mm for the check


def art_and_result(img, s):
    labels, palette, groups, _ = dz.reduce_colors(img, s)
    polys = []
    for ci in range(len(palette)):
        polys += dz._mask_polygons(labels == ci, s.min_area_mm2)
    pattern, _ = dz.digitize(labels, palette, s, groups)
    H, W = labels.shape
    return unary_union(polys), pattern, (W * dz.PX_MM / 2, H * dz.PX_MM / 2)


def coverage(art, pattern, center, thread_mm=THREAD_MM):
    """Returns (uncovered share of the art, picture). Spots smaller than a needle hole are ignored."""
    x0, y0, x1, y1 = art.bounds
    w, h = int((x1 - x0 + 4) * PX), int((y1 - y0 + 4) * PX)
    to_px = lambda x, y: ((x - x0 + 2) * PX, (y - y0 + 2) * PX)  # noqa: E731

    art_img = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(art_img)
    for poly in dz._polygons_of(art):
        d.polygon([to_px(*c) for c in poly.exterior.coords], fill=255)
        for hole in poly.interiors:
            d.polygon([to_px(*c) for c in hole.coords], fill=0)

    sewn = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(sewn)
    prev = None
    width = max(1, round(thread_mm * PX))
    for x, y, c in pattern.stitches:
        c &= 0xFF
        p = to_px(x / 10 + center[0], y / 10 + center[1])
        if c == STITCH and prev is not None:
            d.line([prev, p], fill=255, width=width)
            d.ellipse([p[0] - width / 2, p[1] - width / 2, p[0] + width / 2, p[1] + width / 2], fill=255)
        if c in (STITCH, JUMP):
            prev = p
        elif c in (TRIM, STOP, COLOR_CHANGE):
            prev = None

    a = np.asarray(art_img) > 128
    sw = np.asarray(sewn) > 128
    # ignore the outermost 0.15 mm of the art: edges are anti-aliased in the source
    inner = np.asarray(Image.fromarray(a.astype(np.uint8) * 255).filter(
        __import__("PIL.ImageFilter", fromlist=["MinFilter"]).MinFilter(2 * round(0.15 * PX) + 1))) > 128
    gap = inner & ~sw
    share = gap.sum() / max(1, inner.sum())

    pic = np.full((h, w, 3), 245, np.uint8)
    pic[a] = (205, 205, 205)
    pic[sw] = (40, 40, 40)
    pic[gap] = (230, 20, 20)
    return float(share), Image.fromarray(pic)


if __name__ == "__main__":
    path = sys.argv[1]
    height = float(sys.argv[2]) if len(sys.argv) > 2 else 63.5
    out = sys.argv[3] if len(sys.argv) > 3 else None
    s = dz.Settings(width_mm=400, height_mm=height, colors=0)
    for k, v in dz.FABRICS["T-shirt / knit"][0].items():
        setattr(s, k, v)
    art, pattern, center = art_and_result(dz.load_image(path), s)
    share, pic = coverage(art, pattern, center)
    print(f"{os.path.basename(path)}: {100 * share:.2f}% of the artwork has no thread on it")
    if out:
        pic.save(out)
