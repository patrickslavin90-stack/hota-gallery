// "Export GIF": renders the live look onto a presentation card (HOTA logo,
// the façade elevations with the lights animating, concept name) and
// encodes it as a looping GIF for clients and approvals.
//
// Frames come from engine.js (the in-browser port of engine.py) stepped at
// an exact frame interval, so the animation is smooth and deterministic
// rather than a screen recording of whatever the preview poll caught.
// GIF encoding: vendor/gifenc.esm.js (MIT), loaded only when exporting.
(function (global) {
  "use strict";

  const SCRIPT_URL = document.currentScript && document.currentScript.src;
  const vendorUrl = () => new URL("vendor/gifenc.esm.js", SCRIPT_URL || location.href).href;

  const SIZES = { small: [640, 360], medium: [960, 540], large: [1280, 720], fhd: [1920, 1080], uhd: [3840, 2160] };
  const SIZE_LABEL = { small: "Small", medium: "Medium", large: "Large", fhd: "Full HD", uhd: "4K" };
  // Measured on a 6 s sweep: 960×540 came to ~5 MB and 3840×2160 to ~32 MB,
  // so size grows with about pixels^0.67 (big frames compress better), and
  // linearly with the number of frames.
  const estimateMB = (size, seconds) => {
    const [w, hh] = SIZES[size];
    return 5.0 * Math.pow((w * hh) / (960 * 540), 0.67) * (seconds * 1000 / FRAME_MS) / 86;
  };
  const FRAME_MS = 70; // GIF delays are in 1/100 s; 70 ms ≈ 14 fps
  const BG = "#0f0e0e";

  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    if (attrs) for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "style") el.style.cssText = v;
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(kid));
    return el;
  }
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const slug = s => (s || "concept").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "concept";

  let logoPromise = null;
  function loadLogo() {
    if (!logoPromise) logoPromise = new Promise(res => {
      const img = new Image();
      img.onload = () => res(img);
      img.onerror = () => res(null);
      img.src = new URL("hota-logo.svg", SCRIPT_URL || location.href).href;
    });
    return logoPromise;
  }

  // Longest period in the look(s), so the default length shows a full cycle.
  function suggestLength(currentLook) {
    const looks = [currentLook.default, ...Object.values(currentLook.fixtures || {})].filter(Boolean);
    let p = 0;
    for (const l of looks) for (const layer of [l.colour, l.effect, l.video]) {
      if (layer && layer.mode !== "off" && layer.period_s > 0) p = Math.max(p, layer.period_s);
    }
    return p ? clamp(Math.round(p * 10) / 10, 2, 10) : 4;
  }

  // ---------------------------------------------------------------------
  // One card frame.
  //   job: { cfg, elev, layout, view, title, subtitle, logo, engine }
  // ---------------------------------------------------------------------
  function makeRenderer(job, W, H) {
    const canvas = document.createElement("canvas"); canvas.width = W; canvas.height = H;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    const light = document.createElement("canvas"); light.width = W; light.height = H;
    const lctx = light.getContext("2d");
    const filterOK = (() => { const t = document.createElement("canvas").getContext("2d"); t.filter = "blur(2px)"; return t.filter === "blur(2px)"; })();

    const u = W / 960; // design unit: everything below is laid out for 960 wide
    const pad = 36 * u;
    const headH = 64 * u, footH = 104 * u;
    const area = { x: pad, y: headH, w: W - pad * 2, h: H - headH - footH };

    const building = job.elev && job.layout && job.layout.name === "Building";
    const elevs = building ? job.elev.elevations.filter(e => job.view === "all" || e.name === job.view) : [];

    // World box to fit: chosen elevations, or the fixtures themselves.
    let box;
    if (elevs.length) {
      const ys = elevs.flatMap(e => e.outline.flat().map(p => p[1]));
      box = { minX: Math.min(...elevs.map(e => e.x0)), maxX: Math.max(...elevs.map(e => e.x1)), minY: Math.min(...ys), maxY: Math.max(...ys) };
    } else {
      const pts = Object.values(job.layout ? job.layout.fixtures : {}).flat();
      box = { minX: Math.min(...pts.map(p => p[0])), maxX: Math.max(...pts.map(p => p[0])), minY: Math.min(...pts.map(p => p[1])), maxY: Math.max(...pts.map(p => p[1])) };
    }
    const labelRoom = elevs.length ? 26 * u : 0;
    const s = Math.min(area.w / (box.maxX - box.minX || 1), (area.h - labelRoom) / (box.maxY - box.minY || 1));
    const ox = area.x + (area.w - (box.maxX - box.minX) * s) / 2 - box.minX * s;
    const oy = area.y + (area.h - labelRoom - (box.maxY - box.minY) * s) / 2 - box.minY * s;
    const X = x => x * s + ox, Y = y => y * s + oy;

    // Static layer (background, linework, logo, text) drawn once.
    const base = document.createElement("canvas"); base.width = W; base.height = H;
    const b = base.getContext("2d");
    b.fillStyle = BG; b.fillRect(0, 0, W, H);
    // header
    b.fillStyle = "rgba(245,245,245,.55)"; b.font = `400 ${13 * u}px Rubik, sans-serif`; b.textBaseline = "middle";
    b.fillText("Gallery Façade Lighting Concept", pad, headH / 2);
    if (job.logo) {
      const lh = 20 * u, lw = lh * (job.logo.naturalWidth / job.logo.naturalHeight || 343 / 51);
      b.drawImage(job.logo, W - pad - lw, headH / 2 - lh / 2, lw, lh);
    }
    b.fillStyle = "rgba(245,245,245,.12)"; b.fillRect(pad, headH - 1, W - pad * 2, 1);
    // elevations
    const path = (polys, close) => {
      b.beginPath();
      for (const p of polys) { b.moveTo(X(p[0][0]), Y(p[0][1])); for (let i = 1; i < p.length; i++) b.lineTo(X(p[i][0]), Y(p[i][1])); if (close) b.closePath(); }
    };
    for (const e of elevs) {
      b.lineWidth = 1 * u;
      b.strokeStyle = "rgba(245,245,245,.07)"; path(e.grid, false); b.stroke();
      b.strokeStyle = "rgba(245,245,245,.40)"; b.lineWidth = 0.9 * u; path(e.bands, true); b.stroke();
      b.strokeStyle = "rgba(245,245,245,.9)"; b.lineWidth = 1.4 * u; path(e.outline, true); b.stroke();
      const bottom = Math.max(...e.outline.flat().map(p => p[1]));
      b.fillStyle = "rgba(245,245,245,.6)"; b.font = `500 ${11 * u}px Rubik, sans-serif`; b.textBaseline = "alphabetic";
      b.fillText(e.name, X(e.x0), Y(bottom) + 18 * u);
    }
    // footer: concept name, then the description line
    b.fillStyle = "rgba(245,245,245,.12)"; b.fillRect(pad, H - footH + 14 * u, W - pad * 2, 1);
    b.textBaseline = "alphabetic";
    b.fillStyle = "#f5f5f5"; b.font = `500 ${30 * u}px Rubik, sans-serif`;
    let title = job.title || "Untitled concept";
    while (b.measureText(title).width > W - pad * 2 && title.length > 4) title = title.slice(0, -2).trimEnd() + "…";
    b.fillText(title, pad, H - footH + 58 * u);
    b.fillStyle = "rgba(245,245,245,.55)"; b.font = `400 ${13 * u}px Rubik, sans-serif`;
    b.fillText(job.subtitle || "", pad, H - footH + 84 * u);
    b.fillStyle = "#d5134d"; b.fillRect(pad, H - footH + 14 * u, 48 * u, 2 * u);

    // LED positions in card space, per fixture index.
    const fixtures = job.cfg.fixtures;
    const leds = [];
    fixtures.forEach((f, fi) => {
      const p = job.layout && job.layout.fixtures[`${f.universe}:${f.address}`];
      if (!p) return;
      if (elevs.length && job.view !== "all") {
        const mx = (p[0][0] + p[1][0]) / 2;
        const e = elevs[0];
        if (mx < e.x0 - 200 || mx > e.x1 + 200) return;
      }
      const n = f.led_count;
      for (let i = 0; i < n; i++) {
        const t = n > 1 ? i / (n - 1) : 0;
        leds.push({ fi, i, x: X(p[0][0] + (p[1][0] - p[0][0]) * t), y: Y(p[0][1] + (p[1][1] - p[0][1]) * t), dish: f.led_type === "RGB" });
      }
    });
    const r = clamp(s * 22, 1.4, 5 * u), rDish = clamp(s * 38, 2.6, 9 * u);

    return {
      canvas,
      draw(t) {
        const prev = job.engine.preview(t);
        ctx.globalCompositeOperation = "source-over"; ctx.globalAlpha = 1; ctx.filter = "none";
        ctx.drawImage(base, 0, 0);
        lctx.clearRect(0, 0, W, H);
        ctx.fillStyle = "#3f3c3c";
        let last = null;
        for (const L of leds) {
          const c = prev[L.fi] && prev[L.fi].colors[L.i];
          const w = c ? c[3] : 0;
          const rgb = c ? [Math.min(255, c[0] + w), Math.min(255, c[1] + w * 0.96), Math.min(255, c[2] + w * 0.88)] : [0, 0, 0];
          if (rgb[0] + rgb[1] + rgb[2] <= 24) {
            ctx.beginPath(); ctx.arc(L.x, L.y, L.dish ? rDish * 0.7 : r * 0.6, 0, Math.PI * 2); ctx.fill();
            last = L; continue;
          }
          const col = `rgb(${rgb[0] | 0},${rgb[1] | 0},${rgb[2] | 0})`;
          lctx.fillStyle = col;
          if (!L.dish && last && last.fi === L.fi) {
            lctx.strokeStyle = col; lctx.lineWidth = r * 1.5; lctx.lineCap = "round";
            lctx.beginPath(); lctx.moveTo(last.x, last.y); lctx.lineTo(L.x, L.y); lctx.stroke();
          }
          lctx.beginPath(); lctx.arc(L.x, L.y, L.dish ? rDish : r, 0, Math.PI * 2); lctx.fill();
          last = L;
        }
        ctx.globalCompositeOperation = "lighter";
        if (filterOK) {
          ctx.filter = `blur(${Math.round(12 * u)}px)`; ctx.globalAlpha = 0.65; ctx.drawImage(light, 0, 0);
          ctx.filter = `blur(${Math.max(1, Math.round(3.5 * u))}px)`; ctx.globalAlpha = 0.55; ctx.drawImage(light, 0, 0);
          ctx.filter = "none";
        }
        ctx.globalCompositeOperation = "source-over"; ctx.globalAlpha = 1;
        ctx.drawImage(light, 0, 0);
      },
    };
  }

  // ---------------------------------------------------------------------
  // Encode: one shared palette from a few sample frames (no colour flicker
  // between frames), then every frame mapped onto it.
  // ---------------------------------------------------------------------
  async function encode(job, size, seconds, onProgress, signal) {
    const { GIFEncoder, quantize, applyPalette } = await import(vendorUrl());
    const [W, H] = SIZES[size] || SIZES.medium;
    const R = makeRenderer(job, W, H);
    const frames = Math.max(2, Math.round((seconds * 1000) / FRAME_MS));
    const step = FRAME_MS / 1000;

    const sampleIdx = [0, 0.25, 0.5, 0.75].map(f => Math.floor(f * frames));
    const stride = Math.max(3, Math.floor((W * H) / 200000)); // ~200k sampled pixels per frame
    const perFrame = Math.ceil((W * H) / stride);
    const chunk = new Uint8Array(perFrame * 4 * sampleIdx.length);
    let o = 0;
    for (const fIdx of sampleIdx) {
      R.draw(fIdx * step);
      const d = R.canvas.getContext("2d").getImageData(0, 0, W, H).data;
      for (let p = 0; p < d.length; p += 4 * stride) { chunk[o++] = d[p]; chunk[o++] = d[p + 1]; chunk[o++] = d[p + 2]; chunk[o++] = 255; }
    }
    const palette = quantize(chunk.subarray(0, o), 256);

    const gif = GIFEncoder();
    for (let i = 0; i < frames; i++) {
      if (signal && signal.aborted) throw new DOMException("Cancelled", "AbortError");
      R.draw(i * step);
      const d = R.canvas.getContext("2d").getImageData(0, 0, W, H).data;
      gif.writeFrame(applyPalette(d, palette), W, H, { palette: i === 0 ? palette : undefined, delay: FRAME_MS, repeat: 0 });
      onProgress((i + 1) / frames, i + 1, frames);
      if (i % 3 === 2) await new Promise(r => setTimeout(r, 0)); // let the progress bar paint
    }
    gif.finish();
    return new Blob([gif.bytes()], { type: "image/gif" });
  }

  // ---------------------------------------------------------------------
  // Dialog
  //   opts: { cfg, elev, layout, view, title, subtitle, onDone(msg), onError(msg) }
  // ---------------------------------------------------------------------
  async function open(opts) {
    const cfg = structuredClone(opts.cfg);
    const engine = HotaEngine.createEngine(cfg);
    engine.setCurrentLook(cfg.current_look);
    // Open straight away; the logo and fonts are usually preloaded, and if
    // not, the preview redraws the moment they arrive.
    let logo = null;
    const assetsReady = preload().then(l => { logo = l; if (dlg.isConnected) rebuild(); });

    const views = opts.elev && opts.layout && opts.layout.name === "Building" ? ["all", ...opts.elev.elevations.map(e => e.name)] : ["all"];
    const state = { title: opts.title || "Untitled concept", subtitle: opts.subtitle || "", view: views.includes(opts.view) ? opts.view : "all", size: "medium", seconds: suggestLength(cfg.current_look) };

    const nameIn = h("input", { type: "text", class: "dlg-input", value: state.title, "aria-label": "Concept name", maxlength: 60 });
    const subIn = h("input", { type: "text", class: "dlg-input", value: state.subtitle, "aria-label": "Description line", maxlength: 90, placeholder: "Description (optional)" });
    const viewSel = h("select", { "aria-label": "View" }, views.map(v => h("option", { value: v, text: v === "all" ? "Whole building" : `${v} elevation`, selected: v === state.view })));
    const sizeSel = h("select", { "aria-label": "Size" }, Object.entries(SIZES).map(([k, [w, hh]]) => h("option", { value: k, text: `${SIZE_LABEL[k]}, ${w}×${hh}`, selected: k === state.size })));
    const estimate = h("p", { class: "gif-estimate" });
    const updateEstimate = () => {
      const mb = estimateMB(state.size, state.seconds);
      estimate.textContent = `About ${mb < 10 ? mb.toFixed(1) : Math.round(mb)} MB`
        + (mb > 25 ? ". Large for email; a shorter length or smaller size keeps it lighter." : ".");
      estimate.classList.toggle("warn-text", mb > 25);
    };
    const secIn = h("input", { type: "number", min: 1, max: 20, step: 0.5, value: state.seconds, "aria-label": "Length in seconds", style: "width:90px" });
    const preview = h("canvas", { class: "gif-preview", width: 480, height: 270, "aria-label": "Preview of the GIF" });
    const status = h("p", { class: "dlg-msg gif-status", role: "status" });
    const bar = h("div", { class: "gif-bar", hidden: true }, h("i"));
    const result = h("div", { class: "gif-result", hidden: true });
    const createBtn = h("button", { type: "button", class: "btn primary", text: "Create GIF" });
    const cancelBtn = h("button", { type: "button", class: "btn", text: "Close" });

    const dlg = h("dialog", { class: "dlg dlg-wide", "aria-labelledby": "gifTitle" },
      h("div", { class: "dlg-body" },
        h("h3", { class: "dlg-title", id: "gifTitle", text: "Export as GIF" }),
        h("p", { class: "dlg-msg", text: "A looping card of the current live look, with the HOTA logo and your concept name, for sending to clients or for approval." }),
        h("div", { class: "gif-grid" },
          h("div", null, preview, bar, status, result),
          h("div", { class: "gif-fields" },
            h("label", { class: "gif-field" }, h("span", { text: "Concept name" }), nameIn),
            h("label", { class: "gif-field" }, h("span", { text: "Description" }), subIn),
            h("label", { class: "gif-field" }, h("span", { text: "View" }), viewSel),
            h("div", { class: "gif-row" },
              h("label", { class: "gif-field" }, h("span", { text: "Length (s)" }), secIn),
              h("label", { class: "gif-field", style: "flex:1" }, h("span", { text: "Size" }), sizeSel)),
            estimate)),
        h("div", { class: "dlg-actions" }, cancelBtn, createBtn)));
    document.body.append(dlg);
    dlg.showModal();
    nameIn.focus(); nameIn.select();

    // Live preview of the card at the chosen settings.
    let previewR = null, raf = 0, t0 = performance.now(), abort = null, url = null;
    const job = () => ({ cfg, elev: opts.elev, layout: opts.layout, view: state.view, title: state.title, subtitle: state.subtitle, logo, engine });
    const rebuild = () => { previewR = makeRenderer(job(), 480, 270); };
    rebuild();
    // Follow the live look while the dialog is open: if it changes (a save
    // landing late, the schedule, another browser), the preview and the GIF
    // switch to it within half a second - no need to close and reopen.
    let lookJSON = JSON.stringify(cfg.current_look), lastCheck = 0;
    function syncLook() {
      if (!opts.getCurrentLook) return;
      const now = opts.getCurrentLook();
      const j = JSON.stringify(now);
      if (j === lookJSON) return;
      lookJSON = j;
      cfg.current_look = structuredClone(now);
      engine.setCurrentLook(cfg.current_look);
      if (opts.describe) { state.subtitle = opts.describe(cfg.current_look); if (!subEdited) subIn.value = state.subtitle; }
      rebuild();
    }
    let subEdited = false;
    (function loop() {
      const nowMs = performance.now();
      if (nowMs - lastCheck > 500) { lastCheck = nowMs; syncLook(); }
      const t = ((nowMs - t0) / 1000) % state.seconds;
      previewR.draw(t);
      const pc = preview.getContext("2d"); pc.drawImage(previewR.canvas, 0, 0);
      raf = requestAnimationFrame(loop);
    })();
    nameIn.addEventListener("input", () => { state.title = nameIn.value.trim(); rebuild(); });
    subIn.addEventListener("input", () => { subEdited = true; state.subtitle = subIn.value.trim(); rebuild(); });
    viewSel.addEventListener("change", () => { state.view = viewSel.value; rebuild(); });
    sizeSel.addEventListener("change", () => { state.size = sizeSel.value; updateEstimate(); });
    secIn.addEventListener("change", () => { state.seconds = clamp(+secIn.value || 4, 1, 20); secIn.value = state.seconds; t0 = performance.now(); updateEstimate(); });
    updateEstimate();

    function close() {
      if (abort) abort.abort();
      cancelAnimationFrame(raf);
      if (url) setTimeout(() => URL.revokeObjectURL(url), 60000);
      dlg.close(); dlg.remove();
    }
    cancelBtn.addEventListener("click", close);
    dlg.addEventListener("cancel", e => { e.preventDefault(); close(); });

    createBtn.addEventListener("click", async () => {
      await assetsReady; // never encode a card without its logo
      syncLook(); // encode exactly what's live right now
      createBtn.disabled = true; [nameIn, subIn, viewSel, sizeSel, secIn].forEach(x => { x.disabled = true; });
      cancelBtn.textContent = "Cancel";
      bar.hidden = false; result.hidden = true; status.textContent = "Rendering…";
      abort = new AbortController();
      try {
        const blob = await encode(job(), state.size, state.seconds, (f, i, n) => {
          bar.firstChild.style.width = `${Math.round(f * 100)}%`;
          status.textContent = `Rendering frame ${i} of ${n}`;
        }, abort.signal);
        abort = null;
        url = URL.createObjectURL(blob);
        const name = `HOTA-${slug(state.title)}.gif`;
        const mb = (blob.size / 1048576).toFixed(1);
        const dl = h("a", { class: "btn primary", href: url, download: name, text: `Download GIF (${mb} MB)` });
        result.replaceChildren(h("img", { src: url, alt: `${state.title} GIF`, class: "gif-preview" }), dl);
        result.hidden = false; bar.hidden = true;
        status.textContent = `${name} is ready: ${Math.round(state.seconds * 1000 / FRAME_MS)} frames, ${SIZES[state.size].join("×")}.`;
        preview.hidden = true; cancelAnimationFrame(raf);
        createBtn.hidden = true; cancelBtn.textContent = "Done";
        dl.click(); // start the download straight away; the button stays for a second copy
        if (opts.onDone) opts.onDone(`${name} downloaded.`);
      } catch (e) {
        bar.hidden = true;
        if (e && e.name === "AbortError") { status.textContent = "Cancelled."; return; }
        status.textContent = "The GIF couldn't be made.";
        if (opts.onError) opts.onError(String(e && e.message || e));
        createBtn.disabled = false; [nameIn, subIn, viewSel, sizeSel, secIn].forEach(x => { x.disabled = false; });
        cancelBtn.textContent = "Close";
      }
    });
  }

  // Logo + card fonts, fetched once in the background at page load so the
  // dialog never waits on them (they'd queue behind preview polling).
  let preloaded = null;
  function preload() {
    if (!preloaded) preloaded = Promise.all([
      loadLogo(),
      document.fonts ? document.fonts.load("500 30px Rubik").catch(() => null) : null,
      document.fonts ? document.fonts.load("400 13px Rubik").catch(() => null) : null,
    ]).then(([l]) => l);
    return preloaded;
  }

  global.HotaExport = { open, preload };
})(window);
