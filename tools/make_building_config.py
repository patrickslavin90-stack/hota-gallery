"""Make the main building controller's config: a run of identical fixtures
patched back to back, sent over sACN, laid out in a row (move them to where
they really are in the UI's Layout editor).

    python tools/make_building_config.py                      # 8 x Dim RGBW at 1.100
    python tools/make_building_config.py --count 12 --start 1.1

Writes data/building/config.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "building" / "config.json"
sys.path.insert(0, str(ROOT))
from hota_gallery.config import LED_CHANNELS, off_look, validate_config  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--count", type=int, default=8)
    ap.add_argument("--start", default="1.100", help="universe.address, numbered from 1 like the console")
    ap.add_argument("--type", default="DRGBW", choices=sorted(LED_CHANNELS), help="DRGBW = Dim RGBW, 5 ch")
    ap.add_argument("--name", default="Fixture")
    args = ap.parse_args()

    uni, addr = (int(x) for x in args.start.split("."))
    ch = LED_CHANNELS[args.type]
    if addr - 1 + args.count * ch > 512:
        raise SystemExit(f"{args.count} x {ch} ch from {args.start} runs past channel 512")

    fixtures, layout = [], {}
    for i in range(args.count):
        p = [float(i * 100), 0.0]
        f = {
            "name": f"{args.name} {i + 1}", "led_type": args.type,
            "universe": uni - 1, "address": addr - 1 + i * ch,   # stored 0-indexed, as everywhere
            "led_count": 1, "channels_per_led": ch, "points": [p, list(p)], "zone": None,
        }
        fixtures.append(f)
        layout[f"{f['universe']}:{f['address']}"] = [p, list(p)]

    gallery = json.loads((ROOT / "data" / "config.json").read_text(encoding="utf-8"))
    cfg = {
        "fps": 30.0, "bind_ip": None, "web_port": 8080,
        "device_name": "building", "venue": "building",
        "protocol": "sacn", "sacn_priority": 100, "sacn_destinations": [],
        "remote_pin": gallery.get("remote_pin", "1234"),
        "fixtures": fixtures, "zones": [],
        "current_look": {"default": off_look(), "fixtures": {}},
        "schedule": [], "presets": gallery.get("presets", []),
        "layouts": [{"name": "Building", "fixtures": layout}],
        "randomizer": {"enabled": False, "events_per_hour": 20.0, "burst_s": 6.0, "targets": [], "preset_slots": []},
        "clock_chime": {"enabled": False, "look": off_look(), "duration_s": 60.0, "start_hour": 0, "end_hour": 23},
    }
    validate_config(cfg)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    last = fixtures[-1]
    print(f"{args.count} x {args.type} ({ch} ch): {uni}.{addr:03d} - {uni}.{last['address'] + ch:03d}, sACN universe {uni}")
    for f in fixtures:
        print(f"  {f['name']:<12} {f['universe'] + 1}.{f['address'] + 1:03d}")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
