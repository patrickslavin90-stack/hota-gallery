"""The running engine: holds fixtures + the current look, and continuously
sends Art-Net frames built from them - sacn2wiz's Bridge is the direct
model (sender thread pings the systemd watchdog after every send cycle so
a wedged thread gets restarted rather than silently going dark, bind_ip is
deliberately not hot-reloadable since it owns the one send socket).

Colour is computed per-LED-position, not per-fixture: `sample_colors`
walks every LED's real (x, y) along its fixture's geometry line. A <look>
is three independently-chosen layers, not one mode - `colour` (solid/
gradient/color_cycle/sweep: what RGBW value to show), `effect` (off/
pulse/strobe/chase/sine_chase/trickle/shader: a 0..1 brightness mask
varying over position/time) and `video` (off/bitmap: another 0..1 mask,
sampled from a bitmaps.py grid). The final pixel is the colour layer's
RGBW scaled by effect_intensity * video_intensity - so "noise video +
colour sweep" is exactly the noise bitmap's mask multiplying the sweep's
traveling gradient, not a separate combined mode. `_colour_rgba`,
`_effect_intensity` and `_video_intensity` each take one layer's spec and
a position/time and return their contribution; `_fixture_colors` combines
all three per LED. Any layer's "off" is the neutral value for its role:
black for colour (nothing to show), 1.0 (full pass-through) for effect/
video (no masking) - so colour=off alone already reproduces the old
"off" look regardless of what effect/video are set to.

`build_frames` (DMX) and `preview` (the API's live canvas feed) are both
just different packagings of the same `sample_colors` output - one
source of truth for what's actually being sent, so the UI never shows
something other than what the fixtures are really doing.
"""

from __future__ import annotations

import logging
import math
import os
import socket
import threading
import time
import zlib
from typing import Any, Dict, List, Optional, Tuple

from .artnet import ArtNetSender
from .bitmaps import BITMAPS
from .shaders import SHADERS

log = logging.getLogger("hota_gallery")

Color = Tuple[int, int, int, int]  # r, g, b, w - w unused/ignored for RGB fixtures


def sd_notify(state: str) -> None:
    """Tell systemd (Type=notify) we're ready / still alive / stopping.
    No-op outside systemd (NOTIFY_SOCKET unset) - same as sacn2wiz's
    protocol.sd_notify, duplicated here rather than shared since these are
    two independently-deployed projects.
    """
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sock.sendto(state.encode(), addr)
        sock.close()
    except OSError:
        pass


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _ease(t: float) -> float:
    """Cosine ease: 0->0, 1->1, but with zero slope at both ends - a soft
    S-curve instead of a constant-rate ramp. Used everywhere two colors
    get blended (gradient/sweep's stop interpolation, color_cycle, chase's
    tail falloff) so a transition reads as a fade rather than a mechanical
    straight-line ramp - same curve pulse already used for its brightness
    breathing, just applied consistently now."""
    return (1 - math.cos(math.pi * t)) / 2


def _fold_position(t: float, direction: str) -> float:
    """0..1 spatial position -> folded 0..1 when direction is "wings" -
    mirrors both halves of the axis on top of each other around its
    center, so a traveling effect (sweep/chase/sine_chase/bitmap) appears
    to move outward from the middle in both directions at once, like a
    bird's wings spreading. Any other direction passes the position
    through unchanged - the travel direction itself is handled separately
    by `_travel`."""
    return abs(t - 0.5) * 2 if direction == "wings" else t


def _travel(pos_t: float, progress: float, direction: str) -> float:
    """Combine a 0..1 spatial position with a 0..1 time progress (how far
    through one period) into the phase used to sample a gradient/wave/
    bitmap - this is what makes an effect actually travel rather than
    just oscillate in place. "forward": the wave reaches low positions
    first and moves toward high ones as time passes. "reverse": the exact
    mirror. "bounce": time progress itself ping-pongs (0->1->0) instead of
    wrapping (0->1->0->1->...), so the wave reverses direction at each end
    of its run instead of snapping back to the start. "wings" uses the
    same forward combination here - its distinctive look comes entirely
    from `_fold_position` having already folded pos_t before this runs."""
    if direction == "bounce":
        progress = 1 - abs(2 * (progress % 1.0) - 1)
        return (pos_t - progress) % 1.0
    if direction == "reverse":
        return (pos_t + progress) % 1.0
    return (pos_t - progress) % 1.0


