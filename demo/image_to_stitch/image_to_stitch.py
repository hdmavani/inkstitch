#!/usr/bin/env python3
"""
image_to_stitch.py
==================

A SELF-CONTAINED, RUNNABLE demo of multi-colour image -> embroidery stitch
generation. It is modelled on the Ink/Stitch pipeline but depends only on
pip-installable packages so it runs anywhere:

    pip install pystitch shapely numpy pillow

What it demonstrates (the same conceptual pipeline Ink/Stitch uses):

    image  ->  colour quantization (N thread colours)
           ->  per-colour binary mask
           ->  mask -> polygons (shapely)
           ->  angled scan-line "auto fill" -> ordered stitch points
           ->  pystitch EmbPattern with colour-changes / jumps / trims
           ->  .DST  (+ a .PNG preview of the stitches)

NOTE on fidelity:
  Ink/Stitch's real `lib/stitches/auto_fill.py` builds a networkx graph and
  solves an Eulerian path for optimal, single-thread routing with under-pathing
  and pull compensation. This demo uses a simpler boustrophedon (back-and-forth)
  scan-line fill so it can run without Inkscape/inkex. The OPTIONS and the
  data-flow are intentionally the same, so what you learn here maps 1:1 onto the
  real engine. See the spec doc (TECH_SPEC_image_to_multicolor_stitch.md) for how
  to run the production engine through Inkscape.

Usage:
    python image_to_stitch.py                 # uses a generated sample image
    python image_to_stitch.py myphoto.png     # uses your own image
    python image_to_stitch.py myphoto.png --colors 6 --width-mm 100 \
           --row-spacing-mm 0.4 --angle 45 --format dst

Outputs (next to this script): out.dst, out.png
"""

import argparse
import math
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

import pystitch
from shapely.affinity import rotate
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.ops import unary_union

# --------------------------------------------------------------------------- #
#  Unit helpers.  Embroidery formats work in 1/10 mm. pystitch stores stitches
#  in those native units, so we convert mm -> tenths-of-mm everywhere.
# --------------------------------------------------------------------------- #
TENTHS_PER_MM = 10.0


def mm(value_mm):
    """millimetres -> embroidery units (tenths of a mm)."""
    return value_mm * TENTHS_PER_MM


# --------------------------------------------------------------------------- #
#  STEP 1 - load / generate an image and reduce it to N thread colours.
#  This mirrors what Ink/Stitch's Cross-Stitch Assistant does up front
#  (lib/extensions/utils/bitmap_to_cross_stitch.py): brightness/contrast,
#  then quantize to a small palette.
# --------------------------------------------------------------------------- #
def make_sample_image(size=256):
    """Generate a simple multi-colour test image so the demo runs with no input."""
    img = Image.new("RGB", (size, size), (245, 245, 245))
    d = ImageDraw.Draw(img)
    d.ellipse([30, 30, 150, 150], fill=(200, 40, 40))        # red circle
    d.rectangle([120, 120, 226, 226], fill=(40, 90, 200))     # blue square
    d.polygon([(180, 20), (240, 120), (130, 100)], fill=(30, 160, 60))  # green tri
    d.ellipse([60, 150, 130, 220], fill=(240, 200, 30))       # yellow circle
    return img


def quantize(image, num_colors):
    """Reduce the image to `num_colors` using an adaptive palette.

    Returns (label_array HxW of palette indices, list_of_rgb_tuples).
    """
    image = image.convert("RGB")
    pal_img = image.quantize(colors=num_colors, method=Image.FASTOCTREE, dither=Image.NONE)
    labels = np.array(pal_img)                       # H x W indices
    palette = pal_img.getpalette()[: num_colors * 3]
    colors = [tuple(palette[i * 3:i * 3 + 3]) for i in range(num_colors)]
    return labels, colors


