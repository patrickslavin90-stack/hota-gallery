# HOTA Gallery façade lighting: technical documentation

Lighting control for the HOTA Gallery façade (Home of the Arts, Gold Coast). A Python service on a Raspberry Pi drives 154 fixtures (785 LEDs) over Art-Net, and a browser UI controls it. This document covers the whole system, back end and front end, plus the 2026 UI redesign. It's written so the work can be picked up again from a fresh machine.

A branded HTML version of this document is in `docs/technical.html`, generated from this file by `tools/build_docs.py`.

## At a glance

| | |
|---|---|
| Fixtures | 154 (127 RGBW strip fixtures in the façade "cracks", 27 RGB dish lights) |
| LEDs | 785 |
| Output | Art-Net ArtDMX, broadcast to 255.255.255.255:6454, universes 0–9 (shown as 1–10), 30 fps |
| Controller | Python 3, standard library only (`hota_gallery/`), runs as a systemd service on a Pi |
| Config | one JSON file: `/etc/hota-gallery/config.json` on the Pi, `data/config.json` in the repo |
| UI | plain HTML, CSS and JavaScript with no build step, served by the controller from `hota_gallery/static/` |
| Web port | `web_port` in config (8080 by default) |
| Repo | https://github.com/zacpetersengit/hota-gallery (fork of `patrickslavin90-stack/hota-gallery`) |

## Repository and branches

| Branch | What's on it |
|---|---|
| `master` | the original project, untouched by the redesign |
| `ui-redesign` | the new UI, elevation linework, schedules and settings, GIF export and all fixes |
| `ui-redesign-3d` | everything on `ui-redesign` plus the prototype 3D view |

Remotes: `origin` is the fork (`zacpetersengit/hota-gallery`) and `upstream` is the original (`patrickslavin90-stack/hota-gallery`).

Commit history of the redesign, oldest first:

1. `8523018` HOTA Gallery lighting control, initial commit for review (upstream)
2. `99b57fd` New web UI: elevation linework, HOTA branding, schedules and settings
3. `dd0cdf4` Header: say which controller, add a Lights reachability indicator
4. `d436f04` Handle a lost controller; warn when fixtures ignore the default look
5. `17a217a` Export the live look as an animated GIF for clients and approvals
6. `dbff167` Export GIF: always show the look just picked
7. `487a0c2` Prototype 3D view (`ui-redesign-3d` only)

Repository layout:

```text
data/
  config.json                 the canonical config (fixtures, layouts, looks, presets, schedule)
  fixtures.json               the raw ELM fixture import
  venue_assets/hota/*.png     architect's elevation sheets (ARM, 1:100) - the source for all geometry
docs/
  TECHNICAL.md                this document
  technical.html              branded HTML version (generated)
hota_gallery/                 the controller (Python, stdlib only)
  cli.py  engine.py  webapp.py  config.py  scheduler.py  randomizer.py  clock_chime.py
  artnet.py  shaders.py  bitmaps.py  elm_import.py
  static/                     the web UI (served at / and /static/*)
screenshots/                  live.png, schedules.png, settings.png
tools/                        one-off and dev scripts (see "Tools")
hota-gallery.service          systemd unit
vercel.json                   static demo deployment config
```

## Architecture

```text
 Browser (laptop / iPad / phone)                       Raspberry Pi
 ┌──────────────────────────────┐   HTTP (JSON)   ┌──────────────────────────────────────┐
 │ static/index.html + app.js   │ ──────────────► │ webapp.py  ThreadingHTTPServer        │
 │  backend.js  (API or local)  │ ◄────────────── │   Store: validate → save → reload     │
 │  engine.js   (demo + export) │  /api/preview   │                                        │
 │  export.js   (GIF)           │  every 100 ms   │ engine.py  Engine                      │
 │  view3d.js   (3D, branch)    │                 │   sample_colors() per LED per frame    │
 └──────────────────────────────┘                 │   sender loop @ fps ──► artnet.py      │
                                                  │ scheduler.py  randomizer.py  chime     │
                                                  └──────────────┬───────────────────────┘
                                                                 │ ArtDMX broadcast :6454
                                                                 ▼
                                                    Art-Net nodes ──► DMX ──► fixtures
```

