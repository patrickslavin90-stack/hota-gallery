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
 * layout) are the bars under the parapet, washing down, left of the billboard,
 * evenly spaced, fixture 1 at the left; drag them in Edit layout if the
 * real order differs.
 */
(() => {
  "use strict";

  const WALL = { x0: 0, x1: 31.8, top: -9, fascia: -8.2 };
  const BILLBOARD = { x0: 20.4, x1: 27.9, y0: -7.3, y1: -2.0 };
  const BLADE = { x0: 29.0, x1: 30.5, y0: -9.6, y1: -3.6 };       // HOTA Central sign
  const TOWER_FRONT = [[8.8, -15.6], [22.1, -15.7], [22.1, -10.9], [8.8, -10.6]];
  const TOWER_SIDE = [[4.0, -15.0], [8.8, -15.6], [8.8, -10.6], [4.0, -10.3]];
  const ROOF = [[2.6, -10.2], [4.0, -10.3], [8.8, -10.6], [22.1, -10.9], [31.8, -10.5], [31.8, WALL.top], [0, WALL.top]];
  // Bars hang under the parapet and wash down the wall.
  const BARS = { x0: 1.4, x1: 18.8, y: -7.95, n: 8 };
  // The wall's dark bays between concrete piers, one per bar (bay centres).
  const BAYS = Array.from({ length: BARS.n }, (_, i) => BARS.x0 + (BARS.x1 - BARS.x0) * i / (BARS.n - 1));
  const BAY_W = 1.6;
  // The tower mural's colour blocks, drawn as bands with a hint of their colour.
  const MURAL = [
    ["216,140,200", [[4.0, -15.0], [8.8, -15.6], [8.8, -13.9], [4.0, -13.1]]],
    ["70,140,230", [[4.0, -13.1], [8.8, -13.9], [8.8, -12.6], [4.0, -12.0]]],
    ["240,200,80", [[4.0, -12.0], [6.5, -13.0], [8.8, -12.6], [8.8, -11.6], [4.0, -11.2]]],
    ["230,80,60", [[7.2, -12.75], [8.8, -12.6], [8.8, -11.6], [7.2, -11.45]]],
    ["90,190,170", [[4.0, -11.2], [8.8, -11.6], [8.8, -10.6], [4.0, -10.3]]],
  ];
  const box = { minX: -1.2, maxX: 33.2, minY: -16.8, maxY: 1.4 };

  // Images next to this script, loaded once.
  const SCRIPT_URL = document.currentScript && document.currentScript.src;
  let logo = null;
  const ready = new Promise(res => {
    const img = new Image();
    img.onload = () => { logo = img; res(); };
    img.onerror = () => res();
    img.src = new URL("hota-logo.svg", SCRIPT_URL || location.href).href;
  });

  // Default layout: fixtures in address order along the top of the wall.
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

  // The static drawing, in the same linework style as the gallery's
  // elevations and the stage's front elevation: white lines on black - a
  // strong outline, lighter detail lines, a faint grid - no solid fills
  // except behind the signs. X, Y map world metres to canvas pixels, s is
  // pixels per metre, u a line-width unit (1 on screen, the card's scale in
  // the export). opts.artwork: an image for the billboard instead of the logo.
  function drawBase(c, X, Y, s, u = 1, opts = {}) {
    const poly = pts => { c.beginPath(); pts.forEach(([x, y], i) => (i ? c.lineTo(X(x), Y(y)) : c.moveTo(X(x), Y(y)))); c.closePath(); };
    const rect = r => { c.beginPath(); c.rect(X(r.x0), Y(r.y0), X(r.x1) - X(r.x0), Y(r.y1) - Y(r.y0)); };
    const seg = (x0, y0, x1, y1) => { c.moveTo(X(x0), Y(y0)); c.lineTo(X(x1), Y(y1)); };
    const OUT = "rgba(245,245,245,.92)", BAND = "rgba(245,245,245,.42)", GRID = "rgba(245,245,245,.08)";
    const lerp = (a, b, t) => a + (b - a) * t;
    c.save();
    c.lineJoin = "round"; c.lineCap = "round";

    // Roof rising behind the wall: standing-seam grid, then its edges.
    c.save(); poly(ROOF); c.clip();
    c.beginPath(); for (let x = 0.6; x < WALL.x1; x += 0.9) seg(x, -11.2, x, WALL.top);
    c.strokeStyle = GRID; c.lineWidth = 1 * u; c.stroke();
    c.restore();
    poly(ROOF); c.strokeStyle = BAND; c.lineWidth = 1 * u; c.stroke();

    // Tower side: the mural's blocks as bands, with a hint of their colour.
    for (const [rgb, pts] of MURAL) {
      poly(pts); c.fillStyle = `rgba(${rgb},.10)`; c.fill();
      c.strokeStyle = BAND; c.lineWidth = 0.9 * u; c.stroke();
    }
    poly(TOWER_SIDE); c.strokeStyle = OUT; c.lineWidth = 1.4 * u; c.stroke();

    // Tower front: panel grid, the HOTA sign, the plant on its roof.
    c.save(); poly(TOWER_FRONT); c.clip();
    c.beginPath();
    for (let t = 1; t < 8; t++) { const x = lerp(8.8, 22.1, t / 8); seg(x, -16, x, -10.5); }
    for (const y of [-14.6, -12.2]) seg(8.8, y, 22.1, y - 0.1);
    c.strokeStyle = GRID; c.lineWidth = 1 * u; c.stroke();
    c.restore();
    poly(TOWER_FRONT); c.strokeStyle = OUT; c.lineWidth = 1.4 * u; c.stroke();
    if (logo) {
      const lw = 10.4 * s, lh = lw * (logo.naturalHeight / logo.naturalWidth || 51 / 343);
      c.globalAlpha = 0.75; c.drawImage(logo, X(10.25), Y(-13.35) - lh / 2, lw, lh); c.globalAlpha = 1;
    }
    c.beginPath();
    c.rect(X(4.9), Y(-15.9), X(5.9) - X(4.9), Y(-15.05) - Y(-15.9));                            // plant on the roof
    c.strokeStyle = BAND; c.lineWidth = 1 * u; c.stroke();

    // The wall the bars wash: parapet fascia, piers between dark bays, then the outline.
    c.beginPath();
    seg(WALL.x0, WALL.fascia, WALL.x1, WALL.fascia);
    for (const x of BAYS) { seg(x - BAY_W / 2, WALL.fascia, x - BAY_W / 2, 0); seg(x + BAY_W / 2, WALL.fascia, x + BAY_W / 2, 0); }
    c.strokeStyle = BAND; c.lineWidth = 0.9 * u; c.stroke();
    c.beginPath();
    for (const x of BAYS) for (let y = -6.8; y < 0; y += 1.4) seg(x - BAY_W / 2, y, x + BAY_W / 2, y); // bay joints
    c.strokeStyle = GRID; c.lineWidth = 1 * u; c.stroke();
    c.beginPath(); c.rect(X(WALL.x0), Y(WALL.top), X(WALL.x1) - X(WALL.x0), Y(0) - Y(WALL.top));
    c.strokeStyle = OUT; c.lineWidth = 1.5 * u; c.stroke();

    // Billboard: artwork if given, otherwise the HOTA logo; three lights on top.
    rect(BILLBOARD); c.fillStyle = "#0f0e0e"; c.fill();
    const art = opts.artwork;
    const bw = X(BILLBOARD.x1) - X(BILLBOARD.x0), bh = Y(BILLBOARD.y1) - Y(BILLBOARD.y0);
    if (art && art.naturalWidth) {
      // Cover: fill the board, cropping the artwork's overhang.
      const k = Math.max(bw / art.naturalWidth, bh / art.naturalHeight);
      const sw = bw / k, sh = bh / k;
      c.drawImage(art, (art.naturalWidth - sw) / 2, (art.naturalHeight - sh) / 2, sw, sh, X(BILLBOARD.x0), Y(BILLBOARD.y0), bw, bh);
    } else if (logo) {
      const lw = bw * 0.62, lh = lw * (logo.naturalHeight / logo.naturalWidth || 51 / 343);
      c.globalAlpha = 0.7; c.drawImage(logo, X(BILLBOARD.x0) + (bw - lw) / 2, Y(BILLBOARD.y0) + (bh - lh) / 2, lw, lh); c.globalAlpha = 1;
    }
    rect(BILLBOARD); c.strokeStyle = OUT; c.lineWidth = 1.2 * u; c.stroke();
    c.beginPath();
    for (const t of [0.17, 0.5, 0.83]) {
      const x = lerp(BILLBOARD.x0, BILLBOARD.x1, t);
      seg(x, BILLBOARD.y0, x, BILLBOARD.y0 - 0.6); seg(x, BILLBOARD.y0 - 0.6, x + 0.5, BILLBOARD.y0 - 0.8);
    }
    c.strokeStyle = BAND; c.lineWidth = 1 * u; c.stroke();

    // HOTA Central blade sign, read top to bottom.
    rect(BLADE); c.fillStyle = "#0f0e0e"; c.fill(); c.strokeStyle = OUT; c.lineWidth = 1.2 * u; c.stroke();
    c.save();
    c.translate(X((BLADE.x0 + BLADE.x1) / 2), Y(BLADE.y0 + 0.35));
    c.rotate(Math.PI / 2);
    c.fillStyle = "rgba(245,245,245,.8)"; c.textBaseline = "middle";
    const fs = Math.max(6, (BLADE.x1 - BLADE.x0) * s * 0.42);
    c.font = `700 ${fs}px Rubik, sans-serif`; c.fillText("HOTA", 0, 0);
    const hw = c.measureText("HOTA ").width;
    c.font = `400 ${fs}px Rubik, sans-serif`; c.fillText("central", hw, 0);
    c.restore();

    // Ground, and its label - same style as the stage's "Front elevation"
    // and the gallery's elevation names.
    c.strokeStyle = "rgba(245,245,245,.35)"; c.lineWidth = 1 * u;
    c.beginPath(); seg(box.minX, 0, box.maxX, 0); c.stroke();
    c.fillStyle = "rgba(245,245,245,.6)"; c.font = `500 ${11 * u}px Rubik, sans-serif`; c.textBaseline = "alphabetic";
    c.fillText("Street elevation", X(box.minX), Y(0) + 18 * u);
    c.restore();
  }

  // One bar's wash down the wall, into the light layer: brightest at the
  // bar, spreading and fading towards the ground. (x, y) is the bar on the
  // canvas; s pixels per metre; Y maps world y to the canvas.
  function wash(c, x, y, col, s, Y) {
    const bottom = Y(0), h = bottom - y;
    if (h <= 0) return;
    const a = k => col.replace("rgb(", "rgba(").replace(")", `,${k})`);
    const g = c.createLinearGradient(0, y, 0, bottom);
    g.addColorStop(0, a(0.8)); g.addColorStop(0.35, a(0.4)); g.addColorStop(1, a(0.04));
    c.save();
    c.globalCompositeOperation = "lighter";
    c.fillStyle = g;
    c.beginPath();
    c.moveTo(x - 0.55 * s, y); c.lineTo(x + 0.55 * s, y);
    c.lineTo(x + 1.45 * s, bottom); c.lineTo(x - 1.45 * s, bottom);
    c.closePath(); c.fill();
    c.restore();
  }

  // A bar fitting, centred on (x, y) on the canvas: a long thin rounded
  // rectangle (the shape of a linear wall-wash bar, not its exact size).
  // grow pads it out (for selection/hover outlines).
  const BAR_LEN = 1.3, BAR_H = 0.24;
  function barPath(c, x, y, s, grow = 0) {
    const w = Math.max(10, BAR_LEN * s) + grow * 2, h = Math.max(3, BAR_H * s) + grow * 2;
    c.beginPath();
    if (c.roundRect) c.roundRect(x - w / 2, y - h / 2, w, h, h / 2);
    else c.rect(x - w / 2, y - h / 2, w, h);
  }

  window.HotaBuildingScene = {
    box, ready, layout, drawBase, wash, barPath,
    // For the 3D view (view3d.js): the same surfaces in metres.
    WALL, BILLBOARD, BLADE, BARS, BAYS, BAY_W, MURAL, BAR_LEN, BAR_H,
    get logo() { return logo; },
  };
})();
