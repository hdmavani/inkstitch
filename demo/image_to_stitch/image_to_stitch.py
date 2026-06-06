#!/usr/bin/env python3
"""
image_to_stitch.py
==================

A SELF-CONTAINED, RUNNABLE demo of multi-colour image -> embroidery stitch
generation. Models the Ink/Stitch pipeline but depends only on pip-installable
packages:

    pip install pystitch shapely numpy pillow

Pipeline:
    image
      -> background removal           (--remove-bg / --keep-bg / --bg-color)
      -> colour quantization          (--colors)
      -> per-colour mask              (alpha- or label-based)
      -> mask -> polygons (shapely)
      -> angled scan-line fill -> stitch RUNS per polygon
      -> pystitch pattern (colour blocks + JUMP between runs + TRIM/COLOR_CHANGE)
      -> .dst / .pes / ...  + .png preview

Notes on fidelity:
  Ink/Stitch's real auto_fill (lib/stitches/auto_fill.py) builds a networkx
  graph and solves an Eulerian path for optimal routing with under-pathing
  and pull compensation. This demo uses a simpler boustrophedon scan-line fill
  per polygon so it can run without Inkscape/inkex. The options are the same
  as the production engine -- see TECH_SPEC_image_to_multicolor_stitch.md.

Usage:
    python image_to_stitch.py                              # sample image
    python image_to_stitch.py photo.png --colors 4         # auto bg removal
    python image_to_stitch.py photo.png --bg-color 255,255,255
    python image_to_stitch.py photo.png --keep-bg          # stitch background too
    python image_to_stitch.py photo.png --colors 6 --width-mm 100 \
           --row-spacing-mm 0.4 --angle 45 --format pes
"""

import argparse
import math
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

import pystitch
from shapely.affinity import rotate, scale as shp_scale
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.ops import unary_union


TENTHS_PER_MM = 10.0


def mm(value_mm):
    """millimetres -> embroidery units (tenths of a mm)."""
    return value_mm * TENTHS_PER_MM


# --------------------------------------------------------------------------- #
#  STEP 0 - sample generator (so the demo runs with no input file).
# --------------------------------------------------------------------------- #
def make_sample_image(size=256):
    img = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    d = ImageDraw.Draw(img)
    d.ellipse([30, 30, 150, 150], fill=(200, 40, 40, 255))
    d.rectangle([120, 120, 226, 226], fill=(40, 90, 200, 255))
    d.polygon([(180, 20), (240, 120), (130, 100)], fill=(30, 160, 60, 255))
    d.ellipse([60, 150, 130, 220], fill=(240, 200, 30, 255))
    return img


# --------------------------------------------------------------------------- #
#  STEP 1 - background detection / removal.
#
#  Two ways something can be "background":
#    a) the image already has an alpha channel and the BG is transparent;
#    b) the image is opaque and the BG is a flat colour (white most of the time).
#
#  We compute a foreground MASK (HxW bool) up front, so the quantizer only
#  ever sees the subject. This is what stops the entire canvas being filled
#  with stitches.
# --------------------------------------------------------------------------- #
def compute_foreground_mask(image, mode, bg_color, alpha_threshold, tolerance):
    """Return (rgb_image, fg_mask_bool_HW). 'mode' = auto|on|off|color."""
    has_alpha = image.mode in ("RGBA", "LA") or (
        image.mode == "P" and "transparency" in image.info
    )
    rgba = image.convert("RGBA")
    arr = np.array(rgba)
    H, W = arr.shape[:2]
    rgb = arr[..., :3]
    alpha = arr[..., 3]

    if mode == "off":
        return Image.fromarray(rgb), np.ones((H, W), dtype=bool)

    if mode == "color" and bg_color is not None:
        bg = np.array(bg_color, dtype=np.int16)
        diff = np.abs(rgb.astype(np.int16) - bg).max(axis=-1)
        fg = diff > tolerance
        return Image.fromarray(rgb), fg

    if mode in ("auto", "on"):
        # Prefer alpha if present.
        if has_alpha and alpha.min() < 255:
            fg = alpha > alpha_threshold
            return Image.fromarray(rgb), fg

        # Otherwise: assume background = dominant corner colour.
        corners = np.stack([rgb[0, 0], rgb[0, -1], rgb[-1, 0], rgb[-1, -1]])
        # median of the 4 corners is robust to one anti-aliased corner
        bg = np.median(corners, axis=0).astype(np.int16)
        diff = np.abs(rgb.astype(np.int16) - bg).max(axis=-1)
        fg = diff > tolerance
        # If "auto" and the would-be background is < 5% of pixels, that's not
        # really a background -- skip removal.
        if mode == "auto" and (~fg).mean() < 0.05:
            return Image.fromarray(rgb), np.ones((H, W), dtype=bool)
        print(f"  auto-detected background colour ~= {tuple(int(c) for c in bg)} "
              f"({(~fg).mean()*100:.0f}% of pixels removed)")
        return Image.fromarray(rgb), fg

    return Image.fromarray(rgb), np.ones((H, W), dtype=bool)