- **One process.** `python -m hota_gallery serve` starts the engine's sender thread, the HTTP server, and the scheduler, randomiser and clock-chime threads.
- **One source of truth.** The config is held in memory by the engine. Every write goes through `Store`: it validates the whole config, saves it atomically and reloads the engine. A change is live the moment the HTTP call returns.
- **Preview is real output.** `GET /api/preview` returns exactly the colours being sent on the wire, computed by the same `sample_colors()` the sender uses.

## Back end (Python)

### Modules

| Module | Role |
|---|---|
| `cli.py` | Entry points: `validate` and `serve`. Wires the engine, web server and background loops together. |
| `engine.py` | `Engine`: loads fixtures, computes every LED's colour per frame from the current look, builds 512-byte DMX frames per universe, and runs the sender loop at `fps`. Also handles the systemd watchdog (`sd_notify`). |
| `artnet.py` | Builds ArtDMX packets, plus the `ArtNetSender` broadcast socket and ArtPoll/ArtPollReply discovery. |
| `webapp.py` | `Store` (locked config mutations) and the HTTP handler: the routes in "HTTP API". |
| `config.py` | Schema, defaults and `validate_config()`, which collects every error at once; `load_config` and `save_config` (atomic `.tmp` then `os.replace`). |
| `scheduler.py` | Applies time-of-day looks per target (the whole building, or a zone). |
| `randomizer.py` | Short random preset bursts on random zones. |
| `clock_chime.py` | Plays a look on the hour, within an hour window. |
| `shaders.py` | Python ports of ELM's generative shaders: circle, line, cross and square scroll, radar, plasma. |
| `bitmaps.py` | 32×16 brightness masks: dot, ring, bar_v, bar_h, cross, diamond, checker, stripes_v, stripes_h, triangle, heart, noise. |
| `elm_import.py` | One-time importer from the ELM showfile to `fixtures.json`. |

### Running it

```bash
python -m hota_gallery validate --config data/config.json
python -m hota_gallery serve --config data/config.json            # web on web_port (8080)
python -m hota_gallery serve --config data/config.json --host 127.0.0.1 --port 8765
```

`--port` overrides `web_port` for that run only. The Settings page will then show a "restart to move to port N" notice, because the config and the running port differ.

**Safe local testing.** With `bind_ip` set to null, the engine broadcasts Art-Net on whichever network card it picks automatically. Off-site, set **Settings → Device → Network interface** to `127.0.0.1` (or edit `bind_ip` in a copy of the config) so nothing reaches real lights on whatever network you're on.

### The engine

- **Fixtures.** Each fixture has `universe`, `address` (both 0-based), `led_count`, `led_type` (`RGBW` = 4 channels per LED, `RGB` = 3), `zone`, and two `points`. LED positions are interpolated evenly between the two points (a single-LED fixture sits at the first point). A fixture's identity is `"universe:address"`, because names repeat in the ELM data.
- **Coordinates.** Effects (gradients, sweeps, chases, shaders, bitmaps) are computed over the fixtures' own `points`, normalised to the bounding box of all LEDs. These are ELM's coordinates. Layouts, including the new "Building" layout, are display positions only and never change the output.
- **A look** has three layers, multiplied together: `colour` (what RGBW value), `effect` (a 0–1 brightness mask over position and time) and `video` (a 0–1 bitmap mask). An "off" effect or video layer passes everything through. An "off" colour layer is black.
- **current_look** is `{ "default": look, "fixtures": { "u:a": look } }`. A fixture with its own entry ignores the default.
- **Output.** For each universe that has fixtures, a 512-byte buffer is filled from the sampled colours (RGBW or RGB per `led_type`) and sent as ArtDMX to 255.255.255.255:6454 from `bind_ip` (or an automatically chosen address). The loop paces itself to `fps` and pings the systemd watchdog.

