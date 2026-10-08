#!/usr/bin/env python3
"""Run the ELM import, print a summary, and write data/fixtures.json.

Usage: python tools/import_elm.py "<path to .elm file>"
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hota_gallery.elm_import import find_overlaps, import_elm, resolve_overlaps, summarize  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python tools/import_elm.py <path-to-elm-file>", file=sys.stderr)
        return 1

    elm_path = sys.argv[1]
    raw = import_elm(elm_path)
    print(summarize(raw))

    clean, report = resolve_overlaps(raw)
    print(f"\ncleanup: {len(raw)} -> {len(clean)} fixtures")
    for line in report:
        print(f"  {line}")

    remaining = find_overlaps(clean)
    if remaining:
        print(f"\n*** {len(remaining)} overlap(s) still remain after cleanup - needs a look ***")

    out_path = Path(__file__).resolve().parent.parent / "data" / "fixtures.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump([asdict(fx) for fx in clean], fh, indent=2)
    print(f"\nwrote {out_path} ({len(clean)} fixtures)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
