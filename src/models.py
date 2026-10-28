"""
==============================================================================
Module Name:   models.py
Description:   Plain data records shared by the crawler, the text report and the
               HTML viewer: the crawl blind-spot record (a neighbor that was seen
               on the wire but could not be crawled) and the per-port VLAN record
               (mode, access / native VLAN, trunk allowed VLANs).
Author:        Mai Tan Duc <ducmai.network@gmail.com>
Created:       2026-10-17
Version:       1.0.0
License:       MIT
==============================================================================
Usage:         imported by src/crawler.py, src/report.py, src/linkcheck.py and main.py
Notes:         Requires Python 3.10+. No I/O and no dependencies on other modules.
==============================================================================
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any

__all__ = [
    "BLIND_SPOT_REASONS",
    "REASON_AUTH_FAILURE",
    "REASON_CONNECT_FAILURE",
    "REASON_CRAWL_ERROR",
    "REASON_EXCLUDED",
    "REASON_LIMIT_REACHED",
    "REASON_NO_MGMT_IP",
    "REASON_OUT_OF_SCOPE",
    "REASON_TIMEOUT",
    "REASON_UNSUPPORTED_OS",
    "VLAN_MAX",
    "VLAN_MIN",
    "VLAN_MODES",
    "BlindSpot",
    "PortVlanInfo",
    "format_vlan_list",
    "parse_vlan_list",
]

REASON_OUT_OF_SCOPE = "OUT_OF_SCOPE"  # management IP outside crawl.allowed_subnets
REASON_AUTH_FAILURE = "AUTH_FAILURE"  # SSH login rejected (bad credentials, TACACS+/RADIUS ...)
REASON_TIMEOUT = "TIMEOUT"  # SSH session or command timed out
REASON_UNSUPPORTED_OS = "UNSUPPORTED_OS"  # no LLDP profile for the platform

# The four reasons above are the core set. The rest keep every other uncrawled infrastructure
# neighbor in the audit too: a coverage report that silently drops them would claim more than
# the crawl knows.
REASON_CONNECT_FAILURE = "CONNECT_FAILURE"  # SSH could not be opened (refused, unreachable)
REASON_NO_MGMT_IP = "NO_MGMT_IP"  # the neighbor advertised no management address
REASON_EXCLUDED = "EXCLUDED"  # matches crawl.exclude_hostnames
REASON_LIMIT_REACHED = "LIMIT_REACHED"  # crawl.max_depth / crawl.max_devices stopped the walk
REASON_CRAWL_ERROR = "CRAWL_ERROR"  # command rejected, output not parseable, other errors

BLIND_SPOT_REASONS = (
    REASON_OUT_OF_SCOPE,
    REASON_AUTH_FAILURE,
    REASON_TIMEOUT,
    REASON_UNSUPPORTED_OS,
    REASON_CONNECT_FAILURE,
    REASON_NO_MGMT_IP,
    REASON_EXCLUDED,
    REASON_LIMIT_REACHED,
    REASON_CRAWL_ERROR,
)


@dataclass(frozen=True)
class BlindSpot:
    """One infrastructure neighbor that was seen but not crawled, so its branch is unmapped.

    Leaf endpoints (phones, access points, servers) are never blind spots: they have no
    downstream network to miss.
    """

    hostname: str
    chassis_id: str
    management_ip: str | None
    discovered_via: str  # the crawled switch that reported it (its name as drawn)
    parent_port: str  # the port on that switch the neighbor was seen on
    reason: str  # one of BLIND_SPOT_REASONS
    detail: str  # the error text, or why the crawler skipped it
    node_id: str = ""  # the neighbor's node in the topology graph (lets the viewer focus it)
    parent_id: str = ""  # the reporting switch's node in the topology graph

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly form (all fields), used by the snapshot and the HTML viewer."""
        return asdict(self)


# ------------------------------------------------------------------ VLAN data
VLAN_MIN, VLAN_MAX = 1, 4094  # usable 802.1Q VLAN IDs
VLAN_MODES = ("access", "trunk", "routed", "unknown")