### Background loops

| Loop | Interval | Behaviour |
|---|---|---|
| Scheduler | every 15 s | For each target (whole building, or a zone), the **last** matching enabled entry in the list wins. It's re-applied only when the active entry changes. If a target has been dark for 60 s while its entry wants it lit, the entry is re-applied. Dark overrides on fixtures in zones without a schedule are cleared after 60 s. |
| Randomiser | checks every 5 s | Fires at a jittered 0.5–1.5 × (3600 / `events_per_hour`) seconds. Each burst applies a random preset from `preset_slots` to a random zone in `targets` for `burst_s` seconds, then clears it. |
| Clock chime | checks every 5 s | At minute 0, when `start_hour` ≤ hour ≤ `end_hour`, applies `look` to every fixture for `duration_s` seconds, then clears it. |

Time windows can't cross midnight (start < end) and date ranges can't cross New Year. To span midnight, use two entries.

### Config persistence

`Store` methods take a lock, copy the in-memory config, apply the change, validate the **whole** config, write it atomically (`config.json.tmp`, then `os.replace`) and reload the engine. Layout background images are stored next to the config in `backgrounds/`. Because the service user needs write access to its config directory for that atomic rename, the systemd unit allows `ReadWritePaths=/etc/hota-gallery`.

### Deployment on the Pi (systemd)

`hota-gallery.service` installs the package to `/opt/hota-gallery`, runs it as the unprivileged `hota-gallery` user with `Type=notify` and `WatchdogSec=30s`, and hardens it (`ProtectSystem=strict`, no capabilities, `AF_INET`/`AF_UNIX` only). The full install steps are in the header comment of `hota-gallery.service`.

```bash
sudo systemctl restart hota-gallery      # apply a new web_port, pick up new code
journalctl -u hota-gallery -f            # logs
```

The service can't bind ports below 1024, which is why the UI rejects them.

**Deploying the new UI** means copying `hota_gallery/static/` to `/opt/hota-gallery/hota_gallery/static/`. The redesign changed no Python code. Static files are served with `Cache-Control: no-store`, so browsers pick up new files on the next load. The Pi's existing config doesn't need the new "Building" layout: the UI falls back to `static/building-layout.json` and only writes it into the config the first time someone moves fixtures in it.

## HTTP API

All bodies are JSON. Errors return `{"error": "..."}`. Validation errors (400) list every problem, one per line, as `"  - message"`. Every `PUT` revalidates the whole config.

### GET

| Path | Returns |
|---|---|
| `/`, `/index.html` | the UI (`static/index.html`) |
| `/static/<path>` | any file under `static/` (404 outside it) |
| `/api/status` | `{device_name, fixture_count, current_look}`. The UI uses this to detect a controller and re-sync looks every ~5 s. |
| `/api/config` | the full validated config |
| `/api/preview` | `[{name, colors: [[r,g,b,w], …per LED]}, …]`, in `config.fixtures` order. Exactly what's being sent. |
| `/api/schedule` | the schedule list |
| `/api/presets` | the presets list |
| `/api/randomizer` | the randomiser object |
| `/api/clock_chime` | the clock chime object |
| `/api/layouts` | the layouts list |
| `/api/artnet/status` | `{configured_bind_ip, effective_bind_ip, artnet_port, broadcast_address, web_port, fps, universes: [{universe, fixture_count, channel_count, max_address}]}` |
| `/api/artnet/discover` | ArtPoll scan (~2 s): `[{ip, mac, short_name, long_name, num_ports, universes_in, universes_out}]`. 500 if the scan couldn't run. |
| `/api/layouts/<name>/background` | the layout's background image (404 if none) |

### PUT

