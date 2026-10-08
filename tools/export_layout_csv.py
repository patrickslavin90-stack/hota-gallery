#!/usr/bin/env python3
"""Export every fixture's identity + geometry to a plain CSV for manual
editing in a spreadsheet, against the VectorWorks drawing or photos -
much safer than live-dragging dots on the canvas, and the user can work
at their own pace without any risk of breaking the live config.

universe+address is the key column, not name - see elm_import.py and
config.py's docstrings on why (names aren't unique: a lot of real
fixtures share the generic "RGBW01" placeholder). Re-importing (see
import_layout_csv.py) matches rows back by that same key and only ever
touches the two position columns - everything else about a fixture (its
DMX patch, LED count, type) is untouched by this round trip.

Usage: python tools/export_layout_csv.py [output path, default data/layout_for_editing.csv]
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

COLUMNS = ["universe", "address", "name", "led_type", "led_count", "x1", "y1", "x2", "y2"]


def main() -> int:
    config_path = PROJECT_ROOT / "data" / "config.json"
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT_ROOT / "data" / "layout_for_editing.csv"

    # utf-8-sig: tolerate a BOM, same as config.py's own load_config - a
    # Windows tool (PowerShell's Out-File, notably) writes one by default.
    with open(config_path, encoding="utf-8-sig") as fh:
        cfg = json.load(fh)

    fixtures = sorted(cfg["fixtures"], key=lambda f: (f["universe"], f["address"]))

    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(COLUMNS)
        for fx in fixtures:
            (x1, y1), (x2, y2) = fx["points"]
            writer.writerow([fx["universe"], fx["address"], fx["name"], fx["led_type"], fx["led_count"], x1, y1, x2, y2])

    print(f"wrote {out_path} ({len(fixtures)} fixtures)")
    print("Edit only the x1/y1/x2/y2 columns - everything else is the real DMX patch, don't change it.")
    print("For a single-dot fixture (most dishes), x2/y2 can just match x1/y1.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
