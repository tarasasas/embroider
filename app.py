"""Desktop app: convert an image or lettering into a Brother embroidery file (.pes)."""

import os
import queue
import threading
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

from PIL import Image, ImageTk

import digitizer as dz
from simulator import SewSimulator

IMAGE_TYPES = [("Images", "*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff"), ("All files", "*.*")]
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
DEFAULT_FABRIC = "Woven cotton"


class App(tk.Tk):
    def __init__(self):
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

        d = dz.Settings()
        hoop = "4 x 4 in (100 x 100 mm)"
        self.hoop = tk.StringVar(value=hoop)
        self.width_in = tk.DoubleVar(value=round((dz.HOOPS[hoop][0] - 10) / MM_PER_INCH, 2))
        self.height_in = tk.DoubleVar(value=round((dz.HOOPS[hoop][1] - 10) / MM_PER_INCH, 2))
        self.fabric = tk.StringVar(value=DEFAULT_FABRIC)
        self.colors = tk.IntVar(value=d.colors)
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
        self.file_label = ttk.Label(side, text="No image loaded", foreground="#666", wraplength=300)
        self.file_label.pack(fill="x", pady=(4, 8))

        size = self._group(side, "Size")
        ttk.Label(size, text="Hoop").grid(row=0, column=0, sticky="w", pady=2)
        hoop = ttk.Combobox(size, textvariable=self.hoop, values=list(dz.HOOPS), state="readonly", width=24)
        hoop.grid(row=0, column=1, sticky="e", pady=2)
        hoop.bind("<<ComboboxSelected>>", self._hoop_changed)
        self._spin(size, 1, "Max width (in)", self.width_in, 0.4, 16, 0.1)
        self._spin(size, 2, "Max height (in)", self.height_in, 0.4, 16, 0.1)

        basics = self._group(side, "Basics")
        ttk.Label(basics, text="Fabric").grid(row=0, column=0, sticky="w", pady=2)
        fabric = ttk.Combobox(basics, textvariable=self.fabric, values=list(dz.FABRICS), state="readonly", width=24)
        fabric.grid(row=0, column=1, sticky="e", pady=2)
        fabric.bind("<<ComboboxSelected>>", lambda e: self._apply_fabric())
        self.tip = ttk.Label(basics, text="", wraplength=290, foreground="#555", justify="left")
        self.tip.grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 4))
        self._spin(basics, 2, "Thread colors", self.colors, 1, 15, 1)
        ttk.Checkbutton(basics, text="Remove background", variable=self.remove_bg).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=2)

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
        ttk.Checkbutton(st, text="Underlay", variable=self.underlay).grid(
            row=7, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Button(st, text="Reset to automatic settings", command=self._apply_fabric).grid(
            row=8, column=0, columnspan=2, sticky="ew", pady=(4, 0))

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
        ttk.Spinbox(parent, textvariable=var, from_=lo, to=hi, increment=step, width=8).grid(
            row=row, column=1, sticky="e", pady=2)

    def _toggle_advanced(self):
        if self.show_advanced.get():
            self.advanced.pack(fill="x", before=self.generate_btn)
        else:
            self.advanced.pack_forget()

    def _hoop_changed(self, _event=None):
        w, h = dz.HOOPS[self.hoop.get()]
        self.width_in.set(round((w - 10) / MM_PER_INCH, 2))  # leave a margin inside the hoop
        self.height_in.set(round((h - 10) / MM_PER_INCH, 2))

    def _apply_fabric(self):
        """Sets every stitch setting from the fabric: sensible defaults for beginners."""
        values, tip = dz.FABRICS[self.fabric.get()]
        d = dz.Settings()
        self.spacing.set(values["row_spacing_mm"])
        self.satin_spacing.set(values["satin_spacing_mm"])
        self.pull.set(values["pull_comp_mm"])
        self.underlay.set(values["underlay"])
        self.stitch_len.set(d.stitch_length_mm)
        self.angle.set(d.fill_angle)
        self.trim.set(d.trim_mm)
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

    def open_text(self):
        """Dialog to type words and pick a font; the text becomes the design."""
        fonts = dz.list_fonts()
        if not fonts:
            messagebox.showerror("No fonts", "No TrueType fonts were found on this computer.")
            return
        win = tk.Toplevel(self)
        win.title("Lettering")
        win.transient(self)
        win.resizable(False, False)
        frame = ttk.Frame(win, padding=12)
        frame.pack()
        ttk.Label(frame, text="Text (Enter for a new line)").grid(row=0, column=0, columnspan=2, sticky="w")
        entry = tk.Text(frame, width=34, height=3, font=("Segoe UI", 11))
        entry.grid(row=1, column=0, columnspan=2, pady=(2, 8))
        entry.focus_set()
        ttk.Label(frame, text="Font").grid(row=2, column=0, sticky="w")
        names = list(fonts)
        default = next((n for n in names if n.lower().startswith("arial bold")), names[0])
        font_var = tk.StringVar(value=default)
        ttk.Combobox(frame, textvariable=font_var, values=names, state="readonly", width=30).grid(
            row=2, column=1, sticky="e")
        rgb = [0, 0, 0]
        swatch = tk.Label(frame, width=4, background="#000000", relief="solid", borderwidth=1)

        def pick_color():
            chosen = colorchooser.askcolor(color="#%02x%02x%02x" % tuple(rgb), parent=win)
            if chosen and chosen[0]:
                rgb[:] = [int(v) for v in chosen[0]]
                swatch.config(background=chosen[1])

        ttk.Button(frame, text="Thread color...", command=pick_color).grid(row=3, column=0, sticky="w", pady=8)
        swatch.grid(row=3, column=1, sticky="w")

        def ok():
            text = entry.get("1.0", "end").strip()
            if not text:
                return
            try:
                img = dz.make_text_image(text, fonts[font_var.get()], tuple(rgb))
            except Exception as exc:
                messagebox.showerror("Could not draw text", str(exc), parent=win)
                return
            win.destroy()
            self.remove_bg.set(True)
            self._set_source(img, text.splitlines()[0][:20] or "text", f"Lettering: {text.splitlines()[0][:30]}")

        ttk.Button(frame, text="Use this text", command=ok).grid(row=4, column=0, columnspan=2, sticky="ew")
        win.bind("<Control-Return>", lambda e: ok())

    def _set_source(self, img, name, label):
        self.source, self.source_path, self.result = img, name, None
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
                colors=int(self.colors.get()), remove_background=self.remove_bg.get(),
                brother_colors=self.brother.get(), cleanup=int(self.cleanup.get()),
                fill_type=self.fill_type.get(), row_spacing_mm=float(self.spacing.get()),
                satin_spacing_mm=float(self.satin_spacing.get()),
                stitch_length_mm=float(self.stitch_len.get()), fill_angle=float(self.angle.get()),
                pull_comp_mm=float(self.pull.get()), underlay=self.underlay.get(), trim_mm=float(self.trim.get()),
                edge=self.edge.get(), outline=self.outline.get(),
            )
        except (tk.TclError, ValueError):
            messagebox.showerror("Invalid setting", "Please enter a number in every settings box.")
            return None
        s.width_mm = min(max(s.width_mm, 5), 400)
        s.height_mm = min(max(s.height_mm, 5), 400)
        s.colors = min(max(s.colors, 1), 15)
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

        def work(img=self.source):
            try:
                result = dz.convert(img, s)
                previews = dz.color_preview(result.labels, result.palette), dz.render_preview(result.pattern, hoop_mm=hoop)
                self.jobs.put((result, previews, None))
            except Exception as exc:
                self.jobs.put((None, None, exc))

        threading.Thread(target=work, daemon=True).start()
        self.after(100, self._poll)

    def _poll(self):
        try:
            result, previews, error = self.jobs.get_nowait()
        except queue.Empty:
            self.after(100, self._poll)
            return
        self.busy = False
        self.config(cursor="")
        self.generate_btn.config(state="normal")
        if error is not None:
            self.status.config(text=f"Error: {error}")
            return
        self.result = result
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
            if tag.startswith("swatch"):
                t.tag_delete(tag)
        if threads:
            t.insert("end", "Thread order (click a swatch to change its color):\n")
        for i, thread in enumerate(threads, 1):
            tag = f"swatch{i}"
            t.tag_configure(tag, background=thread["hex"])
            t.insert("end", f"{i:>2}. ")
            if thread.get("skipped"):
                t.insert("end", " skip ")
                t.insert("end", f"  {thread['name'] or thread['hex']}  (background, not stitched)\n")
                continue
            t.insert("end", "      ", tag)
            t.tag_bind(tag, "<Button-1>", lambda e, n=i - 1: self._recolor(n))
            t.tag_bind(tag, "<Enter>", lambda e: t.config(cursor="hand2"))
            t.tag_bind(tag, "<Leave>", lambda e: t.config(cursor="arrow"))
            outline = " [outline]" if thread.get("kind") == "outline" else ""
            t.insert("end", f"  {thread['name'] or thread['hex']}  ({thread['hex']}){outline}\n")
        t.config(state="disabled")

    def _recolor(self, index):
        """Changes one thread's color after conversion. The stitches stay the same."""
        if self.result is None or self.busy:
            return
        pattern, threads = self.result.pattern, self.result.threads
        if index >= len(pattern.threadlist):
            return
        current = threads[index]["hex"]
        chosen = colorchooser.askcolor(color=current, title="Thread color", parent=self)
        if not chosen or not chosen[0]:
            return
        rgb = tuple(int(v) for v in chosen[0])
        thread = pattern.threadlist[index]
        thread.set_color(*rgb)
        thread.description = dz.nearest_brother_thread(rgb)[1]
        for entry in (threads[index], self.result.info["threads"][index]):
            entry.update(rgb=rgb, hex=dz.hex_color(rgb), name=thread.description)
        hoop = dz.HOOPS.get(self.hoop.get())
        self.images["stitches"] = dz.render_preview(pattern, hoop_mm=hoop)
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
