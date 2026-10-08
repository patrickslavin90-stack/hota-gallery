"""Hand-ported selection of ELM's own real generative effects - these are
plain-Python translations of actual GLSL fragment shaders found in the
venue's shader-pack folder (VIDEOassets/*.frag, Shadertoy-sourced), which
is what ELM's "generator" layers actually ran - not a from-scratch
reconstruction. Each is reduced to a function of a centered, aspect-
corrected (x, y) position and time, cheap enough to evaluate per-LED on a
Raspberry Pi. Heavier shaders (raymarched 3D scenes, large noise-driven
iteration loops - e.g. auroras.frag, blob.frag) were left out as
impractical for CPU-only per-pixel evaluation at show time; this set
covers the closed-form, no-loop ones.

Every function here takes (x, y, t, params) and returns a brightness
0..1 - the look's own `color` tints it (see engine.py's "shader" mode),
same convention as bitmaps.py. `params` holds whichever of ELM's own
exposed knobs (force/force2/n_items) the original shader used (its own
iForce/iForce2/iNbItems uniforms), kept under those same names/1-10
ranges so a staff member who used ELM would recognize them.
"""
from __future__ import annotations

import math
from typing import Callable, Dict


def _scale(value: float, lo: float, hi: float, to_lo: float, to_hi: float) -> float:
    return (value - lo) / (hi - lo) * (to_hi - to_lo) + to_lo


def _band_test(s: float, force: float, force2: float) -> float:
    """Shared "is this pixel inside the traveling band, and how bright"
    test from the *.frag scroll/radar family - `force` is the band's
    width, `force2` its edge softness, both on ELM's original 1-10 scale."""
    size_half = _scale(force, 1.0, 10.0, 0.05, 0.9) / 2.0
    fade = _scale(force2, 1.0, 10.0, 1.0, 0.0)
    start, end = 0.5 - size_half, 0.5 + size_half
    if start <= s <= end:
        s = 1.0 - 2.0 * abs(_scale(s, start, end, 0.0, 1.0) - 0.5)
    else:
        s = -1.0
    if s > 0.0 and s < fade:
        return s / fade if fade else 1.0
    return 1.0 if s > 0.0 else 0.0


def _scroll_band(dist: float, t: float, n_items: float, force: float, force2: float) -> float:
    """Core of circle/line/cross/square scroll.frag - a repeating band
    traveling outward from `dist` (however the caller measures distance),
    `n_items` controlling how many bands fit (ELM's iNbItems)."""
    ring_time = _scale(n_items, 1.0, 64.0, 10.0, 0.4)
    s = ((t * 2.2 - dist * 2.0) % ring_time) / ring_time
    return _band_test(s, force, force2)


def circle_scroll(x: float, y: float, t: float, p: Dict[str, float]) -> float:
    # circle scroll.frag: p *= 6.0; r = length(p)*1.8
    cx, cy = x * 6.0, y * 6.0
    dist = math.hypot(cx, cy) * 1.8
    return _scroll_band(dist, t, p.get("n_items", 20.0), p.get("force", 3.0), p.get("force2", 5.0))


def line_scroll(x: float, y: float, t: float, p: Dict[str, float]) -> float:
    # line scroll.frag: p *= 6.0; p.y = 0.5 (fixed); r = length(p)*1.8
    cx = x * 6.0
    dist = math.hypot(cx, 0.5) * 1.8
    return _scroll_band(dist, t, p.get("n_items", 20.0), p.get("force", 3.0), p.get("force2", 5.0))


def cross_scroll(x: float, y: float, t: float, p: Dict[str, float]) -> float:
    # cross scroll.frag: p.x=p.y=min(|p.x|,|p.y|); r = length(p)*1.8
    cx, cy = x * 6.0, y * 6.0
    m = min(abs(cx), abs(cy))
    dist = math.hypot(m, m) * 1.8
    return _scroll_band(dist, t, p.get("n_items", 20.0), p.get("force", 3.0), p.get("force2", 5.0))


def square_scroll(x: float, y: float, t: float, p: Dict[str, float]) -> float:
    # square scroll.frag: p.x=p.y=max(|p.x|,|p.y|); r = length(p)*1.8
    cx, cy = x * 6.0, y * 6.0
    m = max(abs(cx), abs(cy))
    dist = math.hypot(m, m) * 1.8
    return _scroll_band(dist, t, p.get("n_items", 20.0), p.get("force", 3.0), p.get("force2", 5.0))


def radar(x: float, y: float, t: float, p: Dict[str, float]) -> float:
    # radar.frag: p *= 2.0 (done by caller via x,y already centered);
    # rotating sweep arm(s) in polar coordinates.
    cx, cy = x * 2.0, y * 2.0
    n_sweeps = max(1.0, p.get("n_items", 1.0))
    tt = t * 0.75  # speed = 1.5/2.0 in the source
    two_pi = 2.0 * math.pi
    slice_angle = two_pi / n_sweeps
    ang = (math.atan2(cy, cx) + tt) % slice_angle
    s = ang / slice_angle
    return _band_test(s, p.get("force", 3.0), p.get("force2", 5.0))


def plasma(x: float, y: float, t: float, p: Dict[str, float]) -> float:
    # plasma white.frag, minus the screen-space-derivative specular pass
    # (GPU-only, no per-pixel-independent equivalent).
    cx, cy = x * 2.0, y * 2.0
    tt = t * 0.45
    px, py = cx * 4.0 + tt * 0.3, cy * 4.0 + tt * 0.3
    k = 0.1 + math.cos(py + math.sin(0.148 - tt)) + 2.4 + tt
    w = 0.9 + math.sin(px + math.cos(0.628 + tt)) - 0.7 + tt
    d = math.hypot(px, py)
    s = 7.0 * math.cos(d + w) * math.sin(k + w)
    val = 0.5 + 0.5 * math.cos(s + 0.5)
    return max(0.0, min(1.0, val))


SHADERS: Dict[str, Callable[[float, float, float, Dict[str, float]], float]] = {
    "circle_scroll": circle_scroll,
    "line_scroll": line_scroll,
    "cross_scroll": cross_scroll,
    "square_scroll": square_scroll,
    "radar": radar,
    "plasma": plasma,
}
