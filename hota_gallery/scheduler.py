"""Background scheduler: applies a schedule entry's look through the same
Store path the API uses - current_look.default for a whole-layout entry,
or current_look.fixtures (via set_fixture_looks) for a zone-targeted one -
so a scheduled change is persisted and visible exactly like a manual one.

Each *target* (the whole layout, or one named zone) is scheduled
independently: its own last-match-wins evaluation, its own "did this
already get applied" tracking. That's what lets the facade and the dishes
run two unrelated schedules side by side, the same way ELM's two separate
stages did, without one needing to know about the other.

A target's own active entry is deliberately only (re)applied when it
*changes* - a manual override made during an entry's own active window
isn't immediately stomped back on the next tick, so staff can dial in a
one-off look without the scheduler fighting them. The flip side is a
safety net: if that override leaves the target genuinely dark while the
schedule's own entry for right now is *not* supposed to be dark, that's
very likely someone testing something and forgetting to turn it back on
on a live venue display - after DARK_REVERT_S of that, this re-asserts
the schedule's look even though "nothing changed" by the normal
last-match tracking. A target the schedule genuinely wants dark right now
(e.g. the overnight "Off" window) is never touched - the trigger is a
mismatch between "the schedule wants this lit" and "it's actually dark",
not darkness by itself.

That per-target revert only covers zones the schedule actually names
(current_look.default, plus whatever zone names appear in schedule
entries - "dishes" at the time of writing). A manual selection that
doesn't line up with a scheduled zone - e.g. picking "All" on the Live
tab and turning it off, which lands as individual current_look.fixtures
overrides on every fixture including ones in zones with no schedule of
their own ("strips") - isn't a target this scheduler knows how to revert
*to* a specific look. For exactly that case, _tick also walks every
current_look.fixtures override whose fixture's zone isn't one of the
scheduled targets, and after DARK_REVERT_S of it being dark, just clears
the override outright (same mechanism manual "revert to default" already
uses) so it falls through to current_look.default - which the per-target
logic above is independently keeping honest.
"""

from __future__ import annotations

import datetime
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("hota_gallery")

DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]  # matches datetime.weekday()
DARK_REVERT_S = 60.0


def _is_dark(look: Optional[Dict[str, Any]]) -> bool:
    """colour.mode == "off" is the authoritative "shows nothing" signal in
    the {colour, effect, video} look shape - effect/video are masks over
    the colour layer, so if there's no colour there's nothing to mask."""
    if not look:
        return True
    return (look.get("colour") or {}).get("mode", "off") == "off"


def _entry_matches(entry: Dict[str, Any], now: datetime.datetime) -> bool:
    if not entry.get("enabled", True):
        return False
    if DAY_NAMES[now.weekday()] not in entry["active_days"]:
        return False
    hhmm = now.strftime("%H:%M")
    if not (entry["start_time"] <= hhmm < entry["end_time"]):
        return False
    mmdd = now.strftime("%m-%d")
    start_date, end_date = entry.get("start_date"), entry.get("end_date")
    if start_date and mmdd < start_date:
        return False
    if end_date and mmdd > end_date:
        return False
    return True


def active_entry(
    schedule: List[Dict[str, Any]], target: Optional[str], now: Optional[datetime.datetime] = None
) -> Optional[Dict[str, Any]]:
    """The entry that should be showing right now for this target (None =
    the whole-layout default, otherwise a zone name) - last match in list
    order wins, scoped to just that target's own entries. A `priority`
    match beats a non-priority one outright, no matter where either sits
    in the list - that's the whole point of priority: overriding the
    standing schedule for a one-off request without having to go and
    reorder it. Last-match-wins is still the tie-breaker within each of
    the two tiers."""
    now = now or datetime.datetime.now()
    match = None
    priority_match = None
    for entry in schedule:
        if entry.get("zone") != target or not _entry_matches(entry, now):
            continue
        if entry.get("priority"):
            priority_match = entry
        else:
            match = entry
    return priority_match if priority_match is not None else match


def _priority_expired(entry: Dict[str, Any], now: datetime.datetime) -> bool:
    """A priority entry is a one-off override, not a standing part of the
    schedule - once it's done its job it shouldn't clutter the list.
    "Done" means its own `enabled` switch got turned off, or (if it has
    an end_date) that date has passed. Checked directly against the
    calendar rather than by watching for a match -> no-match transition,
    since a date-less priority entry that runs every day would otherwise
    look "finished" at the end of its very first day."""
    if entry.get("enabled") is False:
        return True
    end_date = entry.get("end_date")
    return bool(end_date and now.strftime("%m-%d") > end_date)


