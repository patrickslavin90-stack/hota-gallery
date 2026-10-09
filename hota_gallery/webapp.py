"""Pure-stdlib HTTP API - same shape as sacn2wiz's webapp.py: a
ThreadingHTTPServer/BaseHTTPRequestHandler, JSON over GET/PUT, and a Store
that funnels every mutation through validate -> atomic save -> reload the
live engine, so a bad request never reaches the network and a crash
mid-write never corrupts config.json.

Step 2 scope: just enough API to prove the pattern end-to-end (status,
read/replace config, set the live look). The layout editor/gradient/
bitmap/schedule endpoints (steps 3-5) are additions to this same Store,
not a different architecture.
"""

from __future__ import annotations

import copy
import datetime
import io
import json
import logging
import mimetypes
import re
import subprocess
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

from .artnet import ARTNET_PORT, discover_nodes
from .config import ConfigError, off_look, save_config, validate_config
from .engine import Engine
from .scheduler import active_entry

log = logging.getLogger("hota_gallery")

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _safe_filename(name: str) -> str:
    """Layout name -> a filesystem-safe stem - layout names are free text
    (spaces, punctuation), filenames shouldn't be."""
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "layout"


def _wifi_ip() -> Optional[str]:
    """This Pi's WiFi interface address, for telling a phone where to find
    the web UI - deliberately NOT bind_ip, which stays pinned to the wired
    Art-Net interface on purpose (a dual-NIC setup: Art-Net output must
    never drift onto the venue WiFi, but staff reaching the UI from a
    phone are only ever on that WiFi, never on the isolated lighting
    network). Shells out to `ip` rather than adding a dependency just for
    interface enumeration - every Pi has iproute2. Returns None off the
    real Pi (no wlan* interface, or `ip` isn't there at all) rather than
    raising, since this is a nice-to-have for one dialog, not core
    behaviour."""
    try:
        out = subprocess.run(
            ["ip", "-4", "-o", "addr", "show"], capture_output=True, text=True, timeout=2,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 2 or not parts[1].startswith("wl"):
            continue
        for i, token in enumerate(parts):
            if token == "inet" and i + 1 < len(parts):
                return parts[i + 1].split("/")[0]
    return None


class Store:
    """Owns the on-disk config and the live engine together - every write
    goes through here so the two can never drift apart."""

    def __init__(self, engine: Engine, config_path: str):
        self.engine = engine
        self.config_path = config_path
        # Lives beside config.json (or /etc/hota-gallery/ on the Pi) so it
        # travels with the venue's data, not the code.
        self.backgrounds_dir = Path(config_path).resolve().parent / "backgrounds"
        self._lock = threading.Lock()

    def get_config(self) -> Dict[str, Any]:
        return self.engine.cfg

    def artnet_status(self) -> Dict[str, Any]:
        """Everything worth checking first when a fixture goes dark: this
        device's own send identity (is bind_ip what you think it is, on
        the right subnet) and which universes the current patch actually
        uses (is the fixture you're chasing even patched where you expect).
        No network traffic - "Scan for nodes" (discover_artnet_nodes) is
        the one that actually talks to the wire."""
        cfg = self.engine.cfg
        configured_ip = cfg.get("bind_ip")
        try:
            effective_ip = configured_ip or Engine._auto_bind_ip()
        except OSError:
            effective_ip = None

        by_universe: Dict[int, Dict[str, Any]] = {}
        for fx in cfg["fixtures"]:
            u = fx["universe"]
            entry = by_universe.setdefault(u, {"universe": u, "fixture_count": 0, "channel_count": 0, "max_address": 0})
            channels_per_led = fx.get("channels_per_led", 4 if fx["led_type"] == "RGBW" else 3)
            entry["fixture_count"] += 1
            entry["channel_count"] += fx["led_count"] * channels_per_led
            entry["max_address"] = max(entry["max_address"], fx["address"] + fx["led_count"] * channels_per_led)

        return {
            "configured_bind_ip": configured_ip,
            "effective_bind_ip": effective_ip,
            # Not an Art-Net field - it rides along on this same endpoint
            # because the frontend already calls it, and "what address
            # reaches this device" is the same family of question as
            # bind_ip. See _wifi_ip's docstring for why it's not just
            # effective_bind_ip: that one is deliberately the *wired*
            # interface, which a phone on the venue WiFi can't reach at all.
            "wifi_ip": _wifi_ip(),
            "artnet_port": ARTNET_PORT,
            "broadcast_address": "255.255.255.255",
            "web_port": cfg["web_port"],
            "fps": cfg["fps"],
            "universes": sorted(by_universe.values(), key=lambda e: e["universe"]),
        }

    def discover_artnet_nodes(self) -> List[Dict[str, Any]]:
        cfg = self.engine.cfg
        bind_ip = cfg.get("bind_ip") or Engine._auto_bind_ip()
        return discover_nodes(bind_ip, timeout=2.0)

    def replace_config(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            validated = validate_config(raw)
            save_config(self.config_path, validated)
            self.engine.reload(validated)
            return validated

    def set_default_look(self, look: Dict[str, Any]) -> Dict[str, Any]:
        """The whole-layout look - unaffected fixtures keep whatever
        per-fixture override they already have, same as before any
        selection existed."""
        with self._lock:
            cfg = dict(self.engine.cfg)
            current_look = dict(cfg["current_look"])
            current_look["default"] = look
            cfg["current_look"] = current_look
            validated = validate_config(cfg)
            save_config(self.config_path, validated)
            self.engine.set_look(validated["current_look"])
            return validated["current_look"]

    def move_fixture(self, key: str, dx: float, dy: float) -> None:
        """Translate one fixture's geometry by (dx, dy) - both of its two
        points move together, a rigid shift rather than independent point
        editing, since dragging a dot on the canvas means "move this
        fixture", not "stretch it". Used by the layout editor to nudge an
        imported position into better alignment with the real building,
        e.g. the dish lights (see elm_import.py's module docstring on why
        the ELM import alone isn't exact for those)."""
        with self._lock:
            cfg = dict(self.engine.cfg)
            fixtures = [dict(f) for f in cfg["fixtures"]]
            for f in fixtures:
                if f"{f['universe']}:{f['address']}" == key:
                    f["points"] = [[p[0] + dx, p[1] + dy] for p in f["points"]]
                    break
            else:
                raise ConfigError(f"no fixture at {key!r}")
            cfg["fixtures"] = fixtures
            validated = validate_config(cfg)
            save_config(self.config_path, validated)
            self.engine.reload(validated)

    def set_fixture_looks(self, keys: list, look: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Give a specific selection of fixtures (by "universe:address" -
        see Fixture.key) their own look, or clear back to the default when
        look is None."""
        with self._lock:
            cfg = dict(self.engine.cfg)
            current_look = dict(cfg["current_look"])
            overrides = dict(current_look.get("fixtures", {}))
            for key in keys:
                if look is None:
                    overrides.pop(key, None)
                else:
                    overrides[key] = look
            current_look["fixtures"] = overrides
            cfg["current_look"] = current_look
            validated = validate_config(cfg)
            save_config(self.config_path, validated)
            self.engine.set_look(validated["current_look"])
            return validated["current_look"]

    def set_schedule(self, schedule: list) -> list:
        with self._lock:
            cfg = dict(self.engine.cfg)
            cfg["schedule"] = schedule
            validated = validate_config(cfg)
            save_config(self.config_path, validated)
            self.engine.reload(validated)
            return validated["schedule"]

    def set_presets(self, presets: list) -> list:
        with self._lock:
            cfg = dict(self.engine.cfg)
            cfg["presets"] = presets
            validated = validate_config(cfg)
            save_config(self.config_path, validated)
            self.engine.reload(validated)
            return validated["presets"]

    def set_randomizer(self, randomizer: dict) -> dict:
        with self._lock:
            cfg = dict(self.engine.cfg)
            cfg["randomizer"] = randomizer
            validated = validate_config(cfg)
            save_config(self.config_path, validated)
            self.engine.reload(validated)
            return validated["randomizer"]

    def set_clock_chime(self, clock_chime: dict) -> dict:
        with self._lock:
            cfg = dict(self.engine.cfg)
            cfg["clock_chime"] = clock_chime
            validated = validate_config(cfg)
            save_config(self.config_path, validated)
            self.engine.reload(validated)
            return validated["clock_chime"]

    def set_layouts(self, layouts: list) -> list:
        """Full replace - used for create/rename/delete a layout and for
        adding/removing fixtures from one. Position tweaks from live
        dragging go through move_layout_fixtures instead, so a drag isn't
        a full-list round trip."""
        with self._lock:
            cfg = dict(self.engine.cfg)
            cfg["layouts"] = layouts
            validated = validate_config(cfg)
            save_config(self.config_path, validated)
            self.engine.reload(validated)
            return validated["layouts"]

    def move_layout_fixtures(self, layout_name: str, keys: list, dx: float, dy: float) -> list:
        """Rigid-translate a set of fixtures within one layout's own
        arrangement by (dx, dy) - the drag-to-rearrange gesture in the
        Live tab. Only this layout's stored points change; the fixture's
        real `points` (used by gradient/sweep) are untouched."""
        with self._lock:
            cfg = dict(self.engine.cfg)
            layouts = [dict(l) for l in cfg["layouts"]]
            for layout in layouts:
                if layout["name"] == layout_name:
                    fixtures = dict(layout.get("fixtures", {}))
                    for key in keys:
                        if key in fixtures:
                            fixtures[key] = [[p[0] + dx, p[1] + dy] for p in fixtures[key]]
                    layout["fixtures"] = fixtures
                    break
            else:
                raise ConfigError(f"no layout named {layout_name!r}")
            cfg["layouts"] = layouts
            validated = validate_config(cfg)
            save_config(self.config_path, validated)
            self.engine.reload(validated)
            return validated["layouts"]

    def save_layout_background(self, layout_name: str, image_bytes: bytes) -> list:
        """Store a reference image behind one layout's fixtures - purely a
        visual aid for dragging fixtures into a sensible arrangement, not
        read by the engine at all."""
        with self._lock:
            cfg = dict(self.engine.cfg)
            layouts = [dict(l) for l in cfg["layouts"]]
            for layout in layouts:
                if layout["name"] == layout_name:
                    filename = f"{_safe_filename(layout_name)}.jpg"
                    self.backgrounds_dir.mkdir(parents=True, exist_ok=True)
                    (self.backgrounds_dir / filename).write_bytes(image_bytes)
                    layout["background"] = filename
                    break
            else:
                raise ConfigError(f"no layout named {layout_name!r}")
            cfg["layouts"] = layouts
            validated = validate_config(cfg)
            save_config(self.config_path, validated)
            self.engine.reload(validated)
            return validated["layouts"]

    def clear_layout_background(self, layout_name: str) -> list:
        with self._lock:
            cfg = dict(self.engine.cfg)
            layouts = [dict(l) for l in cfg["layouts"]]
            for layout in layouts:
                if layout["name"] == layout_name:
                    old = layout.pop("background", None)
                    if old:
                        (self.backgrounds_dir / old).unlink(missing_ok=True)
                    break
            else:
                raise ConfigError(f"no layout named {layout_name!r}")
            cfg["layouts"] = layouts
            validated = validate_config(cfg)
            save_config(self.config_path, validated)
            self.engine.reload(validated)
            return validated["layouts"]

    def get_background_path(self, filename: str) -> Optional[Path]:
        path = (self.backgrounds_dir / filename).resolve()
        if self.backgrounds_dir.resolve() not in path.parents or not path.is_file():
            return None
        return path

    def clear_to_schedule(self) -> Dict[str, Any]:
        """The Live tab's "Clear" button: drop every manual override -
        the default look and every per-fixture selection look - and show
        whatever the schedule itself says should be playing right now,
        immediately. Same end state the background Scheduler would
        eventually converge to on its own, but staff don't have to wait
        for its next tick (or its 60s dark-revert safety net, which only
        ever fires when an override leaves something dark - this covers
        the general "undo whatever I was just testing" case instead).
        Reuses scheduler.active_entry so this always agrees with what the
        real scheduler would pick (priority entries included), rather
        than a second copy of that logic drifting out of sync over time."""
        with self._lock:
            cfg = dict(self.engine.cfg)
            now = datetime.datetime.now()
            schedule = cfg.get("schedule", [])
            default_entry = active_entry(schedule, None, now)
            default_look = copy.deepcopy(default_entry["look"]) if default_entry else off_look()

            overrides: Dict[str, Any] = {}
            for zone in {e.get("zone") for e in schedule if e.get("zone")}:
                entry = active_entry(schedule, zone, now)
                if not entry:
                    continue
                for fx in cfg["fixtures"]:
                    if fx.get("zone") == zone:
                        overrides[f"{fx['universe']}:{fx['address']}"] = copy.deepcopy(entry["look"])

            cfg["current_look"] = {"default": default_look, "fixtures": overrides}
            validated = validate_config(cfg)
            save_config(self.config_path, validated)
            self.engine.set_look(validated["current_look"])
            return validated["current_look"]

    def create_backup(self) -> bytes:
        """Everything that's irreplaceable about *this venue's* setup, in
        one zip - config.json (fixtures, zones, schedule, presets, current
        look, layouts) plus any layout background reference images. The
        application code isn't included: that's already safe in git, and
        bundling it here would conflate "this install's software version"
        with "this venue's data", which is the thing actually at risk of
        being lost for good if the Pi's SD card ever dies."""
        with self._lock:
            cfg = self.engine.cfg
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("config.json", json.dumps(cfg, indent=2))
                if self.backgrounds_dir.is_dir():
                    for path in sorted(self.backgrounds_dir.iterdir()):
                        if path.is_file():
                            zf.write(path, f"backgrounds/{path.name}")
                info = (
                    f"HOTA Gallery lighting - backup\n"
                    f"Device:    {cfg.get('device_name') or '(unnamed)'}\n"
                    f"Made:      {datetime.datetime.now().isoformat(timespec='seconds')}\n"
                    f"Fixtures:  {len(cfg['fixtures'])}\n"
                    f"Zones:     {len(cfg['zones'])}\n"
                    f"Schedule:  {len(cfg['schedule'])} entries\n"
                    f"Presets:   {len(cfg['presets'])}\n"
                    f"Layouts:   {len(cfg['layouts'])}\n"
                    f"\n"
                    f"To restore: Settings > Configuration file > Import config.json...\n"
                    f"on the controller, and pick config.json from this zip. The\n"
                    f"backgrounds/ images (if any) are only the layout editor's\n"
                    f"reference pictures, not anything the lights themselves read -\n"
                    f"put a file back at the same name under its data directory's\n"
                    f"backgrounds/ folder only if you need the layout editor's\n"
                    f"picture back too.\n"
                )
                zf.writestr("backup-info.txt", info)
            return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    store: Store  # set on the class by create_server before use

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> Any:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode())

    def log_message(self, fmt: str, *args: Any) -> None:  # quieter default logging
        log.info("%s - %s", self.address_string(), fmt % args)

    def _send_file(self, rel_path: str) -> None:
        file_path = (STATIC_DIR / rel_path).resolve()
        if STATIC_DIR not in file_path.parents or not file_path.is_file():
            self._send_json(404, {"error": "not found"})
            return
        body = file_path.read_bytes()
        content_type, _ = mimetypes.guess_type(str(file_path))
        self.send_response(200)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        # Always re-read from disk - same "edit the file on the Pi, no
        # build/deploy step" workflow as sacn2wiz's static files.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._send_file("index.html")
        elif self.path.startswith("/static/"):
            self._send_file(self.path[len("/static/") :])
        elif self.path == "/api/status":
            cfg = self.store.get_config()
            self._send_json(200, {
                "device_name": cfg["device_name"],
                "fixture_count": len(cfg["fixtures"]),
                "current_look": cfg["current_look"],
            })
        elif self.path == "/api/config":
            self._send_json(200, self.store.get_config())
        elif self.path == "/api/preview":
            self._send_json(200, self.store.engine.preview())
        elif self.path == "/api/schedule":
            self._send_json(200, self.store.get_config()["schedule"])
        elif self.path == "/api/presets":
            self._send_json(200, self.store.get_config()["presets"])
        elif self.path == "/api/randomizer":
            self._send_json(200, self.store.get_config()["randomizer"])
        elif self.path == "/api/clock_chime":
            self._send_json(200, self.store.get_config()["clock_chime"])
        elif self.path == "/api/layouts":
            self._send_json(200, self.store.get_config()["layouts"])
        elif self.path == "/api/backup":
            body = self.store.create_backup()
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            device = re.sub(r"[^A-Za-z0-9_-]+", "_", self.store.get_config().get("device_name") or "hota-gallery").strip("_") or "hota-gallery"
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition", f'attachment; filename="{device}-backup-{stamp}.zip"')
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/artnet/status":
            self._send_json(200, self.store.artnet_status())
        elif self.path == "/api/artnet/discover":
            try:
                self._send_json(200, self.store.discover_artnet_nodes())
            except OSError as exc:
                self._send_json(500, {"error": f"couldn't scan: {exc}"})
        elif self.path.startswith("/api/layouts/") and self.path.endswith("/background"):
            layout_name = unquote(self.path[len("/api/layouts/") : -len("/background")])
            layout = next((l for l in self.store.get_config()["layouts"] if l["name"] == layout_name), None)
            filename = layout.get("background") if layout else None
            file_path = self.store.get_background_path(filename) if filename else None
            if not file_path:
                self._send_json(404, {"error": "no background set"})
                return
            body = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        else:
            self._send_json(404, {"error": "not found"})

    def do_PUT(self) -> None:
        # Raw-bytes upload, not JSON - handled before the generic body read
        # below, which assumes (and would choke on non-UTF8) JSON.
        if self.path.startswith("/api/layouts/") and self.path.endswith("/background"):
            layout_name = unquote(self.path[len("/api/layouts/") : -len("/background")])
            length = int(self.headers.get("Content-Length", 0))
            try:
                if length == 0:
                    layouts = self.store.clear_layout_background(layout_name)
                else:
                    layouts = self.store.save_layout_background(layout_name, self.rfile.read(length))
                self._send_json(200, layouts)
            except ConfigError as exc:
                self._send_json(400, {"error": str(exc)})
            except Exception as exc:  # noqa: BLE001
                log.exception("unhandled error on PUT %s", self.path)
                self._send_json(500, {"error": str(exc)})
            return

        try:
            body = self._read_json()
        except (ValueError, UnicodeDecodeError) as exc:
            self._send_json(400, {"error": f"invalid JSON: {exc}"})
            return

        try:
            if self.path == "/api/config":
                validated = self.store.replace_config(body)
                self._send_json(200, validated)
            elif self.path == "/api/look":
                look = self.store.set_default_look(body)
                self._send_json(200, look)
            elif self.path == "/api/clear":
                look = self.store.clear_to_schedule()
                self._send_json(200, look)
            elif self.path == "/api/look/selection":
                look = self.store.set_fixture_looks(body["fixtures"], body.get("look"))
                self._send_json(200, look)
            elif self.path.startswith("/api/fixtures/") and self.path.endswith("/position"):
                key = self.path[len("/api/fixtures/") : -len("/position")]
                self.store.move_fixture(key, body["dx"], body["dy"])
                self._send_json(200, {"ok": True})
            elif self.path == "/api/schedule":
                schedule = self.store.set_schedule(body)
                self._send_json(200, schedule)
            elif self.path == "/api/presets":
                presets = self.store.set_presets(body)
                self._send_json(200, presets)
            elif self.path == "/api/randomizer":
                randomizer = self.store.set_randomizer(body)
                self._send_json(200, randomizer)
            elif self.path == "/api/clock_chime":
                clock_chime = self.store.set_clock_chime(body)
                self._send_json(200, clock_chime)
            elif self.path == "/api/layouts":
                layouts = self.store.set_layouts(body)
                self._send_json(200, layouts)
            elif self.path.startswith("/api/layouts/") and self.path.endswith("/move"):
                layout_name = unquote(self.path[len("/api/layouts/") : -len("/move")])
                layouts = self.store.move_layout_fixtures(layout_name, body["keys"], body["dx"], body["dy"])
                self._send_json(200, layouts)
            else:
                self._send_json(404, {"error": "not found"})
        except ConfigError as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - a 500 beats a dropped connection
            log.exception("unhandled error on PUT %s", self.path)
            self._send_json(500, {"error": str(exc)})


def create_server(engine: Engine, config_path: str, host: str = "0.0.0.0", port: int = 8080) -> ThreadingHTTPServer:
    store = Store(engine, config_path)
    Handler.store = store
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.store = store  # so cli.py can wire the scheduler to the same Store the API uses
    return httpd
