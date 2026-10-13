"""sACN (ANSI E1.31) sending - the outdoor stage (VOS) protocol, alongside
artnet.py for the gallery. Same shape as ArtNetSender (send_universes /
close) so the engine can use either.

Universe numbering: config.json stores universes 0-indexed, the same as
for Art-Net (see artnet.py), and the UI shows them +1. sACN universes are
1-based on the wire (1-63999, 0 is reserved), so the wire value is
stored + 1. That makes the number on the wire the same number the UI and
the console show: the MVR's "universe 11" is stored as 10 and sent as
sACN universe 11.

By default each universe goes to its standard multicast group
(239.255.hi.lo, port 5568). `destinations` adds unicast targets for nodes
that don't join multicast groups; every universe goes to each of them too.
"""

from __future__ import annotations

import logging
import socket
import time
import uuid
from typing import Dict, Iterable, List, Optional

log = logging.getLogger(__name__)

SACN_PORT = 5568
DEFAULT_PRIORITY = 100

_ACN_ID = b"ASC-E1.17\x00\x00\x00"
_VECTOR_ROOT_E131_DATA = 0x00000004
_VECTOR_E131_DATA_PACKET = 0x00000002
_VECTOR_DMP_SET_PROPERTY = 0x02
_OPT_STREAM_TERMINATED = 0x40


def multicast_group(wire_universe: int) -> str:
    return f"239.255.{(wire_universe >> 8) & 0xFF}.{wire_universe & 0xFF}"


def _flags_length(length: int) -> bytes:
    v = 0x7000 | (length & 0x0FFF)
    return bytes([v >> 8, v & 0xFF])


def build_e131(
    cid: bytes,
    source_name: str,
    wire_universe: int,
    seq: int,
    data: bytes,
    priority: int = DEFAULT_PRIORITY,
    options: int = 0,
) -> bytes:
    """One E1.31 data packet: root layer, framing layer, DMP layer, then
    start code 0 and the slot data. Offsets follow E1.31-2018 table 4-1."""
    if len(cid) != 16:
        raise ValueError("CID must be 16 bytes")
    if len(data) > 512:
        raise ValueError(f"DMX universe data must be <=512 bytes, got {len(data)}")
    if not 1 <= wire_universe <= 63999:
        raise ValueError(f"sACN universe must be 1-63999, got {wire_universe}")
    n = len(data)
    total = 126 + n
    name = source_name.encode("utf-8")[:63].ljust(64, b"\x00")

    root = b"\x00\x10\x00\x00" + _ACN_ID + _flags_length(total - 16) + _VECTOR_ROOT_E131_DATA.to_bytes(4, "big") + cid
    framing = (
        _flags_length(total - 38)
        + _VECTOR_E131_DATA_PACKET.to_bytes(4, "big")
        + name
        + bytes([priority & 0xFF])
        + b"\x00\x00"  # synchronization address: none
        + bytes([seq & 0xFF])
        + bytes([options & 0xFF])
        + wire_universe.to_bytes(2, "big")
    )
    dmp = (
        _flags_length(total - 115)
        + bytes([_VECTOR_DMP_SET_PROPERTY, 0xA1])
        + b"\x00\x00"  # first property address
        + b"\x00\x01"  # address increment
        + (n + 1).to_bytes(2, "big")
        + b"\x00"  # DMX start code
    )
    return root + framing + dmp + bytes(data)


class SacnSender:
    """Sends E1.31 frames out of one local address. One sequence counter
    per universe, as the spec asks. The CID identifies this controller to
    receivers; it is fixed per process, which is all a receiver needs to
    tell one source from another."""

    def __init__(
        self,
        bind_ip: str,
        source_name: str = "HOTA lighting",
        priority: int = DEFAULT_PRIORITY,
        destinations: Optional[Iterable[str]] = None,
        multicast: bool = True,
    ):
        self.bind_ip = bind_ip
        self.source_name = source_name
        self.priority = priority
        self.destinations: List[str] = list(destinations or [])
        self.multicast = multicast
        self.cid = uuid.uuid4().bytes
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 8)
        # Pin multicast to the lighting interface, or a multi-homed Pi
        # sends it out whichever card the default route picks (the WiFi).
        self._sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(bind_ip))
        self._sock.bind((bind_ip, 0))
        self._seq: Dict[int, int] = {}
        self._sent: set = set()
        self._last_error = 0.0

    def _targets(self, wire_universe: int) -> List[str]:
        out = [multicast_group(wire_universe)] if self.multicast else []
        return out + self.destinations

    def send(self, universe: int, data: bytes, options: int = 0) -> None:
        wire = universe + 1
        seq = (self._seq.get(wire, -1) + 1) & 0xFF
        self._seq[wire] = seq
        packet = build_e131(self.cid, self.source_name, wire, seq, data, self.priority, options)
        for ip in self._targets(wire):
            try:
                self._sock.sendto(packet, (ip, SACN_PORT))
            except OSError as e:
                # A cable out or the link not up yet at boot: keep the
                # sender running so output resumes on its own, and log
                # at most every 10 s rather than 30 times a second.
                now = time.monotonic()
                if now - self._last_error > 10:
                    self._last_error = now
                    log.warning("sACN send to %s (universe %d) failed: %s", ip, wire, e)
        self._sent.add(universe)

    def send_universes(self, frames: Dict[int, bytes]) -> None:
        for universe, data in frames.items():
            self.send(universe, data)

    def close(self) -> None:
        """Tell receivers this source is going away (three packets with the
        Stream_Terminated bit, per spec), so they drop to their next source
        or hold-last-look straight away instead of after a 2.5 s timeout."""
        try:
            for _ in range(3):
                for universe in sorted(self._sent):
                    self.send(universe, bytes(512), options=_OPT_STREAM_TERMINATED)
        except OSError:
            pass
        self._sock.close()
