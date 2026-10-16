"""Linework version of the outdoor stage wall, for 2D drawings (the GIF
export's front elevation): white lines on transparent - the panel joints,
the stage opening and the wall's outline - in the same style as the
gallery's traced elevation sheets.

Made from the 3D view's wall texture (hota_gallery/static/vos-wall.png, by
tools/build_vos_model.py), so it needs nothing but that: the joints are the
dark lines in it, the outline is the edge of its alpha. Same size and
(u, v) extent as the texture, so anything that places the texture places
this too.

    python tools/make_vos_linework.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "hota_gallery" / "static" / "vos-wall.png"
OUT = ROOT / "hota_gallery" / "static" / "vos-wall-lines.png"


def main() -> int:
    im = np.array(Image.open(SRC).convert("RGBA")).astype(float)
    rgb, alpha = im[..., :3], im[..., 3] > 127
    lum = rgb.mean(axis=2)
    # A joint is a pixel well below the panel surface around it. Compare with
    # the local brightness (a wide blur ignores the 2-3 px joints), so the
    # light concrete and the darker perforated metal both work.
    surface = np.array(Image.fromarray(np.where(alpha, lum, 0).astype(np.uint8)).filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.GaussianBlur(4))).astype(float)
    joints = alpha & (lum < surface * 0.72)
    # The perforated panels' dot pattern is isolated 2x2 specks; joints are
    # continuous lines. Keep a pixel only if its 5x5 neighbourhood has more
    # joint pixels than one speck could give.
    n = np.array(Image.fromarray((joints * 255).astype(np.uint8)).filter(ImageFilter.BoxBlur(2))).astype(float) / 255 * 25
    joints &= n >= 6.5
    # Outline: the edge of the wall's alpha (top edge, ends).
    a = Image.fromarray((alpha * 255).astype(np.uint8))
    edge = (np.array(a.filter(ImageFilter.MaxFilter(3))) > 0) & ~(np.array(a.filter(ImageFilter.MinFilter(3))) > 0)
    out = np.zeros(alpha.shape + (4,), np.uint8)
    out[..., :3] = 245
    out[..., 3] = np.where(edge, 235, np.where(joints, 120, 0))
    Image.fromarray(out, "RGBA").save(OUT, optimize=True)
    print(f"wrote {OUT.relative_to(ROOT)}: {joints.sum()} joint px, {edge.sum()} outline px, {OUT.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
