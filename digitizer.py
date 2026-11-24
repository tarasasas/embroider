"""Image -> embroidery digitizer.

Turns a raster image (logo, clipart, drawing) or text into embroidery and writes a
Brother .PES file (or DST / JEF / EXP / VP3 for other machines).

Pipeline:
  1. scale the image to the requested size in millimetres
  2. reduce it to N thread colors (k-means in CIELAB), clean up specks, drop background
  3. trace every color into smooth vector shapes (shapely)
  4. wide shapes get tatami fill (edge-walk + cross underlay, rows along the shape);
     narrow strokes become satin columns that follow their centre line
  5. optional running / triple / satin edges and an outline around the whole design
  6. assemble stitches, jumps, trims and color changes with pyembroidery

Command line:
  python digitizer.py logo.png logo.pes --width 90 --height 90 --colors 4
  python digitizer.py "text:Anna" anna.pes --font "C:/Windows/Fonts/arialbd.ttf" --width 80
"""

from __future__ import annotations

import argparse
import math
from collections import deque
import os
from dataclasses import dataclass, field
from functools import lru_cache
from types import SimpleNamespace

import numpy as np
import shapely
from scipy.spatial import Voronoi, cKDTree
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
from pyembroidery import COLOR_CHANGE, END, JUMP, STITCH, TRIM, EmbPattern, EmbThread

PX_MM = 0.15  # working resolution: millimetres per pixel
UNITS_PER_MM = 10  # pyembroidery coordinates are 0.1 mm
MIN_STITCH_MM = 0.4  # shorter stitches just knot the thread

# Embroidery field sizes of common Brother hoops, in mm.
HOOPS = {
    "4 x 4 in (100 x 100 mm)": (100, 100),
    "5 x 7 in (130 x 180 mm)": (130, 180),
    "6 x 10 in (160 x 260 mm)": (160, 260),
    "8 x 8 in (200 x 200 mm)": (200, 200),
    "8 x 12 in (200 x 300 mm)": (200, 300),
    "2.5 x 2.5 in (60 x 60 mm)": (60, 60),
}

# Fabric presets: settings to apply, and a stabilizer tip.
FABRICS = {
    "Woven cotton": ({"row_spacing_mm": 0.24, "satin_spacing_mm": 0.4, "pull_comp_mm": 0.15, "underlay": True},
                     "Medium tear-away stabilizer."),
    "T-shirt / knit": ({"row_spacing_mm": 0.25, "satin_spacing_mm": 0.4, "pull_comp_mm": 0.25, "underlay": True},
                       "Cut-away stabilizer, so the knit can't stretch while it is sewn."),
    "Denim / canvas": ({"row_spacing_mm": 0.24, "satin_spacing_mm": 0.4, "pull_comp_mm": 0.1, "underlay": True},
                       "Tear-away stabilizer and a size 90/14 needle."),
    "Towel / fleece": ({"row_spacing_mm": 0.22, "satin_spacing_mm": 0.35, "pull_comp_mm": 0.2, "underlay": True},
                       "Water-soluble topping on top so stitches don't sink into the pile."),
    "Cap / thick": ({"row_spacing_mm": 0.25, "satin_spacing_mm": 0.4, "pull_comp_mm": 0.2, "underlay": True},
                    "Firm tear-away backing. Keep designs under about 55 mm tall."),
}

FILL_TYPES = ("auto", "tatami", "satin")
LINE_TYPES = ("none", "running", "triple", "satin")
WRITABLE = {".pes", ".pec", ".dst", ".jef", ".exp", ".vp3", ".xxx", ".u01"}


@dataclass
class Settings:
    width_mm: float = 90.0  # the design is scaled to fit inside width x height
    height_mm: float = 90.0
    rotate: int = 0  # clockwise degrees: 0, 90, 180 or 270
    mirror: bool = False
    colors: int = 4  # thread colors, not counting the removed background
    remove_background: bool = True
    brother_colors: bool = False  # snap colors to the Brother thread chart
    cleanup: int = 1  # 0-3: removes specks and smooths jagged edges
    color_edits: dict = field(default_factory=dict)  # {cluster rgb: new rgb, or None to skip it}

    fill_type: str = "auto"  # "auto" = satin for narrow shapes, tatami for the rest
    satin_max_mm: float = 6.5  # "auto": shapes narrower than this become satin
    satin_spacing_mm: float = 0.4
    row_spacing_mm: float = 0.24  # tatami row spacing; smaller = denser (0.22-0.25 covers like Brother's own fills)
    stitch_length_mm: float = 3.5
    fill_angle: float = 45.0
    auto_angle: bool = True  # long shapes are sewn along their length; others use fill_angle
    pull_comp_mm: float = 0.15  # widens rows to offset fabric pull-in
    underlay: bool = True
    trim_mm: float = 1.0  # jumps longer than this get a trim (loose threads show on the fabric)
    min_area_mm2: float = 1.0  # smaller regions are skipped

    edge: str = "none"  # finish on the edges of every color: none / running / triple / satin
    edge_width_mm: float = 1.2
    outline: str = "none"  # line around the whole design: none / running / triple / satin
    outline_width_mm: float = 2.0
    outline_offset_mm: float = 0.0
    outline_color: tuple = (0, 0, 0)
    running_length_mm: float = 2.5


@dataclass
class Result:
    pattern: EmbPattern
    labels: np.ndarray  # (H, W) color index per pixel, -1 = not stitched
    palette: list  # [(r, g, b), ...] indexed by label
    info: dict
    threads: list  # one entry per thread in sewing order, plus skipped colors


# --------------------------------------------------------------------------- sources


def load_image(path) -> Image.Image:
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    return img.convert("RGBA")


@lru_cache(maxsize=1)
def list_fonts() -> dict:
    """{"Family Style": path} for installed TrueType/OpenType fonts."""
    dirs = [
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts"),
        "/Library/Fonts", os.path.expanduser("~/Library/Fonts"), "/usr/share/fonts/truetype",
    ]
    fonts = {}
    for folder in dirs:
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            if not name.lower().endswith((".ttf", ".otf")):
                continue
            path = os.path.join(folder, name)
            try:
                probe = ImageFont.truetype(path, 10)
                family, style = probe.getname()
                probe.getbbox("Ag")  # skip fonts that load but can't draw text
            except Exception:
                continue
            label = family if style in ("Regular", "Normal", "Book", "Roman") else f"{family} {style}"
            fonts.setdefault(label, path)
    return dict(sorted(fonts.items(), key=lambda kv: kv[0].lower()))


def make_text_image(text, font_path, color=(0, 0, 0), size_px=300) -> Image.Image:
    """Renders (multi-line) text as a transparent RGBA image, ready to digitize."""
    font = ImageFont.truetype(font_path, size_px)
    spacing, pad = size_px // 6, size_px // 8
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    x0, y0, x1, y1 = probe.multiline_textbbox((0, 0), text, font=font, align="center", spacing=spacing)
    x0, y0, x1, y1 = math.floor(x0), math.floor(y0), math.ceil(x1), math.ceil(y1)  # some fonts give floats
    img = Image.new("RGBA", (x1 - x0 + 2 * pad, y1 - y0 + 2 * pad), (0, 0, 0, 0))
    ImageDraw.Draw(img).multiline_text((pad - x0, pad - y0), text, font=font, fill=tuple(color) + (255,),
                                       align="center", spacing=spacing)
    return img


# --------------------------------------------------------------------------- colors


def _load_brother_threads():
    try:
        from pyembroidery.EmbThreadPec import get_thread_set
    except ImportError:
        return []
    threads = []
    for t in get_thread_set():
        if t is not None:
            rgb = (t.get_red(), t.get_green(), t.get_blue())
            threads.append((rgb, getattr(t, "description", "") or "", getattr(t, "catalog_number", "") or ""))
    return threads


