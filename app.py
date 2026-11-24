"""Desktop app: convert an image or lettering into a Brother embroidery file (.pes)."""

import os
import queue
import threading
import tkinter as tk
from difflib import SequenceMatcher
from tkinter import colorchooser, filedialog, messagebox, ttk
from tkinter import font as tkfont

from PIL import Image, ImageTk

import digitizer as dz
import realistic
from hoop_layout import HoopLayout
from simulator import SewSimulator

IMAGE_TYPES = [("Images and vector logos", "*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff *.svg"),
               ("All files", "*.*")]
SAVE_TYPES = [
    ("Brother PES", "*.pes"),
    ("Tajima DST", "*.dst"),
    ("Janome JEF", "*.jef"),
    ("Melco EXP", "*.exp"),
    ("Husqvarna VP3", "*.vp3"),
]
TABS = [("original", "Original"), ("colors", "Color regions"), ("stitches", "Stitch preview")]
SIM_TAB = len(TABS)  # the sewing simulator is the last tab
MM_PER_INCH = 25.4
DEFAULT_FABRIC = "T-shirt / knit"


def register_bundled_fonts():
    """Makes the fonts in fonts/ usable by this program (Windows, private to the app), so the font
    list can show their names in their own fonts."""
    try:
        import ctypes
        for name in os.listdir(dz.BUNDLED_FONTS):
            if name.lower().endswith((".ttf", ".otf")):
                ctypes.windll.gdi32.AddFontResourceExW(os.path.join(dz.BUNDLED_FONTS, name), 0x10, 0)  # FR_PRIVATE
    except Exception:
        pass


STITCH_STYLES = {  # lettering stitch style -> settings
    "Smooth fill": {"fill_type": "smooth", "edge": "none", "applique_all": False},
    "Fill + contour edge": {"fill_type": "contour", "edge": "none", "applique_all": False},
    "Light fill": {"fill_type": "light", "edge": "none", "applique_all": False},
    "Auto (satin + fill)": {"fill_type": "auto", "edge": "none", "applique_all": False},
    "Satin": {"fill_type": "satin", "edge": "none", "applique_all": False},
    "Fill": {"fill_type": "tatami", "edge": "none", "applique_all": False},
    "Sketch (running outline)": {"fill_type": "none", "edge": "running", "applique_all": False},
    "Bean (triple outline)": {"fill_type": "none", "edge": "triple", "applique_all": False},
    "Appliqué": {"fill_type": "auto", "edge": "none", "applique_all": True},
}