# --------------------------------------------------------------------------- #
#  STEP 2 - quantize FOREGROUND pixels to N thread colours.
# --------------------------------------------------------------------------- #
def quantize(image, fg_mask, num_colors):
    """Reduce foreground pixels to `num_colors`.

    Returns (labels HxW int16, list_of_rgb_tuples). Background pixels get
    label = -1.
    """
    image = image.convert("RGB")
    pal_img = image.quantize(colors=num_colors, method=Image.FASTOCTREE, dither=Image.NONE)
    labels = np.array(pal_img).astype(np.int16)
    palette = pal_img.getpalette()[: num_colors * 3]
    colors = [tuple(palette[i * 3:i * 3 + 3]) for i in range(num_colors)]
    labels[~fg_mask] = -1     # mark background as "no thread"
    return labels, colors


# --------------------------------------------------------------------------- #
#  STEP 3 - mask -> polygons in millimetre space.
# --------------------------------------------------------------------------- #
def mask_to_polygons(mask, px_to_mm, simplify_mm=0.3, min_area_mm2=1.0):
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return []
    squares = [
        Polygon([(x, y), (x + 1, y), (x + 1, y + 1), (x, y + 1)])
        for x, y in zip(xs.tolist(), ys.tolist())
    ]
    geom = unary_union(squares)
    geom = shp_scale(geom, xfact=px_to_mm, yfact=px_to_mm, origin=(0, 0))
    geom = geom.simplify(simplify_mm, preserve_topology=True).buffer(0)
    polys = list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]
    return [p for p in polys if p.area >= min_area_mm2]


# --------------------------------------------------------------------------- #
#  STEP 4 - angled scan-line fill. Now returns a list of "runs", where each
#  run is a contiguous sequence of stitch points. One polygon -> one run
#  (in this demo). This lets the caller insert JUMPs between runs instead
#  of drawing fake stitch lines across the design.
# --------------------------------------------------------------------------- #
def fill_polygon(polygon, angle_deg, row_spacing_mm, max_stitch_length_mm):
    cx, cy = polygon.centroid.x, polygon.centroid.y
    rotated = rotate(polygon, -angle_deg, origin=(cx, cy), use_radians=False)
    minx, miny, maxx, maxy = rotated.bounds

    rows = []
    y = miny + row_spacing_mm / 2.0
    while y <= maxy:
        scan = LineString([(minx - 1, y), (maxx + 1, y)])
        inter = rotated.intersection(scan)
        if inter.is_empty:
            segments = []
        elif inter.geom_type == "LineString":
            segments = [inter]
        elif inter.geom_type == "MultiLineString":
            segments = list(inter.geoms)
        else:
            segments = []
        segments.sort(key=lambda s: s.coords[0][0])
        rows.append(segments)
        y += row_spacing_mm

    points = []
    flip = False
    for segments in rows:
        ordered = list(reversed(segments)) if flip else segments
        for seg in ordered:
            (x0, _), (x1, _) = seg.coords[0], seg.coords[-1]
            if flip:
                x0, x1 = x1, x0
            points.extend(_subdivide(x0, x1, segments[0].coords[0][1] if False else seg.coords[0][1],
                                     max_stitch_length_mm))
        flip = not flip

    if len(points) < 2:
        return []
    line = rotate(LineString(points), angle_deg, origin=(cx, cy), use_radians=False)
    return [(float(x), float(y)) for x, y in line.coords]


def _subdivide(x0, x1, y, max_len):
    length = abs(x1 - x0)
    n = max(1, int(math.ceil(length / max_len)))
    return [(x0 + (x1 - x0) * i / n, y) for i in range(n + 1)]


