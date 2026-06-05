# Ink/Stitch — Coding & AI Integration Guide

A complete review of the Ink/Stitch codebase and a practical guide for integrating its
stitch‑generation pipeline into an AI system whose main focus is **image → embroidery
stitch generation**.

---

## 1. What Ink/Stitch Is

Ink/Stitch is an open‑source Inkscape extension (Python) that converts SVG vector
artwork — and, indirectly, raster images — into machine‑embroidery files
(DST, PES, EXP, JEF, VP3, XXX, HUS, VIP, CSV …). It is one of the most mature
open‑source embroidery toolchains and bundles:

- Sophisticated fill algorithms (auto‑fill, contour, guided, gradient, tartan, cross‑stitch, meander, circular)
- Satin column / stroke / running‑stitch / bean / zigzag / ripple generators
- A full stitch‑plan model with lock stitches, trims, jumps, color changes
- Export via **pystitch / pyembroidery** to 10+ commercial formats
- A "Cross Stitch Assistant" that converts raster images into pixelated cross‑stitch fills

Project type: Python 3, Inkscape 1.4+ extension. Geometry stack: **shapely 2 +
networkx + numpy + Pillow**. GUI stack (optional): wxPython.

---

## 2. Repository Layout

```
inkstitch.py                  # Entry point (Inkscape invokes this)
lib/
├── extensions/               # 47+ Inkscape extensions (UI workflows)
│   ├── base.py               # InkstitchExtension base class
│   ├── output.py             # Embroidery file export extension
│   ├── cross_stitch_assistant.py   # Raster → cross‑stitch fills
│   ├── lettering.py          # Text → satin/fill paths
│   └── utils/bitmap_to_cross_stitch.py  # Image pixelation engine
├── elements/                 # SVG‑node wrappers w/ stitch behavior
│   ├── element.py            # EmbroideryElement base + .embroider()
│   ├── fill_stitch.py        # FillStitch (auto_fill, contour, …)
│   ├── stroke.py             # Stroke (running, bean, zigzag, ripple)
│   ├── satin_column.py       # SatinColumn
│   ├── image.py              # ImageObject (no direct stitches)
│   └── clone.py / text.py    # Inkscape <use>/<text> wrappers
├── stitches/                 # PURE‑Python stitch algorithms (reusable!)
│   ├── auto_fill.py          # Eulerian‑routed row fill (core)
│   ├── contour_fill.py       # Concentric offsets
│   ├── guided_fill.py        # Rows follow guide line
│   ├── running_stitch.py     # running / bean / zigzag / ripple
│   ├── cross_stitch.py       # Grid cross stitch
│   ├── auto_satin.py         # Auto satin rails from path
│   └── tartan_fill.py / meander_fill.py / circular_fill.py / …
├── stitch_plan/              # Data model
│   ├── stitch.py             # Stitch (Point + flags)
│   ├── stitch_group.py       # StitchGroup
│   ├── color_block.py        # ColorBlock
│   └── stitch_plan.py        # StitchPlan + stitch_groups_to_stitch_plan()
├── output.py                 # write_embroidery_file()  (pystitch wrapper)
├── svg/                      # SVG/path/transform helpers (inkex‑coupled)
├── threads.py                # ThreadCatalog / ThreadColor (palette match)
├── commands.py               # Trim/stop/origin markers
├── metadata.py               # InkStitchMetadata (collapse_len, palette, …)
└── gui/                      # wxPython UI (simulator, lettering editor, …)
```

Key external deps (`requirements.txt`): `pystitch`, `inkex`, `shapely>=2`,
`networkx`, `numpy`, `Pillow`, `wxPython>=4.1.1`, `trimesh>=3.15.2`, `fonttools`, `lxml`.

---

## 3. Execution Flow (How a Run Actually Happens)

```
Inkscape  →  inkstitch.py main()
              │  parses --extension=<name>
              │  dynamically imports lib.extensions.<Name>
              ▼
       InkstitchExtension subclass.run(args)
              │  load SVG  →  inkex Document
              │  get_elements()  →  List[EmbroideryElement]
              │      (FillStitch, Stroke, SatinColumn, …)
              │  elements_to_stitch_groups(elements)
              │      → for each element: element.embroider(...)
              │      → returns List[StitchGroup]
              │  stitch_groups_to_stitch_plan(...)
              │      → StitchPlan (color blocks, locks, trims, jumps)
              ▼
       write_embroidery_file(path, plan, svg)
              → pystitch.EmbPattern → DST/PES/EXP/…
```

