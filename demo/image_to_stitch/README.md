# Image → Multi-Colour Embroidery — Demo

A runnable demo of the full **image → multi-colour stitch** pipeline, plus a
detailed technical spec.

## Files
- `TECH_SPEC_image_to_multicolor_stitch.md` — full spec: every stage, every option, defaults.
- `image_to_stitch.py` — self-contained, runnable demo (no Inkscape needed).
- `out.dst` / `out.pes` / `out.png` — sample outputs (regenerated on each run).

## Quick start
```bash
pip install pystitch shapely numpy pillow

# sample image, 5 colours -> out.dst + out.png
python image_to_stitch.py

# your own image, 8 colours, denser fill, 45° angle, PES output
python image_to_stitch.py photo.png --colors 8 --row-spacing-mm 0.4 --angle 45 --format pes
```

## Options
| Flag | Spec § | Meaning |
|------|--------|---------|
| `--colors N` | §2 | number of thread colours (quantization) |
| **Size** | | |
| `--width-mm` | §1.1 | physical design width on fabric |
| `--height-mm` | §1.1 | physical design height (optional; derived from aspect if omitted) |
| `--fit contain\|stretch` | §1.1 | aspect handling when both w & h are given |
| **Density** | | |
| `--row-spacing-mm` | §1.1, §4 | gap between fill rows (0.30–0.50 typical). Auto if omitted. |
| `--density` | §1.1 | alternative: rows per mm (e.g. 2.5 = 0.4 mm spacing) |
| `--max-stitch-mm` | §4 | longest single stitch (default 3.0) |
| `--angle` | §4 | fill direction in degrees |
| **Background** | | |
| `--remove-bg` | §1.2 | force background removal (default: auto-detect) |
| `--keep-bg` | §1.2 | do NOT remove background (debug / appliqué) |
| `--bg-color R,G,B` | §1.2 | explicit background colour |
| `--bg-tolerance` | §1.2 | how close to bg-color counts (0–255, default 18) |
| `--alpha-threshold` | §1.2 | alpha cutoff for transparent images (default 128) |
| `--min-region-mm2` | §1.2 | drop colour regions smaller than this |
| **Output** | | |
| `--format` | §6 | dst, pes, exp, jef, vp3, csv |
| `--outdir` | | where to write `out.<fmt>` and `out.png` |

### What mm should I pick? (real-world reference)

Embroidery is sized in **millimetres on the fabric**, constrained by the **hoop**
on your machine. Pick a hoop, that fixes the maximum design size.

| Hoop  | mm        | inches    | Use case                                         |
|-------|-----------|-----------|--------------------------------------------------|
| 4×4   | 100×100   | 3.9×3.9   | Most common home hoop. Badges, patches, monograms |
| 5×7   | 130×180   | 5.1×7.1   | Medium home hoop. Pocket logos, larger crests    |
| 6×10  | 160×260   | 6.3×10.2  | Large home / commercial. Back-of-jacket, big motifs |
| 8×8   | 200×200   | 7.9×7.9   | Commercial square. Full chest logos              |
| 8×12  | 200×300   | 7.9×11.8  | Commercial. Big designs, banners                 |
| A4    | 210×297   | 8.3×11.7  | Commercial multi-needle                          |
| 12×12 | 300×300   | 11.8×11.8 | Industrial single-head                           |

List them at any time:
```bash
python image_to_stitch.py --list-hoops
```

Use a hoop directly as a sizing shortcut:
```bash
python image_to_stitch.py photo.png --hoop 5x7    # fits design inside 130x180 mm
python image_to_stitch.py photo.png --hoop 4x4    # 100x100 mm hoop
```

**Reference sizes for common things:**

| Thing                    | Typical size  | Hoop     |
|--------------------------|---------------|----------|
| Shirt pocket logo        | 70–90 mm wide | 4×4      |
| Cap front                | 50–60 mm tall | 4×4      |
| Full chest / back logo   | 100–130 mm    | 5×7      |
| Hoodie back panel        | 200–280 mm    | 6×10+    |
| Towel monogram           | 40–60 mm      | 4×4      |
| Patch / badge            | 50–100 mm     | 4×4      |

**Per-stitch dimensions** (independent of hoop):

| Setting              | Typical | Meaning |
|----------------------|---------|---------|
| `--stitch-length-mm` | 2.5–3.5 | longest single needle drop; smaller = finer / slower / more thread |
| `--row-spacing-mm`   | 0.30–0.50 | gap between fill rows; smaller = denser coverage, heavier fabric load |
| `--density`          | 2.0–3.5 rows/mm | inverse of row-spacing if you prefer thinking that way |

`--stitch-length-mm` is an alias for `--max-stitch-mm`; use whichever feels natural.

### Sizing examples
```bash
# 80 mm wide, height auto from aspect, auto density (0.40 mm)
python image_to_stitch.py photo.png --width-mm 80

# fit inside a 100x100 mm box preserving aspect, force 0.35 mm density
python image_to_stitch.py photo.png --width-mm 100 --height-mm 100 --row-spacing-mm 0.35

# big design (200 mm), auto density picks 0.50 mm
python image_to_stitch.py photo.png --width-mm 200

# explicit density in rows/mm
python image_to_stitch.py photo.png --width-mm 80 --density 3.0
```

### Background examples
```bash
# auto (default): detect from alpha or corner colour
python image_to_stitch.py photo.png --colors 4

# explicit white background, looser tolerance for JPEG artefacts
python image_to_stitch.py photo.jpg --bg-color 255,255,255 --bg-tolerance 30

# keep background (e.g. for appliqué or full-coverage designs)
python image_to_stitch.py photo.png --keep-bg
```

## Verifying output
```python
import pystitch
p = pystitch.read("out.pes")
print(len(p.stitches), "stitches", len(p.threadlist), "colours")
```
Note: `.dst` is stitch-only and stores **no** colours (a format limitation).
Use `.pes`/`.vp3`/`.jef` if you need colours embedded in the file. See spec §6.

## Relationship to real Ink/Stitch
The demo mirrors Ink/Stitch's data flow but uses a simplified scan-line fill so
it runs without Inkscape/inkex. For production-grade routing (Eulerian path,
underlay, pull compensation) run the same design through Inkscape + Ink/Stitch —
see spec §7 "Production path".