BROTHER_THREADS = _load_brother_threads()


def _color_distance(a, b):
    """Weighted RGB distance ("redmean"), close to perceived difference."""
    rm = (a[0] + b[0]) / 2
    dr, dg, db = a[0] - b[0], a[1] - b[1], a[2] - b[2]
    return (2 + rm / 256) * dr * dr + 4 * dg * dg + (2 + (255 - rm) / 256) * db * db


def nearest_brother_thread(rgb):
    """Returns ((r, g, b), name, chart_index) of the closest Brother thread."""
    if not BROTHER_THREADS:
        return tuple(rgb), "", ""
    return min(BROTHER_THREADS, key=lambda t: _color_distance(rgb, t[0]))


def hex_color(rgb):
    return "#%02x%02x%02x" % tuple(rgb)


def _rgb_to_lab(rgb):
    """sRGB (N, 3) -> CIELAB, so k-means groups colors the way the eye does."""
    c = np.asarray(rgb, np.float32) / 255
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]], np.float32)
    xyz = lin @ m.T / np.array([0.95047, 1.0, 1.08883], np.float32)
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[:, 1] - 16, 500 * (f[:, 0] - f[:, 1]), 200 * (f[:, 1] - f[:, 2])], axis=1).astype(np.float32)


def _nearest_center(pixels, centers):
    out = np.empty(len(pixels), np.int16)
    for i in range(0, len(pixels), 250_000):
        chunk = pixels[i : i + 250_000].astype(np.float32)
        d = ((chunk[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
        out[i : i + 250_000] = d.argmin(axis=1)
    return out


def _kmeans(pixels, k, attempts=4, iterations=25, seed=0):
    """Best of several k-means++ runs, so small but distinct colors get their own cluster."""
    rng = np.random.default_rng(seed)
    if len(pixels) > 60_000:
        pixels = pixels[rng.choice(len(pixels), 60_000, replace=False)]
    sample = pixels.astype(np.float32)
    k = max(1, min(k, len(np.unique(pixels, axis=0))))

    best, best_cost = None, np.inf
    for _ in range(attempts):
        # k-means++ seeding: spread the initial centers out
        centers = [sample[rng.integers(len(sample))]]
        d2 = ((sample - centers[0]) ** 2).sum(axis=1)
        for _ in range(1, k):
            idx = rng.choice(len(sample), p=d2 / d2.sum())
            centers.append(sample[idx])
            d2 = np.minimum(d2, ((sample - sample[idx]) ** 2).sum(axis=1))
        centers = np.array(centers)

        for _ in range(iterations):
            labels = _nearest_center(sample, centers)
            new = np.array([sample[labels == i].mean(axis=0) if np.any(labels == i) else centers[i]
                            for i in range(k)])
            converged = np.abs(new - centers).max() < 0.5
            centers = new
            if converged:
                break
        cost = ((sample - centers[_nearest_center(sample, centers)]) ** 2).sum()
        if cost < best_cost:
            best, best_cost = centers, cost
    return best


def _merge(labels, colors, keys):
    """Gives identical colors one label and drops labels whose color is None.

    Returns (labels, unique colors, keys merged into each color).
    """
    unique = list(dict.fromkeys(c for c in colors if c is not None))
    lut = np.array([unique.index(c) if c is not None else -1 for c in colors] + [-1], np.int16)
    groups = [[k for k, c in zip(keys, colors) if c == u] for u in unique]
    return lut[labels], unique, groups


def _sort_by_area(labels, palette, groups):
    """Largest color first: big areas are sewn before the details on top of them."""
    counts = np.bincount(labels[labels >= 0].ravel(), minlength=len(palette))
    order = [i for i in np.argsort(-counts, kind="stable") if counts[i] > 0]
    remap = np.full(len(palette) + 1, -1, np.int16)  # last slot keeps -1 as -1
    for new, old in enumerate(order):
        remap[old] = new
    return remap[labels], [palette[i] for i in order], [groups[i] for i in order]


def reduce_colors(img: Image.Image, s: Settings):
    """Scales the image to the working resolution and maps every pixel to a thread color.

    Returns (labels, palette, groups, skipped): labels is an (H, W) int array (-1 = leave
    empty); groups[i] lists the color keys merged into palette[i] (keys identify color
    clusters for Settings.color_edits); skipped lists keys the edits turned off.
    """
    img = img.convert("RGBA")
    if s.mirror:
        img = ImageOps.mirror(img)
    if s.rotate % 360:
        img = img.transpose({90: Image.ROTATE_270, 180: Image.ROTATE_180, 270: Image.ROTATE_90}[s.rotate % 360])

    w, h = img.size
    mm_per_src_px = min(s.width_mm / w, s.height_mm / h)
    size = (max(1, round(w * mm_per_src_px / PX_MM)), max(1, round(h * mm_per_src_px / PX_MM)))
    arr = np.asarray(img.resize(size, Image.LANCZOS))

    opaque = arr[..., 3] >= 128
    if not opaque.any():
        raise ValueError("The image is completely transparent.")
    has_transparency = not opaque.all()
    extra_bg = 1 if (s.remove_background and not has_transparency) else 0

    # Pick colors from flat areas only: anti-aliased edge pixels are blends of two
    # colors and would otherwise steal clusters from small, distinct details.
    rgb = arr[..., :3]
    a = rgb.astype(np.int16)
    flat = opaque.copy()
    for shift, axis in ((1, 0), (-1, 0), (1, 1), (-1, 1)):
        flat &= np.abs(a - np.roll(a, shift, axis)).max(axis=2) <= 30
    fit = rgb[flat] if flat.sum() >= 0.2 * opaque.sum() else rgb[opaque]  # photos have few flat pixels
    fit_lab = _rgb_to_lab(fit)
    centers_lab = _kmeans(fit_lab, s.colors + extra_bg)
    fit_labels = _nearest_center(fit_lab, centers_lab)
    # thread colors are the plain average of each cluster's flat pixels
    centers = [fit[fit_labels == i].mean(axis=0) if np.any(fit_labels == i) else (128, 128, 128)
               for i in range(len(centers_lab))]
    labels = np.full(opaque.shape, -1, np.int16)
    labels[opaque] = _nearest_center(_rgb_to_lab(rgb[opaque]), centers_lab)

    if s.cleanup > 0:
        img_labels = Image.fromarray((labels + 1).astype(np.uint8), "L")
        img_labels = img_labels.filter(ImageFilter.ModeFilter(2 * s.cleanup + 1))
        labels = np.asarray(img_labels).astype(np.int16) - 1

    if extra_bg:
        # the color that dominates the image border is the background
        border = np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]])
        border = border[border >= 0]
        if border.size:
            labels[labels == np.bincount(border).argmax()] = -1

    palette = [tuple(int(round(c)) for c in center) for center in centers]
    if s.brother_colors:
        snapped = [nearest_brother_thread(c)[0] for c in palette]
        labels, palette, _ = _merge(labels, snapped, snapped)

    keys = palette
    final = [s.color_edits.get(k, k) for k in keys]
    skipped = [k for k, f in zip(keys, final) if f is None and np.any(labels == keys.index(k))]
    labels, palette, groups = _merge(labels, final, keys)
    labels, palette, groups = _sort_by_area(labels, palette, groups)
    return labels, palette, groups, skipped


def color_preview(labels, palette, background=(255, 255, 255)) -> Image.Image:
    lut = np.array(list(palette) + [background], np.uint8)  # label -1 picks the last entry
    return Image.fromarray(lut[labels], "RGB")


