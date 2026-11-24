"""Image -> embroidery digitizer.

Turns a raster image (logo, clipart, drawing) or text into embroidery and writes a
Brother .PES file (or DST / JEF / EXP / VP3 for other machines).

Pipeline:
  1. scale the image to the requested size in millimetres
  2. reduce it to N thread colors (k-means), clean up specks, drop background
  3. stitch every color: tatami fill for large areas, satin for narrow shapes,
     optional running / triple / satin edges
  4. optional outline around the whole design
  5. assemble stitches, jumps, trims and color changes with pyembroidery

Command line:
  python digitizer.py logo.png logo.pes --width 90 --height 90 --colors 4
  python digitizer.py "text:Anna" anna.pes --font "C:/Windows/Fonts/arialbd.ttf" --width 80
"""

from __future__ import annotations

import argparse
import math
import os
from dataclasses import dataclass, field
from functools import lru_cache
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
from pyembroidery import COLOR_CHANGE, END, JUMP, STITCH, TRIM, EmbPattern, EmbThread

PX_MM = 0.2  # working resolution: millimetres per pixel
UNITS_PER_MM = 10  # pyembroidery coordinates are 0.1 mm
MIN_STITCH_MM = 0.4  # shorter stitches just knot the thread
SATIN_SPLIT_MM = 7.0  # satin stitches longer than this are split so they don't snag

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
    "Woven cotton": ({"row_spacing_mm": 0.3, "satin_spacing_mm": 0.4, "pull_comp_mm": 0.15, "underlay": True},
                     "Medium tear-away stabilizer."),
    "T-shirt / knit": ({"row_spacing_mm": 0.4, "satin_spacing_mm": 0.45, "pull_comp_mm": 0.25, "underlay": True},
                       "Cut-away stabilizer. Knits stretch, so density is kept light."),
    "Denim / canvas": ({"row_spacing_mm": 0.3, "satin_spacing_mm": 0.4, "pull_comp_mm": 0.1, "underlay": True},
                       "Tear-away stabilizer and a size 90/14 needle."),
    "Towel / fleece": ({"row_spacing_mm": 0.25, "satin_spacing_mm": 0.35, "pull_comp_mm": 0.2, "underlay": True},
                       "Water-soluble topping on top so stitches don't sink into the pile."),
    "Cap / thick": ({"row_spacing_mm": 0.35, "satin_spacing_mm": 0.4, "pull_comp_mm": 0.2, "underlay": True},
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
    satin_max_mm: float = 5.0  # "auto": shapes narrower than this become satin
    satin_spacing_mm: float = 0.4
    row_spacing_mm: float = 0.3  # tatami density; smaller = denser
    stitch_length_mm: float = 3.5
    fill_angle: float = 45.0
    pull_comp_mm: float = 0.15  # widens rows to offset fabric pull-in
    underlay: bool = True
    trim_mm: float = 3.0  # jumps longer than this get a trim
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
    centers = _kmeans(fit, s.colors + extra_bg)
    labels = np.full(opaque.shape, -1, np.int16)
    labels[opaque] = _nearest_center(rgb[opaque], centers)

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


def _box_filter(mask, radius, erode):
    """Erodes (shrinks) or dilates (grows) a boolean mask with a square of `radius` pixels."""
    if radius <= 0:
        return mask
    k = 2 * radius + 1
    out = mask
    for axis in (1, 0):
        pad = [(0, 0), (0, 0)]
        pad[axis] = (radius + 1, radius)
        c = np.cumsum(np.pad(out.astype(np.int32), pad), axis=axis)
        window = c[:, k:] - c[:, :-k] if axis == 1 else c[k:, :] - c[:-k, :]
        out = window == k if erode else window > 0
    return out


def _erode(mask, radius):
    return _box_filter(mask, radius, erode=True)


def _dilate(mask, radius):
    return _box_filter(mask, radius, erode=False)


def _components(mask):
    """Labels 4-connected regions. Returns (label image with 0 = empty, [(y0, x0, y1, x1), ...])."""
    H, W = mask.shape
    labels = np.zeros((H, W), np.int32)
    edges = np.diff(np.pad(mask, ((0, 0), (1, 1))).astype(np.int8), axis=1)
    rows, starts = np.nonzero(edges == 1)
    _, ends = np.nonzero(edges == -1)  # exclusive
    n = len(rows)
    if n == 0:
        return labels, []

    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    R, S, E = rows.tolist(), starts.tolist(), ends.tolist()
    ptr = np.searchsorted(rows, np.arange(H + 2)).tolist()
    for r in range(H - 1):  # union runs that overlap on the next row
        i, i_end, j, j_end = ptr[r], ptr[r + 1], ptr[r + 1], ptr[r + 2]
        while i < i_end and j < j_end:
            if S[i] < E[j] and S[j] < E[i]:
                a, b = find(i), find(j)
                if a != b:
                    parent[b] = a
            if E[i] < E[j]:
                i += 1
            else:
                j += 1

    ids, boxes = {}, []
    for k in range(n):
        root = find(k)
        if root not in ids:
            ids[root] = len(ids) + 1
            boxes.append([R[k], S[k], R[k] + 1, E[k]])
        c = ids[root]
        box = boxes[c - 1]
        box[0], box[1] = min(box[0], R[k]), min(box[1], S[k])
        box[2], box[3] = max(box[2], R[k] + 1), max(box[3], E[k])
        labels[R[k], S[k] : E[k]] = c
    return labels, [tuple(b) for b in boxes]


def _fill_holes(mask):
    background, _ = _components(~mask)
    touching = np.unique(np.concatenate([background[0], background[-1], background[:, 0], background[:, -1]]))
    holes = (background > 0) & ~np.isin(background, touching)
    return mask | holes


# marching-squares segments per cell case (corner bits: top-left 8, top-right 4,
# bottom-right 2, bottom-left 1), as pairs of crossed cell edges
_MS = {
    1: (("L", "B"),), 2: (("B", "R"),), 3: (("L", "R"),), 4: (("T", "R"),), 5: (("T", "R"), ("L", "B")),
    6: (("T", "B"),), 7: (("T", "L"),), 8: (("T", "L"),), 9: (("T", "B"),), 10: (("T", "L"), ("B", "R")),
    11: (("T", "R"),), 12: (("L", "R"),), 13: (("R", "B"),), 14: (("L", "B"),),
}


def _contours(mask):
    """Closed boundary loops (outer edges and holes) of a mask, as (n, 2) arrays of pixel coords."""
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


# --------------------------------------------------------------------------- stitches


def _scan(mask, box, center, angle, spacing_px, step=0.5):
    """Intersects parallel rows at `angle` degrees with a mask.

    `mask` is a crop whose top-left corner sits at `box` (x, y) in the full image; rows lie on
    a lattice anchored at `center` so neighbouring areas line up. Returns None if empty.
    """
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return None
    H, W = mask.shape
    bx, by = box
    cx, cy = center
    a = math.radians(angle)
    ux, uy = math.cos(a), -math.sin(a)  # along a row (image y axis points down)
    nx, ny = -uy, ux  # across rows

    px, py = xs + bx + 0.5 - cx, ys + by + 0.5 - cy
    proj_n, proj_t = px * nx + py * ny, px * ux + py * uy
    first_row = math.floor((proj_n.min() - 1) / spacing_px)
    last_row = math.ceil((proj_n.max() + 1) / spacing_px)
    grid_rows = np.arange(first_row, last_row + 1)
    ds = grid_rows * spacing_px
    ts = np.arange(proj_t.min() - 1, proj_t.max() + 1, step)

    seg_r, seg_t0, seg_t1 = [], [], []
    chunk = max(1, 2_000_000 // len(ts))
    for i in range(0, len(ds), chunk):
        d = ds[i : i + chunk, None]
        X = np.floor(cx + d * nx + ts * ux).astype(np.int64) - bx
        Y = np.floor(cy + d * ny + ts * uy).astype(np.int64) - by
        ok = (X >= 0) & (X < W) & (Y >= 0) & (Y < H)
        inside = np.zeros(X.shape, bool)
        inside[ok] = mask[Y[ok], X[ok]]
        edges = np.diff(np.pad(inside, ((0, 0), (1, 1))).astype(np.int8), axis=1)
        sr, sc = np.nonzero(edges == 1)
        _, ec = np.nonzero(edges == -1)
        seg_r.append(sr + i)
        seg_t0.append(ts[sc] - step / 2)
        seg_t1.append(ts[ec - 1] + step / 2)
    return SimpleNamespace(
        grid_rows=grid_rows, ds=ds, r=np.concatenate(seg_r), t0=np.concatenate(seg_t0),
        t1=np.concatenate(seg_t1), cx=cx, cy=cy, ux=ux, uy=uy, nx=nx, ny=ny,
    )


def _fill(mask, box, center, angle, spacing_px, stitch_px, pull_px, min_len_px, min_area_px, pen, satin=False):
    """Fills a mask with parallel rows sewn back and forth.

    Tatami: needle points every `stitch_px` along each row, staggered between rows.
    Satin: one needle point at each end of a row, so stitches zigzag across the shape
    (rows longer than `stitch_px` are split).

    Returns (runs, pen): each run is a list of (x, y) pixel points sewn continuously;
    consecutive runs are joined with a jump. `pen` is the current needle position.
    """
    rows = _scan(mask, box, center, angle, spacing_px)
    if rows is None:
        return [], pen
    keep = (rows.t1 - rows.t0) >= min_len_px
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
    min_px = MIN_STITCH_MM / PX_MM

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
        if sum(T1[c] - T0[c] for c, _ in chain) * spacing_px < min_area_px:
            continue

        pts = []
        for idx, forward in chain:
            r, t0, t1 = R[idx], T0[idx] - pull_px, T1[idx] + pull_px
            phase = (grid_rows[r] % 3) * stitch_px / 3.0  # brick offset hides row lines
            k0 = math.ceil((t0 + min_px - phase) / stitch_px)
            k1 = math.floor((t1 - min_px - phase) / stitch_px)
            inner = [phase + k * stitch_px for k in range(k0, k1 + 1)]
            start, end = (t0, t1) if forward else (t1, t0)
            if not forward:
                inner.reverse()
            if satin and pts:
                row_ts = inner + [end]  # the needle is already at this row's start side
            else:
                row_ts = [start] + inner + [end]
            row_pts = [point(r, t) for t in row_ts]
            if pts:
                _travel(pts, row_pts[0], stitch_px)
            pts.extend(row_pts)
        runs.append(pts)
    return runs, pen


def _travel(pts, target, max_len):
    """Appends evenly spaced stitches from the last point to `target` (exclusive)."""
    x0, y0 = pts[-1]
    dist = math.hypot(target[0] - x0, target[1] - y0)
    pieces = math.ceil(dist / max_len)
    for i in range(1, pieces):
        f = i / pieces
        pts.append((x0 + (target[0] - x0) * f, y0 + (target[1] - y0) * f))


def _satin_angle(mask, box, center):
    """Row angle that crosses a shape the short way (smallest typical row length)."""
    best_len, best_angle = np.inf, 0
    for angle in range(0, 180, 15):
        rows = _scan(mask, box, center, angle, 1.0, step=1.0)
        if rows is None or len(rows.r) == 0:
            continue
        length = np.percentile(rows.t1 - rows.t0, 90)
        if length < best_len:
            best_len, best_angle = length, angle
    return best_angle


def _color_runs(mask, center, s: Settings, pen):
    """Underlay + tatami for wide areas and satin for narrow shapes of one color."""
    px = 1 / PX_MM
    min_area = s.min_area_mm2 * px * px
    inset = max(1, round(0.4 * px))
    runs, wide, narrow = [], mask, []

    if s.fill_type in ("auto", "satin"):
        comps, boxes = _components(mask)
        if s.fill_type == "satin":
            narrow_ids = list(range(1, len(boxes) + 1))
        else:  # a shape is narrow if shrinking it by half the satin width wipes it out
            core = _erode(mask, max(1, round(s.satin_max_mm * px / 2)))
            thick = set(np.unique(comps[core]).tolist())
            narrow_ids = [i for i in range(1, len(boxes) + 1) if i not in thick]
        if narrow_ids:
            wide = mask & ~np.isin(comps, narrow_ids)
            for i in narrow_ids:
                y0, x0, y1, x1 = boxes[i - 1]
                narrow.append((comps[y0:y1, x0:x1] == i, (x0, y0)))

    if wide.any():
        if s.underlay:  # sparse perpendicular rows, inset from the edge, stabilise the fabric
            under, pen = _fill(_erode(wide, inset), (0, 0), center, s.fill_angle + 90, 2.0 * px, 3.0 * px,
                               0, 1.0 * px, 4 * min_area, pen)
            runs += under
        top, pen = _fill(wide, (0, 0), center, s.fill_angle, s.row_spacing_mm * px, s.stitch_length_mm * px,
                         s.pull_comp_mm * px, 0.5 * px, min_area, pen)
        runs += top

    while narrow:
        def distance(item):
            part, (bx, by) = item
            return 0 if pen is None else math.dist(pen, (bx + part.shape[1] / 2, by + part.shape[0] / 2))

        part, box = narrow.pop(min(range(len(narrow)), key=lambda i: distance(narrow[i])))
        angle = _satin_angle(part, box, center)
        if s.underlay:  # runs along the column's length
            under, pen = _fill(_erode(part, inset), box, center, angle + 90, 1.5 * px, 3.0 * px,
                               0, 1.0 * px, min_area, pen)
            runs += under
        top, pen = _fill(part, box, center, angle, s.satin_spacing_mm * px / 2, SATIN_SPLIT_MM * px,
                         s.pull_comp_mm * px, 0.3 * px, min_area, pen, satin=True)
        runs += top
    return runs, pen


def _resample_closed(loop, step):
    closed = np.vstack([loop, loop[:1]])
    lengths = np.hypot(*np.diff(closed, axis=0).T)
    cum = np.concatenate([[0], np.cumsum(lengths)])
    n = max(4, int(round(cum[-1] / step)))
    at = np.linspace(0, cum[-1], n, endpoint=False)
    return np.stack([np.interp(at, cum, closed[:, 0]), np.interp(at, cum, closed[:, 1])], axis=1)


def _line_runs(loops, kind, width_px, pen, s: Settings):
    """Sews closed loops as running, triple (bean) or satin lines, nearest loop first."""
    px = 1 / PX_MM
    prepared = []
    for loop in loops:
        if len(loop) < 8:
            continue
        for _ in range(2):  # smooth out the pixel staircase
            loop = sum(np.roll(loop, shift, axis=0) for shift in range(-2, 3)) / 5
        if np.hypot(*(np.roll(loop, -1, axis=0) - loop).T).sum() >= 3 * px:
            prepared.append(loop)

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
            under = _resample_closed(loop, 2.0 * px)  # center walk underlay
            path = [tuple(p) for p in under] + [tuple(under[0])]
            step = s.satin_spacing_mm * px / 2
            c = _resample_closed(loop, step)
            if len(c) % 2:
                c = c[:-1]
            k = max(1, round(0.6 * px / step))
            tangent = np.roll(c, -k, axis=0) - np.roll(c, k, axis=0)
            tangent /= np.maximum(np.hypot(tangent[:, 0], tangent[:, 1]), 1e-9)[:, None]
            normal = np.stack([-tangent[:, 1], tangent[:, 0]], axis=1)
            side = np.where(np.arange(len(c)) % 2 == 0, 0.5, -0.5)[:, None]
            zigzag = c + normal * side * width_px
            path += [tuple(p) for p in zigzag] + [tuple(zigzag[0]), tuple(zigzag[1])]
        else:
            q = _resample_closed(loop, s.running_length_mm * px)
            q = np.vstack([q, q[:1]])
            path = [tuple(q[0])]
            for a, b in zip(q[:-1], q[1:]):
                path += [tuple(b), tuple(a), tuple(b)] if kind == "triple" else [tuple(b)]
        runs.append(path)
        pen = path[-1]
    return runs, pen


def digitize(labels, palette, s: Settings):
    """Builds the stitch pattern. Returns (pattern, sources): sources[i] is the palette index
    sewn by thread i, or "outline"."""
    H, W = labels.shape
    center = (W / 2.0, H / 2.0)
    scale = PX_MM * UNITS_PER_MM
    px = 1 / PX_MM

    pen, blocks = None, []
    for ci, rgb in enumerate(palette):
        mask = labels == ci
        if not mask.any():
            continue
        runs, pen = _color_runs(mask, center, s, pen)
        if s.edge != "none" and runs:
            edge, pen = _line_runs(_contours(mask), s.edge, s.edge_width_mm * px, pen, s)
            runs += edge
        if runs:
            blocks.append((rgb, runs, ci))

    if s.outline != "none" and blocks:
        offset = max(0, round(s.outline_offset_mm * px))
        pad = offset + 2
        union = _fill_holes(_dilate(np.pad(labels >= 0, pad), offset))
        loops = [loop - pad for loop in _contours(union)]
        runs, pen = _line_runs(loops, s.outline, s.outline_width_mm * px, pen, s)
        if runs:
            blocks.append((tuple(s.outline_color), runs, "outline"))

    if not blocks:
        raise ValueError("Nothing to stitch. Try more colors, or turn off background removal.")

    def units(p):  # pixel -> embroidery units, design centered on the origin
        return (p[0] - W / 2) * scale, (p[1] - H / 2) * scale

    pattern = EmbPattern()
    trim_px = s.trim_mm * px
    for rgb, runs, _ in blocks:
        if pattern.threadlist:
            pattern.trim()
            pattern.color_change()
        thread = EmbThread()
        thread.set_color(*rgb)
        thread.description = nearest_brother_thread(rgb)[1]
        thread.brand = "Brother"
        pattern.add_thread(thread)

        last = None
        for run in runs:
            if last is not None and math.dist(last, run[0]) > trim_px:
                pattern.trim()
            pattern.add_stitch_absolute(JUMP, *units(run[0]))
            for p in run:  # the first stitch anchors the thread at the run start
                pattern.add_stitch_absolute(STITCH, *units(p))
            last = run[-1]
    pattern.trim()
    pattern.end()
    return pattern, [source for _, _, source in blocks]


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
        stitch_length_mm=args.stitch, fill_angle=args.angle, pull_comp_mm=args.pull,
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
