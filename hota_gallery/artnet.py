"""Art-Net sending - ArtDMX packet building and a broadcast sender.

Packet structure and the broadcast-rather-than-unicast approach were proven
live tonight against the real venue nodes (two Avolites Titan Art-Net-to-
DMX gateways) before this project existed as code - see build_artdmx's
field-by-field layout, carried over unchanged.

Universe numbering here is the wire-protocol value (0-indexed), matching
what elm_import.py stores directly from the showfile's startUniverse - NOT
the "Uni. N" 1-based label consoles/ELM show in their UI. That offset-by-
one is exactly the kind of thing worth getting wrong once and never again,
so it's isolated to this one comment rather than scattered through call
sites.
"""

from __future__ import annotations

import socket
import time
from typing import Dict, List, Optional

ARTNET_PORT = 6454
ARTNET_PROTOCOL_VERSION = 14
OP_POLL = 0x2000
OP_POLL_REPLY = 0x2100


def build_artdmx(seq: int, universe: int, data: bytes) -> bytes:
    if len(data) > 512:
        raise ValueError(f"DMX universe data must be <=512 bytes, got {len(data)}")
    length = len(data)
    header = b"Art-Net\x00"
    header += bytes([0x00, 0x50])  # OpDmx (0x5000), low byte first per spec
    header += bytes([0x00, ARTNET_PROTOCOL_VERSION])
    header += bytes([seq & 0xFF])
    header += bytes([0x00])  # physical port, informational only
    header += bytes([universe & 0xFF, (universe >> 8) & 0xFF])  # SubUni, Net
    header += bytes([(length >> 8) & 0xFF, length & 0xFF])
    return header + data


def build_artpoll() -> bytes:
    """ArtPoll - a broadcast request asking every Art-Net node on the
    segment to identify itself via ArtPollReply. Only used by the Settings
    tab's "Scan for nodes" diagnostic - the sender loop itself never polls,
    it just broadcasts ArtDMX blind, the same as a real lighting console."""
    header = b"Art-Net\x00"
    header += bytes([OP_POLL & 0xFF, (OP_POLL >> 8) & 0xFF])
    header += bytes([0x00, ARTNET_PROTOCOL_VERSION])
    header += bytes([0x00])  # TalkToMe - don't request unsolicited replies on change
    header += bytes([0x00])  # Priority - all diagnostic messages
    return header


def parse_artpollreply(data: bytes) -> Optional[Dict[str, object]]:
    """Best-effort decode of the ArtPollReply fields most useful for
    fault-finding (name/MAC/reported universes) - not every field in the
    239-byte spec payload, and deliberately tolerant of a short or
    slightly nonstandard reply (returns whatever it could parse) rather
    than raising on one real node's quirky firmware."""
    if len(data) < 18 or data[:8] != b"Art-Net\x00":
        return None
    opcode = data[8] | (data[9] << 8)
    if opcode != OP_POLL_REPLY:
        return None

    def cstr(chunk: bytes) -> str:
        return chunk.split(b"\x00", 1)[0].decode("ascii", errors="replace")

    node: Dict[str, object] = {}
    try:
        node["short_name"] = cstr(data[26:44])
        node["long_name"] = cstr(data[44:108])
    except Exception:
        pass
    try:
        num_ports = data[173]
        node["num_ports"] = num_ports
        net_switch, sub_switch = data[18], data[19]
        sw_in, sw_out = data[186:190], data[190:194]
        n = min(4, num_ports)
        port_address = lambda sw: ((net_switch & 0x7F) << 8) | ((sub_switch & 0x0F) << 4) | (sw & 0x0F)
        node["universes_in"] = [port_address(sw_in[i]) for i in range(n)]
        node["universes_out"] = [port_address(sw_out[i]) for i in range(n)]
    except Exception:
        pass
    try:
        mac = data[201:207]
        if any(mac):
            node["mac"] = ":".join(f"{b:02x}" for b in mac)
    except Exception:
        pass
    return node


def discover_nodes(bind_ip: str, timeout: float = 2.0) -> List[Dict[str, object]]:
    """Broadcast ArtPoll and collect ArtPollReply packets for `timeout`
    seconds - a point-in-time snapshot of who's actually answering on the
    segment, for the Settings tab's "Scan for nodes" diagnostic. Binds to
    the real Art-Net port (not an ephemeral one) because nodes broadcast
    their reply to port 6454, not back to whatever port the poll came
    from."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind((bind_ip, ARTNET_PORT))
    sock.settimeout(0.2)
    try:
        sock.sendto(build_artpoll(), ("255.255.255.255", ARTNET_PORT))

        nodes: Dict[str, Dict[str, object]] = {}
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(1024)
            except socket.timeout:
                continue
            node = parse_artpollreply(data)
            if node is not None:
                node["ip"] = addr[0]
                nodes[addr[0]] = node
        return sorted(nodes.values(), key=lambda n: n["ip"])
    finally:
        sock.close()


class ArtNetSender:
    """Broadcasts ArtDMX frames on the local segment. One sequence counter
    per universe (each universe is logically a separate "port" per the
    spec), bound to a specific local address so sends go out the right NIC
    on a multi-homed host rather than whatever the OS default route picks -
    the exact failure mode documented in sacn2wiz's set_multicast_send_interface.
    """

    def __init__(self, bind_ip: str):
        self.bind_ip = bind_ip
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._sock.bind((bind_ip, 0))
        self._seq: Dict[int, int] = {}

    def send(self, universe: int, data: bytes) -> None:
        seq = self._seq.get(universe, 0) + 1
        if seq > 255:
            seq = 1
        self._seq[universe] = seq
        packet = build_artdmx(seq, universe, data)
        self._sock.sendto(packet, ("255.255.255.255", ARTNET_PORT))

    def send_universes(self, frames: Dict[int, bytes]) -> None:
        for universe, data in frames.items():
            self.send(universe, data)

    def close(self) -> None:
        self._sock.close()
