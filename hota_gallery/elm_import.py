"""One-time importer: ELM showfile XML -> this project's native fixture list.

ELM's <fixture> elements nest arbitrarily deep under `isComposite="True"`
wrapper fixtures (a composite groups several real fixtures under one label,
e.g. "RGB Dish Lights" wrapping 25 individual dish fixtures). Wrapper
fixtures carry no ledType/dmxProtocol/geometry of their own - only leaf
fixtures (the ones actually driving DMX) do. `Element.iter("fixture")`
walks every fixture at any depth in one pass, so filtering to elements that
have both a direct <geometry> child and a dmxProtocol attribute gets every
real fixture regardless of composite nesting, without needing to recurse by
hand.

Channel width is derived from ledType (RGBW=4, RGB=3) rather than hardcoded,
since the showfile mixes both (the dish lights are RGB, the facade strips
are RGBW) - confirmed against the patch.html export's DMX start/end spans
(e.g. 10-LED RGBW fixture spans 40 channels = 4/LED; 1-LED RGB spans 3).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import List, Tuple

CHANNELS_PER_LED = {"RGBW": 4, "RGB": 3}


@dataclass
class Fixture:
    stage: str  # which ELM "stage" this came from, e.g. "HOTA Gallery"
    name: str  # ELM's `group` attribute - the fixture's own label
    group2: str  # ELM's `group2` - informational universe label, not authoritative
    led_type: str  # "RGBW" or "RGB"
    universe: int  # 0-indexed, as stored in the file
    address: int  # 0-indexed start address within that universe
    led_count: int
    channels_per_led: int
    rotation: float
    points: Tuple[Tuple[float, float], Tuple[float, float]]  # the two geometry points

    @property
    def channel_count(self) -> int:
        return self.led_count * self.channels_per_led

    @property
    def end_address(self) -> int:
        """Exclusive - address + channel_count, matching how DMX spans are checked."""
        return self.address + self.channel_count


def _parse_points(geometry: ET.Element) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    pts = geometry.findall("points/point")
    if len(pts) != 2:
        raise ValueError(f"expected exactly 2 <point> elements, got {len(pts)}")
    return tuple((float(p.attrib["x"]), float(p.attrib["y"])) for p in pts)  # type: ignore[return-value]


def import_elm(path: str) -> List[Fixture]:
    """Parse an ELM .elm showfile and return every real (leaf) fixture.

    Raises ValueError with every problem found (not just the first) if any
    fixture is missing required attributes - same "collect everything,
    don't fail on the first typo" philosophy as sacn2wiz's config.py,
    useful here since a showfile this size (180+ fixtures) makes
    one-error-at-a-time painful to debug.
    """
    tree = ET.parse(path)
    root = tree.getroot()

    fixtures: List[Fixture] = []
    errors: List[str] = []

    for stage in root.iter("stage"):
        stage_name = stage.attrib.get("name", "").strip()
        for fx in stage.iter("fixture"):
            geometry = fx.find("geometry")
            if geometry is None or "dmxProtocol" not in fx.attrib:
                continue  # composite wrapper, not a real fixture - skip, recursion already happened via .iter()

            where = f"stage {stage_name!r} fixture {fx.attrib.get('group', '?')!r}"
            try:
                led_type = fx.attrib["ledType"]
                if led_type not in CHANNELS_PER_LED:
                    errors.append(f"{where}: unknown ledType {led_type!r}")
                    continue
                fixtures.append(
                    Fixture(
                        stage=stage_name,
                        name=fx.attrib.get("group", ""),
                        group2=fx.attrib.get("group2", ""),
                        led_type=led_type,
                        universe=int(fx.attrib["startUniverse"]),
                        address=int(fx.attrib["startAddress"]),
                        led_count=int(geometry.attrib["nbLeds"]),
                        channels_per_led=CHANNELS_PER_LED[led_type],
                        rotation=float(geometry.attrib.get("rotation", 0)),
                        points=_parse_points(geometry),
                    )
                )
            except (KeyError, ValueError) as exc:
                errors.append(f"{where}: {exc}")

    if errors:
        raise ValueError("ELM import found problems:\n" + "\n".join(f"  - {e}" for e in errors))

    return fixtures


def find_overlaps(fixtures: List[Fixture]) -> List[Tuple[Fixture, Fixture]]:
    """Pairs of fixtures on the same universe whose address ranges overlap.

    Not necessarily a bug - sacn2wiz's config.py treats shared addresses as
    deliberate zone-ganging, and ELM re-patches the same physical dish
    fixtures under two different "stages" (HOTA Gallery vs HOTA Gallery
    Dishes) for independent scheduling, which looks like an address clash
    here but is actually the same hardware addressed twice on purpose.
    Surfaced separately from summarize() so real duplicates (genuinely
    different fixtures contending for the same channels) can be told apart
    from that expected same-fixture-two-stages case.
    """
    by_universe: dict[int, List[Fixture]] = {}
    for fx in fixtures:
        by_universe.setdefault(fx.universe, []).append(fx)

    overlaps = []
    for group in by_universe.values():
        ordered = sorted(group, key=lambda fx: fx.address)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1 :]:
                if b.address >= a.end_address:
                    break  # sorted by address - nothing further can overlap `a`
                overlaps.append((a, b))
    return overlaps


GENERIC_NAME = "RGBW01"


def _cluster_overlaps(fixtures: List[Fixture]) -> List[List[Fixture]]:
    """Connected components over the overlap graph - small fixture counts
    per universe make a plain BFS plenty fast, no need for union-find."""
    overlaps = find_overlaps(fixtures)
    adjacency: dict[int, set] = {}
    involved = set()
    for a, b in overlaps:
        involved.add(id(a))
        involved.add(id(b))
        adjacency.setdefault(id(a), set()).add(id(b))
        adjacency.setdefault(id(b), set()).add(id(a))

    by_id = {id(fx): fx for fx in fixtures}
    seen = set()
    clusters = []
    for fx in fixtures:
        if id(fx) not in involved or id(fx) in seen:
            continue
        stack = [id(fx)]
        cluster_ids = set()
        while stack:
            cur = stack.pop()
            if cur in cluster_ids:
                continue
            cluster_ids.add(cur)
            seen.add(cur)
            stack.extend(adjacency.get(cur, set()) - cluster_ids)
        clusters.append([by_id[i] for i in cluster_ids])
    return clusters


def resolve_overlaps(fixtures: List[Fixture]) -> Tuple[List[Fixture], List[str]]:
    """Clean up overlapping fixtures per the rules confirmed against the
    real installation (the "random mis-colored pixel on a few bars"
    symptom traced back to exactly this): within each overlapping cluster,
    a specifically-named fixture wins over the generic, never-renamed
    "RGBW01" placeholder; if an entire cluster shares that generic name,
    keep only the largest span (the real patched run) and drop the smaller
    ones nested inside it - those are leftover stray patches, not
    intentional layering. A same-address pair that only differs by being
    patched under two different ELM stages (the dish lights, scheduled
    independently under "HOTA Gallery Dishes") is merged into one
    canonical fixture rather than dropped - that's the same physical
    hardware, not a stray duplicate.

    Returns (clean_fixtures, human-readable report lines).
    """
    to_drop: set = set()
    report: List[str] = []

    for cluster in _cluster_overlaps(fixtures):
        if (
            len(cluster) == 2
            and cluster[0].address == cluster[1].address
            and cluster[0].led_type == cluster[1].led_type
            and cluster[0].led_count == cluster[1].led_count
            and cluster[0].stage != cluster[1].stage
        ):
            keep, drop = cluster
            to_drop.add(id(drop))
            report.append(
                f"merged duplicate: uni {keep.universe} addr {keep.address} {keep.name!r} "
                f"(same fixture patched under both {keep.stage!r} and {drop.stage!r})"
            )
            continue

        named = [fx for fx in cluster if fx.name != GENERIC_NAME]
        generic = [fx for fx in cluster if fx.name == GENERIC_NAME]
        if named:
            for fx in generic:
                to_drop.add(id(fx))
                report.append(
                    f"dropped stray duplicate: uni {fx.universe} addr {fx.address}-{fx.end_address - 1} "
                    f"{fx.name!r} (overlapped named fixture(s) {', '.join(n.name for n in named)})"
                )
        else:
            keep = max(cluster, key=lambda fx: fx.channel_count)
            for fx in cluster:
                if fx is not keep:
                    to_drop.add(id(fx))
                    report.append(
                        f"dropped stray duplicate: uni {fx.universe} addr {fx.address}-{fx.end_address - 1} "
                        f"{fx.name!r} (nested inside larger {keep.name!r} "
                        f"addr {keep.address}-{keep.end_address - 1})"
                    )

    clean = [fx for fx in fixtures if id(fx) not in to_drop]
    return clean, report


def summarize(fixtures: List[Fixture]) -> str:
    """Human-readable per-universe breakdown plus any overlap findings, for
    sanity-checking an import against the patch.html export before trusting
    it."""
    lines = [f"{len(fixtures)} fixtures imported"]
    by_universe: dict[int, List[Fixture]] = {}
    for fx in fixtures:
        by_universe.setdefault(fx.universe, []).append(fx)
    for universe in sorted(by_universe):
        group = by_universe[universe]
        total_channels = sum(fx.channel_count for fx in group)
        max_end = max(fx.end_address for fx in group)
        flag = " <- overlap present, see below" if total_channels > max_end else ""
        lines.append(
            f"  universe {universe:2d} (displayed 'Uni. {universe + 1}'): "
            f"{len(group):3d} fixtures, {total_channels:3d} channels used, "
            f"highest end address {max_end}{flag}"
        )

    overlaps = find_overlaps(fixtures)
    if overlaps:
        lines.append(f"\n{len(overlaps)} overlapping fixture pair(s):")
        for a, b in overlaps:
            # Same DMX identity (universe+address+type) is what actually makes
            # two entries "the same physical fixture" - each ELM stage is an
            # independent 2D canvas, so matching (x,y) points is NOT a safe
            # test: the same fixture can be dragged to a different spot on a
            # second stage's drawing without being a different fixture.
            same_fixture = (
                a.stage != b.stage
                and a.address == b.address
                and a.led_type == b.led_type
                and a.led_count == b.led_count
            )
            note = " (same physical fixture, different ELM stage)" if same_fixture else " *** CHECK THIS ***"
            lines.append(
                f"  uni {a.universe} addr {a.address}-{a.end_address - 1} {a.stage}/{a.name!r} "
                f"<-> addr {b.address}-{b.end_address - 1} {b.stage}/{b.name!r}{note}"
            )
    return "\n".join(lines)