| Path | Body | Returns / effect |
|---|---|---|
| `/api/config` | full config | replaces everything; returns the validated config |
| `/api/look` | a look | sets `current_look.default`; returns it |
| `/api/look/selection` | `{fixtures: ["u:a", …], look: look \| null}` | gives those fixtures their own look, or clears it when `look` is null; returns `current_look` |
| `/api/fixtures/<u:a>/position` | `{dx, dy}` | moves a fixture's real `points` (changes effects; not used by the UI) |
| `/api/schedule` | schedule list | returns the saved list |
| `/api/presets` | presets list | returns the saved list |
| `/api/randomizer` | randomiser object | returns it |
| `/api/clock_chime` | clock chime object | returns it |
| `/api/layouts` | layouts list | returns the saved list |
| `/api/layouts/<name>/move` | `{keys, dx, dy}` | moves fixtures within one layout (not used by the UI) |
| `/api/layouts/<name>/background` | raw image bytes (empty body removes it) | stores `backgrounds/<name>.jpg`; returns the layouts |

Example:

```bash
curl -X PUT http://pi:8080/api/look -H "Content-Type: application/json" \
  -d '{"colour":{"mode":"solid","color":{"r":255,"g":169,"b":20,"w":0}},"effect":{"mode":"off"},"video":{"mode":"off"}}'
```

## Config schema

Unknown keys are errors everywhere except at the top level. Colours are `{r, g, b, w}` integers 0–255, and all four are required. `direction` is one of `forward`, `reverse`, `bounce`, `wings`. `axis` is `x` or `y`.

### Top level

| Key | Type / rule | Default |
|---|---|---|
| `fps` | number > 0 | 30 |
| `bind_ip` | IPv4 string or null (null = choose automatically) | null |
| `web_port` | int 1–65535 (the UI enforces ≥ 1024) | 8080 |
| `device_name` | string | "" |
| `fixtures` | list of fixtures | |
| `zones` | `[{name}]`, unique names (here `strips`, `dishes`) | |
| `current_look` | `{default: look, fixtures: {"u:a": look}}`; keys must be existing fixtures | all off |
| `schedule`, `presets`, `randomizer`, `clock_chime`, `layouts` | see below | |

### Look layers

| Layer.mode | Parameters (engine default when missing) |
|---|---|
| colour `off` | none |
| colour `solid` | `color` |
| colour `gradient` | `axis`, `stops: [{offset 0–1 ascending, color}]` (≥ 2) |
| colour `color_cycle` | `colors` (≥ 2), `period_s` > 0 (5) |
| colour `sweep` | `axis`, `stops`, `period_s` (4), `direction` |
| effect `pulse` | `period_s` (2) |
| effect `strobe` | `on_ms`, `off_ms` (positive ints, required) |
| effect `chase` | `period_s` (1), `tail` % of span ≥ 0 (3), `axis`, `direction` |
| effect `sine_chase` | `period_s` (2), `wavelength` % of span > 0 (4), `axis`, `direction` |
| effect `trickle` | `period_s` (3), `floor` 0–1 (0) |
| effect `shader` | `shader` ∈ circle_scroll, line_scroll, cross_scroll, square_scroll, radar, plasma; `speed` (1), `n_items` (20), `force` (3), `force2` (5), all > 0 |
| video `bitmap` | `bitmap` (name), `axis`, `period_s` ≥ 0 (0 = still), `direction` |

### Other sections

- **Preset:** `{slot: int ≥ 0 (unique), name: string | null, look}`.
- **Schedule entry:** `{name (unique, non-empty), enabled (default true), zone: null | zone name, active_days: subset of mon…sun (non-empty), start_time "HH:MM" < end_time "HH:MM", start_date / end_date: "MM-DD" | null (start ≤ end), look}`.
- **Randomiser:** `{enabled, events_per_hour > 0, burst_s > 0, targets: [zone names], preset_slots: [existing slots]}`. Deleting a preset the randomiser still references fails validation, which is why the UI removes it from `preset_slots` first.
- **Clock chime:** `{enabled, look, duration_s > 0, start_hour 0–23 ≤ end_hour 0–23}`.
- **Layout:** `{name (unique), fixtures: {"u:a": [[x, y], [x, y]]}, background?: string | null}`. Display positions only.

