# Technical Specification — Multi-Colour Image → Embroidery Stitch Generation

This document is the engineering spec for turning a **raster image** into a
**multi-colour machine-embroidery file** using the Ink/Stitch pipeline. It
covers every stage, every tunable option, default/recommended values, and two
ways to run it: the **production path** (real Ink/Stitch inside Inkscape) and the
**embeddable path** (the runnable `image_to_stitch.py` demo in this folder, which
uses the same data model via pystitch but no Inkscape).

A working, runnable demo lives next to this file. See **§7 Demo**.

---

## 1. Pipeline overview

```
            ┌────────────┐   colour-reduce    ┌──────────────┐
  image ──▶ │ 1. Quantize│ ─────────────────▶ │ N colour     │
 (PNG/JPG)  │  to N cols │                     │ regions      │
            └────────────┘                     └──────┬───────┘
                                                      │ per colour
                                            ┌─────────▼─────────┐
                                            │ 2. Vectorise mask │  (trace → SVG paths
                                            │    → polygons     │   or pixel union)
                                            └─────────┬─────────┘
                                                      │
                                            ┌─────────▼─────────┐
                                            │ 3. Fill algorithm │  auto_fill / contour /
                                            │  (rows → stitches)│   guided / cross-stitch
                                            └─────────┬─────────┘
                                                      │ StitchGroup per colour
                                            ┌─────────▼─────────┐
                                            │ 4. Stitch plan    │  colour blocks, ties,
                                            │  (order + commands)│  trims, jumps
                                            └─────────┬─────────┘
                                                      │
                                            ┌─────────▼─────────┐
                                            │ 5. Encode/export  │  DST / PES / EXP / JEF…
                                            └───────────────────┘
```

Each stage maps to concrete Ink/Stitch code:

| Stage | Production (Ink/Stitch) | Demo (`image_to_stitch.py`) |
|-------|-------------------------|-----------------------------|
| 1 Quantize | `lib/extensions/utils/bitmap_to_cross_stitch.py` | `quantize()` (Pillow adaptive palette) |
| 2 Vectorise | Inkscape *Trace Bitmap* (potrace) → `<path>` | `mask_to_polygons()` (shapely union) |
| 3 Fill | `lib/stitches/auto_fill.py` `auto_fill()` | `fill_polygon()` (boustrophedon scan-line) |
| 4 Plan | `lib/stitch_plan/stitch_plan.py` `stitch_groups_to_stitch_plan()` | `build_pattern()` |
| 5 Export | `lib/output.py` `write_embroidery_file()` | `pystitch.write()` |

> The demo's fill is a simplified analogue. Real `auto_fill` builds a networkx
> graph and solves an **Eulerian path** for optimal single-thread routing with
> under-pathing and pull compensation. The **options are identical**, so tuning
> learned on the demo transfers to production.

---

## 2. Stage 1 — Colour quantization (how "multi-colour" is decided)

The number of thread colours = the number of times the machine stops to re-thread.
Fewer colours ⇒ faster, cheaper, less registration error. This is the single most
important decision for multi-colour work.

| Option | Meaning | Default | Recommended |
|--------|---------|---------|-------------|
| `num_colors` | palette size after reduction | 5 | 3–8 for logos; 8–15 for portraits |
| `method` | quantization algo | FastOctree | median-cut or k-means for photos |
| `dither` | error diffusion | off | **off** for embroidery (dither = noise = thread chaos) |
| `brightness` / `contrast` | pre-adjust | 0 / 0 | boost contrast to separate regions |
| `saturation` | pre-adjust | 0 | raise to make colours snap apart |
| `transparency_threshold` | alpha cutoff → "no stitch" | 200 | 128–220 |
| `min_region_area_mm2` | discard tiny specks | 1.0 | raise to avoid un-stitchable dots |

**Thread matching.** After quantization, snap each RGB to a real thread using
Ink/Stitch's catalog:

```python
from lib.threads.catalog import ThreadCatalog
ThreadCatalog().match_and_apply_palette(stitch_plan, "Madeira Polyneon")
```

75 palettes ship in `palettes/*.gpl` (Madeira, Isacord, Aurifil, Brother, DMC…).
This is where "5 arbitrary RGBs" becomes "5 spools the user actually owns."

---

## 3. Stage 2 — Vectorisation (image → polygons)

Embroidery fills need **closed polygons**, not pixels.

- **Production:** Inkscape *Path ▸ Trace Bitmap* (Shift+Alt+B) runs potrace.
  Use "Colors" mode with the same N as Stage 1 to get one path per colour.
  External alternatives: `vtracer` (fast, colour) or `autotrace`.
