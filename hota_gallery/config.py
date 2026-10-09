"""Config schema, validation, load/save - same philosophy as sacn2wiz's
config.py: a flat CONFIG_DEFAULTS dict, validate_config collects every
problem before raising (not just the first - with 150+ fixtures you want
the whole list), save_config writes atomically (temp file + rename) so a
crash or a concurrent read never sees a half-written file.

`fixtures` is seeded once from the ELM import (see tools/init_config.py)
and then lives here as the single source of truth - the layout editor
(step 3) edits this list directly, the same way sacn2wiz's admin UI edits
`bulbs`.

`current_look` is `{"default": <look>, "fixtures": {name: <look>}}` - a
single global look plus per-fixture overrides, so a selection made on the
canvas can be given its own look without affecting anything else.

A <look> is three independently-chosen layers, not one mode:
`{"colour": <colour-layer>, "effect": <effect-layer>, "video": <video-layer>}`.
Any layer missing/absent is treated as that layer's "off". `_validate_look`
checks the whole 3-layer structure (via `_validate_colour`/
`_validate_effect`/`_validate_video`); `_validate_current_look` checks
current_look's default + every override using it.

**Colour layer** (`colour.mode`) decides what RGBW value shows: "off"
(nothing), "solid" (flat color), "gradient" (color varies by position
along `axis`, static), "color_cycle" (crossfades through a `colors` list
over `period_s`), "sweep" (a gradient that slides along `axis` over
`period_s`, looping - this and color_cycle are what actually stand in for
ELM's "rotating line"/"moving rainbow" animations, now that we know there
was never a real video file behind those, just ELM's own generative
rendering).

**Effect layer** (`effect.mode`) is a 0..1 brightness mask applied on top
of the colour layer - "off" means no masking (full pass-through), *not*
black, so a colour-only look isn't silently zeroed out by an idle effect
slot. "pulse" (brightness breathing over `period_s`), "strobe" (on/off at
`on_ms`/`off_ms`), "chase" (a bright point with a fading `tail`) and
"sine_chase" (a smooth traveling wave instead of a discrete point+tail)
both travel across the whole layout's shared (x, y) space along `axis`,
exactly like sweep - *not* independently per fixture - so one pixel reads
as flowing smoothly from one physical strip to the next across the entire
facade, rather than every strip chasing in lockstep. `tail`/`wavelength`
are percentages of the layout's full span, not LED counts, since "how
many LEDs" doesn't mean anything once fixtures share one axis. "trickle"
(each fixture independently drifts in and out of brightness on its own
deterministic phase, reading as an organic twinkle rather than anything
synced) and "shader" (real ELM generator content, see shaders.py) round
out the set.

**Video layer** (`video.mode`) is a second, independent 0..1 mask, same
"off = pass-through" neutral as effect: "bitmap" tints one of bitmaps.py's
procedural shapes, sampled at each LED's normalized (x, y), with an
optional scroll over `axis`. Kept separate from effect (rather than
folded into it) specifically so a bitmap mask and a motion effect can
layer - e.g. the "noise" bitmap playing under a colour "sweep" - without
needing a combined mode for every pairing.

Every color blend (gradient/sweep/chase's tail, color_cycle) runs through
engine.py's `_ease` curve rather than straight linear interpolation, so a
transition reads as a soft fade, not a constant-rate ramp. `direction`
("forward"/"reverse"/"bounce"/"wings", see engine.py's `_travel`/
`_fold_position`) is shared by every traveling mode (sweep, chase,
sine_chase, bitmap). The animated ones are time-driven, not
position-driven like a static gradient - see Engine._fixture_colors.

`schedule` is an ordered list of entries - {name, active_days, start_time,
end_time, start_date, end_date, look, zone, enabled}. `enabled` (default
true) lets a standing entry - BAU hours, say - be switched off without
deleting it, so it stays available as the thing that runs whenever
nothing else is scheduled, rather than needing to be re-created from
scratch later. On each tick (see
scheduler.py) the *last* entry whose day/time/date window contains "now"
gets applied, independently *per target* - `zone: null` (or omitted)
targets the whole layout (current_look.default), `zone: "dishes"` targets
only that zone's fixtures (current_look.fixtures, same override mechanism
the canvas selection uses) - so the facade and the dishes can run their
own schedules exactly like ELM's two separate stages did, without one
schedule's "last match" fight overriding the other's. If nothing in a
given target's entries matches, the scheduler leaves that target alone
rather than forcing it off. start_date/end_date ("MM-DD") are optional and
don't wrap across New Year's - a range must stay within one calendar
year, same limit sacn2wiz's config.py would call out if it had dates at
all.

Overlapping fixture addresses are *not* treated as an error here, same
call as sacn2wiz's bulbs: deliberate ganging (several fixtures meant to
mirror the same channels) is a legitimate pattern, not just the stray-
duplicate bug the ELM import found and cleaned up once already. They're
surfaced as warnings instead, so a genuine new clash doesn't get buried.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
from typing import Any, Dict, List, Optional

from .bitmaps import BITMAPS
from .shaders import SHADERS

log = logging.getLogger("hota_gallery")

LED_CHANNELS = {"RGBW": 4, "RGB": 3}


def off_look() -> Dict[str, Any]:
    """A fresh {colour, effect, video} dict, all "off" - never share one
    instance across defaults, since Store always replaces a look wholesale
    but a shared dict would still be one accidental in-place edit away from
    corrupting every default that pointed at it."""
    return {"colour": {"mode": "off"}, "effect": {"mode": "off"}, "video": {"mode": "off"}}


CONFIG_DEFAULTS: Dict[str, Any] = {
    "fps": 30.0,
    "bind_ip": None,
    "web_port": 8080,
    "device_name": "",
    # Client-side speed bump for remote (non-kiosk) connections only - not
    # real security, same philosophy as the house-lights bridge's admin
    # PIN. null/"" turns it off; the kiosk screen itself never asks.
    "remote_pin": "1234",
    "fixtures": [],
    "zones": [],
    "current_look": {"default": off_look(), "fixtures": {}},
    "schedule": [],
    "presets": [],
    "layouts": [],
    "randomizer": {"enabled": False, "events_per_hour": 20.0, "burst_s": 6.0, "targets": [], "preset_slots": []},
    "clock_chime": {"enabled": False, "look": off_look(), "duration_s": 60.0, "start_hour": 0, "end_hour": 23},
}

FIXTURE_KEYS = {
    "stage", "name", "group2", "led_type", "universe", "address",
    "led_count", "channels_per_led", "rotation", "points", "zone",
}

DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]  # index matches datetime.weekday()
SCHEDULE_ENTRY_KEYS = {"name", "active_days", "start_time", "end_time", "start_date", "end_date", "look", "zone", "enabled"}
ZONE_KEYS = {"name"}
PRESET_KEYS = {"slot", "name", "look"}
LAYOUT_KEYS = {"name", "fixtures", "background"}
RANDOMIZER_KEYS = {"enabled", "events_per_hour", "burst_s", "targets", "preset_slots"}
CLOCK_CHIME_KEYS = {"enabled", "look", "duration_s", "start_hour", "end_hour"}

# Shared by every "traveling" mode (sweep/chase/sine_chase/bitmap) - see
# engine.py's _travel/_fold_position docstrings for what each one does.
DIRECTIONS = {"forward", "reverse", "bounce", "wings"}

LOOK_KEYS = {"colour", "effect", "video"}
COLOUR_MODES = {"off", "solid", "gradient", "color_cycle", "sweep"}
EFFECT_MODES = {"off", "pulse", "strobe", "chase", "sine_chase", "trickle", "shader"}
VIDEO_MODES = {"off", "bitmap"}


class ConfigError(Exception):
    """Config is unusable. Message holds every problem found, not just the first."""


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _valid_ip(v: Any) -> bool:
    if not isinstance(v, str):
        return False
    try:
        ipaddress.IPv4Address(v)
        return True
    except ValueError:
        return False


def _valid_color(color: Any) -> bool:
    return isinstance(color, dict) and all(
        isinstance(color.get(c), int) and not isinstance(color.get(c), bool) and 0 <= color.get(c) <= 255
        for c in ("r", "g", "b", "w")
    )


def _valid_points(points: Any) -> bool:
    return isinstance(points, list) and len(points) == 2 and all(
        isinstance(p, list) and len(p) == 2 and _is_num(p[0]) and _is_num(p[1]) for p in points
    )


def _valid_period(look: Dict[str, Any], where: str, errors: List[str]) -> None:
    if "period_s" in look and (not _is_num(look["period_s"]) or look["period_s"] <= 0):
        errors.append(f"{where}: period_s must be a positive number, got {look['period_s']!r}")


def _valid_direction(look: Dict[str, Any], where: str, errors: List[str]) -> None:
    if "direction" in look and look["direction"] not in DIRECTIONS:
        errors.append(f"{where}: direction must be one of {sorted(DIRECTIONS)}, got {look['direction']!r}")


def _validate_stops(stops: Any, where: str, errors: List[str]) -> None:
    if not isinstance(stops, list) or len(stops) < 2:
        errors.append(f"{where}: needs at least 2 stops")
        return
    offsets_seen = []
    for i, stop in enumerate(stops):
        swhere = f"{where}[{i}]"
        if not isinstance(stop, dict):
            errors.append(f"{swhere} must be an object")
            continue
        offset = stop.get("offset")
        if not _is_num(offset) or not 0 <= offset <= 1:
            errors.append(f"{swhere}: offset must be a number 0-1, got {offset!r}")
        else:
            offsets_seen.append(offset)
        if not _valid_color(stop.get("color")):
            errors.append(f"{swhere}: color.r/g/b/w must be integers 0-255")
    if offsets_seen != sorted(offsets_seen):
        errors.append(f"{where}: offsets must be in ascending order")


def _validate_colour(look: Any, where: str, errors: List[str]) -> None:
    if not isinstance(look, dict):
        errors.append(f"{where} must be an object")
        return
    mode = look.get("mode", "off")
    if mode not in COLOUR_MODES:
        errors.append(f"{where}: unknown colour mode {mode!r} (only {sorted(COLOUR_MODES)} exist)")
        return
    if mode == "off":
        return
    if mode == "solid":
        if not _valid_color(look.get("color")):
            errors.append(f"{where}: solid mode needs color.r/g/b/w as integers 0-255")
        return
    if mode == "gradient":
        axis = look.get("axis", "x")
        if axis not in ("x", "y"):
            errors.append(f"{where}: axis must be 'x' or 'y', got {axis!r}")
        _validate_stops(look.get("stops"), f"{where}.stops", errors)
        return
    if mode == "color_cycle":
        colors = look.get("colors")
        if not isinstance(colors, list) or len(colors) < 2 or not all(_valid_color(c) for c in colors):
            errors.append(f"{where}: color_cycle mode needs a 'colors' list of at least 2 valid colors")
        _valid_period(look, where, errors)
        return
    # sweep
    axis = look.get("axis", "x")
    if axis not in ("x", "y"):
        errors.append(f"{where}: axis must be 'x' or 'y', got {axis!r}")
    _validate_stops(look.get("stops"), f"{where}.stops", errors)
    _valid_period(look, where, errors)
    _valid_direction(look, where, errors)


def _validate_effect(look: Any, where: str, errors: List[str]) -> None:
    if not isinstance(look, dict):
        errors.append(f"{where} must be an object")
        return
    mode = look.get("mode", "off")
    if mode not in EFFECT_MODES:
        errors.append(f"{where}: unknown effect mode {mode!r} (only {sorted(EFFECT_MODES)} exist)")
        return
    if mode == "off":
        return
    if mode == "pulse":
        _valid_period(look, where, errors)
        return
    if mode == "strobe":
        for field in ("on_ms", "off_ms"):
            v = look.get(field)
            if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
                errors.append(f"{where}: {field} must be a positive integer, got {v!r}")
        return
    if mode == "chase":
        if "tail" in look and (not _is_num(look["tail"]) or look["tail"] < 0):
            errors.append(f"{where}: tail must be a non-negative number, got {look['tail']!r}")
        axis = look.get("axis", "x")
        if axis not in ("x", "y"):
            errors.append(f"{where}: axis must be 'x' or 'y', got {axis!r}")
        _valid_period(look, where, errors)
        _valid_direction(look, where, errors)
        return
    if mode == "sine_chase":
        if "wavelength" in look and (not _is_num(look["wavelength"]) or look["wavelength"] <= 0):
            errors.append(f"{where}: wavelength must be a positive number, got {look['wavelength']!r}")
        axis = look.get("axis", "x")
        if axis not in ("x", "y"):
            errors.append(f"{where}: axis must be 'x' or 'y', got {axis!r}")
        _valid_period(look, where, errors)
        _valid_direction(look, where, errors)
        return
    if mode == "trickle":
        if "floor" in look and (not _is_num(look["floor"]) or not 0 <= look["floor"] <= 1):
            errors.append(f"{where}: floor must be a number 0-1, got {look['floor']!r}")
        _valid_period(look, where, errors)
        return
    # shader
    name = look.get("shader")
    if name not in SHADERS:
        errors.append(f"{where}: shader must be one of {sorted(SHADERS)}, got {name!r}")
    for field in ("speed", "n_items", "force", "force2"):
        if field in look and (not _is_num(look[field]) or look[field] <= 0):
            errors.append(f"{where}: {field} must be a positive number, got {look[field]!r}")


def _validate_video(look: Any, where: str, errors: List[str]) -> None:
    if not isinstance(look, dict):
        errors.append(f"{where} must be an object")
        return
    mode = look.get("mode", "off")
    if mode not in VIDEO_MODES:
        errors.append(f"{where}: unknown video mode {mode!r} (only {sorted(VIDEO_MODES)} exist)")
        return
    if mode == "off":
        return
    # bitmap
    name = look.get("bitmap")
    if name not in BITMAPS:
        errors.append(f"{where}: bitmap must be one of {sorted(BITMAPS)}, got {name!r}")
    axis = look.get("axis", "x")
    if axis not in ("x", "y"):
        errors.append(f"{where}: axis must be 'x' or 'y', got {axis!r}")
    if "period_s" in look and (not _is_num(look["period_s"]) or look["period_s"] < 0):
        errors.append(f"{where}: period_s must be a non-negative number, got {look['period_s']!r}")
    _valid_direction(look, where, errors)


def _validate_look(look: Any, where: str, errors: List[str]) -> None:
    if not isinstance(look, dict):
        errors.append(f"{where} must be an object")
        return
    for key in look:
        if key not in LOOK_KEYS:
            errors.append(f"{where}: unknown key {key!r} (a look is {{colour, effect, video}})")
    _validate_colour(look.get("colour") or {"mode": "off"}, f"{where}.colour", errors)
    _validate_effect(look.get("effect") or {"mode": "off"}, f"{where}.effect", errors)
    _validate_video(look.get("video") or {"mode": "off"}, f"{where}.video", errors)


def _validate_current_look(current_look: Any, fixture_keys: set, errors: List[str]) -> None:
    if not isinstance(current_look, dict):
        errors.append("current_look must be an object")
        return
    _validate_look(current_look.get("default"), "current_look.default", errors)

    overrides = current_look.get("fixtures", {})
    if not isinstance(overrides, dict):
        errors.append("current_look.fixtures must be an object")
        return
    for key, look in overrides.items():
        if key not in fixture_keys:
            errors.append(f"current_look.fixtures[{key!r}]: no fixture at that universe:address")
            continue
        _validate_look(look, f"current_look.fixtures[{key!r}]", errors)


def _valid_time_str(v: Any) -> bool:
    if not isinstance(v, str) or v.count(":") != 1:
        return False
    h, m = v.split(":")
    return h.isdigit() and m.isdigit() and 0 <= int(h) <= 23 and 0 <= int(m) <= 59


def _valid_date_str(v: Any) -> bool:
    if not isinstance(v, str) or v.count("-") != 1:
        return False
    mo, day = v.split("-")
    if not (mo.isdigit() and day.isdigit()):
        return False
    mo, day = int(mo), int(day)
    days_in_month = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]  # Feb generous (29) - leap years exist
    return 1 <= mo <= 12 and 1 <= day <= days_in_month[mo - 1]


def _validate_schedule(schedule: Any, zone_names: Dict[str, int], errors: List[str]) -> None:
    if not isinstance(schedule, list):
        errors.append("schedule must be a list")
        return
    seen_names: Dict[str, int] = {}
    for i, entry in enumerate(schedule):
        where = f"schedule[{i}]"
        if not isinstance(entry, dict):
            errors.append(f"{where} must be an object")
            continue
        for key in entry:
            if key not in SCHEDULE_ENTRY_KEYS:
                errors.append(f"{where}: unknown key {key!r}")

        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{where}: name must be a non-empty string")
        elif name in seen_names:
            errors.append(f"{where}: duplicate schedule name {name!r} (also schedule[{seen_names[name]}])")
        else:
            seen_names[name] = i

        if "enabled" in entry and not isinstance(entry["enabled"], bool):
            errors.append(f"{where}: enabled must be true or false, got {entry['enabled']!r}")

        days = entry.get("active_days")
        if not isinstance(days, list) or not days or not all(d in DAY_NAMES for d in days):
            errors.append(f"{where}: active_days must be a non-empty list from {DAY_NAMES}")

        start_time, end_time = entry.get("start_time"), entry.get("end_time")
        if not _valid_time_str(start_time):
            errors.append(f"{where}: start_time must be \"HH:MM\", got {start_time!r}")
        if not _valid_time_str(end_time):
            errors.append(f"{where}: end_time must be \"HH:MM\", got {end_time!r}")
        if _valid_time_str(start_time) and _valid_time_str(end_time) and start_time >= end_time:
            errors.append(f"{where}: start_time {start_time!r} must be before end_time {end_time!r} "
                           "(a window spanning midnight isn't supported yet - split it into two entries)")

        for field in ("start_date", "end_date"):
            value = entry.get(field)
            if value is not None and not _valid_date_str(value):
                errors.append(f"{where}: {field} must be \"MM-DD\" or null, got {value!r}")
        start_date, end_date = entry.get("start_date"), entry.get("end_date")
        if start_date and end_date and start_date > end_date:
            errors.append(f"{where}: start_date {start_date!r} is after end_date {end_date!r} "
                           "(a range spanning New Year's isn't supported yet - split it into two entries)")

        zone = entry.get("zone")
        if zone is not None and zone not in zone_names:
            errors.append(f"{where}: zone {zone!r} is not in zones[]")

        _validate_look(entry.get("look"), f"{where}.look", errors)


def _validate_presets(presets: Any, errors: List[str]) -> Dict[int, int]:
    """A preset is just a saved <look> pinned to a UI grid slot - the
    staff-facing "assignable squares" in the Live tab, used for both a
    saved solid color and a saved generated effect (gradient/pulse/etc),
    same <look> shape as current_look.default or a schedule entry's look.
    `name` is optional (staff can leave a preset unnamed; the UI falls back
    to "Preset N") - this is also what the randomizer's `preset_slots` and
    the clock chime's program picker show instead of a bare slot number.
    Returns the valid slot->index map so the randomizer's preset_slots can
    be checked against it without re-deriving it."""
    if not isinstance(presets, list):
        errors.append("presets must be a list")
        return {}
    seen_slots: Dict[int, int] = {}
    for i, entry in enumerate(presets):
        where = f"presets[{i}]"
        if not isinstance(entry, dict):
            errors.append(f"{where} must be an object")
            continue
        for key in entry:
            if key not in PRESET_KEYS:
                errors.append(f"{where}: unknown key {key!r}")
        slot = entry.get("slot")
        if not isinstance(slot, int) or isinstance(slot, bool) or slot < 0:
            errors.append(f"{where}: slot must be a non-negative integer, got {slot!r}")
        elif slot in seen_slots:
            errors.append(f"{where}: duplicate slot {slot} (also presets[{seen_slots[slot]}])")
        else:
            seen_slots[slot] = i
        if "name" in entry and entry["name"] is not None and not isinstance(entry["name"], str):
            errors.append(f"{where}: name must be a string or null, got {entry['name']!r}")
        _validate_look(entry.get("look"), f"{where}.look", errors)
    return seen_slots


def _validate_layouts(layouts: Any, fixture_keys: set, errors: List[str]) -> None:
    """A layout is a purely operator-facing screen arrangement - like
    GrandMA's Layout Views - and is independent of a fixture's real
    `points` in the main fixtures[] list. The engine's position-driven
    looks (gradient/sweep) only ever read fixtures[].points; a layout's
    own per-fixture points here are just where that fixture's dot is
    drawn/dragged in this particular view, so staff can build whatever
    screen arrangement is useful (e.g. one layout per fixture type, one
    per physical zone, a rough building outline, ...) without touching
    the real geometry. Deliberately venue-agnostic: a venue's config just
    lists whatever layouts make sense for it."""
    if not isinstance(layouts, list):
        errors.append("layouts must be a list")
        return
    seen_names: Dict[str, int] = {}
    for i, entry in enumerate(layouts):
        where = f"layouts[{i}]"
        if not isinstance(entry, dict):
            errors.append(f"{where} must be an object")
            continue
        for key in entry:
            if key not in LAYOUT_KEYS:
                errors.append(f"{where}: unknown key {key!r}")

        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{where}: name must be a non-empty string")
        elif name in seen_names:
            errors.append(f"{where}: duplicate layout name {name!r} (also layouts[{seen_names[name]}])")
        else:
            seen_names[name] = i

        fixtures = entry.get("fixtures", {})
        if not isinstance(fixtures, dict):
            errors.append(f"{where}: fixtures must be an object")
            continue
        for key, points in fixtures.items():
            if key not in fixture_keys:
                errors.append(f"{where}.fixtures[{key!r}]: no fixture at that universe:address")
                continue
            if not _valid_points(points):
                errors.append(f"{where}.fixtures[{key!r}]: points must be exactly two [x, y] pairs")

        if "background" in entry and entry["background"] is not None:
            if not isinstance(entry["background"], str) or not entry["background"].strip():
                errors.append(f"{where}: background must be a non-empty string filename or null, got {entry['background']!r}")


def _validate_randomizer(rc: Any, zone_names: Dict[str, int], preset_slots: Dict[int, int], errors: List[str]) -> None:
    """Occasional unscheduled bursts (e.g. a chase) on a random target
    zone, so there's some ambient movement during otherwise-static BAU
    hours without it reading as a detectable pattern - see randomizer.py.
    `targets` are zone names (e.g. "strips"/"dishes"); `preset_slots`
    references slots in presets[] rather than carrying its own duplicate
    <look> copies - the randomizer's burst pool IS the presets pool (or a
    subset of it), so renaming/editing a preset updates the randomizer too
    instead of needing its own separate editor. Timing itself isn't config
    - randomizer.py jitters each interval around 3600/events_per_hour
    rather than firing on a fixed clock, which is what actually keeps it
    from looking mechanical, not a choice of how many discrete intervals
    to rotate through."""
    if not isinstance(rc, dict):
        errors.append("randomizer must be an object")
        return
    for key in rc:
        if key not in RANDOMIZER_KEYS:
            errors.append(f"randomizer: unknown key {key!r}")

    if "enabled" in rc and not isinstance(rc["enabled"], bool):
        errors.append(f"randomizer: enabled must be true or false, got {rc['enabled']!r}")
    if "events_per_hour" in rc and (not _is_num(rc["events_per_hour"]) or rc["events_per_hour"] <= 0):
        errors.append(f"randomizer: events_per_hour must be a positive number, got {rc['events_per_hour']!r}")
    if "burst_s" in rc and (not _is_num(rc["burst_s"]) or rc["burst_s"] <= 0):
        errors.append(f"randomizer: burst_s must be a positive number, got {rc['burst_s']!r}")

    targets = rc.get("targets", [])
    if not isinstance(targets, list):
        errors.append("randomizer.targets must be a list")
    else:
        for i, zone in enumerate(targets):
            if zone not in zone_names:
                errors.append(f"randomizer.targets[{i}]: zone {zone!r} is not in zones[]")

    slots = rc.get("preset_slots", [])
    if not isinstance(slots, list):
        errors.append("randomizer.preset_slots must be a list")
    else:
        for i, slot in enumerate(slots):
            if slot not in preset_slots:
                errors.append(f"randomizer.preset_slots[{i}]: no preset at slot {slot!r}")


def _validate_clock_chime(cc: Any, errors: List[str]) -> None:
    """One "program" (a single <look>, typically copied from a saved
    preset - see the Settings tab) that fires across every fixture at the
    top of every hour between start_hour and end_hour inclusive, for
    duration_s, no schedule entries needed - see clock_chime.py. Whole-
    layout only (unlike the randomizer, which picks a zone) since this is
    meant as a single deliberate "big show" moment, not an ambient
    per-zone accent."""
    if not isinstance(cc, dict):
        errors.append("clock_chime must be an object")
        return
    for key in cc:
        if key not in CLOCK_CHIME_KEYS:
            errors.append(f"clock_chime: unknown key {key!r}")

    if "enabled" in cc and not isinstance(cc["enabled"], bool):
        errors.append(f"clock_chime: enabled must be true or false, got {cc['enabled']!r}")
    if "duration_s" in cc and (not _is_num(cc["duration_s"]) or cc["duration_s"] <= 0):
        errors.append(f"clock_chime: duration_s must be a positive number, got {cc['duration_s']!r}")

    for field in ("start_hour", "end_hour"):
        if field in cc:
            v = cc[field]
            if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 23:
                errors.append(f"clock_chime: {field} must be an integer 0-23, got {v!r}")
    start_hour, end_hour = cc.get("start_hour", 0), cc.get("end_hour", 23)
    if isinstance(start_hour, int) and isinstance(end_hour, int) and start_hour > end_hour:
        errors.append(
            f"clock_chime: start_hour {start_hour!r} is after end_hour {end_hour!r} "
            "(a window spanning midnight isn't supported yet - same limit as the schedule's start/end time)"
        )

    if "look" in cc:
        _validate_look(cc["look"], "clock_chime.look", errors)


def validate_config(raw: Any) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []

    if not isinstance(raw, dict):
        raise ConfigError("top level of the config must be a JSON object")

    for key in raw:
        if key not in CONFIG_DEFAULTS:
            warnings.append(f"unknown key {key!r} ignored")

    cfg = dict(CONFIG_DEFAULTS)
    cfg.update({k: v for k, v in raw.items() if k in CONFIG_DEFAULTS})

    if not _is_num(cfg["fps"]) or cfg["fps"] <= 0:
        errors.append(f"fps must be a positive number, got {cfg['fps']!r}")

    if cfg["bind_ip"] is not None and not _valid_ip(cfg["bind_ip"]):
        errors.append(f"bind_ip must be an IPv4 address or null, got {cfg['bind_ip']!r}")

    if not isinstance(cfg["web_port"], int) or isinstance(cfg["web_port"], bool) or not 1 <= cfg["web_port"] <= 65535:
        errors.append(f"web_port must be an integer 1-65535, got {cfg['web_port']!r}")

    if not isinstance(cfg["device_name"], str):
        errors.append(f"device_name must be a string, got {cfg['device_name']!r}")

    if cfg["remote_pin"] not in (None, "") and not (isinstance(cfg["remote_pin"], str) and cfg["remote_pin"].isdigit()):
        errors.append(f"remote_pin must be a string of digits, or null/empty to disable it, got {cfg['remote_pin']!r}")

    zones = cfg["zones"]
    zone_names: Dict[str, int] = {}
    if not isinstance(zones, list):
        errors.append("zones must be a list")
        zones = []
    else:
        for i, entry in enumerate(zones):
            where = f"zones[{i}]"
            if not isinstance(entry, dict):
                errors.append(f"{where} must be an object")
                continue
            for key in entry:
                if key not in ZONE_KEYS:
                    warnings.append(f"{where}: unknown key {key!r} ignored")
            name = entry.get("name")
            if not isinstance(name, str) or not name.strip():
                errors.append(f"{where}: name must be a non-empty string")
            elif name in zone_names:
                errors.append(f"{where}: duplicate zone name {name!r} (also zones[{zone_names[name]}])")
            else:
                zone_names[name] = i

    fixtures = cfg["fixtures"]
    if not isinstance(fixtures, list):
        errors.append("fixtures must be a list")
        fixtures = []

    by_universe: Dict[int, List[dict]] = {}
    # "universe:address", not name - names aren't unique (a lot of real
    # fixtures here share the generic "RGBW01" placeholder, see
    # elm_import.py), but universe+address is the fixture's real identity,
    # already relied on everywhere else in this file.
    fixture_keys: set = set()
    for i, entry in enumerate(fixtures):
        where = f"fixtures[{i}]"
        if not isinstance(entry, dict):
            errors.append(f"{where} must be an object")
            continue
        for key in entry:
            if key not in FIXTURE_KEYS:
                warnings.append(f"{where}: unknown key {key!r} ignored")

        # Not checked for uniqueness: ELM itself never enforced it, and a
        # large chunk of real fixtures here legitimately share the generic
        # "RGBW01" placeholder name (see elm_import.py's cleanup) - address
        # + universe is this project's real identity, name is just a label.
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{where}: name must be a non-empty string")

        led_type = entry.get("led_type")
        if led_type not in LED_CHANNELS:
            errors.append(f"{where}: led_type must be one of {sorted(LED_CHANNELS)}, got {led_type!r}")
            continue

        universe = entry.get("universe")
        address = entry.get("address")
        led_count = entry.get("led_count")
        if not isinstance(universe, int) or isinstance(universe, bool) or universe < 0:
            errors.append(f"{where}: universe must be a non-negative integer, got {universe!r}")
            continue
        if not isinstance(address, int) or isinstance(address, bool) or address < 0:
            errors.append(f"{where}: address must be a non-negative integer, got {address!r}")
            continue
        if not isinstance(led_count, int) or isinstance(led_count, bool) or led_count <= 0:
            errors.append(f"{where}: led_count must be a positive integer, got {led_count!r}")
            continue

        channels_per_led = entry.get("channels_per_led", LED_CHANNELS[led_type])
        end_address = address + led_count * channels_per_led
        if end_address > 512:
            errors.append(
                f"{where}: {led_type} fixture at universe {universe} addr {address} "
                f"needs channels up to {end_address - 1}, overflowing the 512-channel universe"
            )
            continue

        if "zone" in entry and entry["zone"] is not None:
            if not isinstance(entry["zone"], str):
                errors.append(f"{where}: zone must be a string or null, got {entry['zone']!r}")
            elif entry["zone"] not in zone_names:
                errors.append(f"{where}: zone {entry['zone']!r} is not in zones[]")

        if not _valid_points(entry.get("points")):
            errors.append(f"{where}: points must be exactly two [x, y] pairs")

        by_universe.setdefault(universe, []).append({"address": address, "end": end_address, "name": name})
        fixture_keys.add(f"{universe}:{address}")

    for universe, group in by_universe.items():
        ordered = sorted(group, key=lambda f: f["address"])
        for j, a in enumerate(ordered):
            for b in ordered[j + 1 :]:
                if b["address"] >= a["end"]:
                    break
                warnings.append(
                    f"fixtures: {a['name']!r} and {b['name']!r} overlap on universe {universe} "
                    f"(addr {a['address']}-{a['end'] - 1} vs {b['address']}-{b['end'] - 1}) - "
                    "fine if intentional ganging, otherwise worth checking"
                )

    _validate_current_look(cfg["current_look"], fixture_keys, errors)
    _validate_schedule(cfg["schedule"], zone_names, errors)
    preset_slots = _validate_presets(cfg["presets"], errors)
    _validate_layouts(cfg["layouts"], fixture_keys, errors)
    _validate_randomizer(cfg["randomizer"], zone_names, preset_slots, errors)
    _validate_clock_chime(cfg["clock_chime"], errors)

    for msg in warnings:
        log.warning("config: %s", msg)
    if errors:
        raise ConfigError("\n".join(f"  - {e}" for e in errors))
    return cfg


def load_config(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        raise ConfigError(f"config not found: {path}")
    except ValueError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}")
    return validate_config(raw)


def save_config(path: str, cfg: Dict[str, Any]) -> None:
    validated = validate_config(cfg)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(validated, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
