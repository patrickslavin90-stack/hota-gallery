#!/usr/bin/env python3
"""Places every fixture onto the traced elevations (static/elevations.json)
and saves the result as a "Building" layout in data/config.json (and
static/building-layout.json for the UI) - display
positions only; fixtures' own `points` (what effects are computed from)
are left alone.

Strips: the ELM layout has the right *sequence* of strip pieces but not the
right proportions, so each crack run is mapped piecewise. Anchor pairs tie
a corner in the ELM run (usually a single-LED "corner" fixture) to the same
corner of the lit crack on the architect's strip-setout sheet, read off
strip_setout_east_north.png at full resolution. Every LED is projected onto
the ELM anchor polyline and re-placed at the same fraction along the
matching drawing segment, so lights always sit on the crack line.

Dishes: there's no data tying a dish's name to its position (see
redistribute_dishes.py), so they're assigned to the dish symbols on the
drawings in left-to-right order - right count per wall, arbitrary within
it.

    python tools/map_to_elevations.py
"""
from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "data" / "config.json"
ELEV = ROOT / "hota_gallery" / "static" / "elevations.json"
# Also shipped with the UI, so a controller whose config predates this
# layout still shows the building view (see app.js allLayouts()).
STATIC_LAYOUT = ROOT / "hota_gallery" / "static" / "building-layout.json"

# (ELM x, y) -> (sheet x, y) on strip_setout_east_north.png
EAST = [
    ((1147.8, 281.3), (2153.0, 886.0)),     # canopy pop-out (out of plane on the drawing)
    ((1128.8, 276.8), (1702.9, 1119.6)),
    ((1039.2, 301.2), (1535.1, 1150.4)),
    ((1037.9, 337.5), (1519.7, 1366.4)),
    ((1019.0, 343.0), (1404.0, 1395.4)),
    ((993.0, 390.0), (1249.7, 1646.1)),
    ((975.8, 466.1), (1143.6, 2128.3)),
    ((1023.6, 487.1), (1461.8, 2269.0)),
    ((1030.2, 509.1), (1486.9, 2398.3)),
    ((1034.8, 597.8), (1510.0, 2919.0)),
    ((1004.8, 589.8), (1313.3, 2866.9)),
    ((929.5, 628.3), (869.7, 3092.6)),
    ((987.8, 669.9), (1220.7, 3349.1)),
    ((987.9, 714.9), (1230.4, 3622.9)),
]
NORTH_A = [  # universes 3-5: canopy, down the left crack to the ground
    ((1504.4, 267.3), (3594.0, 829.0)),
    ((1541.6, 267.3), (3700.0, 1150.0)),
    ((1557.6, 286.1), (3785.7, 1400.0)),
    ((1556.3, 347.0), (3877.1, 1577.1)),
    ((1559.1, 350.2), (3911.4, 1634.3)),
    ((1582.4, 356.5), (4002.9, 1653.7)),
    ((1598.3, 366.4), (4117.1, 1714.3)),
    ((1625.7, 402.5), (4294.3, 1931.4)),
    ((1644.9, 494.0), (4402.9, 2480.0)),
    ((1671.1, 511.0), (4551.4, 2597.7)),
    ((1691.8, 542.5), (4688.6, 2771.4)),
    ((1688.6, 560.7), (4666.4, 2879.3)),
    ((1727.9, 597.9), (4917.1, 3094.0)),
    ((1698.8, 690.4), (4711.4, 3631.4)),
    ((1692.1, 717.8), (4679.3, 3830.7)),
]
NORTH_B = [  # universes 6-8: right crack, out to the east edge and back down
    ((1605.3, 315.1), (4170.9, 1457.0)),
    ((1606.0, 348.5), (4170.9, 1600.0)),
    ((1623.7, 358.5), (4288.6, 1674.3)),
    ((1636.7, 394.4), (4368.6, 1893.7)),
    ((1721.0, 428.6), (4860.0, 2091.4)),
    ((1737.7, 505.4), (4962.9, 2554.3)),
    ((1754.7, 530.1), (5068.8, 2703.1)),
    ((1751.3, 547.9), (5045.7, 2808.6)),
    ((1756.1, 573.6), (5061.1, 2952.6)),
    ((1815.6, 572.4), (5431.4, 2943.6)),
    ((1903.6, 634.0), (5849.3, 3243.1)),
    ((1906.1, 646.2), (5849.3, 3400.0)),
    ((1805.9, 662.2), (5367.1, 3483.5)),
    ((1791.4, 683.8), (5283.5, 3605.7)),
    ((1795.2, 739.2), (5302.8, 3959.2)),
]


