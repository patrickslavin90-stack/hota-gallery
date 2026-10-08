// Browser port of engine.py's colour sampling (plus shaders.py and
// bitmaps.py). Used for the demo mode when there's no controller to ask
// for /api/preview, and for the little look swatches. Keep in step with
// the Python - same look schema, same maths.
(function (global) {
  "use strict";

  const TAU = Math.PI * 2;
  const mod1 = v => ((v % 1) + 1) % 1;
  const lerp = (a, b, t) => a + (b - a) * t;
  const ease = t => (1 - Math.cos(Math.PI * t)) / 2;
  const fold = (t, dir) => (dir === "wings" ? Math.abs(t - 0.5) * 2 : t);
  function travel(pos, progress, dir) {
    if (dir === "bounce") { progress = 1 - Math.abs(2 * mod1(progress) - 1); return mod1(pos - progress); }
    if (dir === "reverse") return mod1(pos + progress);
    return mod1(pos - progress);
  }

  function crc32(str) {
    let crc = 0xffffffff;
    for (let i = 0; i < str.length; i++) {
      let c = (crc ^ str.charCodeAt(i)) & 0xff;
      for (let k = 0; k < 8; k++) c = c & 1 ? (c >>> 1) ^ 0xedb88320 : c >>> 1;
      crc = (crc >>> 8) ^ c;
    }
    return (crc ^ 0xffffffff) >>> 0;
  }

  // -- shaders.py ------------------------------------------------------------
  const scale = (v, lo, hi, a, b) => ((v - lo) / (hi - lo)) * (b - a) + a;
  function bandTest(s, force, force2) {
    const half = scale(force, 1, 10, 0.05, 0.9) / 2, fade = scale(force2, 1, 10, 1, 0);
    const start = 0.5 - half, end = 0.5 + half;
    s = s >= start && s <= end ? 1 - 2 * Math.abs(scale(s, start, end, 0, 1) - 0.5) : -1;
    if (s > 0 && s < fade) return fade ? s / fade : 1;
    return s > 0 ? 1 : 0;
  }
  function scrollBand(dist, t, p) {
    const ring = scale(p.n_items, 1, 64, 10, 0.4);
    return bandTest((((t * 2.2 - dist * 2) % ring) + ring) % ring / ring, p.force, p.force2);
  }
  const SHADERS = {
    circle_scroll: (x, y, t, p) => scrollBand(Math.hypot(x * 6, y * 6) * 1.8, t, p),
    line_scroll: (x, y, t, p) => scrollBand(Math.hypot(x * 6, 0.5) * 1.8, t, p),
    cross_scroll: (x, y, t, p) => { const m = Math.min(Math.abs(x * 6), Math.abs(y * 6)); return scrollBand(Math.hypot(m, m) * 1.8, t, p); },
    square_scroll: (x, y, t, p) => { const m = Math.max(Math.abs(x * 6), Math.abs(y * 6)); return scrollBand(Math.hypot(m, m) * 1.8, t, p); },
    radar: (x, y, t, p) => {
      const n = Math.max(1, p.n_items), slice = TAU / n;
      const ang = (((Math.atan2(y * 2, x * 2) + t * 0.75) % slice) + slice) % slice;
      return bandTest(ang / slice, p.force, p.force2);
    },
    plasma: (x, y, t) => {
      const tt = t * 0.45, px = x * 8 + tt * 0.3, py = y * 8 + tt * 0.3;
      const k = 0.1 + Math.cos(py + Math.sin(0.148 - tt)) + 2.4 + tt;
      const w = 0.9 + Math.sin(px + Math.cos(0.628 + tt)) - 0.7 + tt;
      const s = 7 * Math.cos(Math.hypot(px, py) + w) * Math.sin(k + w);
      return Math.max(0, Math.min(1, 0.5 + 0.5 * Math.cos(s + 0.5)));
    },
  };

  // -- bitmaps.py: 32 x 16 brightness grids -----------------------------------
  const GW = 32, GH = 16;
  const grid = fn => Array.from({ length: GH }, (_, y) => Array.from({ length: GW }, (_, x) => fn(x / (GW - 1), y / (GH - 1))));
  const barV = x => Math.max(0, 1 - Math.abs(x - 0.5) * 6);
  const barH = (x, y) => Math.max(0, 1 - Math.abs(y - 0.5) * 6);
  const BITMAPS = {
    dot: grid((x, y) => Math.max(0, 1 - Math.hypot(x - 0.5, y - 0.5) * 2.4)),
    ring: grid((x, y) => Math.max(0, 1 - Math.abs(Math.hypot(x - 0.5, y - 0.5) - 0.3) * 6)),
    bar_v: grid(barV),
    bar_h: grid(barH),
    cross: grid((x, y) => Math.max(barV(x), barH(x, y))),
    diamond: grid((x, y) => Math.max(0, 1 - (Math.abs(x - 0.5) + Math.abs(y - 0.5)) * 2.4)),
    checker: grid((x, y) => ((Math.floor(x * 8) + Math.floor(y * 4)) % 2 === 0 ? 1 : 0)),
    stripes_v: grid(x => (Math.sin(x * Math.PI * 8) + 1) / 2),
    stripes_h: grid((x, y) => (Math.sin(y * Math.PI * 8) + 1) / 2),
    triangle: grid((x, y) => (Math.abs(x - 0.5) <= (1 - y) * 0.5 ? 1 : 0)),
    heart: grid((x, y) => { const cx = (x - 0.5) * 2.2, cy = (0.58 - y) * 2.2; return (cx * cx + cy * cy - 1) ** 3 - cx * cx * cy ** 3 <= 0 ? 1 : 0; }),
    noise: grid((x, y) => { const n = Math.sin(x * 127.1 + y * 311.7) * 43758.5453; return n - Math.floor(n); }),
  };

  // -- engine.py ----------------------------------------------------------------
  const OFF = { colour: { mode: "off" }, effect: { mode: "off" }, video: { mode: "off" } };
  const rgbw = c => [c.r || 0, c.g || 0, c.b || 0, c.w || 0];

  function makeBBox(fixtures) {
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const f of fixtures) for (const [x, y] of f.leds) {
      if (x < minX) minX = x; if (x > maxX) maxX = x; if (y < minY) minY = y; if (y > maxY) maxY = y;
    }
    if (minX === Infinity) return { minX: 0, maxX: 1, minY: 0, maxY: 1 };
    return { minX, maxX, minY, maxY };
  }
  const norm = (bb, x, y, axis) => axis === "y"
    ? (bb.maxY - bb.minY ? (y - bb.minY) / (bb.maxY - bb.minY) : 0.5)
    : (bb.maxX - bb.minX ? (x - bb.minX) / (bb.maxX - bb.minX) : 0.5);

  function gradientColor(stops, t) {
    t = Math.max(0, Math.min(1, t));
    let lo = stops[0], hi = stops[stops.length - 1];
    for (let i = 0; i < stops.length - 1; i++) {
      if (stops[i].offset <= t && t <= stops[i + 1].offset) { lo = stops[i]; hi = stops[i + 1]; break; }
    }
    const span = hi.offset - lo.offset, lt = span ? ease((t - lo.offset) / span) : 0;
    const a = rgbw(lo.color), b = rgbw(hi.color);
    return [lerp(a[0], b[0], lt), lerp(a[1], b[1], lt), lerp(a[2], b[2], lt), lerp(a[3], b[3], lt)];
  }
  const sortedStops = L => (L.stops || []).slice().sort((a, b) => a.offset - b.offset);

  function colourAt(L, x, y, bb, now) {
    switch (L.mode) {
      case "solid": return rgbw(L.color || {});
      case "gradient": return gradientColor(sortedStops(L), norm(bb, x, y, L.axis || "x"));
      case "color_cycle": {
        const colors = L.colors || [], n = colors.length;
        if (!n) return [0, 0, 0, 0];
        const period = L.period_s ?? 5, t = mod1(now / period) * n, i = Math.floor(t) % n, f = ease(t - Math.floor(t));
        const a = rgbw(colors[i]), b = rgbw(colors[(i + 1) % n]);
        return [lerp(a[0], b[0], f), lerp(a[1], b[1], f), lerp(a[2], b[2], f), lerp(a[3], b[3], f)];
      }
      case "sweep": {
        const dir = L.direction || "forward", period = L.period_s ?? 4;
        return gradientColor(sortedStops(L), travel(fold(norm(bb, x, y, L.axis || "x"), dir), mod1(now / period), dir));
      }
    }
    return [0, 0, 0, 0];
  }

  function effectAt(L, x, y, bb, now, fx) {
    switch (L.mode) {
      case "pulse": return (1 - Math.cos(TAU * mod1(now / (L.period_s ?? 2)))) / 2;
      case "strobe": { const cyc = L.on_ms + L.off_ms; return ((now * 1000) % cyc) < L.on_ms ? 1 : 0; }
      case "chase": {
        const dir = L.direction || "forward", tail = Math.max(0.001, (L.tail ?? 3) / 100);
        const progress = mod1(now / (L.period_s ?? 1)), pos = norm(bb, x, y, L.axis || "x");
        let dist;
        if (dir === "bounce") dist = Math.abs(pos - (1 - Math.abs(2 * progress - 1)));
        else if (dir === "wings") dist = Math.abs(Math.abs(pos - 0.5) * 2 - progress);
        else dist = travel(pos, progress, dir);
        return 1 - ease(Math.min(1, dist / tail));
      }
      case "sine_chase": {
        const dir = L.direction || "forward", cycles = Math.max(0.1, 100 / Math.max(0.1, L.wavelength ?? 4));
        const pos = mod1(fold(norm(bb, x, y, L.axis || "x"), dir) * cycles);
        return (1 - Math.cos(TAU * travel(pos, mod1(now / (L.period_s ?? 2)), dir))) / 2;
      }
      case "trickle": {
        const floor = L.floor ?? 0, w = (1 - Math.cos(TAU * mod1(now / (L.period_s ?? 3) + fx.phase))) / 2;
        return floor + (1 - floor) * w;
      }
      case "shader": {
        const sx = bb.maxX - bb.minX, sy = bb.maxY - bb.minY;
        let cx = norm(bb, x, y, "x") - 0.5, cy = norm(bb, x, y, "y") - 0.5;
        if (sx && sy) { if (sx < sy) cx *= sx / sy; else cy *= sy / sx; }
        const fn = SHADERS[L.shader] || SHADERS.circle_scroll;
        return fn(cx, cy, now * (L.speed ?? 1), { n_items: L.n_items ?? 20, force: L.force ?? 3, force2: L.force2 ?? 5 });
      }
    }
    return 1;
  }

  function videoAt(L, x, y, bb, now) {
    if (L.mode !== "bitmap") return 1;
    const g = BITMAPS[L.bitmap] || BITMAPS.dot, dir = L.direction || "forward", period = L.period_s ?? 0;
    const progress = period > 0 ? mod1(now / period) : 0;
    let u = norm(bb, x, y, "x"), v = norm(bb, x, y, "y");
    if ((L.axis || "x") === "x") u = travel(fold(u, dir), progress, dir); else v = travel(fold(v, dir), progress, dir);
    return g[Math.min(GH - 1, Math.floor(v * GH))][Math.min(GW - 1, Math.floor(u * GW))];
  }

  function sampleLook(look, x, y, bb, now, fx) {
    const c = colourAt(look.colour || OFF.colour, x, y, bb, now);
    const k = effectAt(look.effect || OFF.effect, x, y, bb, now, fx) * videoAt(look.video || OFF.video, x, y, bb, now);
    return [Math.round(c[0] * k), Math.round(c[1] * k), Math.round(c[2] * k), Math.round(c[3] * k)];
  }

  function ledPositions(points, n) {
    const [[x1, y1], [x2, y2]] = points;
    if (n <= 1) return [[x1, y1]];
    const out = [];
    for (let i = 0; i < n; i++) out.push([lerp(x1, x2, i / (n - 1)), lerp(y1, y2, i / (n - 1))]);
    return out;
  }

  function createEngine(cfg) {
    let fixtures = [], bb = null, currentLook = { default: OFF, fixtures: {} };
    function setConfig(c) {
      fixtures = c.fixtures.map(f => {
        const key = `${f.universe}:${f.address}`;
        return { key, name: f.name, leds: ledPositions(f.points, f.led_count), phase: (crc32(key) % 10007) / 10007 };
      });
      bb = makeBBox(fixtures);
      currentLook = c.current_look || currentLook;
    }
    setConfig(cfg);
    return {
      setConfig,
      setCurrentLook(cl) { currentLook = cl; },
      // Same shape as GET /api/preview.
      preview(now) {
        const def = currentLook.default || OFF, over = currentLook.fixtures || {};
        return fixtures.map(fx => {
          const L = over[fx.key] || def;
          return { name: fx.name, colors: fx.leds.map(([x, y]) => sampleLook(L, x, y, bb, now, fx)) };
        });
      },
    };
  }

  // Swatch helper: a look sampled along a unit line, for thumbnails.
  function sampleStrip(look, n, now) {
    const bb = { minX: 0, maxX: 1, minY: 0, maxY: 1 }, out = [];
    for (let i = 0; i < n; i++) {
      const x = (i + 0.5) / n;
      out.push(sampleLook(look, x, 0.5, bb, now, { phase: (i * 0.37) % 1 }));
    }
    return out;
  }

  global.HotaEngine = { createEngine, sampleStrip, SHADERS: Object.keys(SHADERS), BITMAPS: Object.keys(BITMAPS), BITMAP_GRIDS: BITMAPS, OFF };
})(window);