# --------------------------------------------------------------------------- shapes
#
# From here on geometry is vector: every color region becomes smooth polygons in millimetres.
# Fills and satin columns are computed on those polygons, so edges are smooth instead of
# following the pixel grid.


# marching-squares segments per cell case (corner bits: top-left 8, top-right 4,
# bottom-right 2, bottom-left 1), as pairs of crossed cell edges
_MS = {
    1: (("L", "B"),), 2: (("B", "R"),), 3: (("L", "R"),), 4: (("T", "R"),), 5: (("T", "R"), ("L", "B")),
    6: (("T", "B"),), 7: (("T", "L"),), 8: (("T", "L"),), 9: (("T", "B"),), 10: (("T", "L"), ("B", "R")),
    11: (("T", "R"),), 12: (("L", "R"),), 13: (("R", "B"),), 14: (("L", "B"),),
}


def _contours(mask):
    m = np.pad(mask, 1).astype(np.uint8)
    case = m[:-1, :-1] * 8 + m[:-1, 1:] * 4 + m[1:, 1:] * 2 + m[1:, :-1]
    ys, xs = np.nonzero((case > 0) & (case < 15))
    links = {}
    for y, x, c in zip(ys.tolist(), xs.tolist(), case[ys, xs].tolist()):
        # edge midpoints in doubled coordinates, so they are integers shared by neighbours
        edge = {"T": (2 * x + 1, 2 * y), "R": (2 * x + 2, 2 * y + 1), "B": (2 * x + 1, 2 * y + 2),
                "L": (2 * x, 2 * y + 1)}
        for a, b in _MS[c]:
            links.setdefault(edge[a], []).append(edge[b])
            links.setdefault(edge[b], []).append(edge[a])

    loops, seen = [], set()
    for start in links:
        if start in seen:
            continue
        loop, prev, cur = [start], start, links[start][0]
        seen.add(start)
        while cur != start:
            seen.add(cur)
            loop.append(cur)
            a, b = links[cur]
            prev, cur = cur, (a if b == prev else b)
        # doubled padded coords -> pixel coords (pixel i covers [i, i + 1))
        loops.append(np.array(loop, float) / 2 - 0.5)
    return loops


def _smooth_ring(pts, passes=2):
    for _ in range(passes):
        pts = sum(np.roll(pts, shift, axis=0) for shift in range(-2, 3)) / 5
    return pts


def _polygons_of(geom):
    """Flattens any shapely geometry into a list of Polygons."""
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom]
    return [p for part in getattr(geom, "geoms", []) for p in _polygons_of(part)]


def _mask_polygons(mask, min_area_mm2):
    """Smooth polygons (in mm) for a boolean mask. Holes become interior rings."""
    loops, raw, smooth = [], [], []
    for loop in _contours(mask):
        if len(loop) < 8:
            continue
        poly = Polygon(_smooth_ring(loop * PX_MM))
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty:
            continue
        loops.append(loop)
        raw.append(Polygon(loop * PX_MM))
        smooth.append(poly)
    if not smooth:
        return []

    # Nesting depth of each loop = how many other loops contain it. Even depth is an outer edge,
    # odd depth is a hole. (Loops never cross, so one point per loop is enough to test.)
    points = shapely.points(np.array([l[0] * PX_MM for l in loops]))
    pairs = shapely.STRtree(raw).query(points, predicate="within")
    depth = np.bincount(pairs[0], minlength=len(loops)) if pairs.size else np.zeros(len(loops), int)
    levels = {}
    for d, poly in zip(depth.tolist(), smooth):
        levels.setdefault(d, []).append(poly)
    parts = []
    for d in sorted(levels):
        if d % 2 == 0:  # an outer level minus the holes directly inside it
            outer = unary_union(levels[d])
            holes = levels.get(d + 1)
            parts.append(outer.difference(unary_union(holes)) if holes else outer)
    geom = unary_union(parts)
    polys = []
    for p in _polygons_of(geom):
        p = p.simplify(0.02)
        if p.area >= min_area_mm2:
            polys.append(p)
    return polys


def _elongation(poly):
    """(long side, short side, angle of the long side in the row convention) of the oriented bounding box."""
    rect = poly.minimum_rotated_rectangle
    if rect.geom_type != "Polygon":
        return 0.0, 0.0, 0.0
    c = np.asarray(rect.exterior.coords)
    e0, e1 = c[1] - c[0], c[2] - c[1]
    l0, l1 = float(np.hypot(*e0)), float(np.hypot(*e1))
    e, long_side, short_side = (e0, l0, l1) if l0 >= l1 else (e1, l1, l0)
    return long_side, short_side, math.degrees(math.atan2(-e[1], e[0])) % 180


def _split_narrow(polys, width_mm, min_area):
    """Splits polygons into wide areas (tatami) and narrow strokes (satin columns).

    A part is narrow if it vanishes when the polygon is shrunk by half the satin width.
    """
    if width_mm <= 0:
        return polys, []
    wide, narrow = [], []
    for p in polys:
        core = p.buffer(-width_mm / 2).buffer(width_mm / 2)
        if core.is_empty:
            narrow.append(p)
            continue
        kept = []
        near_core = core.buffer(0.05)
        for part in _polygons_of(p.difference(core)):
            long_side, short_side, _ = _elongation(part)
            if part.area < max(min_area, 1.5) or long_side < max(2.5, 2.0 * short_side):
                continue
            # a real stroke touches the wide area only at its end; a thin skin hugging the
            # wide area's edge (left over where the shrink-and-grow rounds a corner) touches all along
            shared = part.boundary.intersection(near_core).length
            if shared < 0.6 * long_side:
                kept.append(part)
        rest = p.difference(unary_union(kept)) if kept else p
        wide += [w for w in _polygons_of(rest) if w.area >= min_area]
        narrow += kept
    return wide, narrow


# --------------------------------------------------------------------------- paths


def _resample_closed(loop, step):
    closed = np.vstack([loop, loop[:1]])
    lengths = np.hypot(*np.diff(closed, axis=0).T)
    cum = np.concatenate([[0], np.cumsum(lengths)])
    if cum[-1] <= 0:
        return loop[:1]
    n = max(4, int(round(cum[-1] / step)))
    at = np.linspace(0, cum[-1], n, endpoint=False)
    return np.stack([np.interp(at, cum, closed[:, 0]), np.interp(at, cum, closed[:, 1])], axis=1)


def _resample_open(path, step, keep_end=True):
    path = np.asarray(path, float)
    lengths = np.hypot(*np.diff(path, axis=0).T)
    cum = np.concatenate([[0], np.cumsum(lengths)])
    if cum[-1] <= 0:
        return path[:1]
    n = max(2, int(round(cum[-1] / step)) + 1)
    at = np.linspace(0, cum[-1], n)
    return np.stack([np.interp(at, cum, path[:, 0]), np.interp(at, cum, path[:, 1])], axis=1)


def _smooth_path(pts, closed, passes=3):
    pts = np.asarray(pts, float)
    for _ in range(passes):
        if closed:
            pts = (np.roll(pts, 1, axis=0) + 2 * pts + np.roll(pts, -1, axis=0)) / 4
        elif len(pts) > 2:
            inner = (pts[:-2] + 2 * pts[1:-1] + pts[2:]) / 4
            pts = np.vstack([pts[:1], inner, pts[-1:]])  # the end points stay put
    return pts


