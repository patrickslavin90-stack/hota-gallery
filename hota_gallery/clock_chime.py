"""Background clock chime: a big, deliberate show moment at the top of
every hour, no schedule entries needed. While enabled and the current
hour falls within [start_hour, end_hour], it fires the configured
"program" (a single <look>, typically copied from a saved preset) across
every fixture for duration_s, then reverts - same per-fixture override
mechanism the randomizer uses, so reverting is just clearing the
override and falling back to whatever the schedule/default was already
showing, never a snapshot this module has to manage itself.

Whole-layout only (unlike the randomizer, which targets one zone) - this
is meant to read as a single unified event across the whole facade, not
an ambient accent on part of it.
"""

from __future__ import annotations

import datetime
import logging
import threading
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("hota_gallery")


class ClockChime:
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
        # "YYYY-MM-DD HH" of the last hour this actually fired for - so a
        # 5-second check period doesn't fire the same hour mark 12 times
        # over, and a restart mid-hour doesn't immediately re-fire either
        # (only a *new* hour value triggers it).
        self._last_fired_hour: Optional[str] = None

    @staticmethod
    def _all_fixture_keys(cfg: Dict[str, Any]) -> List[str]:
        return [f"{fx['universe']}:{fx['address']}" for fx in cfg["fixtures"]]

    def _tick(self) -> None:
        cfg = self._get_config()
        cc = cfg.get("clock_chime") or {}
        if not cc.get("enabled"):
            return
        now = datetime.datetime.now()
        if not (cc.get("start_hour", 0) <= now.hour <= cc.get("end_hour", 23)):
            return
        if now.minute != 0:
            return
        hour_mark = now.strftime("%Y-%m-%d %H")
        if hour_mark == self._last_fired_hour:
            return
        self._last_fired_hour = hour_mark

        keys = self._all_fixture_keys(cfg)
        if not keys:
            return
        look = cc.get("look") or {"colour": {"mode": "off"}, "effect": {"mode": "off"}, "video": {"mode": "off"}}
        duration_s = cc.get("duration_s") or 60.0
        colour_mode = (look.get("colour") or {}).get("mode")
        effect_mode = (look.get("effect") or {}).get("mode")
        video_mode = (look.get("video") or {}).get("mode")
        log.info(
            "clock chime: firing colour=%r effect=%r video=%r across %d fixtures for %.0fs",
            colour_mode, effect_mode, video_mode, len(keys), duration_s,
        )
        self._set_fixture_looks(keys, look)
        # Blocks this dedicated thread only, same as the randomizer.
        if self._stop.wait(duration_s):
            return  # shutting down - leave it as-is
        self._set_fixture_looks(keys, None)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                log.exception("clock chime tick failed")
            self._stop.wait(self._check_period)

    def start_background(self) -> threading.Thread:
        t = threading.Thread(target=self._loop, name="clock-chime", daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self._stop.set()
