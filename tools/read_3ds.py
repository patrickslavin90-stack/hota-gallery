"""Minimal 3D Studio (.3ds) reader: every mesh object's name, vertices and
triangles (no materials, no hierarchy). Enough to pull building geometry
out of an architect's export without any 3D library.

    python tools/read_3ds.py "screenshots/HOTA Master Plan v2026.3ds"   # list objects
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import numpy as np

MAIN, EDITOR, OBJECT, TRIMESH, VERTS, FACES, MATRIX = 0x4D4D, 0x3D3D, 0x4000, 0x4100, 0x4110, 0x4120, 0x4160


def read(path: str | Path) -> list[dict]:
    """[{name, verts (n,3) float, faces (m,3) int}] - vertices in the file's
    world coordinates (3DS stores mesh vertices already transformed)."""
    data = Path(path).read_bytes()
    out: list[dict] = []

    def walk(start: int, end: int, obj: dict | None) -> None:
        p = start
        while p + 6 <= end:
            cid, ln = struct.unpack_from("<HI", data, p)
            if ln < 6 or p + ln > end:
                break
            body = p + 6
            if cid in (MAIN, EDITOR):
                walk(body, p + ln, None)
            elif cid == OBJECT:
                q = data.index(b"\0", body)
                o = {"name": data[body:q].decode("latin-1"), "verts": None, "faces": None}
                walk(q + 1, p + ln, o)
                if o["verts"] is not None and o["faces"] is not None:
                    out.append(o)
            elif cid == TRIMESH and obj is not None:
                walk(body, p + ln, obj)
            elif cid == VERTS and obj is not None:
                n = struct.unpack_from("<H", data, body)[0]
                obj["verts"] = np.frombuffer(data, "<f4", n * 3, body + 2).reshape(n, 3).astype(float)
            elif cid == FACES and obj is not None:
                n = struct.unpack_from("<H", data, body)[0]
                obj["faces"] = np.frombuffer(data, "<u2", n * 4, body + 2).reshape(n, 4)[:, :3].astype(int)
            p += ln

    walk(0, len(data), None)
    return out


if __name__ == "__main__":
    objs = read(sys.argv[1])
    allv = np.vstack([o["verts"] for o in objs])
    print(f"{len(objs)} objects, {len(allv)} vertices, {sum(len(o['faces']) for o in objs)} triangles")
    print("extent min", allv.min(0).round(1), "max", allv.max(0).round(1))
    for o in sorted(objs, key=lambda o: -len(o["faces"]))[:60]:
        v = o["verts"]
        print(f"{o['name']:24} {len(o['faces']):7} tris  min {v.min(0).round(1)} max {v.max(0).round(1)}")
