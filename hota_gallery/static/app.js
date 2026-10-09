// HOTA Gallery lighting control - UI. Talks to whichever backend
// backend.js found (the controller, or this browser in demo mode).
(async function () {
  "use strict";

  // ===================================================================
  // helpers
  // ===================================================================
  const $ = s => document.querySelector(s);
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    if (attrs) for (const [k, v] of Object.entries(attrs)) {
      // ARIA states need the literal "true"/"false" (CSS keys off
      // [aria-pressed="true"]); other booleans are present/absent.
      if (k.startsWith("aria-") && typeof v === "boolean") { el.setAttribute(k, String(v)); continue; }
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k === "html") el.innerHTML = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "style") el.style.cssText = v;
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(kid));
    return el;
  }
  const clone = o => structuredClone(o);
  const keyOf = f => `${f.universe}:${f.address}`;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const toHex = c => "#" + [c.r, c.g, c.b].map(v => clamp(Math.round(v || 0), 0, 255).toString(16).padStart(2, "0")).join("");
  const fromHex = (hex, w = 0) => ({ r: parseInt(hex.slice(1, 3), 16), g: parseInt(hex.slice(3, 5), 16), b: parseInt(hex.slice(5, 7), 16), w });
  const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
  const DAY_LABEL = { mon: "Mon", tue: "Tue", wed: "Wed", thu: "Thu", fri: "Fri", sat: "Sat", sun: "Sun" };
  const prefersReduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Identical messages merge into one with a count instead of stacking.
  const openToasts = new Map();
  function toast(msg, isErr) {
    const key = (isErr ? "e|" : "i|") + msg, ms = isErr ? 9000 : 3200;
    const existing = openToasts.get(key);
    if (existing) {
      existing.count++;
      existing.badge.textContent = `×${existing.count}`;
      clearTimeout(existing.timer);
      existing.timer = setTimeout(existing.close, ms);
      return;
    }
    const badge = h("span", { class: "toast-count" });
    const t = h("div", { class: "toast" + (isErr ? " err" : "") }, h("span", { text: msg }), badge);
    const entry = { count: 1, badge, close: () => { t.remove(); openToasts.delete(key); } };
    entry.timer = setTimeout(entry.close, ms);
    openToasts.set(key, entry);
    $("#toasts").append(t);
  }
  // Themed replacement for prompt()/confirm(). Resolves to the entered
  // text (input dialogs), true (confirm dialogs), or null if cancelled.
  //   ask({ title, message, input: true, value, placeholder, ok, danger })
  function ask({ title, message, input = false, value = "", placeholder = "", ok = "OK", cancel = "Cancel", danger = false }) {
    return new Promise(resolve => {
      const field = input ? h("input", { type: "text", class: "dlg-input", value, placeholder, "aria-label": title, autocomplete: "off" }) : null;
      const okBtn = h("button", { type: "submit", class: `btn ${danger ? "danger-fill" : "primary"}`, text: ok });
      const form = h("form", { method: "dialog", class: "dlg-body" },
        h("h3", { class: "dlg-title", id: "dlgTitle", text: title }),
        message ? h("p", { class: "dlg-msg", text: message }) : null,
        field,
        h("div", { class: "dlg-actions" }, h("button", { type: "button", class: "btn", text: cancel, onclick: () => done(null) }), okBtn));
      const dlg = h("dialog", { class: "dlg", "aria-labelledby": "dlgTitle" }, form);
      let settled = false;
      function done(result) {
        if (settled) return;
        settled = true;
        dlg.classList.add("closing");
        setTimeout(() => { dlg.close(); dlg.remove(); }, prefersReduced ? 0 : 120);
        resolve(result);
      }
      form.addEventListener("submit", e => { e.preventDefault(); done(input ? field.value : true); });
      dlg.addEventListener("cancel", e => { e.preventDefault(); done(null); });
      dlg.addEventListener("click", e => { if (e.target === dlg) done(null); }); // backdrop click
      document.body.append(dlg);
      dlg.showModal();
      if (field) { field.focus(); field.select(); } else okBtn.focus();
    });
  }

  const errText = e => {
    // fetch() rejects with a bare TypeError when nothing answers at all.
    if (e instanceof TypeError && /fetch|network|load/i.test(e.message)) return "Can't reach the controller. Check it's running and on the network.";
    return (e && e.message ? e.message : String(e)).replace(/^\s+- /gm, "• ");
  };

  // ===================================================================
  // Pill dropdowns: every <select> gets a styled trigger + listbox popover
  // (rounded panel, hover highlight, check on the chosen option). The
  // native select stays in the DOM as the source of truth, so existing
  // code that reads .value, sets .value or listens for "change" just works.
  // ===================================================================
  const CHEVRON = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const CHECK = '<svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true"><path d="M3.5 8.5l3 3 6-7" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const valueDesc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value");
  const indexDesc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "selectedIndex");
  let openPop = null;
  let uid = 0;

  function enhanceSelect(sel) {
    if (sel._pill || sel.multiple || sel.dataset.native != null) return;
    const id = `ps${++uid}`;
    const trigger = h("button", { type: "button", class: "ps-trigger", role: "combobox", "aria-haspopup": "listbox", "aria-expanded": "false", "aria-controls": `${id}-list` },
      h("span", { class: "ps-value" }), h("span", { class: "ps-chev", html: CHEVRON }));
    const valueEl = trigger.firstChild;
    sel.classList.add("ps-native");
    sel.tabIndex = -1;
    sel.setAttribute("aria-hidden", "true");
    sel.after(trigger);
    if (sel.style.cssText) trigger.style.cssText = sel.style.cssText;
    if (sel.getAttribute("aria-label")) trigger.setAttribute("aria-label", sel.getAttribute("aria-label"));
    if (sel.id) {
      const lbl = document.querySelector(`label[for="${sel.id}"]`);
      trigger.id = `${sel.id}-trigger`;
      if (lbl) { lbl.htmlFor = trigger.id; if (!trigger.getAttribute("aria-label")) trigger.setAttribute("aria-labelledby", `${lbl.id || (lbl.id = `${id}-lbl`)} ${trigger.id}`); }
    }
    const sync = () => {
      const o = sel.options[sel.selectedIndex];
      valueEl.textContent = o ? o.textContent : "";
      trigger.classList.toggle("placeholder", !o || o.value === "");
    };
    // Programmatic .value / .selectedIndex writes don't fire "change", so
    // hook them on this element to keep the trigger label in step.
    Object.defineProperty(sel, "value", { configurable: true, get() { return valueDesc.get.call(this); }, set(v) { valueDesc.set.call(this, v); sync(); } });
    Object.defineProperty(sel, "selectedIndex", { configurable: true, get() { return indexDesc.get.call(this); }, set(v) { indexDesc.set.call(this, v); sync(); } });
    sel.addEventListener("change", sync);
    new MutationObserver(sync).observe(sel, { childList: true, subtree: true, attributes: true, attributeFilter: ["selected"] });
    sync();

    function open() {
      if (openPop) openPop.close();
      const opts = [...sel.options];
      let active = Math.max(0, sel.selectedIndex);
      const list = h("div", { class: "ps-list", role: "listbox", id: `${id}-list`, tabindex: -1 });
      const items = opts.map((o, i) => {
        const it = h("div", { class: "ps-item", role: "option", id: `${id}-o${i}`, "aria-selected": i === sel.selectedIndex },
          h("span", { text: o.textContent }), i === sel.selectedIndex ? h("span", { class: "ps-check", html: CHECK }) : null);
        it.addEventListener("pointermove", () => setActive(i));
        it.addEventListener("click", () => choose(i));
        list.append(it);
        return it;
      });
      const host = trigger.closest("dialog") || document.body;
      host.append(list);
      function place() {
        const r = trigger.getBoundingClientRect();
        list.style.minWidth = `${r.width}px`;
        const below = innerHeight - r.bottom - 12, above = r.top - 12;
        const want = Math.min(list.scrollHeight, 320);
        const up = below < Math.min(want, 200) && above > below;
        list.style.maxHeight = `${Math.max(120, Math.min(320, up ? above : below))}px`;
        list.style.left = `${Math.min(r.left, innerWidth - list.offsetWidth - 8)}px`;
        list.style.top = up ? "" : `${r.bottom + 6}px`;
        list.style.bottom = up ? `${innerHeight - r.top + 6}px` : "";
        list.dataset.side = up ? "top" : "bottom";
      }
      function setActive(i, scroll) {
        active = i;
        items.forEach((it, j) => it.classList.toggle("active", j === i));
        trigger.setAttribute("aria-activedescendant", items[i] ? items[i].id : "");
        if (scroll && items[i]) items[i].scrollIntoView({ block: "nearest" });
      }
      function choose(i) {
        const changed = i !== sel.selectedIndex;
        sel.selectedIndex = i;
        close();
        trigger.focus();
        if (changed) sel.dispatchEvent(new Event("change", { bubbles: true }));
      }
      function onKey(e) {
        // Handled here only - stop the trigger's own "open on Enter/Arrow"
        // listener from seeing the same keypress and reopening the list.
        if (e.key !== "Tab") e.stopImmediatePropagation();
        if (e.key === "ArrowDown") { e.preventDefault(); setActive(Math.min(items.length - 1, active + 1), true); }
        else if (e.key === "ArrowUp") { e.preventDefault(); setActive(Math.max(0, active - 1), true); }
        else if (e.key === "Home") { e.preventDefault(); setActive(0, true); }
        else if (e.key === "End") { e.preventDefault(); setActive(items.length - 1, true); }
        else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); choose(active); }
        else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); trigger.focus(); }
        else if (e.key === "Tab") close();
        else if (e.key.length === 1) {
          const k = e.key.toLowerCase();
          const n = opts.findIndex((o, j) => j > active && o.textContent.toLowerCase().startsWith(k));
          const m = n >= 0 ? n : opts.findIndex(o => o.textContent.toLowerCase().startsWith(k));
          if (m >= 0) setActive(m, true);
        }
      }
      const outside = e => { if (!list.contains(e.target) && !trigger.contains(e.target)) close(); };
      const onScroll = e => { if (!list.contains(e.target)) close(); };
      function close() {
        list.remove();
        trigger.setAttribute("aria-expanded", "false");
        trigger.removeAttribute("aria-activedescendant");
        trigger.removeEventListener("keydown", onKey, true);
        document.removeEventListener("pointerdown", outside, true);
        window.removeEventListener("scroll", onScroll, true);
        window.removeEventListener("resize", close);
        if (openPop && openPop.list === list) openPop = null;
      }
      trigger.setAttribute("aria-expanded", "true");
      trigger.addEventListener("keydown", onKey, true);
      document.addEventListener("pointerdown", outside, true);
      window.addEventListener("scroll", onScroll, true);
      window.addEventListener("resize", close);
      place();
      setActive(active, true);
      openPop = { list, close };
    }
    trigger.addEventListener("click", () => { if (openPop && openPop.list && trigger.getAttribute("aria-expanded") === "true") openPop.close(); else open(); });
    trigger.addEventListener("keydown", e => {
      if (trigger.getAttribute("aria-expanded") === "true") return;
      if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) { e.preventDefault(); open(); }
    });
    sel._pill = trigger;
  }
  const enhanceAll = root => { if (root.matches && root.matches("select")) enhanceSelect(root); if (root.querySelectorAll) root.querySelectorAll("select").forEach(enhanceSelect); };
  enhanceAll(document.body);
  new MutationObserver(muts => { for (const m of muts) for (const n of m.addedNodes) if (n.nodeType === 1) enhanceAll(n); })
    .observe(document.body, { childList: true, subtree: true });

  // Serialises saves of one kind so a burst of slider moves sends the
  // latest value once the previous request finishes, never out of order.
  function latestOnly(fn, delay = 90) {
    let timer = null, running = false, pending = null;
    async function run() {
      if (running || !pending) return;
      running = true;
      const args = pending; pending = null;
      try { await fn(...args); } catch (e) { toast(errText(e), true); }
      running = false;
      if (pending) run();
    }
    return (...args) => { pending = args; clearTimeout(timer); timer = setTimeout(run, delay); };
  }

  // ===================================================================
  // load
  // ===================================================================
  const conn = $("#conn");
  let api, cfg, elev = null;
  try {
    api = await HotaBackend.connect();
    cfg = await api.getConfig();
  } catch (e) {
    conn.dataset.kind = "error";
    conn.querySelector("span").textContent = "Couldn't load the configuration";
    toast(errText(e), true);
    return;
  }
  try { elev = await (await fetch("static/elevations.json", { cache: "no-store" })).json(); } catch { elev = null; }
  // Fixture positions on the elevations, shipped with the UI. Used when the
  // controller's config doesn't have its own "Building" layout yet - it gets
  // saved there the first time someone moves fixtures in it.
  let b3d = null; // rough 3D massing derived from the elevation grids (tools/build_3d.py)
  try { b3d = await (await fetch("static/building3d.json", { cache: "no-store" })).json(); } catch { b3d = null; }
  let builtinBuilding = null;
  try { builtinBuilding = await (await fetch("static/building-layout.json", { cache: "no-store" })).json(); } catch { builtinBuilding = null; }
  const allLayouts = () => {
    const ls = cfg.layouts || [];
    return elev && builtinBuilding && !ls.some(l => l.name === "Building") ? [builtinBuilding, ...ls] : ls;
  };

  // Where the controller is: "this computer" when served from localhost
  // (e.g. a test run on a laptop), otherwise its address on the network.
  let offline = false; // set by the preview poll when the controller stops answering
  const isLocalHost = ["localhost", "127.0.0.1", "[::1]", "::1"].includes(location.hostname);
  function setConn() {
    if (offline) {
      conn.dataset.kind = "error";
      conn.querySelector("span").textContent = "Lost connection to the controller, retrying";
      conn.title = `Nothing is answering at ${location.host}. The drawing is frozen on the last frame it received.`;
      return;
    }
    conn.dataset.kind = api.kind;
    conn.querySelector("span").textContent = api.kind === "controller"
      ? (isLocalHost ? "Controller on this computer" : `Controller at ${location.hostname}`)
      : "Demo: changes are saved in this browser only";
    conn.title = api.kind === "controller"
      ? `This page is talking to the lighting controller "${cfg.device_name || "unnamed"}" at ${location.host}. Whether its lights are reachable is shown by the Lights indicator.`
      : "No controller found. Nothing here affects the real building.";
  }
  setConn();

  let fixtureIndex = new Map();
  let zoneNames = [];
  function indexConfig() {
    fixtureIndex = new Map(cfg.fixtures.map((f, i) => [keyOf(f), i]));
    zoneNames = (cfg.zones || []).map(z => z.name);
    cfg.current_look = cfg.current_look || { default: clone(HotaEngine.OFF), fixtures: {} };
    cfg.current_look.fixtures = cfg.current_look.fixtures || {};
  }
  indexConfig();

  // ===================================================================
  // remote PIN gate - see renderRemote() in Settings for where this is
  // set. A client-side speed bump only, same philosophy as the house-
  // lights bridge's admin PIN: nothing server-side enforces it, so it
  // only ever blocks the UI, never the API. Skipped entirely on this
  // kiosk's own screen (isLocalHost) - you're already standing at the
  // building. Anyone else is asked once per browser, then it's
  // remembered in localStorage so the same phone isn't asked again.
  // ===================================================================
  const PIN_OK_KEY = "hotaRemotePinOk";
  async function requireRemotePin() {
    if (isLocalHost || !cfg.remote_pin) return;
    let stored = null;
    try { stored = localStorage.getItem(PIN_OK_KEY); } catch { /* private browsing - just ask every time */ }
    if (stored === cfg.remote_pin) return;
    await new Promise(resolve => {
      const field = h("input", { type: "tel", inputmode: "numeric", class: "dlg-input", placeholder: "PIN", autocomplete: "off" });
      const errSlot = h("div", { class: "alert-slot" });
      const form = h("form", { method: "dialog", class: "dlg-body" },
        h("h3", { class: "dlg-title", id: "dlgTitle", text: "Enter PIN" }),
        h("p", { class: "dlg-msg", text: "Ask gallery staff for the access PIN to control the lights remotely." }),
        field, errSlot,
        h("div", { class: "dlg-actions" }, h("button", { type: "submit", class: "btn primary", text: "Unlock" })));
      const dlg = h("dialog", { class: "dlg", "aria-labelledby": "dlgTitle" }, form);
      form.addEventListener("submit", e => {
        e.preventDefault();
        const val = field.value.trim();
        if (val === cfg.remote_pin) {
          try { localStorage.setItem(PIN_OK_KEY, val); } catch { /* private browsing - just re-ask next time */ }
          dlg.close(); dlg.remove();
          resolve();
        } else {
          slideAlert(errSlot, "Wrong PIN", "Try again.");
          field.value = "";
          field.focus();
        }
      });
      // Deliberately no cancel/backdrop-close - unlike every other dialog
      // in this app, there's nothing behind this one to fall back to.
      dlg.addEventListener("cancel", e => e.preventDefault());
      document.body.append(dlg);
      dlg.showModal();
      field.focus();
    });
  }
  await requireRemotePin();

  // ===================================================================
  // clock + tabs
  // ===================================================================
  const clockEl = $("#clock");
  const tickClock = () => { clockEl.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }); };
  tickClock(); setInterval(tickClock, 1000);

  const tabs = { live: $("#view-live"), schedules: $("#view-schedules"), settings: $("#view-settings") };
  function showTab(name) {
    if (!tabs[name]) name = "live";
    for (const [k, el] of Object.entries(tabs)) el.hidden = k !== name;
    document.querySelectorAll(".tabs button").forEach(b => b.setAttribute("aria-selected", b.dataset.tab === name));
    if (name === "schedules") openSchedules();
    if (name === "settings") openSettings();
    if (name === "live") requestAnimationFrame(resize);
    if (location.hash.slice(1) !== name) history.replaceState(null, "", name === "live" ? location.pathname : `#${name}`);
  }
  document.querySelectorAll(".tabs button").forEach(b => b.addEventListener("click", () => showTab(b.dataset.tab)));
  window.addEventListener("hashchange", () => showTab(location.hash.slice(1) || "live"));

  // ===================================================================
  // look editor (used in Live, the schedule drawer and nowhere else)
  // ===================================================================
  const COLOUR_MODES = [["off", "Off"], ["solid", "Solid"], ["gradient", "Gradient"], ["color_cycle", "Colour cycle"], ["sweep", "Sweep"]];
  const EFFECT_MODES = [["off", "Off"], ["pulse", "Pulse"], ["strobe", "Strobe"], ["chase", "Chase"], ["sine_chase", "Sine chase"], ["trickle", "Trickle"], ["shader", "Shader"]];
  const SHADER_LABEL = { circle_scroll: "Rings", line_scroll: "Lines", cross_scroll: "Cross", square_scroll: "Squares", radar: "Radar", plasma: "Plasma" };
  const BITMAP_LABEL = { dot: "Dot", ring: "Ring", bar_v: "Bar, vertical", bar_h: "Bar, horizontal", cross: "Cross", diamond: "Diamond", checker: "Checker", stripes_v: "Stripes, vertical", stripes_h: "Stripes, horizontal", triangle: "Triangle", heart: "Heart", noise: "Noise" };
  const DIRS = [["forward", "Forward"], ["reverse", "Reverse"], ["bounce", "Bounce"], ["wings", "Wings"]];
  const AXES = [["x", "Horizontal"], ["y", "Vertical"]];
  const WHITE = { r: 255, g: 255, b: 255, w: 0 }, MAGENTA = { r: 213, g: 19, b: 77, w: 0 }, BLUE = { r: 0, g: 60, b: 255, w: 0 };

  function defaultLayer(layer, mode, prev) {
    const colours = prev && (prev.stops ? prev.stops.map(s => s.color) : prev.colors || (prev.color ? [prev.color] : null));
    const c0 = (colours && colours[0]) || WHITE, c1 = (colours && colours[1]) || MAGENTA, c2 = (colours && colours[2]) || BLUE;
    if (layer === "colour") {
      if (mode === "solid") return { mode, color: clone(c0) };
      if (mode === "gradient") return { mode, axis: "x", stops: [{ offset: 0, color: clone(c0) }, { offset: 1, color: clone(c1) }] };
      if (mode === "color_cycle") return { mode, colors: [clone(c0), clone(c1), clone(c2)], period_s: 5 };
      if (mode === "sweep") return { mode, axis: "x", stops: [{ offset: 0, color: clone(c0) }, { offset: 1, color: clone(c1) }], period_s: 4, direction: "forward" };
    }
    if (layer === "effect") {
      if (mode === "pulse") return { mode, period_s: 2 };
      if (mode === "strobe") return { mode, on_ms: 100, off_ms: 400 };
      if (mode === "chase") return { mode, period_s: 1, tail: 3, axis: "x", direction: "forward" };
      if (mode === "sine_chase") return { mode, period_s: 2, wavelength: 4, axis: "x", direction: "forward" };
      if (mode === "trickle") return { mode, period_s: 3, floor: 0 };
      if (mode === "shader") return { mode, shader: "circle_scroll", speed: 1, n_items: 20, force: 3, force2: 5 };
    }
    if (layer === "video" && mode === "bitmap") return { mode, bitmap: "dot", axis: "x", period_s: 0, direction: "forward" };
    return { mode: "off" };
  }

  function bitmapThumb(name) {
    const g = HotaEngine.BITMAP_GRIDS[name], c = h("canvas", { width: 32, height: 16, "aria-hidden": "true" });
    const ctx = c.getContext("2d"), img = ctx.createImageData(32, 16);
    for (let y = 0; y < 16; y++) for (let x = 0; x < 32; x++) {
      const v = Math.round(g[y][x] * 255), i = (y * 32 + x) * 4;
      img.data[i] = v; img.data[i + 1] = v; img.data[i + 2] = v; img.data[i + 3] = 255;
    }
    ctx.putImageData(img, 0, 0);
    return c;
  }

  // Builds the three-layer editor into `root`. `onChange(look)` fires on
  // every edit; call `.set(look)` to load a different look.
  function LookEditor(root, onChange, opts = {}) {
    let look = clone(HotaEngine.OFF);
    const memory = { colour: {}, effect: {}, video: {} }; // last params per mode, so flipping modes doesn't lose work
    const open = { colour: true, effect: opts.openEffect !== false, video: false };

    const changed = () => { onChange(clone(look)); };

    function numField(label, obj, key, o) {
      const id = `f${Math.random().toString(36).slice(2, 8)}`;
      const range = h("input", { type: "range", min: o.min, max: o.max, step: o.step, value: obj[key], "aria-label": label });
      const num = h("input", { type: "number", id, min: o.min, step: o.step, value: obj[key], style: "width:70px" });
      const sync = (v, from) => {
        let val = parseFloat(v);
        if (!Number.isFinite(val)) return;
        if (o.int) val = Math.round(val);
        if (o.minExclusive != null && val <= o.minExclusive) val = o.min;
        if (o.min != null && val < o.min && !o.allowBelow) val = o.min;
        if (o.hardMax != null && val > o.hardMax) val = o.hardMax;
        obj[key] = o.scale ? val / o.scale : val;
        if (from !== range) range.value = val;
        if (from !== num) num.value = val;
        changed();
      };
      const shown = o.scale ? Math.round(obj[key] * o.scale) : obj[key];
      range.value = shown; num.value = shown;
      range.addEventListener("input", () => sync(range.value, range));
      num.addEventListener("change", () => sync(num.value, num));
      return h("div", { class: "field" }, h("label", { for: id, text: label + (o.unit ? ` (${o.unit})` : "") }), h("div", { class: "row" }, range, num));
    }
    function selField(label, obj, key, options, after) {
      const id = `f${Math.random().toString(36).slice(2, 8)}`;
      const sel = h("select", { id }, options.map(([v, t]) => h("option", { value: v, text: t, selected: obj[key] === v })));
      sel.addEventListener("change", () => { obj[key] = sel.value; changed(); if (after) after(); });
      return h("div", { class: "field" }, h("label", { for: id, text: label }), sel);
    }
    function colourField(label, colour, onDelete) {
      const pick = h("input", { type: "color", value: toHex(colour), "aria-label": `${label} colour` });
      const w = h("input", { type: "number", class: "w", min: 0, max: 255, step: 1, value: colour.w || 0, title: "White channel (RGBW strips only)", "aria-label": `${label} white channel` });
      pick.addEventListener("input", () => { Object.assign(colour, fromHex(pick.value, colour.w || 0)); changed(); });
      w.addEventListener("change", () => { colour.w = clamp(Math.round(+w.value || 0), 0, 255); w.value = colour.w; changed(); });
      return h("div", { class: "field" }, h("span", { class: "lbl", text: label }),
        h("div", { class: "colour-in" }, pick, h("span", { class: "hint", style: "margin:0", text: "W" }), w,
          onDelete ? h("button", { type: "button", class: "btn sm icon ghost", "aria-label": `Remove ${label}`, onclick: onDelete, text: "×" }) : null));
    }
    function chips(options, current, onPick) {
      return h("div", { class: "chips" }, options.map(([v, t]) =>
        h("button", { type: "button", "aria-pressed": current === v, text: t, onclick: () => onPick(v) })));
    }
    function setMode(layer, mode) {
      const cur = look[layer] || { mode: "off" };
      if (cur.mode === mode) return;
      memory[layer][cur.mode] = cur;
      look[layer] = memory[layer][mode] ? clone(memory[layer][mode]) : defaultLayer(layer, mode, cur);
      render(); changed();
    }

    function colourBody(L) {
      const out = [chips(COLOUR_MODES, L.mode, m => setMode("colour", m))];
      if (L.mode === "solid") out.push(colourField("Colour", L.color));
      if (L.mode === "gradient" || L.mode === "sweep") {
        out.push(selField("Axis", L, "axis", AXES));
        const list = h("div", { class: "stops" });
        L.stops.sort((a, b) => a.offset - b.offset).forEach((s, i) => {
          list.append(colourField(`Stop ${i + 1}`, s.color, L.stops.length > 2 ? () => { L.stops.splice(i, 1); render(); changed(); } : null));
          list.append(numField(`Stop ${i + 1} at`, s, "offset", { min: 0, max: 1, step: 0.01, hardMax: 1 }));
        });
        out.push(list);
        if (L.stops.length < 6) out.push(h("button", { type: "button", class: "btn sm", style: "margin-top:8px", text: "Add stop", onclick: () => {
          const last = L.stops[L.stops.length - 1];
          L.stops.forEach((s, i) => { s.offset = Math.round(i / L.stops.length * 100) / 100; });
          L.stops.push({ offset: 1, color: clone(last.color) }); render(); changed();
        } }));
        if (L.mode === "sweep") {
          out.push(numField("Period", L, "period_s", { min: 0.1, max: 60, step: 0.1, unit: "s", minExclusive: 0 }));
          out.push(selField("Direction", L, "direction", DIRS));
        }
      }
      if (L.mode === "color_cycle") {
        L.colors.forEach((c, i) => out.push(colourField(`Colour ${i + 1}`, c, L.colors.length > 2 ? () => { L.colors.splice(i, 1); render(); changed(); } : null)));
        if (L.colors.length < 8) out.push(h("button", { type: "button", class: "btn sm", style: "margin-top:8px", text: "Add colour", onclick: () => { L.colors.push(clone(L.colors[L.colors.length - 1])); render(); changed(); } }));
        out.push(numField("Period", L, "period_s", { min: 0.1, max: 60, step: 0.1, unit: "s", minExclusive: 0 }));
      }
      return out;
    }
    function effectBody(L) {
      const out = [chips(EFFECT_MODES, L.mode, m => setMode("effect", m))];
      if (L.mode === "pulse") out.push(numField("Period", L, "period_s", { min: 0.1, max: 20, step: 0.1, unit: "s" }));
      if (L.mode === "strobe") {
        out.push(h("p", { class: "hint warn-text", text: "Fast flashing can affect people with photosensitive epilepsy. Keep the off time long in public hours." }));
        out.push(numField("On", L, "on_ms", { min: 10, max: 2000, step: 10, unit: "ms", int: true }));
        out.push(numField("Off", L, "off_ms", { min: 10, max: 5000, step: 10, unit: "ms", int: true }));
      }
      if (L.mode === "chase" || L.mode === "sine_chase") {
        out.push(numField("Period", L, "period_s", { min: 0.1, max: 20, step: 0.1, unit: "s" }));
        if (L.mode === "chase") out.push(numField("Tail", L, "tail", { min: 0, max: 100, step: 0.5, unit: "% of span" }));
        else out.push(numField("Wavelength", L, "wavelength", { min: 0.5, max: 100, step: 0.5, unit: "% of span" }));
        out.push(selField("Axis", L, "axis", AXES));
        out.push(selField("Direction", L, "direction", DIRS));
      }
      if (L.mode === "trickle") {
        out.push(numField("Period", L, "period_s", { min: 0.1, max: 20, step: 0.1, unit: "s" }));
        out.push(numField("Floor", L, "floor", { min: 0, max: 100, step: 1, unit: "%", scale: 100, hardMax: 100 }));
      }
      if (L.mode === "shader") {
        out.push(h("div", { style: "margin-top:8px" }, chips(HotaEngine.SHADERS.map(s => [s, SHADER_LABEL[s] || s]), L.shader, s => { L.shader = s; render(); changed(); })));
        out.push(numField("Speed", L, "speed", { min: 0.05, max: 10, step: 0.05 }));
        if (L.shader !== "plasma") {
          out.push(numField(L.shader === "radar" ? "Arms" : "Count", L, "n_items", { min: 1, max: 64, step: 1 }));
          out.push(numField("Width", L, "force", { min: 1, max: 10, step: 0.1 }));
          out.push(numField("Softness", L, "force2", { min: 1, max: 10, step: 0.1 }));
        }
      }
      return out;
    }
    function videoBody(L) {
      const grid = h("div", { class: "bitmaps" },
        h("button", { type: "button", "aria-pressed": L.mode === "off", onclick: () => setMode("video", "off"), text: "Off" }),
        HotaEngine.BITMAPS.map(b => h("button", { type: "button", title: BITMAP_LABEL[b] || b, "aria-label": BITMAP_LABEL[b] || b,
          "aria-pressed": L.mode === "bitmap" && L.bitmap === b,
          onclick: () => { if (L.mode !== "bitmap") setMode("video", "bitmap"); look.video.bitmap = b; render(); changed(); } }, bitmapThumb(b))));
      const out = [grid];
      if (L.mode === "bitmap") {
        out.push(numField("Period", L, "period_s", { min: 0, max: 60, step: 0.1, unit: "s, 0 = still" }));
        out.push(selField("Axis", L, "axis", AXES));
        out.push(selField("Direction", L, "direction", DIRS));
      }
      return out;
    }

    function section(key, title, meta, body, active) {
      const d = h("details", { class: "sec", open: open[key] }, h("summary", null, title, h("span", { class: active ? "meta on" : "meta", text: meta })), h("div", { class: "body" }, body));
      d.addEventListener("toggle", () => { open[key] = d.open; });
      return d;
    }
    function render() {
      const c = look.colour || { mode: "off" }, e = look.effect || { mode: "off" }, v = look.video || { mode: "off" };
      look.colour = c; look.effect = e; look.video = v;
      const label = (list, m) => (list.find(x => x[0] === m) || [m, m])[1];
      root.replaceChildren(
        section("colour", "Colour", label(COLOUR_MODES, c.mode), colourBody(c), c.mode !== "off"),
        section("effect", "Effect", e.mode === "shader" ? SHADER_LABEL[e.shader] || e.shader : label(EFFECT_MODES, e.mode), effectBody(e), e.mode !== "off"),
        section("video", "Video", v.mode === "bitmap" ? BITMAP_LABEL[v.bitmap] || v.bitmap : "Off", videoBody(v), v.mode !== "off"),
      );
    }
    render();
    return {
      set(l) { look = clone(l || HotaEngine.OFF); memory.colour = {}; memory.effect = {}; memory.video = {}; render(); },
      get: () => clone(look),
    };
  }

  // Animated swatch for a look (presets, schedule rows).
  const swatches = new Set();
  function lookSwatch(look, cls = "swatch") {
    const c = h("canvas", { class: cls, width: 96, height: 14, "aria-hidden": "true" });
    c._look = look; swatches.add(c);
    paintSwatch(c, 0);
    return c;
  }
  function paintSwatch(c, t) {
    const ctx = c.getContext("2d"), n = 24, w = c.width / n, cols = HotaEngine.sampleStrip(c._look, n, t);
    ctx.fillStyle = "#000"; ctx.fillRect(0, 0, c.width, c.height);
    cols.forEach((col, i) => {
      ctx.fillStyle = `rgb(${Math.min(255, col[0] + col[3])},${Math.min(255, col[1] + col[3])},${Math.min(255, col[2] + col[3])})`;
      ctx.fillRect(i * w + 0.5, 2, w - 1, c.height - 4);
    });
  }
  let swatchT = 0;
  setInterval(() => {
    if (prefersReduced || document.hidden) return;
    swatchT += 0.066;
    for (const c of swatches) { if (!c.isConnected) { swatches.delete(c); continue; } paintSwatch(c, swatchT); }
  }, 66);

  // ===================================================================
  // LIVE: state
  // ===================================================================
  const live = {
    layoutName: null,
    selection: new Set(),
    edit: false, draft: null, dirty: false,
    cam: { x: 0, y: 0, s: 1 },
    hover: null,
    marquee: null,
    preview: null,
  };
  const isBuilding = () => live.layoutName === "Building" && elev;
  const currentLayout = () => allLayouts().find(l => l.name === live.layoutName) || null;
  const layoutFixtures = () => {
    const l = live.edit && live.draft ? live.draft : currentLayout();
    return l ? l.fixtures : {};
  };

  // ===================================================================
  // LIVE: canvas
  // ===================================================================
  const wrap = $("#canvasWrap"), canvas = $("#canvas"), ctx = canvas.getContext("2d");
  const base = document.createElement("canvas"), bctx = base.getContext("2d");
  const light = document.createElement("canvas"), lctx = light.getContext("2d");
  let W = 0, H = 0, DPR = 1, baseDirty = true;
  const filterOK = (() => { const t = document.createElement("canvas").getContext("2d"); t.filter = "blur(2px)"; return t.filter === "blur(2px)"; })();

  function resize() {
    const r = wrap.getBoundingClientRect();
    if (!r.width || !r.height) return;
    DPR = Math.min(2, devicePixelRatio || 1);
    W = r.width; H = r.height;
    for (const c of [canvas, base, light]) { c.width = Math.round(W * DPR); c.height = Math.round(H * DPR); }
    baseDirty = true;
  }
  // Refit on resize unless the user has zoomed/panned by hand (currentView null).
  new ResizeObserver(() => { const had = W; resize(); if (!had || currentView) fitView(currentView || "all"); }).observe(wrap);

  function worldBox(which) {
    if (isBuilding()) {
      const E = elev.elevations;
      const pick = which && which !== "all" ? E.filter(e => e.name === which) : E;
      const ys = [];
      for (const e of pick) for (const p of e.outline) for (const q of p) ys.push(q[1]);
      return { minX: Math.min(...pick.map(e => e.x0)), maxX: Math.max(...pick.map(e => e.x1)), minY: Math.min(...ys), maxY: Math.max(...ys) };
    }
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const pts of Object.values(layoutFixtures())) for (const [x, y] of pts) {
      minX = Math.min(minX, x); maxX = Math.max(maxX, x); minY = Math.min(minY, y); maxY = Math.max(maxY, y);
    }
    if (minX === Infinity) return { minX: 0, maxX: 1, minY: 0, maxY: 1 };
    return { minX, maxX, minY, maxY };
  }
  let currentView = "all";
  function fitView(which = currentView) {
    currentView = which;
    if (!W) return;
    const b = worldBox(which);
    const bw = Math.max(1, b.maxX - b.minX), bh = Math.max(1, b.maxY - b.minY);
    // Building view keeps a left gutter for the floor-level labels.
    const padTop = document.querySelector(".toolbar").getBoundingClientRect().height + 22, pad = 28, padBottom = isBuilding() ? 48 : 28, padLeft = isBuilding() ? 86 : 28;
    const s = Math.min((W - padLeft - pad) / bw, (H - padTop - padBottom) / bh);
    live.cam = { s, x: (b.minX + b.maxX) / 2 - ((padLeft - pad) / 2) / s, y: (b.minY + b.maxY) / 2 - ((padTop - padBottom) / 2) / s };
    baseDirty = true;
    document.querySelectorAll("#viewSeg button").forEach(btn => btn.setAttribute("aria-pressed", btn.dataset.view === which));
  }
  const sx = x => (x - live.cam.x) * live.cam.s + W / 2;
  const sy = y => (y - live.cam.y) * live.cam.s + H / 2;
  const wx = px => (px - W / 2) / live.cam.s + live.cam.x;
  const wy = py => (py - H / 2) / live.cam.s + live.cam.y;
  function zoomAt(factor, px = W / 2, py = H / 2) {
    const x = wx(px), y = wy(py);
    const fit = worldBox("all"), minS = Math.min(W / (fit.maxX - fit.minX || 1), H / (fit.maxY - fit.minY || 1)) * 0.5;
    live.cam.s = clamp(live.cam.s * factor, minS, 6);
    live.cam.x = x - (px - W / 2) / live.cam.s;
    live.cam.y = y - (py - H / 2) / live.cam.s;
    currentView = null;
    document.querySelectorAll("#viewSeg button").forEach(btn => btn.setAttribute("aria-pressed", "false"));
    baseDirty = true;
  }

  // Background image (controller only), cached per layout/filename.
  const bgCache = new Map();
  function backgroundImage(layout) {
    if (!layout || !layout.background || !api.backgroundUrl) return null;
    const k = `${layout.name}|${layout.background}`;
    if (!bgCache.has(k)) {
      const img = new Image();
      img.onload = () => { baseDirty = true; };
      const url = api.backgroundUrl(layout.name);
      if (url) img.src = url;
      bgCache.set(k, img);
    }
    const img = bgCache.get(k);
    return img.complete && img.naturalWidth ? img : null;
  }

  function drawBase() {
    const c = bctx;
    c.setTransform(DPR, 0, 0, DPR, 0, 0);
    c.fillStyle = "#0f0e0e"; c.fillRect(0, 0, W, H);
    const s = live.cam.s;

    if (isBuilding()) {
      // Floor levels: datum lines across the whole drawing, labelled at the left.
      c.save();
      c.setLineDash([10, 4, 2, 4]); c.lineWidth = 1; c.strokeStyle = "rgba(245,245,245,.10)";
      c.font = "11px Rubik, sans-serif"; c.fillStyle = "rgba(245,245,245,.38)"; c.textBaseline = "bottom";
      const x0 = sx(elev.elevations[0].x0) - 78, x1 = sx(elev.elevations[elev.elevations.length - 1].x1) + 30;
      for (const lv of elev.levels) {
        const y = Math.round(sy(lv.y)) + 0.5;
        if (y < -10 || y > H + 10) continue;
        c.beginPath(); c.moveTo(Math.max(0, x0), y); c.lineTo(x1, y); c.stroke();
        c.fillText(lv.name, Math.max(6, x0), y - 3);
      }
      c.restore();

      const path = (polys, close) => {
        c.beginPath();
        for (const p of polys) {
          c.moveTo(sx(p[0][0]), sy(p[0][1]));
          for (let i = 1; i < p.length; i++) c.lineTo(sx(p[i][0]), sy(p[i][1]));
          if (close) c.closePath();
        }
      };
      for (const e of elev.elevations) {
        if (sx(e.x1) < -50 || sx(e.x0) > W + 50) continue;
        c.lineWidth = 1;
        c.strokeStyle = "rgba(245,245,245,.07)"; path(e.grid, false); c.stroke();
        c.strokeStyle = "rgba(245,245,245,.42)"; c.lineWidth = s > 0.25 ? 1.1 : 0.8; path(e.bands, true); c.stroke();
        c.strokeStyle = "rgba(245,245,245,.92)"; c.lineWidth = 1.4; path(e.outline, true); c.stroke();
        // Title under each elevation, drawing-sheet style.
        const ys = e.outline.flat().map(p => p[1]);
        const ty = sy(Math.max(...ys)) + 22, tx = sx(e.x0);
        // Shorten the title when the elevation is drawn too small for it.
        const room = sx(e.x1) - tx;
        c.fillStyle = "rgba(245,245,245,.85)"; c.font = "500 12px Rubik, sans-serif"; c.textBaseline = "alphabetic";
        let title = `${e.name} elevation`;
        if (c.measureText(title).width > room) title = e.name;
        c.fillText(title, tx, ty);
        const tw = c.measureText(title).width;
        c.font = "11px Rubik, sans-serif";
        if (tw + 10 + c.measureText("1:100").width <= room) { c.fillStyle = "rgba(245,245,245,.4)"; c.fillText("1:100", tx + tw + 10, ty); }
        c.strokeStyle = "rgba(245,245,245,.25)"; c.lineWidth = 1;
        c.beginPath(); c.moveTo(tx, ty + 6.5); c.lineTo(sx(e.x1), ty + 6.5); c.stroke();
      }
    } else {
      const img = backgroundImage(currentLayout());
      if (img) {
        const b = worldBox();
        const bw = (b.maxX - b.minX) * s, bh = (b.maxY - b.minY) * s;
        const k = Math.min(bw / img.naturalWidth, bh / img.naturalHeight) * 1.08;
        const w = img.naturalWidth * k, hh = img.naturalHeight * k;
        c.globalAlpha = 0.45;
        c.drawImage(img, sx((b.minX + b.maxX) / 2) - w / 2, sy((b.minY + b.maxY) / 2) - hh / 2, w, hh);
        c.globalAlpha = 1;
      }
    }
    baseDirty = false;
  }

  // Per-frame geometry: every visible LED in screen space.
  let leds = []; // {key, fi, i, x, y}
  function buildLeds() {
    leds = [];
    const lf = layoutFixtures();
    for (const [key, pts] of Object.entries(lf)) {
      const fi = fixtureIndex.get(key);
      if (fi == null) continue;
      const f = cfg.fixtures[fi], n = f.led_count;
      const [[x1, y1], [x2, y2]] = pts;
      for (let i = 0; i < n; i++) {
        const t = n > 1 ? i / (n - 1) : 0;
        leds.push({ key, fi, i, x: sx(x1 + (x2 - x1) * t), y: sy(y1 + (y2 - y1) * t), dish: f.zone === "dishes" || f.led_type === "RGB" });
      }
    }
  }

  function ledColour(fi, i) {
    const p = live.preview && live.preview[fi];
    const c = p && p.colors[i];
    if (!c) return null;
    const w = c[3] || 0;
    return [Math.min(255, c[0] + w), Math.min(255, c[1] + w * 0.96), Math.min(255, c[2] + w * 0.88)];
  }

  function draw() {
    if (!W) return;
    if (baseDirty) drawBase();
    buildLeds();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.drawImage(base, 0, 0);
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);

    const s = live.cam.s;
    const r = clamp(s * 22, 1.6, 5);            // LED dot radius
    const rDish = clamp(s * 38, 3, 9);

    // Selection underlay.
    if (live.selection.size) {
      ctx.fillStyle = "rgba(213,19,77,.55)";
      for (const L of leds) if (live.selection.has(L.key)) {
        ctx.beginPath(); ctx.arc(L.x, L.y, (L.dish ? rDish : r) + 3.5, 0, Math.PI * 2); ctx.fill();
      }
    }

    // Lights into their own layer, so the glow can be blurred from it.
    lctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    lctx.clearRect(0, 0, W, H);
    ctx.fillStyle = "#3f3c3c";
    let prev = null;
    for (const L of leds) {
      const c = ledColour(L.fi, L.i);
      const lit = c && (c[0] + c[1] + c[2]) > 24;
      if (!lit) {
        ctx.beginPath(); ctx.arc(L.x, L.y, L.dish ? rDish * 0.7 : r * 0.6, 0, Math.PI * 2); ctx.fill();
        prev = L; continue;
      }
      const col = `rgb(${c[0] | 0},${c[1] | 0},${c[2] | 0})`;
      lctx.fillStyle = col;
      // Strip LEDs join up into tape with a short segment to the previous LED.
      if (!L.dish && prev && prev.key === L.key) {
        lctx.strokeStyle = col; lctx.lineWidth = r * 1.5; lctx.lineCap = "round";
        lctx.beginPath(); lctx.moveTo(prev.x, prev.y); lctx.lineTo(L.x, L.y); lctx.stroke();
      }
      lctx.beginPath(); lctx.arc(L.x, L.y, L.dish ? rDish : r, 0, Math.PI * 2); lctx.fill();
      prev = L;
    }

    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.globalCompositeOperation = "lighter";
    if (filterOK) {
      // Real gaussian blur in two radii: a wide soft halo and a tight bloom.
      ctx.filter = `blur(${Math.round(14 * DPR)}px)`; ctx.globalAlpha = 0.65; ctx.drawImage(light, 0, 0);
      ctx.filter = `blur(${Math.round(4 * DPR)}px)`; ctx.globalAlpha = 0.55; ctx.drawImage(light, 0, 0);
      ctx.filter = "none";
    } else {
      ctx.shadowBlur = 12 * DPR; ctx.shadowColor = "rgba(255,255,255,.35)";
    }
    // The LEDs themselves go on top normally (not added), so their colour
    // stays true instead of washing out towards white.
    ctx.globalCompositeOperation = "source-over";
    ctx.globalAlpha = 1; ctx.drawImage(light, 0, 0);
    ctx.shadowBlur = 0;
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);

    if (live.hover) {
      const L = live.hover;
      ctx.strokeStyle = "#f5f5f5"; ctx.lineWidth = 1.2;
      ctx.beginPath(); ctx.arc(L.x, L.y, (L.dish ? rDish : r) + 4, 0, Math.PI * 2); ctx.stroke();
    }
    if (live.marquee) {
      const m = live.marquee;
      ctx.setLineDash([4, 3]); ctx.strokeStyle = "#f0336c"; ctx.fillStyle = "rgba(213,19,77,.08)"; ctx.lineWidth = 1;
      const x = Math.min(m.x0, m.x1), y = Math.min(m.y0, m.y1), w = Math.abs(m.x1 - m.x0), hh = Math.abs(m.y1 - m.y0);
      ctx.fillRect(x, y, w, hh); ctx.strokeRect(x + 0.5, y + 0.5, w, hh); ctx.setLineDash([]);
    }
  }

  // Preview: polled from the controller, or computed in-page for the demo.
  // Preview polling doubles as the connection check: a few failures in a
  // row mark the controller as lost (red header, polling slows to every 2 s
  // so the console doesn't flood); the first success reconnects.
  let previewBusy = false, failStreak = 0, pollCount = 0, lastLookEdit = 0;
  async function pollPreview() {
    if (previewBusy) return;
    previewBusy = true;
    try {
      live.preview = await api.getPreview();
      failStreak = 0;
      if (offline) await goOnline();
    } catch {
      if (++failStreak >= 5 && !offline && api.kind === "controller") goOffline();
    }
    previewBusy = false;
  }
  function goOffline() {
    offline = true;
    setConn();
    toast(`Lost connection to the controller at ${location.host}. Changes won't save until it's back; this page keeps retrying.`, true);
  }
  async function goOnline() {
    offline = false;
    try { cfg = await api.getConfig(); indexConfig(); } catch { /* keep what we had */ }
    setConn();
    renderLayoutSelect(); renderSelectionTools(); renderPresets(); selectionChanged();
    toast("Reconnected to the controller.");
  }
  // Keeps the "own look" notice honest when the scheduler, randomiser or
  // another browser changes per-fixture looks behind this page's back.
  async function refreshCurrentLook() {
    if (Date.now() - lastLookEdit < 3000) return; // a save may still be on its way
    try {
      const st = await (await fetch("/api/status", { cache: "no-store" })).json();
      if (JSON.stringify(st.current_look) !== JSON.stringify(cfg.current_look)) { cfg.current_look = st.current_look; updateTarget(); }
    } catch { /* the preview poll reports outages */ }
  }
  if (api.kind === "controller") {
    (function loop() {
      if (offline || (!document.hidden && !tabs.live.hidden)) pollPreview();
      if (!offline && ++pollCount % 50 === 0) refreshCurrentLook(); // every ~5 s
      setTimeout(loop, offline ? 2000 : 100);
    })();
  }
  function frame() {
    if (!tabs.live.hidden) {
      if (api.kind === "local") pollPreview();
      if (!view3dOn) draw();
    }
    requestAnimationFrame(frame);
  }

  // -- pointer interaction ------------------------------------------------
  function hitTest(px, py) {
    let best = null, bd = 14 * 14;
    for (const L of leds) {
      const d = (L.x - px) ** 2 + (L.y - py) ** 2;
      if (d < bd) { bd = d; best = L; }
    }
    return best;
  }
  function describe(L) {
    const f = cfg.fixtures[L.fi];
    const c = live.preview && live.preview[L.fi] && live.preview[L.fi].colors[L.i];
    const col = c ? `R${c[0]} G${c[1]} B${c[2]}${f.led_type === "RGBW" ? ` W${c[3]}` : ""}` : "";
    return `${f.name}   universe ${f.universe + 1}, address ${f.address + 1}   ${f.led_count} LED${f.led_count > 1 ? "s" : ""} ${f.led_type}   ${col}`;
  }
  let drag = null;
  canvas.addEventListener("pointerdown", e => {
    // Focus for the keyboard shortcuts, but flag it as mouse focus so the
    // keyboard focus ring isn't drawn round the whole drawing.
    canvas.dataset.pointerFocus = "";
    canvas.focus({ preventScroll: true, focusVisible: false });
    const r = canvas.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top;
    canvas.setPointerCapture(e.pointerId);
    const additive = e.shiftKey || e.ctrlKey || e.metaKey;
    if (e.button === 1 || e.button === 2 || e.altKey || spaceDown) { drag = { type: "pan", px, py, cx: live.cam.x, cy: live.cam.y }; return; }
    const hit = hitTest(px, py);
    if (live.edit && hit) {
      if (!live.selection.has(hit.key)) { if (!additive) live.selection.clear(); live.selection.add(hit.key); selectionChanged(); }
      drag = { type: "move", px, py, lx: wx(px), ly: wy(py), moved: false };
      return;
    }
    drag = { type: "select", px, py, hit, additive, moved: false };
  });
  canvas.addEventListener("pointermove", e => {
    const r = canvas.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top;
    if (!drag) {
      live.hover = hitTest(px, py);
      $("#readout").textContent = live.hover ? describe(live.hover) : "";
      canvas.style.cursor = live.hover ? (live.edit ? "move" : "pointer") : (spaceDown ? "grab" : "crosshair");
      return;
    }
    if (drag.type === "pan") {
      live.cam.x = drag.cx - (px - drag.px) / live.cam.s;
      live.cam.y = drag.cy - (py - drag.py) / live.cam.s;
      baseDirty = true; return;
    }
    if (Math.hypot(px - drag.px, py - drag.py) > 3) drag.moved = true;
    if (drag.type === "move" && drag.moved) {
      const x = wx(px), y = wy(py);
      moveSelection(x - drag.lx, y - drag.ly);
      drag.lx = x; drag.ly = y;
    }
    if (drag.type === "select" && drag.moved) live.marquee = { x0: drag.px, y0: drag.py, x1: px, y1: py };
  });
  canvas.addEventListener("pointerup", () => {
    if (!drag) return;
    if (drag.type === "select") {
      if (live.marquee) {
        const m = live.marquee, x0 = Math.min(m.x0, m.x1), x1 = Math.max(m.x0, m.x1), y0 = Math.min(m.y0, m.y1), y1 = Math.max(m.y0, m.y1);
        if (!drag.additive) live.selection.clear();
        for (const L of leds) if (L.x >= x0 && L.x <= x1 && L.y >= y0 && L.y <= y1) live.selection.add(L.key);
        live.marquee = null;
      } else if (drag.hit) {
        const k = drag.hit.key;
        if (drag.additive) { live.selection.has(k) ? live.selection.delete(k) : live.selection.add(k); }
        else if (live.selection.size === 1 && live.selection.has(k)) live.selection.clear();
        else { live.selection.clear(); live.selection.add(k); }
      } else if (!drag.additive) live.selection.clear();
      selectionChanged();
    }
    drag = null;
  });
  canvas.addEventListener("pointerleave", () => { if (!drag) { live.hover = null; $("#readout").textContent = ""; } });
  canvas.addEventListener("contextmenu", e => e.preventDefault());
  canvas.addEventListener("wheel", e => {
    e.preventDefault();
    const r = canvas.getBoundingClientRect();
    zoomAt(Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0015)), e.clientX - r.left, e.clientY - r.top);
  }, { passive: false });
  let spaceDown = false;
  // Tabbing in (focus not from a pointer press) shows the ring again.
  canvas.addEventListener("blur", () => { delete canvas.dataset.pointerFocus; });
  canvas.addEventListener("keydown", e => {
    if (e.code === "Space") { spaceDown = true; e.preventDefault(); return; }
    if (e.key === "Escape") { live.selection.clear(); selectionChanged(); }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a") { e.preventDefault(); selectKeys(Object.keys(layoutFixtures())); }
    if (e.key === "+" || e.key === "=") zoomAt(1.25);
    if (e.key === "-") zoomAt(0.8);
    if (e.key === "0") fitView("all");
    if (live.edit && live.selection.size && e.key.startsWith("Arrow")) {
      e.preventDefault();
      const step = (e.shiftKey ? 20 : 2) / live.cam.s;
      moveSelection(e.key === "ArrowLeft" ? -step : e.key === "ArrowRight" ? step : 0, e.key === "ArrowUp" ? -step : e.key === "ArrowDown" ? step : 0);
    }
  });
  canvas.addEventListener("keyup", e => { if (e.code === "Space") spaceDown = false; });
  $("#zoomIn").addEventListener("click", () => zoomAt(1.3));
  $("#zoomOut").addEventListener("click", () => zoomAt(1 / 1.3));
  $("#zoomFit").addEventListener("click", () => fitView("all"));

  // -- 3D view (prototype) -----------------------------------------------------
  let view3dOn = false, view3d = null, view3dLoading = false;
  async function setView3d(on) {
    if (on && !view3d) {
      if (view3dLoading) return;
      view3dLoading = true;
      $("#toggle3d").textContent = "Loading 3D…";
      try {
        view3d = await HotaView3D.create($("#view3d"), {
          elev, b3d,
          getLayout: () => allLayouts().find(l => l.name === "Building"),
          getFixtures: () => cfg.fixtures,
          getPreview: () => live.preview,
        });
        $("#view3dSeg").replaceChildren(...view3d.views.map(v => h("button", { type: "button", text: v, onclick: () => view3d.setView(v) })));
      } catch (e) {
        toast(`The 3D view couldn't start: ${errText(e)}`, true);
        on = false;
      }
      view3dLoading = false;
      $("#toggle3d").textContent = "3D";
    }
    view3dOn = on;
    $("#toggle3d").setAttribute("aria-pressed", String(on));
    $("#view3d").hidden = !on;
    canvas.hidden = on;
    $("#view3dSeg").hidden = !on;
    $("#viewSeg").hidden = on || !isBuilding();
    document.querySelector('.seg[aria-label="Zoom"]').hidden = on;
    $("#readout").textContent = on ? "Drag to orbit, right-drag to pan, scroll to zoom. Selecting fixtures works in the 2D view." : "";
    if (view3d) view3d.setActive(on);
  }
  $("#toggle3d").addEventListener("click", () => setView3d(!view3dOn));

  // -- layouts --------------------------------------------------------------
  const layoutSelect = $("#layoutSelect");
  function renderLayoutSelect() {
    const layouts = allLayouts();
    if (!layouts.find(l => l.name === live.layoutName)) live.layoutName = (layouts.find(l => l.name === "Building") || layouts[0] || {}).name || null;
    layoutSelect.replaceChildren(...layouts.map(l => h("option", { value: l.name, text: l.name, selected: l.name === live.layoutName })));
    const seg = $("#viewSeg");
    seg.hidden = !isBuilding() || view3dOn;
    $("#toggle3d").hidden = !(isBuilding() && b3d && window.HotaView3D);
    if (view3dOn && !isBuilding()) setView3d(false);
    if (isBuilding()) {
      seg.replaceChildren(
        h("button", { type: "button", "data-view": "all", text: "All", onclick: () => fitView("all") }),
        ...elev.elevations.map(e => h("button", { type: "button", "data-view": e.name, text: e.name, onclick: () => fitView(e.name) })));
    }
  }
  layoutSelect.addEventListener("change", async () => {
    if (live.edit && live.dirty && !(await ask({ title: "Discard fixture moves?", message: "You've moved fixtures in this layout without saving. Switching layouts throws those moves away.", ok: "Discard moves", danger: true }))) { layoutSelect.value = live.layoutName; return; }
    exitEdit();
    live.layoutName = layoutSelect.value;
    renderLayoutSelect(); fitView("all"); renderSelectionTools();
  });

  function moveSelection(dx, dy) {
    if (!live.draft) return;
    for (const k of live.selection) {
      const p = live.draft.fixtures[k];
      if (p) live.draft.fixtures[k] = p.map(([x, y]) => [Math.round((x + dx) * 10) / 10, Math.round((y + dy) * 10) / 10]);
    }
    live.dirty = true;
    $("#editSave").disabled = false;
  }
  function enterEdit() {
    const l = currentLayout();
    if (!l) return;
    live.edit = true; live.draft = clone(l); live.dirty = false;
    $("#editbar").hidden = false; $("#editSave").disabled = true;
  }
  function exitEdit() { live.edit = false; live.draft = null; live.dirty = false; $("#editbar").hidden = true; }
  async function saveLayouts(layouts, msg) {
    try {
      cfg.layouts = await api.putLayouts(layouts);
      bgCache.clear(); baseDirty = true;
      if (view3d) view3d.rebuild();
      renderLayoutSelect();
      if (msg) toast(msg);
      return true;
    } catch (e) { toast(errText(e), true); return false; }
  }
  $("#editSave").addEventListener("click", async () => {
    const layouts = allLayouts().map(l => (l.name === live.draft.name ? live.draft : l));
    if (await saveLayouts(layouts, "Layout saved.")) exitEdit();
  });
  $("#editDiscard").addEventListener("click", () => { exitEdit(); });

  const menuBtn = $("#layoutMenuBtn"), menu = $("#layoutMenu");
  const closeMenu = () => { menu.hidden = true; menuBtn.setAttribute("aria-expanded", "false"); };
  menuBtn.addEventListener("click", e => { e.stopPropagation(); menu.hidden = !menu.hidden; menuBtn.setAttribute("aria-expanded", String(!menu.hidden)); });
  document.addEventListener("click", e => { if (!menu.contains(e.target) && e.target !== menuBtn) closeMenu(); });
  menu.addEventListener("click", async e => {
    const act = e.target.closest("button") && e.target.closest("button").dataset.act;
    if (!act) return;
    closeMenu();
    const layouts = clone(allLayouts()), cur = currentLayout();
    if (act === "edit") { if (live.edit) exitEdit(); else enterEdit(); }
    if (act === "new") {
      const name = ((await ask({ title: "New layout", message: cur ? `It starts as a copy of "${cur.name}".` : "", input: true, placeholder: "Layout name", ok: "Create layout" })) || "").trim();
      if (!name) return;
      if (layouts.some(l => l.name === name)) return toast(`There's already a layout called "${name}".`, true);
      layouts.push({ name, fixtures: cur ? clone(cur.fixtures) : {} });
      if (await saveLayouts(layouts, `Layout "${name}" created.`)) { live.layoutName = name; renderLayoutSelect(); fitView("all"); }
    }
    if (act === "rename" && cur) {
      const name = ((await ask({ title: "Rename layout", input: true, value: cur.name, ok: "Rename" })) || "").trim();
      if (!name || name === cur.name) return;
      if (layouts.some(l => l.name === name)) return toast(`There's already a layout called "${name}".`, true);
      layouts.find(l => l.name === cur.name).name = name;
      if (await saveLayouts(layouts, "Layout renamed.")) { live.layoutName = name; renderLayoutSelect(); }
    }
    if (act === "delete" && cur) {
      if (layouts.length < 2) return toast("Keep at least one layout.", true);
      if (!(await ask({ title: `Delete "${cur.name}"?`, message: "Only the layout goes. Fixtures, looks and schedules aren't affected.", ok: "Delete layout", danger: true }))) return;
      if (await saveLayouts(layouts.filter(l => l.name !== cur.name), "Layout deleted.")) { live.layoutName = null; renderLayoutSelect(); fitView("all"); }
    }
    if (act === "add-sel" && cur) {
      if (!live.selection.size) return toast("Select fixtures first (in another layout), then add them here.", true);
      const source = allLayouts().find(l => l.name === "Building");
      const target = layouts.find(l => l.name === cur.name);
      for (const k of live.selection) {
        const f = cfg.fixtures[fixtureIndex.get(k)];
        target.fixtures[k] = clone((source && source.fixtures[k]) || f.points);
      }
      saveLayouts(layouts, `Added ${live.selection.size} fixture${live.selection.size > 1 ? "s" : ""}.`);
    }
    if (act === "remove-sel" && cur) {
      if (!live.selection.size) return toast("Select the fixtures to remove first.", true);
      const target = layouts.find(l => l.name === cur.name);
      for (const k of live.selection) delete target.fixtures[k];
      saveLayouts(layouts, "Removed from this layout.");
    }
    if (act === "bg-clear" && cur) {
      try { await api.putBackground(cur.name, null); cfg = await api.getConfig(); indexConfig(); bgCache.clear(); baseDirty = true; toast("Background removed."); }
      catch (err) { toast(errText(err), true); }
    }
  });
  $("#bgFile").addEventListener("change", async e => {
    const file = e.target.files[0], cur = currentLayout();
    e.target.value = "";
    closeMenu();
    if (!file || !cur) return;
    try { await api.putBackground(cur.name, file); cfg = await api.getConfig(); indexConfig(); bgCache.clear(); baseDirty = true; toast("Background image set."); }
    catch (err) { toast(errText(err), true); }
  });

  // -- selection tools -------------------------------------------------------
  function selectKeys(keys, additive) {
    if (!additive) live.selection.clear();
    for (const k of keys) live.selection.add(k);
    selectionChanged();
  }
  const visibleKeys = () => Object.keys(layoutFixtures()).filter(k => fixtureIndex.has(k));
  const fx = k => cfg.fixtures[fixtureIndex.get(k)];
  function wallOf(k) {
    const building = allLayouts().find(l => l.name === "Building");
    const p = building && building.fixtures[k];
    if (!p || !elev) return null;
    const x = (p[0][0] + p[1][0]) / 2;
    const e = elev.elevations.find(e => x >= e.x0 - 200 && x <= e.x1 + 200);
    return e ? e.name : null;
  }
  // Selection chips. Each chip is a named set of fixtures; a chip lights up
  // when the current selection is exactly its set, or exactly the union of
  // several chips in its group (e.g. East + North via shift-click). Click
  // a lit chip to deselect its fixtures; shift-click to add to the selection.
  let selGroups = [];
  function renderSelectionTools() {
    const vis = visibleKeys();
    const strips = vis.filter(k => fx(k).led_type === "RGBW");
    const dishes = vis.filter(k => fx(k).led_type === "RGB");
    selGroups = [
      { el: $("#selQuick"), chips: [
        ["All", vis],
        ["Strips", strips],
        ["Dishes", dishes],
        ...zoneNames.filter(z => !["strips", "dishes"].includes(z)).map(z => [z, vis.filter(k => fx(k).zone === z)]),
      ] },
      { el: $("#selWalls"), chips: elev ? elev.elevations.map(e => [e.name, vis.filter(k => wallOf(k) === e.name)]) : [] },
      { el: $("#selMore"), chips: [
        ["Strip corners", strips.filter(k => fx(k).led_count === 1)],
        ["Strips, odd", strips.filter((_, i) => i % 2 === 0)],
        ["Strips, even", strips.filter((_, i) => i % 2 === 1)],
        ["Dishes, odd", dishes.filter((_, i) => i % 2 === 0)],
        ["Dishes, even", dishes.filter((_, i) => i % 2 === 1)],
      ] },
    ];
    for (const g of selGroups) {
      g.chips = g.chips.map(([label, keys]) => ({ label, keys, btn: null }));
      g.el.replaceChildren(...g.chips.map(c => (c.btn = h("button", { type: "button", text: c.label, "aria-pressed": false, disabled: !c.keys.length,
        title: c.keys.length ? `${c.keys.length} fixture${c.keys.length > 1 ? "s" : ""}. Shift-click to add to the selection.` : "None in this layout",
        onclick: ev => {
          if (c.btn.getAttribute("aria-pressed") === "true") { for (const k of c.keys) live.selection.delete(k); selectionChanged(); }
          else selectKeys(c.keys, ev.shiftKey);
        } }))));
    }
    updateSelChips();
  }
  function updateSelChips() {
    const sel = live.selection;
    const inSel = c => c.keys.length > 0 && c.keys.every(k => sel.has(k));
    const all = selGroups.flatMap(g => g.chips);
    // Exact matches anywhere win (so "All" lights alone, not every wall too).
    let lit = all.filter(c => inSel(c) && c.keys.length === sel.size);
    if (!lit.length && sel.size) {
      // Otherwise: a group whose chips wholly inside the selection add up to
      // exactly the selection (e.g. East + North after a shift-click).
      for (const g of selGroups) {
        const inside = g.chips.filter(inSel);
        if (inside.length > 1 && new Set(inside.flatMap(c => c.keys)).size === sel.size) lit = lit.concat(inside);
      }
    }
    for (const c of all) c.btn.setAttribute("aria-pressed", String(lit.includes(c)));
  }
  $("#selClear").addEventListener("click", () => { live.selection.clear(); selectionChanged(); });
  $("#selRevert").addEventListener("click", async () => {
    try {
      cfg.current_look = await api.putSelection([...live.selection], null);
      toast("Selection reverted to the default look.");
      loadEditorFromTarget();
    } catch (e) { toast(errText(e), true); }
  });

  function selectionChanged() {
    const n = live.selection.size;
    $("#selCount").textContent = n ? `${n} selected` : "";
    updateSelChips();
    $("#selRevert").hidden = !n || ![...live.selection].some(k => cfg.current_look.fixtures[k]);
    updateTarget();
    loadEditorFromTarget();
  }

  // The box above the look panel: what the edits below will affect. When
  // editing the default, warn if fixtures with their own look will ignore it.
  function updateTarget() {
    const n = live.selection.size;
    const t = $("#target");
    const own = Object.keys(cfg.current_look.fixtures || {}).filter(k => fixtureIndex.has(k));
    const total = cfg.fixtures.length;
    t.classList.toggle("sel", n > 0);
    t.classList.toggle("warn", !n && own.length > 0);
    if (n) {
      t.replaceChildren(h("b", { text: `Editing ${n} selected fixture${n > 1 ? "s" : ""}` }), h("p", { text: "Changes below apply only to the selection." }));
      return;
    }
    if (!own.length) {
      t.replaceChildren(h("b", { text: "Editing the default look" }), h("p", { text: "Applies to every fixture without its own look. Select fixtures on the drawing to give them their own." }));
      return;
    }
    t.replaceChildren(
      h("b", { text: "Editing the default look" }),
      h("p", { text: own.length === total
        ? `All ${total} fixtures have their own look, so changes here won't show until you select them or revert them.`
        : `${own.length} of ${total} fixtures have their own look and won't follow these changes. Select them to edit them.` }),
      h("div", { style: "display:flex;gap:6px;flex-wrap:wrap;margin-top:8px" },
        h("button", { type: "button", class: "btn sm", text: "Select them", onclick: () => selectKeys(own) }),
        h("button", { type: "button", class: "btn sm", text: "Revert all to default", onclick: async () => {
          try { cfg.current_look = await api.putSelection(own, null); toast("Every fixture now follows the default look."); updateTarget(); }
          catch (e) { toast(errText(e), true); }
        } })),
    );
  }

  // -- live look editor -------------------------------------------------------
  const sendLookRemote = latestOnly(async (look, keys) => {
    if (keys.length) cfg.current_look = await api.putSelection(keys, look);
    else cfg.current_look.default = await api.putLook(look);
    updateTarget();
    $("#selRevert").hidden = !live.selection.size || ![...live.selection].some(k => cfg.current_look.fixtures[k]);
  });
  // Update the page's own copy straight away, then save in the background:
  // anything that reads cfg.current_look right after a click (Export GIF,
  // the target box, presets) sees the look just picked, not the one before
  // the controller replied. The reply (or the 5 s status refresh) settles it.
  function sendLook(look, keys) {
    lastLookEdit = Date.now();
    if (keys.length) for (const k of keys) cfg.current_look.fixtures[k] = clone(look);
    else cfg.current_look.default = clone(look);
    updateTarget();
    sendLookRemote(look, keys);
  }
  const liveEditor = LookEditor($("#liveLook"), look => sendLook(look, [...live.selection]));
  function targetLook() {
    const first = [...live.selection][0];
    return (first && cfg.current_look.fixtures[first]) || cfg.current_look.default || HotaEngine.OFF;
  }
  function loadEditorFromTarget() { liveEditor.set(targetLook()); }

  // -- presets -------------------------------------------------------------------
  const presetName = (p, i) => p.name || `Preset ${i + 1}`;
  async function savePresets(list, msg) {
    try { cfg.presets = await api.putPresets(list) || list; renderPresets(); if (msg) toast(msg); }
    catch (e) { toast(errText(e), true); }
  }
  function renderPresets() {
    const list = (cfg.presets || []).slice().sort((a, b) => a.slot - b.slot);
    const row = $("#presetRow");
    row.replaceChildren(
      ...list.map((p, i) => {
        const tile = h("div", { class: "preset", role: "button", tabindex: 0, "aria-label": `Apply ${presetName(p, i)}` },
          lookSwatch(p.look, ""), h("b", { text: presetName(p, i), title: presetName(p, i) }),
          h("span", { class: "tools" },
            h("button", { type: "button", title: "Rename", "aria-label": `Rename ${presetName(p, i)}`, text: "✎", onclick: async e => {
              e.stopPropagation();
              const name = await ask({ title: "Rename preset", input: true, value: p.name || "", placeholder: presetName(p, i), ok: "Rename" });
              if (name === null) return;
              savePresets(list.map(q => (q.slot === p.slot ? { ...q, name: name.trim() || null } : q)), "Preset renamed.");
            } }),
            h("button", { type: "button", title: "Delete", "aria-label": `Delete ${presetName(p, i)}`, text: "×", onclick: async e => {
              e.stopPropagation();
              const inRandomiser = cfg.randomizer && (cfg.randomizer.preset_slots || []).includes(p.slot);
              if (!(await ask({ title: `Delete "${presetName(p, i)}"?`, message: `Schedules that used this preset keep their own copy of the look.${inRandomiser ? " It's also taken out of the randomiser." : ""}`, ok: "Delete preset", danger: true }))) return;
              // The controller rejects presets the randomiser still points at,
              // so drop it from there first.
              if (inRandomiser) {
                try { cfg.randomizer = await api.putRandomizer({ ...cfg.randomizer, preset_slots: cfg.randomizer.preset_slots.filter(s => s !== p.slot) }); }
                catch (err) { return toast(errText(err), true); }
              }
              savePresets(list.filter(q => q.slot !== p.slot), "Preset deleted.");
            } })));
        const apply = () => { liveEditor.set(p.look); sendLook(clone(p.look), [...live.selection]); toast(`${presetName(p, i)} applied${live.selection.size ? " to the selection" : ""}.`); };
        tile.addEventListener("click", apply);
        tile.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); apply(); } });
        return tile;
      }),
      h("button", { type: "button", class: "preset add", text: "Save current look", onclick: async () => {
        const name = await ask({ title: "Save as preset", message: "Stores the look shown in the panel so you can apply it in one click.", input: true, value: `Preset ${list.length + 1}`, ok: "Save preset" });
        if (name === null) return;
        const slot = list.length ? Math.max(...list.map(p => p.slot)) + 1 : 0;
        savePresets([...list, { slot, name: name.trim() || null, look: liveEditor.get() }], "Preset saved.");
      } }),
    );
  }

  // ===================================================================
  // SCHEDULES
  // ===================================================================
  const sched = { draft: null, dirty: false };
  const lookSummary = look => {
    const parts = [];
    const c = look.colour || {}, e = look.effect || {}, v = look.video || {};
    parts.push(c.mode && c.mode !== "off" ? (COLOUR_MODES.find(m => m[0] === c.mode) || [0, c.mode])[1] : "No colour");
    if (e.mode && e.mode !== "off") parts.push(e.mode === "shader" ? SHADER_LABEL[e.shader] : (EFFECT_MODES.find(m => m[0] === e.mode) || [0, e.mode])[1]);
    if (v.mode === "bitmap") parts.push(BITMAP_LABEL[v.bitmap] || v.bitmap);
    return parts.join(", ");
  };
  const fmtDate = d => {
    if (!d) return "";
    const [m, day] = d.split("-").map(Number);
    return new Date(2024, m - 1, day).toLocaleDateString([], { day: "numeric", month: "short" });
  };
  function entryActive(e, now) {
    if (e.enabled === false) return false;
    const day = DAYS[(now.getDay() + 6) % 7];
    if (!e.active_days.includes(day)) return false;
    const hm = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
    if (!(e.start_time <= hm && hm < e.end_time)) return false;
    const md = `${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
    if (e.start_date && md < e.start_date) return false;
    if (e.end_date && md > e.end_date) return false;
    return true;
  }
  const targetName = z => (z == null ? "Whole building" : `Zone: ${z}`);
  // Mirrors scheduler.py's active_entry: a priority match beats a
  // non-priority one outright regardless of list order, so the "running
  // now" indicators here agree with what the controller is actually doing.
  function activeEntryFor(list, target, now) {
    let match = null, priorityMatch = null;
    for (const e of list) {
      if ((e.zone ?? null) !== target || !entryActive(e, now)) continue;
      if (e.priority) priorityMatch = e; else match = e;
    }
    return priorityMatch ?? match;
  }

  function setSchedDirty(d) {
    sched.dirty = d;
    $("#schedSave").disabled = !d; $("#schedDiscard").disabled = !d;
  }
  function openSchedules() {
    if (!sched.draft || !sched.dirty) { sched.draft = clone(cfg.schedule || []); setSchedDirty(false); }
    renderSchedule(); renderRandomizer(); renderChime();
  }
  function renderNow() {
    const now = new Date();
    const targets = [null, ...zoneNames];
    $("#nowStrip").replaceChildren(...targets.map(t => {
      const active = activeEntryFor(sched.draft, t, now);
      return h("div", null, h("span", { text: `${targetName(t)}, now` }), h("b", { text: active ? active.name : "Nothing scheduled" }));
    }));
  }
  function renderSchedule() {
    renderNow();
    const tbl = $("#schedTable"), list = sched.draft, now = new Date();
    const nowMin = now.getHours() * 60 + now.getMinutes();
    const winners = new Map();
    for (const target of new Set(list.map(e => e.zone ?? null))) {
      const active = activeEntryFor(list, target, now);
      if (active) winners.set(target, list.indexOf(active));
    }
    if (!list.length) {
      tbl.replaceChildren(h("tbody", null, h("tr", null, h("td", { class: "empty", colspan: 7 }, "No entries yet. Add one to switch looks on automatically."))));
      return;
    }
    const toMin = t => { const [a, b] = t.split(":").map(Number); return a * 60 + b; };
    tbl.replaceChildren(
      h("thead", null, h("tr", null, ["On", "Name", "Applies to", "Days", "Time", "Dates", "Look", ""].map(t => h("th", { text: t })))),
      h("tbody", null, list.map((e, i) => {
        const on = e.enabled !== false;
        const cb = h("input", { type: "checkbox", checked: on, "aria-label": `Enable ${e.name}`, onchange: () => { e.enabled = cb.checked; setSchedDirty(true); renderSchedule(); } });
        const s = toMin(e.start_time), en = toMin(e.end_time);
        return h("tr", { class: (on ? "" : "off") + (winners.get(e.zone ?? null) === i ? " active-now" : "") },
          h("td", null, cb),
          h("td", null,
            h("b", { style: "font-weight:500", text: e.name }),
            e.priority ? h("span", { class: "badge", title: "Overrides other entries for this target, and deletes itself once it's done", text: "Priority" }) : null,
            winners.get(e.zone ?? null) === i ? h("div", { class: "hint", style: "margin:0;color:var(--accent-2)", text: "Running now" }) : null),
          h("td", { text: targetName(e.zone ?? null) }),
          h("td", null, h("span", { class: "days" }, DAYS.map(d => h("i", { class: e.active_days.includes(d) ? "on" : "", title: DAY_LABEL[d], text: DAY_LABEL[d][0] })))),
          h("td", null, `${e.start_time} to ${e.end_time}`,
            h("div", { class: "timebar", title: "Across 24 hours" }, h("b", { style: `left:${s / 14.4}%;width:${Math.max(0.6, (en - s) / 14.4)}%` }), h("i", { style: `left:${nowMin / 14.4}%` }))),
          h("td", { text: e.start_date || e.end_date ? `${fmtDate(e.start_date) || "start of year"} to ${fmtDate(e.end_date) || "end of year"}` : "All year" }),
          h("td", { title: lookSummary(e.look) }, lookSwatch(e.look), h("div", { class: "hint", style: "margin:2px 0 0", text: lookSummary(e.look) })),
          h("td", { class: "actions" },
            h("button", { type: "button", class: "btn sm icon ghost", title: "Move up", "aria-label": `Move ${e.name} up`, disabled: i === 0, text: "↑", onclick: () => { [list[i - 1], list[i]] = [list[i], list[i - 1]]; setSchedDirty(true); renderSchedule(); } }),
            h("button", { type: "button", class: "btn sm icon ghost", title: "Move down", "aria-label": `Move ${e.name} down`, disabled: i === list.length - 1, text: "↓", onclick: () => { [list[i + 1], list[i]] = [list[i], list[i + 1]]; setSchedDirty(true); renderSchedule(); } }),
            h("button", { type: "button", class: "btn sm", text: "Edit", onclick: () => editEntry(i) }),
            h("button", { type: "button", class: "btn sm", text: "Duplicate", onclick: () => {
              const copy = clone(e); let n = 2; while (list.some(x => x.name === `${e.name} (${n})`)) n++;
              copy.name = `${e.name} (${n})`; list.splice(i + 1, 0, copy); setSchedDirty(true); renderSchedule();
            } }),
            h("button", { type: "button", class: "btn sm danger", text: "Delete", onclick: async () => {
              if (!(await ask({ title: `Delete "${e.name}"?`, message: "It's removed from the list now and from the controller when you save the schedule.", ok: "Delete entry", danger: true }))) return;
              list.splice(i, 1); setSchedDirty(true); renderSchedule();
            } })));
      })),
    );
  }
  $("#schedAdd").addEventListener("click", () => editEntry(-1));
  $("#schedDiscard").addEventListener("click", () => { sched.draft = clone(cfg.schedule || []); setSchedDirty(false); renderSchedule(); });
  $("#schedSave").addEventListener("click", async () => {
    try { cfg.schedule = await api.putSchedule(sched.draft); sched.draft = clone(cfg.schedule); setSchedDirty(false); renderSchedule(); toast("Schedule saved."); }
    catch (e) { toast(`The schedule wasn't saved:\n${errText(e)}`, true); }
  });

  const drawer = $("#drawer");
  function closeDrawer() { drawer.hidden = true; drawer.replaceChildren(); }
  document.addEventListener("keydown", e => { if (e.key === "Escape" && !drawer.hidden) closeDrawer(); });

  function editEntry(index) {
    const isNew = index < 0;
    const e = isNew
      ? { name: "", zone: null, enabled: true, priority: false, active_days: [...DAYS], start_time: "16:30", end_time: "23:59", start_date: null, end_date: null, look: clone(cfg.current_look.default || HotaEngine.OFF) }
      : clone(sched.draft[index]);
    e.zone = e.zone ?? null;
    const err = h("p", { class: "err-text", hidden: true });
    const name = h("input", { type: "text", id: "eName", value: e.name, placeholder: "e.g. Evening amber" });
    const enabled = h("input", { type: "checkbox", id: "eOn", checked: e.enabled !== false });
    const priority = h("input", { type: "checkbox", id: "ePriority", checked: !!e.priority });
    const zone = h("select", { id: "eZone" }, h("option", { value: "", text: "Whole building" }), zoneNames.map(z => h("option", { value: z, text: `Zone: ${z}`, selected: e.zone === z })));
    const days = h("div", { class: "dayset" });
    const renderDays = () => days.replaceChildren(...DAYS.map(d => h("button", { type: "button", "aria-pressed": e.active_days.includes(d), text: DAY_LABEL[d], onclick: () => {
      e.active_days = e.active_days.includes(d) ? e.active_days.filter(x => x !== d) : DAYS.filter(x => x === d || e.active_days.includes(x)); renderDays();
    } })));
    renderDays();
    const quick = h("div", { class: "chips", style: "margin-top:6px" },
      [["Every day", DAYS], ["Weekdays", DAYS.slice(0, 5)], ["Weekends", DAYS.slice(5)]].map(([t, ds]) => h("button", { type: "button", text: t, onclick: () => { e.active_days = [...ds]; renderDays(); } })));
    const start = h("input", { type: "time", id: "eStart", value: e.start_time });
    const end = h("input", { type: "time", id: "eEnd", value: e.end_time });

    const monthSel = (val, id) => h("select", { id, "aria-label": "Month" }, h("option", { value: "", text: "Any" }),
      Array.from({ length: 12 }, (_, m) => h("option", { value: String(m + 1).padStart(2, "0"), text: new Date(2024, m, 1).toLocaleDateString([], { month: "short" }), selected: val && val.slice(0, 2) === String(m + 1).padStart(2, "0") })));
    const daySel = (val, id) => h("select", { id, "aria-label": "Day" }, h("option", { value: "", text: "–" }),
      Array.from({ length: 31 }, (_, d) => h("option", { value: String(d + 1).padStart(2, "0"), text: d + 1, selected: val && val.slice(3) === String(d + 1).padStart(2, "0") })));
    const sm = monthSel(e.start_date, "eSm"), sd = daySel(e.start_date, "eSd"), em = monthSel(e.end_date, "eEm"), ed = daySel(e.end_date, "eEd");

    const presetPick = h("select", { "aria-label": "Start from a preset" }, h("option", { value: "", text: "Start from a preset…" }),
      (cfg.presets || []).slice().sort((a, b) => a.slot - b.slot).map((p, i) => h("option", { value: p.slot, text: presetName(p, i) })));
    const editorRoot = h("div", { class: "look-editor" });
    const editor = LookEditor(editorRoot, l => { e.look = l; swatchBox.replaceChildren(lookSwatch(e.look)); }, { openEffect: true });
    const swatchBox = h("span");
    editor.set(e.look); swatchBox.append(lookSwatch(e.look));
    presetPick.addEventListener("change", () => {
      const p = (cfg.presets || []).find(p => String(p.slot) === presetPick.value);
      if (p) { e.look = clone(p.look); editor.set(e.look); swatchBox.replaceChildren(lookSwatch(e.look)); }
      presetPick.value = "";
    });

    function apply() {
      e.name = name.value.trim();
      e.enabled = enabled.checked;
      e.priority = priority.checked;
      e.zone = zone.value || null;
      e.start_time = start.value; e.end_time = end.value;
      e.start_date = sm.value && sd.value ? `${sm.value}-${sd.value}` : null;
      e.end_date = em.value && ed.value ? `${em.value}-${ed.value}` : null;
      e.look = editor.get();
      const problems = [];
      if (!e.name) problems.push("Give the entry a name.");
      if (sched.draft.some((x, i) => x.name === e.name && i !== index)) problems.push(`Another entry is already called "${e.name}".`);
      if (!e.active_days.length) problems.push("Pick at least one day.");
      if (!e.start_time || !e.end_time || !(e.start_time < e.end_time)) problems.push("The end time must be after the start time. To run past midnight, add a second entry from 00:00.");
      if ((sm.value && !sd.value) || (!sm.value && sd.value) || (em.value && !ed.value) || (!em.value && ed.value)) problems.push("Pick both a month and a day for each date, or leave both empty.");
      if (e.start_date && e.end_date && e.start_date > e.end_date) problems.push("The start date must be on or before the end date. Date ranges can't cross New Year.");
      if (problems.length) { err.hidden = false; err.textContent = problems.join("\n"); return; }
      if (isNew) sched.draft.push(e); else sched.draft[index] = e;
      setSchedDirty(true); renderSchedule(); closeDrawer();
      toast(isNew ? "Entry added. Save the schedule to send it to the controller." : "Entry updated. Save the schedule to send it to the controller.");
    }

    drawer.replaceChildren(
      h("header", null, h("h3", { id: "drawerTitle", text: isNew ? "New schedule entry" : `Edit "${e.name}"` }),
        h("div", { class: "actions" }, h("button", { type: "button", class: "btn", text: "Cancel", onclick: closeDrawer }), h("button", { type: "button", class: "btn primary", text: isNew ? "Add entry" : "Update entry", onclick: apply }))),
      h("div", { class: "body" },
        err,
        h("div", { class: "field" }, h("label", { for: "eName", text: "Name" }), name),
        h("div", { class: "field" }, h("label", { for: "eOn", text: "Enabled" }), h("div", { class: "row" }, enabled)),
        h("div", { class: "field" }, h("label", { for: "ePriority", text: "Priority" }), h("div", { class: "row" }, priority)),
        h("p", { class: "hint", text: "For a one-off request, without touching the standing schedule. A priority entry overrides any other entry for the same target whenever it matches, no matter where it sits in this list. It deletes itself once it's done: when its end date has passed, or as soon as you switch Enabled off." }),
        h("div", { class: "field" }, h("label", { for: "eZone", text: "Applies to" }), zone),
        h("div", { class: "field", style: "align-items:start" }, h("span", { class: "lbl", style: "padding-top:5px", text: "Days" }), h("div", null, days, quick)),
        h("div", { class: "field" }, h("label", { for: "eStart", text: "From" }), h("div", { class: "row" }, start, h("span", { class: "hint", style: "margin:0", text: "to" }), end)),
        h("div", { class: "field" }, h("label", { for: "eSm", text: "Start date" }), h("div", { class: "row" }, sm, sd)),
        h("div", { class: "field" }, h("label", { for: "eEm", text: "End date" }), h("div", { class: "row" }, em, ed)),
        h("p", { class: "hint", text: "Leave the dates empty to run all year." }),
        h("div", { style: "display:flex;align-items:center;gap:8px;margin:18px 0 4px" }, h("h4", { text: "Look" }), swatchBox),
        h("div", { style: "display:flex;gap:6px;flex-wrap:wrap" }, presetPick,
          h("button", { type: "button", class: "btn sm", text: "Copy the live look", onclick: () => { e.look = clone(targetLook()); editor.set(e.look); swatchBox.replaceChildren(lookSwatch(e.look)); } })),
        editorRoot,
      ),
    );
    drawer.hidden = false;
    name.focus();
  }

  // -- randomizer + chime ---------------------------------------------------
  function renderRandomizer() {
    const r = clone(cfg.randomizer || { enabled: false, events_per_hour: 20, burst_s: 6, targets: [], preset_slots: [] });
    const presets = (cfg.presets || []).slice().sort((a, b) => a.slot - b.slot);
    const en = h("input", { type: "checkbox", id: "rOn", checked: r.enabled });
    const rate = h("input", { type: "number", id: "rRate", min: 0.1, step: 0.1, value: r.events_per_hour, style: "width:90px" });
    const burst = h("input", { type: "number", id: "rBurst", min: 0.5, step: 0.5, value: r.burst_s, style: "width:90px" });
    const toggles = (items, sel, label) => h("div", { class: "chips" }, items.map(([v, t]) => {
      const b = h("button", { type: "button", "aria-pressed": sel.includes(v), text: t, "aria-label": `${label}: ${t}` });
      b.addEventListener("click", () => { const i = sel.indexOf(v); i < 0 ? sel.push(v) : sel.splice(i, 1); b.setAttribute("aria-pressed", sel.includes(v)); });
      return b;
    }));
    $("#randCard").replaceChildren(
      h("header", null, h("h3", { text: "Randomiser" }), h("div", { class: "actions" }, h("button", { type: "button", class: "btn sm primary", text: "Save", onclick: async () => {
        const body = { enabled: en.checked, events_per_hour: +rate.value, burst_s: +burst.value, targets: r.targets, preset_slots: r.preset_slots };
        try { cfg.randomizer = await api.putRandomizer(body); toast("Randomiser saved."); } catch (e) { toast(errText(e), true); }
      } }))),
      h("div", { class: "body" },
        h("p", { class: "hint", style: "margin-top:0", text: "Now and then, plays a random preset on a random zone for a few seconds, then goes back to the scheduled look." }),
        h("div", { class: "field" }, h("label", { for: "rOn", text: "Enabled" }), h("div", { class: "row" }, en)),
        h("div", { class: "field" }, h("label", { for: "rRate", text: "Bursts per hour" }), rate),
        h("div", { class: "field" }, h("label", { for: "rBurst", text: "Each lasts (s)" }), burst),
        h("p", { class: "group-lbl", text: "Zones it can play on" }), toggles(zoneNames.map(z => [z, z]), r.targets, "Zone"),
        h("p", { class: "group-lbl", text: "Presets it can pick" }),
        presets.length ? toggles(presets.map((p, i) => [p.slot, presetName(p, i)]), r.preset_slots, "Preset") : h("p", { class: "hint", text: "Save some presets on the Live tab first." }),
      ),
    );
  }
  function renderChime() {
    const c = clone(cfg.clock_chime || { enabled: false, look: HotaEngine.OFF, duration_s: 60, start_hour: 9, end_hour: 21 });
    const presets = (cfg.presets || []).slice().sort((a, b) => a.slot - b.slot);
    const match = presets.find(p => JSON.stringify(p.look) === JSON.stringify(c.look));
    const en = h("input", { type: "checkbox", id: "cOn", checked: c.enabled });
    const prog = h("select", { id: "cProg" }, h("option", { value: "-1", text: "Nothing (off)" }),
      presets.map((p, i) => h("option", { value: p.slot, text: presetName(p, i), selected: match && match.slot === p.slot })),
      !match && c.look && c.look.colour && c.look.colour.mode !== "off" ? h("option", { value: "keep", text: "Current chime look (not a preset)", selected: true }) : null);
    const hourSel = (id, v) => h("select", { id }, Array.from({ length: 24 }, (_, i) => h("option", { value: i, text: `${String(i).padStart(2, "0")}:00`, selected: i === v })));
    const sh = hourSel("cFrom", c.start_hour), eh = hourSel("cTo", c.end_hour);
    const dur = h("input", { type: "number", id: "cDur", min: 1, step: 1, value: c.duration_s, style: "width:90px" });
    $("#chimeCard").replaceChildren(
      h("header", null, h("h3", { text: "Hourly chime" }), h("div", { class: "actions" }, h("button", { type: "button", class: "btn sm primary", text: "Save", onclick: async () => {
        const p = presets.find(p => String(p.slot) === prog.value);
        const look = prog.value === "keep" ? c.look : p ? clone(p.look) : clone(HotaEngine.OFF);
        const body = { enabled: en.checked, look, duration_s: +dur.value, start_hour: +sh.value, end_hour: +eh.value };
        if (body.start_hour > body.end_hour) return toast("The first hour must be on or before the last hour.", true);
        try { cfg.clock_chime = await api.putClockChime(body); toast("Hourly chime saved."); } catch (e) { toast(errText(e), true); }
      } }))),
      h("div", { class: "body" },
        h("p", { class: "hint", style: "margin-top:0", text: "On the hour, plays a preset over the whole building, then goes back to the scheduled look." }),
        h("div", { class: "field" }, h("label", { for: "cOn", text: "Enabled" }), h("div", { class: "row" }, en)),
        h("div", { class: "field" }, h("label", { for: "cProg", text: "Plays" }), prog),
        h("div", { class: "field" }, h("label", { for: "cFrom", text: "First hour" }), sh),
        h("div", { class: "field" }, h("label", { for: "cTo", text: "Last hour" }), eh),
        h("div", { class: "field" }, h("label", { for: "cDur", text: "Lasts (s)" }), dur),
      ),
    );
  }

  // ===================================================================
  // SETTINGS
  // ===================================================================
  function openSettings() {
    $("#settingsSub").textContent = api.kind === "controller"
      ? "Device and network settings for the lighting controller."
      : "You're in the demo. Settings are saved in this browser, and network details need the real controller.";
    renderDevice(); renderRemote(); renderArtnet(); renderConfigCard(); renderFixtures();
  }

  // "Scan to connect": a QR code for the address this page is live on right
  // now (location.origin - correct whichever interface you reached it by,
  // wired or Wi-Fi, no hardcoded IP to go stale) - EXCEPT on the kiosk
  // itself, which loads via http://localhost:8080/ so its origin means
  // nothing to a phone's camera. shareableUrl() below detects that one
  // case and substitutes the controller's real network address instead.
  // Demo mode has no controller to hand a phone, so the card just
  // explains that instead.
  function renderRemote() {
    const pinAlertSlot = h("div", { class: "alert-slot" });
    const pin = h("input", { type: "text", id: "dPin", value: cfg.remote_pin || "", placeholder: "No PIN - anyone can connect", style: "max-width:160px" });
    $("#remoteCard").replaceChildren(
      h("header", null, h("h3", { text: "Remote access" }), api.kind === "controller"
        ? h("div", { class: "actions" }, h("button", { type: "button", class: "btn sm primary", text: "Save", onclick: async () => {
          const val = pin.value.trim();
          if (val && !/^[0-9]{4,8}$/.test(val)) return slideAlert(pinAlertSlot, "Check the PIN", "Use 4-8 digits, or leave it empty to turn the PIN off.");
          try {
            cfg = await api.putConfig({ ...cfg, remote_pin: val || null });
            pinAlertSlot.replaceChildren();
            toast(val ? "PIN saved." : "PIN turned off - remote access no longer asks for one.");
          } catch (e) { slideAlert(pinAlertSlot, "PIN wasn't saved", errText(e)); }
        } }))
        : null),
      pinAlertSlot,
      h("div", { class: "body" },
        api.kind === "controller"
          ? [
            h("p", { class: "hint", style: "margin-top:0", text: "Scan this from a phone on the gallery Wi-Fi to open the live control - handy for checking the facade from outside." }),
            h("button", { type: "button", class: "btn primary", text: "Scan to connect", onclick: openRemoteAccessDialog }),
            h("div", { class: "field", style: "margin-top:14px" }, h("label", { for: "dPin", text: "Access PIN" }), pin),
            h("p", { class: "hint", text: "Asked once per browser for anyone connecting remotely - this kiosk screen is never asked. This is a speed bump, not real security: it doesn't stop someone who reaches the controller's network directly." }),
          ]
          : h("p", { class: "hint", style: "margin-top:0", text: "Scanning to connect needs the real controller - not available in the demo." })));
  }

  function buildQrSvg(text, cellPx) {
    const qr = qrcode(0, "M"); // typeNumber 0 = smallest version that fits
    qr.addData(text);
    qr.make();
    // Scanners need a blank "quiet zone" around the code to lock on - the
    // spec calls for >=4 modules; without it the white fill butting right
    // up against the dialog's dark background looked fine but didn't
    // actually decode (confirmed with a real decoder before this fix).
    const QUIET = 4;
    const n = qr.getModuleCount(), size = (n + QUIET * 2) * cellPx, ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", `0 0 ${size} ${size}`);
    svg.setAttribute("width", size); svg.setAttribute("height", size);
    svg.style.cssText = "display:block;background:#fff;border-radius:8px";
    for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) {
      if (!qr.isDark(r, c)) continue;
      const rect = document.createElementNS(ns, "rect");
      rect.setAttribute("x", (c + QUIET) * cellPx); rect.setAttribute("y", (r + QUIET) * cellPx);
      rect.setAttribute("width", cellPx); rect.setAttribute("height", cellPx);
      rect.setAttribute("fill", "#0f0e0e");
      svg.append(rect);
    }
    return svg;
  }

  // location.origin is right for anyone who actually typed/scanned their
  // way to a network address - but the kiosk display loads this page via
  // http://localhost:8080/ (deliberately, so it never breaks if the Pi's
  // IP changes), and "localhost" in a QR code just points a phone's camera
  // at the phone itself. Ask the controller for a real address instead
  // whenever we're being viewed through localhost - specifically its WiFi
  // address, not effective_bind_ip: this is a dual-NIC Pi, bind_ip is
  // pinned to the *wired* Art-Net interface on purpose, and a phone on
  // the venue WiFi can't reach that network at all. Fall back to
  // effective_bind_ip only if there's genuinely no WiFi interface up.
  async function shareableUrl() {
    if (!["localhost", "127.0.0.1", "[::1]"].includes(location.hostname)) return location.origin + "/";
    try {
      const status = await api.artnetStatus();
      const ip = status && (status.wifi_ip || status.effective_bind_ip);
      if (ip) return `http://${ip}:${status.web_port}/`;
    } catch { /* fall through */ }
    return location.origin + "/"; // best effort - still beats throwing
  }

  async function openRemoteAccessDialog() {
    const url = await shareableUrl();
    const copyBtn = h("button", { type: "button", class: "btn", text: "Copy link", onclick: async () => {
      const ok = await copyText(url);
      copyBtn.textContent = ok ? "Copied" : url;
      if (ok) setTimeout(() => { copyBtn.textContent = "Copy link"; }, 1800);
    } });
    const form = h("form", { method: "dialog", class: "dlg-body" },
      h("h3", { class: "dlg-title", id: "dlgTitle", text: "Scan to connect" }),
      h("p", { class: "dlg-msg", text: "Open your phone's camera and point it at this code to load the remote control." }),
      h("div", { style: "display:flex;justify-content:center;margin:4px 0" }, buildQrSvg(url, 4)),
      h("div", { class: "qr-url", text: url }),
      h("p", { class: "hint", style: "text-align:center;margin:0 0 4px" , text: "Works on the gallery Wi-Fi only" }),
      h("div", { class: "dlg-actions" }, copyBtn, h("button", { type: "submit", class: "btn primary", text: "Done" })));
    const dlg = h("dialog", { class: "dlg", "aria-labelledby": "dlgTitle" }, form);
    const close = () => { dlg.close(); dlg.remove(); };
    form.addEventListener("submit", e => { e.preventDefault(); close(); });
    dlg.addEventListener("cancel", e => { e.preventDefault(); close(); });
    dlg.addEventListener("click", e => { if (e.target === dlg) close(); });
    document.body.append(dlg);
    dlg.showModal();
  }
  // The port this page was actually served on - what the controller is
  // listening on right now (web_port is only read when it starts).
  const runningPort = () => +(location.port || (location.protocol === "https:" ? 443 : 80));
  function newUrl(port) { const u = new URL(location.href); u.port = String(port); u.hash = "#settings"; return u.href; }

  function renderDevice() {
    const name = h("input", { type: "text", id: "dName", value: cfg.device_name || "" });
    const fps = h("input", { type: "number", id: "dFps", min: 1, max: 60, step: 1, value: cfg.fps, style: "width:90px" });
    const bind = h("input", { type: "text", id: "dBind", value: cfg.bind_ip || "", placeholder: "Automatic" });
    const port = h("input", { type: "number", id: "dPort", min: 1024, max: 65535, step: 1, value: cfg.web_port, style: "width:110px" });
    const alertSlot = h("div", { class: "alert-slot" });   // field errors - red, slide away
    const pendingSlot = h("div", { class: "alert-slot" }); // restart needed - amber, stays
    // Saved but not yet live: the controller is still on the old port.
    const pending = () => api.kind === "controller" && cfg.web_port !== runningPort();
    const renderPortNote = () => {
      if (!pending()) { pendingSlot.replaceChildren(); return; }
      const restartCmd = "sudo systemctl restart hota-gallery";
      slideAlert(pendingSlot, `Restart the controller to move to port ${cfg.web_port}`,
        `It's still running on ${runningPort()} until then. Afterwards this page lives at ${newUrl(cfg.web_port).replace(/#.*/, "")}.`, 0, {
          kind: "warn",
          actions: [
            h("button", { type: "button", class: "btn sm", text: "Copy restart command", onclick: async e => {
              const ok = await copyText(restartCmd);
              e.target.textContent = ok ? "Copied" : restartCmd;
              if (ok) setTimeout(() => { e.target.textContent = "Copy restart command"; }, 1800);
            } }),
            h("a", { class: "btn sm", href: newUrl(cfg.web_port), text: "Open new address" }),
          ],
        });
    };
    $("#deviceCard").replaceChildren(
      h("header", null, h("h3", { text: "Device" }), h("div", { class: "actions" }, h("button", { type: "button", class: "btn sm primary", text: "Save", onclick: async () => {
        const ip = bind.value.trim();
        if (ip && !/^(\d{1,3}\.){3}\d{1,3}$/.test(ip)) return slideAlert(alertSlot, "Check the network interface", "It must be an IPv4 address like 192.168.1.20, or empty to pick automatically.");
        const p = Number(port.value);
        // The service runs as an unprivileged user, so it can't open ports
        // below 1024 - saving one would leave it failing to start.
        if (!Number.isInteger(p) || p < 1024 || p > 65535) return slideAlert(alertSlot, "Check the web port", "Use a whole number from 1024 to 65535. Lower ports need root, which the controller doesn't run as.");
        const portChanged = p !== cfg.web_port;
        try {
          cfg = await api.putConfig({ ...cfg, device_name: name.value.trim(), fps: +fps.value, bind_ip: ip || null, web_port: p });
          indexConfig(); setConn();
          alertSlot.replaceChildren(); // clear any earlier "check this field" notice
          if (portChanged) renderPortNote();
          toast(portChanged && api.kind === "controller" ? `Device settings saved. The web port changes to ${p} when the controller restarts.` : "Device settings saved.");
        } catch (e) { slideAlert(alertSlot, "Device settings weren't saved", errText(e)); }
      } }))),
      pendingSlot,
      alertSlot,
      h("div", { class: "body grid2" },
        h("div", null,
          h("div", { class: "field" }, h("label", { for: "dName", text: "Device name" }), name),
          h("div", { class: "field" }, h("label", { for: "dFps", text: "Output rate" }), h("div", { class: "row" }, fps, h("span", { class: "hint", style: "margin:0", text: "frames per second" }))),
          h("div", { class: "field" }, h("label", { for: "dBind", text: "Network interface" }), bind),
          h("p", { class: "hint", text: "The address of the network card Art-Net goes out on. Leave empty to pick automatically." }),
          h("div", { class: "field" }, h("label", { for: "dPort", text: "Web port" }), h("div", { class: "row" }, port)),
          h("p", { class: "hint", text: "The port this page is served on. A new port takes effect when the controller restarts." })),
        h("dl", { class: "kv", style: "margin:7px 0 0" },
          h("dt", { text: "Fixtures" }), h("dd", { text: `${cfg.fixtures.length} (${cfg.fixtures.reduce((a, f) => a + f.led_count, 0)} LEDs)` }),
          h("dt", { text: "Zones" }), h("dd", { text: zoneNames.join(", ") || "None" }),
          h("dt", { text: "Layouts" }), h("dd", { text: allLayouts().map(l => l.name).join(", ") }))),
    );
    renderPortNote();
  }

  async function copyText(text) {
    // navigator.clipboard needs https; the controller is plain http on the
    // LAN, so fall back to the old execCommand route.
    try { await navigator.clipboard.writeText(text); return true; } catch { /* fall through */ }
    const ta = h("textarea", { style: "position:fixed;opacity:0;pointer-events:none" });
    ta.value = text; document.body.append(ta); ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch { ok = false; }
    ta.remove();
    return ok;
  }

  // A notice that slides down out of a card's header. Red errors slide away
  // after `ms`; pass ms = 0 to keep it. opts.kind "warn" makes it amber (a
  // state to act on, not a fault); opts.actions adds buttons under the text.
  function slideAlert(slot, title, message, ms = 8000, opts = {}) {
    slot.replaceChildren();
    const close = () => {
      wrap.classList.remove("open");
      setTimeout(() => wrap.remove(), prefersReduced ? 0 : 360);
    };
    const wrap = h("div", { class: "alert-wrap" },
      h("div", { class: "alert-inner" },
        h("div", { class: `alert${opts.kind === "warn" ? " warn" : ""}`, role: opts.kind === "warn" ? "status" : "alert" },
          h("span", { class: "alert-icon", "aria-hidden": "true", text: "!" }),
          h("div", { class: "alert-text" }, h("b", { text: title }), message ? h("p", { text: message }) : null,
            opts.actions ? h("div", { class: "alert-actions" }, opts.actions) : null),
          h("button", { type: "button", class: "alert-close", "aria-label": "Dismiss", text: "×", onclick: close }))));
    slot.append(wrap);
    requestAnimationFrame(() => requestAnimationFrame(() => wrap.classList.add("open")));
    if (ms) setTimeout(close, ms);
  }

  // Last node scan, kept across re-renders: { at: Date, nodes: [...] } or null.
  let artnetScan = null;
  let artnetScanning = false;

  // Art-Net check shared by the header "Lights" pill and the Settings card.
  // Runs on load (controller only), every 5 minutes, and on "Scan again".
  const lightsEl = $("#lights");
  function updateLights() {
    if (api.kind !== "controller") { lightsEl.hidden = true; return; }
    lightsEl.hidden = false;
    if (!artnetScan) { lightsEl.dataset.state = "checking"; lightsEl.querySelector("span").textContent = "Checking lights…"; lightsEl.title = "Looking for Art-Net nodes on the network."; return; }
    const unis = patchUniverses().map(u => u.universe);
    const answering = new Set(artnetScan.nodes.flatMap(n => n.universes_out || []));
    const ok = unis.filter(u => answering.has(u)).length;
    lightsEl.dataset.state = ok === unis.length ? "ok" : ok ? "part" : "bad";
    lightsEl.querySelector("span").textContent = ok === 0 ? "No lights answering" : `Lights: ${ok}/${unis.length} universes`;
    lightsEl.title = `${artnetScan.nodes.length} Art-Net node${artnetScan.nodes.length === 1 ? "" : "s"} replied at ${artnetScan.at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}. Open Settings for details.`;
  }
  lightsEl.addEventListener("click", () => showTab("settings"));
  async function scanArtnet() {
    if (artnetScanning || api.kind !== "controller") return null;
    artnetScanning = true; updateLights();
    try {
      const nodes = await api.artnetDiscover();
      artnetScan = { at: new Date(), nodes };
      return nodes;
    } finally {
      artnetScanning = false; updateLights();
      if (!tabs.settings.hidden) renderArtnet();
    }
  }

  // Universes the patch uses - same numbers as /api/artnet/status, but
  // worked out locally so the demo can show the table too.
  function patchUniverses() {
    const by = new Map();
    for (const f of cfg.fixtures) {
      const cpl = f.channels_per_led || (f.led_type === "RGBW" ? 4 : 3);
      const e = by.get(f.universe) || { universe: f.universe, fixture_count: 0, channel_count: 0, max_address: 0 };
      e.fixture_count++; e.channel_count += f.led_count * cpl; e.max_address = Math.max(e.max_address, f.address + f.led_count * cpl);
      by.set(f.universe, e);
    }
    return [...by.values()].sort((a, b) => a.universe - b.universe);
  }

  async function renderArtnet() {
    const card = $("#artnetCard");
    const alertSlot = h("div", { class: "alert-slot" });
    const summary = h("span", { class: "conn-summary" });
    const scan = h("button", { type: "button", class: "btn sm", disabled: artnetScanning, text: artnetScanning ? "Scanning…" : artnetScan ? "Scan again" : "Scan for nodes", onclick: () => runScan() });
    const body = h("div", { class: "body" }, h("p", { class: "hint", style: "margin:0", text: "Loading…" }));
    card.replaceChildren(h("header", null, h("h3", { text: "Art-Net output" }), summary, h("div", { class: "actions" }, scan)), alertSlot, body);

    let st = null;
    if (api.kind === "controller") {
      try { st = await api.artnetStatus(); } catch (e) { slideAlert(alertSlot, "Couldn't read the Art-Net status", errText(e), 0); }
    }
    const universes = (st && st.universes) || patchUniverses();
    const byUniverse = new Map(universes.map(u => [u.universe, u]));

    if (artnetScan) {
      const answeredUnis = new Set(artnetScan.nodes.flatMap(n => n.universes_out || []));
      const ok = universes.filter(u => answeredUnis.has(u.universe)).length;
      summary.className = "conn-summary " + (!artnetScan.nodes.length ? "bad" : ok === universes.length ? "ok" : ok ? "part" : "bad");
      summary.textContent = artnetScan.nodes.length
        ? `${artnetScan.nodes.length} node${artnetScan.nodes.length === 1 ? "" : "s"} found, ${ok} of ${universes.length} patched universes covered`
        : "No nodes replied";
    }

    // The real, physical things on the network are the Art-Net nodes
    // (e.g. the two Titan gateways) - lead with those, one card per node,
    // and show each of its *ports* as a row: what universe that port is
    // configured to output, and (cross-referenced against the patch)
    // whether anything is actually assigned to it. A universe number on
    // its own doesn't say which box on the wall it comes out of; this does.
    const deviceCards = artnetScan && artnetScan.nodes.length
      ? artnetScan.nodes.map(n => h("div", { class: "node" },
          h("div", { class: "node-head" },
            h("span", { class: "ustat ok" }, h("i")), h("b", { text: n.short_name || n.ip }),
            h("span", { class: "hint", style: "margin:0 0 0 auto", text: [n.ip, n.mac].filter(Boolean).join(" · ") })),
          n.long_name && n.long_name !== n.short_name ? h("div", { class: "hint", style: "margin:2px 0 8px", text: n.long_name }) : null,
          h("div", { class: "tbl-wrap", style: "margin-top:8px" }, h("table", { class: "tbl" },
            h("thead", null, h("tr", null, ["Port", "Universe", "Fixtures", "Channels", "Highest address"].map((t, i) => h("th", { text: t, class: i > 1 ? "num" : "" })))),
            (n.universes_out || []).length
              ? h("tbody", null, n.universes_out.map((u, i) => {
                const patched = byUniverse.get(u);
                return h("tr", null,
                  h("td", { text: i + 1 }),
                  h("td", null, h("span", { class: "ustat " + (patched ? "ok" : "unknown") }, h("i")), String(u + 1)),
                  h("td", { class: "num", text: patched ? patched.fixture_count : "–" }),
                  h("td", { class: "num", text: patched ? patched.channel_count : "–" }),
                  h("td", { class: "num" + (patched && patched.max_address > 512 ? " err-text" : ""), text: patched ? (patched.max_address > 512 ? `${patched.max_address} (over 512)` : patched.max_address) : "Not used by the patch" }));
              }))
              : h("tbody", null, h("tr", null, h("td", { class: "empty", colspan: 5, text: "This node didn't report any output ports." })))))))
      : [];

    const coveredUnis = new Set(artnetScan ? artnetScan.nodes.flatMap(n => n.universes_out || []) : []);
    const orphanUnis = artnetScan ? universes.filter(u => !coveredUnis.has(u.universe)) : [];

    body.replaceChildren(
      st ? h("dl", { class: "kv" },
        h("dt", { text: "Sending from" }), h("dd", { text: `${st.effective_bind_ip} ${st.configured_bind_ip ? "(set)" : "(automatic)"}` }),
        h("dt", { text: "Broadcast to" }), h("dd", { text: `${st.broadcast_address}:${st.artnet_port}` }),
        h("dt", { text: "Rate" }), h("dd", { text: `${st.fps} fps` }))
        : h("p", { class: "hint", style: "margin:0", text: "The demo doesn't send any lighting data. On the controller, this shows which Art-Net nodes answered and what each one's ports are outputting." }),
      !artnetScan
        ? h("p", { class: "hint", style: "margin-top:14px", text: api.kind === "controller" ? 'Not scanned yet - click "Scan for nodes".' : "Needs the controller." })
        : h("div", { class: "nodes", style: "margin-top:14px;grid-template-columns:1fr" }, deviceCards.length ? deviceCards : h("p", { class: "hint", text: "No nodes replied." })),
      orphanUnis.length
        ? h("p", { class: "hint warn-text", style: "margin-top:10px",
            text: `Universe${orphanUnis.length > 1 ? "s" : ""} ${orphanUnis.map(u => u.universe + 1).join(", ")} ${orphanUnis.length > 1 ? "are" : "is"} used by the patch, but no node answered for ${orphanUnis.length > 1 ? "them" : "it"}.` })
        : null,
      artnetScan ? h("p", { class: "hint", text: `Last scanned at ${artnetScan.at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}. Universes are numbered from 1, like ELM.` }) : null,
    );

    async function runScan() {
      if (artnetScanning) return;
      scan.disabled = true; scan.textContent = "Scanning…";
      try {
        const nodes = await scanArtnet();
        if (nodes && !nodes.length) slideAlert($("#artnetCard .alert-slot"), "No Art-Net nodes replied", "Check the network interface under Device, and that the nodes are powered and plugged into the gallery network.");
      } catch (e) {
        slideAlert($("#artnetCard .alert-slot"), "The scan didn't run", errText(e));
      }
    }
  }
  function renderConfigCard() {
    const file = h("input", { type: "file", accept: "application/json,.json", hidden: true });
    file.addEventListener("change", async () => {
      const f = file.files[0]; file.value = "";
      if (!f) return;
      let parsed;
      try { parsed = JSON.parse(await f.text()); } catch { return toast("That file isn't valid JSON.", true); }
      if (!(await ask({ title: "Replace the configuration?", message: `Everything (fixtures, layouts, presets and schedules) is replaced with "${f.name}". Download a copy of the current one first if you might want it back.`, ok: "Replace configuration", danger: true }))) return;
      try {
        cfg = await api.putConfig(parsed); indexConfig(); setConn();
        sched.draft = null; afterConfigReplaced(); openSettings(); toast("Configuration imported.");
      } catch (e) { toast(`Import failed:\n${errText(e)}`, true); }
    });
    $("#configCard").replaceChildren(
      h("header", null, h("h3", { text: "Configuration file" })),
      h("div", { class: "body" },
        h("p", { class: "hint", style: "margin-top:0", text: "Everything (fixtures, layouts, looks, presets and schedules) lives in one config.json. Download a copy before big changes so you can roll back." }),
        h("div", { style: "display:flex;gap:8px;flex-wrap:wrap;margin-top:10px" },
          h("button", { type: "button", class: "btn", text: "Download config.json", onclick: () => {
            const blob = new Blob([JSON.stringify(cfg, null, 2)], { type: "application/json" });
            const a = h("a", { href: URL.createObjectURL(blob), download: `hota-gallery-config-${new Date().toISOString().slice(0, 10)}.json` });
            document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
          } }),
          h("label", { class: "btn" }, "Import config.json…", file),
          api.kind === "local" ? h("button", { type: "button", class: "btn danger", text: "Reset demo", onclick: async () => {
            if (!(await ask({ title: "Reset the demo?", message: "Your demo changes in this browser are thrown away and the original configuration comes back.", ok: "Reset demo", danger: true }))) return;
            cfg = api.reset(); indexConfig(); sched.draft = null; afterConfigReplaced(); openSettings(); toast("Demo reset.");
          } }) : null),
        api.kind === "controller"
          ? [
            h("p", { class: "hint", text: "\"Download config.json\" above is this page's own copy - handy for a quick rollback. For an actual backup, use this instead: it's read straight from the controller's disk and bundled into one zip with any layout background images, so a Pi SD card failure doesn't take the whole venue setup with it." }),
            h("a", { class: "btn primary", href: "api/backup", download: true, text: "Download full backup (.zip)" }),
          ]
          : h("p", { class: "hint", text: "A full backup needs the real controller - not available in the demo." })),
    );
  }
  function renderFixtures() {
    const q = h("input", { type: "text", placeholder: "Filter by name, zone or universe", "aria-label": "Filter fixtures", style: "width:260px" });
    const body = h("tbody");
    const fill = () => {
      const s = q.value.trim().toLowerCase();
      body.replaceChildren(...cfg.fixtures.filter(f => !s || `${f.name} ${f.zone} ${f.universe + 1} ${f.led_type}`.toLowerCase().includes(s)).map(f => h("tr", null,
        h("td", { text: f.name }), h("td", { text: f.zone || "–" }), h("td", { text: f.led_type }), h("td", { class: "num", text: f.led_count }),
        h("td", { class: "num", text: f.universe + 1 }), h("td", { class: "num", text: f.address + 1 }), h("td", { class: "num", text: `${f.address + 1}–${f.address + f.led_count * (f.channels_per_led || (f.led_type === "RGBW" ? 4 : 3))}` }))));
    };
    q.addEventListener("input", fill);
    $("#fixturesCard").replaceChildren(
      h("header", null, h("h3", { text: "Fixtures" }), h("p", { class: "hint", text: "Patch from the ELM showfile. Universe and address are shown 1-based." }), h("div", { class: "actions" }, q)),
      h("div", { class: "tbl-wrap", style: "max-height:420px;overflow:auto" }, h("table", { class: "tbl" },
        h("thead", null, h("tr", null, ["Name", "Zone", "Type", "LEDs", "Universe", "Address", "Channels"].map((t, i) => h("th", { text: t, class: i > 2 ? "num" : "" })))), body)),
    );
    fill();
  }

  // ===================================================================
  // boot
  // ===================================================================
  function afterConfigReplaced() {
    exitEdit();
    live.selection.clear();
    renderLayoutSelect(); renderSelectionTools(); renderPresets(); selectionChanged();
    fitView("all");
  }
  HotaExport.preload();
  // Clear: drop every manual look (default + every per-fixture override)
  // and show whatever the schedule itself says should be playing right
  // now - undoes whatever staff were just testing without waiting on the
  // scheduler's own next tick. Press-twice-to-confirm instead of a modal,
  // since this is the kind of button someone reaches for in a hurry.
  (() => {
    const btn = $("#clearProgram");
    const IDLE_TEXT = btn.textContent;
    let armed = false, timer = null;
    function disarm() {
      armed = false;
      clearTimeout(timer);
      btn.textContent = IDLE_TEXT;
      btn.classList.remove("danger-fill");
    }
    btn.addEventListener("click", async () => {
      if (!armed) {
        armed = true;
        btn.textContent = "Click again to clear";
        btn.classList.add("danger-fill");
        timer = setTimeout(disarm, 4000);
        return;
      }
      disarm();
      lastLookEdit = Date.now();
      try {
        cfg.current_look = await api.clear();
        updateTarget(); loadEditorFromTarget();
        toast("Cleared. Showing what the schedule says should be playing now.");
      } catch (e) { toast(errText(e), true); }
    });
  })();

  // Export GIF: the default look's matching preset (if any) names the concept.
  const describeLook = cl => {
    const own = Object.keys(cl.fixtures || {}).length;
    return [lookSummary(cl.default || HotaEngine.OFF), own ? `${own} fixture${own > 1 ? "s" : ""} with their own look` : null,
      new Date().toLocaleDateString([], { day: "numeric", month: "long", year: "numeric" })].filter(Boolean).join(", ");
  };
  $("#exportGif").addEventListener("click", () => {
    const def = cfg.current_look.default || HotaEngine.OFF;
    const presets = (cfg.presets || []).slice().sort((a, b) => a.slot - b.slot);
    const i = presets.findIndex(p => JSON.stringify(p.look) === JSON.stringify(def));
    if (api.kind === "controller") refreshCurrentLook(); // pick up anything changed elsewhere
    HotaExport.open({
      cfg, elev, layout: currentLayout(), view: currentView || "all",
      title: i >= 0 ? presetName(presets[i], i) : "Untitled concept",
      subtitle: describeLook(cfg.current_look),
      getCurrentLook: () => cfg.current_look,
      describe: describeLook,
      onDone: msg => toast(msg),
      onError: msg => toast(`The GIF couldn't be made: ${msg}`, true),
    });
  });

  renderLayoutSelect();
  renderSelectionTools();
  renderPresets();
  selectionChanged();
  resize(); fitView("all");
  showTab(location.hash.slice(1) || "live");
  requestAnimationFrame(frame);
  updateLights();
  if (api.kind === "controller") {
    scanArtnet().catch(() => {});
    setInterval(() => { if (!document.hidden) scanArtnet().catch(() => {}); }, 5 * 60 * 1000);
  }
})();
