#!/usr/bin/env python3
"""Re-import a layout CSV (see export_layout_csv.py) edited by hand -
matches rows back to fixtures by universe+address and updates only their
points. Anything else about a fixture (name, led_type, led_count,
universe, address) is expected to be unchanged; a mismatch there almost
always means a row got reordered/deleted by accident in the spreadsheet,
so it's treated as an error, not silently trusted.

Usage: python tools/import_layout_csv.py <edited-csv-path> [config path, default data/config.json]
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from hota_gallery.config import ConfigError, save_config, validate_config  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python tools/import_layout_csv.py <edited-csv-path> [config-path]", file=sys.stderr)
        return 1

    csv_path = Path(sys.argv[1])
    config_path = Path(sys.argv[2]) if len(sys.argv) > 2 else PROJECT_ROOT / "data" / "config.json"

    # utf-8-sig: tolerate a BOM, same as config.py's own load_config - a
    # Windows tool (PowerShell's Out-File, notably) writes one by default.
    with open(config_path, encoding="utf-8-sig") as fh:
        cfg = json.load(fh)
    by_key = {f"{fx['universe']}:{fx['address']}": fx for fx in cfg["fixtures"]}

    errors = []
    updated = 0
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        for i, row in enumerate(csv.DictReader(fh), start=2):  # row 1 is the header
            key = f"{row['universe']}:{row['address']}"
            fx = by_key.get(key)
            if fx is None:
                errors.append(f"row {i}: no fixture at universe {row['universe']} address {row['address']}")
                continue
            if fx["name"] != row["name"] or fx["led_type"] != row["led_type"] or str(fx["led_count"]) != row["led_count"]:
                errors.append(
                    f"row {i} ({key}): name/led_type/led_count changed "
                    f"({fx['name']!r}/{fx['led_type']}/{fx['led_count']} -> "
                    f"{row['name']!r}/{row['led_type']}/{row['led_count']}) - "
                    "looks like a reordered/edited row, not just a position change"
                )
                continue
            try:
                fx["points"] = [
                    [float(row["x1"]), float(row["y1"])],
                    [float(row["x2"]), float(row["y2"])],
                ]
            except ValueError as exc:
                errors.append(f"row {i} ({key}): bad number in x1/y1/x2/y2 - {exc}")
                continue
            updated += 1

    if errors:
        print(f"{len(errors)} problem(s), nothing written:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    try:
        validated = validate_config(cfg)
    except ConfigError as exc:
        print(f"resulting config is invalid, nothing written:\n{exc}", file=sys.stderr)
        return 1

    save_config(str(config_path), validated)
    print(f"updated {updated} fixture position(s), wrote {config_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