class App(tk.Tk):
    def __init__(self):
        register_bundled_fonts()
        super().__init__()
        self.title("Embroidery Converter")
        self.geometry("1280x900")
        self.minsize(960, 660)

        self.source = None
        self.source_path = None
        self.result = None
        self.busy = False
        self.images = {key: None for key, _ in TABS}
        self.photos = {}
        self.canvases = {}
        self.jobs = queue.Queue()
        self.color_edits = {}  # {color key: new rgb, or None = leave that color as bare fabric}
        self.applique = set()  # color keys sewn as applique
        self.applique_all = tk.BooleanVar(value=False)  # set by the Applique lettering style
        self.editor_draft = {}
        self.previous_image_style = None
        self.fixed_hoop = False
        self.photo_hoop = None
        self.layout_items = []
        self.layout_style = "Smooth fill"

        d = dz.Settings()
        hoop = "4 x 4 in (100 x 100 mm)"
        self.hoop = tk.StringVar(value=hoop)
        self.width_in = tk.DoubleVar(value=round((dz.HOOPS[hoop][0] - 10) / MM_PER_INCH, 2))
        self.height_in = tk.DoubleVar(value=round((dz.HOOPS[hoop][1] - 10) / MM_PER_INCH, 2))
        self.shirt_rgb = realistic.SHIRT_WHITE  # preview only: the fabric the design is shown on
        self.realistic_view = tk.BooleanVar(value=True)
        self.bold = tk.DoubleVar(value=0.0)  # mm added around every shape: bolder lines
        self.lock = tk.BooleanVar(value=True)  # keep width x height in the image's proportions
        self.aspect = 1.0
        self._syncing = False
        self.width_in.trace_add("write", lambda *_: self._size_edited("width"))
        self.height_in.trace_add("write", lambda *_: self._size_edited("height"))
        self.fabric = tk.StringVar(value=DEFAULT_FABRIC)
        self.colors = tk.IntVar(value=0)  # 0 = count the art's colors automatically
        self.cleanup = tk.IntVar(value=d.cleanup)
        self.remove_bg = tk.BooleanVar(value=d.remove_background)
        self.brother = tk.BooleanVar(value=True)
        self.spacing = tk.DoubleVar(value=d.row_spacing_mm)
        self.satin_spacing = tk.DoubleVar(value=d.satin_spacing_mm)
        self.stitch_len = tk.DoubleVar(value=d.stitch_length_mm)
        self.angle = tk.DoubleVar(value=d.fill_angle)
        self.pull = tk.DoubleVar(value=d.pull_comp_mm)
        self.trim = tk.DoubleVar(value=d.trim_mm)
        self.underlay = tk.BooleanVar(value=d.underlay)
        self.fill_type = tk.StringVar(value=d.fill_type)
        self.fill_pattern = tk.StringVar(value=d.fill_pattern)
        self.knockdown = tk.BooleanVar(value=d.knockdown)
        self.edge = tk.StringVar(value=d.edge)
        self.outline = tk.StringVar(value=d.outline)
        self.show_advanced = tk.BooleanVar(value=False)

        self._build_ui()
        self._apply_fabric()

    # ------------------------------------------------------------------ layout

    def _build_ui(self):
        root = ttk.Frame(self, padding=8)
        root.pack(fill="both", expand=True)

        side = ttk.Frame(root)
        side.pack(side="left", fill="y", padx=(0, 8))

        buttons = ttk.Frame(side)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Open image...", command=self.open_image).pack(side="left", expand=True, fill="x")
        ttk.Button(buttons, text="Lettering...", command=self.open_text).pack(side="left", expand=True, fill="x",
                                                                                padx=(4, 0))
        ttk.Button(buttons, text="Motifs...", command=lambda: self.open_text(motif_only=True)).pack(
            side="left", expand=True, fill="x", padx=(4, 0))
        ttk.Button(side, text="4 × 4 Hoop Layout...", command=self.open_layout).pack(fill="x", pady=(4, 0))
        self.file_label = ttk.Label(side, text="No image loaded", foreground="#666", wraplength=300)
        self.file_label.pack(fill="x", pady=(4, 8))

        size = self._group(side, "Size")
        ttk.Label(size, text="Hoop").grid(row=0, column=0, sticky="w", pady=2)
        hoop = ttk.Combobox(size, textvariable=self.hoop, values=list(dz.HOOPS), state="readonly", width=24)
        hoop.grid(row=0, column=1, sticky="e", pady=2)
        hoop.bind("<<ComboboxSelected>>", self._hoop_changed)
        width = self._spin(size, 1, "Max width (in)", self.width_in, 0.4, 16, 0.1)
        height = self._spin(size, 2, "Max height (in)", self.height_in, 0.4, 16, 0.1)
        lock = ttk.Checkbutton(size, text="Lock width x height (keep proportions)", variable=self.lock,
                               command=lambda: self._size_edited("width"))
        lock.grid(row=3, column=0, columnspan=2, sticky="w", pady=2)
        self.hoop_picker = hoop
        self.size_controls = (width, height, lock)

        basics = self._group(side, "Basics")
        ttk.Label(basics, text="Fabric").grid(row=0, column=0, sticky="w", pady=2)
        fabric = ttk.Combobox(basics, textvariable=self.fabric, values=list(dz.FABRICS), state="readonly", width=24)
        fabric.grid(row=0, column=1, sticky="e", pady=2)
        fabric.bind("<<ComboboxSelected>>", lambda e: self._apply_fabric())
        self.tip = ttk.Label(basics, text="", wraplength=290, foreground="#555", justify="left")
        self.tip.grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 4))
        self._spin(basics, 2, "Thread colors (0 = auto)", self.colors, 0, 15, 1)
        self.background_control = ttk.Checkbutton(basics, text="Remove background", variable=self.remove_bg)
        self.background_control.grid(row=3, column=0, columnspan=2, sticky="w", pady=2)
        self._spin(basics, 6, "Bolder lines (mm, 0-1)", self.bold, 0, 1.0, 0.1)
        ttk.Label(basics, text="Shirt color (preview)").grid(row=4, column=0, sticky="w", pady=2)
        self.shirt_btn = tk.Button(basics, width=6, relief="solid", borderwidth=1, cursor="hand2",
                                   background=dz.hex_color(self.shirt_rgb), command=self._pick_shirt)
        self.shirt_btn.grid(row=4, column=1, sticky="e", pady=2)
        ttk.Checkbutton(basics, text="Realistic thread preview", variable=self.realistic_view,
                        command=self._view_changed).grid(row=5, column=0, columnspan=2, sticky="w", pady=2)

        self.advanced_toggle = ttk.Checkbutton(side, text="Show advanced settings", variable=self.show_advanced,
                                               command=self._toggle_advanced)
        self.advanced_toggle.pack(anchor="w", pady=(0, 6))

        self.advanced = ttk.Frame(side)  # packed by _toggle_advanced
        adv = self._group(self.advanced, "Colors")
        self._spin(adv, 0, "Detail cleanup (0-3)", self.cleanup, 0, 3, 1)
        ttk.Checkbutton(adv, text="Use Brother thread colors", variable=self.brother).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=2)

        st = self._group(self.advanced, "Stitches (millimetres)")
        ttk.Label(st, text="Fill type").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Combobox(st, textvariable=self.fill_type, values=dz.FILL_TYPES, state="readonly", width=10).grid(
            row=0, column=1, sticky="e", pady=2)
        self._spin(st, 1, "Row spacing", self.spacing, 0.2, 1.5, 0.05)
        self._spin(st, 2, "Satin spacing", self.satin_spacing, 0.2, 1.0, 0.05)
        self._spin(st, 3, "Stitch length", self.stitch_len, 1.5, 7, 0.5)
        self._spin(st, 4, "Fill angle (deg)", self.angle, 0, 180, 15)
        self._spin(st, 5, "Pull compensation", self.pull, 0, 0.6, 0.05)
        self._spin(st, 6, "Trim jumps over", self.trim, 1, 20, 1)
        ttk.Label(st, text="Fill pattern").grid(row=7, column=0, sticky="w", pady=2)
        ttk.Combobox(st, textvariable=self.fill_pattern, values=dz.FILL_PATTERNS, state="readonly", width=10).grid(
            row=7, column=1, sticky="e", pady=2)
        ttk.Checkbutton(st, text="Underlay", variable=self.underlay).grid(
            row=8, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Checkbutton(st, text="Knockdown (towels, fleece)", variable=self.knockdown).grid(
            row=9, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Button(st, text="Reset to automatic settings", command=self._apply_fabric).grid(
            row=10, column=0, columnspan=2, sticky="ew", pady=(4, 0))

        fin = self._group(self.advanced, "Edges and outline")
        ttk.Label(fin, text="Color edges").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Combobox(fin, textvariable=self.edge, values=dz.LINE_TYPES, state="readonly", width=10).grid(
            row=0, column=1, sticky="e", pady=2)
        ttk.Label(fin, text="Outline").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Combobox(fin, textvariable=self.outline, values=dz.LINE_TYPES, state="readonly", width=10).grid(
            row=1, column=1, sticky="e", pady=2)

        self.generate_btn = ttk.Button(side, text="Generate stitches", command=self.generate)
        self.generate_btn.pack(fill="x", pady=(4, 4))
        self.save_btn = ttk.Button(side, text="Save embroidery file...", command=self.save, state="disabled")
        self.save_btn.pack(fill="x")

        self.status = ttk.Label(side, text="Open an image to begin.", wraplength=300, justify="left")
        self.status.pack(fill="x", pady=(8, 4))

        self.threads = tk.Text(side, width=38, height=8, relief="flat", background=self.cget("background"),
                               font=("Segoe UI", 9), state="disabled", cursor="arrow")
        self.threads.pack(fill="both", expand=True)

        self.tabs = ttk.Notebook(root)
        self.tabs.pack(side="left", fill="both", expand=True)
        for key, title in TABS:
            canvas = tk.Canvas(self.tabs, background="#f4f4f4", highlightthickness=0)
            self.tabs.add(canvas, text=title)
            canvas.bind("<Configure>", lambda e, k=key: self._show(k))
            self.canvases[key] = canvas
        self.sim = SewSimulator(self.tabs)
        self.tabs.add(self.sim, text="Sew simulation")
        self.tabs.bind("<<NotebookTabChanged>>", lambda e: self._show(self._current_tab()))

    def _group(self, parent, title):
        frame = ttk.LabelFrame(parent, text=title, padding=(8, 4))
        frame.pack(fill="x", pady=(0, 8))
        frame.columnconfigure(1, weight=1)
        return frame

    def _spin(self, parent, row, label, var, lo, hi, step):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        spin = ttk.Spinbox(parent, textvariable=var, from_=lo, to=hi, increment=step, width=8)
        spin.grid(row=row, column=1, sticky="e", pady=2)
        return spin

    def _toggle_advanced(self):
        if self.show_advanced.get():
            self.advanced.pack(fill="x", before=self.generate_btn)
        else:
            self.advanced.pack_forget()

    def _size_edited(self, changed):
        """With the lock on, changing one size box updates the other to keep the proportions."""
        if self._syncing or self.fixed_hoop or not self.lock.get():
            return
        try:
            w, h = float(self.width_in.get()), float(self.height_in.get())
        except (tk.TclError, ValueError):
            return  # half-typed number
        self._syncing = True
        try:
            if changed == "width":
                self.height_in.set(round(w / self.aspect, 2))
            else:
                self.width_in.set(round(h * self.aspect, 2))
        finally:
            self._syncing = False

    def _fit_to_hoop(self):
        """Biggest design that fits the hoop with a margin (and keeps the proportions if locked)."""
        hw, hh = dz.HOOPS[self.hoop.get()]
        w, h = (hw - 10) / MM_PER_INCH, (hh - 10) / MM_PER_INCH  # leave a margin inside the hoop
        if self.fixed_hoop:
            w = h = dz.LAYOUT_SIZE_MM / MM_PER_INCH
        elif self.lock.get():
            w = min(w, h * self.aspect)
            h = w / self.aspect
        self._syncing = True
        try:
            self.width_in.set(round(w, 2))
            self.height_in.set(round(h, 2))
        finally:
            self._syncing = False

    def _hoop_changed(self, _event=None):
        self._fit_to_hoop()

    def _apply_fabric(self):
        """Sets every stitch setting from the fabric: sensible defaults for beginners."""
        values, tip = dz.FABRICS[self.fabric.get()]
        d = dz.Settings()
        self.spacing.set(values["row_spacing_mm"])
        self.satin_spacing.set(values["satin_spacing_mm"])
        self.pull.set(values["pull_comp_mm"])
        self.underlay.set(values["underlay"])
        self.knockdown.set(values.get("knockdown", False))
        self.fill_pattern.set(d.fill_pattern)
        self.stitch_len.set(d.stitch_length_mm)
        self.angle.set(d.fill_angle)
        self.trim.set(d.trim_mm)
        if self.previous_image_style is None:
            self.fill_type.set(d.fill_type)
        self.tip.config(text=tip)

    # ------------------------------------------------------------------ images

    def _current_tab(self):
        index = self.tabs.index(self.tabs.select())
        return TABS[index][0] if index < len(TABS) else None

    def _show(self, key):
        if key is None:
            return
        canvas = self.canvases[key]
        canvas.delete("all")
        cw, ch = canvas.winfo_width(), canvas.winfo_height()
        img = self.images[key]
        if img is None:
            hint = "Open an image to begin." if self.source is None else "Click 'Generate stitches'."
            canvas.create_text(cw / 2, ch / 2, text=hint, fill="#999", font=("Segoe UI", 12))
            return
        scale = min((cw - 20) / img.width, (ch - 20) / img.height, 6)
        if scale <= 0:
            return
        size = (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
        resample = Image.NEAREST if key == "colors" and scale > 1 else Image.LANCZOS
        self.photos[key] = ImageTk.PhotoImage(img.resize(size, resample))
        canvas.create_image(cw // 2, ch // 2, image=self.photos[key])

    # ------------------------------------------------------------------ actions

    def open_image(self):
        path = filedialog.askopenfilename(title="Open image", filetypes=IMAGE_TYPES)
        if not path:
            return
        try:
            img = dz.load_image(path)
        except Exception as exc:
            messagebox.showerror("Could not open image", str(exc))
            return
        self._set_source(img, path, f"{os.path.basename(path)}  ({img.width} x {img.height} px)")

    def open_layout(self):
        def apply(image, items, style):
            self.layout_items, self.layout_style = items, style
            self._set_source(image, "4x4 layout", "4 × 4 hoop layout", style=style, fixed_hoop=True)

        HoopLayout(self, self.layout_items, self.layout_style, STITCH_STYLES, self.open_text, apply)

    def open_text(self, motif_only=False, layout_parent=None, on_layout=None):
        """Lettering: text, font (each name in its own font), shape, outlines/shadow, stitch style,
        with a live preview. The result becomes the design."""
        fonts = dz.list_fonts()
        if not fonts and not motif_only:
            messagebox.showerror("No fonts", "No TrueType fonts were found on this computer.")
            return
        parent = layout_parent or self
        win = tk.Toplevel(parent)
        win.title("Motifs" if motif_only else "Lettering")
        win.transient(parent)
        win.grab_set()
        draft_key = ("layout", motif_only) if on_layout else motif_only
        draft = self.editor_draft.get(draft_key, {})
        frame = ttk.Frame(win, padding=12)
        frame.pack(fill="both", expand=True)
        left, right = ttk.Frame(frame), ttk.Frame(frame)
        if not motif_only:
            left.pack(side="left", fill="y")
        right.pack(side="left", fill="both", expand=True, padx=(14 if not motif_only else 0, 0))

        ttk.Label(left, text="Text (Enter for a new line)").grid(row=0, column=0, columnspan=2, sticky="w")
        entry = tk.Text(left, width=34, height=3, font=("Segoe UI", 11))
        entry.grid(row=1, column=0, columnspan=2, pady=(2, 8))
        if not motif_only:
            entry.insert("1.0", draft.get("text", "" if on_layout else "Bears"))
            entry.focus_set()
        names = list(fonts)
        default = next((n for n in names if n.lower().startswith("graduate")), None) or \
            next((n for n in names if n.lower().startswith("arial bold")), next(iter(names), ""))
        selected_font = tk.StringVar(value=draft.get("font", default))
        default_font = selected_font.get()
        letter_fonts = dict(draft.get("letter_fonts", {}))
        previous_text = entry.get("1.0", "end-1c")
        target_offsets = {}
        target = tk.StringVar(value="Default font")
        if not motif_only:
            targets = ttk.Combobox(left, textvariable=target, state="readonly", width=14)
            ttk.Label(left, text="Apply font to").grid(row=2, column=0, columnspan=2, sticky="w")
            targets.grid(row=3, column=0, sticky="w", pady=(0, 6))
            clear_letter = ttk.Button(left, text="Use default font", command=lambda: clear_letter_font())
            clear_letter.grid(row=3, column=1, sticky="e", pady=(0, 6))
            self._font_picker(left, fonts, selected_font, lambda name: choose_font(name)).grid(
                row=4, column=0, columnspan=2, sticky="nsew")

        opts = ttk.LabelFrame(right, text="Style", padding=(8, 4))
        opts.pack(fill="x")
        shape = tk.StringVar(value=draft.get("shape", "straight"))
        bend = tk.DoubleVar(value=draft.get("bend", 0.5))
        height_scale = tk.DoubleVar(value=draft.get("height_scale", 1))
        align = tk.StringVar(value=draft.get("align", "center"))
        colors = {"text": (15, 30, 80), "outline": (230, 90, 20), "outline2": (255, 255, 255),
                  "shadow": (60, 60, 60), "motif": (230, 90, 20)} | draft.get("colors", {})
        use = {k: tk.BooleanVar(value=draft.get("use", {}).get(k, False)) for k in ("outline", "outline2", "shadow")}
        width = {k: tk.DoubleVar(value=draft.get("width", {}).get(k, value))
                 for k, value in (("outline", 0.06), ("outline2", 0.05), ("shadow", 0.05))}
        style = tk.StringVar(value=draft.get("style", "Auto (satin + fill)" if motif_only else "Smooth fill"))
        motifs = sorted(dz.motifs())
        default_motif = ("Star" if "Star" in motifs else next(iter(motifs), "")) if motif_only else "None"
        motif = tk.StringVar(value=draft.get("motif", default_motif))
        placement = tk.StringVar(value=draft.get("placement", "Right"))
        scale = tk.DoubleVar(value=draft.get("scale", 0.6))

        if not motif_only and not on_layout:
            ttk.Label(opts, text="Shape").grid(row=0, column=0, sticky="w", pady=2)
            ttk.Combobox(opts, textvariable=shape, values=dz.TEXT_SHAPES, state="readonly", width=14).grid(
                row=0, column=1, sticky="w", pady=2)
            ttk.Label(opts, text="Bend").grid(row=1, column=0, sticky="w")
            bend_slider = ttk.Scale(opts, from_=0, to=1, variable=bend, length=100)
            bend_slider.grid(row=1, column=1, sticky="w")
            align_picker = ttk.Combobox(opts, textvariable=align, values=("left", "center", "right"),
                                        state="readonly", width=7)
            ttk.Label(opts, text="Alignment").grid(row=9, column=0, sticky="w")
            align_picker.grid(row=9, column=1, sticky="w")
        if not motif_only:
            ttk.Label(opts, text="Letter height").grid(row=10, column=0, sticky="w")
            ttk.Scale(opts, from_=0.5, to=3, variable=height_scale, length=100).grid(row=10, column=1, sticky="w")
            height_label = ttk.Label(opts)
            height_label.grid(row=10, column=2, sticky="w")

        swatches = {}

        def pick(key):
            chosen = colorchooser.askcolor(color="#%02x%02x%02x" % tuple(colors[key]), parent=win)
            if chosen and chosen[0]:
                colors[key] = tuple(int(v) for v in chosen[0])
                swatches[key].config(background=chosen[1])
                if key in use:
                    use[key].set(True)
                refresh()

        rows = [] if motif_only else [("text", "Letter color", None), ("outline", "Outline", "width"),
                ("outline2", "Second outline (varsity)", "width"), ("shadow", "Drop shadow", "offset")]
        for i, (key, label, what) in enumerate(rows, start=2):
            if key in use:
                ttk.Checkbutton(opts, text=label, variable=use[key], command=lambda: refresh()).grid(
                    row=i, column=0, sticky="w", pady=2)
            else:
                ttk.Label(opts, text=label).grid(row=i, column=0, sticky="w", pady=2)
            swatches[key] = tk.Button(opts, width=4, relief="solid", borderwidth=1, cursor="hand2",
                                      background="#%02x%02x%02x" % tuple(colors[key]), command=lambda k=key: pick(k))
            swatches[key].grid(row=i, column=1, sticky="w", pady=2)
            if what:
                ttk.Scale(opts, from_=0.02, to=0.14, variable=width[key], length=90,
                          command=lambda v: refresh()).grid(row=i, column=2, sticky="w")

        if not on_layout:
            ttk.Label(opts, text="Stitch style").grid(row=6, column=0, sticky="w", pady=(8, 2))
            ttk.Combobox(opts, textvariable=style, values=list(STITCH_STYLES), state="readonly", width=24).grid(
                row=6, column=1, columnspan=2, sticky="w", pady=(8, 2))
        if motif_only or not on_layout:
            ttk.Label(opts, text="Motif").grid(row=7, column=0, sticky="w")
            ttk.Combobox(opts, textvariable=motif, state="readonly", width=14,
                         values=motifs if motif_only else ["None", *motifs]).grid(row=7, column=1, sticky="w")
            swatches["motif"] = tk.Button(opts, width=4, background="#%02x%02x%02x" % colors["motif"],
                                          command=lambda: pick("motif"))
            swatches["motif"].grid(row=7, column=2, sticky="w")
        if not motif_only and not on_layout:
            ttk.Label(opts, text="Motif placement / size").grid(row=8, column=0, sticky="w")
            ttk.Combobox(opts, textvariable=placement, state="readonly", width=10,
                         values=("Left", "Right", "Above", "Below")).grid(row=8, column=1, sticky="w")
            ttk.Scale(opts, from_=0.25, to=1.5, variable=scale, length=90).grid(row=8, column=2, sticky="w")

        preview = ttk.Label(right, background="#ebebeb", anchor="center")
        preview.pack(fill="both", expand=True, pady=(10, 0))
        state = {"job": None, "photo": None}

        def render_text(text, font, overrides=None):
            img = dz.make_text_image(
                    text, fonts[font], colors["text"], shape=shape.get(), bend=bend.get(), align=align.get(),
                    letter_fonts=overrides,
                    outline=(colors["outline"], width["outline"].get()) if use["outline"].get() else None,
                    outline2=(colors["outline2"], width["outline2"].get()) if use["outline2"].get() else None,
                    shadow=(colors["shadow"], width["shadow"].get(), width["shadow"].get())
                    if use["shadow"].get() else None)
            if box := img.getbbox():
                ink = img.crop(box)
                return ink.resize((ink.width, max(1, round(ink.height * height_scale.get()))), Image.Resampling.LANCZOS)
            return None

        def make():
            raw = entry.get("1.0", "end-1c")
            text = raw.strip()
            start = len(raw) - len(raw.lstrip())
            overrides = {offset - start: fonts[name] for offset, name in letter_fonts.items()}
            img = render_text(text, default_font, overrides) if text else None
            if motif.get() in motifs:
                img = dz.compose_motif(img, motif.get(), colors["motif"], placement.get(), scale.get())
            box = img.getbbox() if img is not None else None
            return img.crop(box) if box else None

        def draw_preview():
            state["job"] = None
            if not motif_only:
                height_label.config(text=f"{height_scale.get():.0%}")
            try:
                img = make()
            except Exception as exc:
                preview.config(image="", text=f"Can't draw this: {exc}")
                use_design.config(state="disabled")
                return
            if img is None:
                prompt = "Choose a motif" if motif_only else "Type some text"
                preview.config(image="", text=prompt)
                use_design.config(state="disabled")
                return
            shown = Image.new("RGB", img.size, (235, 235, 235))
            shown.paste(img, (0, 0), img)
            shown.thumbnail((460, 300))
            state["photo"] = ImageTk.PhotoImage(shown)
            preview.config(image=state["photo"], text="")
            use_design.config(state="normal")

        def refresh(*_):
            if state["job"]:
                win.after_cancel(state["job"])
            state["job"] = win.after(250, draw_preview)

        def shape_changed(*_):
            bend_slider.config(state="disabled" if shape.get() in ("straight", "circle") else "normal")
            align_picker.config(state="readonly" if shape.get() in ("straight", "slant") else "disabled")
            refresh()

        def choose_font(name):
            nonlocal default_font
            offset = target_offsets.get(target.get())
            if offset is None:
                default_font = name
            elif name == default_font:
                letter_fonts.pop(offset, None)
            else:
                letter_fonts[offset] = name
            show_target()
            refresh()

        def clear_letter_font():
            choose_font(default_font)

        def show_target(_event=None):
            offset = target_offsets.get(target.get())
            selected_font.set(letter_fonts.get(offset, default_font))
            clear_letter.config(state="normal" if offset in letter_fonts else "disabled")

        def update_targets(text):
            target_offsets.clear()
            target_offsets.update((f"{index + 1}: {letter}", offset)
                                  for index, (offset, letter) in enumerate(dz.text_letters(text))
                                  if not letter.isspace())
            targets.config(values=("Default font", *target_offsets))
            targets.current(0)
            show_target()

        if not motif_only and not on_layout:
            shape.trace_add("write", shape_changed)
        if not motif_only:
            targets.bind("<<ComboboxSelected>>", show_target)
            update_targets(previous_text)
        for var in (bend, align, motif, placement, scale, height_scale):
            var.trace_add("write", refresh)

        def text_changed(_):
            nonlocal previous_text, letter_fonts
            if entry.edit_modified():
                entry.edit_modified(False)
                current = entry.get("1.0", "end-1c")
                if current != previous_text:
                    if letter_fonts:
                        valid = dict(dz.text_letters(current))
                        letter_fonts = {
                            new + offset - old: name
                            for old, new, size in SequenceMatcher(None, previous_text, current, autojunk=False)
                            .get_matching_blocks()
                            for offset, name in letter_fonts.items()
                            if old <= offset < old + size and new + offset - old in valid
                        }
                    previous_text = current
                    update_targets(current)
                refresh()

        entry.edit_modified(False)
        entry.bind("<<Modified>>", text_changed)

        def ok():
            try:
                img = make()
            except Exception as exc:
                messagebox.showerror("Could not draw design", str(exc), parent=win)
                return
            if img is None:
                return
            text = entry.get("1.0", "end-1c")
            name = (text.strip().splitlines()[0] if text.strip() else motif.get())[:30]
            if on_layout:
                parts = []
                for offset, letter in dz.text_letters(text):
                    if not letter.isspace():
                        ink = render_text(letter, letter_fonts.get(offset, default_font))
                        if ink:
                            parts.append((letter, ink, ink.height * 26 / 300))
                if motif.get() in motifs:
                    art = dz.compose_motif(None, motif.get(), colors["motif"], "Right", 1)
                    parts.append((motif.get(), art, 21))
            self.editor_draft[draft_key] = {
                "text": text, "font": default_font, "letter_fonts": letter_fonts,
                "shape": shape.get(), "bend": bend.get(), "align": align.get(),
                "height_scale": height_scale.get(),
                "colors": colors, "use": {k: v.get() for k, v in use.items()},
                "width": {k: v.get() for k, v in width.items()}, "style": style.get(),
                "motif": motif.get(), "placement": placement.get(), "scale": scale.get(),
            }
            close()
            if on_layout:
                on_layout(parts)
                return
            kind = "Motif" if motif_only else "Lettering"
            self._set_source(img, name, f"{kind}: {name}", style=style.get())

        def close():
            if state["job"]:
                win.after_cancel(state["job"])
            win.destroy()
            if layout_parent:
                layout_parent.grab_set()

        use_design = ttk.Button(right, text="Use design", command=ok, state="disabled")
        use_design.pack(fill="x", pady=(10, 0))
        win.protocol("WM_DELETE_WINDOW", close)
        win.bind("<Escape>", lambda e: close())
        win.bind("<Control-Return>", lambda e: ok())
        win.keep = (state, colors, use, width, swatches)
        if motif_only or on_layout:
            refresh()
        else:
            shape_changed()

    SYMBOL_FONTS = ("wingdings", "webdings", "marlett", "symbol", "mt extra", "mdl2", "fluent icons", "bookshelf")

    def _font_picker(self, parent, fonts, font_var, select):
        """A searchable list where every font's name is written in that font."""
        box = ttk.Frame(parent)
        ttk.Label(box, text="Font").grid(row=0, column=0, sticky="w")
        query = tk.StringVar()
        search = ttk.Entry(box, textvariable=query, width=24)
        search.grid(row=0, column=1, sticky="e", pady=(0, 4))
        categories = dz.font_categories()
        category = tk.StringVar(value="All")
        ttk.Combobox(box, textvariable=category, values=("All", *sorted(set(categories.values()))),
                     state="readonly", width=11).grid(row=0, column=2, sticky="w", padx=(4, 0))
        listing = tk.Text(box, width=40, height=12, wrap="none", cursor="hand2", relief="solid", borderwidth=1,
                          padx=6, pady=4, spacing1=3, spacing3=3)
        bar = ttk.Scrollbar(box, orient="vertical", command=listing.yview)
        listing.configure(yscrollcommand=bar.set)
        listing.grid(row=1, column=0, columnspan=3, sticky="nsew")
        bar.grid(row=1, column=3, sticky="ns")

        tk_fonts, plain = {}, tkfont.Font(family="Segoe UI", size=9)
        tag_of = {name: f"f{i}" for i, name in enumerate(fonts)}  # Tk splits tag names at spaces
        families = set(tkfont.families())

        def tk_font(name, size=14):
            """The Tk font for a listed font: its family, plus bold/italic from its style."""
            key = (name, size)
            if key not in tk_fonts:
                try:
                    family, style = dz.ImageFont.truetype(fonts[name], 10).getname()
                except Exception:
                    family, style = name, ""
                st = (style or "").lower()
                if f"{family} {style}" in families:  # e.g. "Arial Black" is its own family on Windows
                    family, st = f"{family} {style}", ""
                tk_fonts[key] = tkfont.Font(
                    family=family, size=size,
                    weight="bold" if any(w in st for w in ("bold", "black", "heavy", "semibold")) else "normal",
                    slant="italic" if ("italic" in st or "oblique" in st) else "roman")
            return tk_fonts[key]

        def show_choice(*_):
            listing.tag_remove("chosen", "1.0", "end")
            ranges = listing.tag_ranges(tag_of[font_var.get()])
            if ranges:
                listing.tag_add("chosen", ranges[0], f"{ranges[0]} lineend")
                listing.see(ranges[0])

        def fill(*_):
            q = query.get().strip().lower()
            listing.configure(state="normal")
            listing.delete("1.0", "end")
            for name in fonts:
                if q not in name.lower() or (category.get() != "All" and categories.get(name) != category.get()):
                    continue
                tag = tag_of[name]
                listing.tag_configure(tag, font=tk_font(name))
                listing.tag_bind(tag, "<Button-1>", lambda e, n=name: select(n))
                listing.insert("end", name, tag)
                if any(sym in name.lower() for sym in self.SYMBOL_FONTS):  # symbol fonts: say what they are
                    listing.insert("end", f"   ({name})", ("plain", tag))
                listing.insert("end", "\n", tag)
            listing.tag_configure("plain", font=plain, foreground="#888")
            listing.tag_configure("chosen", background="#cfe3ff")
            listing.configure(state="disabled")
            show_choice()

        query.trace_add("write", fill)
        category.trace_add("write", fill)
        font_var.trace_add("write", show_choice)
        fill()
        # keep the fonts and the search variable alive as long as the list is shown (Tk drops a
        # font as soon as Python forgets it, and the list would fall back to a plain font)
        box.keep = (tk_fonts, plain, query, category, fill, tag_of)
        return box

    def _set_source(self, img, name, label, style=None, fixed_hoop=False):
        if style is not None:
            if self.previous_image_style is None:
                self.previous_image_style = {key: getattr(self, key).get() for key in STITCH_STYLES[style]}
                self.previous_image_style["remove_bg"] = self.remove_bg.get()
            for key, value in STITCH_STYLES[style].items():
                getattr(self, key).set(value)
            self.remove_bg.set(False)
        elif self.previous_image_style is not None:
            for key, value in self.previous_image_style.items():
                getattr(self, key).set(value)
            self.previous_image_style = None
        if fixed_hoop and not self.fixed_hoop:
            self.photo_hoop = self.hoop.get()
            self.hoop.set(dz.LAYOUT_HOOP)
        elif self.fixed_hoop and not fixed_hoop:
            self.hoop.set(self.photo_hoop)
        self.fixed_hoop = fixed_hoop
        self.hoop_picker.config(state="disabled" if fixed_hoop else "readonly")
        for control in (*self.size_controls, self.background_control):
            control.config(state="disabled" if fixed_hoop else "normal")
        self.source, self.source_path, self.result = img, name, None
        self.color_edits = {}
        self.applique = set()
        self.aspect = img.width / max(1, img.height)
        self._fit_to_hoop()
        shown = Image.new("RGBA", img.size, (244, 244, 244, 255))
        shown.alpha_composite(img)
        self.images = {"original": shown.convert("RGB"), "colors": None, "stitches": None}
        self.file_label.config(text=label)
        self.save_btn.config(state="disabled")
        self._set_threads([])
        self.sim.clear()
        self.tabs.select(0)
        self._show("original")
        self.generate()

    def _read_settings(self):
        try:
            s = dz.Settings(
                width_mm=float(self.width_in.get()) * MM_PER_INCH, height_mm=float(self.height_in.get()) * MM_PER_INCH,
                fixed_hoop=self.fixed_hoop,
                colors=int(self.colors.get()), remove_background=self.remove_bg.get(),
                bold_mm=min(max(float(self.bold.get()), 0.0), 1.0),
                brother_colors=self.brother.get(), cleanup=int(self.cleanup.get()),
                fill_type=self.fill_type.get(), fill_pattern=self.fill_pattern.get(),
                knockdown=self.knockdown.get(), applique=set(self.applique), applique_all=self.applique_all.get(),
                row_spacing_mm=float(self.spacing.get()),
                satin_spacing_mm=float(self.satin_spacing.get()),
                stitch_length_mm=float(self.stitch_len.get()), fill_angle=float(self.angle.get()),
                pull_comp_mm=float(self.pull.get()), underlay=self.underlay.get(), trim_mm=float(self.trim.get()),
                edge=self.edge.get(), outline=self.outline.get(), color_edits=dict(self.color_edits),
            )
        except (tk.TclError, ValueError):
            messagebox.showerror("Invalid setting", "Please enter a number in every settings box.")
            return None
        s.width_mm = min(max(s.width_mm, 5), 400)
        s.height_mm = min(max(s.height_mm, 5), 400)
        s.colors = min(max(s.colors, 0), 15)
        s.cleanup = min(max(s.cleanup, 0), 3)
        s.row_spacing_mm = min(max(s.row_spacing_mm, 0.2), 3)
        s.satin_spacing_mm = min(max(s.satin_spacing_mm, 0.2), 2)
        s.stitch_length_mm = min(max(s.stitch_length_mm, 1), 10)
        s.pull_comp_mm = min(max(s.pull_comp_mm, 0), 1)
        return s

    def generate(self):
        if self.busy:
            return
        if self.source is None:
            messagebox.showinfo("No image", "Open an image first.")
            return
        s = self._read_settings()
        if s is None:
            return
        self.busy = True
        self.generate_btn.config(state="disabled")
        self.save_btn.config(state="disabled")
        self.status.config(text="Digitizing... this can take a few seconds.")
        self.config(cursor="watch")

        hoop = dz.HOOPS.get(self.hoop.get())  # read Tk variables here, not in the worker thread
        view = self._view()

        def work(img=self.source):
            try:
                result = dz.convert(img, s)
                previews = dz.color_preview(result.labels, result.palette), dz.render_preview(result.pattern, hoop_mm=hoop, **view)
                self.jobs.put((img, result, previews, None))
            except Exception as exc:
                self.jobs.put((img, None, None, exc))

        threading.Thread(target=work, daemon=True).start()
        self.after(100, self._poll)

    def _poll(self):
        try:
            image, result, previews, error = self.jobs.get_nowait()
        except queue.Empty:
            self.after(100, self._poll)
            return
        self.busy = False
        self.config(cursor="")
        self.generate_btn.config(state="normal")
        if image is not self.source:
            self.generate()
            return
        if error is not None:
            self.status.config(text=f"Error: {error}")
            return
        self.result = result
        if result.info["height_mm"] > 0:  # from now on the lock keeps the design's own proportions
            self.aspect = result.info["width_mm"] / result.info["height_mm"]
        self.images["colors"], self.images["stitches"] = previews
        self._update_status()
        self._set_threads(result.threads)
        self.sim.load(result.pattern)
        self.save_btn.config(state="normal")
        self.tabs.select(2)
        self._show("stitches")

    def _update_status(self):
        info = self.result.info
        self.status.config(text=(
            f"Size: {info['width_mm'] / MM_PER_INCH:.2f} x {info['height_mm'] / MM_PER_INCH:.2f} in\n"
            f"Stitches: {info['stitches']:,}    Colors: {info['colors']}    Trims: {info['trims']}\n"
            f"Estimated sewing time: ~{max(1, round(info['minutes']))} min"
        ))

    def _set_threads(self, threads):
        t = self.threads
        t.config(state="normal")
        t.delete("1.0", "end")
        for tag in t.tag_names():
            if tag.startswith(("swatch", "action", "appl")):
                t.tag_delete(tag)
        if threads:
            t.insert("end", "Thread order. Click a swatch to change a color (same colors merge "
                            "into one thread); 'skip' leaves that color as bare fabric.\n")
        for i, thread in enumerate(threads, 1):
            tag, action = f"swatch{i}", f"action{i}"
            t.tag_configure(tag, background=thread["hex"])
            t.tag_configure(action, foreground="#1a6fd1", underline=True)
            t.insert("end", f"{i:>2}. ")
            for name in (tag, action):
                t.tag_bind(name, "<Enter>", lambda e: t.config(cursor="hand2"))
                t.tag_bind(name, "<Leave>", lambda e: t.config(cursor="arrow"))
            if thread.get("skipped"):
                t.insert("end", "      ", tag)
                t.insert("end", f"  {thread['name'] or thread['hex']}: not stitched  ")
                t.insert("end", "stitch it", action)
                t.tag_bind(action, "<Button-1>", lambda e, k=thread["keys"]: self._edit_colors(k, "reset"))
                t.insert("end", "\n")
                continue
            t.insert("end", "      ", tag)
            outline = thread.get("kind") == "outline"
            t.tag_bind(tag, "<Button-1>", (lambda e, n=i - 1: self._recolor_outline(n)) if outline
                       else (lambda e, th=thread: self._pick_color(th)))
            t.insert("end", f"  {thread['name'] or thread['hex']}  ({thread['hex']})"
                            f"{' [outline]' if outline else ''}  ")
            if not outline:
                t.insert("end", "skip", action)
                t.tag_bind(action, "<Button-1>", lambda e, k=thread["keys"]: self._edit_colors(k, None))
                if self.applique_all.get():
                    t.insert("end", "  Appliqué\n")
                    continue
                appl = f"appl{i}"
                is_appl = bool(set(thread["keys"]) & self.applique)
                t.tag_configure(appl, foreground="#1a6fd1", underline=True)
                t.tag_bind(appl, "<Enter>", lambda e: t.config(cursor="hand2"))
                t.tag_bind(appl, "<Leave>", lambda e: t.config(cursor="arrow"))
                t.insert("end", "  ")
                t.insert("end", "sew as fill" if is_appl else "applique", appl)
                t.tag_bind(appl, "<Button-1>", lambda e, k=thread["keys"]: self._toggle_applique(k))
            t.insert("end", "\n")
        t.config(state="disabled")

    def _view(self):
        return {"style": "realistic" if self.realistic_view.get() else "flat", "fabric": self.shirt_rgb}

    def _pick_shirt(self):
        chosen = colorchooser.askcolor(color=dz.hex_color(self.shirt_rgb), title="Shirt color", parent=self)
        if chosen and chosen[0]:
            self.shirt_rgb = tuple(int(v) for v in chosen[0])
            self.shirt_btn.config(background=dz.hex_color(self.shirt_rgb))
            self._view_changed()

    def _view_changed(self):
        """Redraws the stitch preview and the sew simulation in the chosen style and shirt color."""
        self.sim.set_style(self.realistic_view.get(), self.shirt_rgb)
        if self.result is not None:
            self.images["stitches"] = dz.render_preview(self.result.pattern, hoop_mm=dz.HOOPS.get(self.hoop.get()),
                                                        **self._view())
            self._show(self._current_tab())

    def _toggle_applique(self, keys):
        """Applique: the machine sews a placement line, stops for you to lay fabric, tacks it down,
        stops for you to trim it, then covers the edge with satin."""
        if self.result is None or self.busy:
            return
        keys = set(keys)
        if keys & self.applique:
            self.applique -= keys
        else:
            self.applique |= keys
        self.generate()

    def _pick_color(self, thread):
        chosen = colorchooser.askcolor(color=thread["hex"], title="Thread color", parent=self)
        if chosen and chosen[0]:
            self._edit_colors(thread["keys"], tuple(int(v) for v in chosen[0]))

    def _edit_colors(self, keys, value):
        """Recolors (value = rgb), skips (None) or restores ("reset") the colors in `keys`, then redoes
        the stitches so identical colors are sewn as one thread."""
        if self.result is None or self.busy:
            return
        for key in keys:
            if value == "reset":
                self.color_edits.pop(key, None)
            else:
                self.color_edits[key] = value
        self.generate()

    def _recolor_outline(self, index):
        """The outline isn't an image color: change its thread directly. The stitches stay the same."""
        pattern, threads = self.result.pattern, self.result.threads
        chosen = colorchooser.askcolor(color=threads[index]["hex"], title="Outline color", parent=self)
        if not chosen or not chosen[0]:
            return
        rgb = tuple(int(v) for v in chosen[0])
        thread = pattern.threadlist[index]
        thread.set_color(*rgb)
        thread.description = dz.nearest_brother_thread(rgb)[1]
        for entry in (threads[index], self.result.info["threads"][index]):
            entry.update(rgb=rgb, hex=dz.hex_color(rgb), name=thread.description)
        self.images["stitches"] = dz.render_preview(pattern, hoop_mm=dz.HOOPS.get(self.hoop.get()), **self._view())
        self._set_threads(threads)
        self.sim.load(pattern)
        self.tabs.select(2)
        self._show("stitches")

    def save(self):
        if self.result is None:
            return
        base = os.path.splitext(os.path.basename(self.source_path))[0] or "design"
        path = filedialog.asksaveasfilename(
            title="Save embroidery file", initialfile=base + ".pes", defaultextension=".pes", filetypes=SAVE_TYPES)
        if not path:
            return
        try:
            path = dz.save(self.result.pattern, path)
        except Exception as exc:
            messagebox.showerror("Could not save", str(exc))
            return
        self._update_status()
        self.status.config(text=self.status.cget("text") + f"\n\nSaved: {path}")


if __name__ == "__main__":
    try:  # crisp text on high-DPI Windows displays
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    App().mainloop()
