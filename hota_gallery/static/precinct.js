// Precinct page: the whole HOTA precinct from one place. Reads every
// venue's config through this controller (/api/precinct), shows their
// current looks together on the same card the GIF export makes, puts one
// colour live everywhere or schedules it for an event day, and exports
// one GIF with every venue on it. Picking a venue in the menu by the logo
// goes to that venue's own page to give it its own look.
(async () => {
  "use strict";

  const $ = s => document.querySelector(s);
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(kid));
    return el;
  }
  const getJson = async url => { const r = await fetch(url, { cache: "no-store" }); if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); };
  const send = async (method, url, body) => {
    const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    const j = await r.json().catch(() => null);
    if (!r.ok) throw new Error((j && j.error) || `HTTP ${r.status}`);
    return j;
  };
  const PRECINCT = "Precinct (all)";

  // Clock, like the venue pages.
  const tick = () => { $("#clock").textContent = new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" }); };
  tick(); setInterval(tick, 1000);

  // Remote PIN: the same speed bump as the venue pages (same browser memory).
  const isLocalHost = ["localhost", "127.0.0.1", "[::1]", "::1"].includes(location.hostname);
  let own = null;
  try { own = await getJson("/api/config"); } catch { /* shown below */ }
  if (own && own.remote_pin && !isLocalHost) {
    let ok = null;
    try { ok = localStorage.getItem("hotaRemotePinOk"); } catch { /* private browsing */ }
    if (ok !== own.remote_pin) await new Promise(resolve => {
      const field = h("input", { type: "tel", inputmode: "numeric", class: "dlg-input", placeholder: "PIN", autocomplete: "off" });
      const msg = h("p", { class: "dlg-msg" });
      const form = h("form", { method: "dialog", class: "dlg-body" },
        h("h3", { class: "dlg-title", text: "Enter PIN" }),
        h("p", { class: "dlg-msg", text: "Ask HOTA staff for the access PIN to control the lights remotely." }),
        field, msg, h("div", { class: "dlg-actions" }, h("button", { type: "submit", class: "btn primary", text: "Unlock" })));
      const dlg = h("dialog", { class: "dlg" }, form);
      form.addEventListener("submit", e => {
        e.preventDefault();
        if (field.value.trim() === own.remote_pin) {
          try { localStorage.setItem("hotaRemotePinOk", own.remote_pin); } catch { /* private browsing */ }
          dlg.close(); dlg.remove(); resolve();
        } else { msg.textContent = "Wrong PIN, try again."; field.value = ""; field.focus(); }
      });
      dlg.addEventListener("cancel", e => e.preventDefault());
      document.body.append(dlg); dlg.showModal(); field.focus();
    });
  }

  // Drawings each venue's card needs (shipped with every controller).
  const [elev, galleryLayout, v3d, vb] = await Promise.all(
    ["static/elevations.json", "static/building-layout.json", "static/vos3d.json", "static/vos-building.json"]
      .map(u => getJson(u).catch(() => null)));
  const scene = window.HotaBuildingScene || null;
  if (scene) await scene.ready;

  // A venue's config -> what the export's card needs for its panel.
  function panelFor(name, cfg) {
    const kind = cfg.venue || "gallery", ls = cfg.layouts || [];
    if (kind === "gallery") return { name, cfg, elev, layout: ls.find(l => l.name === "Building") || galleryLayout };
    if (kind === "vos") return { name, cfg, stage: v3d && vb ? { vb, v3d } : null, layout: ls[0] || null };
    if (kind === "building") return { name, cfg, scene, layout: ls.find(l => l.name === "Street view") || (scene ? scene.layout(cfg.fixtures) : ls[0]) };
    return { name, cfg, layout: ls[0] || null };
  }

  // Plain-words summary of a look, for the venue list.
  const hex = c => "#" + [c.r, c.g, c.b].map(v => (v | 0).toString(16).padStart(2, "0")).join("");
  function describe(cl) {
    const l = cl && cl.default, n = cl && cl.fixtures ? Object.keys(cl.fixtures).length : 0;
    const mode = l && l.colour ? l.colour.mode : "off";
    const what = { off: "Off", solid: l && l.colour.color ? `Solid ${hex(l.colour.color)}${l.colour.color.w ? ` + W${l.colour.color.w}` : ""}` : "Solid",
      gradient: "Gradient", color_cycle: "Colour cycle", sweep: "Sweep", chase: "Chase", sine_chase: "Sine chase", trickle: "Trickle", shader: "Shader" }[mode] || mode;
    const fx = l && l.effect && l.effect.mode !== "off" ? `, ${l.effect.mode}` : "";
    return what + fx + (n ? `, ${n} fixture${n > 1 ? "s" : ""} with their own look` : "");
  }

  // Venue menu: each venue's own page, or stay here.
  let venues = [];
  function renderMenu() {
    const sel = $("#venueSel");
    sel.replaceChildren(...venues.map(v => h("option", { value: v.name, text: v.name + (v.ok ? "" : " (offline)"), disabled: !v.ok })),
      h("option", { value: PRECINCT, text: PRECINCT, selected: true }));
    sel.hidden = false; $("#pcTitle").hidden = true;
    sel.onchange = () => { if (sel.value !== PRECINCT) location.href = "/venue/" + encodeURIComponent(sel.value); };
    const from = $("#copyFrom"), keep = from.value;
    from.replaceChildren(...venues.filter(v => v.ok).map(v => h("option", { value: v.name, text: v.name, selected: v.name === keep })));
  }
  function renderVenues() {
    $("#venues").replaceChildren(...venues.map(v => h("div", { class: "pc-venue", "data-online": String(!!v.ok) }, h("i"),
      h("b", { text: v.name }),
      h("p", { class: "hint", text: v.ok ? describe(v.result.current_look) : `Not answering (${v.error || "offline"})` }),
      v.ok ? h("a", { class: "btn sm", href: "/venue/" + encodeURIComponent(v.name), text: "Open" }) : null)));
  }

  // Live preview: the precinct card, rebuilt whenever a look changes.
  const canvas = $("#preview"), pctx = canvas.getContext("2d");
  let renderer = null, looksKey = "", building = false;
  async function rebuildPreview() {
    const panels = venues.filter(v => v.ok).map(v => panelFor(v.name, v.result));
    if (!panels.length || building) return;
    building = true;
    try { renderer = await HotaExport.precinctPreview(panels, canvas.width, canvas.height, "Live now", "Every venue's current look"); }
    finally { building = false; }
  }
  const t0 = performance.now();
  (function loop() {
    if (renderer) { renderer.draw((performance.now() - t0) / 1000); pctx.drawImage(renderer.canvas, 0, 0); }
    requestAnimationFrame(loop);
  })();

  async function refresh() {
    const ORDER = { gallery: 0, vos: 1, building: 2 };
    const rank = v => (v.ok && v.result.venue in ORDER ? ORDER[v.result.venue] : 3);
    try { venues = (await getJson("/api/precinct")).sort((x, y) => rank(x) - rank(y)); }
    catch (e) { $("#venues").replaceChildren(h("p", { class: "pc-msg bad", text: `Couldn't reach the venues: ${e.message}` })); return; }
    renderMenu(); renderVenues();
    const key = JSON.stringify(venues.map(v => v.ok ? [v.name, v.result.current_look, v.result.layouts] : v.name));
    if (key !== looksKey) { looksKey = key; await rebuildPreview(); }
    $("#exportBtn").disabled = !venues.some(v => v.ok);
  }
  await refresh();
  setInterval(refresh, 5000);

  // Colour: HOTA-ish quick picks, or any colour. Warm white uses the W
  // channel where the fixtures have one.
  const SWATCHES = [["Orange", "#ff6a00"], ["HOTA pink", "#d5134d"], ["Red", "#ff0000"], ["Purple", "#7a1fff"], ["Blue", "#0050ff"],
    ["Teal", "#00c8b4"], ["Green", "#00d23c"], ["Yellow", "#ffc800"], ["Warm white", "white"]];
  let pick = { r: 255, g: 106, b: 0, w: 0 };
  const colourIn = $("#colour");
  function setPick(c, from) {
    pick = c;
    if (from !== "input") colourIn.value = c.w && !c.r && !c.g && !c.b ? "#ffe0b0" : hex(c);
    $("#colourHex").textContent = c.w && !c.r && !c.g && !c.b ? "Warm white (W)" : hex(c).toUpperCase();
    document.querySelectorAll(".pc-sw").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.c === (c.w && !c.r ? "white" : hex(c)))));
  }
  const toRgb = s => ({ r: parseInt(s.slice(1, 3), 16), g: parseInt(s.slice(3, 5), 16), b: parseInt(s.slice(5, 7), 16), w: 0 });
  $("#swatches").replaceChildren(...SWATCHES.map(([name, c]) => h("button", {
    type: "button", class: "pc-sw", title: name, "aria-label": name, "data-c": c, "aria-pressed": "false",
    style: `background:${c === "white" ? "#ffe0b0" : c}`,
    onclick: () => setPick(c === "white" ? { r: 0, g: 0, b: 0, w: 255 } : toRgb(c)),
  })));
  colourIn.addEventListener("input", () => setPick(toRgb(colourIn.value), "input"));
  setPick(pick);
  const solid = () => ({ colour: { mode: "solid", color: { ...pick } }, effect: { mode: "off" }, video: { mode: "off" } });

  // Results from every venue, in one line.
  function report(el, results, done) {
    const bad = results.filter(r => !r.ok);
    el.classList.toggle("bad", !!bad.length);
    el.textContent = bad.length
      ? `${done} on ${results.filter(r => r.ok).map(r => r.name).join(", ") || "nowhere"}. Didn't work on ${bad.map(r => `${r.name} (${r.error})`).join(", ")}.`
      : `${done} on ${results.map(r => r.name).join(", ")}.`;
  }
  async function act(btn, el, fn) {
    btn.disabled = true; el.classList.remove("bad"); el.textContent = "Working…";
    try { await fn(); } catch (e) { el.classList.add("bad"); el.textContent = e.message; }
    btn.disabled = false;
    refresh();
  }
  const confirmAll = msg => window.confirm(msg);

  $("#applyAll").addEventListener("click", e => {
    if (!confirmAll("Put this colour live on every venue now? It replaces whatever each one is showing until the next schedule change or \"Back to schedules\".")) return;
    act(e.currentTarget, $("#lookMsg"), async () => report($("#lookMsg"), await send("PUT", "/api/precinct/look", { look: solid() }), "Live"));
  });
  $("#copyAll").addEventListener("click", e => {
    const v = venues.find(x => x.ok && x.name === $("#copyFrom").value);
    if (!v) return;
    if (!confirmAll(`Put ${v.name}'s current look live on every venue?`)) return;
    act(e.currentTarget, $("#lookMsg"), async () => report($("#lookMsg"), await send("PUT", "/api/precinct/look", { look: v.result.current_look.default }), `${v.name}'s look is live`));
  });
  $("#clearAll").addEventListener("click", e => {
    if (!confirmAll("Send every venue back to whatever its own schedule says right now?")) return;
    act(e.currentTarget, $("#lookMsg"), async () => report($("#lookMsg"), await send("PUT", "/api/precinct/clear", {}), "Back to schedule"));
  });

  // Event day: a priority entry on every venue's schedule.
  const evDate = $("#evDate");
  evDate.valueAsDate = new Date();
  $("#schedAll").addEventListener("click", e => {
    const el = $("#schedMsg");
    const d = evDate.value, from = $("#evFrom").value, to = $("#evTo").value, name = $("#evName").value.trim() || "Precinct event";
    if (!d) { el.classList.add("bad"); el.textContent = "Pick a date."; return; }
    if (!from || !to || from >= to) { el.classList.add("bad"); el.textContent = "The start time needs to be before the end time, on the same day."; return; }
    const md = d.slice(5); // "MM-DD"
    const entry = { name, active_days: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"], start_time: from, end_time: to,
      start_date: md, end_date: md, look: solid(), zone: null, enabled: true, priority: true };
    if (!confirmAll(`Add "${name}" to every venue's schedule: ${d}, ${from} to ${to}, in ${$("#colourHex").textContent}?`)) return;
    act(e.currentTarget, el, async () => report(el, await send("PUT", "/api/precinct/schedule", { entry }), `"${name}" scheduled for ${d}`));
  });

  // Export: the same dialog as a venue's, with every venue on the card.
  HotaExport.preload();
  $("#exportBtn").addEventListener("click", () => {
    const panels = venues.filter(v => v.ok).map(v => panelFor(v.name, v.result));
    if (!panels.length) return;
    const solidEverywhere = new Set(panels.map(p => JSON.stringify(p.cfg.current_look))).size === 1;
    HotaExport.open({
      venues: panels,
      title: "Untitled concept",
      subtitle: (solidEverywhere ? describe(panels[0].cfg.current_look) + " across the precinct" : "Every venue") + ", " +
        new Date().toLocaleDateString([], { day: "numeric", month: "long", year: "numeric" }),
    });
  });
})();