## Front end (static/)

No framework and no build step. Edit the file, reload the browser.

| File | Role |
|---|---|
| `index.html` | page shell: header, tabs, Live / Schedules / Settings views |
| `app.css` | design tokens and all styles |
| `app.js` | the app: Live canvas, look editor, presets, schedules, settings, dialogs, dropdowns |
| `backend.js` | `ControllerBackend` (the HTTP API) and `LocalBackend` (demo: localStorage + in-page engine) |
| `engine.js` | browser port of `engine.py` + `shaders.py` + `bitmaps.py`; same maths and same look schema |
| `export.js` | Export GIF |
| `view3d.js` | 3D view (`ui-redesign-3d` branch) |
| `elevations.json` | traced elevation linework, generated by `tools/build_elevations.py` |
| `building-layout.json` | built-in "Building" layout, generated by `tools/map_to_elevations.py` |
| `building3d.json` | 3D massing, generated by `tools/build_3d.py` (3D branch) |
| `demo-config.json` | config used by demo mode (a copy of `data/config.json`) |
| `hota-logo.svg` | white HOTA wordmark (from hota.com.au) |
| `legacy.html` | the original UI, still at `/static/legacy.html` |
| `vendor/` | `gifenc.esm.js` (MIT); `three/` (three.js 0.160 + OrbitControls, MIT, 3D branch) |

### Boot and backends

1. `backend.js connect()` tries `GET /api/status`. If it gets a JSON answer it uses `ControllerBackend`; otherwise it loads `static/demo-config.json` into `LocalBackend`, which saves to `localStorage` (key `hota-gallery-demo-config-v1`) and computes the preview with `engine.js`.
2. It loads the config, `elevations.json`, `building-layout.json` and, on the 3D branch, `building3d.json`.
3. It renders the layout picker, selection tools, presets and look editor, then starts the loops.

### Live tab

- **Canvas.** Draws the elevations (floor levels, outline, crack bands and glazing grid) once into a cached base layer. Each frame it draws the LEDs into a light layer, composites two Gaussian blur passes (14 px and 4 px, additive) for the glow, then draws the sharp LEDs on top. Unlit LEDs show as dim grey fittings. Pan with right-drag, Alt-drag or Space-drag; scroll to zoom; Fit. The camera refits on resize unless you've zoomed by hand.
- **Selection.** Click, Shift or Ctrl-click to add, drag a marquee, or use the chips (All, Strips, Dishes, By wall, Patterns). Chips light up when the selection matches them exactly, or matches a union of chips in one group, and clicking a lit chip deselects. Keyboard: Esc clears the selection, Ctrl+A selects all, +/−/0 zoom, and arrow keys nudge (Shift for bigger steps) in Move mode.
- **Look editor.** Colour, Effect and Video sections with mode chips. The active option is filled in `#d5134d`, and the section header shows the active mode as a red pill. Each edit updates the page's own copy of `current_look` straight away, then saves to the controller in the background. Rapid edits collapse into one request, sent in order. With a selection it edits those fixtures (`/api/look/selection`), otherwise the default.
- **Target box.** Says what you're editing. When nothing is selected and some fixtures have their own look, it warns (amber) and offers "Select them" and "Revert all to default".
- **Presets.** Click to apply. Rename, delete and "Save current look" go through themed dialogs.
- **Layouts menu.** Move fixtures (drag or arrow keys, then Save or Discard), new, rename, delete, add or remove the selection, and set a background image (controller only).
- **Preview polling.** In controller mode, `GET /api/preview` every 100 ms, one request at a time, while the tab is visible. After 5 consecutive failures the header turns red ("Lost connection…") and polling backs off to every 2 s. The first success reconnects, reloads the config and shows a toast. Every 5 s, `/api/status` re-syncs `current_look`, except within 3 s of a local edit.

