#!/usr/bin/env python3
"""Reset current_look to a clean off state without touching anything else
(fixtures, zones, schedule) - just clears whatever manual/test color was
last left active."""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from hota_gallery.config import off_look, save_config, validate_config  # noqa: E402


def main() -> int:
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT_ROOT / "data" / "config.json"
    with open(config_path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    cfg["current_look"] = {"default": off_look(), "fixtures": {}}
    save_config(str(config_path), validate_config(cfg))
    print(f"reset current_look to off in {config_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
