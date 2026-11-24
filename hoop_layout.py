"""Windows editor for placing artwork on the 4 x 4 hoop."""

import tkinter as tk
from dataclasses import dataclass, replace
from tkinter import ttk

from PIL import Image, ImageTk

from digitizer import LAYOUT_MARGIN_MM, LAYOUT_SIZE_MM


@dataclass
class HoopItem:
    title: str
    image: Image.Image
    x: float
    y: float
    width: float
    height: float

    def bounds(self):
        return (self.x - self.width / 2, self.y - self.height / 2,
                self.x + self.width / 2, self.y + self.height / 2)

    def move(self, x, y):
        left = LAYOUT_MARGIN_MM + self.width / 2
        top = LAYOUT_MARGIN_MM + self.height / 2
        self.x = min(max(x, left), LAYOUT_SIZE_MM - left)
        self.y = min(max(y, top), LAYOUT_SIZE_MM - top)


def layout_image(items):
    """Keep the empty hoop margins transparent and preserve physical positions."""
    pixels_per_mm = 10
    side = round(LAYOUT_SIZE_MM * pixels_per_mm)
    image = Image.new("RGBA", (side, side))
    for item in items:
        left, top, right, bottom = (round(value * pixels_per_mm) for value in item.bounds())
        ink = item.image.resize((right - left, bottom - top), Image.Resampling.LANCZOS)
        image.alpha_composite(ink, (left, top))
    return image