### Schedules tab

- **Edits stay local until you save.** Entries are edited in a draft, and nothing is sent until **Save schedule** (`PUT /api/schedule`).
- **"Now" strip.** Shows which entry wins for each target, using this device's clock.
- **Entries table.** Enable, name, target, days, a time bar across 24 h with a now-marker, dates, a look swatch, up/down (the last match wins), Edit, Duplicate and Delete.
- **Editor drawer.** Name, enabled, zone, days (with Every day, Weekdays and Weekends shortcuts), times, and month/day dates. The look can start from a preset or copy the live look. The editor checks everything the server would, with plain-language errors.
- **Randomiser and Hourly chime.** Each has its own card with its own Save.

### Settings tab

- **Device.** Name, output fps, network interface (`bind_ip`) and web port, all saved to config. The web port must be 1024–65535 and only takes effect after a restart, so an amber notice offers "Copy restart command" and "Open new address".
- **Art-Net output.** Status from `/api/artnet/status`, plus a per-universe Node column from the last ArtPoll scan: green when a node replied, red for no node, grey when not checked. A summary pill shows "N of M universes answering", and each node that replied gets a card. Scans run automatically on page load, every 5 minutes and on "Scan again". A failure slides down a red alert under the header.
- **Header Lights pill.** The same scan result: green "Lights: N/M universes", amber for partial, red "No lights answering". Clicking it opens Settings. The connection label says "Controller on this computer" or "Controller at <host>", which names the controller rather than claiming the building is reachable.
- **Configuration file.** Download `config.json`, import a replacement (`PUT /api/config`) and, in the demo, reset.
- **Fixtures table.** Filterable; universe and address shown 1-based like ELM.

### Export GIF (`export.js`)

