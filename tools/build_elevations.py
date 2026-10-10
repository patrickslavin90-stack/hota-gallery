#!/usr/bin/env python3
"""Traces the architect's elevation sheets (data/venue_assets/hota/) into
vector linework for the web UI's canvas - building outline, crack bands
and glazing grid as simplified polylines - and writes
hota_gallery/static/elevations.json.

World units are full-resolution sheet pixels (both setout sheets are the
same paper size at 1:100, so one unit is the same real distance on every
elevation). Elevations are laid out left to right in the order you'd walk
round the building: South, East, North, West - each one's right edge meets
the next one's left edge.

Needs OpenCV + numpy (dev-time only, not on the Pi):
    pip install opencv-python-headless numpy
    python tools/build_elevations.py
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "data" / "venue_assets" / "hota"
OUT = ROOT / "hota_gallery" / "static" / "elevations.json"

SCALE = 0.5  # process at half resolution - bands are ~20px wide there
GAP = 260    # world units between elevations in the unfolded row

# (name, sheet, crop x0, y0, x1, y1) in full-res sheet pixels. Crops stop
# short of the dimension strings/gridline bubbles around each drawing.
ELEVATIONS = [
    ("South", "strip_setout_west_south", 3600, 560, 5340, 2830),
    ("East", "strip_setout_east_north", 200, 560, 2950, 3660),
    ("North", "strip_setout_east_north", 3240, 560, 5900, 4000),
    ("West", "strip_setout_west_south", 950, 560, 2860, 3830),
]

# The two sheets place the drawings at different heights on the page.
# Their floor-level lines sit 154px apart, so shifting the West/South
# sheet down by that puts every elevation on one shared datum.
SHEET_DY = {"strip_setout_west_south": 154.0}

# Floor levels (east/north sheet y, which is the shared datum), read off
# the blue level lines on the setout sheets.
LEVELS = [
    ("Roof", 959), ("Level 5", 1384), ("Level 4", 1894), ("Level 3", 2405),
    ("Level 2", 2916), ("Level 1", 3238), ("Ground", 3622), ("Lower ground", 3960),
]


def simplify(contours, eps, min_len):
    out = []
    for c in contours:
        if cv2.arcLength(c, True) < min_len:
            continue
        a = cv2.approxPolyDP(c, eps, True).reshape(-1, 2)
        if len(a) >= 2:
            out.append(a)
    return out


def trace(sheet_img, crop):
    x0, y0, x1, y1 = crop
    img = sheet_img[y0:y1, x0:x1]
    img = cv2.resize(img, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    sat, val = hsv[..., 1], hsv[..., 2]

    grey = (sat < 14) & (val > 160) & (val < 205)
    grey = grey.astype(np.uint8) * 255

    # Crack bands: the thick grey strokes. Opening drops the thin glazing
    # grid lines and text, which share the same grey.
    # Close first, so the white lit-strip line drawn down the middle of a
    # band (and dimension strings crossing it) don't split it in two.
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    bands = cv2.morphologyEx(grey, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))
    bands = cv2.morphologyEx(bands, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13)))
    bands = cv2.GaussianBlur(bands, (9, 9), 0)
    bands = (bands > 127).astype(np.uint8) * 255

    # Building: anything coloured (the cladding fills) or banded, closed up
    # and hole-filled, so its outer contour is the elevation's silhouette.
    coloured = ((sat > 18) & (val > 120)).astype(np.uint8) * 255
    body = cv2.bitwise_or(coloured, bands)
    body = cv2.morphologyEx(body, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15)))
    body = cv2.morphologyEx(body, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (11, 11)))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(body)
    keep = np.zeros_like(body)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] > body.size * 0.01:
            keep[labels == i] = 255
    body = keep
    outer, _ = cv2.findContours(body, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)

    # Bands only count inside the building (drops grey title-block bits).
    inside = cv2.dilate(body, cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9)))
    bands = cv2.bitwise_and(bands, inside)
    band_c, _ = cv2.findContours(bands, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)

    # Glazing grid: long thin straight grey lines inside the building, off
    # the bands.
    thin = cv2.bitwise_and(grey, cv2.bitwise_not(cv2.dilate(bands, k)))
    thin = cv2.bitwise_and(thin, cv2.erode(body, cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))))
    lines = cv2.HoughLinesP(thin, 1, np.pi / 180, threshold=60, minLineLength=60, maxLineGap=6)
    grid = []
    if lines is not None:
        for (ax, ay, bx, by) in lines.reshape(-1, 4):
            # Only near-horizontal/vertical runs - mullions and transoms.
            if abs(ax - bx) < 3 or abs(ay - by) < 3:
                grid.append(np.array([[ax, ay], [bx, by]]))

    # Dish symbols: white discs ~40px across at this scale (a ring with a
    # target in the middle). Fill each white blob's holes and keep the
    # round ones.
    white = ((sat < 12) & (val > 240)).astype(np.uint8) * 255
    white = cv2.bitwise_and(white, cv2.dilate(body, cv2.getStructuringElement(cv2.MORPH_RECT, (41, 41))))
    blobs, _ = cv2.findContours(white, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    dishes = []
    for b in blobs:
        area = cv2.contourArea(b)
        (cx, cy), r = cv2.minEnclosingCircle(b)
        if 14 <= r <= 30 and area > 0.7 * np.pi * r * r:
            dishes.append((cx, cy))

    to_world = lambda pts: [[round(x0 + float(px) / SCALE, 1), round(y0 + float(py) / SCALE, 1)] for px, py in pts]
    return {
        "outline": [to_world(p) for p in simplify(outer, 1.6, 200)],
        "bands": [to_world(p) for p in simplify(band_c, 1.4, 160)],
        "grid": [to_world(p) for p in grid],
        "dishes": to_world(sorted(dishes, key=lambda d: (d[1], d[0]))),
    }


def main():
    sheets = {}
    result = {"units": "sheet px (1:100, full res)", "elevations": []}
    offset = 0.0
    for name, sheet, *crop in ELEVATIONS:
        if sheet not in sheets:
            sheets[sheet] = cv2.imread(str(ASSETS / f"{sheet}.png"))
        t = trace(sheets[sheet], crop)
        allpts = [p for k in ("outline", "bands", "grid") for poly in t[k] for p in poly]
        minx = min(p[0] for p in allpts)
        maxx = max(p[0] for p in allpts)
        dx = offset - minx
        dy = SHEET_DY.get(sheet, 0.0)
        shift = lambda polys: [[[round(x + dx, 1), round(y + dy, 1)] for x, y in poly] for poly in polys]
        result["elevations"].append({
            "name": name,
            "sheet": sheet,
            "sheet_offset": [round(dx, 1), dy],  # world = sheet + this
            "x0": round(offset, 1),
            "x1": round(offset + maxx - minx, 1),
            **{k: shift(v) for k, v in t.items() if k != "dishes"},
            "dishes": shift([t["dishes"]])[0] if t["dishes"] else [],
        })
        print(f"{name}: outline {len(t['outline'])}, bands {len(t['bands'])}, grid {len(t['grid'])}, dishes {len(t['dishes'])}")
        offset += (maxx - minx) + GAP
    result["levels"] = [{"name": n, "y": y} for n, y in LEVELS]
    OUT.write_text(json.dumps(result, separators=(",", ":")))
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
