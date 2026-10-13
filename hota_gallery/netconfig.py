"""Lighting network settings: give a wired port a fixed address for sACN /
Art-Net, from the web UI, through NetworkManager (nmcli).

The controller runs as an unprivileged user; install-vos.sh adds a polkit
rule that lets that one user change NetworkManager connections and nothing
else. The address lives in its own connection profile, "hota-lighting":

  - fixed IPv4 address, no gateway, never the default route - so internet
    and the Wi-Fi staff connect over are untouched;
  - tied to one wired interface, autoconnect with top priority - so it
    survives reboots and comes up by itself when the cable goes in.

Wi-Fi interfaces are refused outright, and the API refuses the interface a
request arrived on (webapp.Store.set_lighting_network), so this can't cut
off the person using it.
"""

from __future__ import annotations

import ipaddress
import json
import shutil
import subprocess
from typing import Any, Dict, List, Optional

PROFILE = "hota-lighting"


class NetConfigError(Exception):
    pass


def available() -> bool:
    return shutil.which("nmcli") is not None


def _run(args: List[str], timeout: float = 15) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def _addresses() -> Dict[str, List[str]]:
    """Interface -> IPv4 addresses with prefix, e.g. {"enp1s0": ["2.0.0.10/8"]}."""
    try:
        out = _run(["ip", "-j", "-4", "addr", "show"]).stdout
        return {i["ifname"]: [f"{a['local']}/{a['prefixlen']}" for a in i.get("addr_info", [])] for i in json.loads(out or "[]")}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def interfaces() -> List[Dict[str, Any]]:
    """Ethernet and Wi-Fi interfaces NetworkManager knows about."""
    if not available():
        return []
    res = _run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"])
    addrs = _addresses()
    out = []
    for line in res.stdout.splitlines():
        parts = line.replace("\\:", "\x00").split(":")
        if len(parts) < 4:
            continue
        dev, typ, state, conn = (p.replace("\x00", ":") for p in parts[:4])
        if typ not in ("ethernet", "wifi"):
            continue
        out.append({
            "name": dev,
            "type": typ,
            "state": state,                      # connected / disconnected / unavailable (no cable)
            "cable": state != "unavailable" if typ == "ethernet" else None,
            "connection": conn or None,
            "addresses": addrs.get(dev, []),
        })
    return out


def current() -> Optional[Dict[str, Any]]:
    """The hota-lighting profile's settings, or None if there isn't one."""
    if not available():
        return None
    res = _run(["nmcli", "-t", "-g", "connection.interface-name,ipv4.addresses", "connection", "show", PROFILE])
    if res.returncode != 0:
        return None
    lines = res.stdout.splitlines()
    iface = lines[0] if lines else ""
    addr = (lines[1] if len(lines) > 1 else "").split(",")[0].strip()
    if "/" not in addr:
        return {"interface": iface, "address": None, "prefix": None}
    ip, prefix = addr.split("/", 1)
    return {"interface": iface, "address": ip, "prefix": int(prefix)}


def validate(interface: Any, address: Any, prefix: Any) -> ipaddress.IPv4Interface:
    if not isinstance(interface, str) or not interface:
        raise NetConfigError("pick a network port")
    try:
        net = ipaddress.IPv4Interface(f"{address}/{int(prefix)}")
    except (ValueError, TypeError):
        raise NetConfigError(f"{address!r} / {prefix!r} isn't a valid IPv4 address and subnet")
    if not 1 <= net.network.prefixlen <= 30:
        raise NetConfigError("the subnet must be between /1 and /30")
    if net.ip in (net.network.network_address, net.network.broadcast_address):
        raise NetConfigError(f"{net.ip} is the network's own {'network' if net.ip == net.network.network_address else 'broadcast'} address - pick another")
    if net.ip.is_loopback or net.ip.is_multicast or net.ip.is_unspecified:
        raise NetConfigError(f"{net.ip} can't be used as a port's address")
    return net


def apply(interface: str, address: str, prefix: int) -> Dict[str, Any]:
    """Create or update the hota-lighting profile and bring it up. With no
    cable in, the profile is saved and comes up when one is plugged in."""
    if not available():
        raise NetConfigError("NetworkManager (nmcli) isn't installed on this controller")
    net = validate(interface, address, prefix)
    known = {i["name"]: i for i in interfaces()}
    if interface not in known:
        raise NetConfigError(f"there's no network port called {interface!r}")
    if known[interface]["type"] != "ethernet":
        raise NetConfigError(f"{interface} is Wi-Fi - the lighting network has to be a wired port")

    settings = [
        "connection.interface-name", interface,
        "connection.autoconnect", "yes",
        "connection.autoconnect-priority", "100",
        "ipv4.method", "manual",
        "ipv4.addresses", str(net.with_prefixlen),
        "ipv4.gateway", "",
        "ipv4.dns", "",
        "ipv4.never-default", "yes",
        "ipv6.method", "disabled",
    ]
    exists = _run(["nmcli", "-t", "connection", "show", PROFILE]).returncode == 0
    if exists:
        res = _run(["nmcli", "connection", "modify", PROFILE, *settings])
    else:
        res = _run(["nmcli", "connection", "add", "type", "ethernet", "con-name", PROFILE, *settings])
    if res.returncode != 0:
        msg = (res.stderr or res.stdout).strip()
        if "not authorized" in msg.lower() or "insufficient privileges" in msg.lower():
            msg += " - the controller isn't allowed to change network settings; re-run install-vos.sh to add its permission"
        raise NetConfigError(f"couldn't save the network settings: {msg}")

    up = _run(["nmcli", "connection", "up", PROFILE], timeout=30)
    if up.returncode == 0:
        state = "up"
    elif not known[interface]["cable"]:
        state = "waiting for cable"
    else:
        state = "saved, not up: " + (up.stderr or up.stdout).strip()
    return {"interface": interface, "address": str(net.ip), "prefix": net.network.prefixlen, "state": state}
