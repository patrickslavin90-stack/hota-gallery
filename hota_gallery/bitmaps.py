"""Generic, venue-agnostic bitmap patterns for the "bitmap" look mode -
GrandMA/ELM-style canned shapes (like ELM's own basic generative bitmaps),
not photos or video. Each is a fixed-resolution 2D grid of brightness
floats (0..1) generated once at import time from plain math - no image
files, so the same library works identically at any venue. The look's own
`color` tints whichever shape is picked (see engine.py's "bitmap" mode) -
the bitmap only ever supplies shape, never color.
"""
from __future__ import annotations

import math
from typing import Callable, Dict, List

GRID_W = 32
GRID_H = 16


def _grid(fn: Callable[[float, float], float]) -> List[List[float]]:
    return [[fn(x / (GRID_W - 1), y / (GRID_H - 1)) for x in range(GRID_W)] for y in range(GRID_H)]


def _dot(x: float, y: float) -> float:
    return max(0.0, 1 - math.hypot(x - 0.5, y - 0.5) * 2.4)


def _ring(x: float, y: float) -> float:
    return max(0.0, 1 - abs(math.hypot(x - 0.5, y - 0.5) - 0.3) * 6)


def _bar_v(x: float, y: float) -> float:
    return max(0.0, 1 - abs(x - 0.5) * 6)


def _bar_h(x: float, y: float) -> float:
    return max(0.0, 1 - abs(y - 0.5) * 6)


def _cross(x: float, y: float) -> float:
    return max(_bar_v(x, y), _bar_h(x, y))


def _diamond(x: float, y: float) -> float:
    return max(0.0, 1 - (abs(x - 0.5) + abs(y - 0.5)) * 2.4)


def _checker(x: float, y: float) -> float:
    return 1.0 if (int(x * 8) + int(y * 4)) % 2 == 0 else 0.0


def _stripes_v(x: float, y: float) -> float:
    return (math.sin(x * math.pi * 8) + 1) / 2


def _stripes_h(x: float, y: float) -> float:
    return (math.sin(y * math.pi * 8) + 1) / 2


def _triangle(x: float, y: float) -> float:
    return 1.0 if abs(x - 0.5) <= (1 - y) * 0.5 else 0.0


def _heart(x: float, y: float) -> float:
    cx = (x - 0.5) * 2.2
    cy = (0.58 - y) * 2.2
    val = (cx ** 2 + cy ** 2 - 1) ** 3 - (cx ** 2) * (cy ** 3)
    return 1.0 if val <= 0 else 0.0


def _noise(x: float, y: float) -> float:
    # Deterministic fixed "static" - baked in once at import time, not
    # re-randomized per frame (that would just look like fuzzy nothing).
    n = math.sin(x * 127.1 + y * 311.7) * 43758.5453
    return n - math.floor(n)


BITMAPS: Dict[str, List[List[float]]] = {
    "dot": _grid(_dot),
    "ring": _grid(_ring),
    "bar_v": _grid(_bar_v),
    "bar_h": _grid(_bar_h),
    "cross": _grid(_cross),
    "diamond": _grid(_diamond),
    "checker": _grid(_checker),
    "stripes_v": _grid(_stripes_v),
    "stripes_h": _grid(_stripes_h),
    "triangle": _grid(_triangle),
    "heart": _grid(_heart),
    "noise": _grid(_noise),
}
