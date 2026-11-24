# Embroidery Converter

Turns an image (logo, clipart, drawing) into a machine embroidery file for
Brother machines (`.pes`). It can also save `.dst`, `.jef`, `.exp` and `.vp3`.

## Run it

Double-click `run.bat`, or:

```
pip install -r requirements.txt
python app.py
```

1. Choose **Open image...**, **Lettering...**, or **Motifs...**. In either editor, **Use design** generates the first stitch preview.
2. Pick your **hoop** and **fabric**. The fabric sets the stitch density, pull compensation and the rest for you, and shows the stabilizer to use. Change the number of **thread colors** if needed, then click **Generate stitches**.
3. Look at the **Stitch preview** tab (realistic thread on a knit fabric; set **Shirt color** to see it on your shirt) and the thread list. The list shows the closest Brother thread name for each color, in sewing order. Click a color swatch to change that thread's color. The **Sew simulation** tab plays the stitching in machine order, in the same realistic style: underlay first, then fills, satin, travel and trims, with a pause at each thread change.
4. **Save embroidery file...** to write a `.pes`. Copy it to a USB stick, or send it to the machine over USB or wireless.

**Vector logos (.svg)** open directly and give the cleanest stitches: edges stay perfectly crisp at
any size, and watermark layers that free logo sites add are removed. In the thread list, click a swatch
to change a color (colors that end up the same are sewn as one thread), or click **skip** to leave that
color as bare fabric. Example: for a two-color logo you want in black only, skip the second color and
make the first one black.

Sizes are in inches. Tick **Show advanced settings** for fill type (satin for narrow shapes, tatami for large areas), edges, outline and the stitch settings in millimetres.

## Using it from the iPhone app

The iPhone app can send an image to this PC and download the finished `.pes`. The stitches are
made by the same engine as the desktop app, so they are identical.

1. On the PC, double-click `install_autostart.bat` once. It starts a small background server
   every time you sign in to Windows (undo with `uninstall_autostart.bat`). Or run `run_server.bat`
   when you need it. If Windows asks, allow Python on **private** networks.
2. Double-click `show_phone_info.bat` to see the PC's address and pairing code.
3. In the phone app, open **PC connection**, type the address and code, and tap **Test connection**.

The phone and PC must be on the same Wi-Fi network, and the PC must be on (not asleep). The phone
cannot wake or launch the PC. If the PC can't be reached, the app makes a simpler version on the phone.
The link is plain HTTP on your home network, protected only by the pairing code, so don't use it on public Wi-Fi.

**Add lettering** and **Add motif** open the iPhone editors. The phone can generate fill stitches
on its own. Satin, sketch, bean, and appliqué use the PC engine. Restart the PC server after
updating it to use these styles from the phone.

An iPhone version (SwiftUI) is in `ios/`. See `.github/workflows/ios.yml`; it builds an unsigned IPA you can sideload.

Command line:

```
python digitizer.py logo.png logo.pes --width 90 --height 90 --colors 4 --preview preview.png
python digitizer.py --help
```

## Stitch types and options

| Feature | What it does |
|---|---|
| Tatami fill | Rows sewn back and forth with staggered needle points. Density (row spacing), stitch length, angle, pull compensation. Long shapes are sewn along their length. |
| Fill patterns | brick3 (standard), brick2, brick4, diagonal, random: how needle points are offset between rows, which sets the fill's texture. |
| Satin columns | Narrow strokes (up to 6.5 mm) follow their centre line; split at sharp corners. |
| Satin underlay | Picked by column width like commercial software: none (<1 mm), centre run (1-2.5 mm), edge run (2.5-4 mm), edge run + zigzag (wider). |
| Short stitches | On tight curves every other stitch on the crowded inside edge stops short, so thread doesn't pile up. |
| Split satin | Stitches over 7 mm are split, with staggered split points so no line shows. |
| Fill underlay | Edge walk plus cross rows at 90 degrees. |
| Edges / outline | Running, triple (bean), satin, or e-stitch (blanket) on each color's edge or around the whole design. |
| Applique | Per color: placement line, pause, tack-down, pause (trim the fabric), satin cover. The pauses are same-color thread changes, like Brother's own applique files. |
| Knockdown | Open cross-hatch under the design that flattens towel loops and fleece. On automatically for the Towel / fleece preset. |
| Travel and trims | Moves inside a color follow a path through that color with hidden running stitches; other jumps over 1 mm are trimmed. Tie-in/tie-off lock stitches at every start and stop. |
| Colors | Automatic color count, CIEDE2000 matching to the Brother thread chart, recolor/skip/merge per color. |

All settings are in the desktop app (most under **Show advanced settings**; applique is a link next to each
color in the thread list).

### Proof

`python tests/test_stitches.py` builds test shapes, converts them, and measures the real stitches against the
settings: row spacing, stitch length, fill-pattern offsets, pull compensation, satin spacing and width,
satin underlay by width, short stitches, split stitches, e-stitch prongs, applique pauses in the saved file,
knockdown, trims, and all five file formats.

`python tests/make_sampler.py` writes `test_sews/stitch_sampler.pes`, every fill pattern, density and satin
type on one 4x4 in hoop, to sew and compare.

## Tips

- Works best on **simple, flat-color art** with a few colors and clear shapes. Photos and gradients convert, but look posterized.
- Details narrower than about **1 mm** (thin lines, small text) don't stitch well. Make the design bigger or simplify the art.
- Always stitch a test on scrap fabric of the same type, with stabilizer.

## How it works

`digitizer.py` scales the image to 0.15 mm per pixel and reduces it to N colors with k-means in
CIELAB (so colors are grouped the way the eye sees them). Each color is traced into smooth vector
shapes. Narrow strokes (up to 6.5 mm wide) become **satin columns** that follow the stroke's centre
line, split at sharp corners so the stitches don't fan out. Wider areas get **tatami** fill: an edge
walk and a cross-hatch underlay first, then rows with staggered needle points; long shapes are sewn
along their length. Fills are grown by the pull compensation so neighbouring colors overlap slightly
and no fabric shows between them. Every jump longer than 1 mm is trimmed so no loose threads are left.
Stitches, jumps, trims and color changes are written with
[pyembroidery](https://github.com/EmbroidePy/pyembroidery), which also adds tie-in/tie-off lock stitches.
