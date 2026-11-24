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
    is_letter: bool = False

    def bounds(self):
        return (self.x - self.width / 2, self.y - self.height / 2,
                self.x + self.width / 2, self.y + self.height / 2)

    def move(self, x, y):
        left = LAYOUT_MARGIN_MM + self.width / 2
        top = LAYOUT_MARGIN_MM + self.height / 2
        self.x = min(max(x, left), LAYOUT_SIZE_MM - left)
        self.y = min(max(y, top), LAYOUT_SIZE_MM - top)


def item_bounds(items):
    boxes = [item.bounds() for item in items]
    return (min(box[0] for box in boxes), min(box[1] for box in boxes),
            max(box[2] for box in boxes), max(box[3] for box in boxes))


@dataclass
class LayoutState:
    items: list
    selected_index: int | None
    all_letters: bool
    style: str


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
        self.all_letters = False
        self.drag_offset = None
        self.gesture_start = None
        self.undo_history = []
        self.syncing_controls = False
        self.photos = {}
        self.style = tk.StringVar(self, style)
        self.current_style = style

        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(frame, width=self.SIDE, height=self.SIDE, background="#eaf0f5", highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="n")
        self.canvas.bind("<Button-1>", self._start_drag)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._finish_gesture)
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
        self.select_letters = ttk.Button(controls, text="Select all letters", command=self._select_letters)
        self.select_letters.pack(fill="x", pady=2)
        self.undo_button = ttk.Button(controls, text="Undo (Ctrl+Z)", command=self._undo)
        self.undo_button.pack(fill="x", pady=2)
        ttk.Separator(controls).pack(fill="x", pady=10)
        self.selection_label = ttk.Label(controls, text="Select or drag an item")
        self.selection_label.pack(anchor="w")
        self.size_controls = {}
        for axis in ("width", "height"):
            ttk.Label(controls, text=axis.title()).pack(anchor="w", pady=(8, 0))
            slider = ttk.Scale(controls, from_=2, to=self.MAX_SIZE, length=170,
                               command=lambda raw, key=axis: self._resize(key, float(raw)))
            slider.pack(fill="x")
            slider.bind("<Button-1>", lambda _: self._begin_gesture())
            slider.bind("<ButtonRelease-1>", self._finish_gesture)
            value = ttk.Label(controls)
            value.pack(anchor="e")
            self.size_controls[axis] = slider, value

        self.duplicate = ttk.Button(controls, text="Duplicate", command=self._duplicate)
        self.duplicate.pack(fill="x", pady=(6, 2))
        self.delete = ttk.Button(controls, text="Remove", command=self._delete)
        self.delete.pack(fill="x", pady=2)
        ttk.Separator(controls).pack(fill="x", pady=10)
        ttk.Label(controls, text="Stitch style").pack(anchor="w")
        styles_picker = ttk.Combobox(controls, textvariable=self.style, values=list(styles), state="readonly", width=24)
        styles_picker.pack(fill="x")
        styles_picker.bind("<<ComboboxSelected>>", self._change_style)

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
        self.bind("<Control-z>", self._undo)
        self._show_selection()

    def _insert_parts(self, parts):
        before = self._snapshot()
        for title, image, height, is_letter in parts:
            width = height * image.width / image.height
            scale = min(1, self.MAX_SIZE / max(width, height))
            self._insert(title, image, max(2, width * scale), max(2, height * scale), is_letter)
        self._record(before)
        self._show_selection()

    def _insert(self, title, image, width, height, is_letter):
        item = HoopItem(title, image, 0, 0, width, height, is_letter)
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
        self.all_letters = False
        self.selected = item

    def _duplicate(self):
        if item := self.selected:
            before = self._snapshot()
            self._insert(item.title, item.image, item.width, item.height, item.is_letter)
            self._record(before)
            self._show_selection()

    def _delete(self):
        if self.selected is not None:
            before = self._snapshot()
            self.photos.pop(id(self.selected), None)
            self.items = [item for item in self.items if item is not self.selected]
            self.selected = None
            self._record(before)
            self._show_selection()

    def _selection(self):
        if self.all_letters:
            return [item for item in self.items if item.is_letter]
        return [self.selected] if self.selected is not None else []

    def _select_letters(self):
        self.all_letters = True
        self.selected = None
        self._show_selection()

    def _snapshot(self):
        index = next((i for i, item in enumerate(self.items) if item is self.selected), None)
        return LayoutState([replace(item) for item in self.items], index, self.all_letters, self.current_style)

    def _record(self, before):
        if self.items != before.items or self.current_style != before.style:
            self.undo_history.append(before)
            self.undo_button.config(state="normal")

    def _undo(self, _event=None):
        if self.undo_history:
            before = self.undo_history.pop()
            self.gesture_start = self.drag_offset = None
            self.items = before.items
            self.selected = None if before.selected_index is None else self.items[before.selected_index]
            self.all_letters = before.all_letters
            self.current_style = before.style
            self.style.set(before.style)
            self.photos.clear()
            self._show_selection()
        return "break"

    def _change_style(self, _event=None):
        before = self._snapshot()
        self.current_style = self.style.get()
        self._record(before)

    def _begin_gesture(self):
        self.gesture_start = self._snapshot()

    def _finish_gesture(self, _event=None):
        if self.gesture_start is not None:
            self._record(self.gesture_start)
        self.gesture_start = self.drag_offset = None

    def _start_drag(self, event):
        x, y = event.x / self.SCALE, event.y / self.SCALE
        hit = None
        for item in reversed(self.items):
            left, top, right, bottom = item.bounds()
            if left <= x <= right and top <= y <= bottom:
                hit = item
                break
        if not self.all_letters or hit is None or not hit.is_letter:
            self.all_letters = False
            self.selected = hit
        if hit is not None:
            self.drag_offset = (x, y)
            self._begin_gesture()
        self._show_selection()

    def _drag(self, event):
        if self.drag_offset is None or self.gesture_start is None:
            return
        before = self.gesture_start
        origin_x, origin_y = self.drag_offset
        indices = ([i for i, item in enumerate(before.items) if item.is_letter]
                   if before.all_letters else [before.selected_index])
        originals = [before.items[i] for i in indices]
        left, top, right, bottom = item_bounds(originals)
        dx = min(max(event.x / self.SCALE - origin_x, LAYOUT_MARGIN_MM - left),
                 LAYOUT_SIZE_MM - LAYOUT_MARGIN_MM - right)
        dy = min(max(event.y / self.SCALE - origin_y, LAYOUT_MARGIN_MM - top),
                 LAYOUT_SIZE_MM - LAYOUT_MARGIN_MM - bottom)
        for index, item in zip(indices, originals):
            self.items[index].x, self.items[index].y = item.x + dx, item.y + dy
        self._draw()

    def _resize(self, axis, value):
        items = self._selection()
        if not items or self.syncing_controls:
            return
        left, top, right, bottom = item_bounds(items)
        start, end, coordinate = (left, right, "x") if axis == "width" else (top, bottom, "y")
        extent = end - start
        if abs(extent - value) < 1e-8:
            return
        before = self._snapshot() if self.gesture_start is None else None
        center = (start + end) / 2
        target = min(max(center, LAYOUT_MARGIN_MM + value / 2),
                     LAYOUT_SIZE_MM - LAYOUT_MARGIN_MM - value / 2)
        factor = value / extent
        for item in items:
            setattr(item, coordinate, target + (getattr(item, coordinate) - center) * factor)
            setattr(item, axis, getattr(item, axis) * factor)
        if before is not None:
            self._record(before)
        self.size_controls[axis][1].config(text=f"{value / 25.4:.2f} in")
        self._draw()

    def _show_selection(self):
        items = self._selection()
        item = self.selected
        title = "All letters" if self.all_letters else item.title if item else "Select or drag an item"
        self.selection_label.config(text=title)
        self.syncing_controls = True
        for axis, (slider, label) in self.size_controls.items():
            slider.config(state="normal" if items else "disabled")
            if items:
                left, top, right, bottom = item_bounds(items)
                extent = right - left if axis == "width" else bottom - top
                slider.config(from_=min(extent, 2 * extent / min(getattr(entry, axis) for entry in items)))
                slider.set(extent)
                label.config(text=f"{extent / 25.4:.2f} in")
        self.syncing_controls = False
        self.select_letters.config(state="normal" if any(entry.is_letter for entry in self.items) else "disabled")
        self.undo_button.config(state="normal" if self.undo_history else "disabled")
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
        if items := self._selection():
            box = [value * self.SCALE for value in item_bounds(items)]
            self.canvas.create_rectangle(*box, outline="#376ca6", width=2, tags="artwork")
