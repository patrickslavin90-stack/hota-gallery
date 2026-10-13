"""Build the outdoor stage (VOS) building model for the 3D view from the
architect's as-built drawing set (ARM / Topotek 1, "Architectural -
As-Builts.pdf", 82 A0 sheets at 1:100). Needs PyMuPDF, numpy and Pillow -
a dev-time tool like build_elevations.py, not something the Pi runs.

Sheets used (1-based page numbers in the PDF):
  42  TH-2000 ELEVATIONS - STAGE WALL: the unfolded west elevation of the
      Voronoi stone wall (north wing | stage | south wing), with the two
      "line of fold" corners, the stage FFL datum and the canopy seen
      front-on.
  26  TH-1010 ROOF PLAN: the wall's plan and the canopy's plan (ring,
      Voronoi glazing grid, legs and footings) in one frame.
  68  TH-3200 SECTIONS, section S1: the canopy's front-to-back profile.

Coordinates out: the Vectorworks LX plan's frame, so the model lines up
with the dots - mm, X across the stage (audience left negative), Y depth
(audience at -Y; the wings angle towards it), Z up from the stage floor
(RL 3.50). The fold corners land within ~0.1 m of where the dots' own
plan lines meet, which is the check that the frames agree.

Writes hota_gallery/static/vos-building.json and vos-wall.png.

    python tools/build_vos_model.py "C:/Users/you/Downloads/Architectural - As-Builts.pdf"
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
OUT_JSON = ROOT / "hota_gallery" / "static" / "vos-building.json"
OUT_TEX = ROOT / "hota_gallery" / "static" / "vos-wall.png"

MM_PER_PT = 25.4 / 72 * 100  # 1:100 sheets: 35.28 mm per PDF point

# Sheet 42 (elevation), in displayed page points.
ELEV_PAGE = 42
FOLD_X = (656.5, 1396.7)        # the two "line of fold" corners
STAGE_FFL_Y = 841.0             # stage floor line, RL 3.50
ELEV_CLIP = (60, 470, 2040, 880)
# Sheet 26 (roof plan).
PLAN_PAGE = 26
WALL_FRONT_Y = 583.1            # front face of the centre wall
WALL_CORNERS_X = (620.2, 1377.0)
CANOPY_COLOR = (0.75, 0.75, 0.75)
# Sheet 68 (sections), section S1.
SECT_PAGE = 68
SECT_FFL_Y = 550.3
SECT_WALL_X = 1157.0            # front face of the wall in S1
SECT_CLIP = (600, 60, 1260, 240)

WING_ANGLE = math.radians(35.5)  # from the dots' plan lines (and the roof plan's ~36 deg)
TEX_MM_PER_PX = 20
GLASS_DOORS = (829, 743.3, 1225, 841)  # glazed stage doors under the bi-fold panels, elevation page points


def key(p):
    r = lambda t: tuple(round(x, 2) for x in (t or ()))
    return (r(p.get("color")), round(p.get("width") or 0, 2), r(p.get("fill")))


def polylines(p, m, steps=6):
    """A drawing path as polylines in displayed page coordinates."""
    lines, cur = [], []

    def push(a, b):
        nonlocal cur
        if cur and math.dist(cur[-1], a) > 1e-3:
            lines.append(cur)
            cur = []
        if not cur:
            cur.append(a)
        cur.append(b)

    for it in p["items"]:
        if it[0] == "l":
            push(tuple(it[1]), tuple(it[2]))
        elif it[0] == "c":
            P = it[1:5]
            prev = tuple(P[0])
            for s in range(1, steps + 1):
                u = s / steps
                q = tuple((1 - u) ** 3 * P[0][k] + 3 * (1 - u) ** 2 * u * P[1][k] + 3 * (1 - u) * u * u * P[2][k] + u ** 3 * P[3][k] for k in (0, 1))
                push(prev, q)
                prev = q
        elif it[0] == "re":
            R = it[1]
            pts = [(R.x0, R.y0), (R.x1, R.y0), (R.x1, R.y1), (R.x0, R.y1), (R.x0, R.y0)]
            for a, b in zip(pts, pts[1:]):
                push(a, b)
        elif it[0] == "qu":
            Q = it[1]
            pts = [tuple(Q.ul), tuple(Q.ur), tuple(Q.lr), tuple(Q.ll), tuple(Q.ul)]
            for a, b in zip(pts, pts[1:]):
                push(a, b)
    if cur:
        lines.append(cur)
    return [[tuple(pymupdf.Point(x, y) * m) for x, y in pl] for pl in lines]


def paths_in(page, clip):
    m = page.rotation_matrix
    r = pymupdf.Rect(clip)
    for p in page.get_drawings():
        if (p["rect"] * m).intersects(r):
            yield p, polylines(p, m)


# --- wall --------------------------------------------------------------------

def elev_to_uv(x, y):
    """Elevation page point -> (u, v) mm: u along the unfolded wall from the
    stage centreline (audience left negative), v up from the stage floor."""
    xc = (FOLD_X[0] + FOLD_X[1]) / 2
    return (x - xc) * MM_PER_PT, (STAGE_FFL_Y - y) * MM_PER_PT


def build_wall(doc):
    page = doc[ELEV_PAGE - 1]
    x0, y0, x1, y1 = ELEV_CLIP
    s = MM_PER_PT / TEX_MM_PER_PX  # texture px per page point
    W, H = int((x1 - x0) * s), int((y1 - y0) * s)
    px = lambda pt: ((pt[0] - x0) * s, (pt[1] - y0) * s)

    # The drawing's grey fills are rectangular zones, not panel finishes,
    # so only the cell joints are taken from it; finishes are by region
    # below (as in the photos: darker perforated metal across the stage,
    # concrete on the wings).
    lines = Image.new("L", (W, H), 0)       # cell joints
    dl = ImageDraw.Draw(lines)
    for p, pls in paths_in(page, ELEV_CLIP):
        k = key(p)
        r = p["rect"] * page.rotation_matrix
        small = r.width < 14 and r.height < 14
        if k[0] == (0.0, 0.0, 0.0) and k[1] == 0.24 and not small:
            for pl in pls:
                for a, b in zip(pl, pl[1:]):
                    L = math.dist(a, b)
                    axis = abs(a[0] - b[0]) < 0.2 or abs(a[1] - b[1]) < 0.2
                    if L < 95 and not (axis and L > 40):  # cell edges, not leaders or grid lines
                        dl.line([px(a), px(b)], fill=255, width=2)

    # Wall top: per column, the highest cell joint; then a median filter to
    # drop the odd stray annotation stub, and solid wall from there down.
    L = np.array(lines) > 0
    top = np.full(W, -1)
    for c in range(W):
        rows = np.nonzero(L[:, c])[0]
        if len(rows):
            top[c] = rows.min()
    valid = top >= 0
    cols = np.nonzero(valid)[0]
    c0, c1 = int(cols.min()), int(cols.max())
    t = top.astype(float)
    t[~valid] = np.interp(np.nonzero(~valid)[0], cols, top[valid])
    # Spikes: exhaust stacks behind the wall and section markers poke up
    # ~1.5 m wide. Anything more than 0.3 m above the median of the 6 m
    # around it drops to that median; then a light smoothing.
    k = int(3000 / TEX_MM_PER_PX)
    med = np.array([np.median(t[max(0, i - k):i + k + 1]) for i in range(W)])
    lim = 300 / TEX_MM_PER_PX
    t = np.where(t < med - lim, med, t)
    k = int(120 / TEX_MM_PER_PX)
    tm = np.array([np.median(t[max(0, i - k):i + k + 1]) for i in range(W)])

    floor_row = int((STAGE_FFL_Y - y0) * s)
    bottom_row = min(H - 1, floor_row + int(1500 / TEX_MM_PER_PX))  # 1.5 m below the stage floor
    alpha = np.zeros((H, W), np.uint8)
    for c in range(c0, c1 + 1):
        alpha[int(tm[c]):bottom_row, c] = 255

    # Texture: warm light concrete, darker cells where the drawing shades
    # them (the proscenium frame), dark joints.
    concrete = np.array([200, 196, 190], float)
    perf = np.array([128, 126, 124], float)
    rgb = np.broadcast_to(concrete, (H, W, 3)).copy()
    fx0, fx1 = int(px((FOLD_X[0], 0))[0]), int(px((FOLD_X[1], 0))[0])
    rgb[:, fx0:fx1] = perf
    # Perforation: a fine dot grid on the metal panels (~120 mm pitch).
    yy, xx = np.mgrid[0:H, 0:W]
    pitch = 120 / TEX_MM_PER_PX
    dots = ((xx % pitch < 1.5) & (yy % pitch < 1.5))
    rgb[:, fx0:fx1][dots[:, fx0:fx1]] *= 0.7
    # Bi-fold proscenium panels above the glass: darker still.
    bx0, by0 = px((GLASS_DOORS[0], 647.0))
    bx1, by1 = px((GLASS_DOORS[2], GLASS_DOORS[1]))
    rgb[int(by0):int(by1), int(bx0):int(bx1)] *= 0.72
    joints = np.array(lines.filter(ImageFilter.GaussianBlur(0.8))).astype(float) / 255
    rgb = rgb * (1 - 0.65 * joints[..., None])
    # Glass stage doors: dark, slightly blue.
    gx0, gy0 = px(GLASS_DOORS[:2])
    gx1, gy1 = px(GLASS_DOORS[2:])
    rgb[int(gy0):int(gy1), int(gx0):int(gx1)] = rgb[int(gy0):int(gy1), int(gx0):int(gx1)] * 0.25 + np.array([18, 24, 32]) * 0.75
    img = np.dstack([np.clip(rgb, 0, 255).astype(np.uint8), alpha])
    img = img[:, c0:c1 + 1]
    Image.fromarray(img, "RGBA").save(OUT_TEX, optimize=True)

    to_uv = lambda c, r: elev_to_uv(x0 + c / s, y0 + r / s)
    u0, _ = to_uv(c0, 0)
    u1, _ = to_uv(c1 + 1, 0)
    _, v_top = to_uv(0, 0)
    _, v_bot = to_uv(0, H)
    step = max(1, int(250 / TEX_MM_PER_PX))  # top edge sampled every 250 mm
    top_uv = [[round(to_uv(c, tm[c])[0]), round(to_uv(c, tm[c])[1])] for c in range(c0, c1 + 1, step)]
    fold = (FOLD_X[1] - FOLD_X[0]) / 2 * MM_PER_PT
    return {
        "texture": OUT_TEX.name,
        "u0": round(u0), "u1": round(u1),       # texture's horizontal extent (mm along the unfolded wall)
        "v_top": round(v_top), "v_bottom": round(v_bot),  # texture's vertical extent (mm from stage floor)
        "fold_u": round(fold),
        "wing_angle_deg": round(math.degrees(WING_ANGLE), 2),
        "top": top_uv,
    }


# --- canopy ------------------------------------------------------------------

def section_profile(doc):
    """Canopy height (top of slab) against depth Y, from section S1."""
    page = doc[SECT_PAGE - 1]
    pts = []
    for p, pls in paths_in(page, SECT_CLIP):
        if key(p) == ((0.0, 0.0, 0.0), 0.66, ()):
            for pl in pls:
                pts += pl
    bins = {}
    for x, y in pts:
        b = round(x / 6)
        bins[b] = min(bins.get(b, 1e9), y)
    prof = sorted(((x * 6 - SECT_WALL_X) * MM_PER_PT, (SECT_FFL_Y - y) * MM_PER_PT) for x, y in ((b, v) for b, v in bins.items()))
    return [(round(Y), round(Z)) for Y, Z in prof]


def front_sag(doc):
    """How far the canopy's front-on top edge drops from its peak, against
    X - from the faint canopy linework on the elevation sheet."""
    page = doc[ELEV_PAGE - 1]
    pts = []
    for p, pls in paths_in(page, (60, 380, 2040, 760)):
        if key(p) == ((0.71, 0.67, 0.67), 0.24, ()):
            for pl in pls:
                pts += pl
    bins = {}
    for x, y in pts:
        b = round(x / 10)
        bins[b] = min(bins.get(b, 1e9), y)
    fold = (FOLD_X[1] - FOLD_X[0]) / 2 * MM_PER_PT
    out = []
    for b, y in sorted(bins.items()):
        u, v = elev_to_uv(b * 10, y)
        # Unfolded u -> plan X beyond the fold corners.
        X = u if abs(u) <= fold else math.copysign(fold + (abs(u) - fold) * math.cos(WING_ANGLE), u)
        out.append((X, v))
    peak = max(v for _, v in out)
    return [(round(X), round(peak - v)) for X, v in out], peak


def interp(table, x):
    xs = [a for a, _ in table]
    if x <= xs[0]:
        return table[0][1]
    if x >= xs[-1]:
        return table[-1][1]
    i = next(i for i in range(1, len(xs)) if xs[i] >= x)
    (x0, y0), (x1, y1) = table[i - 1], table[i]
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def build_canopy(doc):
    page = doc[PLAN_PAGE - 1]
    xc = (WALL_CORNERS_X[0] + WALL_CORNERS_X[1]) / 2
    # Plan page -> VW frame. The roof plan has the audience at the top of
    # the sheet, so audience-left is the sheet's right: X flips.
    to_xy = lambda x, y: (-(x - xc) * MM_PER_PT, (y - WALL_FRONT_Y) * MM_PER_PT)
    prof = section_profile(doc)
    raw, _ = front_sag(doc)
    # The traced front-on edge is noisy (it picks up the legs and brackets
    # too), so fit a smooth symmetric dip, sag = a X^2 + b X^4, over the
    # ring's span and hold it flat beyond.
    span = 23600
    A = np.array([[(X / 1e4) ** 2, (X / 1e4) ** 4] for X, _ in raw if abs(X) <= span])
    y = np.array([v for X, v in raw if abs(X) <= span], float)
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    sag_of = lambda X: max(0.0, coef[0] * (min(abs(X), span) / 1e4) ** 2 + coef[1] * (min(abs(X), span) / 1e4) ** 4)
    z_at = lambda X, Y: interp(prof, Y) - sag_of(X)

    lines = []
    for p, pls in paths_in(page, (60, 60, 2050, 1600)):
        if key(p) == (CANOPY_COLOR, 0.24, ()):
            for pl in pls:
                if len(pl) >= 2:
                    lines.append([to_xy(*q) for q in pl])
    # Legs and footings sit behind the wall (Y > ~1 m past the section's
    # slab); everything else is the canopy itself.
    y_back = max(Y for Y, _ in prof)
    canopy, legs = [], []
    for pl in lines:
        (legs if max(Y for _, Y in pl) > y_back + 800 else canopy).append(pl)
    pts = [q for pl in canopy for q in pl]
    x_min, x_max = min(X for X, _ in pts), max(X for X, _ in pts)

    def lift(pl):
        return [[round(X), round(Y), round(z_at(X, Y))] for X, Y in pl]

    # Each leg: from the canopy's side end, arcing out and down to the
    # footing behind the wall. The plan only gives the footprint, so the
    # curve is drawn as a smooth arch through those two points.
    leg_curves = []
    for side in (-1, 1):
        foot_pts = [q for pl in legs for q in pl if (q[0] > 0) == (side > 0)]
        if not foot_pts:
            continue
        foot = max(foot_pts, key=lambda q: q[1])
        end_x = x_max if side > 0 else x_min
        end = min((q for q in pts if abs(q[0] - end_x) < 600), key=lambda q: abs(q[0] - end_x))
        z_end = z_at(*end)
        ground = -700
        curve = []
        for i in range(25):
            t = i / 24
            X = end[0] + (foot[0] - end[0]) * t
            Y = end[1] + (foot[1] - end[1]) * t
            # Outward bulge (photos: the legs sweep out past the ring) and
            # a quarter-ellipse drop to the ground.
            X += side * 2500 * math.sin(math.pi * t)
            Z = ground + (z_end - ground) * math.cos(t * math.pi / 2)
            curve.append([round(X), round(Y), round(Z)])
        leg_curves.append(curve)

    # Brackets where the legs meet the ring sit past its ends; drop them.
    canopy = [pl for pl in canopy if all(abs(X) <= span + 200 for X, _ in pl)]
    return {
        "lines": [lift(pl) for pl in canopy],
        "legs": leg_curves,
        "profile": prof,
        "sag": [[X, round(sag_of(X))] for X in range(-span, span + 1, 2000)],
    }


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    doc = pymupdf.open(sys.argv[1])
    wall = build_wall(doc)
    canopy = build_canopy(doc)
    OUT_JSON.write_text(json.dumps({
        "source": "tools/build_vos_model.py from the ARM/Topotek 1 as-built set: sheets TH-2000 (elevation), TH-1010 (roof plan), TH-3200 S1 (section)",
        "units": "mm; X across (audience left negative), Y depth (audience at -Y), Z up from the stage floor (RL 3.50)",
        "ground_z": -700,
        "wall": wall,
        "canopy": canopy,
    }) + "\n", encoding="utf-8")
    zs = [q[2] for pl in canopy["lines"] for q in pl]
    print(f"wall: u {wall['u0']}..{wall['u1']} mm, fold at +-{wall['fold_u']} mm, top up to {max(v for _, v in wall['top'])} mm")
    print(f"canopy: {len(canopy['lines'])} lines, Z {min(zs)}..{max(zs)} mm, {len(canopy['legs'])} legs")
    print(f"wrote {OUT_JSON.relative_to(ROOT)} and {OUT_TEX.relative_to(ROOT)} ({OUT_TEX.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
