"""Light battle: an automatic game of ping-pong between the lighting
controllers (gallery, outdoor stage, building...).

The controller that starts a battle works the whole match out up front from
a random seed - every shot, return, miss and point - and sends that script
to the others (webapp.Store.start_battle). Each controller then plays its own
part of the same script against its own clock, so nothing has to stay in
touch during the match and a network hiccup can't freeze it halfway.

The game: each player is a building with its own colour. The ball is a burst
of light with a tail that crosses one building's fixtures; when it reaches
the end that building returns it (a flash) and it appears at the next player,
round in order. Every return is a little faster, and the longer the rally the
likelier a miss. A miss: the ball flies off the end, that building strobes
red, and the player who hit it last scores - everyone pulses in the scorer's
colour. First to `points_to_win` takes the finale: every building floods in
the winner's colour with a sparkle, then they all go back to their own look.

Colours are (r, g, b, w) tuples, the engine's own convention.
"""

from __future__ import annotations

import math
import random
import time
from typing import Any, Dict, List, Optional, Tuple

Color = Tuple[int, int, int, int]

DEFAULT_COLOURS = {
    "gallery": (255, 0, 140, 0),
    "vos": (0, 170, 255, 0),
    "building": (255, 130, 0, 0),
}
SPARE_COLOURS = [(120, 255, 0, 0), (180, 0, 255, 0), (255, 255, 0, 0), (0, 255, 160, 0)]
MISS_RED: Color = (255, 0, 0, 0)

COUNTDOWN_S = 3.0      # all buildings breathe in their colours before the serve
POINT_S = 2.4          # scorer's colour pulses everywhere after a miss
FINALE_S = 8.0
FIRST_SHOT_S = 1.8     # time for the ball to cross a building at the start of a rally
SPEEDUP = 0.88         # each return
FASTEST_S = 0.45
SERVE_GAP_S = 0.6


def make_match(players: List[Dict[str, Any]], points_to_win: int = 3, seed: Optional[int] = None) -> Dict[str, Any]:
    """The whole match as a script. players: [{"name", "colour": [r,g,b,w]}]
    in playing order. Times are seconds from the start."""
    if len(players) < 2:
        raise ValueError("a light battle needs at least two controllers")
    if not 1 <= points_to_win <= 9:
        raise ValueError("points to win must be 1-9")
    seed = random.randrange(1 << 30) if seed is None else seed
    rng = random.Random(seed)
    n = len(players)
    score = [0] * n
    shots: List[Dict[str, Any]] = []
    points: List[Dict[str, Any]] = []
    t = COUNTDOWN_S
    server = rng.randrange(n)
    while max(score) < points_to_win:
        p, dur, hits = server, FIRST_SHOT_S, 0
        last_hitter = None
        while True:
            # The ball crosses p's building; p returns it - or doesn't.
            miss_chance = 0.0 if hits < 2 else min(0.75, 0.06 + 0.035 * hits)
            missed = rng.random() < miss_chance
            shots.append({"t0": round(t, 3), "t1": round(t + dur, 3), "player": p, "dir": rng.choice((1, -1)), "miss": missed})
            t += dur
            if missed:
                winner = last_hitter if last_hitter is not None else (p - 1) % n
                score[winner] += 1
                points.append({"t0": round(t + 0.9, 3), "t1": round(t + 0.9 + POINT_S, 3), "winner": winner, "loser": p, "score": list(score)})
                t += 0.9 + POINT_S + SERVE_GAP_S
                server = p  # whoever missed serves next
                break
            last_hitter = p
            hits += 1
            p = (p + 1) % n
            dur = max(FASTEST_S, dur * SPEEDUP)
    winner = score.index(max(score))
    return {
        "style": "pingpong",
        "seed": seed,
        "players": players,
        "points_to_win": points_to_win,
        "shots": shots,
        "points": points,
        "finale": {"t0": round(t, 3), "t1": round(t + FINALE_S, 3), "winner": winner},
        "duration": round(t + FINALE_S, 3),
        "score": score,
    }


# -- call and response ------------------------------------------------------
#
# The buildings take turns: one plays a move in its colour, the next answers
# with a faster, denser one, round after round, then everyone plays together
# trading colours on the beat. No winner - it's a duet (or trio) that builds.

MOVES = ("wipe", "chase", "strobe", "pulse", "sparkle", "burst")
CALL_ROUNDS = 4
CALL_FINALE_S = 9.0


