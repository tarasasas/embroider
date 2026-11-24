"""Realistic stitch rendering: thread with shading, sheen and shadow on a knit fabric texture.

Each stitch is drawn as a small cylinder of thread: dark edges, a lighter middle and a highlight
whose strength depends on the stitch direction (thread catches the light most when it lies across
the light, like real embroidery sheen). A soft shadow on the fabric sits under every stitch, so
later stitches visibly lie on top of earlier ones.
"""

import math

import numpy as np
from PIL import Image, ImageDraw
from pyembroidery import COLOR_CHANGE, END, JUMP, STITCH, STOP, TRIM

THREAD_MM = 0.42  # sewn 40 wt thread
LIGHT = (-0.6, -0.8)  # light from the top left (screen coordinates, y down)
SHIRT_WHITE = (242, 240, 236)


def _mix(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _scale(c, f):
    return tuple(max(0, min(255, int(round(v * f)))) for v in c)


def fabric_texture(size, rgb=SHIRT_WHITE, px_per_mm=10.0, seed=7):
    """A knit (jersey) fabric: fine columns of V-shaped loops, plus a little noise."""
    w, h = size
    y, x = np.mgrid[0:h, 0:w].astype(np.float32) / px_per_mm
    wale = 0.62  # mm between loop columns
    course = 0.45  # mm between loop rows
    u = (x / wale) % 1.0
    v_shape = np.abs(u - 0.5)  # 0 in the middle of a column, 0.5 at its edges
    loops = np.cos(2 * np.pi * (y / course + v_shape))  # the V of each knit loop
    ribs = np.cos(2 * np.pi * x / wale)
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, 1, (h, w)).astype(np.float32)
    light = 1.0 + 0.035 * loops + 0.02 * ribs + 0.012 * noise
    base = np.array(rgb, np.float32)[None, None, :]
    return Image.fromarray(np.clip(base * light[..., None], 0, 255).astype(np.uint8), "RGB")


class ThreadPainter:
    """Draws stitches as shaded thread. Threads go on one layer and their shadows on another, so a
    shadow only darkens fabric that still shows, never the thread sewn just before (which in a
    dense fill would turn it gray). compose() puts the layers together."""

    def __init__(self, size, px_per_mm, fabric_rgb=SHIRT_WHITE, fabric=None):
        self.fabric = fabric if fabric is not None else fabric_texture(size, fabric_rgb, px_per_mm)
        self.threads = Image.new("RGBA", size, (0, 0, 0, 0))
        self.shadows = Image.new("L", size, 0)
        self.draw = ImageDraw.Draw(self.threads)
        self.shadow_draw = ImageDraw.Draw(self.shadows)
        self.w = max(2.0, THREAD_MM * px_per_mm)
        self.shadow_off = (0.07 * px_per_mm, 0.10 * px_per_mm)
        self._cache = {}

    def compose(self):
        """Fabric, darkened by the shadows, with the threads on top."""
        if getattr(self, "_dark", None) is None:  # computed once; compose() runs every animation frame
            self._dark = Image.eval(self.fabric, lambda v: int(v * 0.68))
        base = Image.composite(self._dark, self.fabric, self.shadows)
        base = base.convert("RGBA")
        base.alpha_composite(self.threads)
        return base.convert("RGB")

    def _shades(self, rgb, sheen):
        key = (rgb, round(sheen, 1))
        if key not in self._cache:
            lum = (0.3 * rgb[0] + 0.59 * rgb[1] + 0.11 * rgb[2]) / 255
            edge = _scale(rgb, 0.62)
            mid = _mix(rgb, (255, 255, 255), 0.03 + 0.06 * sheen)
            # dark threads need a stronger highlight to look like thread at all
            gloss = _mix(rgb, (255, 255, 255), 0.06 + (0.16 + 0.22 * lum) * sheen)
            self._cache[key] = (edge, mid, gloss)
        return self._cache[key]

    def stitch(self, a, b, rgb):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        if length < 0.5:
            return
        ux, uy = dx / length, dy / length
        sheen = abs(ux * LIGHT[1] - uy * LIGHT[0])  # strongest across the light
        edge, mid, gloss = self._shades(tuple(rgb), sheen)
        w, d = self.w, self.draw
        sx, sy = self.shadow_off
        self.shadow_draw.line((a[0] + sx, a[1] + sy, b[0] + sx, b[1] + sy), fill=255, width=max(1, round(w * 1.15)))
        d.line((a[0], a[1], b[0], b[1]), fill=edge, width=max(1, round(w)))
        r = w / 2
        for px, py in (a, b):  # rounded ends where the thread goes into the fabric
            d.ellipse((px - r, py - r, px + r, py + r), fill=edge)
        # rounded middle and a centred highlight (centred, so the look doesn't depend on which
        # way neighbouring rows happened to be sewn)
        d.line((a[0], a[1], b[0], b[1]), fill=mid, width=max(1, round(w * 0.6)))
        shrink = min(0.3, r / max(length, 1e-6))  # keep the highlight off the thread ends
        d.line((a[0] + dx * shrink, a[1] + dy * shrink, b[0] - dx * shrink, b[1] - dy * shrink),
               fill=gloss, width=max(1, round(w * 0.24)))


def render(pattern, max_px=1400, hoop_mm=None, fabric_rgb=SHIRT_WHITE):
    """The whole design as it will look sewn, on a knit fabric. 2x supersampled."""
    min_x, min_y, max_x, max_y = pattern.bounds()
    if hoop_mm:
        hw, hh = hoop_mm[0] * 10 / 2, hoop_mm[1] * 10 / 2
        min_x, min_y, max_x, max_y = min(min_x, -hw), min(min_y, -hh), max(max_x, hw), max(max_y, hh)
    w_mm, h_mm = (max_x - min_x) / 10, (max_y - min_y) / 10
    ppm = max(4.0, min(16.0, max_px / max(w_mm, h_mm, 1)))
    ss, margin = 2, 4
    px = ppm * ss  # supersampled pixels per mm
    size = (int((w_mm + 2 * margin) * px), int((h_mm + 2 * margin) * px))
    painter = ThreadPainter(size, px, fabric_rgb)
    ox, oy = margin * px - min_x / 10 * px, margin * px - min_y / 10 * px

    def to_px(x, y):
        return (x / 10 * px + ox, y / 10 * px + oy)

    if hoop_mm:
        line = _mix(fabric_rgb, (0, 0, 0), 0.25)
        ImageDraw.Draw(painter.fabric).rounded_rectangle((*to_px(-hw, -hh), *to_px(hw, hh)), radius=4 * px,
                                                         outline=line, width=ss * 2)
    colors = [(t.get_red(), t.get_green(), t.get_blue()) for t in pattern.threadlist] or [(0, 0, 0)]
    ci, prev = 0, None
    for x, y, cmd in pattern.stitches:
        c = cmd & 0xFF
        p = to_px(x, y)
        if c == STITCH:
            if prev is not None:
                painter.stitch(prev, p, colors[min(ci, len(colors) - 1)])
            prev = p
        elif c == JUMP:
            prev = p
        elif c in (TRIM, STOP):
            prev = None
        elif c == COLOR_CHANGE:
            ci += 1
            prev = None
        elif c == END:
            break
    return painter.compose().resize((size[0] // ss, size[1] // ss), Image.LANCZOS)