def _skeleton_branches(poly, step=0.25):
    """Centre lines of a narrow polygon, from the Voronoi diagram of its boundary.

    Returns a list of (points, closed, leaf_at_start, leaf_at_end, max_radius): one per branch of
    the skeleton. Short spurs (caused by corners and ragged edges) are pruned away.
    """
    rings = [poly.exterior] + list(poly.interiors)
    parts, ring_id, ring_idx, ring_n = [], [], [], []
    for k, ring in enumerate(rings):
        n = max(12, int(ring.length / step))
        at = np.linspace(0, ring.length, n, endpoint=False)
        parts.append(shapely.get_coordinates(shapely.line_interpolate_point(ring, at)))
        ring_id += [k] * n
        ring_idx += list(range(n))
        ring_n += [n] * n
    P = np.vstack(parts)
    ring_id, ring_idx, ring_n = np.array(ring_id), np.array(ring_idx), np.array(ring_n)
    try:
        vor = Voronoi(P)
    except Exception:
        return []
    V = vor.vertices
    inside = shapely.contains_xy(poly, V[:, 0], V[:, 1])
    radius = cKDTree(P).query(V)[0]

    adj = {}
    for (a, b), (p, q) in zip(vor.ridge_vertices, vor.ridge_points):
        if a < 0 or b < 0 or not (inside[a] and inside[b]):
            continue
        if ring_id[p] == ring_id[q]:  # ridges between neighbouring boundary points are spikes
            gap = abs(ring_idx[p] - ring_idx[q])
            if min(gap, ring_n[p] - gap) <= 2:
                continue
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)

    def branches(adj):
        deg = {n: len(a) for n, a in adj.items()}
        used, paths = set(), []
        for n in [n for n in adj if deg[n] != 2]:
            for m in adj[n]:
                if (n, m) in used:
                    continue
                path = [n, m]
                used.update(((n, m), (m, n)))
                prev, cur = n, m
                while deg[cur] == 2:
                    nxt = next(x for x in adj[cur] if x != prev)
                    used.update(((cur, nxt), (nxt, cur)))
                    path.append(nxt)
                    prev, cur = cur, nxt
                paths.append(path)
        covered = {n for path in paths for n in path}
        for n in adj:  # loops with no branch points at all
            if n in covered or deg[n] != 2:
                continue
            path, prev, cur = [n], None, n
            while True:
                nxt = next(x for x in adj[cur] if x != prev) if prev is not None else next(iter(adj[cur]))
                if nxt == n:
                    break
                path.append(nxt)
                covered.add(nxt)
                prev, cur = cur, nxt
            covered.add(n)
            paths.append(path + [n])
        return paths

    def length_of(path):
        return float(np.hypot(*np.diff(V[path], axis=0).T).sum())

    for _ in range(6):  # prune spurs, repeating because removing one can expose another
        removed = False
        deg = {n: len(a) for n, a in adj.items()}
        for path in branches(adj):
            leaf_a, leaf_b = deg.get(path[0]) == 1, deg.get(path[-1]) == 1
            if leaf_a == leaf_b:
                continue
            width = 2 * max(radius[n] for n in path)
            if length_of(path) < max(1.0, width):
                for n in (path[:-1] if leaf_a else path[1:]):
                    for m in adj.pop(n, ()):
                        if m in adj:
                            adj[m].discard(n)
                removed = True
        adj = {n: a for n, a in adj.items() if a}
        if not removed:
            break

    deg = {n: len(a) for n, a in adj.items()}
    out = []
    for path in branches(adj):
        if length_of(path) < 0.8:
            continue
        closed = path[0] == path[-1] and len(path) > 3
        out.append((V[path[:-1] if closed else path], closed, deg.get(path[0]) == 1, deg.get(path[-1]) == 1,
                    float(max(radius[n] for n in path))))
    return out


# --------------------------------------------------------------------------- stitches


def _scan_poly(poly, center, angle, spacing):
    """Intersects parallel rows at `angle` degrees with a polygon (holes included).

    Rows lie on a lattice anchored at `center`, so neighbouring shapes line up. Row ends are exact
    (sub-pixel), which keeps the fill edge smooth.
    """
    a = math.radians(angle)
    ux, uy = math.cos(a), -math.sin(a)  # along a row (image y axis points down)
    nx, ny = -uy, ux  # across rows
    cx, cy = center

    edges = []
    for ring in [poly.exterior, *poly.interiors]:
        c = np.asarray(ring.coords)
        px, py = c[:, 0] - cx, c[:, 1] - cy
        t, d = px * ux + py * uy, px * nx + py * ny
        edges.append(np.stack([t[:-1], d[:-1], t[1:], d[1:]], axis=1))
    E = np.vstack(edges)
    E = E[E[:, 1] != E[:, 3]]
    if len(E) == 0:
        return None
    t0, d0, t1, d1 = E.T
    lo, hi = np.minimum(d0, d1), np.maximum(d0, d1)
    first, last = math.floor(lo.min() / spacing), math.ceil(hi.max() / spacing)
    grid_rows = np.arange(first, last + 1)
    ds = grid_rows * spacing

    seg_r, seg_t0, seg_t1 = [], [], []
    for r, d in enumerate(ds):
        sel = (lo <= d) & (hi > d)  # half-open, so a vertex on a row is counted once
        if not sel.any():
            continue
        ts = np.sort(t0[sel] + (d - d0[sel]) * (t1[sel] - t0[sel]) / (d1[sel] - d0[sel]))
        if len(ts) % 2:
            continue
        for k in range(0, len(ts), 2):
            if ts[k + 1] - ts[k] > 1e-6:
                seg_r.append(r)
                seg_t0.append(ts[k])
                seg_t1.append(ts[k + 1])
    if not seg_r:
        return None
    return SimpleNamespace(grid_rows=grid_rows, ds=ds, r=np.array(seg_r), t0=np.array(seg_t0),
                           t1=np.array(seg_t1), cx=cx, cy=cy, ux=ux, uy=uy, nx=nx, ny=ny)


def _travel(pts, target, max_len):
    """Appends evenly spaced stitches from the last point to `target` (exclusive)."""
    x0, y0 = pts[-1]
    dist = math.hypot(target[0] - x0, target[1] - y0)
    pieces = math.ceil(dist / max_len)
    for i in range(1, pieces):
        f = i / pieces
        pts.append((x0 + (target[0] - x0) * f, y0 + (target[1] - y0) * f))


