"""Background randomizer: during BAU hours, occasionally fires a burst
look (e.g. a chase) across a random target zone for a few seconds, then
reverts - entirely through the same per-fixture override mechanism a
manual canvas selection already uses (set_fixture_looks), so "reverting"
is just clearing that override and falling back to whatever the
schedule/default was already showing underneath, never a snapshot/
restore of state this module has to manage itself.

Timing is jittered around `3600 / events_per_hour` (half to one-and-a-half
of the average interval, redrawn after every fire) rather than a fixed
clock or a small rotation of preset intervals - genuine randomness has no
period for an attentive viewer to ever lock onto, which a handful of
discrete intervals eventually would given enough time.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("hota_gallery")


class Randomizer:
    def __init__(
        self,
        get_config: Callable[[], Dict[str, Any]],
        set_fixture_looks: Callable[[List[str], Optional[Dict[str, Any]]], Any],
        check_period: float = 5.0,
    ):
        self._get_config = get_config
        self._set_fixture_looks = set_fixture_looks
        self._check_period = check_period
        self._stop = threading.Event()
        self._next_fire: Optional[float] = None  # time.monotonic() seconds

    @staticmethod
    def _zone_fixture_keys(cfg: Dict[str, Any], zone: str) -> List[str]:
        return [f"{fx['universe']}:{fx['address']}" for fx in cfg["fixtures"] if fx.get("zone") == zone]

    def _schedule_next(self, rc: Dict[str, Any]) -> None:
        rate = rc.get("events_per_hour") or 20.0
        avg_interval = 3600.0 / rate
        self._next_fire = time.monotonic() + avg_interval * random.uniform(0.5, 1.5)

    def _fire(self, cfg: Dict[str, Any], rc: Dict[str, Any]) -> None:
        targets = rc.get("targets") or []
        preset_slots = rc.get("preset_slots") or []
        if not targets or not preset_slots:
            return
        # The burst pool IS the presets pool (or a subset of it) - looked up
        # fresh each fire, so editing/renaming/deleting a preset on the Live
        # tab is immediately reflected here without touching this config.
        looks_by_slot = {p["slot"]: p["look"] for p in cfg.get("presets", [])}
        available = [s for s in preset_slots if s in looks_by_slot]
        if not available:
            return
        zone = random.choice(targets)
        look = looks_by_slot[random.choice(available)]
        keys = self._zone_fixture_keys(cfg, zone)
        if not keys:
            return
        burst_s = rc.get("burst_s") or 6.0
        colour_mode = (look.get("colour") or {}).get("mode")
        effect_mode = (look.get("effect") or {}).get("mode")
        video_mode = (look.get("video") or {}).get("mode")
        log.info(
            "randomizer: firing colour=%r effect=%r video=%r on zone %r (%d fixtures) for %.1fs",
            colour_mode, effect_mode, video_mode, zone, len(keys), burst_s,
        )
        self._set_fixture_looks(keys, look)
        # Blocks this dedicated thread only - the engine/scheduler/API all
        # keep running normally for the burst's duration.
        if self._stop.wait(burst_s):
            return  # shutting down - leave the burst as-is, nothing left to revert for
        self._set_fixture_looks(keys, None)

    def _tick(self) -> None:
        cfg = self._get_config()
        rc = cfg.get("randomizer") or {}
        if not rc.get("enabled"):
            self._next_fire = None
            return
        if self._next_fire is None:
            self._schedule_next(rc)
            return
        if time.monotonic() >= self._next_fire:
            self._fire(cfg, rc)
            # Re-fetch: enabled/rate may have changed while the burst ran.
            rc = (self._get_config().get("randomizer")) or {}
            if rc.get("enabled"):
                self._schedule_next(rc)
            else:
                self._next_fire = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                log.exception("randomizer tick failed")
            self._stop.wait(self._check_period)

    def start_background(self) -> threading.Thread:
        t = threading.Thread(target=self._loop, name="randomizer", daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self._stop.set()
