#!/usr/bin/env python3
"""One-time bootstrap: data/fixtures.json + CONFIG_DEFAULTS -> a real,
validated config.json. From this point on, config.json (not fixtures.json)
is the single source of truth - the layout editor edits it directly.

Usage: python tools/init_config.py [output path, default data/config.json]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hota_gallery.config import CONFIG_DEFAULTS, save_config  # noqa: E402


def main() -> int:
    project_root = Path(__file__).resolve().parent.parent
    fixtures_path = project_root / "data" / "fixtures.json"
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else project_root / "data" / "config.json"

    with open(fixtures_path, encoding="utf-8") as fh:
        fixtures = json.load(fh)

    cfg = dict(CONFIG_DEFAULTS)
    cfg["fixtures"] = fixtures
    cfg["device_name"] = "gallery"

    save_config(str(out_path), cfg)
    print(f"wrote {out_path} ({len(fixtures)} fixtures)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