class Fixture:
    def __init__(self, data: Dict[str, Any]):
        self.name: str = data["name"]
        self.universe: int = data["universe"]
        self.address: int = data["address"]
        self.led_count: int = data["led_count"]
        self.led_type: str = data["led_type"]
        self.channels_per_led: int = data.get("channels_per_led", 4 if self.led_type == "RGBW" else 3)
        self.zone: Optional[str] = data.get("zone")

        (x1, y1), (x2, y2) = data["points"]
        # One position per LED, evenly spaced along the fixture's geometry
        # line - a single-LED fixture just sits at its first point. This
        # mirrors how ELM itself laid LEDs out along a fixture's line for
        # its own bitmap sampling (see elm_import.py's module docstring).
        if self.led_count == 1:
            self.led_positions: List[Tuple[float, float]] = [(x1, y1)]
        else:
            self.led_positions = [
                (_lerp(x1, x2, i / (self.led_count - 1)), _lerp(y1, y2, i / (self.led_count - 1)))
                for i in range(self.led_count)
            ]

    @property
    def channel_count(self) -> int:
        return self.led_count * self.channels_per_led

    @property
    def key(self) -> str:
        """"universe:address" - the fixture's real identity, used by
        current_look.fixtures overrides. Not `name`: many real fixtures
        here share the generic "RGBW01" placeholder name (see
        elm_import.py), so name alone can't target one specific fixture."""
        return f"{self.universe}:{self.address}"


class BoundingBox:
    """The real layout's (x, y) extent across every fixture - what a
    gradient's 0..1 axis is normalized against. Recomputed on reload since
    the layout editor (future) can add/move/remove fixtures."""

    def __init__(self, fixtures: List[Fixture]):
        xs = [x for fx in fixtures for x, _ in fx.led_positions]
        ys = [y for fx in fixtures for _, y in fx.led_positions]
        self.min_x, self.max_x = (min(xs), max(xs)) if xs else (0.0, 1.0)
        self.min_y, self.max_y = (min(ys), max(ys)) if ys else (0.0, 1.0)

    def normalize(self, x: float, y: float, axis: str) -> float:
        if axis == "y":
            span = self.max_y - self.min_y
            return (y - self.min_y) / span if span else 0.5
        span = self.max_x - self.min_x
        return (x - self.min_x) / span if span else 0.5


