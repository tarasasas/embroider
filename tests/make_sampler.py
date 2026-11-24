"""Builds test_sews/stitch_sampler.pes: every stitch type and density on one 4x4 in hoop.

Layout (each tile is 18 mm, read like a book, top-left first):
  row 1  fill patterns:  brick3 (standard) | brick2 | diagonal | random
  row 2  fill density:   0.20 | 0.24 (default) | 0.30 | 0.40 mm rows
  row 3  satin:          2 mm (centre-run underlay) | 3.5 mm (edge run) | 5 mm (edge run + zigzag) | tight ring (short stitches)
  row 4  edges & more:   satin border | triple-run edge | satin lettering "Ab" | knockdown (towels)

Run:  python tests/make_sampler.py
"""

import os
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import digitizer as dz  # noqa: E402
from pyembroidery import COLOR_CHANGE, END, JUMP, STITCH, STOP, TRIM, EmbPattern, EmbThread  # noqa: E402

TILE = 18.0  # mm
PITCH = 22.0  # mm between tile centres


def canvas(draw_fn, w=600, h=600):
    img = Image.new("RGB", (w, h), "white")
    draw_fn(ImageDraw.Draw(img))
    return img.convert("RGBA")


SQUARE = canvas(lambda d: d.rectangle((50, 50, 550, 550), fill="black"))


def bar(width_mm):
    px = int(round(500 * width_mm / TILE))
    return canvas(lambda d: d.rectangle((50, 300 - px // 2, 550, 300 - px // 2 + px), fill="black"))


RING = canvas(lambda d: d.ellipse((50, 50, 550, 550), outline="black", width=140))


def base(**kw):
    s = dz.Settings(width_mm=TILE, height_mm=TILE, colors=1, brother_colors=True)
    for k, v in dz.FABRICS["T-shirt / knit"][0].items():
        setattr(s, k, v)
    for k, v in kw.items():
        setattr(s, k, v)
    return s


TILES = [
    # row 1: fill patterns
    (SQUARE, base(fill_type="tatami", fill_pattern="brick3")),
    (SQUARE, base(fill_type="tatami", fill_pattern="brick2")),
    (SQUARE, base(fill_type="tatami", fill_pattern="diagonal")),
    (SQUARE, base(fill_type="tatami", fill_pattern="random")),
    # row 2: fill density
    (SQUARE, base(fill_type="tatami", row_spacing_mm=0.20)),
    (SQUARE, base(fill_type="tatami", row_spacing_mm=0.24)),
    (SQUARE, base(fill_type="tatami", row_spacing_mm=0.30)),
    (SQUARE, base(fill_type="tatami", row_spacing_mm=0.40)),
    # row 3: satin columns
    (bar(2.0), base()),
    (bar(3.5), base()),
    (bar(5.0), base()),
    (RING, base(satin_max_mm=7.0)),
    # row 4: edges, lettering, knockdown
    (SQUARE, base(fill_type="tatami", edge="satin", edge_width_mm=1.5)),
    (SQUARE, base(fill_type="tatami", edge="triple")),
    (dz.make_text_image("Ab", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "arialbd.ttf")),
     base()),
    (SQUARE, base(fill_type="tatami", knockdown=True)),
]


def build():
    out = EmbPattern()
    thread = EmbThread()
    thread.set_color(0, 0, 0)
    thread.description = "Black"
    out.add_thread(thread)
    for n, (img, s) in enumerate(TILES):
        row, col = divmod(n, 4)
        ox = (col - 1.5) * PITCH * 10  # 0.1 mm units, tiles centred on the hoop
        oy = (row - 1.5) * PITCH * 10
        part = dz.convert(img, s).pattern
        if n:
            out.trim()
        for x, y, cmd in part.stitches:
            c = cmd & 0xFF
            if c == END:
                break
            if c == COLOR_CHANGE:
                continue
            if c in (STITCH, JUMP):
                out.add_stitch_absolute(c, x + ox, y + oy)
            elif c in (TRIM, STOP):
                out.add_command(c)
    out.trim()
    out.end()
    return out


if __name__ == "__main__":
    root = os.path.join(os.path.dirname(__file__), "..", "test_sews")
    os.makedirs(root, exist_ok=True)
    pattern = build()
    path = dz.save(pattern, os.path.join(root, "stitch_sampler.pes"))
    dz.render_preview(pattern, max_px=1400, hoop_mm=(100, 100)).save(os.path.join(root, "stitch_sampler_preview.png"))
    info = dz.describe(pattern)
    print(f"{path}: {info['width_mm'] / 25.4:.2f} x {info['height_mm'] / 25.4:.2f} in, "
          f"{info['stitches']:,} stitches, {info['trims']} trims, ~{info['minutes']:.0f} min")