- **Demo:** unions one square per pixel then `simplify()` — coarse but
  dependency-free.

| Option | Meaning | Default | Notes |
|--------|---------|---------|-------|
| `trace_mode` | brightness / edge / colour | colour | colour gives one region per thread |
| `simplify_mm` | path smoothing tolerance | 0.3 | higher = smoother, fewer nodes |
| `speckle_suppression` | drop blobs < N px | on | removes trace noise |
| `min_area_mm2` | discard tiny polys | 1.0 | machines can't stitch <~1 mm² |

Output of this stage: for each colour, one shapely `Polygon`/`MultiPolygon`
in **millimetre** coordinates.

---

## 4. Stage 3 — Fill algorithm (the core)

This is where polygons become ordered stitch points. Ink/Stitch offers several
fill methods; pick per region.

### 4.1 Fill method selection

| `fill_method` | Best for | Module |
|---------------|----------|--------|
| `auto_fill` | general flat areas (default) | `lib/stitches/auto_fill.py` |
| `contour_fill` | organic shapes, leaves, swirls | `lib/stitches/contour_fill.py` |
| `guided_fill` | follow a curve / texture direction | `lib/stitches/guided_fill.py` |
| `circular_fill` | eyes, suns, radial shapes | `lib/stitches/circular_fill.py` |
| `linear_gradient_fill` | shading / photo depth (density ramp) | `lib/stitches/linear_gradient_fill.py` |
| `meander_fill` | sketchy single-line fills | `lib/stitches/meander_fill.py` |
| `cross_stitch` | pixel-art / counted-cross look | `lib/stitches/cross_stitch.py` |
| `tartan_fill` | plaids / patterns | `lib/stitches/tartan_fill.py` |

### 4.2 `auto_fill` options (the ones you tune most)

Signature (`lib/stitches/auto_fill.py:74`), all distances in **pixels**
(`PIXELS_PER_MM = 96/25.4 ≈ 3.78`; multiply mm by this):

| Param | Meaning | Default | Recommended |
|-------|---------|---------|-------------|
| `angle` | row direction, **radians** | 0 | vary per region for realism |
| `row_spacing` | gap between rows = **density** | 0.25 mm | 0.3–0.5 mm (denser = heavier, slower) |
| `end_row_spacing` | taper density across shape | None | for gradient effects |
| `max_stitch_length` | longest single stitch | 4.0 mm | 2.5–3.5 mm |
| `running_stitch_length` | travel-stitch length | 2.5 mm | 1.5–2.5 mm |
| `running_stitch_tolerance` | path-smoothing tol | 0.2 mm | leave default |
| `staggers` | brick-offset rows (hides "tramlines") | 4 | 2–4 |
| `skip_last` | omit last row stitch | False | False |
| `starting_point` | where the fill begins | — | nearest exit of previous colour |
| `ending_point` | where it ends | None | toward next region |
| `underpath` | route travel **under** fill | True | **True** (clean front face) |
| `gap_fill_rows` | extra rows to close gaps | 0 | 0–2 |
| `enable_random_stitch_length` | jitter for organic look | False | True for photos |
| `random_sigma` / `random_seed` | jitter amount / repeatable | 0 / "" | 0.1–0.3 sigma |
| `pull_compensation_px` / `_percent` | widen to fight fabric pull | (0,0) | 0.2–0.4 mm on satins |

### 4.3 Underlay (do not skip for real sew-outs)

Underlay = a sparse first pass that stabilises fabric. Configured on the
`FillStitch` element (`lib/elements/fill_stitch.py`):

| Param | Meaning | Default |
|-------|---------|---------|
| `fill_underlay` | enable | True |
| `fill_underlay_angle` | usually `angle + 90°` | auto |
| `fill_underlay_row_spacing` | sparser than top | ~3× top spacing |
| `fill_underlay_max_stitch_length` | longer | larger |

---

## 5. Stage 4 — Stitch plan (multi-colour sequencing)

`stitch_groups_to_stitch_plan(stitch_groups, collapse_len=3.0, min_stitch_len=0.1, disable_ties=False)`
(`lib/stitch_plan/stitch_plan.py:18`) takes one `StitchGroup` per colour region and:

