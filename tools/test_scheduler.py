#!/usr/bin/env python3
"""Quick sanity check of scheduler.active_entry's matching logic -
day/time/date window, per-target last-match-wins - against a few
constructed scenarios, before trusting it against the live service."""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hota_gallery.scheduler import active_entry  # noqa: E402

bau_facade = {
    "name": "BAU", "zone": None,
    "active_days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
    "start_time": "16:30", "end_time": "23:59",
    "start_date": None, "end_date": None,
    "look": {"mode": "solid", "color": {"r": 255, "g": 254, "b": 19, "w": 0}},
}
off_facade = {
    "name": "Off", "zone": None,
    "active_days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
    "start_time": "00:01", "end_time": "06:59",
    "start_date": None, "end_date": None,
    "look": {"mode": "off"},
}
bau_dishes = {
    "name": "BAU dishes", "zone": "dishes",
    "active_days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
    "start_time": "16:30", "end_time": "23:59",
    "start_date": None, "end_date": None,
    "look": {"mode": "solid", "color": {"r": 255, "g": 255, "b": 255, "w": 0}},
}
disabled_override = {
    "name": "disabled-test", "zone": None, "enabled": False,
    "active_days": ["wed"],
    "start_time": "16:30", "end_time": "23:59",
    "start_date": None, "end_date": None,
    "look": {"mode": "solid", "color": {"r": 0, "g": 0, "b": 0, "w": 0}},
}
schedule = [bau_facade, off_facade, bau_dishes, disabled_override]

cases = [
    ("18:00 default target - BAU window", datetime.datetime(2026, 10, 7, 18, 0), None, "BAU"),
    ("18:00 dishes target - its own BAU window", datetime.datetime(2026, 10, 7, 18, 0), "dishes", "BAU dishes"),
    ("03:00 default target - overnight Off window", datetime.datetime(2026, 10, 7, 3, 0), None, "Off"),
    ("03:00 dishes target - nothing scheduled for dishes at this hour", datetime.datetime(2026, 10, 7, 3, 0), "dishes", None),
    ("12:00 default target - daytime gap, nothing matches", datetime.datetime(2026, 10, 7, 12, 0), None, None),
    ("18:00 default target - disabled entry is skipped, BAU still wins", datetime.datetime(2026, 10, 7, 18, 0), None, "BAU"),
]

failures = 0
for description, now, target, expected_name in cases:
    entry = active_entry(schedule, target, now)
    got_name = entry["name"] if entry else None
    ok = got_name == expected_name
    failures += not ok
    print(f"{'OK  ' if ok else 'FAIL'} {description}: expected {expected_name!r}, got {got_name!r}")

print(f"\n{len(cases) - failures}/{len(cases)} passed")
sys.exit(1 if failures else 0)
