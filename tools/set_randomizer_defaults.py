#!/usr/bin/env python3
"""One-time: seed the randomizer with sensible defaults - targets both
zones, referencing a small pool of named chase-style demo presets for
variety (created here if a preset with that name doesn't already exist),
~20 events/hour, 6 second bursts. Left *disabled* - turning it on live is
a deliberate staff decision, not something a config push should do on its
own.

The randomizer's burst pool is just a set of preset slot numbers (see
config.py's _validate_randomizer) - it has no editor of its own anymore,
it reuses whatever's on the Live tab's preset grid. This script only
exists to seed a few demo presets so the pool isn't empty on first boot;
after that, add/remove bursts from the pool via the Schedules tab.

Usage: python tools/set_randomizer_defaults.py [config path, default data/config.json]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from hota_gallery.config import ConfigError, save_config, validate_config  # noqa: E402

WHITE = {"r": 255, "g": 255, "b": 255, "w": 0}
_SOLID_WHITE = {"mode": "solid", "color": WHITE}
_NO_VIDEO = {"mode": "off"}

DEMO_PRESETS = [
    {
        "name": "Chase forward",
        "look": {"colour": _SOLID_WHITE, "video": _NO_VIDEO,
                  "effect": {"mode": "chase", "period_s": 1.5, "tail": 4, "axis": "x", "direction": "forward"}},
    },
    {
        "name": "Chase reverse",
        "look": {"colour": _SOLID_WHITE, "video": _NO_VIDEO,
                  "effect": {"mode": "chase", "period_s": 1.5, "tail": 4, "axis": "x", "direction": "reverse"}},
    },
    {
        "name": "Chase bounce",
        "look": {"colour": _SOLID_WHITE, "video": _NO_VIDEO,
                  "effect": {"mode": "chase", "period_s": 1.2, "tail": 3, "axis": "x", "direction": "bounce"}},
    },
    {
        "name": "Sine wings",
        "look": {"colour": _SOLID_WHITE, "video": _NO_VIDEO,
                  "effect": {"mode": "sine_chase", "period_s": 1.2, "wavelength": 8, "axis": "x", "direction": "wings"}},
    },
]


def main() -> int:
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT_ROOT / "data" / "config.json"

    with open(config_path, encoding="utf-8-sig") as fh:
        cfg = json.load(fh)

    presets = list(cfg.get("presets", []))
    by_name = {p.get("name"): p["slot"] for p in presets}
    next_slot = max([p["slot"] for p in presets], default=-1) + 1

    seeded_slots = []
    for demo in DEMO_PRESETS:
        if demo["name"] in by_name:
            seeded_slots.append(by_name[demo["name"]])
            continue
        presets.append({"slot": next_slot, "name": demo["name"], "look": demo["look"]})
        seeded_slots.append(next_slot)
        next_slot += 1
    cfg["presets"] = presets

    cfg["randomizer"] = {
        "enabled": False,
        "events_per_hour": 20,
        "burst_s": 6,
        "targets": ["strips", "dishes"],
        "preset_slots": seeded_slots,
    }

    try:
        validated = validate_config(cfg)
    except ConfigError as exc:
        print(f"resulting config is invalid, nothing written:\n{exc}", file=sys.stderr)
        return 1

    save_config(str(config_path), validated)
    print(f"wrote {config_path} (randomizer enabled=False, preset_slots={seeded_slots})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
