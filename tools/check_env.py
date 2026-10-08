import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hota_gallery import artnet, elm_import  # noqa: F401

print("modules import OK")

data_path = Path(__file__).resolve().parent.parent / "data" / "fixtures.json"
fixtures = json.load(open(data_path))
print(f"{len(fixtures)} fixtures loaded from {data_path}")