# --------------------------------------------------------------------------- #
#  STEP 5 - assemble the pystitch pattern.
#  color_blocks is now: list of (rgb, list_of_runs), each run = list of points.
# --------------------------------------------------------------------------- #
def build_pattern(color_blocks, name="out"):
    pattern = pystitch.EmbPattern()
    pattern.extras["name"] = name
    last_x = last_y = 0.0

    for block_index, (rgb, runs) in enumerate(color_blocks):
        runs = [r for r in runs if len(r) >= 2]
        if not runs:
            continue
        thread = pystitch.EmbThread()
        thread.set_color(*rgb)
        pattern.add_thread(thread)

        for run_index, run in enumerate(runs):
            fx, fy = run[0]
            pattern.add_stitch_absolute(pystitch.JUMP, mm(fx), mm(fy))
            for (x, y) in run:
                pattern.add_stitch_absolute(pystitch.NEEDLE_AT, mm(x), mm(y))
                last_x, last_y = x, y

        if block_index < len(color_blocks) - 1:
            pattern.add_stitch_absolute(pystitch.TRIM, mm(last_x), mm(last_y))
            pattern.add_stitch_absolute(pystitch.COLOR_CHANGE, mm(last_x), mm(last_y))

    pattern.add_stitch_absolute(pystitch.END, mm(last_x), mm(last_y))
    return pattern


# --------------------------------------------------------------------------- #
#  STEP 6 - PNG preview. Only draws WITHIN a run (no fake jump lines).
# --------------------------------------------------------------------------- #
def render_preview(color_blocks, width_mm, height_mm, path, px_per_mm=6):
    # px_per_mm + line width chosen so adjacent rows visually merge into a
    # solid colour fill at typical row_spacing (0.3-0.5 mm).
    W = max(1, int(width_mm * px_per_mm))
    H = max(1, int(height_mm * px_per_mm))
    img = Image.new("RGB", (W, H), (250, 250, 250))
    d = ImageDraw.Draw(img)
    line_w = max(2, int(px_per_mm * 0.45))
    for rgb, runs in color_blocks:
        for run in runs:
            if len(run) < 2:
                continue
            pts = [(x * px_per_mm, y * px_per_mm) for (x, y) in run]
            d.line(pts, fill=rgb, width=line_w)
    img.save(path)


# --------------------------------------------------------------------------- #
#  Driver.
# --------------------------------------------------------------------------- #
def parse_color(s):
    if s is None:
        return None
    parts = [int(p) for p in s.split(",")]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("expected R,G,B")
    return tuple(parts)


