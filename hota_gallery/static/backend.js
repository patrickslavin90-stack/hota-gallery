// Where the UI reads and saves config. Two interchangeable backends with
// the same methods:
//   ControllerBackend - the real webapp.py API on the Pi.
//   LocalBackend      - no controller reachable (e.g. the hosted demo):
//                       config lives in this browser's localStorage and the
//                       preview is computed in-page by engine.js.
(function (global) {
  "use strict";

  class ApiError extends Error {}

  async function request(method, path, body, raw) {
    const opts = { method, headers: {} };
    if (raw !== undefined) opts.body = raw;
    else if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
    const res = await fetch(path, opts);
    let data = null;
    try { data = await res.json(); } catch { /* empty body */ }
    if (!res.ok) throw new ApiError((data && data.error) || `${method} ${path} failed (${res.status})`);
    return data;
  }

  class ControllerBackend {
    constructor() { this.kind = "controller"; }
    getConfig() { return request("GET", "/api/config"); }
    getPreview() { return request("GET", "/api/preview"); }
    putConfig(cfg) { return request("PUT", "/api/config", cfg); }
    putLook(look) { return request("PUT", "/api/look", look); }
    putSelection(keys, look) { return request("PUT", "/api/look/selection", { fixtures: keys, look }); }
    clear() { return request("PUT", "/api/clear", {}); }
    setCurrentLook(cl) { return request("PUT", "/api/look/full", cl); }
    putPresets(list) { return request("PUT", "/api/presets", list); }
    putSchedule(list) { return request("PUT", "/api/schedule", list); }
    putRandomizer(obj) { return request("PUT", "/api/randomizer", obj); }
    putClockChime(obj) { return request("PUT", "/api/clock_chime", obj); }
    putLayouts(list) { return request("PUT", "/api/layouts", list); }
    putBackground(name, file) { return request("PUT", `/api/layouts/${encodeURIComponent(name)}/background`, undefined, file || new Blob([])); }
    backgroundUrl(name) { return `/api/layouts/${encodeURIComponent(name)}/background?t=${Date.now()}`; }
    artnetStatus() { return request("GET", "/api/artnet/status"); }
    artnetDiscover() { return request("GET", "/api/artnet/discover"); }
  }

  const STORE_KEY = "hota-gallery-demo-config-v1";

  class LocalBackend {
    constructor(seed) {
      this.kind = "local";
      this.seed = seed;
      let saved = null;
      try { saved = JSON.parse(localStorage.getItem(STORE_KEY)); } catch { /* storage blocked */ }
      this.cfg = saved && saved.fixtures ? saved : structuredClone(seed);
      this.engine = HotaEngine.createEngine(this.cfg);
      this.t0 = performance.now();
    }
    _save() {
      try { localStorage.setItem(STORE_KEY, JSON.stringify(this.cfg)); } catch { /* storage full or blocked - keep in memory */ }
      this.engine.setConfig(this.cfg);
      return structuredClone(this.cfg);
    }
    async getConfig() { return structuredClone(this.cfg); }
    async getPreview() { return this.engine.preview((performance.now() - this.t0) / 1000); }
    async putConfig(cfg) {
      if (!Array.isArray(cfg.fixtures)) throw new ApiError("  - config must have a fixtures list");
      this.cfg = structuredClone(cfg);
      return this._save();
    }
    async putLook(look) { this.cfg.current_look.default = look; this._save(); return this.cfg.current_look.default; }
    async putSelection(keys, look) {
      const over = this.cfg.current_look.fixtures || (this.cfg.current_look.fixtures = {});
      for (const k of keys) { if (look) over[k] = look; else delete over[k]; }
      this._save();
      return this.cfg.current_look;
    }
    async setCurrentLook(cl) {
      this.cfg.current_look = cl;
      this._save();
      return this.cfg.current_look;
    }
    async clear() {
      // No background scheduler runs in the demo, so there's no "what's
      // scheduled right now" to resync to the way the real controller
      // does - this just drops every override back to off, same spirit
      // (undo whatever look I was just trying out) in the one mode where
      // there's no schedule underneath to fall back to instead.
      this.cfg.current_look = { default: structuredClone(HotaEngine.OFF), fixtures: {} };
      this._save();
      return this.cfg.current_look;
    }
    async putPresets(list) {
      const slots = list.map(p => p.slot);
      if (new Set(slots).size !== slots.length) throw new ApiError("  - preset slots must be unique");
      this.cfg.presets = list; this._save(); return list;
    }
    async putSchedule(list) {
      const errors = [], names = new Set();
      list.forEach((e, i) => {
        const where = `schedule[${i}] (${e.name || "unnamed"})`;
        if (!e.name) errors.push(`${where}: name is required`);
        else if (names.has(e.name)) errors.push(`${where}: name must be unique`);
        names.add(e.name);
        if (!e.active_days || !e.active_days.length) errors.push(`${where}: pick at least one day`);
        if (!(e.start_time < e.end_time)) errors.push(`${where}: start time must be before end time (no windows across midnight)`);
        if (e.start_date && e.end_date && !(e.start_date <= e.end_date)) errors.push(`${where}: start date must be on or before end date`);
      });
      if (errors.length) throw new ApiError(errors.map(e => `  - ${e}`).join("\n"));
      this.cfg.schedule = list; this._save(); return list;
    }
    async putRandomizer(obj) { this.cfg.randomizer = obj; this._save(); return obj; }
    async putClockChime(obj) {
      if (obj.start_hour > obj.end_hour) throw new ApiError("  - clock chime start hour must be on or before end hour");
      this.cfg.clock_chime = obj; this._save(); return obj;
    }
    async putLayouts(list) {
      const names = list.map(l => l.name);
      if (new Set(names).size !== names.length) throw new ApiError("  - layout names must be unique");
      this.cfg.layouts = list; this._save(); return list;
    }
    async putBackground() { throw new ApiError("Background images need the controller - not available in the demo."); }
    backgroundUrl() { return null; }
    async artnetStatus() { return null; }
    async artnetDiscover() { throw new ApiError("Art-Net discovery needs the controller on the gallery network."); }
    reset() { try { localStorage.removeItem(STORE_KEY); } catch { /* ignore */ } this.cfg = structuredClone(this.seed); return this._save(); }
  }

  async function connect() {
    try {
      const res = await fetch("/api/status", { cache: "no-store" });
      if (res.ok && (res.headers.get("content-type") || "").includes("json")) return new ControllerBackend();
    } catch { /* no controller */ }
    const seed = await (await fetch("static/demo-config.json", { cache: "no-store" })).json();
    return new LocalBackend(seed);
  }

  global.HotaBackend = { connect, ApiError };
})(window);
