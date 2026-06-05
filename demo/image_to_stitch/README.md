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
| `--width-mm` | — | physical design width |
| `--row-spacing-mm` | §4 | fill density (0.3–0.5 typical) |
| `--max-stitch-mm` | §4 | longest single stitch |
| `--angle` | §4 | fill direction in degrees |
| `--format` | §6 | dst, pes, exp, jef, vp3, csv |

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