def _fill_rows(rows, spacing, stitch, min_len, min_area, pen):
    """Sews prepared rows back and forth (tatami): needle points every `stitch` along each row,
    staggered between rows.

    Returns (runs, pen): each run is a list of (x, y) mm points sewn continuously; consecutive
    runs are joined with a jump. `pen` is the current needle position.
    """
    if rows is None:
        return [], pen
    keep = (rows.t1 - rows.t0) >= min_len
    seg_r, seg_t0, seg_t1 = rows.r[keep], rows.t0[keep], rows.t1[keep]
    n = len(seg_r)
    if n == 0:
        return [], pen

    cx, cy, ux, uy, nx, ny = rows.cx, rows.cy, rows.ux, rows.uy, rows.nx, rows.ny
    DS, grid_rows = rows.ds.tolist(), rows.grid_rows.tolist()

    def point(r, t):
        d = DS[r]
        return (cx + d * nx + t * ux, cy + d * ny + t * uy)

    ds = rows.ds[seg_r]
    end0 = np.stack((cx + ds * nx + seg_t0 * ux, cy + ds * ny + seg_t0 * uy), axis=1)
    end1 = np.stack((cx + ds * nx + seg_t1 * ux, cy + ds * ny + seg_t1 * uy), axis=1)
    by_row = [[] for _ in DS]
    for idx, r in enumerate(seg_r.tolist()):
        by_row[r].append(idx)
    R, T0, T1 = seg_r.tolist(), seg_t0.tolist(), seg_t1.tolist()
    visited = np.zeros(n, bool)
    left = n

    runs = []
    while left:
        # start the next run at the unvisited row end closest to the needle
        if pen is None:
            cur, fwd = int(np.argmin(visited)), True
        else:
            d0 = ((end0 - pen) ** 2).sum(axis=1)
            d1 = ((end1 - pen) ** 2).sum(axis=1)
            d0[visited] = np.inf
            d1[visited] = np.inf
            i0, i1 = int(d0.argmin()), int(d1.argmin())
            cur, fwd = (i0, True) if d0[i0] <= d1[i1] else (i1, False)

        # serpentine: keep stepping to an overlapping row segment next door
        chain, sweep = [], 1
        while True:
            visited[cur] = True
            left -= 1
            chain.append((cur, fwd))
            end_t = T1[cur] if fwd else T0[cur]
            best = None
            for dr in (sweep, -sweep):
                rr = R[cur] + dr
                if 0 <= rr < len(DS):
                    for j in by_row[rr]:
                        if visited[j] or T0[j] >= T1[cur] or T1[j] <= T0[cur]:
                            continue
                        cost = min(abs(T0[j] - end_t), abs(T1[j] - end_t))
                        if best is None or cost < best[0]:
                            best = (cost, j)
                if best is not None:
                    sweep = dr
                    break
            if best is None:
                break
            cur = best[1]
            fwd = abs(T0[cur] - end_t) <= abs(T1[cur] - end_t)

        last, last_fwd = chain[-1]
        pen = end1[last] if last_fwd else end0[last]
        if sum(T1[c] - T0[c] for c, _ in chain) * spacing < min_area:
            continue

        pts = []
        for idx, forward in chain:
            r, t0, t1 = R[idx], T0[idx], T1[idx]
            phase = (grid_rows[r] % 3) * stitch / 3.0  # brick offset hides row lines
            k0 = math.ceil((t0 + MIN_STITCH_MM - phase) / stitch)
            k1 = math.floor((t1 - MIN_STITCH_MM - phase) / stitch)
            inner = [phase + k * stitch for k in range(k0, k1 + 1)]
            start, end = (t0, t1) if forward else (t1, t0)
            if not forward:
                inner.reverse()
            row_pts = [point(r, t) for t in [start] + inner + [end]]
            if pts:
                _travel(pts, row_pts[0], stitch)
            pts.extend(row_pts)
        runs.append(pts)
    return runs, pen


def _tatami(poly, center, s, pen):
    """Underlay (edge walk + sparse perpendicular rows) and the top fill of one polygon."""
    angle = s.fill_angle
    if s.auto_angle:  # elongated shapes are sewn along their length; blobs use the chosen angle
        long_side, short_side, along = _elongation(poly)
        if short_side > 0 and long_side >= 1.8 * short_side:
            angle = along
    runs = []
    if s.underlay:
        for part in _polygons_of(poly.buffer(-0.4)):
            if part.area < 2.0:
                continue
            if part.area >= 12.0:  # edge walk: holds the edge down before the rows go on top
                walk = _resample_closed(np.asarray(part.exterior.coords)[:-1], 2.5)
                runs.append([tuple(p) for p in walk] + [tuple(walk[0])])
                pen = walk[0]
            under, pen = _fill_rows(_scan_poly(part, center, angle + 90, 2.0), 2.0, 3.0, 1.0,
                                    4 * s.min_area_mm2, pen)
            runs += under
    top, pen = _fill_rows(_scan_poly(poly, center, angle, s.row_spacing_mm), s.row_spacing_mm,
                          s.stitch_length_mm, 0.5, s.min_area_mm2, pen)
    return runs + top, pen


def _split_at_corners(path, closed, leaf_a, leaf_b, radius, angle=60.0):
    """Cuts a centre line where it turns sharply, so each straight-ish stroke gets its own column.

    One column can't turn a tight corner without fanning its stitches. Each cut end is marked as a
    leaf, so the column runs on past the corner and the neighbouring columns overlap there.
    """
    pts = _resample_closed(path, 0.2) if closed else _resample_open(path, 0.2)
    n = len(pts)
    if n < 12:
        return [(path, closed, leaf_a, leaf_b, radius)]
    pts = _smooth_path(pts, closed, passes=4)
    w = 3  # points are 0.2 mm apart, so this looks 0.6 mm either side
    score = np.zeros(n)
    for i in range(n):
        if not closed and (i < w or i >= n - w):
            continue
        v1, v2 = pts[i] - pts[(i - w) % n], pts[(i + w) % n] - pts[i]
        denom = float(np.hypot(*v1) * np.hypot(*v2))
        if denom > 0:
            score[i] = math.degrees(math.acos(max(-1.0, min(1.0, float(v1 @ v2) / denom))))
    corners = [i for i in range(n) if score[i] >= angle
               and score[i] == score[max(0, i - 2 * w):i + 2 * w + 1].max()
               and (closed or w * 2 <= i < n - w * 2)]
    # keep one corner per cluster
    corners = [c for k, c in enumerate(corners) if k == 0 or c - corners[k - 1] > 2 * w]
    if not corners:
        return [(path, closed, leaf_a, leaf_b, radius)]
    if closed:
        pts = np.roll(pts, -corners[0], axis=0)
        corners = [c - corners[0] for c in corners]
        cuts = corners + [n]
        pieces = [pts[cuts[k]:cuts[k + 1] + 1] if cuts[k + 1] < n else np.vstack([pts[cuts[k]:], pts[:1]])
                  for k in range(len(corners))]
        return [(p, False, True, True, radius) for p in pieces if len(p) >= 4]
    cuts = [0] + corners + [n - 1]
    out = []
    for k in range(len(cuts) - 1):
        p = pts[cuts[k]:cuts[k + 1] + 1]
        if len(p) >= 4:
            out.append((p, False, True if k > 0 else leaf_a, True if k < len(cuts) - 2 else leaf_b, radius))
    return out


def _satin_runs(poly, s, pen):
    """Satin columns along the centre lines of a narrow polygon. Returns (runs, pen) or None."""
    try:
        branches = _skeleton_branches(poly)
    except Exception:
        return None
    if not branches:
        return None
    shapely.prepare(poly)
    step = max(0.15, s.satin_spacing_mm / 2)
    runs = []
    todo = []
    for path, closed, leaf_a, leaf_b, radius in branches:
        todo += _split_at_corners(path, closed, leaf_a, leaf_b, radius)
    while todo:
        if pen is None:
            k = 0
        else:
            k = min(range(len(todo)), key=lambda i: min(math.dist(pen, todo[i][0][0]), math.dist(pen, todo[i][0][-1])))
        path, closed, leaf_a, leaf_b, radius = todo.pop(k)
        if pen is not None and not closed and math.dist(pen, path[-1]) < math.dist(pen, path[0]):
            path, leaf_a, leaf_b = path[::-1], leaf_b, leaf_a
        run = _satin_column(poly, path, closed, leaf_a, leaf_b, radius, step, s)
        if run:
            runs.append(run)
            pen = np.array(run[-1])
    return (runs, pen) if runs else None