def make_call_response(players: List[Dict[str, Any]], rounds: int = CALL_ROUNDS, seed: Optional[int] = None) -> Dict[str, Any]:
    if len(players) < 2:
        raise ValueError("a light battle needs at least two controllers")
    if not 1 <= rounds <= 8:
        raise ValueError("rounds must be 1-8")
    seed = random.randrange(1 << 30) if seed is None else seed
    rng = random.Random(seed)
    n = len(players)
    turns: List[Dict[str, Any]] = []
    t = COUNTDOWN_S
    total = rounds * n
    last = None
    for k in range(total):
        level = (k + 1) / total                       # 0 -> 1 across the battle
        move = rng.choice([m for m in MOVES if m != last])
        last = move
        dur = 3.6 - 1.2 * level                        # moves get shorter as they get wilder
        turns.append({"t0": round(t, 3), "t1": round(t + dur, 3), "player": k % n, "move": move,
                      "level": round(level, 3), "dir": rng.choice((1, -1))})
        t += dur + 0.35
    return {
        "style": "call",
        "seed": seed,
        "players": players,
        "rounds": rounds,
        "turns": turns,
        "finale": {"t0": round(t, 3), "t1": round(t + CALL_FINALE_S, 3), "winner": None},
        "points": [],
        "shots": [],
        "duration": round(t + CALL_FINALE_S, 3),
        "score": [0] * n,
    }


def _move(move: str, x: float, t: float, level: float, direction: int, seed: int) -> float:
    """Brightness 0..1 of one move at x (0..1 across the building), t seconds in."""
    xd = x if direction > 0 else 1 - x
    if move == "wipe":
        k = min(1.0, t / max(0.6, 1.6 - level))
        return 1.0 if xd <= k else 0.0
    if move == "chase":
        bands, speed = 2 + 5 * level, 0.8 + 2.4 * level
        return 1.0 if (xd * bands - t * speed) % 1.0 < 0.35 else 0.0
    if move == "strobe":
        hz = 3 + 9 * level
        return 1.0 if (t * hz) % 1.0 < 0.3 else 0.0
    if move == "pulse":
        hz = 0.8 + 2.2 * level
        return 0.5 - 0.5 * math.cos(2 * math.pi * t * hz)
    if move == "sparkle":
        step = int(t * (6 + 14 * level))
        h = math.sin((round(x * 997) + step * 7919 + seed) * 12.9898) * 43758.5453
        return 1.0 if h - math.floor(h) < 0.25 + 0.35 * level else 0.08
    if move == "burst":
        bands, speed = 1 + 3 * level, 1.0 + 2.0 * level
        return 1.0 if (abs(x - 0.5) * 2 * bands - t * speed) % 1.0 < 0.4 else 0.0
    return 0.0


def _scale(c: Color, k: float) -> Color:
    k = max(0.0, min(1.0, k))
    return tuple(int(round(v * k)) for v in c)  # type: ignore[return-value]


def _add(a: Color, b: Color) -> Color:
    return tuple(min(255, x + y) for x, y in zip(a, b))  # type: ignore[return-value]