- **The card.** A 16:9 image: the HOTA logo top right, "Gallery Façade Lighting Concept" top left, the elevations with the lights animating, the concept name with a red rule, and a description line (look summary and date).
- **How frames are made.** `engine.js` is stepped at an exact 70 ms per frame, so the animation is deterministic rather than a screen recording. One 256-colour palette is sampled from four frames and applied to every frame, so colours don't flicker. Encoding uses `gifenc`, loaded on demand.
- **Options.** Concept name (pre-filled from a matching preset), description, view (whole building or one elevation), length (defaults to the look's longest period) and size: Small 640×360, Medium 960×540, Large 1280×720, Full HD or 4K. The file-size estimate is calibrated from real exports (a 6 s export is ~5 MB at Medium and ~32 MB at 4K).
- **Behaviour.** The dialog opens instantly (the logo and fonts are preloaded at page load) and follows the live look while open.

### 3D view (`view3d.js`, branch `ui-redesign-3d`)

- **What it shows.** The four elevation walls folded into a rough massing from `building3d.json`. In three.js coordinates, x is plan X east, y is height and z is minus plan Y (north), all in metres.
- **The model.** Walls are opaque dark meshes made from the traced outlines, with the linework drawn 12 mm in front of them. There's a roof cap at roof level and a ground grid.
- **The lights.** Additive glow point-sprites 70 mm in front of the wall, with extra samples every 140 mm along the strips so they read as continuous tape. They're coloured each frame from the same `live.preview` as the 2D view.
- **Camera.** Orbit controls, plus presets (North-east, South-east, South-west, North-west, Above) fitted to the window. Selection stays in 2D.
- **Loading.** three.js loads only when 3D is first opened.

### UI building blocks

- **`h(tag, attrs, …children)`.** The DOM helper. ARIA booleans are written as the strings `"true"`/`"false"`, because CSS matches on `[aria-pressed="true"]`.
- **Pill dropdowns.** Every `<select>` is enhanced automatically, including ones added later, by a MutationObserver. A pill trigger with a listbox popover (hover highlight, a red check on the current choice, full keyboard support) is added beside it. The native select stays as the source of truth, with its `value` and `selectedIndex` setters hooked so code that sets them keeps the trigger in sync.
- **`ask({...})`.** Themed modal replacing `prompt`/`confirm`, built on `<dialog>`.
- **Toasts.** Identical messages merge into one with a ×N count.
- **`slideAlert(slot, title, msg, ms, {kind, actions})`.** Red (error) or amber (`warn`) notice that slides out of a card header.

### Design system

| Token | Value | Use |
|---|---|---|
| `--bg` | `#0f0e0e` | page (HOTA charcoal-950) |
| `--surface` / `-2` / `-3` | `#1c1a1a` / `#282727` / `#343232` | panels, controls, hover |
| `--line`, `--line-2` | `#343232`, `#484545` | borders |
| `--text`, `--text-2`, `--muted` | `#f5f5f5`, `#c9c8c8`, `#8f8e8e` | text |
| `--accent` | `#d5134d` | HOTA feature colour: active states, primary buttons |
| `--warn`, `--ok` | `#fcbb00`, `#9aebd3` | amber notices, connected |
| `--pill`, `--radius-card`, `--radius-tile` | 999px, 16px, 12px | buttons/inputs, cards, inner tiles |

Type is Rubik (Google Fonts) at 400 and 500, with tabular numerals. The palette and logo come from hota.com.au, and the feature colour was specified by the client.

## Geometry pipeline (tools)

All geometry comes from the architect's strip-light setout sheets in `data/venue_assets/hota/`: 7152×5052 px PNGs at 1:100, where 0.0851 sheet px = 1 mm.

| Tool | Output | What it does |
|---|---|---|
| `tools/build_elevations.py` | `static/elevations.json` | Traces South, East, North and West into simplified polylines with OpenCV: the building outline, the grey crack bands (closed then opened, so the white lit-strip line and dimension strings don't split them), and the glazing grid (Hough lines). It also detects dish symbols. Elevations are laid out S, E, N, W, and the West/South sheet is shifted +154 px so floor levels share one datum. |
| `tools/map_to_elevations.py` | the "Building" layout in `data/config.json` + `static/building-layout.json` | Places every fixture on the drawings. Strips: the ELM runs have the right order but wrong proportions, so each run is mapped piecewise through anchor pairs (an ELM corner matched to the same crack corner on the drawing, read at full resolution): East = universes 0–2, North A = 3–5, North B = 6–8. Dishes: 22 on detected symbols, 2 on the side-on corner symbols, and 3 placed low on the South wall (that part isn't drawn), assigned left to right. |
| `tools/build_3d.py` | `static/building3d.json` | Finds the red structural grid lines on each sheet (1–3 at 5,928 and 13,145 mm; J–N at 5,850, 6,000, 6,000 and 5,800 mm; every fit within 5 mm), maps each wall into plan coordinates, and sets the wall planes. The footprint comes out about 26.1 × 22.1 m, with ~31 m to the roof. |
| `tools/build_docs.py` | `docs/technical.html` | Turns this Markdown into the branded HTML page. |
| `tools/check_ui_api.py` | none (pass/fail report) | End-to-end check of the UI files and every API call; restores the config afterwards. |

The OpenCV tools are for development only:

```bash
python -m venv .venv
.venv/Scripts/python -m pip install opencv-python-headless numpy pillow   # Windows (bin/ on Linux/mac)
.venv/Scripts/python tools/build_elevations.py
python tools/map_to_elevations.py
.venv/Scripts/python tools/build_3d.py
cp data/config.json hota_gallery/static/demo-config.json   # keep the demo in step
python tools/build_docs.py
```

Run them in that order. `map_to_elevations.py` reads `elevations.json`, and `build_3d.py` reads both.

## Demo mode and Vercel

With no controller (`/api/status` doesn't return JSON), the UI runs on `demo-config.json`, saves to `localStorage` and simulates the engine in-page. A footer and the header make clear it doesn't affect the building. `vercel.json` serves `hota_gallery/static` as the site root, with `/static/*` rewritten to `/*`, so the same files work at `/`. It hasn't been deployed. The fork is public, so a deployment would make the code and the HOTA branding public.

## Testing

```bash
python -m hota_gallery validate --config data/config.json
python tools/test_scheduler.py                       # 6 scheduler cases
python tools/check_ui_api.py http://127.0.0.1:8765   # UI files + every API call; restores the config
```

- **Local test controller.** Run it on a copy of the config with `bind_ip` set to `127.0.0.1`, so Art-Net stays on the machine.
- **Use `127.0.0.1` on Windows.** It's faster than `localhost`, which tries IPv6 first, and the Python server opens a new connection per request, so every request pays ~0.3 s.
- **Background tabs.** Browsers pause `requestAnimationFrame` in hidden tabs, so the 2D glow, the export preview and the 3D view only animate while the tab is visible.
- **Screenshots** were taken with headless Edge:

```bash
msedge --headless=new --disable-gpu --hide-scrollbars --run-all-compositor-stages-before-draw \
  --window-size=1630,1095 --virtual-time-budget=9000 --screenshot=screenshots/live.png http://127.0.0.1:8765/
```

## Known limitations and open items

- **Dish positions.** Nothing ties a dish's name to its physical position, so dishes are assigned to symbols wall by wall. Three South-wall dishes are placed by estimate, and need confirming on site.
- **Effects use ELM coordinates.** Chases and sweeps travel through ELM's schematic coordinates, not the real building. Switching the engine to the Building layout positions would make them physically accurate, but it changes the real output, so it's a deliberate decision to make, not done yet.
- **3D model is rough.** The South setout only shows that wall from Level 2 up and across about 18 of 26 m. The canopy pop-outs are drawn flat on their walls, and walls are single planes. An export of the architect's model (OBJ, FBX, glTF or IFC) would replace the folded walls.
- **Caching.** Static files are sent with `no-store`, so `vendor/` (three.js, about 670 KB) re-downloads each time. Caching `vendor/` in `webapp.py` would be a small improvement.
- **4K GIFs are large.** About 32 MB for 6 s. An MP4 or WebM export would suit high-resolution screens better.
- **Not yet done:** the Vercel deploy, merging `ui-redesign` (or `-3d`) into `master`, and testing on real Pi hardware.

## Third-party code and assets

| What | Licence | Where |
|---|---|---|
| gifenc 1.0.3 (Matt DesLauriers) | MIT | `static/vendor/gifenc.esm.js` + `gifenc.LICENSE.md` |
| three.js 0.160 + OrbitControls | MIT | `static/vendor/three/` + `LICENSE` |
| Rubik | SIL OFL | Google Fonts (loaded from the web) |
| HOTA wordmark | HOTA's trademark | `static/hota-logo.svg`, from hota.com.au. For HOTA's own controller; check with HOTA before using it publicly. |
| Elevation sheets | ARM Architecture / HOTA | `data/venue_assets/hota/`, the source for all traced geometry |

## Rebuilding on a new machine

1. Install Git, Python 3.10+, Node (only for the syntax checks) and the GitHub CLI (`winget install GitHub.cli`, then `gh auth login`).
2. `gh repo clone zacpetersengit/hota-gallery`, then `git switch ui-redesign` (or `ui-redesign-3d`).
3. Make a safe copy of the config: copy `data/config.json`, set `"bind_ip": "127.0.0.1"`, then run `python -m hota_gallery serve --config <copy> --host 127.0.0.1 --port 8765`.
4. Open http://127.0.0.1:8765/ (the old UI is at `/static/legacy.html`).
5. Before committing UI changes, run `python tools/check_ui_api.py http://127.0.0.1:8765`.
6. To regenerate the geometry, set up the dev venv shown under "Geometry pipeline" and rerun the tools.
