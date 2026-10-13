"""Build the outdoor stage (VOS) controller config and 3D data from the
lighting department's files in data/venue_assets/vos/:

  vos strips and dots.mvr    patch: 609 RGB pixels (3 ch), sACN universes 11-16
  facade strip.xml           grandMA3 layout: 2D position of every strip pixel
  facade dot.xml             grandMA3 layout: 2D position of every dot
  HOTA - VOS - LX PLAN.xml   Vectorworks export: real 3D position of the dots

The MVR carries the patch only (no <Matrix> positions), so 2D comes from the
MA layouts, joined to the patch by fixture name. (Not FixtureID: "Strip D
2-160" have different IDs in the layout than in the patch.)

3D: dots use their real Vectorworks position, joined by absolute DMX
address. Strips aren't in the Vectorworks file, so their front-on position
(X across, Z up) comes from a least-squares fit of the MA layout to the
Vectorworks dots, and their depth (Y) from the facade's plan line, which
the dots also give us: flat across the middle, angled back ~35 deg at
each side. The MA layout was placed by hand, so expect strip positions to
be out by up to a metre or so. A model of the stage shell would fix that.

Writes data/vos/config.json, plus hota_gallery/static/vos3d.json (3D) and
hota_gallery/static/vos-demo-config.json (demo mode, ?venue=vos).

    python tools/import_vos.py
"""

from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "venue_assets" / "vos"
OUT_CONFIG = ROOT / "data" / "vos" / "config.json"
OUT_3D = ROOT / "hota_gallery" / "static" / "vos3d.json"
OUT_DEMO = ROOT / "hota_gallery" / "static" / "vos-demo-config.json"  # demo mode, ?venue=vos
GALLERY_CONFIG = ROOT / "data" / "config.json"

sys.path.insert(0, str(ROOT))
from hota_gallery.config import off_look, validate_config  # noqa: E402

# MA fixture-name prefix -> zone. The MA patch calls the right-hand dots
# (as seen from the audience) "PS LED Dot" and the left-hand ones plain
# "LED Dot". The UI treats any zone ending in "dots" as dots.
ZONES = [
    ("Strip A", re.compile(r"^Strip \d+$")),
    ("Strip B", re.compile(r"^Strip B \d+$")),
    ("Strip C", re.compile(r"^Strip C \d+$")),
    ("Strip D", re.compile(r"^Strip D \d+$")),
    ("Left dots", re.compile(r"^LED Dot \d+$")),
    ("Right dots", re.compile(r"^PS LED Dot \d+$")),
]


def zone_of(name: str) -> str:
    for zone, rx in ZONES:
        if rx.match(name):
            return zone
    raise ValueError(f"no zone for fixture {name!r}")


def read_mvr(path: Path) -> dict:
    """name -> 1-based absolute DMX address."""
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("GeneralSceneDescription.xml"))
    out = {}
    for f in root.iter("Fixture"):
        mode = f.findtext("GDTFMode")
        if mode != "8 bit":
            raise ValueError(f"{f.get('name')}: expected the 8 bit RGB mode, got {mode!r}")
        out[f.get("name")] = int(f.findtext("Addresses/Address"))
    return out


def read_ma_layout(path: Path) -> dict:
    """name -> (x, y) in MA layout units, y up."""
    root = ET.parse(path).getroot()
    return {e.get("Name"): (int(e.get("PosX")), int(e.get("PosY"))) for e in root.iter("Element")}


def read_vectorworks(path: Path) -> dict:
    """1-based absolute address -> (X, Y, Z) mm, for every addressed instrument."""
    text = path.read_text(encoding="utf-8", errors="replace")
    out = {}
    for _, block in re.findall(r"<(UID_[0-9_]+)>(.*?)</\1>", text, re.S):
        def field(k):
            m = re.search(rf"<{k}>([^<]*)<", block)
            return m.group(1) if m else ""
        addr = field("Absolute_Address")
        if addr.isdigit() and int(addr) > 0 and field("X_Location_mm"):
            out[int(addr)] = tuple(float(field(k)) for k in ("X_Location_mm", "Y_Location_mm", "Z_Location_mm"))
    return out


def lstsq_affine(src, dst):
    """Fit dst ~ a*sx + b*sy + c, one output column at a time (plain normal
    equations, so no numpy needed on the Pi)."""
    def solve(A, y):
        n = len(A[0])
        M = [[sum(r[i] * r[j] for r in A) for j in range(n)] for i in range(n)]
        v = [sum(r[i] * t for r, t in zip(A, y)) for i in range(n)]
        for i in range(n):  # Gauss-Jordan
            p = max(range(i, n), key=lambda r: abs(M[r][i]))
            M[i], M[p], v[i], v[p] = M[p], M[i], v[p], v[i]
            for r in range(n):
                if r != i:
                    k = M[r][i] / M[i][i]
                    M[r] = [a - k * b for a, b in zip(M[r], M[i])]
                    v[r] -= k * v[i]
        return [v[i] / M[i][i] for i in range(n)]
    A = [[x, y, 1.0] for x, y in src]
    return [solve(A, [d[k] for d in dst]) for k in range(len(dst[0]))]