class Battle:
    """One controller's part in a match: which player it is, and when the
    match started on this controller's clock."""

    def __init__(self, match: Dict[str, Any], me: int, start: float):
        self.match = match
        self.me = me
        self.start = start
        self.colours: List[Color] = [tuple(p["colour"]) for p in match["players"]]  # type: ignore[misc]

    def elapsed(self, now: Optional[float] = None) -> float:
        return (time.time() if now is None else now) - self.start

    def over(self, now: Optional[float] = None) -> bool:
        return self.elapsed(now) >= self.match["duration"]

    def status(self, now: Optional[float] = None) -> Dict[str, Any]:
        e = self.elapsed(now)
        done = [p for p in self.match["points"] if p["t0"] <= e]
        turn = next((t["player"] for t in self.match.get("turns", []) if t["t0"] <= e < t["t1"]), None)
        return {
            "style": self.match.get("style", "pingpong"),
            "turn": turn,
            "finale": e >= self.match["finale"]["t0"],
            "active": 0 <= e < self.match["duration"],
            "starts_in": round(max(0.0, -e), 1),
            "ends_in": round(max(0.0, self.match["duration"] - e), 1),
            "me": self.me,
            "players": [p["name"] for p in self.match["players"]],
            "score": done[-1]["score"] if done else [0] * len(self.match["players"]),
            "winner": self.match["finale"]["winner"] if e >= self.match["finale"]["t0"] else None,
        }

    def colour_at(self, x: float, now: float) -> Color:
        """This controller's colour for a fixture at x (0..1 across the
        building) at wall-clock time now."""
        e = now - self.start
        m, mine = self.match, self.colours[self.me]
        if e < 0:
            return _scale(mine, 0.06)
        if m.get("style") == "call":
            return self._call_colour(x, e)
        # Countdown: everyone breathes in their own colour, three times.
        if e < COUNTDOWN_S:
            return _scale(mine, 0.15 + 0.6 * (0.5 - 0.5 * math.cos(2 * math.pi * e)))
        fin = m["finale"]
        if e >= fin["t0"]:
            w = self.colours[fin["winner"]]
            k = (e - fin["t0"]) / (fin["t1"] - fin["t0"])
            # Winner floods in from the centre, then sparkles, then fades.
            reach = min(1.0, k * 4)
            base = 1.0 if abs(x - 0.5) * 2 <= reach else 0.15
            sparkle = 0.35 * (0.5 + 0.5 * math.sin(e * 23 + x * 61)) if k > 0.25 else 0.0
            fade = 1.0 if k < 0.8 else (1 - k) / 0.2
            return _add(_scale(w, base * fade * (1 - sparkle * 0.5)), _scale((255, 255, 255, 255), sparkle * 0.25 * fade))
        for pt in m["points"]:
            if pt["t0"] <= e < pt["t1"]:
                k = (e - pt["t0"]) / (pt["t1"] - pt["t0"])
                return _scale(self.colours[pt["winner"]], 0.2 + 0.8 * abs(math.sin(k * 3 * math.pi)))
        court = _scale(mine, 0.08)
        for s in m["shots"]:
            if s["player"] != self.me:
                continue
            if s["t0"] <= e < s["t1"]:
                k = (e - s["t0"]) / (s["t1"] - s["t0"])
                pos = k if s["dir"] > 0 else 1 - k
                # Ball with a tail behind it.
                d = (x - pos) * s["dir"]
                if -0.05 <= d <= 0.03:
                    ball = 1.0
                elif d < -0.05:
                    ball = max(0.0, 1 + (d + 0.05) / 0.3) ** 2
                else:
                    ball = 0.0
                return _add(court, _scale(mine, ball))
            # Just after the ball leaves: a return flash, or the miss strobe.
            if s["t1"] <= e < s["t1"] + (0.9 if s["miss"] else 0.18):
                if s["miss"]:
                    on = int((e - s["t1"]) * 10) % 2 == 0
                    return MISS_RED if on else (0, 0, 0, 0)
                return _scale(mine, 1 - (e - s["t1"]) / 0.18)
        return court


    def _call_colour(self, x: float, e: float) -> Color:
        m, mine = self.match, self.colours[self.me]
        if e < COUNTDOWN_S:
            return _scale(mine, 0.15 + 0.6 * (0.5 - 0.5 * math.cos(2 * math.pi * e)))
        fin = m["finale"]
        if e >= fin["t0"]:
            # Everyone together, trading colours on the beat, fading at the end.
            k = (e - fin["t0"]) / (fin["t1"] - fin["t0"])
            beat = int((e - fin["t0"]) / 0.45)
            c = self.colours[(beat + self.me) % len(self.colours)]
            b = _move("chase" if beat % 2 else "burst", x, e - fin["t0"], 1.0, 1, m["seed"])
            fade = 1.0 if k < 0.85 else (1 - k) / 0.15
            return _scale(c, (0.25 + 0.75 * b) * fade)
        for tn in m["turns"]:
            if tn["t0"] <= e < tn["t1"]:
                if tn["player"] == self.me:
                    b = _move(tn["move"], x, e - tn["t0"], tn["level"], tn["dir"], m["seed"])
                    flash = 0.35 * tn["level"] if b > 0.9 else 0.0   # white edge on the wildest moves
                    return _add(_scale(mine, 0.06 + 0.94 * b), _scale((255, 255, 255, 255), flash))
                # Listening: a slow shimmer in our colour while the other plays.
                return _scale(mine, 0.06 + 0.04 * math.sin(e * 3 + x * 9))
        return _scale(mine, 0.08)


def make(style: str, players: List[Dict[str, Any]], points_to_win: int = 3) -> Dict[str, Any]:
    """Either battle style's script."""
    if style == "call":
        return make_call_response(players)
    return make_match(players, points_to_win)


def colours_for(players_meta: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Give each player a colour: its venue's default, else a spare one."""
    out, spare, used = [], list(SPARE_COLOURS), set()
    for p in players_meta:
        c = tuple(p.get("colour") or DEFAULT_COLOURS.get(p.get("venue") or "", ()))
        if not c or c in used:
            c = spare.pop(0) if spare else (255, 255, 255, 0)
        used.add(c)
        out.append({"name": p["name"], "venue": p.get("venue"), "colour": list(c)})
    return out
