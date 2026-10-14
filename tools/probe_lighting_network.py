"""What's out there on the lighting network, per universe - for a universe
that "goes somewhere, nobody knows where". sACN never answers, so this asks
three things that do:

  1. Who has subscribed to each sACN universe's multicast group (an IGMP
     general query, the same one a switch or router sends - every receiver
     answers with the groups it has joined; 239.255.0.13 = universe 13).
  2. Which nodes answer an Art-Net poll (most sACN nodes do), with their
     names and the universe on each output port.
  3. What RDM fixtures each node has found on its DMX outputs (ArtTodRequest
     for the node's cached table - no fresh discovery, so nothing flickers).

Read-only: nothing here changes any output. Needs root (raw socket for
IGMP). Run on a controller that's on the lighting network:

    sudo python3 probe_lighting_network.py 10.10.20.201 [--wait 10]
"""

from __future__ import annotations

import argparse
import socket
import struct
import time
from collections import defaultdict


def checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\0"
    s = sum(struct.unpack(f"!{len(data) // 2}H", data))
    s = (s >> 16) + (s & 0xFFFF)
    s += s >> 16
    return ~s & 0xFFFF


def igmp_listeners(bind_ip: str, wait: float) -> dict:
    """Send an IGMPv2 general query; collect membership reports (v2 and v3)
    for wait seconds. Returns {group: set(reporter ips)}."""
    rx = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_IGMP)
    rx.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(bind_ip))
    rx.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
    # Ask to receive reports sent to the groups themselves (IGMPv2) and to
    # 224.0.0.22 (IGMPv3). Joining every sACN group we care about is what
    # lets the kernel hand us v2 reports addressed to them.
    for g in ["224.0.0.22"] + [f"239.255.0.{u}" for u in range(1, 64)]:
        try:
            rx.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, socket.inet_aton(g) + socket.inet_aton(bind_ip))
        except OSError:
            pass
    max_resp = int(min(wait, 10) * 10)  # tenths of a second
    q = struct.pack("!BBH4s", 0x11, max_resp, 0, socket.inet_aton("0.0.0.0"))
    q = struct.pack("!BBH4s", 0x11, max_resp, checksum(q), socket.inet_aton("0.0.0.0"))
    rx.sendto(q, ("224.0.0.1", 0))
    found = defaultdict(set)
    rx.settimeout(0.5)
    end = time.time() + wait + 1
    while time.time() < end:
        try:
            pkt, (src, _) = rx.recvfrom(2048)
        except socket.timeout:
            continue
        ihl = (pkt[0] & 0x0F) * 4
        igmp = pkt[ihl:]
        if not igmp or src == bind_ip:
            continue
        t = igmp[0]
        if t in (0x12, 0x16) and len(igmp) >= 8:          # v1 / v2 report
            found[socket.inet_ntoa(igmp[4:8])].add(src)
        elif t == 0x22 and len(igmp) >= 8:                  # v3 report
            n = struct.unpack("!H", igmp[6:8])[0]
            off = 8
            for _ in range(n):
                if off + 8 > len(igmp):
                    break
                rtype, aux, nsrc = igmp[off], igmp[off + 1], struct.unpack("!H", igmp[off + 2:off + 4])[0]
                group = socket.inet_ntoa(igmp[off + 4:off + 8])
                if rtype in (2, 4) or nsrc:                  # MODE_IS_EXCLUDE / CHANGE_TO_EXCLUDE (= joined)
                    found[group].add(src)
                off += 8 + 4 * nsrc + 4 * aux
    rx.close()
    return found


def artnet(bind_ip: str, broadcast: str, wait: float):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.bind((bind_ip, 6454))
    s.settimeout(0.3)
    poll = b"Art-Net\0" + struct.pack("<H", 0x2000) + bytes([0, 14, 0, 0])
    tod = b"Art-Net\0" + struct.pack("<H", 0x8000) + bytes([0, 14]) + bytes(9) + bytes([0, 0]) + bytes([32]) + bytes(range(32))
    for dest in (broadcast, "255.255.255.255"):
        s.sendto(poll, (dest, 6454))
    time.sleep(0.4)
    for net in range(0, 1):
        for dest in (broadcast, "255.255.255.255"):
            s.sendto(tod[:21] + bytes([net]) + tod[22:], (dest, 6454))
    nodes, tods = {}, defaultdict(list)
    end = time.time() + wait
    while time.time() < end:
        try:
            d, (ip, _) = s.recvfrom(2048)
        except socket.timeout:
            continue
        if d[:8] != b"Art-Net\0" or ip == bind_ip:
            continue
        op = struct.unpack("<H", d[8:10])[0]
        if op == 0x2100 and len(d) >= 207:
            name = d[26:44].split(b"\0")[0].decode(errors="replace")
            longn = d[44:108].split(b"\0")[0].decode(errors="replace")
            net, sub = d[18], d[19]
            ports = [((net & 0x7F) << 8) | ((sub & 0x0F) << 4) | (b & 0x0F) for b in d[190:194]]
            nports = min(4, max(1, d[173]))
            n = nodes.setdefault(ip, {"name": name, "long": longn, "out": []})
            n["out"] += ports[:nports]
        elif op == 0x8100 and len(d) >= 28:
            port_addr = (d[21] << 8) | d[23]
            count = d[27]
            uids = [d[28 + 6 * i:34 + 6 * i].hex(":") for i in range(count)]
            tods[(ip, port_addr)] += uids
    s.close()
    return nodes, tods


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bind_ip", help="this controller's address on the lighting network")
    ap.add_argument("--wait", type=float, default=10)
    ap.add_argument("--broadcast", default=None, help="subnet broadcast, e.g. 10.10.255.255")
    a = ap.parse_args()
    bcast = a.broadcast or ".".join(a.bind_ip.split(".")[:2]) + ".255.255"

    print(f"== 1. sACN listeners (IGMP query from {a.bind_ip}, {a.wait:.0f} s for answers)")
    groups = igmp_listeners(a.bind_ip, a.wait)
    sacn = {int(g.split(".")[3]) + 256 * int(g.split(".")[2]): ips for g, ips in groups.items() if g.startswith("239.255.")}
    for u in sorted(sacn):
        print(f"   universe {u:>3} (239.255.{u >> 8}.{u & 255}): {', '.join(sorted(sacn[u]))}")
    if not sacn:
        print("   nobody reported joining any sACN universe")
    other = {g: ips for g, ips in groups.items() if not g.startswith("239.255.")}
    if other:
        print("   (other multicast groups:", ", ".join(f"{g} <- {len(i)}" for g, i in sorted(other.items())), ")")

    print(f"\n== 2./3. Art-Net nodes and their RDM fixtures (poll + table-of-devices, {min(a.wait, 4):.0f} s)")
    nodes, tods = artnet(a.bind_ip, bcast, min(a.wait, 4))
    for ip, n in sorted(nodes.items()):
        print(f"   {ip:<15} {n['name']!r} ({n['long']}) - outputs: {', '.join(str(p) for p in n['out'])} (Art-Net numbering, 0 = universe 1)")
        for (tip, pa), uids in sorted(tods.items()):
            if tip == ip:
                print(f"      port-address {pa}: {len(uids)} RDM device(s) {' '.join(uids[:12])}{' ...' if len(uids) > 12 else ''}")
    if not nodes:
        print("   no Art-Net nodes answered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
