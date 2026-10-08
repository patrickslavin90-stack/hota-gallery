#!/usr/bin/env python3
"""One-time (re-run after editing bitmaps.py): render each procedural
bitmap pattern to a small static JPEG for the Live tab's Video section -
static files are cheaper for the browser than rebuilding a canvas from
raw grid data client-side, and these patterns never change at runtime.

Usage: python tools/generate_bitmap_thumbs.py
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from PIL import Image  # noqa: E402

from hota_gallery.bitmaps import BITMAPS  # noqa: E402

OUT_DIR = PROJECT_ROOT / "hota_gallery" / "static" / "bitmap_thumbs"
SCALE = 4  # 32x16 source -> 128x64 thumbnail, nearest-neighbour to keep edges crisp


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, grid in BITMAPS.items():
        h, w = len(grid), len(grid[0])
        img = Image.new("L", (w, h))
        img.putdata([max(0, min(255, round(v * 255))) for row in grid for v in row])
        img = img.resize((w * SCALE, h * SCALE), Image.NEAREST)
        out_path = OUT_DIR / f"{name}.jpg"
        img.convert("RGB").save(out_path, "JPEG", quality=85)
        print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
