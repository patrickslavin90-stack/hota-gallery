#!/usr/bin/env python3
"""One-time fix: tag every dish fixture (led_type == "RGB" - exclusively
dishes in this fixture set, see redistribute_dishes.py) with zone
"dishes", and register that zone - needed so the scheduler can give the
dishes their own look distinct from the main facade, matching how ELM's
two separate stages worked.

Usage: python tools/tag_dish_zone.py [config path, default data/config.json]
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

    with open(config_path, encoding="utf-8") as fh:
        cfg = json.load(fh)

    if not any(z["name"] == "dishes" for z in cfg["zones"]):
        cfg["zones"].append({"name": "dishes"})

    tagged = 0
    for fx in cfg["fixtures"]:
        if fx["led_type"] == "RGB":
            fx["zone"] = "dishes"
            tagged += 1

    validated = validate_config(cfg)
    save_config(str(config_path), validated)
    print(f"tagged {tagged} dish fixtures with zone 'dishes', wrote {config_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
