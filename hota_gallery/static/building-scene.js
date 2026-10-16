/* HOTA main building, street view: a simple sketch of what the building
 * controller lights, for the live view and the GIF export (window.HotaBuildingScene).
 *
 * Traced by eye from a street-view photo of the HOTA Central side (no plans
 * yet), so the proportions are close, not surveyed. Three surfaces: the long
 * wall the eight bars wash, the fly tower's front above it with the HOTA
 * sign, and the tower's side with the artwork, seen at an angle. Plus the
 * HOTA Central blade sign and the billboard (with its own three lights,
 * which aren't ours). No trees.
 *
 * World units are metres, x left to right, y down from the ground (so the
 * wall top is at y = -9). The default fixture positions ("Street view"
 * layout) are the bars at the foot of the wall, left of the billboard,
 * evenly spaced, fixture 1 at the left; drag them in Edit layout if the
 * real order differs.
 */
(() => {
  "use strict";

  const WALL = { x0: 0, x1: 31.8, top: -9 };
  const BILLBOARD = { x0: 20.4, x1: 27.9, y0: -8.6, y1: -2.3 };
  const BLADE = { x0: 29.0, x1: 30.5, y0: -9.6, y1: -3.6 };       // HOTA Central sign
  const TOWER_FRONT = [[8.8, -15.6], [22.1, -15.7], [22.1, -10.9], [8.8, -10.6]];
  const TOWER_SIDE = [[4.0, -15.0], [8.8, -15.6], [8.8, -10.6], [4.0, -10.3]];
  const ROOF = [[2.6, -10.2], [4.0, -10.3], [8.8, -10.6], [22.1, -10.9], [31.8, -10.5], [31.8, WALL.top], [0, WALL.top]];
  const BARS = { x0: 1.4, x1: 18.8, y: -0.3, n: 8 };
  const box = { minX: -1.2, maxX: 33.2, minY: -16.6, maxY: 1.4 };

  // Images next to this script, loaded once.
  const SCRIPT_URL = document.currentScript && document.currentScript.src;
  let logo = null;
  const ready = new Promise(res => {
    const img = new Image();
    img.onload = () => { logo = img; res(); };
    img.onerror = () => res();
    img.src = new URL("hota-logo.svg", SCRIPT_URL || location.href).href;
  });

  // Default layout: fixtures in address order along the foot of the wall.
  function layout(fixtures, name = "Street view") {
    const fx = [...fixtures].sort((a, b) => a.universe - b.universe || a.address - b.address);
    const out = {};
    fx.forEach((f, i) => {
      const t = fx.length > 1 ? i / (fx.length - 1) : 0.5;
      const x = +(BARS.x0 + (BARS.x1 - BARS.x0) * t).toFixed(2);
      out[`${f.universe}:${f.address}`] = [[x, BARS.y], [x, BARS.y]];
    });
    return { name, fixtures: out };
  }

  // The static drawing. X, Y map world metres to canvas pixels, s is pixels
  // per metre, u a line-width unit (1 on screen, the card's scale in the
  // export). opts.artwork: an image for the billboard instead of the logo.
  function drawBase(c, X, Y, s, u = 1, opts = {}) {
    const poly = pts => { c.beginPath(); pts.forEach(([x, y], i) => (i ? c.lineTo(X(x), Y(y)) : c.moveTo(X(x), Y(y)))); c.closePath(); };
    const rect = r => { c.beginPath(); c.rect(X(r.x0), Y(r.y0), X(r.x1) - X(r.x0), Y(r.y1) - Y(r.y0)); };
    const line = "rgba(245,245,245,.9)", faint = "rgba(245,245,245,.35)";
    c.save();
    c.lineJoin = "round";

    // Roof between the tower and the wall: just its edge, faint.
    poly(ROOF); c.fillStyle = "rgba(245,245,245,.025)"; c.fill();
    c.strokeStyle = "rgba(245,245,245,.22)"; c.lineWidth = 1 * u; c.stroke();

    // Tower side with the artwork: soft colour blocks like the mural.
    poly(TOWER_SIDE); c.fillStyle = "rgba(245,245,245,.03)"; c.fill();
    c.save(); poly(TOWER_SIDE); c.clip();
    const art = [
      ["rgba(216,140,200,.30)", [[3.8, -15.6], [9, -15.8], [9, -13.9], [3.8, -13.1]]],
      ["rgba(70,140,230,.32)", [[3.8, -13.1], [9, -13.9], [9, -12.6], [3.8, -12.0]]],
      ["rgba(240,200,80,.32)", [[3.8, -12.0], [6.5, -13.0], [9, -12.6], [9, -11.6], [3.8, -11.2]]],
      ["rgba(230,80,60,.34)", [[7.2, -12.6], [9, -12.8], [9, -11.0], [7.2, -10.9]]],
      ["rgba(90,190,170,.28)", [[3.8, -11.2], [9, -11.6], [9, -10.4], [3.8, -10.2]]],
    ];
    for (const [col, pts] of art) { poly(pts); c.fillStyle = col; c.fill(); }
    c.restore();
    poly(TOWER_SIDE); c.strokeStyle = line; c.lineWidth = 1.3 * u; c.stroke();

    // Tower front with the HOTA sign.
    poly(TOWER_FRONT); c.fillStyle = "rgba(245,245,245,.04)"; c.fill();
    c.strokeStyle = line; c.lineWidth = 1.3 * u; c.stroke();
    if (logo) {
      const lw = (11.4) * s, lh = lw * (logo.naturalHeight / logo.naturalWidth || 51 / 343);
      c.globalAlpha = 0.9; c.drawImage(logo, X(9.9), Y(-13.3) - lh / 2, lw, lh); c.globalAlpha = 1;
    }

    // The wall the bars wash.
    c.beginPath(); c.rect(X(WALL.x0), Y(WALL.top), X(WALL.x1) - X(WALL.x0), Y(0) - Y(WALL.top));
    c.fillStyle = "rgba(245,245,245,.035)"; c.fill();
    c.strokeStyle = line; c.lineWidth = 1.4 * u; c.stroke();

    // Billboard: artwork if given, otherwise the HOTA logo; three lights on top.
    rect(BILLBOARD); c.fillStyle = "#1b1a1a"; c.fill();
    const art2 = opts.artwork;
    const bw = X(BILLBOARD.x1) - X(BILLBOARD.x0), bh = Y(BILLBOARD.y1) - Y(BILLBOARD.y0);
    if (art2 && art2.naturalWidth) {
      // Cover: fill the board, cropping the artwork's overhang.
      const k = Math.max(bw / art2.naturalWidth, bh / art2.naturalHeight);
      const sw = bw / k, sh = bh / k;
      c.drawImage(art2, (art2.naturalWidth - sw) / 2, (art2.naturalHeight - sh) / 2, sw, sh, X(BILLBOARD.x0), Y(BILLBOARD.y0), bw, bh);
    } else if (logo) {
      const lw = bw * 0.7, lh = lw * (logo.naturalHeight / logo.naturalWidth || 51 / 343);
      c.globalAlpha = 0.85; c.drawImage(logo, X(BILLBOARD.x0) + (bw - lw) / 2, Y(BILLBOARD.y0) + (bh - lh) / 2, lw, lh); c.globalAlpha = 1;
    }
    rect(BILLBOARD); c.strokeStyle = "rgba(245,245,245,.7)"; c.lineWidth = 1.1 * u; c.stroke();
    c.strokeStyle = faint; c.lineWidth = 1 * u; c.lineCap = "round";
    for (const t of [0.17, 0.5, 0.83]) {
      const x = BILLBOARD.x0 + (BILLBOARD.x1 - BILLBOARD.x0) * t;
      c.beginPath(); c.moveTo(X(x), Y(BILLBOARD.y0)); c.lineTo(X(x), Y(BILLBOARD.y0 - 0.7)); c.lineTo(X(x + 0.5), Y(BILLBOARD.y0 - 0.9)); c.stroke();
    }

    // HOTA Central blade sign, read top to bottom.
    rect(BLADE); c.fillStyle = "#1b1a1a"; c.fill(); c.strokeStyle = line; c.lineWidth = 1.2 * u; c.stroke();
    c.save();
    c.translate(X((BLADE.x0 + BLADE.x1) / 2), Y(BLADE.y0 + 0.35));
    c.rotate(Math.PI / 2);
    c.fillStyle = "rgba(245,245,245,.92)"; c.textBaseline = "middle";
    const fs = Math.max(6, (BLADE.x1 - BLADE.x0) * s * 0.42);
    c.font = `700 ${fs}px Rubik, sans-serif`; c.fillText("HOTA", 0, 0);
    const hw = c.measureText("HOTA ").width;
    c.font = `400 ${fs}px Rubik, sans-serif`; c.fillText("central", hw, 0);
    c.restore();

    // Ground, and the sheet label.
    c.strokeStyle = faint; c.lineWidth = 1 * u;
    c.beginPath(); c.moveTo(X(box.minX), Y(0)); c.lineTo(X(box.maxX), Y(0)); c.stroke();
    c.fillStyle = "rgba(245,245,245,.6)"; c.font = `500 ${11 * u}px Rubik, sans-serif`; c.textBaseline = "alphabetic";
    c.fillText("Street view", X(box.minX), Y(0) + 18 * u);
    c.restore();
  }

  // One bar's wash up the wall, into the light layer: brightest at the bar,
  // fading out near the top, spreading a little as it rises. (x, y) is the
  // bar on the canvas; s pixels per metre.
  function wash(c, x, y, col, s, Y) {
    const top = Y(WALL.top), h = y - top;
    if (h <= 0) return;
    const g = c.createLinearGradient(0, y, 0, top);
    g.addColorStop(0, col.replace("rgb(", "rgba(").replace(")", ",.85)"));
    g.addColorStop(0.45, col.replace("rgb(", "rgba(").replace(")", ",.38)"));
    g.addColorStop(1, col.replace("rgb(", "rgba(").replace(")", ",0)"));
    c.save();
    c.globalCompositeOperation = "lighter";
    c.fillStyle = g;
    c.beginPath();
    c.moveTo(x - 0.6 * s, y); c.lineTo(x + 0.6 * s, y);
    c.lineTo(x + 1.5 * s, top); c.lineTo(x - 1.5 * s, top);
    c.closePath(); c.fill();
    c.restore();
  }

  window.HotaBuildingScene = { box, ready, layout, drawBase, wash, BILLBOARD };
})();
