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

`digitizer.py` scales the image to 0.15 mm per pixel and reduces it to N colors with k-means in
CIELAB (so colors are grouped the way the eye sees them). Each color is traced into smooth vector
shapes. Narrow strokes (up to 6.5 mm wide) become **satin columns** that follow the stroke's centre
line, split at sharp corners so the stitches don't fan out. Wider areas get **tatami** fill: an edge
walk and a cross-hatch underlay first, then rows with staggered needle points; long shapes are sewn
along their length. Fills are grown by the pull compensation so neighbouring colors overlap slightly
and no fabric shows between them. Every jump longer than 1 mm is trimmed so no loose threads are left.
Stitches, jumps, trims and color changes are written with
[pyembroidery](https://github.com/EmbroidePy/pyembroidery), which also adds tie-in/tie-off lock stitches.
