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
