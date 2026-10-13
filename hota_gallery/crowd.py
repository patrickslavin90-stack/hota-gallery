"""Crowd battle: the audience's phones play tug of war on one controller's
lights (made for the outdoor stage). Two sides, two colours; the building is
split at a bright line that the faster-tapping side pushes across. Push it
all the way and your colour floods the building.

People open /crowd (from a QR code on the screen), pick a side, and tap. The
controller counts taps per side over the last two seconds - capped per phone,
so an auto-clicker can't win it alone - and moves the line by the difference
each frame. While a game runs it sits in the engine's battle slot, so it
replaces the look and the look comes back by itself afterwards, like a light
battle (battle.py).
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

Color = Tuple[int, int, int, int]

SIDES = ("left", "right")
DEFAULT_COLOURS: Dict[str, Color] = {"left": (255, 0, 140, 0), "right": (0, 170, 255, 0)}
WINDOW_S = 2.0          # tap rate is measured over this window
MAX_TAPS_PER_PHONE = 9  # per second - a fast human, not a script
# The line moves by how dominant a side is, not how big the crowd is:
# speed = SPEED * (R - L) / (R + L + DAMP). A wipe-out wins in ~8 s from the
# middle; a 55/45 crowd takes over a minute.
SPEED = 0.125
DAMP = 4.0
TIME_LIMIT_S = 120.0    # if nobody pushes it home, the side ahead wins
COUNTDOWN_S = 5.0
WIN_S = 7.0


class CrowdBattle:
    def __init__(self, colours: Optional[Dict[str, Color]] = None, time_limit: float = TIME_LIMIT_S):
        self.colours = {**DEFAULT_COLOURS, **(colours or {})}
        self.start = time.time() + COUNTDOWN_S
        self.time_limit = time_limit
        self.pos = 0.0                    # -1 = left has pushed it home, +1 = right
        self.winner: Optional[str] = None
        self.won_at: Optional[float] = None
        self._taps: Dict[str, Deque[Tuple[float, str]]] = {s: deque() for s in SIDES}
        self._phones: Dict[str, Deque[float]] = {}
        self._players: Dict[str, Tuple[str, float]] = {}  # phone -> (side, last seen)
        self._last_step = self.start
        self._lock = threading.Lock()

    # -- input ------------------------------------------------------------

    def tap(self, phone: str, side: str, count: int = 1) -> bool:
        """Taps from one phone (a page may batch a few). Returns whether
        they counted - not before the start, not after a win, and not over
        the per-phone cap."""
        if side not in SIDES:
            return False
        now = time.time()
        with self._lock:
            self._players[phone] = (side, now)
            if now < self.start or self.winner:
                return False
            recent = self._phones.setdefault(phone, deque())
            while recent and recent[0] < now - 1.0:
                recent.popleft()
            ok = 0
            for _ in range(max(0, min(count, 20))):
                if len(recent) >= MAX_TAPS_PER_PHONE:
                    break
                recent.append(now)
                self._taps[side].append((now, phone))
                ok += 1
            return ok > 0

    # -- game ---------------------------------------------------------------

    def _rates(self, now: float) -> Dict[str, float]:
        out = {}
        for s in SIDES:
            q = self._taps[s]
            while q and q[0][0] < now - WINDOW_S:
                q.popleft()
            out[s] = len(q) / WINDOW_S
        return out

    def step(self, now: float) -> None:
        """Move the line - called once per frame by the engine."""
        with self._lock:
            if self.winner or now < self.start:
                self._last_step = max(self._last_step, min(now, self.start))
                return
            dt = min(0.2, now - self._last_step)
            self._last_step = now
            r = self._rates(now)
            push = (r["right"] - r["left"]) / (r["right"] + r["left"] + DAMP)
            self.pos = max(-1.0, min(1.0, self.pos + push * SPEED * dt))
            if abs(self.pos) >= 1.0 or now - self.start >= self.time_limit:
                self.winner = "right" if self.pos > 0 else "left" if self.pos < 0 else ("left" if r["left"] >= r["right"] else "right")
                self.won_at = now

    def over(self, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        return self.won_at is not None and now - self.won_at >= WIN_S

    def status(self, now: Optional[float] = None) -> Dict[str, Any]:
        now = time.time() if now is None else now
        with self._lock:
            r = self._rates(now)
            players = {s: 0 for s in SIDES}
            for side, seen in self._players.values():
                if now - seen < 30:
                    players[side] += 1
            return {
                "style": "crowd",
                "active": not self.over(now),
                "starts_in": round(max(0.0, self.start - now), 1),
                "ends_in": round(max(0.0, self.start + self.time_limit - now), 1) if not self.winner else 0,
                "position": round(self.pos, 3),
                "rates": {s: round(v, 1) for s, v in r.items()},
                "players": players,
                "winner": self.winner,
                "colours": {s: list(c) for s, c in self.colours.items()},
            }

    def colour_at(self, x: float, now: float) -> Color:
        left, right = self.colours["left"], self.colours["right"]
        if now < self.start:
            # Countdown: both colours breathe on their halves.
            k = 0.2 + 0.6 * (0.5 - 0.5 * math.cos(2 * math.pi * (self.start - now)))
            return _scale(left if x < 0.5 else right, k)
        if self.winner:
            w = self.colours[self.winner]
            k = (now - (self.won_at or now)) / WIN_S
            sparkle = 0.3 * (0.5 + 0.5 * math.sin(now * 21 + x * 57))
            fade = 1.0 if k < 0.8 else max(0.0, (1 - k) / 0.2)
            return _scale(w, (0.7 + sparkle) * fade)
        line = 0.5 + 0.5 * self.pos
        d = x - line
        if abs(d) < 0.025:
            return (255, 255, 255, 255)
        side = left if d < 0 else right
        # Brighter right by the line, where the fight is.
        return _scale(side, 0.35 + 0.65 * max(0.0, 1 - abs(d) / 0.35))


def _scale(c: Color, k: float) -> Color:
    k = max(0.0, min(1.0, k))
    return tuple(int(round(v * k)) for v in c)  # type: ignore[return-value]


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<title>Light battle</title>
<style>
  :root { --bg:#0f0e0e; --text:#f5f5f5; --muted:#a3a1a1; --left:#ff008c; --right:#00aaff; }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  html, body { height:100%; margin:0; }
  body { background:var(--bg); color:var(--text); font:16px/1.4 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; display:flex; flex-direction:column; }
  header { padding:18px 18px 8px; text-align:center; }
  h1 { margin:0; font-size:22px; letter-spacing:.02em; }
  #msg { color:var(--muted); margin:6px 0 0; min-height:1.4em; }
  .bar { position:relative; height:28px; margin:12px 18px; border-radius:999px; overflow:hidden; background:var(--right); }
  .bar .l { position:absolute; inset:0 auto 0 0; width:50%; background:var(--left); transition:width .25s linear; }
  .bar .line { position:absolute; top:0; bottom:0; width:4px; margin-left:-2px; left:50%; background:#fff; box-shadow:0 0 12px #fff; transition:left .25s linear; }
  .sides { display:flex; gap:12px; padding:0 18px; }
  .sides button { flex:1; padding:16px; border-radius:16px; border:2px solid transparent; color:#fff; font:inherit; font-weight:700; font-size:18px; }
  #pickL { background:var(--left); } #pickR { background:var(--right); }
  .sides button[aria-pressed=true] { border-color:#fff; box-shadow:0 0 0 3px rgba(255,255,255,.25); }
  .count { display:flex; justify-content:space-between; padding:6px 22px 0; color:var(--muted); font-size:13px; }
  #tap { flex:1; margin:18px; border:none; border-radius:28px; font:inherit; font-size:40px; font-weight:800; color:#fff; background:#333; touch-action:manipulation; user-select:none; }
  #tap:active { transform:scale(.98); filter:brightness(1.2); }
  #tap[disabled] { opacity:.45; }
</style>
</head>
<body>
<header>
  <h1>Light battle</h1>
  <p id="msg">Pick a side, then tap as fast as you can!</p>
</header>
<div class="bar" aria-hidden="true"><div class="l" id="barL"></div><div class="line" id="barLine"></div></div>
<div class="count"><span id="nL">0 tapping</span><span id="nR">0 tapping</span></div>
<div class="sides">
  <button id="pickL" aria-pressed="false">Left</button>
  <button id="pickR" aria-pressed="false">Right</button>
</div>
<button id="tap" disabled>TAP!</button>
<script>
(() => {
  const $ = id => document.getElementById(id);
  let side = null, pending = 0, st = null;
  const phone = (() => { try { let k = localStorage.getItem("crowd-phone"); if (!k) { k = Math.random().toString(36).slice(2); localStorage.setItem("crowd-phone", k); } return k; } catch { return Math.random().toString(36).slice(2); } })();
  const hex = c => "#" + c.slice(0, 3).map(v => v.toString(16).padStart(2, "0")).join("");
  function pick(s) {
    side = s;
    $("pickL").setAttribute("aria-pressed", s === "left"); $("pickR").setAttribute("aria-pressed", s === "right");
    $("tap").style.background = getComputedStyle(document.documentElement).getPropertyValue(s === "left" ? "--left" : "--right");
    render();
  }
  $("pickL").onclick = () => pick("left"); $("pickR").onclick = () => pick("right");
  $("tap").addEventListener("pointerdown", e => { e.preventDefault(); if (side) { pending++; if (navigator.vibrate) navigator.vibrate(8); } });
  // Taps go up in small batches, a few times a second.
  setInterval(async () => {
    if (!pending || !side) return;
    const n = pending; pending = 0;
    try { await fetch("/api/crowd/tap", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ phone, side, count: n }) }); } catch {}
  }, 250);
  function render() {
    const tap = $("tap");
    if (!st) { $("msg").textContent = "Waiting for the next battle…"; tap.disabled = true; return; }
    document.documentElement.style.setProperty("--left", hex(st.colours.left));
    document.documentElement.style.setProperty("--right", hex(st.colours.right));
    const pct = 50 - 50 * st.position;
    $("barL").style.width = pct + "%"; $("barLine").style.left = pct + "%";
    $("nL").textContent = `${st.players.left} tapping`; $("nR").textContent = `${st.players.right} tapping`;
    if (st.winner) {
      $("msg").textContent = st.winner === side ? "Your side won! 🎉" : `${st.winner === "left" ? "Left" : "Right"} wins!`;
      tap.disabled = true; return;
    }
    if (!side) { $("msg").textContent = "Pick a side, then tap as fast as you can!"; tap.disabled = true; return; }
    if (st.starts_in > 0) { $("msg").textContent = `Get ready… ${Math.ceil(st.starts_in)}`; tap.disabled = true; return; }
    $("msg").textContent = "TAP TAP TAP!"; tap.disabled = false;
  }
  async function poll() {
    try { const r = await fetch("/api/crowd", { cache: "no-store" }); st = r.ok ? await r.json() : null; } catch { st = null; }
    render();
    setTimeout(poll, st ? 400 : 2000);
  }
  poll();
})();
</script>
</body>
</html>
"""