# --------------------------------------------------------------------------- #
#  STEP 2 - turn one colour's mask into shapely polygons.
#  Ink/Stitch gets polygons from SVG paths; here we vectorise the pixel mask
#  by unioning one square per "on" pixel.  Coarse but dependency-free.
# --------------------------------------------------------------------------- #
def mask_to_polygons(mask, px_to_mm, simplify_mm=0.3, min_area_mm2=1.0):
    """Convert a boolean HxW mask into a (Multi)Polygon in millimetre space."""
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    squares = [
        Polygon([(x, y), (x + 1, y), (x + 1, y + 1), (x, y + 1)])
        for x, y in zip(xs.tolist(), ys.tolist())
    ]
    geom = unary_union(squares)                      # merge touching pixels
    # scale pixel-space -> mm-space
    from shapely.affinity import scale
    geom = scale(geom, xfact=px_to_mm, yfact=px_to_mm, origin=(0, 0))
    geom = geom.simplify(simplify_mm, preserve_topology=True)
    geom = geom.buffer(0)                            # repair self-intersections
    # drop slivers
    polys = list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]
    polys = [p for p in polys if p.area >= min_area_mm2]
    if not polys:
        return None
    return MultiPolygon(polys) if len(polys) > 1 else polys[0]


# --------------------------------------------------------------------------- #
#  STEP 3 - the FILL itself: angled scan-line / boustrophedon fill.
#  This is the demo's analogue of lib/stitches/auto_fill.py.
#  Options mirror Ink/Stitch fill params (angle, row_spacing, max_stitch_length).
# --------------------------------------------------------------------------- #
def fill_polygon(polygon, angle_deg, row_spacing_mm, max_stitch_length_mm):
    """Return a list of (x_mm, y_mm) stitch points filling `polygon`.

    angle_deg            row direction (0 = horizontal, like Ink/Stitch).
    row_spacing_mm       distance between rows (fill density).
    max_stitch_length_mm longest single stitch; longer spans are subdivided.
    """
    # Rotate the shape so rows become horizontal, scan, then rotate points back.
    cx, cy = polygon.centroid.x, polygon.centroid.y
    rotated = rotate(polygon, -angle_deg, origin=(cx, cy), use_radians=False)
    minx, miny, maxx, maxy = rotated.bounds

    rows = []
    y = miny + row_spacing_mm / 2.0
    while y <= maxy:
        scan = LineString([(minx - 1, y), (maxx + 1, y)])
        inter = rotated.intersection(scan)
        segments = []
        if inter.is_empty:
            pass
        elif inter.geom_type == "LineString":
            segments = [inter]
        elif inter.geom_type == "MultiLineString":
            segments = list(inter.geoms)
        # sort left-to-right so boustrophedon ordering is clean
        segments.sort(key=lambda s: s.coords[0][0])
        rows.append((y, segments))
        y += row_spacing_mm

    # Walk the rows alternating direction (boustrophedon) to minimise jumps.
    points = []
    flip = False
    for y, segments in rows:
        ordered = list(reversed(segments)) if flip else segments
        for seg in ordered:
            (x0, _), (x1, _) = seg.coords[0], seg.coords[-1]
            if flip:
                x0, x1 = x1, x0
            points.extend(_subdivide(x0, x1, y, max_stitch_length_mm))
        flip = not flip

    # rotate the flat point list back into the original orientation
    import shapely
    if not points:
        return []
    line = LineString(points) if len(points) > 1 else None
    if line is None:
        return points
    line = rotate(line, angle_deg, origin=(cx, cy), use_radians=False)
    return [(float(x), float(y)) for x, y in line.coords]


def _subdivide(x0, x1, y, max_len):
    """Break a horizontal span [x0,x1] at row y into <= max_len steps."""
    length = abs(x1 - x0)
    n = max(1, int(math.ceil(length / max_len)))
    return [(x0 + (x1 - x0) * i / n, y) for i in range(n + 1)]


# --------------------------------------------------------------------------- #
#  STEP 4 - assemble the pystitch pattern (colour blocks, jumps, trims).
#  This is exactly the model lib/output.py uses: one thread per colour block,
#  NEEDLE_AT for real stitches, JUMP to travel, TRIM + COLOR_CHANGE between
#  colours.
# --------------------------------------------------------------------------- #
def build_pattern(color_blocks, name="out"):
    """color_blocks: list of (rgb_tuple, [ (x_mm, y_mm), ... ]).

    Returns a pystitch.EmbPattern in native (tenths-of-mm) units.
    """
    pattern = pystitch.EmbPattern()
    pattern.extras["name"] = name

    last_x = last_y = 0.0
    for block_index, (rgb, points) in enumerate(color_blocks):
        if not points:
            continue
        thread = pystitch.EmbThread()
        thread.set_color(*rgb)
        pattern.add_thread(thread)

        # jump from wherever we were to the first stitch of this block
        fx, fy = points[0]
        pattern.add_stitch_absolute(pystitch.JUMP, mm(fx), mm(fy))

        for (x, y) in points:
            pattern.add_stitch_absolute(pystitch.NEEDLE_AT, mm(x), mm(y))
            last_x, last_y = x, y

        # trim + colour change before the next colour (not after the last block)
        if block_index < len(color_blocks) - 1:
            pattern.add_stitch_absolute(pystitch.TRIM, mm(last_x), mm(last_y))
            pattern.add_stitch_absolute(pystitch.COLOR_CHANGE, mm(last_x), mm(last_y))

    pattern.add_stitch_absolute(pystitch.END, mm(last_x), mm(last_y))
    return pattern


