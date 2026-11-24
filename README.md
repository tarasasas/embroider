# Embroidery Converter

Turns an image (logo, clipart, drawing) into a machine embroidery file for
Brother machines (`.pes`). It can also save `.dst`, `.jef`, `.exp` and `.vp3`.

## Run it

Double-click `run.bat`, or:

```
pip install -r requirements.txt
python app.py
```

1. **Open image...** (or **Lettering...** to type words): the app generates a first version right away.
2. Pick your **hoop** and **fabric**. The fabric sets the stitch density, pull compensation and the rest for you, and shows the stabilizer to use. Change the number of **thread colors** if needed, then click **Generate stitches**.
3. Look at the **Stitch preview** tab and the thread list. The list shows the closest Brother thread name for each color, in sewing order. Click a color swatch to change that thread's color. The **Sew simulation** tab replays the stitching in machine order.
4. **Save embroidery file...** to write a `.pes`. Copy it to a USB stick, or send it to the machine over USB or wireless.

Sizes are in inches. Tick **Show advanced settings** for fill type (satin for narrow shapes, tatami for large areas), edges, outline and the stitch settings in millimetres.

An iPhone version (SwiftUI) is in `ios/`. See `.github/workflows/ios.yml`; it builds an unsigned IPA you can sideload.

Command line:

```
python digitizer.py logo.png logo.pes --width 90 --height 90 --colors 4 --preview preview.png
python digitizer.py --help
```

## Settings

| Setting | What it does |
|---|---|
| Max width / height | The design is scaled to fit inside this size, keeping its proportions. Keep it a bit smaller than the hoop. |
| Thread colors | How many colors the image is reduced to (not counting a removed background). |
| Detail cleanup | Removes specks and smooths jagged edges. Raise it for JPEGs and scans. |
| Remove background | Skips the color along the image border (or transparent pixels). Every area of that color is skipped, including areas inside the design. |
| Use Brother thread colors | Snaps colors to the Brother thread chart, so the preview matches the threads you'll load. |
| Row spacing | Fill density. Smaller = denser (0.25-0.35 mm is typical). Go denser for towels and fleece, lighter for thin fabric. |
| Stitch length | Length of fill stitches. |
| Fill angle | Direction of the stitch rows. Changing it changes how the fill catches the light. |
| Pull compensation | Makes rows slightly wider, because stitching pulls the fabric in. |
| Underlay | Sparse stitches sewn first so the top fill lies flat. Leave on. |
| Trim jumps over | Cuts the thread instead of leaving long loose threads between areas. |

## Tips

- Works best on **simple, flat-color art** with a few colors and clear shapes. Photos and gradients convert, but look posterized.
- Details narrower than about **1 mm** (thin lines, small text) don't stitch well. Make the design bigger or simplify the art.
- Always stitch a test on scrap fabric of the same type, with stabilizer.

## How it works

`digitizer.py` scales the image to 0.2 mm per pixel. It reduces the image to N colors with k-means, then fills each color region with
**tatami** stitches: parallel rows at the fill angle, sewn back and forth, with staggered needle points so no lines
show. Underlay rows go at 90 degrees under the fill. Stitches, jumps, trims and color changes are written with
[pyembroidery](https://github.com/EmbroidePy/pyembroidery), which also adds tie-in/tie-off lock stitches.

In "auto" fill mode, narrow shapes become satin columns and wide areas get tatami fill. The iPhone version only does tatami fill.