class HoopLayout(tk.Toplevel):
    SIDE = 440
    SCALE = SIDE / LAYOUT_SIZE_MM
    MAX_SIZE = LAYOUT_SIZE_MM - 2 * LAYOUT_MARGIN_MM

    def __init__(self, parent, items, style, styles, open_artwork, on_use):
        super().__init__(parent)
        self.title("4 × 4 Hoop Layout")
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)
        self.items = [replace(item) for item in items]
        self.selected = None
        self.drag_offset = None
        self.photos = {}
        self.style = tk.StringVar(self, style)

        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(frame, width=self.SIDE, height=self.SIDE, background="#eaf0f5", highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="n")
        self.canvas.bind("<Button-1>", self._start_drag)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", lambda _: setattr(self, "drag_offset", None))
        for i in range(1, 4):
            line = self.SIDE * i / 4
            self.canvas.create_line(line, 0, line, self.SIDE, fill="#c5d3e2")
            self.canvas.create_line(0, line, self.SIDE, line, fill="#c5d3e2")
        inset = LAYOUT_MARGIN_MM * self.SCALE
        self.canvas.create_rectangle(inset, inset, self.SIDE - inset, self.SIDE - inset, outline="#6a89ab", dash=(4, 4))

        controls = ttk.Frame(frame, padding=(12, 0, 0, 0))
        controls.grid(row=0, column=1, sticky="ns")
        for label, motif_only in (("Add text...", False), ("Add motif...", True)):
            ttk.Button(controls, text=label,
                       command=lambda flag=motif_only: open_artwork(flag, self, self._insert_parts)).pack(fill="x", pady=2)
        ttk.Separator(controls).pack(fill="x", pady=10)
        self.selection_label = ttk.Label(controls, text="Select or drag an item")
        self.selection_label.pack(anchor="w")
        self.size_controls = {}
        for axis in ("width", "height"):
            ttk.Label(controls, text=axis.title()).pack(anchor="w", pady=(8, 0))
            slider = ttk.Scale(controls, from_=2, to=self.MAX_SIZE, length=170,
                               command=lambda raw, key=axis: self._resize(key, float(raw)))
            slider.pack(fill="x")
            value = ttk.Label(controls)
            value.pack(anchor="e")
            self.size_controls[axis] = slider, value

        self.duplicate = ttk.Button(controls, text="Duplicate", command=self._duplicate)
        self.duplicate.pack(fill="x", pady=(6, 2))
        self.delete = ttk.Button(controls, text="Remove", command=self._delete)
        self.delete.pack(fill="x", pady=2)
        ttk.Separator(controls).pack(fill="x", pady=10)
        ttk.Label(controls, text="Stitch style").pack(anchor="w")
        ttk.Combobox(controls, textvariable=self.style, values=list(styles), state="readonly", width=24).pack(fill="x")

        buttons = ttk.Frame(frame)
        buttons.grid(row=1, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="left", padx=(0, 6))

        def apply():
            image = layout_image(self.items)
            on_use(image, self.items, self.style.get())
            self.destroy()

        self.use = ttk.Button(buttons, text="Use layout", command=apply)
        self.use.pack(side="left")
        self.bind("<Escape>", lambda _: self.destroy())
        self.bind("<Delete>", lambda _: self._delete())
        self._show_selection()

    def _insert_parts(self, parts):
        for title, image, height in parts:
            width = height * image.width / image.height
            scale = min(1, self.MAX_SIZE / max(width, height))
            self._insert(title, image, max(2, width * scale), max(2, height * scale))
        self._show_selection()

    def _insert(self, title, image, width, height):
        item = HoopItem(title, image, 0, 0, width, height)
        occupied = [other.bounds() for other in self.items]
        item.move(0, 0)
        x0, y0 = item.x, item.y
        for y in range(round((LAYOUT_SIZE_MM - 2 * y0) / 3) + 1):
            for x in range(round((LAYOUT_SIZE_MM - 2 * x0) / 3) + 1):
                item.move(x0 + x * 3, y0 + y * 3)
                l, t, r, b = item.bounds()
                if not any(l < r2 + 2 and r > l2 - 2 and t < b2 + 2 and b > t2 - 2
                           for l2, t2, r2, b2 in occupied):
                    break
            else:
                continue
            break
        else:
            item.move(LAYOUT_SIZE_MM / 2, LAYOUT_SIZE_MM / 2)
        self.items.append(item)
        self.selected = item

    def _duplicate(self):
        if item := self.selected:
            self._insert(item.title, item.image, item.width, item.height)
            self._show_selection()

    def _delete(self):
        if self.selected is not None:
            self.photos.pop(id(self.selected), None)
            self.items = [item for item in self.items if item is not self.selected]
            self.selected = None
            self._show_selection()

    def _start_drag(self, event):
        x, y = event.x / self.SCALE, event.y / self.SCALE
        self.selected = None
        for item in reversed(self.items):
            left, top, right, bottom = item.bounds()
            if left <= x <= right and top <= y <= bottom:
                self.selected = item
                self.drag_offset = (item.x - x, item.y - y)
                break
        self._show_selection()

    def _drag(self, event):
        if self.selected is not None and self.drag_offset is not None:
            dx, dy = self.drag_offset
            self.selected.move(event.x / self.SCALE + dx, event.y / self.SCALE + dy)
            self._draw()

    def _resize(self, axis, value):
        item = self.selected
        if item is not None:
            setattr(item, axis, value)
            item.move(item.x, item.y)
            self.size_controls[axis][1].config(text=f"{value / 25.4:.2f} in")
            self._draw()

    def _show_selection(self):
        item = self.selected
        self.selection_label.config(text=item.title if item else "Select or drag an item")
        for axis, (slider, label) in self.size_controls.items():
            if item:
                slider.set(getattr(item, axis))
                label.config(text=f"{getattr(item, axis) / 25.4:.2f} in")
            slider.config(state="normal" if item else "disabled")
        self.duplicate.config(state="normal" if item else "disabled")
        self.delete.config(state="normal" if item else "disabled")
        self.use.config(state="normal" if self.items else "disabled")
        self._draw()

    def _draw(self):
        self.canvas.delete("artwork")
        for item in self.items:
            width, height = round(item.width * self.SCALE), round(item.height * self.SCALE)
            key = (width, height)
            cached = self.photos.get(id(item))
            if cached is None or cached[0] != key:
                image = item.image.resize(key, Image.Resampling.LANCZOS)
                cached = key, ImageTk.PhotoImage(image, master=self)
                self.photos[id(item)] = cached
            self.canvas.create_image(item.x * self.SCALE, item.y * self.SCALE, image=cached[1], tags="artwork")
        if self.selected is not None:
            box = [value * self.SCALE for value in self.selected.bounds()]
            self.canvas.create_rectangle(*box, outline="#376ca6", width=2, tags="artwork")
