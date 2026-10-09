#!/usr/bin/env python3
"""End-to-end check that the web UI and the Python controller still fit
together: the controller serves the UI and every file it loads, and every
API call the UI makes round-trips through webapp.py into the engine.

    python tools/check_ui_api.py                       # http://127.0.0.1:8765
    python tools/check_ui_api.py http://192.168.1.20:8080

It changes looks, presets and the schedule while it runs, then restores the
controller's config exactly as it found it. Don't run it against the live
gallery during public hours - the façade will flash briefly.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765").rstrip("/")
passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS  {name}")
    else:
        failed += 1
        print(f"FAIL  {name}  {detail}")


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"} if body is not None else {})
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, resp.headers.get("Content-Type", ""), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()


def main() -> int:
    for _ in range(50):
        try:
            req("GET", "/api/status")
            break
        except Exception:
            time.sleep(0.2)
    else:
        print(f"Nothing answering at {BASE}")
        return 2

    original = json.loads(req("GET", "/api/config")[2])
    try:
        run(original)
    finally:
        s, _, b = req("PUT", "/api/config", original)
        print(f"\nRestored the original config ({s}).")
    print(f"{passed} passed, {failed} failed")
    return 1 if failed else 0


def run(cfg):
    # --- the page and everything it loads -----------------------------------
    s, ct, b = req("GET", "/")
    check("GET / serves the UI", s == 200 and b"static/app.js" in b)
    for f in ["app.js", "app.css", "engine.js", "backend.js", "export.js", "elevations.json",
              "building-layout.json", "hota-logo.svg", "demo-config.json", "legacy.html",
              "vendor/gifenc.esm.js", "bitmap_thumbs/heart.jpg"]:
        s, ct, b = req("GET", "/static/" + f)
        check(f"GET /static/{f}", s == 200 and len(b) > 100, f"{s} {len(b)}")
    for f in ["view3d.js", "building3d.json", "vendor/three/three.module.min.js"]:  # 3D branch only
        s, _, b = req("GET", "/static/" + f)
        print(f"INFO  /static/{f}: {'present' if s == 200 else 'not on this branch'}")

    # --- API ---------------------------------------------------------------
    s, ct, _ = req("GET", "/api/status")
    check("GET /api/status is JSON (how the UI detects a controller)", s == 200 and "json" in ct)
    check("config has fixtures", len(cfg["fixtures"]) > 0)

    red = {"colour": {"mode": "solid", "color": {"r": 213, "g": 19, "b": 77, "w": 0}}, "effect": {"mode": "off"}, "video": {"mode": "off"}}
    req("PUT", "/api/look/selection", {"fixtures": list(cfg["current_look"]["fixtures"].keys()), "look": None})
    s, _, _ = req("PUT", "/api/look", red)
    check("PUT /api/look", s == 200)
    prev = json.loads(req("GET", "/api/preview")[2])
    check("engine output follows the look (every LED 213,19,77)",
          all(c[:3] == [213, 19, 77] for p in prev for c in p["colors"]), str(prev[0]["colors"][:1]))

    k0 = f'{cfg["fixtures"][0]["universe"]}:{cfg["fixtures"][0]["address"]}'
    blue = {"colour": {"mode": "solid", "color": {"r": 0, "g": 0, "b": 255, "w": 0}}}
    s, _, _ = req("PUT", "/api/look/selection", {"fixtures": [k0], "look": blue})
    prev = json.loads(req("GET", "/api/preview")[2])
    check("PUT /api/look/selection reaches only that fixture",
          s == 200 and prev[0]["colors"][0][:3] == [0, 0, 255] and prev[1]["colors"][0][:3] == [213, 19, 77])
    s, _, b = req("PUT", "/api/look/selection", {"fixtures": [k0], "look": None})
    check("revert a selection (look: null)", s == 200 and k0 not in json.loads(b)["fixtures"])

    s, _, _ = req("PUT", "/api/presets", cfg["presets"] + [{"slot": 999, "name": "check", "look": red}])
    check("PUT /api/presets", s == 200)
    entry = {"name": "check entry", "zone": None, "enabled": True, "active_days": ["sat"], "start_time": "18:00",
             "end_time": "22:00", "start_date": None, "end_date": None, "look": red}
    s, _, _ = req("PUT", "/api/schedule", cfg["schedule"] + [entry])
    check("PUT /api/schedule", s == 200)
    bad = dict(entry, name="bad", start_time="22:00", end_time="18:00")
    s, _, b = req("PUT", "/api/schedule", cfg["schedule"] + [bad])
    check("invalid schedule rejected with an error message", s == 400 and b"error" in b)
    s, _, _ = req("PUT", "/api/randomizer", cfg["randomizer"])
    check("PUT /api/randomizer", s == 200)
    s, _, _ = req("PUT", "/api/clock_chime", cfg["clock_chime"])
    check("PUT /api/clock_chime", s == 200)
    s, _, _ = req("PUT", "/api/layouts", cfg["layouts"])
    check("PUT /api/layouts", s == 200)
    s, _, b = req("GET", "/api/artnet/status")
    check("GET /api/artnet/status", s == 200 and "universes" in json.loads(b))
    s, _, b = req("GET", "/api/artnet/discover")
    check("GET /api/artnet/discover", s == 200 and isinstance(json.loads(b), list))
    c2 = json.loads(req("GET", "/api/config")[2])
    c2["device_name"] = (c2.get("device_name") or "controller") + "-check"
    s, _, b = req("PUT", "/api/config", c2)
    check("PUT /api/config", s == 200 and json.loads(b)["device_name"].endswith("-check"))


if __name__ == "__main__":
    sys.exit(main())
