"""Desktop app: convert an image into a Brother embroidery file (.pes)."""

import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

import digitizer as dz

IMAGE_TYPES = [("Images", "*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff"), ("All files", "*.*")]
SAVE_TYPES = [
    ("Brother PES", "*.pes"),
    ("Tajima DST", "*.dst"),
    ("Janome JEF", "*.jef"),
    ("Melco EXP", "*.exp"),
    ("Husqvarna VP3", "*.vp3"),
]
TABS = [("original", "Original"), ("colors", "Color regions"), ("stitches", "Stitch preview")]


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Embroidery Converter")
        self.geometry("1280x840")
        self.minsize(960, 660)

        self.source = None
        self.source_path = None
        self.result = None
        self.images = {key: None for key, _ in TABS}
        self.photos = {}
        self.canvases = {}
        self.jobs = queue.Queue()

        d = dz.Settings()
        hoop = next(iter(dz.HOOPS))
        self.hoop = tk.StringVar(value=hoop)
        self.width_mm = tk.DoubleVar(value=dz.HOOPS[hoop][0] - 10)
        self.height_mm = tk.DoubleVar(value=dz.HOOPS[hoop][1] - 10)
        self.colors = tk.IntVar(value=d.colors)
        self.cleanup = tk.IntVar(value=d.cleanup)
        self.remove_bg = tk.BooleanVar(value=d.remove_background)
        self.brother = tk.BooleanVar(value=d.brother_colors)
        self.spacing = tk.DoubleVar(value=d.row_spacing_mm)
        self.stitch_len = tk.DoubleVar(value=d.stitch_length_mm)
        self.angle = tk.DoubleVar(value=d.fill_angle)
        self.pull = tk.DoubleVar(value=d.pull_comp_mm)
        self.trim = tk.DoubleVar(value=d.trim_mm)
        self.underlay = tk.BooleanVar(value=d.underlay)

        self._build_ui()

    # ------------------------------------------------------------------ layout

    def _build_ui(self):
        root = ttk.Frame(self, padding=8)
        root.pack(fill="both", expand=True)

        side = ttk.Frame(root)
        side.pack(side="left", fill="y", padx=(0, 8))

        ttk.Button(side, text="Open image...", command=self.open_image).pack(fill="x")
        self.file_label = ttk.Label(side, text="No image loaded", foreground="#666", wraplength=300)
        self.file_label.pack(fill="x", pady=(4, 8))

        size = self._group(side, "Size")
        ttk.Label(size, text="Hoop").grid(row=0, column=0, sticky="w", pady=2)
        hoop = ttk.Combobox(size, textvariable=self.hoop, values=list(dz.HOOPS), state="readonly", width=24)
        hoop.grid(row=0, column=1, sticky="e", pady=2)
        hoop.bind("<<ComboboxSelected>>", self._hoop_changed)
        self._spin(size, 1, "Max width (mm)", self.width_mm, 10, 300, 1)
        self._spin(size, 2, "Max height (mm)", self.height_mm, 10, 300, 1)

        colors = self._group(side, "Colors")
        self._spin(colors, 0, "Thread colors", self.colors, 1, 15, 1)
        self._spin(colors, 1, "Detail cleanup (0-3)", self.cleanup, 0, 3, 1)
        ttk.Checkbutton(colors, text="Remove background", variable=self.remove_bg).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Checkbutton(colors, text="Use Brother thread colors", variable=self.brother).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=2)

        stitches = self._group(side, "Stitches")
        self._spin(stitches, 0, "Row spacing (mm)", self.spacing, 0.25, 1.5, 0.05)
        self._spin(stitches, 1, "Stitch length (mm)", self.stitch_len, 2, 7, 0.5)
        self._spin(stitches, 2, "Fill angle (deg)", self.angle, 0, 180, 15)
        self._spin(stitches, 3, "Pull compensation (mm)", self.pull, 0, 0.6, 0.05)
        self._spin(stitches, 4, "Trim jumps over (mm)", self.trim, 1, 20, 1)
        ttk.Checkbutton(stitches, text="Underlay", variable=self.underlay).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=2)

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

    def _hoop_changed(self, _event=None):
        w, h = dz.HOOPS[self.hoop.get()]
        self.width_mm.set(w - 10)  # leave a margin inside the hoop
        self.height_mm.set(h - 10)

    # ------------------------------------------------------------------ images

    def _current_tab(self):
        return TABS[self.tabs.index(self.tabs.select())][0]

    def _show(self, key):
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
        self.source, self.source_path, self.result = img, path, None
        shown = Image.new("RGBA", img.size, (244, 244, 244, 255))
        shown.alpha_composite(img)
        self.images = {"original": shown.convert("RGB"), "colors": None, "stitches": None}
        self.file_label.config(text=f"{os.path.basename(path)}  ({img.width} x {img.height} px)")
        self.save_btn.config(state="disabled")
        self._set_threads([])
        self.tabs.select(0)
        self._show("original")
        self.generate()

    def _read_settings(self):
        try:
            s = dz.Settings(
                width_mm=float(self.width_mm.get()), height_mm=float(self.height_mm.get()),
                colors=int(self.colors.get()), remove_background=self.remove_bg.get(),
                brother_colors=self.brother.get(), cleanup=int(self.cleanup.get()),
                row_spacing_mm=float(self.spacing.get()), stitch_length_mm=float(self.stitch_len.get()),
                fill_angle=float(self.angle.get()), pull_comp_mm=float(self.pull.get()),
                underlay=self.underlay.get(), trim_mm=float(self.trim.get()),
            )
        except (tk.TclError, ValueError):
            messagebox.showerror("Invalid setting", "Please enter a number in every settings box.")
            return None
        s.width_mm = min(max(s.width_mm, 5), 400)
        s.height_mm = min(max(s.height_mm, 5), 400)
        s.colors = min(max(s.colors, 1), 15)
        s.cleanup = min(max(s.cleanup, 0), 3)
        s.row_spacing_mm = min(max(s.row_spacing_mm, 0.2), 3)
        s.stitch_length_mm = min(max(s.stitch_length_mm, 1), 10)
        s.pull_comp_mm = min(max(s.pull_comp_mm, 0), 1)
        return s

    def generate(self):
        if self.source is None:
            messagebox.showinfo("No image", "Open an image first.")
            return
        s = self._read_settings()
        if s is None:
            return
        self.generate_btn.config(state="disabled")
        self.save_btn.config(state="disabled")
        self.status.config(text="Digitizing... this can take a few seconds.")
        self.config(cursor="watch")

        def work(img=self.source):
            try:
                result = dz.convert(img, s)
                previews = dz.color_preview(result.labels, result.palette), dz.render_preview(result.pattern)
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
        self.config(cursor="")
        self.generate_btn.config(state="normal")
        if error is not None:
            self.status.config(text=f"Error: {error}")
            return
        self.result = result
        self.images["colors"], self.images["stitches"] = previews
        info = result.info
        self.status.config(text=(
            f"Size: {info['width_mm']:.1f} x {info['height_mm']:.1f} mm\n"
            f"Stitches: {info['stitches']:,}    Colors: {info['colors']}    Trims: {info['trims']}\n"
            f"Estimated sewing time: ~{max(1, round(info['minutes']))} min"
        ))
        self._set_threads(info["threads"])
        self.save_btn.config(state="normal")
        self.tabs.select(2)
        self._show("stitches")

    def _set_threads(self, threads):
        t = self.threads
        t.config(state="normal")
        t.delete("1.0", "end")
        if threads:
            t.insert("end", "Thread order (closest Brother thread):\n")
        for i, thread in enumerate(threads, 1):
            tag = f"swatch{i}"
            t.tag_configure(tag, background=thread["hex"])
            t.insert("end", f"{i:>2}. ")
            t.insert("end", "      ", tag)
            t.insert("end", f"  {thread['name'] or thread['hex']}  ({thread['hex']})\n")
        t.config(state="disabled")

    def save(self):
        if self.result is None:
            return
        base = os.path.splitext(os.path.basename(self.source_path))[0]
        path = filedialog.asksaveasfilename(
            title="Save embroidery file", initialfile=base + ".pes", defaultextension=".pes", filetypes=SAVE_TYPES)
        if not path:
            return
        try:
            path = dz.save(self.result.pattern, path)
        except Exception as exc:
            messagebox.showerror("Could not save", str(exc))
            return
        self.status.config(text=self.status.cget("text") + f"\n\nSaved: {path}")


if __name__ == "__main__":
    try:  # crisp text on high-DPI Windows displays
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    App().mainloop()