def main():
    ap = argparse.ArgumentParser(description="Multi-colour image -> embroidery demo")
    ap.add_argument("image", nargs="?")
    ap.add_argument("--colors", type=int, default=5)
    # PHYSICAL SIZE  --- this is what determines real-world stitch look.
    ap.add_argument("--width-mm", type=float, default=None,
                    help="design width on the fabric (mm). Default: 80 if neither w/h given.")
    ap.add_argument("--height-mm", type=float, default=None,
                    help="design height on the fabric (mm). If only one of w/h is given the "
                         "other is derived from image aspect ratio.")
    ap.add_argument("--fit", choices=("contain", "stretch"), default="contain",
                    help="when BOTH width and height are given: 'contain' preserves aspect, "
                         "'stretch' fills the box.")
    # DENSITY -- two equivalent ways to set it.
    ap.add_argument("--row-spacing-mm", type=float, default=None,
                    help="gap between rows in mm (real-world stitch density). "
                         "Typical 0.30-0.50. Default: auto from design size.")
    ap.add_argument("--density", type=float, default=None,
                    help="alternative to --row-spacing-mm: rows per mm (e.g. 2.5 = 0.4mm spacing).")
    ap.add_argument("--max-stitch-mm", type=float, default=3.0,
                    help="longest single stitch in mm (2.5-3.5 typical).")
    ap.add_argument("--angle", type=float, default=0.0,
                    help="fill direction in degrees (0 = horizontal).")
    ap.add_argument("--format", default="dst",
                    help="dst, pes, exp, jef, vp3, csv")
    ap.add_argument("--outdir", default=os.path.dirname(os.path.abspath(__file__)))

    # background controls
    bg = ap.add_mutually_exclusive_group()
    bg.add_argument("--remove-bg", dest="bg_mode", action="store_const",
                    const="on", help="force background removal")
    bg.add_argument("--keep-bg", dest="bg_mode", action="store_const",
                    const="off", help="stitch the background too")
    ap.add_argument("--bg-color", type=parse_color, default=None,
                    help="explicit background colour as R,G,B (implies --remove-bg)")
    ap.add_argument("--bg-tolerance", type=int, default=18,
                    help="how close to bg-color counts as background (0-255)")
    ap.add_argument("--alpha-threshold", type=int, default=128,
                    help="alpha < this is treated as background")
    ap.add_argument("--min-region-mm2", type=float, default=2.0,
                    help="drop colour regions smaller than this")

    args = ap.parse_args()
    if args.bg_color is not None:
        bg_mode = "color"
    else:
        bg_mode = args.bg_mode or "auto"

    # 0. load
    if args.image:
        image = Image.open(args.image)
    else:
        image = make_sample_image()
        print("No image given - using a generated sample.")

    # 1. background mask
    print(f"Background mode: {bg_mode}")
    rgb_image, fg_mask = compute_foreground_mask(
        image, bg_mode, args.bg_color, args.alpha_threshold, args.bg_tolerance
    )

    # 2. quantize foreground only
    labels, colors = quantize(rgb_image, fg_mask, args.colors)
    H_px, W_px = labels.shape

    # ---- resolve physical size (width_mm, height_mm) ----
    aspect = W_px / H_px
    w_arg, h_arg = args.width_mm, args.height_mm
    if w_arg is None and h_arg is None:
        width_mm = 80.0
        height_mm = width_mm / aspect
    elif w_arg is not None and h_arg is None:
        width_mm = w_arg
        height_mm = width_mm / aspect
    elif w_arg is None and h_arg is not None:
        height_mm = h_arg
        width_mm = height_mm * aspect
    else:
        if args.fit == "stretch":
            width_mm, height_mm = w_arg, h_arg
        else:  # contain
            if w_arg / h_arg > aspect:
                height_mm = h_arg
                width_mm = h_arg * aspect
            else:
                width_mm = w_arg
                height_mm = w_arg / aspect

    px_to_mm_x = width_mm / W_px
    px_to_mm_y = height_mm / H_px
    # use the smaller of the two for the (uniform) polygon scaling so we don't
    # distort the design; non-uniform only matters in --fit stretch mode.
    if args.fit == "stretch" and w_arg is not None and h_arg is not None:
        px_to_mm = (px_to_mm_x + px_to_mm_y) / 2  # demo simplification
    else:
        px_to_mm = px_to_mm_x

    # ---- resolve density (row_spacing_mm) ----
    if args.density is not None and args.row_spacing_mm is not None:
        print("ERROR: pass either --row-spacing-mm or --density, not both.", file=sys.stderr)
        sys.exit(2)
    if args.density is not None:
        row_spacing_mm = 1.0 / args.density
    elif args.row_spacing_mm is not None:
        row_spacing_mm = args.row_spacing_mm
    else:
        # auto: pick a sensible density from the larger physical dimension.
        # Real embroidery is almost always 0.30-0.50 mm regardless of size,
        # but tiny designs benefit from finer rows, big designs from coarser.
        biggest = max(width_mm, height_mm)
        if biggest < 40:
            row_spacing_mm = 0.30
        elif biggest < 120:
            row_spacing_mm = 0.40
        else:
            row_spacing_mm = 0.50
        print(f"  auto row-spacing = {row_spacing_mm:.2f} mm "
              f"(density {1/row_spacing_mm:.2f} rows/mm)")

    est_rows = int(max(width_mm, height_mm) / row_spacing_mm)
    print(f"Image {W_px}x{H_px} px  ->  {width_mm:.1f} x {height_mm:.1f} mm "
          f"(fit={args.fit})")
    print(f"Density: row spacing {row_spacing_mm:.2f} mm "
          f"(~{est_rows} rows across), max stitch {args.max_stitch_mm:.1f} mm, "
          f"angle {args.angle}°")
    print(f"Foreground: {fg_mask.mean()*100:.0f}% of pixels, {args.colors} colours requested")

    # 3-4. per colour: polygons -> fill runs
    color_blocks = []
    # darker colours first (rough layering heuristic)
    order = sorted(range(len(colors)), key=lambda i: sum(colors[i]))
    for idx in order:
        rgb = colors[idx]
        mask = labels == idx
        if mask.sum() < 20:
            continue
        polys = mask_to_polygons(mask, px_to_mm, min_area_mm2=args.min_region_mm2)
        if not polys:
            continue
        runs = []
        for poly in polys:
            run = fill_polygon(poly, args.angle, row_spacing_mm, args.max_stitch_mm)
            if len(run) >= 2:
                runs.append(run)
        if runs:
            total_pts = sum(len(r) for r in runs)
            color_blocks.append((rgb, runs))
            print(f"  colour {rgb}: {len(runs)} regions, {total_pts} stitches")

    if not color_blocks:
        print("No stitchable regions found.")
        sys.exit(1)

    # 5. build + write
    pattern = build_pattern(color_blocks, name="out")
    out_emb = os.path.join(args.outdir, f"out.{args.format}")
    pystitch.write(pattern, out_emb)
    total = sum(len(r) for _, runs in color_blocks for r in runs)
    print(f"\nWrote {out_emb}  ({total} stitches, {len(color_blocks)} colours)")

    # 6. preview
    out_png = os.path.join(args.outdir, "out.png")
    render_preview(color_blocks, width_mm, height_mm, out_png)
    print(f"Wrote preview {out_png}")


if __name__ == "__main__":
    main()