def _satin_column(poly, path, closed, leaf_a, leaf_b, radius, step, s):
    pts = _resample_closed(path, 0.2) if closed else _resample_open(path, 0.2)
    if len(pts) < 3:
        return None
    pts = _smooth_path(pts, closed, passes=6)

    if not closed:  # carry the column to the tip: the centre line stops one radius short of the end
        for at_start, leaf in ((True, leaf_a), (False, leaf_b)):
            if not leaf:
                continue
            tip, nxt = (pts[0], pts[min(4, len(pts) - 1)]) if at_start else (pts[-1], pts[max(-5, -len(pts))])
            direction = tip - nxt
            norm = float(np.hypot(*direction))
            if norm <= 0:
                continue
            direction /= norm
            extra = [tip + direction * d for d in np.arange(0.1, radius * 1.1, 0.1)
                     if poly.buffer(0.05).contains(Point(*(tip + direction * d)))]
            if extra:
                pts = np.vstack([extra[::-1], pts]) if at_start else np.vstack([pts, extra])

    pts = _resample_closed(pts, step) if closed else _resample_open(pts, step)
    if len(pts) < 3:
        return None
    # Local half-width of the stroke: distance to the nearest edge. Near the tips and corners that
    # distance shrinks (the end edge is close), so take the largest value within a window along the column.
    local = shapely.distance(shapely.points(pts), poly.boundary)

    # Stitch direction: measured over about one stroke width of centre line, so small wiggles and
    # the bend where the centre line runs into a corner don't fan the stitches out.
    n = len(pts)
    k = max(2, int(round(2 * float(np.median(local)) / step)))
    if closed:
        tangent = np.roll(pts, -k, axis=0) - np.roll(pts, k, axis=0)
    else:
        idx = np.arange(n)
        tangent = pts[np.minimum(n - 1, idx + k)] - pts[np.maximum(0, idx - k)]
    tangent /= np.maximum(np.hypot(tangent[:, 0], tangent[:, 1]), 1e-9)[:, None]
    normal = np.stack([-tangent[:, 1], tangent[:, 0]], axis=1)
    win = max(1, int(round(1.5 * radius / step)))
    reach = np.array([local[max(0, i - win):i + win + 1].max() for i in range(len(pts))]) * 1.3 + 0.2
    # where strokes meet, the "width" balloons; never go much past this column's typical width
    reach = np.minimum(reach, float(np.median(local)) * 1.35 + 0.2)
    half = 2 * radius + 1.0  # how far to look for the edges on either side
    lines = shapely.linestrings(np.stack([pts - normal * half, pts + normal * half], axis=1))
    cut = shapely.intersection(lines, poly)
    lo = np.full(len(pts), np.nan)
    hi = np.full(len(pts), np.nan)
    for i, geom in enumerate(cut):
        if geom.is_empty:
            continue
        pieces = [geom] if geom.geom_type == "LineString" else [g for g in getattr(geom, "geoms", [])
                                                                 if g.geom_type == "LineString"]
        if not pieces:
            continue
        piece = min(pieces, key=lambda g: g.distance(Point(*pts[i])))
        a, b = np.asarray(piece.coords[0]), np.asarray(piece.coords[-1])
        ta, tb = float((a - pts[i]) @ normal[i]), float((b - pts[i]) @ normal[i])
        lo[i], hi[i] = max(min(ta, tb), -reach[i]), min(max(ta, tb), reach[i])  # stay within the stroke's own width
    ok = np.isfinite(lo) & ((hi - lo) > 0.2)
    if ok.sum() < 3:
        return None
    pts, normal, lo, hi = pts[ok], normal[ok], lo[ok], hi[ok]
    pull = s.pull_comp_mm

    def zigzag(idx, inset, pull_out):
        out = []
        for j, i in enumerate(idx):
            width = hi[i] - lo[i]
            if width <= 2 * inset + 0.2:
                out.append(tuple(pts[i] + normal[i] * (hi[i] + lo[i]) / 2))
            elif j % 2 == 0:
                out.append(tuple(pts[i] + normal[i] * (lo[i] + inset - pull_out)))
            else:
                out.append(tuple(pts[i] + normal[i] * (hi[i] - inset + pull_out)))
        return out

    n = len(pts)
    top = zigzag(range(n), 0.0, pull)
    if not s.underlay:
        return top
    every = lambda pitch: list(range(0, n, max(1, round(pitch / step)))) + ([n - 1] if (n - 1) % max(1, round(pitch / step)) else [])
    center = [tuple(pts[i] + normal[i] * (hi[i] + lo[i]) / 2) for i in every(2.5)]
    under = zigzag(every(1.4), 0.3, 0.0)[::-1]  # comes back along the column, so no jump is needed
    return center + under + top


def _line_runs(rings, kind, width_mm, pen, s):
    """Sews closed loops (arrays of mm points) as running, triple (bean) or satin lines, nearest first."""
    prepared = [np.asarray(r, float) for r in rings if len(r) >= 8 and
                np.hypot(*(np.roll(r, -1, axis=0) - r).T).sum() >= 3.0]
    runs = []
    while prepared:
        if pen is None:
            i, j = 0, 0
        else:
            nearest = [((loop - pen) ** 2).sum(axis=1) for loop in prepared]
            i = min(range(len(prepared)), key=lambda k: nearest[k].min())
            j = int(nearest[i].argmin())
        loop = np.roll(prepared.pop(i), -j, axis=0)

        if kind == "satin":
            under = _resample_closed(loop, 2.0)  # centre walk underlay
            path = [tuple(p) for p in under] + [tuple(under[0])]
            step = max(0.15, s.satin_spacing_mm / 2)
            c = _resample_closed(loop, step)
            if len(c) % 2:
                c = c[:-1]
            k = max(1, round(0.6 / step))
            tangent = np.roll(c, -k, axis=0) - np.roll(c, k, axis=0)
            tangent /= np.maximum(np.hypot(tangent[:, 0], tangent[:, 1]), 1e-9)[:, None]
            normal = np.stack([-tangent[:, 1], tangent[:, 0]], axis=1)
            side = np.where(np.arange(len(c)) % 2 == 0, 0.5, -0.5)[:, None]
            zig = c + normal * side * width_mm
            path += [tuple(p) for p in zig] + [tuple(zig[0]), tuple(zig[1])]
        else:
            q = _resample_closed(loop, s.running_length_mm)
            q = np.vstack([q, q[:1]])
            path = [tuple(q[0])]
            for a, b in zip(q[:-1], q[1:]):
                path += [tuple(b), tuple(a), tuple(b)] if kind == "triple" else [tuple(b)]
        runs.append(path)
        pen = np.array(path[-1])
    return runs, pen


def _rings(polys):
    out = []
    for p in polys:
        for ring in [p.exterior, *p.interiors]:
            out.append(np.asarray(ring.coords)[:-1])
    return out


def _tidy(run, max_len=7.0, min_len=0.2):
    """Drops needle points that are almost on top of each other and splits very long stitches."""
    out = [run[0]]
    for p in run[1:-1]:
        if math.dist(p, out[-1]) >= min_len:
            out.append(p)
    if len(run) > 1:
        out.append(run[-1])
    final = [out[0]]
    for p in out[1:]:
        _travel(final, p, max_len)
        final.append(p)
    return final


class _Router:
    """Finds a path between two points that stays inside one color's shapes (grid search),
    so the needle can travel there with small hidden stitches instead of a trim."""

    CELL = 0.4  # mm

    def __init__(self, inside):
        self.inside = inside
        x0, y0, x1, y1 = inside.bounds
        self.x0, self.y0 = x0, y0
        self.w = max(1, int(math.ceil((x1 - x0) / self.CELL)) + 1)
        self.h = max(1, int(math.ceil((y1 - y0) / self.CELL)) + 1)
        gx, gy = np.meshgrid(x0 + np.arange(self.w) * self.CELL, y0 + np.arange(self.h) * self.CELL)
        self.ok = shapely.contains_xy(inside, gx, gy)

    def _cell(self, p):
        cx = int(round((p[0] - self.x0) / self.CELL))
        cy = int(round((p[1] - self.y0) / self.CELL))
        if 0 <= cx < self.w and 0 <= cy < self.h and self.ok[cy, cx]:
            return cy, cx
        best = None  # the point sits right at an edge: use the nearest open cell close by
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                y, x = cy + dy, cx + dx
                if 0 <= x < self.w and 0 <= y < self.h and self.ok[y, x]:
                    d = dx * dx + dy * dy
                    if best is None or d < best[0]:
                        best = (d, (y, x))
        return best[1] if best else None

    def path(self, a, b, max_len):
        if self.inside.contains(LineString([a, b])):
            return [a, b]
        start, goal = self._cell(a), self._cell(b)
        if start is None or goal is None:
            return None
        prev = {start: None}
        queue = deque([start])
        steps = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))
        while queue:
            cur = queue.popleft()
            if cur == goal:
                break
            for dy, dx in steps:
                nxt = (cur[0] + dy, cur[1] + dx)
                if 0 <= nxt[0] < self.h and 0 <= nxt[1] < self.w and self.ok[nxt] and nxt not in prev:
                    prev[nxt] = cur
                    queue.append(nxt)
        if goal not in prev:
            return None
        cells, cur = [], goal
        while cur is not None:
            cells.append(cur)
            cur = prev[cur]
        pts = [a] + [(self.x0 + x * self.CELL, self.y0 + y * self.CELL) for y, x in reversed(cells)] + [b]
        line = LineString(pts)
        simple = line.simplify(0.3)
        if self.inside.buffer(0.1).contains(simple):
            line = simple
        if line.length > max_len:
            return None
        return [tuple(c) for c in line.coords]