class Scheduler:
    def __init__(
        self,
        get_config: Callable[[], Dict[str, Any]],
        set_default_look: Callable[[Dict[str, Any]], Any],
        set_fixture_looks: Callable[[List[str], Optional[Dict[str, Any]]], Any],
        set_schedule: Callable[[List[Dict[str, Any]]], Any],
        check_period: float = 15.0,
        dark_revert_s: float = DARK_REVERT_S,
    ):
        self._get_config = get_config
        self._set_default_look = set_default_look
        self._set_fixture_looks = set_fixture_looks
        self._set_schedule = set_schedule
        self._check_period = check_period
        self._dark_revert_s = dark_revert_s
        self._stop = threading.Event()
        # Tracked by (target, entry name), not by comparing the whole look
        # spec - so a manual override made *during* an entry's own active
        # window isn't immediately stomped back on the next tick; a target
        # only gets reasserted when its active entry actually changes.
        self._last_applied: Dict[Optional[str], Optional[str]] = {}
        # time.monotonic() a target was first seen dark while its schedule
        # entry wanted it lit - None once it's lit again or the mismatch
        # hasn't been observed yet. See DARK_REVERT_S / _is_dark above.
        self._dark_since: Dict[Optional[str], Optional[float]] = {}
        # Same idea, per individual fixture key, for stray overrides that
        # don't belong to any scheduled zone - see the module docstring.
        self._dark_fixture_since: Dict[str, float] = {}

    def _zone_fixture_keys(self, cfg: Dict[str, Any], zone: str) -> List[str]:
        return [f"{fx['universe']}:{fx['address']}" for fx in cfg["fixtures"] if fx.get("zone") == zone]

    def _target_is_dark(self, cfg: Dict[str, Any], target: Optional[str]) -> bool:
        current_look = cfg.get("current_look") or {}
        if target is None:
            return _is_dark(current_look.get("default"))
        overrides = current_look.get("fixtures") or {}
        default = current_look.get("default")
        keys = self._zone_fixture_keys(cfg, target)
        return any(_is_dark(overrides.get(key, default)) for key in keys)

    def _apply(self, cfg: Dict[str, Any], target: Optional[str], entry: Dict[str, Any]) -> None:
        if target is None:
            self._set_default_look(entry["look"])
        else:
            self._set_fixture_looks(self._zone_fixture_keys(cfg, target), entry["look"])
        self._last_applied[target] = entry["name"]
        self._dark_since[target] = None

    def _delete_expired_priority_entries(self, cfg: Dict[str, Any], now: datetime.datetime) -> None:
        expired = [e["name"] for e in cfg.get("schedule", []) if e.get("priority") and _priority_expired(e, now)]
        if not expired:
            return
        log.info("schedule: priority entr%s finished, removing: %s",
                  "y" if len(expired) == 1 else "ies", ", ".join(expired))
        self._set_schedule([e for e in cfg["schedule"] if e["name"] not in expired])

    def _tick(self) -> None:
        cfg = self._get_config()
        now = datetime.datetime.now()
        self._delete_expired_priority_entries(cfg, now)
        cfg = self._get_config()  # re-fetch: may have just changed above
        targets = {entry.get("zone") for entry in cfg.get("schedule", [])}
        for target in targets:
            entry = active_entry(cfg["schedule"], target, now)
            if entry is None:
                continue
            if entry["name"] != self._last_applied.get(target):
                log.info("schedule: applying %r to %s", entry["name"], target or "default")
                self._apply(cfg, target, entry)
                continue
            if _is_dark(entry["look"]):
                # The schedule itself wants this target dark right now
                # (e.g. the overnight "Off" window) - nothing to revert.
                self._dark_since[target] = None
                continue
            if not self._target_is_dark(cfg, target):
                self._dark_since[target] = None
                continue
            since = self._dark_since.get(target)
            if since is None:
                self._dark_since[target] = time.monotonic()
            elif time.monotonic() - since >= self._dark_revert_s:
                log.info(
                    "schedule: %s has been dark for %.0fs while %r should be showing - reverting",
                    target or "default", time.monotonic() - since, entry["name"],
                )
                self._apply(cfg, target, entry)

        self._revert_stray_dark_overrides(cfg, targets)

    def _revert_stray_dark_overrides(self, cfg: Dict[str, Any], scheduled_targets: set) -> None:
        overrides = (cfg.get("current_look") or {}).get("fixtures") or {}
        zone_by_key = {f"{fx['universe']}:{fx['address']}": fx.get("zone") for fx in cfg["fixtures"]}
        stray_keys = {key for key, zone in zone_by_key.items() if zone not in scheduled_targets}

        now = time.monotonic()
        for key in list(self._dark_fixture_since):
            if key not in overrides or key not in stray_keys or not _is_dark(overrides.get(key)):
                del self._dark_fixture_since[key]

        to_clear = []
        for key in stray_keys & overrides.keys():
            if not _is_dark(overrides[key]):
                continue
            since = self._dark_fixture_since.get(key)
            if since is None:
                self._dark_fixture_since[key] = now
            elif now - since >= self._dark_revert_s:
                to_clear.append(key)

        if to_clear:
            log.info("schedule: %d fixture(s) dark with no scheduled zone to revert to - clearing override", len(to_clear))
            self._set_fixture_looks(to_clear, None)
            for key in to_clear:
                self._dark_fixture_since.pop(key, None)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                log.exception("scheduler tick failed")
            self._stop.wait(self._check_period)

    def start_background(self) -> threading.Thread:
        t = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self._stop.set()