Important file pointers:

| Stage | File | Symbol |
|-------|------|--------|
| Entry | `inkstitch.py` | `main()` lines 129–166 |
| Base ext | `lib/extensions/base.py` | `InkstitchExtension` 18–107 |
| Element loop | `lib/elements/element.py` | `EmbroideryElement.embroider()` ~720 |
| Plan build | `lib/stitch_plan/stitch_plan.py` | `stitch_groups_to_stitch_plan()` 18–117 |
| Export | `lib/output.py` | `write_embroidery_file()` 53–130 |

---

## 4. Image → Embroidery — How Ink/Stitch Actually Does It

This is the part most relevant to your AI system. Ink/Stitch supports **two
distinct image paths**:

### Path A — Image → Cross‑Stitch (built‑in)

`lib/extensions/cross_stitch_assistant.py` + `lib/extensions/utils/bitmap_to_cross_stitch.py`.

```python
class BitmapToCrossStitch:
    def __init__(self, svg, bitmap, settings, palette=None):
        image = self._get_image_byte_string(bitmap.node)   # base64 or path
        self.original_image = Image.open(image).convert("RGBA")
        self.original_image = self.apply_transform(self.original_image)
        self.apply_color_corrections(image)
        # → emits <path> elements with inkstitch:fill_method=cross_stitch
```

What it does:
1. Reads the raster (embedded base64 or linked).
2. Applies SVG transform, brightness / contrast / saturation, alpha threshold.
3. **Quantizes the palette** (k‑means / threshold to N colors), optionally matching a thread palette.
4. Emits one SVG `<path>` *per color region* and tags it with cross‑stitch fill metadata.
5. Down‑stream, `FillStitch` + `lib/stitches/cross_stitch.py` turn those regions into a grid of X stitches.

Method options: `simple_cross`, `quarter_cross`, `half_cross`, `three_quarter_cross`.

### Path B — Image → Vector → Fill (recommended for "embroidery look")