def digitize(labels, palette, s: Settings):
    """Builds the stitch pattern. Returns (pattern, sources): sources[i] is the palette index
    sewn by thread i, or "outline"."""
    H, W = labels.shape
    center = (W * PX_MM / 2.0, H * PX_MM / 2.0)

    satin_width = {"auto": s.satin_max_mm, "satin": 12.0, "tatami": 0.0}[s.fill_type]
    pen, blocks, all_polys = None, [], []
    for ci, rgb in enumerate(palette):
        mask = labels == ci
        if not mask.any():
            continue
        polys = _mask_polygons(mask, s.min_area_mm2)
        if not polys:
            continue
        all_polys += polys
        wide, narrow = _split_narrow(polys, satin_width, s.min_area_mm2)
        # grow wide areas by the pull compensation: stitching pulls the fabric in, and the growth
        # also overlaps neighbouring colors slightly so no fabric shows between them
        wide = [w for w in _polygons_of(unary_union([w.buffer(s.pull_comp_mm) for w in wide]))
                if w.area >= s.min_area_mm2]

        runs = []
        todo = list(wide)
        while todo:
            if pen is None:
                k = 0
            else:
                k = min(range(len(todo)), key=lambda i: math.dist(pen, todo[i].representative_point().coords[0]))
            poly = todo.pop(k)
            new, pen = _tatami(poly, center, s, pen)
            runs += new
        todo = list(narrow)
        while todo:
            if pen is None:
                k = 0
            else:
                k = min(range(len(todo)), key=lambda i: math.dist(pen, todo[i].representative_point().coords[0]))
            poly = todo.pop(k)
            done = _satin_runs(poly, s, pen)
            if done is None:  # too odd a shape for a column: fill it instead
                new, pen = _tatami(poly.buffer(s.pull_comp_mm), center, s, pen)
                runs += new
            else:
                new, pen = done
                runs += new
        if s.edge != "none" and runs:
            edge, pen = _line_runs(_rings(polys), s.edge, s.edge_width_mm, pen, s)
            runs += edge
        if runs:
            blocks.append((rgb, runs, ci, unary_union(polys)))

    if s.outline != "none" and blocks:
        union = unary_union([p.buffer(max(0.0, s.outline_offset_mm)) for p in all_polys])
        shell = [Polygon(p.exterior) for p in _polygons_of(union)]  # outer edge only, holes ignored
        runs, pen = _line_runs(_rings(shell), s.outline, s.outline_width_mm, pen, s)
        if runs:
            blocks.append((tuple(s.outline_color), runs, "outline", None))

    if not blocks:
        raise ValueError("Nothing to stitch. Try more colors, or turn off background removal.")

    def units(p):  # mm -> embroidery units, design centered on the origin
        return (p[0] - center[0]) * UNITS_PER_MM, (p[1] - center[1]) * UNITS_PER_MM

    pattern = EmbPattern()
    for rgb, runs, _, region in blocks:
        if pattern.threadlist:
            pattern.trim()
            pattern.color_change()
        thread = EmbThread()
        thread.set_color(*rgb)
        thread.description = nearest_brother_thread(rgb)[1]
        thread.brand = "Brother"
        pattern.add_thread(thread)

        # Moving to the next part of the same color: if a straight path stays inside this color's
        # shapes, sew small running stitches along it (hidden in the same-color stitching) instead
        # of cutting the thread. Every trim leaves a tail on the fabric, so this is much cleaner.
        inside = region.buffer(-0.2) if region is not None else None
        router = None
        if inside is not None and not inside.is_empty:
            shapely.prepare(inside)
            router = _Router(inside)
        last = None
        for run in runs:
            run = _tidy(run)
            if len(run) < 2:
                continue
            gap = math.dist(last, run[0]) if last is not None else 0.0
            route = None
            if last is not None and gap > 0.05 and router is not None:
                route = router.path(tuple(last), tuple(run[0]), max_len=max(25.0, 2.5 * gap))
            if route:
                path = [route[0]]
                for p in route[1:]:
                    _travel(path, p, 2.5)
                    path.append(p)
                for p in path[1:]:
                    pattern.add_stitch_absolute(STITCH, *units(p))
            else:
                if last is not None and gap > s.trim_mm:
                    pattern.trim()
                pattern.add_stitch_absolute(JUMP, *units(run[0]))
            for p in run:  # the first stitch anchors the thread at the run start
                pattern.add_stitch_absolute(STITCH, *units(p))
            last = run[-1]
    pattern.trim()
    pattern.end()
    return pattern, [source for _, _, source, _ in blocks]


# --------------------------------------------------------------------------- output


def describe(pattern: EmbPattern) -> dict:
    n = max(1, len(pattern.threadlist))
    stitches, length = [0] * n, [0.0] * n
    color, prev = 0, None
    for x, y, cmd in pattern.stitches:
        c = cmd & 0xFF
        if c == STITCH:
            ci = min(color, n - 1)
            stitches[ci] += 1
            if prev is not None:
                length[ci] += math.hypot(x - prev[0], y - prev[1])
        elif c == COLOR_CHANGE:
            color += 1
        elif c == END:
            break
        prev = (x, y)

    threads = []
    for i, t in enumerate(pattern.threadlist):
        threads.append({
            "hex": t.hex_color(), "rgb": (t.get_red(), t.get_green(), t.get_blue()), "name": t.description or "",
            "stitches": stitches[i],
            # top thread: stitch length plus a little per stitch for passing through the fabric
            "meters": (length[i] / UNITS_PER_MM + 0.8 * stitches[i]) / 1000,
        })
    min_x, min_y, max_x, max_y = pattern.bounds()
    total = sum(stitches)
    return {
        "width_mm": (max_x - min_x) / UNITS_PER_MM,
        "height_mm": (max_y - min_y) / UNITS_PER_MM,
        "stitches": total,
        "trims": pattern.count_stitch_commands(TRIM),
        "colors": len(pattern.threadlist),
        "minutes": total / 650,  # typical home machine speed
        "meters": sum(t["meters"] for t in threads),
        "threads": threads,
    }


def convert(img: Image.Image, s: Settings) -> Result:
    labels, palette, groups, skipped = reduce_colors(img, s)
    pattern, sources = digitize(labels, palette, s)
    info = describe(pattern)
    threads = []
    for thread, source in zip(info["threads"], sources):
        entry = dict(thread, kind="outline" if source == "outline" else "color", skipped=False)
        entry["keys"] = [] if source == "outline" else groups[source]
        threads.append(entry)
    for key in skipped:
        threads.append({"kind": "color", "keys": [key], "skipped": True, "rgb": key, "hex": hex_color(key),
                        "name": nearest_brother_thread(key)[1], "stitches": 0, "meters": 0.0})
    return Result(pattern, labels, palette, info, threads)