def chain_for(fx):
    u = fx["universe"]
    if u in (0, 1, 2):
        return EAST, "East"
    if u in (3, 4, 5):
        return NORTH_A, "North"
    return NORTH_B, "North"


def project(anchors, p):
    """ELM point -> sheet point: nearest spot on the ELM anchor polyline,
    re-placed at the same fraction along the matching drawing segment."""
    best = None
    for i in range(len(anchors) - 1):
        (ax, ay), (bx, by) = anchors[i][0], anchors[i + 1][0]
        vx, vy = bx - ax, by - ay
        L2 = vx * vx + vy * vy or 1e-9
        t = max(0.0, min(1.0, ((p[0] - ax) * vx + (p[1] - ay) * vy) / L2))
        d = math.hypot(ax + vx * t - p[0], ay + vy * t - p[1])
        if best is None or d < best[0]:
            best = (d, i, t)
    _, i, t = best
    (cx, cy), (dx, dy) = anchors[i][1], anchors[i + 1][1]
    return (cx + (dx - cx) * t, cy + (dy - cy) * t)


def main():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    elev = json.loads(ELEV.read_text(encoding="utf-8"))
    by_name = {e["name"]: e for e in elev["elevations"]}
    to_world = lambda wall, p: [round(p[0] + by_name[wall]["sheet_offset"][0], 1), round(p[1] + by_name[wall]["sheet_offset"][1], 1)]

    positions = {}
    for fx in cfg["fixtures"]:
        if fx["zone"] != "strips":
            continue
        anchors, wall = chain_for(fx)
        a, b = fx["points"]
        if fx["led_count"] == 1:
            p = to_world(wall, project(anchors, a))
            positions[f"{fx['universe']}:{fx['address']}"] = [p, p]
        else:
            positions[f"{fx['universe']}:{fx['address']}"] = [to_world(wall, project(anchors, a)), to_world(wall, project(anchors, b))]

    # Dish slots, left to right along the unfolded building.
    east, north, west, south = by_name["East"], by_name["North"], by_name["West"], by_name["South"]
    slots = [tuple(p) for e in (south, east, north, west) for p in e["dishes"]]
    # Corner dishes, drawn side-on at the elevation edges on the setout
    # sheets (East/North corner, North/West corner).
    slots.append((east["x1"] - 194, 2212.0))
    slots.append((north["x1"] - 20, 2040.0))
    # The aerial count puts 7 dishes on the South wall but the setout only
    # draws its top half (4). The other 3 go low on the wall, evenly spaced -
    # approximate until someone confirms where they are.
    south_bottom = max(p[1] for poly in south["outline"] for p in poly)
    for f in (0.25, 0.5, 0.75):
        slots.append((south["x0"] + (south["x1"] - south["x0"]) * f, south_bottom - 160))
    slots.sort()

    dishes = sorted((fx for fx in cfg["fixtures"] if fx["zone"] == "dishes"), key=lambda f: f["points"][0][0])
    assert len(slots) == len(dishes), f"{len(slots)} dish slots vs {len(dishes)} dishes"
    for fx, (x, y) in zip(dishes, slots):
        p = [round(x, 1), round(y, 1)]
        positions[f"{fx['universe']}:{fx['address']}"] = [p, p]

    layout = {"name": "Building", "fixtures": positions}
    cfg["layouts"] = [layout] + [l for l in cfg["layouts"] if l["name"] != "Building"]
    CONFIG.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    STATIC_LAYOUT.write_text(json.dumps(layout, separators=(",", ":")), encoding="utf-8")
    print(f"Building layout: {len(positions)} fixtures placed")


if __name__ == "__main__":
    main()
