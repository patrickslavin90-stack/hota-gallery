#!/usr/bin/env python3
"""Set the real standing schedule - replaces ELM's day-specific BAU
variants and seasonal T20 entries (per the user: run every night the
same, drop the old event-specific date ranges) with one uniform 7-night
schedule per target: the facade (default) and the dishes (zone), each
with its own BAU-hours look and overnight blackout, matching ELM's
original color split between the two without ELM's day-of-week variants.

Usage: python tools/set_schedule.py [config path, default data/config.json]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from hota_gallery.config import off_look, save_config, validate_config  # noqa: E402

ALL_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

NO_EFFECT_OR_VIDEO = {"effect": {"mode": "off"}, "video": {"mode": "off"}}


def _solid(color: dict) -> dict:
    return {"colour": {"mode": "solid", "color": color}, **NO_EFFECT_OR_VIDEO}


SCHEDULE = [
    {
        "name": "BAU - facade",
        "zone": None,
        "active_days": ALL_DAYS,
        "start_time": "16:30",
        "end_time": "23:59",
        "start_date": None,
        "end_date": None,
        "look": _solid({"r": 255, "g": 254, "b": 19, "w": 0}),
    },
    {
        "name": "Off - facade",
        "zone": None,
        "active_days": ALL_DAYS,
        "start_time": "00:01",
        "end_time": "06:59",
        "start_date": None,
        "end_date": None,
        "look": off_look(),
    },
    {
        "name": "BAU - dishes",
        "zone": "dishes",
        "active_days": ALL_DAYS,
        "start_time": "16:30",
        "end_time": "23:59",
        "start_date": None,
        "end_date": None,
        "look": _solid({"r": 255, "g": 255, "b": 255, "w": 0}),
    },
    {
        "name": "Off - dishes",
        "zone": "dishes",
        "active_days": ALL_DAYS,
        "start_time": "00:01",
        "end_time": "06:59",
        "start_date": None,
        "end_date": None,
        "look": off_look(),
    },
]


def main() -> int:
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT_ROOT / "data" / "config.json"

    with open(config_path, encoding="utf-8") as fh:
        cfg = json.load(fh)

    cfg["schedule"] = SCHEDULE
    validated = validate_config(cfg)
    save_config(str(config_path), validated)
    print(f"wrote {len(SCHEDULE)} schedule entries to {config_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
