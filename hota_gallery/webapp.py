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
import random
import re
import subprocess
import threading
import time
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import urllib.error
from urllib.parse import quote, unquote

from . import battle as battle_mod, crowd, netconfig, paste
from .artnet import ARTNET_PORT, discover_nodes
from .sacn import SACN_PORT, multicast_group
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
            channels_per_led = fx.get("channels_per_led", {"RGBW": 4, "DRGBW": 5, "RGBWD": 5}.get(fx["led_type"], 3))
            entry["fixture_count"] += 1
            entry["channel_count"] += fx["led_count"] * channels_per_led
            entry["max_address"] = max(entry["max_address"], fx["address"] + fx["led_count"] * channels_per_led)

        for entry in by_universe.values():
            entry["sacn_group"] = multicast_group(entry["universe"] + 1)

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
            # Outdoor stage: sACN instead of Art-Net. Wire universe is the
            # stored one + 1 (see sacn.py), the same number the UI shows.
            "protocol": cfg.get("protocol", "artnet"),
            "sacn_port": SACN_PORT,
            "sacn_priority": cfg.get("sacn_priority", 100),
            "sacn_destinations": cfg.get("sacn_destinations", []),
            "web_port": cfg["web_port"],
            "fps": cfg["fps"],
            "universes": sorted(by_universe.values(), key=lambda e: e["universe"]),
        }

    def discover_artnet_nodes(self) -> List[Dict[str, Any]]:
        cfg = self.engine.cfg
        bind_ip = cfg.get("bind_ip") or Engine._auto_bind_ip()
        return discover_nodes(bind_ip, timeout=3.0)

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

    def set_current_look(self, current_look: Dict[str, Any]) -> Dict[str, Any]:
        """Replace the whole current_look (default + every per-fixture
        override) in one shot - the Live tab's "Go live" button, sending a
        blind-programmed look across in a single atomic write instead of
        one putLook plus one putSelection per distinct override look."""
        with self._lock:
            cfg = dict(self.engine.cfg)
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

    def dmx(self, universe: int) -> Dict[str, Any]:
        """One universe as it's being sent (overrides included), what each
        channel is patched to, and which channels are overridden."""
        frames = self.engine.build_frames()
        cfg = self.engine.cfg
        labels: Dict[int, str] = {}
        attrs = {"RGB": "RGB", "RGBW": "RGBW", "DRGBW": ["Dim", "R", "G", "B", "W"], "RGBWD": ["R", "G", "B", "W", "Dim"]}
        for fx in cfg["fixtures"]:
            if fx["universe"] != universe:
                continue
            cpl = fx.get("channels_per_led", {"RGBW": 4, "DRGBW": 5, "RGBWD": 5}.get(fx["led_type"], 3))
            names = list(attrs.get(fx["led_type"], "RGB"))
            for led in range(fx["led_count"]):
                for k in range(cpl):
                    ch = fx["address"] + led * cpl + k
                    pixel = f" px{led + 1}" if fx["led_count"] > 1 else ""
                    labels[ch] = f"{fx['name']}{pixel} · {names[k] if k < len(names) else k + 1}"
        with self.engine._lock:
            over = dict(self.engine.overrides.get(universe, {}))
        patched = sorted({fx["universe"] for fx in cfg["fixtures"]} | {u for u, chs in self.engine.overrides.items() if chs})
        return {
            "universe": universe,
            "values": list(frames.get(universe, bytes(512))),
            "overrides": {str(ch): v for ch, v in over.items()},
            "labels": {str(ch): t for ch, t in labels.items()},
            "universes": patched,
            "override_count": sum(len(c) for c in self.engine.overrides.values()),
        }

    def set_dmx(self, universe: int, body: dict) -> Dict[str, Any]:
        """{"set": {channel: value}} and/or {"release": [channels]} or
        {"release_all": true} (every universe). Channels 0-indexed."""
        if not 0 <= universe <= 63998:
            raise ConfigError(f"universe {universe} is out of range")
        if body.get("release_all"):
            self.engine.release_overrides()
        if body.get("release"):
            self.engine.release_overrides(universe, [int(c) for c in body["release"]])
        values = {}
        for ch, v in (body.get("set") or {}).items():
            ch, v = int(ch), int(v)
            if not 0 <= ch <= 511 or not 0 <= v <= 255:
                raise ConfigError(f"channel {ch + 1} = {v}: channels are 1-512 and values 0-255")
            values[ch] = v
        if values:
            self.engine.set_overrides(universe, values)
        return self.dmx(universe)

    # -- light battle ---------------------------------------------------------

    VENUE_NAMES = {"gallery": "Gallery", "vos": "Outdoor stage", "building": "HOTA Central"}
    PRECINCT = "Precinct (all)"  # the venue dropdown's whole-precinct page

    def battle_status(self) -> Optional[Dict[str, Any]]:
        b = self.engine.battle
        if b is None or b.over():
            return None
        return b.status()

    def join_battle(self, body: dict) -> Dict[str, Any]:
        """Play our part in a match another controller (or we) worked out:
        {"match": ..., "me": index, "starts_in": seconds}."""
        match, me = body.get("match"), body.get("me")
        if not isinstance(match, dict) or not isinstance(me, int) or not 0 <= me < len(match.get("players", [])):
            raise ConfigError("not a light battle")
        if body.get("auto") and not (self.engine.cfg.get("battle_auto") or {}).get("enabled"):
            raise ConfigError("the nightly light battle is switched off here")
        starts_in = float(body.get("starts_in", 0))
        if not 0 <= starts_in <= 30 or not 0 < float(match.get("duration", 0)) <= 600:
            raise ConfigError("light battle timing out of range")
        self.engine.battle = battle_mod.Battle(match, me, time.time() + starts_in)
        self._arm_record(self.engine.battle)
        log.info("light battle: playing %s, starts in %.1f s", match["players"][me]["name"], starts_in)
        return self.engine.battle.status()

    def _arm_record(self, b: Any) -> None:
        """Score a ping-pong match once its finale starts - unless it was
        stopped, so only matches played to the end count. Each player
        records the same script, so their scoreboards agree."""
        if b.match.get("style", "pingpong") != "pingpong":
            return
        delay = b.start + b.match["finale"]["t0"] + 0.5 - time.time()

        def record() -> None:
            if getattr(b, "stopped", False) or self.engine.battle is not b:
                return
            names = [p["name"] for p in b.match["players"]]
            winner = names[b.match["finale"]["winner"]]
            key = " vs ".join(sorted(names))
            with self._lock:
                cfg = dict(self.engine.cfg)
                rec = copy.deepcopy(cfg.get("battle_record") or {})
                tally = rec.setdefault(key, {})
                for n in names:
                    tally.setdefault(n, 0)
                tally[winner] += 1
                cfg["battle_record"] = rec
                validated = validate_config(cfg)
                save_config(self.config_path, validated)
                self.engine.reload(validated)
            log.info("light battle: %s won (%s: %s)", winner, key, tally)

        t = threading.Timer(max(0.0, delay), record)
        t.daemon = True
        t.start()

    def crowd_status(self) -> Optional[Dict[str, Any]]:
        b = self.engine.battle
        return b.status() if isinstance(b, crowd.CrowdBattle) and not b.over() else None

    def start_crowd(self, body: dict) -> Dict[str, Any]:
        """Start an audience-phone tug of war on this controller's lights."""
        colours = {s: tuple(int(v) for v in body["colours"][s]) for s in crowd.SIDES if s in (body.get("colours") or {})}
        limit = float(body.get("time_limit", crowd.TIME_LIMIT_S))
        if not 20 <= limit <= 600:
            raise ConfigError("time limit must be 20-600 seconds")
        self.engine.battle = crowd.CrowdBattle(colours, limit)
        log.info("crowd battle started")
        return self.engine.battle.status()

    def crowd_tap(self, body: dict, client_ip: str) -> Dict[str, Any]:
        b = self.engine.battle
        if not isinstance(b, crowd.CrowdBattle):
            return {"ok": False}
        phone = f"{client_ip}/{str(body.get('phone', ''))[:40]}"
        return {"ok": b.tap(phone, str(body.get("side")), int(body.get("count", 1)))}

    def stop_battle(self, everywhere: bool) -> None:
        b = self.engine.battle
        self.engine.battle = None
        if b is not None:
            b.stopped = True  # a stopped match doesn't go on the scoreboard
        if everywhere and b is not None and hasattr(b, "match"):
            for p in b.match["players"]:
                if p.get("url"):
                    try:
                        urllib.request.urlopen(urllib.request.Request(p["url"].rstrip("/") + "/api/battle", method="DELETE"), timeout=2).close()
                    except Exception:  # noqa: BLE001 - it ends by itself anyway
                        pass

    def start_battle(self, body: dict) -> Dict[str, Any]:
        """Host a match: {"players": [peer names, "this" for us], "points_to_win": n}.
        Works the whole match out, sends each other player its part, then
        joins itself. If a player can't be reached, nobody starts."""
        cfg = self.engine.cfg
        me_name = self.VENUE_NAMES.get(cfg.get("venue"), cfg.get("device_name") or "This controller")
        peers = {p["name"]: p for p in self.peers_status()}
        roster = []
        for name in body.get("players") or []:
            if name == "this":
                roster.append({"name": me_name, "venue": cfg.get("venue"), "url": None})
            elif name in peers:
                p = peers[name]
                if not p.get("online"):
                    raise ConfigError(f"{name} isn't answering, so it can't play")
                roster.append({"name": name, "venue": p.get("venue"), "url": p["url"]})
            else:
                raise ConfigError(f"{name!r} isn't one of the other controllers")
        if len(roster) < 2:
            raise ConfigError("pick at least two players")
        players = battle_mod.colours_for(roster)
        for p, r in zip(players, roster):
            p["url"] = r["url"]
        try:
            match = battle_mod.make(body.get("style", "pingpong"), players, int(body.get("points_to_win", 3)))
        except ValueError as exc:
            raise ConfigError(str(exc))
        lead = 2.5  # seconds for the script to reach everyone
        t0 = time.time()
        joined, failed = [], []
        for i, p in enumerate(players):
            if not p["url"]:
                continue
            payload = json.dumps({"match": match, "me": i, "starts_in": max(0.5, lead - (time.time() - t0)), "auto": bool(body.get("auto"))}).encode()
            try:
                urllib.request.urlopen(urllib.request.Request(p["url"].rstrip("/") + "/api/battle", payload,
                                                              {"Content-Type": "application/json"}, method="PUT"), timeout=3).close()
                joined.append(p)
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{p['name']} ({getattr(exc, 'reason', exc)})")
        if failed:
            for p in joined:
                try:
                    urllib.request.urlopen(urllib.request.Request(p["url"].rstrip("/") + "/api/battle", method="DELETE"), timeout=2).close()
                except Exception:  # noqa: BLE001
                    pass
            raise ConfigError("couldn't start the battle - " + ", ".join(failed) + " didn't join (it may need updating)")
        me = next(i for i, p in enumerate(players) if not p["url"])
        self.engine.battle = battle_mod.Battle(match, me, t0 + lead)
        self._arm_record(self.engine.battle)
        return self.engine.battle.status()

    def my_name(self) -> str:
        cfg = self.engine.cfg
        return self.VENUE_NAMES.get(cfg.get("venue"), cfg.get("device_name") or "This controller")

    def set_battle_auto(self, body: dict, propagate: bool = False) -> Dict[str, Any]:
        """Change the nightly easter egg. propagate=True (the Settings
        switch) also sets the opponent the same way, pointed back at us, so
        one switch turns it on or off for both."""
        with self._lock:
            cfg = dict(self.engine.cfg)
            cfg["battle_auto"] = {**(cfg.get("battle_auto") or {}), **{k: v for k, v in body.items() if k in ("enabled", "opponent", "time", "points_to_win")}}
            validated = validate_config(cfg)
            save_config(self.config_path, validated)
            self.engine.reload(validated)
            ba = validated["battle_auto"]
        if propagate and ba.get("opponent"):
            peer = next((p for p in self.engine.cfg.get("peers", []) if p["name"] == ba["opponent"]), None)
            if peer:
                mirror = {"enabled": ba["enabled"], "opponent": self.my_name(), "time": ba["time"], "points_to_win": ba["points_to_win"]}
                try:
                    urllib.request.urlopen(urllib.request.Request(peer["url"].rstrip("/") + "/api/battle/auto", json.dumps(mirror).encode(),
                                                                  {"Content-Type": "application/json"}, method="PUT"), timeout=3).close()
                except Exception as exc:  # noqa: BLE001
                    raise ConfigError(f"saved here, but {peer['name']} didn't take the change ({getattr(exc, 'reason', exc)}) - set it there too")
        return ba

    _auto_fired: Optional[str] = None  # date the nightly match was last tried

    def auto_battle_tick(self) -> None:
        """Called every few seconds by cli.py: at battle_auto.time, serve the
        nightly ping-pong match. Both sides run this; the one whose name
        sorts first serves on the dot, the other 20 s later and only if no
        match started (its partner was off or offline)."""
        ba = self.engine.cfg.get("battle_auto") or {}
        if not ba.get("enabled") or not ba.get("opponent"):
            return
        now = datetime.datetime.now()
        hh, mm = (int(x) for x in ba.get("time", "19:30").split(":"))
        due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        today = now.date().isoformat()
        late = (now - due).total_seconds()
        if self._auto_fired == today or not 0 <= late < 120:
            return
        first = self.my_name() < ba["opponent"]
        if not first and late < 20:
            return
        self._auto_fired = today
        if self.engine.battle is not None:
            return
        peer = next((p for p in self.peers_status() if p["name"] == ba["opponent"]), None)
        if not peer or not peer.get("online") or peer.get("battle"):
            log.info("nightly light battle: %s isn't free, skipped", ba["opponent"])
            return
        try:
            self.start_battle({"players": ["this", ba["opponent"]], "points_to_win": ba.get("points_to_win", 3), "auto": True})
            log.info("nightly light battle: serving against %s", ba["opponent"])
        except ConfigError as exc:
            log.info("nightly light battle skipped: %s", exc)

    def peers_status(self) -> List[Dict[str, Any]]:
        """Each configured peer controller with its /api/status, asked by
        this controller (no browser cross-origin rules, and the same answer
        on the kiosk as on a phone). All at once, 2 s timeout each."""
        peers = list(self.engine.cfg.get("peers") or [])
        out: List[Dict[str, Any]] = [dict(p, online=False) for p in peers]

        def probe(i: int) -> None:
            url = peers[i]["url"].rstrip("/") + "/api/status"
            try:
                with urllib.request.urlopen(url, timeout=2) as r:
                    st = json.loads(r.read().decode())
                out[i].update(online=True, device_name=st.get("device_name"), fixture_count=st.get("fixture_count"),
                              venue=st.get("venue"), protocol=st.get("protocol"), output_live=st.get("output_live"),
                              battle=st.get("battle"), battle_capable=bool(st.get("battle_capable")))
            except Exception as exc:  # noqa: BLE001 - offline, wrong address, not a controller
                out[i]["error"] = str(getattr(exc, "reason", exc))

        threads = [threading.Thread(target=probe, args=(i,), daemon=True) for i in range(len(peers))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(3)
        return out

    def set_peers(self, peers: list) -> List[Dict[str, Any]]:
        with self._lock:
            cfg = dict(self.engine.cfg)
            cfg["peers"] = [{"name": str(p.get("name", "")).strip(), "url": str(p.get("url", "")).strip()} for p in peers]
            validated = validate_config(cfg)
            save_config(self.config_path, validated)
            self.engine.reload(validated)
        return self.peers_status()

    # -- the whole precinct: this controller and its peers, from one page ----
    #
    # The Precinct page (static/precinct.html) reads every venue's config and
    # sets looks or event-day schedule entries on all of them at once. Done
    # here, controller to controller, like peers_status: no cross-origin
    # rules in the browser, and one answer wherever the page is opened.

    def _venues(self, names: Optional[list] = None) -> List[Dict[str, Any]]:
        """Me ({"url": None}) and every peer, optionally only those named."""
        out = [{"name": self.my_name(), "url": None}] + [dict(p) for p in self.engine.cfg.get("peers") or []]
        return [v for v in out if names is None or v["name"] in names]

    @staticmethod
    def _peer_call(url: str, method: str, path: str, body: Any = None, timeout: float = 5) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url.rstrip("/") + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode() or "null")
        except urllib.error.HTTPError as exc:
            try:
                msg = json.loads(exc.read().decode()).get("error")
            except Exception:  # noqa: BLE001
                msg = None
            raise ConfigError(msg or f"HTTP {exc.code}")

    def _each_venue(self, names: Optional[list], fn) -> List[Dict[str, Any]]:
        """Run fn(venue) for each venue in parallel; [{name, ok, result|error}]."""
        venues = self._venues(names)
        out: List[Dict[str, Any]] = [{"name": v["name"], "ok": False} for v in venues]

        def run(i: int) -> None:
            try:
                out[i].update(ok=True, result=fn(venues[i]))
            except Exception as exc:  # noqa: BLE001 - offline, refused, invalid
                out[i]["error"] = str(getattr(exc, "reason", exc))

        threads = [threading.Thread(target=run, args=(i,), daemon=True) for i in range(len(venues))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(8)
        return out

    PRECINCT_KEYS = ("venue", "device_name", "fixtures", "layouts", "current_look", "zones", "presets")

    def precinct_state(self) -> List[Dict[str, Any]]:
        """Every venue's config (just what drawing and exporting need)."""
        def read(v: Dict[str, Any]) -> Dict[str, Any]:
            cfg = self.get_config() if v["url"] is None else self._peer_call(v["url"], "GET", "/api/config")
            return {k: cfg.get(k) for k in self.PRECINCT_KEYS}
        return self._each_venue(None, read)

    def precinct_look(self, body: dict) -> List[Dict[str, Any]]:
        """Put one look live everywhere (or on body["venues"]): it replaces
        each venue's whole current look, per-fixture looks included."""
        look = body.get("look")
        if not isinstance(look, dict):
            raise ConfigError("look is required")
        full = {"default": look, "fixtures": {}}

        def put(v: Dict[str, Any]) -> Any:
            if v["url"] is None:
                return self.set_current_look(full)
            return self._peer_call(v["url"], "PUT", "/api/look/full", full)
        return self._each_venue(body.get("venues"), put)

    def precinct_clear(self, body: dict) -> List[Dict[str, Any]]:
        """Every venue (or body["venues"]) back to what its schedule says."""
        def clear(v: Dict[str, Any]) -> Any:
            if v["url"] is None:
                return self.clear_to_schedule()
            return self._peer_call(v["url"], "PUT", "/api/clear", {})
        return self._each_venue(body.get("venues"), clear)

    def precinct_schedule(self, body: dict) -> List[Dict[str, Any]]:
        """Add one schedule entry to every venue (or body["venues"]) - an
        event day, as a priority entry that removes itself once its date has
        passed (see config.py on priority entries)."""
        entry = body.get("entry")
        if not isinstance(entry, dict):
            raise ConfigError("entry is required")

        def add(v: Dict[str, Any]) -> Any:
            if v["url"] is None:
                return len(self.set_schedule(list(self.get_config().get("schedule") or []) + [entry]))
            cfg = self._peer_call(v["url"], "GET", "/api/config")
            return len(self._peer_call(v["url"], "PUT", "/api/schedule", list(cfg.get("schedule") or []) + [entry]))
        return self._each_venue(body.get("venues"), add)

    def lighting_network(self) -> Dict[str, Any]:
        """Wired/Wi-Fi ports and the lighting network profile, for Settings."""
        return {
            "available": netconfig.available(),
            "interfaces": netconfig.interfaces(),
            "profile": netconfig.current(),
            "bind_ip": self.engine.cfg.get("bind_ip"),
        }

    def set_lighting_network(self, body: dict, via_ip: str) -> Dict[str, Any]:
        """Give a wired port a fixed address (netconfig.apply) and send the
        lighting output from it. via_ip is the address this request came in
        on: the port carrying it is refused, so nobody cuts themselves off."""
        interface, address, prefix = body.get("interface"), body.get("address"), body.get("prefix")
        try:
            netconfig.validate(interface, address, prefix)
            for i in netconfig.interfaces():
                if i["name"] == interface and any(a.split("/")[0] == via_ip for a in i["addresses"]):
                    raise netconfig.NetConfigError(f"you're connected to the controller through {interface} - pick the lighting port, not this one")
            result = netconfig.apply(interface, address, int(prefix))
        except netconfig.NetConfigError as exc:
            raise ConfigError(str(exc))
        with self._lock:
            cfg = dict(self.engine.cfg)
            cfg["bind_ip"] = result["address"]
            validated = validate_config(cfg)
            save_config(self.config_path, validated)
            self.engine.reload(validated)  # the sender reopens on the new address
        return {**self.lighting_network(), "result": result}

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

    # -- venue switcher: one address for the whole precinct -------------------
    #
    # A browser can be pointed at another controller (a peer) with the
    # hota_venue cookie, set by GET /venue/<name> from the header dropdown.
    # Every request it then makes - the page, the static files, the API - is
    # relayed to that peer and the answer passed back, so one address (the
    # one controller you give out) shows any venue. Only configured peers
    # can be relayed to. The cookie lasts for the browser session, so a
    # kiosk that restarts always comes back to its own building.

    def _venue_target(self) -> Optional[Tuple[str, str]]:
        m = re.search(r"(?:^|;\s*)hota_venue=([^;]*)", self.headers.get("Cookie") or "")
        if not m:
            return None
        name = unquote(m.group(1))
        peer = next((p for p in self.store.engine.cfg.get("peers", []) if p["name"] == name), None)
        return (name, peer["url"].rstrip("/")) if peer else None

    def _venue_switch(self) -> bool:
        if not self.path.startswith("/venue/"):
            return False
        name = unquote(self.path[len("/venue/"):].split("?")[0])
        self.send_response(303)
        if name == Store.PRECINCT or any(p["name"] == name for p in self.store.engine.cfg.get("peers", [])):
            self.send_header("Set-Cookie", f"hota_venue={quote(name)}; Path=/; SameSite=Lax")
        else:  # our own name (or anything unknown): back to this controller
            self.send_header("Set-Cookie", "hota_venue=; Path=/; Max-Age=0; SameSite=Lax")
        self.send_header("Location", "/")
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True

    def _precinct(self) -> bool:
        """This browser picked "Precinct (all)": / is the precinct page."""
        m = re.search(r"(?:^|;\s*)hota_venue=([^;]*)", self.headers.get("Cookie") or "")
        return bool(m) and unquote(m.group(1)) == Store.PRECINCT

    def _relay(self, method: str) -> bool:
        target = self._venue_target()
        if not target:
            return False
        name, base = target
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items() if k.lower() in ("content-type", "accept")}
        try:
            with urllib.request.urlopen(urllib.request.Request(base + self.path, data=body, headers=headers, method=method), timeout=15) as r:
                status, rh, data = r.status, r.headers, r.read()
        except urllib.error.HTTPError as exc:
            status, rh, data = exc.code, exc.headers, exc.read()
        except Exception as exc:  # noqa: BLE001 - the venue's controller isn't answering
            reason = getattr(exc, "reason", exc)
            if self.path.startswith("/api/"):
                self._send_json(502, {"error": f"{name} isn't answering ({reason}). Switch venue from the dropdown."})
            else:
                page = (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'><title>{name} offline</title>"
                        f"<body style='background:#0f0e0e;color:#f5f5f5;font:16px system-ui;padding:40px'><h2>{name} isn't answering</h2>"
                        f"<p>Its controller didn't reply ({reason}).</p><p><a style='color:#f0336c' href='/venue/{quote(self.store.my_name())}'>"
                        f"Back to {self.store.my_name()}</a></p>").encode()
                self.send_response(502)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)
            return True
        if "text/html" in (rh.get("Content-Type") or "") and b'id="venueSel"' not in data and b"</body>" in data:
            # That venue's controller is on an older version with no venue
            # dropdown: add a small one, so nobody gets stuck there.
            names = [self.store.my_name()] + [p["name"] for p in self.store.engine.cfg.get("peers", [])] + [Store.PRECINCT]
            opts = "".join(f'<option value="{quote(n)}"{" selected" if n == name else ""}>{n}</option>' for n in names)
            bar = ('<div id="hotaVenueBar" style="position:fixed;right:12px;bottom:12px;z-index:99999;background:#1c1a1a;border:1px solid #484545;'
                   'border-radius:999px;padding:6px 10px;font:13px system-ui;color:#f5f5f5">Venue '
                   '<select onchange="location.href=\'/venue/\'+this.value" style="background:#282727;color:#f5f5f5;border:0;'
                   f'border-radius:999px;padding:3px 8px;font:inherit">{opts}</select></div>').encode()
            data = data.replace(b"</body>", bar + b"</body>", 1)
        self.send_response(status)
        for k in ("Content-Type", "Cache-Control", "Content-Disposition"):
            if rh.get(k):
                self.send_header(k, rh[k])
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        return True

    def do_GET(self) -> None:
        if self._venue_switch() or self._relay("GET"):
            return
        if paste.handle(self, "GET"):  # /paste - shared scratchpad, see paste.py
            return
        if self.path.split("?")[0] in ("/crowd", "/crowd/"):  # audience phones - crowd.py
            body = crowd.PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path in ("/", "/index.html"):
            self._send_file("precinct.html" if self._precinct() else "index.html")
        elif self.path.startswith("/static/"):
            self._send_file(self.path[len("/static/") :])
        elif self.path == "/api/status":
            cfg = self.store.get_config()
            self._send_json(200, {
                "device_name": cfg["device_name"],
                "fixture_count": len(cfg["fixtures"]),
                "current_look": cfg["current_look"],
                # For other controllers' "Other controllers" card.
                "venue": cfg.get("venue", "gallery"),
                "venue_name": self.store.my_name(),
                "protocol": cfg.get("protocol", "artnet"),
                "output_live": cfg.get("bind_ip") != "127.0.0.1" and cfg.get("output_enabled", True),
                "output_enabled": cfg.get("output_enabled", True),
                "overrides": sum(len(c) for c in self.store.engine.overrides.values()),
                "battle": self.store.battle_status(),
                "battle_capable": True,
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
        elif self.path == "/api/network":
            self._send_json(200, self.store.lighting_network())
        elif self.path == "/api/peers":
            self._send_json(200, self.store.peers_status())
        elif self.path == "/api/precinct":
            self._send_json(200, self.store.precinct_state())
        elif self.path == "/api/battle":
            self._send_json(200, self.store.battle_status())
        elif self.path == "/api/battle/record":
            cfg = self.store.get_config()
            self._send_json(200, {"record": cfg.get("battle_record", {}), "auto": cfg.get("battle_auto", {})})
        elif self.path == "/api/crowd":
            self._send_json(200, self.store.crowd_status())
        elif re.fullmatch(r"/api/dmx/\d+", self.path):
            self._send_json(200, self.store.dmx(int(self.path.rsplit("/", 1)[1])))
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
        if self._relay("PUT"):
            return
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
            elif self.path == "/api/look/full":
                look = self.store.set_current_look(body)
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
            elif self.path == "/api/network":
                self._send_json(200, self.store.set_lighting_network(body, self.connection.getsockname()[0]))
            elif self.path == "/api/peers":
                self._send_json(200, self.store.set_peers(body))
            elif self.path == "/api/precinct/look":
                self._send_json(200, self.store.precinct_look(body))
            elif self.path == "/api/precinct/clear":
                self._send_json(200, self.store.precinct_clear(body))
            elif self.path == "/api/precinct/schedule":
                self._send_json(200, self.store.precinct_schedule(body))
            elif self.path.split("?")[0] == "/api/battle/auto":
                # From the Settings switch (?both=1): set the opponent too.
                self._send_json(200, self.store.set_battle_auto(body, propagate="both=1" in self.path))
            elif self.path == "/api/crowd/start":
                self._send_json(200, self.store.start_crowd(body))
            elif self.path == "/api/battle/start":
                self._send_json(200, self.store.start_battle(body))
            elif self.path == "/api/battle":
                self._send_json(200, self.store.join_battle(body))
            elif re.fullmatch(r"/api/dmx/\d+", self.path):
                self._send_json(200, self.store.set_dmx(int(self.path.rsplit("/", 1)[1]), body))
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


    def do_POST(self) -> None:
        if self._relay("POST"):
            return
        if self.path == "/api/crowd/tap":
            try:
                self._send_json(200, self.store.crowd_tap(self._read_json(), self.client_address[0]))
            except (ValueError, UnicodeDecodeError):
                self._send_json(400, {"error": "bad tap"})
        elif not paste.handle(self, "POST"):
            self._send_json(404, {"error": "not found"})

    def do_DELETE(self) -> None:
        if self._relay("DELETE"):
            return
        if self.path.split("?")[0] == "/api/battle":
            # ?all=1 from the Stop button stops every player, not just us.
            self.store.stop_battle(everywhere="all=1" in self.path)
            self._send_json(200, None)
        elif not paste.handle(self, "DELETE"):
            self._send_json(404, {"error": "not found"})


def create_server(engine: Engine, config_path: str, host: str = "0.0.0.0", port: int = 8080) -> ThreadingHTTPServer:
    store = Store(engine, config_path)
    Handler.store = store
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.store = store  # so cli.py can wire the scheduler to the same Store the API uses
    return httpd