# --------------------------------------------------------------------------- #
#  STEP 5 - a quick PNG preview of the generated stitches.
# --------------------------------------------------------------------------- #
def render_preview(color_blocks, width_mm, height_mm, path, px_per_mm=4):
    W = max(1, int(width_mm * px_per_mm))
    H = max(1, int(height_mm * px_per_mm))
    img = Image.new("RGB", (W, H), (250, 250, 250))
    d = ImageDraw.Draw(img)
    for rgb, points in color_blocks:
        prev = None
        for (x, y) in points:
            p = (x * px_per_mm, y * px_per_mm)
            if prev is not None:
                d.line([prev, p], fill=rgb, width=1)
            prev = p
    img.save(path)


# --------------------------------------------------------------------------- #
#  Driver.
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Multi-colour image -> embroidery demo")
    ap.add_argument("image", nargs="?", help="input image (PNG/JPG). Omit for a sample.")
    ap.add_argument("--colors", type=int, default=5, help="number of thread colours")
    ap.add_argument("--width-mm", type=float, default=80.0, help="design width in mm")
    ap.add_argument("--row-spacing-mm", type=float, default=0.4,
                    help="fill density: distance between rows (0.3-0.5 typical)")
    ap.add_argument("--max-stitch-mm", type=float, default=3.0, help="max stitch length")
    ap.add_argument("--angle", type=float, default=0.0, help="fill angle in degrees")
    ap.add_argument("--format", default="dst", help="output format: dst, pes, exp, jef, vp3, csv")
    ap.add_argument("--outdir", default=os.path.dirname(os.path.abspath(__file__)))
    args = ap.parse_args()

    # 1. load + quantize
    if args.image:
        image = Image.open(args.image)
    else:
        image = make_sample_image()
        print("No image given - using a generated sample.")
    labels, colors = quantize(image, args.colors)
    H, W = labels.shape
    px_to_mm = args.width_mm / W
    height_mm = H * px_to_mm
    print(f"Image {W}x{H}px -> {args.width_mm:.0f}x{height_mm:.0f} mm, {args.colors} colours")

    # 2-3. per colour: mask -> polygons -> fill
    color_blocks = []
    # paint darker colours first (rough heuristic for layering)
    order = sorted(range(len(colors)), key=lambda i: sum(colors[i]))
    for idx in order:
        rgb = colors[idx]
        mask = labels == idx
        if mask.sum() < 20:
            continue
        geom = mask_to_polygons(mask, px_to_mm)
        if geom is None:
            continue
        polys = list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]
        pts = []
        for poly in polys:
            pts.extend(fill_polygon(poly, args.angle, args.row_spacing_mm, args.max_stitch_mm))
        if pts:
            color_blocks.append((rgb, pts))
            print(f"  colour {rgb}: {len(pts)} stitches")

    if not color_blocks:
        print("No stitchable regions found.")
        sys.exit(1)

    # 4. build + write embroidery file
    pattern = build_pattern(color_blocks, name="out")
    out_emb = os.path.join(args.outdir, f"out.{args.format}")
    pystitch.write(pattern, out_emb)
    total = sum(len(p) for _, p in color_blocks)
    print(f"\nWrote {out_emb}  ({total} stitches, {len(color_blocks)} colours)")

    # 5. preview
    out_png = os.path.join(args.outdir, "out.png")
    render_preview(color_blocks, args.width_mm, height_mm, out_png)
    print(f"Wrote preview {out_png}")


if __name__ == "__main__":
    main()