Ink/Stitch itself does **not** auto‑trace. The expected flow is:
1. **Trace bitmap** (Inkscape's potrace, or `autotrace` / `vtracer` externally) → SVG paths.
2. Assign each region a fill color.
3. Run **FillStitch.auto_fill** to convert each region to dense satin rows.
4. Optionally generate **satin columns** for outlines via `auto_satin`.

`lib/elements/image.py` (`ImageObject.to_stitch_groups()` returns `[]`) explicitly
warns the user that raw `<image>` nodes don't stitch — they must be traced or
sent through the Cross Stitch Assistant first.

---

## 5. The Core Stitch Algorithms (Reusable)

All of these are **pure Python** (shapely + networkx + numpy) and have **no
Inkscape dependency**, which makes them the most attractive piece to embed in an
AI system.

### 5.1 auto_fill — `lib/stitches/auto_fill.py`

The flagship fill algorithm.

```python
def auto_fill(shape, angle, row_spacing, end_row_spacing, max_stitch_length,
              running_stitch_length, running_stitch_tolerance, staggers,
              skip_last, starting_point, ending_point=None, underpath=True,
              gap_fill_rows=0, enable_random_stitch_length=False,
              random_sigma=0.0, random_seed="",
              pull_compensation_px=(0, 0), pull_compensation_percent=(0, 0))
              -> list[Stitch]
```

Pipeline:
1. Pull‑compensate the shape (shapely buffer).
2. Intersect with a grating of parallel lines at `angle`, `row_spacing` apart.
3. Build two networkx graphs: `fill_stitch_graph` (row segments + boundary edges) and `travel_graph` (under‑path routes between rows).
4. Solve an **Eulerian path** to minimize jumps / thread breaks.
5. Sample stitches along the path at `max_stitch_length`, with stagger pattern.

This is what gives Ink/Stitch its "single continuous thread" embroidery feel.

### 5.2 contour_fill / guided_fill / cross_stitch

- `contour_fill(shape, …)` — successive `polygon.buffer(-d)` rings.
- `guided_fill(shape, guide_line, …)` — rows follow a user‑drawn stroke.
- `cross_stitch(shape, grid_size, palette, …)` — pixel‑grid X stitches; used by Path A.

### 5.3 running_stitch — `lib/stitches/running_stitch.py`

Generates point sequences along a path with `even_running_stitch`,
`bean_stitch` (back‑and‑forth for thickness), `zigzag_stitch`, `ripple_stitch`.

### 5.4 auto_satin — `lib/stitches/auto_satin.py`

Auto‑generates a pair of satin rails from a closed boundary — useful when an AI
produces outline strokes only.

---

## 6. The Stitch Data Model

```python
# lib/stitch_plan/stitch.py
class Stitch(Point):
    x: float
    y: float
    color: Any
    jump: bool          # needle up
    stop: bool          # machine stop
    trim: bool          # cut thread
    color_change: bool
    min_stitch_length: float | None
    tags: set[str]
```

```python
# lib/stitch_plan/stitch_group.py
class StitchGroup:
    color: Any
    stitches: list[Stitch]
    trim_after: bool
    stop_after: bool
    lock_stitches: tuple[LockStitch, LockStitch]
    force_lock_stitches: bool
```

```python
# lib/stitch_plan/stitch_plan.py
class StitchPlan:
    color_blocks: list[ColorBlock]
    # derived
    num_stitches, num_colors, num_jumps, num_trims
    bounding_box, estimated_thread  # meters
```

Aggregation:

```python
stitch_plan = stitch_groups_to_stitch_plan(
    stitch_groups,
    collapse_len=3.0,       # mm — jumps shorter than this become stitches
    min_stitch_len=0.1,     # mm — filter dupes
    disable_ties=False,     # whether to insert lock stitches
)
```

Export:

```python
write_embroidery_file("out.dst", stitch_plan, svg_root,
                      settings={"scale": (1.0, 1.0), "translate": (0, 0)})
```

Internally calls `pystitch.EmbPattern` and maps each `Stitch` flag to
`JUMP / NEEDLE_AT / TRIM / COLOR_CHANGE / STOP`.

---

## 7. What Is and Isn't Reusable Outside Inkscape

| Module | Coupling | Reusable? |
|--------|----------|-----------|
| `lib/stitches/*` | shapely + networkx only | ✅ Drop‑in |
| `lib/stitch_plan/*` | pure Python | ✅ Drop‑in |
| `lib/output.py` | pystitch + uses `svg` for transform | ⚠️ Reusable if you fake a minimal `svg` root or pass `None`‑safe settings |
| `lib/threads.py` | pure Python | ✅ Drop‑in |
| `lib/elements/*` | wraps `inkex` SVG nodes | ❌ Needs inkex |
| `lib/extensions/*` | subclasses `inkex.EffectExtension` | ❌ Needs inkex/Inkscape |
| `lib/svg/*` | inkex + lxml | ❌ Mostly Inkscape‑specific |
| `lib/gui/*` | wxPython | ❌ UI only |

**Bottom line:** you can use Ink/Stitch's stitch algorithms + plan + export
without Inkscape, but you'll **lose the SVG → element pipeline** and need to
build polygons yourself (e.g. via shapely from your traced paths).

---

## 8. Integrating Into Your AI Image‑to‑Embroidery System

### 8.1 Recommended overall architecture

```
┌──────────────┐   ┌──────────────────┐   ┌────────────────────┐   ┌────────────────┐
│  Input image │ → │ AI segmentation/  │ → │ Vectorize (potrace │ → │ Ink/Stitch     │
│  (PNG/JPG)   │   │ palette reduction │   │ / vtracer / SVG)   │   │ stitch engine  │
└──────────────┘   └──────────────────┘   └────────────────────┘   └────────┬───────┘
                                                                            ▼
                                                            DST / PES / EXP / JEF…
```

The AI portion handles the **perceptual** problem (what to embroider, which
colors, which regions deserve satin vs fill vs running stitch). Ink/Stitch
handles the **mechanical** problem (path planning, density, pull comp, ties,
jumps). This division mirrors how human digitizers work.

### 8.2 Two integration modes

**Mode 1 — "Headless Ink/Stitch" (recommended).**
Use only `lib/stitches`, `lib/stitch_plan`, `lib/output`. Feed shapely polygons
straight in.

```python
# pip install shapely networkx numpy pystitch pillow
from shapely.geometry import Polygon
from lib.stitches.auto_fill import auto_fill
from lib.stitch_plan import StitchGroup, stitch_groups_to_stitch_plan
from lib.output import write_embroidery_file

# 1) AI gives you: list of (polygon, rgb) regions, all in mm
regions = ai_segment_and_vectorize("input.png")   # your code

stitch_groups = []
for polygon, rgb in regions:
    stitches = auto_fill(
        shape=polygon,
        angle=0.0,                   # radians; the AI can pick per region
        row_spacing=0.4,             # mm  — density
        end_row_spacing=None,
        max_stitch_length=3.0,       # mm
        running_stitch_length=1.5,
        running_stitch_tolerance=0.2,
        staggers=4,
        skip_last=False,
        starting_point=polygon.representative_point().coords[0],
        ending_point=None,
        underpath=True,
    )
    stitch_groups.append(StitchGroup(color=rgb, stitches=stitches,
                                     trim_after=True))

plan = stitch_groups_to_stitch_plan(stitch_groups,
                                    collapse_len=3.0, min_stitch_len=0.1)

write_embroidery_file("out.dst", plan, svg=None,
                      settings={"scale": (1, 1), "translate": (0, 0)})
```

You may need to stub one or two `svg`‑sniffing lines in `lib/output.py`
(reading page size / rotate‑on‑export from metadata); easiest is to pass a
minimal `lxml` SVG root with a `<svg width=… height=…>`.

**Mode 2 — "Full Inkscape pipeline as a subprocess".**
Let the AI emit an SVG, then shell out:

```
inkscape --actions="export-filename:out.dst;export-do" input.svg
# or invoke the Output extension directly via:
python inkstitch.py --extension=output --format=dst --file=out.svg
```

Heavier, but gets you *everything* (lettering, satin, gradient fills, the GUI
simulator). Good if your AI's output is naturally SVG.

### 8.3 What the AI Should Decide vs What Ink/Stitch Decides

| Decision | Best done by AI | Best done by Ink/Stitch |
|----------|----------------|--------------------------|
| Which regions to stitch | ✅ | |
| Color quantization & thread match | ✅ (then `ThreadCatalog.match_and_apply_palette`) | |
| Per‑region fill *type* (auto_fill / satin / running) | ✅ | |
| Stitch angle per region (follow contour / texture) | ✅ | |
| Density / row spacing (fabric‑aware) | ✅ | |
| Path planning / Eulerian routing | | ✅ |
| Pull compensation | | ✅ |
| Lock / tie‑in / trims / jumps | | ✅ |
| Export format encoding | | ✅ (pystitch) |

### 8.4 Concrete AI hooks worth building

1. **Region classifier** → outputs `{auto_fill, satin_column, running_stitch}` per polygon, plus an angle (so satin follows shape direction, fills follow contour).
2. **Angle field predictor** — diffusion model that paints a per‑pixel stitch‑angle field; sample it per region to feed `auto_fill(angle=…)` or use `guided_fill` with a generated guide line.
3. **Thread‑palette mapper** — embed Madeira/Isacord palettes (Ink/Stitch ships them under `palettes/`); match by ΔE in Lab.
4. **Vectorizer choice** — Ink/Stitch does not include one; pair with `vtracer` (fast, color) or `potrace` (B/W, very clean).
5. **Quality estimator** — `StitchPlan` exposes `num_stitches`, `num_jumps`, `num_trims`, `estimated_thread` — use as RL reward / loss for AI tuning.

### 8.5 Bringing it back to images directly

For the simplest end‑to‑end pipeline driven *only* by Ink/Stitch built‑ins:

```python
from lib.extensions.utils.bitmap_to_cross_stitch import BitmapToCrossStitch
# requires an inkex.SvgDocumentElement + a bitmap node + settings dict
BitmapToCrossStitch(svg, bitmap_node, settings={
    "method": "simple_cross",
    "num_colors": 12,
    "row_spacing": 1.5,
    "brightness": 0,
    "contrast": 0,
    "transparency_threshold": 200,
}, palette=madeira_palette)
```

That writes pixelated cross‑stitch fill paths into the SVG, which then go
through the normal `FillStitch` → `cross_stitch` algorithm. Good baseline /
ground‑truth generator for your AI.

---

## 9. Practical Gotchas

- **Units.** Ink/Stitch internally works in *SVG user units* (≈ pixels at 96 dpi). pystitch outputs in 0.1 mm. The `scale` setting in `write_embroidery_file` does the conversion. If you feed mm‑native polygons, configure `scale` accordingly or pre‑transform.
- **Coordinate system.** Ink/Stitch matches SVG (y‑down). Embroidery formats are y‑up; pystitch handles the flip, but verify with a small test.
- **Pull compensation** (`pull_compensation_px`, `pull_compensation_percent`) compensates fabric distortion; expose this to your AI as a fabric‑aware parameter.
- **Underpath** (`underpath=True` in `auto_fill`) routes the needle *under* finished rows — required for clean travel; almost always leave it on.
- **Min stitch length** — embroidery machines bunch under ~0.5 mm. `stitch_groups_to_stitch_plan(min_stitch_len=0.4)` is a safer default than 0.1.
- **Trim / color change** — set `trim_after=True` between groups of different colors; the plan builder inserts the right machine commands.
- **Threading dependencies.** `shapely 2.x` is required; older 1.x will silently misbehave.
- **License.** Ink/Stitch is **GPL‑3.0**. Bundling it inside a closed‑source product requires care — running it as an out‑of‑process tool (Mode 2) is the safest pattern, or open‑source the embedding layer.

---

## 10. Suggested Adoption Plan for Your AI System

1. **Phase 0 — Headless baseline.** Vendor `lib/stitches`, `lib/stitch_plan`, `lib/output.py`, `lib/threads.py`. Wire `auto_fill` + `write_embroidery_file`. Validate on hand‑crafted shapely shapes against a known good DST.
2. **Phase 1 — AI segmentation.** Plug in your segmentation + vectorization model. Feed colored polygons. Use `StitchPlan.estimated_thread` and `num_jumps` as automated metrics.
3. **Phase 2 — AI angle field.** Predict per‑region stitch direction. Compare visual realism vs fixed‑angle baseline.
4. **Phase 3 — Fill‑type classifier.** Decide auto_fill vs satin vs running per region. Borrow `auto_satin` for boundary detection.
5. **Phase 4 — Thread‑palette matching.** Use `lib/threads.py` + the palettes shipped under `palettes/`.
6. **Phase 5 — Cross‑stitch path.** Use `BitmapToCrossStitch` as a free fallback / pixel‑art mode, and as a labeled‑data generator for training.
7. **Phase 6 — Quality loop.** Render previews via Ink/Stitch's realistic PNG renderer (`PngRealistic` extension) and feed back into a perceptual loss.

---

## 11. Quick Reference — Top File:Line Pointers

| Concern | Location |
|---------|----------|
| Entry point | `inkstitch.py` — `main` ~129 |
| Extension base | `lib/extensions/base.py:18` |
| Element → stitches | `lib/elements/element.py` — `embroider` ~720 |
| Fill main | `lib/elements/fill_stitch.py` — `to_stitch_groups` |
| Auto‑fill algorithm | `lib/stitches/auto_fill.py:74` — `auto_fill()` |
| Contour fill | `lib/stitches/contour_fill.py` |
| Guided fill | `lib/stitches/guided_fill.py` |
| Cross stitch | `lib/stitches/cross_stitch.py` |
| Running / bean / zigzag | `lib/stitches/running_stitch.py` |
| Stitch dataclass | `lib/stitch_plan/stitch.py:16` |
| Plan builder | `lib/stitch_plan/stitch_plan.py:18` |
| File export | `lib/output.py:53` |
| Image → cross stitch | `lib/extensions/utils/bitmap_to_cross_stitch.py:29` |
| Image element | `lib/elements/image.py` |
| Threads / palettes | `lib/threads.py`, `palettes/` |
| Metadata | `lib/metadata.py` |

---

## 12. TL;DR

- Ink/Stitch's **stitch generation core** (`lib/stitches/` + `lib/stitch_plan/` + `lib/output.py`) is pure Python and **drop‑in usable** by any AI system that can produce colored shapely polygons.
- **It does not auto‑vectorize images** — you pair it with potrace/vtracer or your own segmentation model. The built‑in `BitmapToCrossStitch` covers the pixelated cross‑stitch case.
- For an AI image‑to‑embroidery product, the highest‑leverage integration is: AI = segmentation + colors + fill‑type + angle field; Ink/Stitch = routing + density + ties + export.
- Mind the **GPL‑3.0** license when deciding between in‑process embedding and out‑of‑process invocation.
