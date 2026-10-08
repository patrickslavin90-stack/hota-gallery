#!/usr/bin/env python3
"""One-time fix: the 27 dish fixtures' ELM coordinates came from their own
separate editing canvas in ELM (the "HOTA Gallery Dishes" stage), which
has no positional relationship to the main facade's canvas - plotting them
together put most dishes bunched at one spot with a stray outlier, not a
real layout bug, just two incompatible coordinate spaces merged.

This is a rough, deliberate one-time redistribution (per the user: "just
set a rough layout" - not chasing exact positions without the VectorWorks
render), spreading the 27 dishes evenly across the same 4 visual wall
bands as the UI's cosmetic quarter-split dividers, roughly scattered
within each band. Not meant to be precise - just no longer nonsensical.

Usage: python tools/redistribute_dishes.py <pi-host>
"""
from __future__ import annotations

import json
import random
import sys
import urllib.request

random.seed(42)  # reproducible - same "rough" layout every run, not a new shuffle each time


def api(host: str, path: str, method: str = "GET", body=None):
    url = f"http://{host}:8080{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def main() -> int:
    host = sys.argv[1] if len(sys.argv) > 1 else "192.168.136.222"
    cfg = api(host, "/api/config")
    fixtures = cfg["fixtures"]

    xs = [p[0] for fx in fixtures for p in fx["points"]]
    ys = [p[1] for fx in fixtures for p in fx["points"]]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max_x - min_x

    dishes = [fx for fx in fixtures if fx["led_type"] == "RGB"]
    print(f"{len(dishes)} dish fixtures found")

    # Real counts from the aerial photos (user-confirmed), not an even
    # split: wall1=6 + 1 on the 1/2 corner, wall2=4 + 1 on the 2/3 corner,
    # wall3=8, wall4=7. Sums to 27 - exactly the dish count found above.
    # Which *specific* named dish lands in which slot is arbitrary (file
    # order) - there's no data tying a name to a real position, only
    # these counts, so this is deliberately "rough" per the user's call.
    assignment = (
        ["wall1"] * 6 + ["corner12"] + ["wall2"] * 4 + ["corner23"] + ["wall3"] * 8 + ["wall4"] * 7
    )
    assert len(assignment) == len(dishes), f"{len(assignment)} slots vs {len(dishes)} dishes"

    n_bands = 4
    band_width = span_x / n_bands
    mid_y = min_y + (max_y - min_y) * 0.4  # upper-middle, roughly matching the photos

    def band_x_range(band_index: int) -> tuple:
        return min_x + band_index * band_width, min_x + (band_index + 1) * band_width

    for fx, slot in zip(dishes, assignment):
        if slot == "corner12":
            target_x = min_x + band_width  # the wall1/wall2 divider line
        elif slot == "corner23":
            target_x = min_x + 2 * band_width  # the wall2/wall3 divider line
        else:
            band_index = int(slot[-1]) - 1
            lo, hi = band_x_range(band_index)
            target_x = lo + random.uniform(0.15, 0.85) * (hi - lo)
        target_y = mid_y + random.uniform(-1, 1) * (max_y - min_y) * 0.15

        old_x, old_y = fx["points"][0]
        dx, dy = target_x - old_x, target_y - old_y
        key = f"{fx['universe']}:{fx['address']}"
        api(host, f"/api/fixtures/{key}/position", "PUT", {"dx": dx, "dy": dy})
        print(f"  {fx['name']!r} (uni {fx['universe']} addr {fx['address']}): "
              f"{slot}, moved to ({target_x:.0f}, {target_y:.0f})")

    print("\ndone - refresh the browser to see the new spread")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