class Engine:
    def __init__(self, cfg: Dict[str, Any], config_path: Optional[str] = None):
        self.cfg = cfg
        self.config_path = config_path
        self.fps = float(cfg["fps"])
        self.bind_ip = cfg["bind_ip"]
        self.fixtures: List[Fixture] = [Fixture(f) for f in cfg["fixtures"]]
        self.bbox = BoundingBox(self.fixtures)
        self.current_look: Dict[str, Any] = cfg["current_look"]

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._sender: Optional[ArtNetSender] = None

    # -- live reconfiguration -------------------------------------------

    def reload(self, cfg: Dict[str, Any]) -> None:
        """Apply fixtures/look/fps changes without restarting the process.
        bind_ip is NOT handled here - it owns the sender's one socket,
        same call sacn2wiz's Bridge.reload makes about its receive socket.
        """
        with self._lock:
            self.fps = float(cfg["fps"])
            self.fixtures = [Fixture(f) for f in cfg["fixtures"]]
            self.bbox = BoundingBox(self.fixtures)
            self.current_look = cfg["current_look"]
            self.cfg = cfg

    def set_look(self, look: Dict[str, Any]) -> None:
        """Used by the API to change the live look without a full config
        reload - persistence to disk is the caller's job (webapp.Store)."""
        with self._lock:
            self.current_look = look
            self.cfg["current_look"] = look

    # -- colour sampling ----------------------------------------------------

    def sample_colors(self) -> List[List[Color]]:
        """Per-fixture list of per-LED (r, g, b, w) tuples for the current
        look - the one place "what color is this LED right now" gets
        decided, shared by build_frames (DMX) and preview (the API).

        Each fixture gets its own look spec: current_look.fixtures[key] if
        it has an override (key is "universe:address" - see Fixture.key),
        else current_look.default. A selection made on the canvas only
        ever writes an override for the fixtures actually selected, so
        everything else keeps whatever it already had.
        """
        with self._lock:
            fixtures = self.fixtures
            current_look = self.current_look
            bbox = self.bbox

        default = current_look.get("default") or self._OFF_LOOK
        overrides = current_look.get("fixtures", {})
        now = time.monotonic()
        return [
            self._fixture_colors(fx, overrides.get(fx.key, default), bbox, now)
            for fx in fixtures
        ]

    _OFF_LOOK: Dict[str, Any] = {"colour": {"mode": "off"}, "effect": {"mode": "off"}, "video": {"mode": "off"}}

    @classmethod
    def _fixture_colors(cls, fx: "Fixture", look: Dict[str, Any], bbox: "BoundingBox", now: float) -> List[Color]:
        colour_look = look.get("colour") or {"mode": "off"}
        effect_look = look.get("effect") or {"mode": "off"}
        video_look = look.get("video") or {"mode": "off"}
        colors = []
        for x, y in fx.led_positions:
            c = cls._colour_rgba(colour_look, x, y, bbox, now)
            factor = cls._effect_intensity(effect_look, x, y, bbox, now, fx) * cls._video_intensity(
                video_look, x, y, bbox, now
            )
            colors.append(tuple(round(c[k] * factor) for k in range(4)))
        return colors  # type: ignore[return-value]

    @classmethod
    def _colour_rgba(cls, look: Dict[str, Any], x: float, y: float, bbox: "BoundingBox", now: float) -> Color:
        """The colour layer: what RGBW value shows, independent of whatever
        effect/video masking is layered on top of it."""
        mode = look.get("mode", "off")
        if mode == "solid":
            c = look["color"]
            return (c["r"], c["g"], c["b"], c["w"])
        if mode == "gradient":
            axis = look.get("axis", "x")
            stops = sorted(look["stops"], key=lambda s: s["offset"])
            return cls._gradient_color(stops, bbox.normalize(x, y, axis))
        if mode == "color_cycle":
            colors = look["colors"]
            period = look.get("period_s", 5.0)
            n = len(colors)
            t = (now % period) / period * n
            i = int(t) % n
            frac = _ease(t - int(t))
            a, b = colors[i], colors[(i + 1) % n]
            return tuple(round(_lerp(a[k], b[k], frac)) for k in ("r", "g", "b", "w"))  # type: ignore[return-value]
        if mode == "sweep":
            axis = look.get("axis", "x")
            stops = sorted(look["stops"], key=lambda s: s["offset"])
            period = look.get("period_s", 4.0)
            direction = look.get("direction", "forward")
            progress = (now % period) / period
            phase = _travel(_fold_position(bbox.normalize(x, y, axis), direction), progress, direction)
            return cls._gradient_color(stops, phase)
        return (0, 0, 0, 0)  # off

    @staticmethod
    def _effect_intensity(
        look: Dict[str, Any], x: float, y: float, bbox: "BoundingBox", now: float, fx: "Fixture"
    ) -> float:
        """The effect layer: a 0..1 brightness mask varying over position
        and/or time, applied on top of whatever the colour layer produces -
        "off" is the neutral value (full pass-through), not black, so a
        colour-only look isn't silently zeroed out by an idle effect slot."""
        mode = look.get("mode", "off")
        if mode == "pulse":
            period = look.get("period_s", 2.0)
            phase = (now % period) / period
            return (1 - math.cos(2 * math.pi * phase)) / 2  # smooth 0->1->0, not a linear triangle
        if mode == "strobe":
            cycle_ms = look["on_ms"] + look["off_ms"]
            return 1.0 if (now * 1000) % cycle_ms < look["on_ms"] else 0.0
        if mode == "chase":
            # Travels along the whole layout's real (x, y) space (like
            # sweep/gradient), NOT each fixture's own LED run in isolation -
            # otherwise every strip chases in lockstep rather than reading
            # as one pixel flowing smoothly from one fixture to the next
            # across the entire facade. `tail` is a percentage of the
            # layout's full span, not an LED count, since "how many LEDs"
            # no longer means anything once fixtures share one axis.
            axis = look.get("axis", "x")
            period = look.get("period_s", 1.0)
            tail_frac = max(0.001, look.get("tail", 3.0) / 100.0)
            direction = look.get("direction", "forward")
            progress = (now % period) / period
            pos = bbox.normalize(x, y, axis)
            if direction == "bounce":
                # A single point bouncing needs its target position computed
                # directly (0->1->0 across the period) and compared by plain
                # distance - going through _travel's wrapped phase would make
                # the turnaround (target==1) alias to the start (==0) and
                # the point would never appear to reach the far end.
                target = 1 - abs(2 * progress - 1)
                dist = abs(pos - target)
            elif direction == "wings":
                # Same reasoning as bounce: the point spreading out from the
                # center to the ends needs a direct (unwrapped) distance, or
                # the end (folded position 1.0) aliases with the center
                # (0.0) and both would flash at once.
                folded = abs(pos - 0.5) * 2  # 0 at center, 1 at either end
                dist = abs(folded - progress)
            else:
                dist = _travel(pos, progress, direction)
            return 1 - _ease(min(1.0, dist / tail_frac))
        if mode == "sine_chase":
            # Same whole-layout travel as chase above - `wavelength` is a
            # percentage of the full span per wave cycle, not an LED count.
            axis = look.get("axis", "x")
            period = look.get("period_s", 2.0)
            cycles = max(0.1, 100.0 / max(0.1, look.get("wavelength", 4.0)))
            direction = look.get("direction", "forward")
            progress = (now % period) / period
            folded = _fold_position(bbox.normalize(x, y, axis), direction)
            pos_t = (folded * cycles) % 1.0
            phase = _travel(pos_t, progress, direction)
            return (1 - math.cos(2 * math.pi * phase)) / 2
        if mode == "trickle":
            period = look.get("period_s", 3.0)
            floor = look.get("floor", 0.0)
            # Deterministic per-fixture phase offset (not Python's hash() -
            # that's randomized per process via PYTHONHASHSEED, which would
            # reshuffle every fixture's offset on each service restart) so
            # fixtures independently drift in and out of phase with each
            # other, reading as an organic trickle rather than a synced
            # pulse or a fixed-order chase.
            offset = (zlib.crc32(fx.key.encode()) % 10_007) / 10_007.0
            t = ((now / period) + offset) % 1.0
            wave = (1 - math.cos(2 * math.pi * t)) / 2
            return floor + (1 - floor) * wave
        if mode == "shader":
            # Real ELM generator content (see shaders.py's docstring) - a
            # continuous function of the whole layout's shared (x, y) space
            # and time, so it's inherently smooth across every fixture at
            # once, the same way sweep/bitmap already are.
            fn = SHADERS.get(look.get("shader", "circle_scroll"), SHADERS["circle_scroll"])
            speed = look.get("speed", 1.0)
            params = {
                "n_items": look.get("n_items", 20.0),
                "force": look.get("force", 3.0),
                "force2": look.get("force2", 5.0),
            }
            span_x = bbox.max_x - bbox.min_x
            span_y = bbox.max_y - bbox.min_y
            cx = bbox.normalize(x, y, "x") - 0.5
            cy = bbox.normalize(x, y, "y") - 0.5
            # Aspect-correct exactly like the original shaders' own
            # `if (iResolution.x < iResolution.y)` check, using the layout's
            # real (x, y) span in place of pixel resolution.
            if span_x and span_y:
                if span_x < span_y:
                    cx *= span_x / span_y
                else:
                    cy *= span_y / span_x
            return fn(cx, cy, now * speed, params)
        return 1.0  # off - no masking, full pass-through

    @staticmethod
    def _video_intensity(look: Dict[str, Any], x: float, y: float, bbox: "BoundingBox", now: float) -> float:
        """The video layer: a second 0..1 mask sampled from a bitmaps.py
        grid - same "off = pass-through" neutral as the effect layer, and
        deliberately a separate slot from it so a bitmap mask and a motion
        effect (e.g. the "noise" bitmap plus a chase) can run at once."""
        mode = look.get("mode", "off")
        if mode == "bitmap":
            grid = BITMAPS.get(look.get("bitmap", "dot"), BITMAPS["dot"])
            gh, gw = len(grid), len(grid[0])
            axis = look.get("axis", "x")
            period = look.get("period_s", 0.0)
            direction = look.get("direction", "forward")
            progress = (now % period) / period if period > 0 else 0.0
            u = bbox.normalize(x, y, "x")
            v = bbox.normalize(x, y, "y")
            if axis == "x":
                u = _travel(_fold_position(u, direction), progress, direction)
            else:
                v = _travel(_fold_position(v, direction), progress, direction)
            gx = min(gw - 1, int(u * gw))
            gy = min(gh - 1, int(v * gh))
            return grid[gy][gx]
        return 1.0  # off - no masking, full pass-through

    @staticmethod
    def _gradient_color(stops: List[Dict[str, Any]], t: float) -> Color:
        t = max(0.0, min(1.0, t))
        lo, hi = stops[0], stops[-1]
        for i in range(len(stops) - 1):
            if stops[i]["offset"] <= t <= stops[i + 1]["offset"]:
                lo, hi = stops[i], stops[i + 1]
                break
        span = hi["offset"] - lo["offset"]
        local_t = _ease((t - lo["offset"]) / span) if span else 0.0
        a, b = lo["color"], hi["color"]
        return tuple(round(_lerp(a[k], b[k], local_t)) for k in ("r", "g", "b", "w"))  # type: ignore[return-value]

    def preview(self) -> List[Dict[str, Any]]:
        """Per-fixture colours for the layout UI's live canvas - same data
        that's actually being sent, not a client-side recomputation of it."""
        with self._lock:
            fixtures = self.fixtures
        colors = self.sample_colors()
        return [
            {"name": fx.name, "colors": [list(c) for c in fx_colors]}
            for fx, fx_colors in zip(fixtures, colors)
        ]

    # -- frame building ---------------------------------------------------

    def build_frames(self) -> Dict[int, bytearray]:
        """One 512-byte buffer per universe that has fixtures, populated
        from the current look. Universes with no fixtures are skipped
        entirely - nothing to send there."""
        with self._lock:
            fixtures = self.fixtures
        colors = self.sample_colors()

        frames: Dict[int, bytearray] = {}
        for fx, fx_colors in zip(fixtures, colors):
            buf = frames.setdefault(fx.universe, bytearray(512))
            values = bytearray()
            for c in fx_colors:
                values += bytes(c) if fx.led_type == "RGBW" else bytes(c[:3])
            buf[fx.address : fx.address + len(values)] = values
        return frames

    # -- run loop -----------------------------------------------------------

    @staticmethod
    def _watchdog_period() -> float:
        try:
            usec = int(os.environ.get("WATCHDOG_USEC", "0"))
        except ValueError:
            return 0.0
        return (usec / 1_000_000.0) / 2.0 if usec > 0 else 0.0

    def _sender_loop(self) -> None:
        self._sender = ArtNetSender(self.bind_ip) if self.bind_ip else ArtNetSender(self._auto_bind_ip())
        watchdog_period = self._watchdog_period()
        next_watchdog = 0.0
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                frames = self.build_frames()
                self._sender.send_universes({u: bytes(b) for u, b in frames.items()})

                if watchdog_period and now >= next_watchdog:
                    sd_notify("WATCHDOG=1")
                    next_watchdog = now + watchdog_period

                self._stop.wait(max(0.01, 1.0 / self.fps))
        finally:
            if self._sender:
                self._sender.close()

    @staticmethod
    def _auto_bind_ip() -> str:
        """Best-guess local address when bind_ip isn't configured - same
        connect()-picks-a-route trick as sacn2wiz's protocol.local_ip_for,
        scoped to the broadcast address rather than a specific peer.
        SO_BROADCAST has to be set before connect() to a broadcast address
        at all - without it Linux refuses with EPERM, since an unprivileged
        broadcast send is opt-in.
        """
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.connect(("255.255.255.255", 1))
            return s.getsockname()[0]

    def start_background(self) -> List[threading.Thread]:
        t = threading.Thread(target=self._sender_loop, name="artnet-sender", daemon=True)
        t.start()
        return [t]

    def run(self) -> int:
        sd_notify("READY=1\nSTATUS=bridging")
        try:
            self._sender_loop()
        except KeyboardInterrupt:
            pass
        finally:
            sd_notify("STOPPING=1")
        return 0

    def stop(self) -> None:
        self._stop.set()
