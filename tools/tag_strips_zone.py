#!/usr/bin/env python3
"""One-time: tag every facade strip fixture (led_type == "RGBW" -
exclusively the facade circuit in this fixture set, dishes are RGB) with
zone "strips", and register that zone - mirrors tag_dish_zone.py. Needed
so the scheduler/randomizer can target "the strips" as a group the same
way they already target "dishes".

Usage: python tools/tag_strips_zone.py [config path, default data/config.json]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from hota_gallery.config import save_config, validate_config  # noqa: E402


def main() -> int:
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT_ROOT / "data" / "config.json"

    with open(config_path, encoding="utf-8-sig") as fh:
        cfg = json.load(fh)

    if not any(z["name"] == "strips" for z in cfg["zones"]):
        cfg["zones"].append({"name": "strips"})

    tagged = 0
    for fx in cfg["fixtures"]:
        if fx["led_type"] == "RGBW":
            fx["zone"] = "strips"
            tagged += 1

    validated = validate_config(cfg)
    save_config(str(config_path), validated)
    print(f"tagged {tagged} strip fixtures with zone 'strips', wrote {config_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