_VLAN_TOKEN = re.compile(r"^(\d{1,4})(?:\s*-\s*(\d{1,4}))?$")


def parse_vlan_list(value: Any) -> set[int] | None:
    """Turn a VLAN list into a set of VLAN IDs; ``None`` when the list is unknown.

    Accepts ``"1-10,20,30-40"``, a list of such pieces (what the ntc-templates TextFSM lists
    carry: ``["1-10", "20"]``), ``"ALL"`` (every VLAN, 1-4094) and ``"NONE"`` (no VLAN). Spaces
    are ignored; IDs outside 1-4094 and malformed pieces are dropped, so one odd token never
    costs the rest of the list. A value that yields nothing usable and is not ``NONE`` is
    "not reported": ``None``, never an empty set (an empty set means "allows nothing").
    """
    if value is None:
        return None
    pieces: list[str] = []
    items = value if isinstance(value, (list, tuple, set, frozenset)) else [value]
    for item in items:
        pieces.extend(str(item).replace(";", ",").split(","))
    vlans: set[int] = set()
    seen_any = False
    for piece in pieces:
        token = piece.strip().lower()
        if not token:
            continue
        if token == "all":
            return set(range(VLAN_MIN, VLAN_MAX + 1))
        if token == "none":
            seen_any = True
            continue
        match = _VLAN_TOKEN.match(token)
        if not match:
            continue
        low = int(match.group(1))
        high = int(match.group(2)) if match.group(2) else low
        if low > high:
            low, high = high, low
        usable = range(max(low, VLAN_MIN), min(high, VLAN_MAX) + 1)
        if usable:  # a token wholly outside 1-4094 says nothing
            seen_any = True
            vlans.update(usable)
    if not seen_any:
        return None
    return vlans


def format_vlan_list(vlans: Iterable[int] | None) -> str:
    """Compact text of a VLAN set: ``{1,2,3,7}`` -> ``"1-3,7"``, all VLANs -> ``"1-4094"``.

    ``None`` (unknown) gives ``""``; an empty set gives ``"none"``.
    """
    if vlans is None:
        return ""
    ordered = sorted({int(v) for v in vlans})
    if not ordered:
        return "none"
    ranges: list[str] = []
    start = prev = ordered[0]
    for vlan in ordered[1:]:
        if vlan == prev + 1:
            prev = vlan
            continue
        ranges.append(f"{start}-{prev}" if prev > start else str(start))
        start = prev = vlan
    ranges.append(f"{start}-{prev}" if prev > start else str(start))
    return ",".join(ranges)


@dataclass
class PortVlanInfo:
    """What one device says about the VLAN setup of one of its own ports.

    ``mode`` is ``access``, ``trunk``, ``routed`` (a Layer 3 port: no switchport) or
    ``unknown``. ``access_vlan`` is only meaningful on an access port, ``native_vlan`` and
    ``allowed_vlans`` only on a trunk. ``allowed_vlans`` is ``None`` when the device did not
    report a list (not the same as an empty set, which means the trunk carries no VLAN).
    """

    interface: str
    mode: str = "unknown"
    access_vlan: int | None = None
    native_vlan: int | None = None
    allowed_vlans: set[int] | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly form; the allowed list is written as compact ranges (``1-10,20``)."""
        return {
            "interface": self.interface,
            "mode": self.mode,
            "access_vlan": self.access_vlan,
            "native_vlan": self.native_vlan,
            "allowed_vlans": (
                None if self.allowed_vlans is None else format_vlan_list(self.allowed_vlans)
            ),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PortVlanInfo:
        """Inverse of :meth:`to_dict` (an unknown mode becomes ``unknown``)."""
        mode = str(data.get("mode") or "unknown")
        allowed = data.get("allowed_vlans")
        return cls(
            interface=str(data.get("interface") or ""),
            mode=mode if mode in VLAN_MODES else "unknown",
            access_vlan=_as_vlan(data.get("access_vlan")),
            native_vlan=_as_vlan(data.get("native_vlan")),
            allowed_vlans=None if allowed is None else parse_vlan_list(allowed),
        )


def _as_vlan(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if VLAN_MIN <= number <= VLAN_MAX else None