def plan_line(points):
    """Facade plan line (X -> Y) from the dots: a piecewise-linear profile
    through the median depth of the dots in 2 m slices across the stage."""
    pts = sorted(points)
    lo, hi = pts[0][0], pts[-1][0]
    knots = []
    x = lo
    while x <= hi + 1e-6:
        ys = sorted(p[1] for p in pts if abs(p[0] - x) <= 1000)
        if ys:
            knots.append((x, ys[len(ys) // 2]))
        x += 2000
    return knots


def interp(knots, x):
    if x <= knots[0][0]:
        (x0, y0), (x1, y1) = knots[0], knots[1]
    elif x >= knots[-1][0]:
        (x0, y0), (x1, y1) = knots[-2], knots[-1]
    else:
        i = next(i for i in range(1, len(knots)) if knots[i][0] >= x)
        (x0, y0), (x1, y1) = knots[i - 1], knots[i]
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def main() -> int:
    patch = read_mvr(SRC / "vos strips and dots.mvr")
    layout = {**read_ma_layout(SRC / "facade strip.xml"), **read_ma_layout(SRC / "facade dot.xml")}
    vw = read_vectorworks(SRC / "HOTA - VOS - LX PLAN.xml")

    unplaced = sorted(set(patch) - set(layout))
    if unplaced:
        raise SystemExit(f"patched but not in the MA layouts: {unplaced}")
    unpatched = sorted(set(layout) - set(patch))

    fixtures = []
    for name, absolute in sorted(patch.items(), key=lambda kv: kv[1]):
        x, y = layout[name]
        universe, address = divmod(absolute - 1, 512)
        # Screen convention, like the gallery's: y grows downwards.
        p = [float(x), float(-y)]
        fixtures.append({
            "name": name, "led_type": "RGB", "universe": universe, "address": address,
            "led_count": 1, "channels_per_led": 3, "points": [p, list(p)], "zone": zone_of(name),
        })

    # --- 3D ---------------------------------------------------------------
    pairs = [(layout[n], vw[a]) for n, a in patch.items() if "Dot" in n and a in vw]
    fit = lstsq_affine([l for l, _ in pairs], [(q[0], q[2]) for _, q in pairs])
    to_xz = lambda x, y: (fit[0][0] * x + fit[0][1] * y + fit[0][2], fit[1][0] * x + fit[1][1] * y + fit[1][2])
    resid = [((to_xz(*l)[0] - q[0]) ** 2 + (to_xz(*l)[1] - q[2]) ** 2) ** 0.5 for l, q in pairs]
    knots = plan_line([(q[0], q[1]) for _, q in pairs])

    pos3d = {}
    for f in fixtures:
        key = f"{f['universe']}:{f['address']}"
        absolute = f["universe"] * 512 + f["address"] + 1
        if absolute in vw and f["zone"].endswith("dots"):
            pos3d[key] = [round(v) for v in vw[absolute]]
        else:
            X, Z = to_xz(*layout[f["name"]])
            pos3d[key] = [round(X), round(interp(knots, X)), round(Z)]
    estimated_dots = [f["name"] for f in fixtures if "Dot" in f["name"] and f["universe"] * 512 + f["address"] + 1 not in vw]
    vw_unpatched = sorted(a for a in vw if a not in set(patch.values()))

    # --- config -----------------------------------------------------------
    gallery = json.loads(GALLERY_CONFIG.read_text(encoding="utf-8"))
    keys = {f"{f['universe']}:{f['address']}": f["points"] for f in fixtures}
    cfg = {
        "fps": 30.0,
        "bind_ip": None,
        "web_port": 8080,
        "device_name": "vos",
        "venue": "vos",
        "protocol": "sacn",
        "sacn_priority": 100,
        "sacn_destinations": [],
        "remote_pin": gallery.get("remote_pin", "1234"),
        "fixtures": fixtures,
        "zones": [{"name": z} for z, _ in ZONES],
        "current_look": {"default": off_look(), "fixtures": {}},
        "schedule": [],
        # Looks are venue-independent, so start from the gallery's library.
        "presets": gallery.get("presets", []),
        "layouts": [{"name": "Facade", "fixtures": keys}],
        "randomizer": {"enabled": False, "events_per_hour": 20.0, "burst_s": 6.0, "targets": [], "preset_slots": []},
        "clock_chime": {"enabled": False, "look": off_look(), "duration_s": 60.0, "start_hour": 0, "end_hour": 23},
    }
    validate_config(cfg)
    OUT_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    OUT_CONFIG.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    OUT_DEMO.write_text(json.dumps(cfg) + "\n", encoding="utf-8")

    xs = [p[0] for p in pos3d.values()]
    OUT_3D.write_text(json.dumps({
        "source": "tools/import_vos.py: dots from the Vectorworks LX plan, strips fitted from the grandMA3 layout",
        "units": "mm; X across the stage (audience left to right), Y depth (negative = upstage), Z up",
        "plan": [[round(x), round(y)] for x, y in knots],
        "extent": {"min_x": min(xs), "max_x": max(xs), "max_z": max(p[2] for p in pos3d.values())},
        "fixtures": pos3d,
    }) + "\n", encoding="utf-8")

    by_zone = {}
    for f in fixtures:
        by_zone[f["zone"]] = by_zone.get(f["zone"], 0) + 1
    print(f"{len(fixtures)} fixtures on universes {sorted({f['universe'] + 1 for f in fixtures})}: {by_zone}")
    print(f"in the MA layout but not patched (left out): {unpatched}")
    print(f"layout -> 3D fit from {len(pairs)} dots: rms {(sum(r * r for r in resid) / len(resid)) ** 0.5:.0f} mm, max {max(resid):.0f} mm")
    print(f"dots with no Vectorworks position (placed from the layout fit): {estimated_dots}")
    print(f"Vectorworks addresses not in the patch: {vw_unpatched}")
    print(f"wrote {OUT_CONFIG.relative_to(ROOT)}, {OUT_3D.relative_to(ROOT)} and {OUT_DEMO.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
