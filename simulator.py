"""Step-through sewing simulator: replays a pattern stitch by stitch, in machine order."""

import time
import tkinter as tk
from tkinter import ttk

import numpy as np
import realistic
from PIL import Image, ImageDraw, ImageTk
from pyembroidery import COLOR_CHANGE, END, JUMP, STITCH

FABRIC = (236, 232, 224)
JUMP_COLOR = (150, 150, 150)
SS = 2  # draw at 2x and shrink, for smooth thread lines
FRAME_MS = 30
MACHINE_SPM = 650  # stitches per minute of a typical home machine
MAX_RATE = 5000  # stitches per second at full speed


def _clock(minutes):
    return f"{int(minutes)}:{int(minutes * 60) % 60:02d}"


class SewSimulator(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.pattern = None
        self.total = 0  # needle drops in the pattern
        self.pos = 0  # needle drops sewn so far
        self.drawn = 0  # needle drops painted on self.img
        self.playing = False
        self.message = ""
        self.img = None
        self.photo = None
        self._job = None
        self._resize_job = None
        self._acc = 0.0
        self._last = 0.0

        self.realistic = True  # shaded thread on knit fabric; False = plain lines
        self.fabric_rgb = realistic.SHIRT_WHITE
        self.painter = None
        self.position = tk.DoubleVar(value=0)
        self.speed = tk.DoubleVar(value=0.45)  # 0..1 on a log scale
        self.stop_at_colors = tk.BooleanVar(value=True)
        self.show_jumps = tk.BooleanVar(value=False)
        self._build()

    # ------------------------------------------------------------------ widgets

    def _build(self):
        self.canvas = tk.Canvas(self, background="#f4f4f4", highlightthickness=0, takefocus=1)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._schedule_layout)
        self.canvas.bind("<Button-1>", lambda e: self.canvas.focus_set())
        keys = {
            "<space>": lambda e: self.toggle(),
            "<Left>": lambda e: self.step(-1),
            "<Right>": lambda e: self.step(1),
            "<Shift-Left>": lambda e: self.step(-50),
            "<Shift-Right>": lambda e: self.step(50),
            "<Home>": lambda e: self.goto(0),
            "<End>": lambda e: self.goto(self.total),
            "<Prior>": lambda e: self.prev_color(),
            "<Next>": lambda e: self.next_color(),
        }
        for key, handler in keys.items():
            self.canvas.bind(key, handler)

        bar = ttk.Frame(self, padding=(8, 6, 8, 8))
        bar.pack(fill="x")
        self.scrub = ttk.Scale(bar, from_=0, to=1, variable=self.position, command=self._scrubbed)
        self.scrub.pack(fill="x")

        controls = ttk.Frame(bar)
        controls.pack(fill="x", pady=(6, 2))
        buttons = [
            ("Start", lambda: self.goto(0)),
            ("< Color", self.prev_color),
            ("< Step", lambda: self.step(-1)),
            ("Play", self.toggle),
            ("Step >", lambda: self.step(1)),
            ("Color >", self.next_color),
            ("End", lambda: self.goto(self.total)),
        ]
        for text, command in buttons:
            button = ttk.Button(controls, text=text, command=command, width=8, takefocus=0)
            button.pack(side="left", padx=(0, 4))
            if text == "Play":
                self.play_btn = button

        ttk.Label(controls, text="Speed").pack(side="left", padx=(12, 4))
        ttk.Scale(controls, from_=0, to=1, variable=self.speed, length=140,
                  command=lambda v: self._update_speed_label()).pack(side="left")
        self.speed_label = ttk.Label(controls, width=10)
        self.speed_label.pack(side="left", padx=(4, 12))
        ttk.Checkbutton(controls, text="Pause at thread changes", variable=self.stop_at_colors,
                        takefocus=0).pack(side="left", padx=(0, 8))
        ttk.Checkbutton(controls, text="Show jumps", variable=self.show_jumps, takefocus=0,
                        command=self._redraw).pack(side="left")

        status = ttk.Frame(bar)
        status.pack(fill="x", pady=(4, 0))
        self.swatch = tk.Label(status, width=3, background="#f4f4f4", relief="solid", borderwidth=1)
        self.swatch.pack(side="left", padx=(0, 6))
        self.info = ttk.Label(status, text="Generate stitches to simulate sewing.")
        self.info.pack(side="left", fill="x")
        self._update_speed_label()

    def _update_speed_label(self):
        self.speed_label.config(text=f"{self._rate():,.0f} st/s")

    def _rate(self):
        return MAX_RATE ** float(self.speed.get())  # 1 .. MAX_RATE stitches per second

    # ------------------------------------------------------------------ data

    def load(self, pattern):
        """Prepares a pyembroidery pattern for playback, starting at the first stitch."""
        self.pause()
        segs, color, prev = [], 0, None
        for x, y, cmd in pattern.stitches:
            c = cmd & 0xFF
            if c == STITCH:
                segs.append((*(prev or (x, y)), x, y, color, 0))
            elif c == JUMP and prev is not None:
                segs.append((*prev, x, y, color, 1))
            elif c == COLOR_CHANGE:
                color += 1
            elif c == END:
                break
            prev = (x, y)

        self.pattern = pattern
        self.segs = np.array(segs, dtype=np.float64).reshape(-1, 6)
        self.stitch_seg = np.nonzero(self.segs[:, 5] == 0)[0]
        self.total = len(self.stitch_seg)
        threads = pattern.threadlist
        self.colors = [(t.get_red(), t.get_green(), t.get_blue()) for t in threads] or [(0, 0, 0)]
        self.names = [t.description or t.hex_color() for t in threads] or ["Thread"]
        stitch_color = np.minimum(self.segs[self.stitch_seg, 4].astype(int), len(self.colors) - 1)
        self.stitch_color = stitch_color
        # a new thread starts once this many stitches are sewn
        self.color_starts = (np.nonzero(np.diff(stitch_color))[0] + 1).tolist()
        self.scrub.configure(to=max(1, self.total))
        self.pos = self.drawn = 0
        self.message = ""
        self._layout()

    def clear(self):
        self.pause()
        self.pattern, self.total, self.pos, self.drawn, self.img = None, 0, 0, 0, None
        self.position.set(0)
        self._layout()

    def _seg_end(self, p):
        """Number of segments (stitches plus jumps) sewn after p needle drops."""
        return 0 if p <= 0 else int(self.stitch_seg[p - 1]) + 1

    # ------------------------------------------------------------------ drawing

    def _schedule_layout(self, _event=None):
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(120, self._layout)

    def _layout(self):
        """Fits the design into the canvas and rebuilds the drawing at the current position."""
        self._resize_job = None
        canvas = self.canvas
        canvas.delete("all")
        cw, ch = canvas.winfo_width(), canvas.winfo_height()
        if self.pattern is None or self.total == 0:
            canvas.create_text(cw / 2, ch / 2, text="Generate stitches to simulate sewing.",
                               fill="#999", font=("Segoe UI", 12))
            self.info.config(text="")
            self.swatch.config(background="#f4f4f4")
            return
        if cw < 50 or ch < 50:
            return

        min_x, min_y, max_x, max_y = self.pattern.bounds()
        pad = 12
        k = min((cw - 2 * pad) / max(max_x - min_x, 1), (ch - 2 * pad) / max(max_y - min_y, 1))
        w, h = int((max_x - min_x) * k) + 2 * pad, int((max_y - min_y) * k) + 2 * pad
        s = k * SS
        pts = self.segs.copy()
        pts[:, [0, 2]] = (pts[:, [0, 2]] - min_x) * s + pad * SS
        pts[:, [1, 3]] = (pts[:, [1, 3]] - min_y) * s + pad * SS
        self.lines = [(x0, y0, x1, y1, int(c), int(j)) for x0, y0, x1, y1, c, j in pts.tolist()]
        self.width = max(1, round(4 * s))  # thread ~0.4 mm = 4 units
        self.offset = ((cw - w) // 2, (ch - h) // 2)

        # faint image of the whole design, so you can see what is still to come
        if self.realistic:
            self.px_per_mm = s * 10  # s is pixels per 0.1 mm
            fabric = realistic.fabric_texture((w * SS, h * SS), self.fabric_rgb, self.px_per_mm)
            whole = realistic.ThreadPainter(fabric.size, self.px_per_mm, fabric=fabric)
            for x0, y0, x1, y1, c, is_jump in self.lines:
                if not is_jump:
                    whole.stitch((x0, y0), (x1, y1), self.colors[min(c, len(self.colors) - 1)])
            self.ghost = Image.blend(fabric, whole.compose(), 0.18)
        else:
            full = Image.new("RGB", (w * SS, h * SS), FABRIC)
            self._draw_segments(ImageDraw.Draw(full), 0, len(self.lines), jumps=False)
            self.ghost = Image.blend(Image.new("RGB", full.size, FABRIC), full, 0.18)

        self.photo = ImageTk.PhotoImage("RGB", (w, h))
        canvas.create_image(*self.offset, image=self.photo, anchor="nw")
        r = 7
        canvas.create_oval(-r, -r, r, r, outline="#ffffff", width=4, tags="needle")
        canvas.create_oval(-r, -r, r, r, outline="#e0245e", width=2, tags="needle")
        canvas.create_line(-r - 5, 0, r + 5, 0, fill="#e0245e", tags="needle")
        canvas.create_line(0, -r - 5, 0, r + 5, fill="#e0245e", tags="needle")
        self._needle_at = (0, 0)
        self._redraw()

    def set_style(self, realistic_on=None, fabric_rgb=None):
        """Switches between realistic and plain drawing, and sets the fabric (shirt) color."""
        if realistic_on is not None:
            self.realistic = bool(realistic_on)
        if fabric_rgb is not None:
            self.fabric_rgb = tuple(fabric_rgb)
        self._layout()

    def _new_canvas(self):
        """Bare fabric (plus the faint whole design) to sew onto."""
        if self.realistic:
            self.painter = realistic.ThreadPainter(self.ghost.size, self.px_per_mm, fabric=self.ghost)
            self.img = self.ghost
        else:
            self.painter = None
            self.img = self.ghost.copy()

    def _draw_segments(self, draw, a, b, jumps):
        """Paints segments a..b: shaded thread (realistic) or polylines of one color (plain)."""
        lines, colors, width = self.lines, self.colors, self.width
        if self.realistic and self.painter is not None and draw is None:
            for x0, y0, x1, y1, c, is_jump in lines[a:b]:
                if is_jump:
                    if jumps:
                        self.painter.draw.line((x0, y0, x1, y1), fill=JUMP_COLOR + (255,), width=SS)
                else:
                    self.painter.stitch((x0, y0), (x1, y1), colors[min(c, len(colors) - 1)])
            return
        poly, poly_color = [], 0

        def flush():
            if len(poly) > 1:
                draw.line(poly, fill=colors[min(poly_color, len(colors) - 1)], width=width)
            poly.clear()

        for x0, y0, x1, y1, c, is_jump in lines[a:b]:
            if is_jump:
                flush()
                if jumps:
                    draw.line((x0, y0, x1, y1), fill=JUMP_COLOR, width=SS)
            elif poly and c == poly_color and poly[-1] == (x0, y0):
                poly.append((x1, y1))
            else:
                flush()
                poly.extend(((x0, y0), (x1, y1)))
                poly_color = c
        flush()

    def _redraw(self):
        if self.pattern is None or self.photo is None:
            return
        self._new_canvas()
        self.drawn = 0
        self.goto(self.pos, keep_message=True)

    def goto(self, p, keep_message=False):
        if self.pattern is None or self.img is None:
            return
        p = max(0, min(int(p), self.total))
        if not keep_message:
            self.message = ""
        if p < self.drawn:  # going backwards: start again from bare fabric
            self._new_canvas()
            self.drawn = 0
        if p > self.drawn:
            target = None if self.realistic else ImageDraw.Draw(self.img)
            self._draw_segments(target, self._seg_end(self.drawn), self._seg_end(p), self.show_jumps.get())
        self.drawn = self.pos = p
        self._present()

    def _present(self):
        p = self.pos
        frame = self.painter.compose() if (self.realistic and self.painter is not None) else self.img
        self.photo.paste(frame.reduce(SS))
        self.position.set(p)

        x0, y0, x1, y1, _, _ = self.lines[self.stitch_seg[max(p - 1, 0)]]
        x, y = (x1, y1) if p > 0 else (x0, y0)
        nx, ny = self.offset[0] + x / SS, self.offset[1] + y / SS
        self.canvas.move("needle", nx - self._needle_at[0], ny - self._needle_at[1])
        self._needle_at = (nx, ny)

        c = int(self.stitch_color[max(p - 1, 0)])
        self.swatch.config(background="#%02x%02x%02x" % self.colors[c])
        text = (f"Stitch {p:,} of {self.total:,}     Thread {c + 1} of {len(self.colors)}: {self.names[c]}"
                f"     Sewing time {_clock(p / MACHINE_SPM)} of {_clock(self.total / MACHINE_SPM)}")
        if self.message:
            text += f"     {self.message}"
        self.info.config(text=text)

    # ------------------------------------------------------------------ playback

    def toggle(self):
        if self.playing:
            self.pause()
        else:
            self.play()

    def play(self):
        if self.pattern is None or self.img is None:
            return
        if self.pos >= self.total:
            self.goto(0)
        self.playing, self._acc, self._last = True, 0.0, time.perf_counter()
        self.message = ""
        self.play_btn.config(text="Pause")
        self._job = self.after(FRAME_MS, self._tick)

    def pause(self):
        self.playing = False
        if self._job:
            self.after_cancel(self._job)
            self._job = None
        if hasattr(self, "play_btn"):
            self.play_btn.config(text="Play")

    def _tick(self):
        self._job = None
        if not self.playing:
            return
        now = time.perf_counter()
        self._acc += self._rate() * (now - self._last)
        self._last = now
        n = int(self._acc)
        if n:
            self._acc -= n
            target = min(self.pos + n, self.total)
            boundary = next((b for b in self.color_starts if b > self.pos), None)
            if self.stop_at_colors.get() and boundary is not None and target >= boundary:
                self.goto(boundary)
                self.pause()
                self.message = f"Change thread to {self.names[int(self.stitch_color[boundary])]}, then press Play."
                self._present()
                return
            self.goto(target)
            if target >= self.total:
                self.pause()
                self.message = "Done!"
                self._present()
                return
        self._job = self.after(FRAME_MS, self._tick)

    def step(self, n):
        self.pause()
        self.goto(self.pos + n)

    def next_color(self):
        self.pause()
        self.goto(next((b for b in self.color_starts if b > self.pos), self.total))

    def prev_color(self):
        self.pause()
        self.goto(next((b for b in reversed(self.color_starts) if b < self.pos), 0))

    def _scrubbed(self, value):
        p = int(float(value))
        if p != self.pos:
            self.pause()
            self.goto(p)