def render_preview(pattern: EmbPattern, max_px=1400, background=(236, 232, 224), hoop_mm=None) -> Image.Image:
    """Draws the stitches as thread lines (2x supersampled for smooth edges).

    With `hoop_mm` = (width, height) the hoop's sewing field is outlined around the design.
    """
    min_x, min_y, max_x, max_y = pattern.bounds()
    if hoop_mm:
        hw, hh = hoop_mm[0] * UNITS_PER_MM / 2, hoop_mm[1] * UNITS_PER_MM / 2
        min_x, min_y, max_x, max_y = min(min_x, -hw), min(min_y, -hh), max(max_x, hw), max(max_y, hh)
    w_mm, h_mm = (max_x - min_x) / UNITS_PER_MM, (max_y - min_y) / UNITS_PER_MM
    ppm = max(2.0, min(12.0, max_px / max(w_mm, h_mm, 1)))  # preview pixels per mm
    ss, margin = 2, 4
    k = ppm * ss / UNITS_PER_MM
    size = (int((w_mm + 2 * margin) * ppm * ss), int((h_mm + 2 * margin) * ppm * ss))
    img = Image.new("RGB", size, background)
    draw = ImageDraw.Draw(img)
    width = max(1, round(0.4 * ppm * ss))  # thread is ~0.4 mm thick
    ox, oy = margin * ppm * ss - min_x * k, margin * ppm * ss - min_y * k

    if hoop_mm:
        draw.rounded_rectangle((-hw * k + ox, -hh * k + oy, hw * k + ox, hh * k + oy),
                               radius=4 * ppm * ss, outline=(170, 165, 155), width=ss * 2)
        for x0, y0, x1, y1 in ((-6, 0, 6, 0), (0, -6, 0, 6)):  # center mark
            draw.line((x0 * ppm * ss + ox, y0 * ppm * ss + oy, x1 * ppm * ss + ox, y1 * ppm * ss + oy),
                      fill=(170, 165, 155), width=ss)

    colors = [(t.get_red(), t.get_green(), t.get_blue()) for t in pattern.threadlist] or [(0, 0, 0)]
    ci, pos, line = 0, None, []

    def flush():
        if len(line) > 1:
            draw.line(line, fill=colors[min(ci, len(colors) - 1)], width=width)
        line.clear()

    for x, y, cmd in pattern.stitches:
        p = (x * k + ox, y * k + oy)
        cmd &= 0xFF
        if cmd == STITCH:
            if not line and pos is not None:
                line.append(pos)
            line.append(p)
        else:
            flush()
            if cmd == COLOR_CHANGE:
                ci += 1
            elif cmd == END:
                break
        pos = p
    flush()
    return img.resize((size[0] // ss, size[1] // ss), Image.LANCZOS)


def save(pattern: EmbPattern, path) -> str:
    """Writes the pattern; the format comes from the extension (.pes if unknown)."""
    if os.path.splitext(path)[1].lower() not in WRITABLE:
        path += ".pes"
    # tie_on / tie_off add small lock stitches so the thread doesn't unravel after trims
    pattern.write(path, tie_on=True, tie_off=True)
    return path


def main(argv=None):
    d = Settings()
    ap = argparse.ArgumentParser(description="Convert an image or text into an embroidery file (Brother .pes).")
    ap.add_argument("image", help='image file, or "text:Your words" for lettering')
    ap.add_argument("output", help="output file, e.g. design.pes (also .dst .jef .exp .vp3)")
    ap.add_argument("--font", default=os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "arialbd.ttf"),
                    help="font file for text:")
    ap.add_argument("--width", type=float, default=d.width_mm, help="max design width in mm")
    ap.add_argument("--height", type=float, default=d.height_mm, help="max design height in mm")
    ap.add_argument("--rotate", type=int, default=0, choices=(0, 90, 180, 270))
    ap.add_argument("--mirror", action="store_true")
    ap.add_argument("--colors", type=int, default=d.colors, help="number of thread colors")
    ap.add_argument("--keep-background", action="store_true", help="stitch the background color too")
    ap.add_argument("--brother-colors", action="store_true", help="snap colors to Brother threads")
    ap.add_argument("--cleanup", type=int, default=d.cleanup, choices=range(4))
    ap.add_argument("--fill", default=d.fill_type, choices=FILL_TYPES, help="auto = satin for narrow shapes")
    ap.add_argument("--satin-width", type=float, default=d.satin_max_mm, help="auto: max satin width in mm")
    ap.add_argument("--spacing", type=float, default=d.row_spacing_mm, help="tatami row spacing in mm")
    ap.add_argument("--stitch", type=float, default=d.stitch_length_mm, help="tatami stitch length in mm")
    ap.add_argument("--angle", type=float, default=d.fill_angle, help="fill angle in degrees")
    ap.add_argument("--fixed-angle", action="store_true", help="always use --angle (default: long shapes follow their length)")
    ap.add_argument("--pull", type=float, default=d.pull_comp_mm, help="pull compensation in mm")
    ap.add_argument("--no-underlay", action="store_true")
    ap.add_argument("--edge", default=d.edge, choices=LINE_TYPES, help="finish on every color's edges")
    ap.add_argument("--edge-width", type=float, default=d.edge_width_mm)
    ap.add_argument("--outline", default=d.outline, choices=LINE_TYPES, help="line around the whole design")
    ap.add_argument("--outline-width", type=float, default=d.outline_width_mm)
    ap.add_argument("--outline-offset", type=float, default=d.outline_offset_mm)
    ap.add_argument("--outline-color", default="#000000")
    ap.add_argument("--preview", help="also write a PNG preview of the stitches")
    args = ap.parse_args(argv)

    hx = args.outline_color.lstrip("#")
    s = Settings(
        width_mm=args.width, height_mm=args.height, rotate=args.rotate, mirror=args.mirror, colors=args.colors,
        remove_background=not args.keep_background, brother_colors=args.brother_colors, cleanup=args.cleanup,
        fill_type=args.fill, satin_max_mm=args.satin_width, row_spacing_mm=args.spacing,
        stitch_length_mm=args.stitch, fill_angle=args.angle, auto_angle=not args.fixed_angle, pull_comp_mm=args.pull,
        underlay=not args.no_underlay, edge=args.edge, edge_width_mm=args.edge_width, outline=args.outline,
        outline_width_mm=args.outline_width, outline_offset_mm=args.outline_offset,
        outline_color=tuple(int(hx[i : i + 2], 16) for i in (0, 2, 4)),
    )
    if args.image.startswith("text:"):
        img = make_text_image(args.image[5:].replace("\\n", "\n"), args.font)
    else:
        img = load_image(args.image)
    result = convert(img, s)
    path = save(result.pattern, args.output)
    if args.preview:
        render_preview(result.pattern).save(args.preview)

    info = result.info
    print(f"Saved {path}")
    print(f"Size {info['width_mm']:.1f} x {info['height_mm']:.1f} mm, {info['stitches']:,} stitches, "
          f"{info['colors']} colors, {info['trims']} trims, ~{info['minutes']:.0f} min, ~{info['meters']:.0f} m thread")
    for i, t in enumerate(result.threads, 1):
        note = "  (skipped)" if t["skipped"] else f"  {t['stitches']:,} st, {t['meters']:.1f} m"
        print(f"  {i}. {t['hex']}  {t['name']}{' [outline]' if t['kind'] == 'outline' else ''}{note}")


if __name__ == "__main__":
    main()
