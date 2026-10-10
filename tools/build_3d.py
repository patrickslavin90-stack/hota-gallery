#!/usr/bin/env python3
"""Derives a rough 3D massing for the 3D view from the elevation sheets'
structural grid - there are no floor plans in the repo, but every
elevation draws its grid lines (red dash-dot) and dimensions them:

    numbered grid 1-3 (seen on East/West):  3 -5928- 2 -13145- 1
    lettered grid J-N (seen on North/South): J -5850- K -6000- L -6000- M -5800- N

So each sheet x maps to a real plan coordinate, which places every wall
of the traced elevations (static/elevations.json) in one plan. Writes
static/building3d.json with, per wall, the plane it sits on and the
linear map from sheet x to the coordinate along that wall.

Plan axes (mm): X east, Y north, Z up from Level Ground (FFL 7.500).
Rough by design: walls are flat planes at the tower's outer faces, so
the canopy pop-outs and any faceting are drawn flat.

Needs OpenCV + numpy (dev only):  python tools/build_3d.py
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "data" / "venue_assets" / "hota"
ELEV = ROOT / "hota_gallery" / "static" / "elevations.json"
OUT = ROOT / "hota_gallery" / "static" / "building3d.json"

GRID_X = {"N": 0.0, "M": 5800.0, "L": 11800.0, "K": 17800.0, "J": 23650.0}   # west -> east
GRID_Y = {"3": 0.0, "2": 5928.0, "1": 19073.0}                              # south -> north
GROUND_Y = 3622.0  # Level Ground line, east/north sheet y (the shared datum in elevations.json)

# Which grid lines each elevation shows, left to right as drawn, and which
# sheet x-range it occupies (so lines from the neighbouring drawing on the
# same sheet aren't mixed in).
WALLS = {
    "East":  {"sheet": "strip_setout_east_north", "xr": (200, 2950),  "labels": ["3", "2", "1"],      "axis": "Y"},
    "North": {"sheet": "strip_setout_east_north", "xr": (3240, 6150), "labels": ["J", "K", "L", "M", "N"], "axis": "X"},
    "West":  {"sheet": "strip_setout_west_south", "xr": (950, 2860),  "labels": ["1", "2", "3"],      "axis": "Y"},
    "South": {"sheet": "strip_setout_west_south", "xr": (3600, 5340), "labels": ["N", "M", "L", "K"], "axis": "X"},
}


def red_columns(img, x0, x1):
    b, g, r = (img[..., i].astype(int) for i in range(3))
    red = (r > 180) & (g < 140) & (b < 160)
    cols = red[400:3900, x0:x1].sum(0)
    xs = [x for x in range(len(cols)) if cols[x] > 600]
    groups = []
    for x in xs:
        if groups and x - groups[-1][-1] <= 4:
            groups[-1].append(x)
        else:
            groups.append([x])
    return [x0 + float(np.mean(gp)) for gp in groups]


def main():
    elev = {e["name"]: e for e in json.loads(ELEV.read_text())["elevations"]}
    sheets = {}
    maps = {}
    for name, w in WALLS.items():
        img = sheets.setdefault(w["sheet"], cv2.imread(str(ASSETS / f"{w['sheet']}.png")))
        found = red_columns(img, *w["xr"])
        grid = GRID_Y if w["axis"] == "Y" else GRID_X
        # Detection can miss a line (e.g. N at the sheet edge); pair the
        # found lines with labels in order, as many as were found.
        pairs = list(zip(found, w["labels"]))
        sx = np.array([p[0] for p in pairs])
        val = np.array([grid[p[1]] for p in pairs])
        a, b = np.polyfit(sx, val, 1)  # along-wall coordinate = a * sheet_x + b
        resid = np.abs(a * sx + b - val).max()
        maps[name] = (a, b)
        print(f"{name:5s} grid {[p[1] for p in pairs]} at sheet x {[round(x) for x in sx]}  "
              f"scale {1 / abs(a):.4f} px/mm  worst fit {resid:.0f} mm")

    k = float(np.mean([1 / abs(a) for a, _ in maps.values()]))  # sheet px per mm

    def span(name):
        e = elev[name]
        xs = [p[0] - e["sheet_offset"][0] for poly in e["outline"] for p in poly]
        a, b = maps[name]
        return sorted([a * min(xs) + b, a * max(xs) + b])

    # Wall planes: where the perpendicular walls' drawings end. West is
    # where North and South both stop; East/North/South use the main body
    # edges (the canopy pop-outs run past them).
    west = float(np.mean([span("North")[0], span("South")[0]]))
    east = 24200.0   # North elevation's main body edge (canopy excluded) - see module docstring
    south = span("West")[0]
    north = span("West")[1]

    faces = {
        "East":  {"plane": {"x": east},  "along": "y", "a": maps["East"][0],  "b": maps["East"][1],  "normal": [1, 0, 0]},
        "North": {"plane": {"y": north}, "along": "x", "a": maps["North"][0], "b": maps["North"][1], "normal": [0, 1, 0]},
        "West":  {"plane": {"x": west},  "along": "y", "a": maps["West"][0],  "b": maps["West"][1],  "normal": [-1, 0, 0]},
        "South": {"plane": {"y": south}, "along": "x", "a": maps["South"][0], "b": maps["South"][1], "normal": [0, -1, 0]},
    }
    out = {
        "units": "mm; X east, Y north, Z up from Level Ground",
        "px_per_mm": k,
        "ground_y": GROUND_Y,
        "footprint": {"west": round(west), "east": round(east), "south": round(south), "north": round(north)},
        "grid": {"x": GRID_X, "y": GRID_Y},
        "faces": faces,
    }
    OUT.write_text(json.dumps(out, indent=1))
    print(f"footprint X {west:.0f}..{east:.0f}  Y {south:.0f}..{north:.0f}  ({(east - west) / 1000:.1f} x {(north - south) / 1000:.1f} m)")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
