#!/usr/bin/env python3
"""One-time: create the initial "Facade Strips" (RGBW), "Dishes" (RGB),
and "All" (everything) layouts, seeded from each fixture's current
`points` as a starting arrangement - drag them into whatever arrangement
is actually useful from there in the Live tab. Re-running replaces any
existing layout with the same name; it does not touch other layouts.

Usage: python tools/seed_layouts.py [config path, default data/config.json]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from hota_gallery.config import ConfigError, save_config, validate_config  # noqa: E402

LAYOUTS = [
    ("Facade Strips", "RGBW"),
    ("Dishes", "RGB"),
    ("All", None),  # None = every fixture, regardless of led_type
]


def main() -> int:
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT_ROOT / "data" / "config.json"

    with open(config_path, encoding="utf-8-sig") as fh:
        cfg = json.load(fh)

    by_name = {l["name"]: l for l in cfg.get("layouts", [])}
    for name, led_type in LAYOUTS:
        fixtures = {
            f"{fx['universe']}:{fx['address']}": fx["points"]
            for fx in cfg["fixtures"]
            if led_type is None or fx["led_type"] == led_type
        }
        by_name[name] = {"name": name, "fixtures": fixtures}
        print(f"{name}: {len(fixtures)} fixtures")

    cfg["layouts"] = list(by_name.values())

    try:
        validated = validate_config(cfg)
    except ConfigError as exc:
        print(f"resulting config is invalid, nothing written:\n{exc}", file=sys.stderr)
        return 1

    save_config(str(config_path), validated)
    print(f"wrote {config_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