- groups them into **colour blocks** (one per thread),
- inserts a **colour-change** command between blocks,
- adds **lock/tie stitches** at the start/end of each block (so threads don't unravel),
- converts long gaps into **jumps**, or inserts a **trim** when configured,
- filters duplicate/zero-length stitches.

| Option | Meaning | Default | Notes |
|--------|---------|---------|-------|
| `collapse_len` (mm) | jumps shorter than this become normal stitches | 3.0 | avoids needless trims |
| `min_stitch_len` (mm) | drop stitches shorter than this | 0.1 | use **0.4** for production (machines bunch < 0.5 mm) |
| `disable_ties` | skip lock stitches | False | keep False |
| `trim_after` (per group) | cut thread after region | — | set True between far-apart same-colour areas |
| `stop_after` (per group) | machine stop (e.g. applique) | — | rare |

**Colour ordering matters.** Sort regions to minimise colour changes and travel:
group all areas of the same colour into one block; sew background/dark first,
highlights last. The demo sorts by luminance as a simple heuristic.

The resulting `StitchPlan` exposes QA metrics you can gate on:
`num_stitches`, `num_colors`, `num_jumps`, `num_trims`, `bounding_box`,
`estimated_thread` (metres).

---

## 6. Stage 5 — Export

`write_embroidery_file(file_path, stitch_plan, svg, settings)` (`lib/output.py:53`)
maps each `Stitch` flag to a pystitch command (`JUMP / NEEDLE_AT / TRIM /
COLOR_CHANGE / STOP / END`) and encodes the file.

| Format | Ext | Stores colours? | Typical machine |
|--------|-----|-----------------|-----------------|
| Tajima | `.dst` | **No** (stitch-only; colours via operator/.edr) | industrial |
| Brother/Baby Lock | `.pes` | Yes | home |
| Melco | `.exp` | No | commercial |
| Janome | `.jef` | Yes | home |
| Husqvarna/Pfaff | `.vp3` | Yes | home |
| Singer | `.xxx` | Yes | home |
| Debug | `.csv` | Yes (text) | inspection |

> **Multi-colour gotcha:** `.dst` does **not** embed thread colours — that's a
> format limitation, not a bug. If you need colours preserved in the file,
> export `.pes`/`.vp3`/`.jef`, or ship a colour sidecar. The demo proves this:
> the same design exported as `.dst` reads back with 0 threads, but as `.pes`
> with all 5.

Key `settings` (auto-filled by `write_embroidery_file`):
`scale = 10/PIXELS_PER_MM` (px → 1/10 mm), `translate = -origin`,
`full_jump = True`, `trims = True`.

---

## 7. Demo (runnable now)

`image_to_stitch.py` in this folder runs the whole pipeline with only
pip-installable deps (no Inkscape):

```bash
pip install pystitch shapely numpy pillow

# sample image, 5 colours
python image_to_stitch.py

# your own image, tuned
python image_to_stitch.py photo.png --colors 8 --width-mm 100 \
       --row-spacing-mm 0.4 --max-stitch-mm 3 --angle 45 --format pes
```

Outputs `out.<format>` + `out.png` preview. Options map directly to the spec:
`--colors`→§2, `--angle`/`--row-spacing-mm`/`--max-stitch-mm`→§4, `--format`→§6.

### Production path (full quality, needs Inkscape + Ink/Stitch installed)

```bash
# 1. Trace your image to SVG paths (Inkscape GUI: Path ▸ Trace Bitmap, Colors mode)
# 2. Assign each region a fill colour, mark as Ink/Stitch fill
# 3. Batch-export via the Output extension / CLI:
inkscape --batch-process \
  --actions="org.inkstitch.output.dst" design.svg
# or open in Inkscape and use Extensions ▸ Ink/Stitch ▸ Embroidery file ▸ Export
```

This route gives you real `auto_fill` Eulerian routing, underlay, pull
compensation, lettering, and the realistic preview — everything in §4–6.

---

## 8. Recommended defaults cheat-sheet

| Use case | colours | row_spacing | angle | max_stitch | fill_method |
|----------|---------|-------------|-------|-----------|-------------|
| Flat logo | 3–6 | 0.4 mm | per region | 3.0 mm | auto_fill |
| Text/monogram | 1–3 | — | — | — | satin (lettering) |
| Photo / portrait | 8–15 | 0.35 mm + random | angle field | 3.0 mm | linear_gradient / guided |
| Pixel art | 4–12 | grid | — | — | cross_stitch |
| Organic (leaf/flower) | 3–8 | 0.4 mm | — | 3.0 mm | contour / circular |

---

## 9. Where AI adds the most value

- **Region segmentation** → cleaner masks than threshold/trace (Stage 2).
- **Colour/thread mapping** → perceptual ΔE matching to a real palette (Stage 2/5).
- **Per-region fill-type + angle field** → realism that hand-tuning can't scale (Stage 3).
- **Density/pull from fabric** → fewer test sew-outs (Stage 3).
- Let Ink/Stitch own routing, ties, trims, underlay, encoding (Stages 4–5) —
  those are solved mechanics, not perception problems.
