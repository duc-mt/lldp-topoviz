"""
==============================================================================
Module Name:   base.py
Description:   Vendor-neutral LLDP neighbor record, the TextFSM column map that
               each platform profile uses, and the row -> record conversion.
Author:        Mai Tan Duc <ducmai.network@gmail.com>
Created:       2026-10-10
Version:       1.0.0
License:       MIT
==============================================================================
Usage:         imported by src/vendors/__init__.py and src/parser.py
Notes:         Requires Python 3.10+
==============================================================================
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from src import (
    MapperError,
    extract_ipv4,
    looks_like_mac,
    normalize_hostname,
    normalize_interface,
)


class ParserError(MapperError):
    """Raised when LLDP output cannot be turned into neighbor records."""

    kind = "parse"  # failure class shown in the diagram


#: Canonical capability names (after normalisation).
KNOWN_CAPABILITIES = frozenset(
    {"router", "bridge", "telephone", "wlan", "station", "repeater", "docsis", "other"}
)

_CAPABILITY_CODES = {
    "r": "router",
    "b": "bridge",
    "t": "telephone",
    "w": "wlan",
    "s": "station",
    "p": "repeater",
    "c": "docsis",
    "o": "other",
}
_CAPABILITY_WORDS = {
    "router": "router",
    "bridge": "bridge",
    "switch": "bridge",
    "trans-bridge": "bridge",  # CDP / FDP wording
    "source-route-bridge": "bridge",
    "host": "station",
    "telephone": "telephone",
    "phone": "telephone",
    "wlan": "wlan",
    "wlan-access-point": "wlan",
    "access-point": "wlan",
    "station": "station",
    "station-only": "station",
    "repeater": "repeater",
    "docsis": "docsis",
    "cable-device": "docsis",
    "other": "other",
}
#: IEEE 802.1AB System Capabilities bits (the 16-bit map of the LLDP TLV), lowest bit first.
#: Note ``0x0008`` is WLAN access point and ``0x0020`` is telephone (not the other way round).
CAPABILITY_BITS = (
    (0x0001, "other"),
    (0x0002, "repeater"),
    (0x0004, "bridge"),
    (0x0008, "wlan"),
    (0x0010, "router"),
    (0x0020, "telephone"),
    (0x0040, "docsis"),
    (0x0080, "station"),
)
_HEX_BITMAP = re.compile(r"0x[0-9a-f]{1,4}")
_NO_VALUE = {"", "-", "--", "n/a", "na", "none", "not advertised", "not-advertised"}
_NOISE_TOKENS = {
    "not",
    "advertised",
    "none",
    "n/a",
    "na",
    "-",
    "--",
    # CDP capability words that say nothing about the role of the device
    "igmp",
    "filtering",
    "supports-stp-dispute",
    "two-port-mac-relay",
    "cvta",
    "remote",
    # Status / noise flags
    "on",
    "off",
    "yes",
    "no",
    "enabled",
    "disabled",
    "supported",
}


def _flatten_items(value: Any) -> list[Any]:
    flat: list[Any] = []
    items = value if isinstance(value, (list, tuple, set, frozenset)) else [value]
    for item in items:
        if isinstance(item, (list, tuple, set, frozenset)):
            flat.extend(_flatten_items(item))
        else:
            flat.append(item)
    return flat


def decode_capability_bitmap(bitmap: int) -> frozenset[str]:
    """Canonical names of the capabilities set in a raw LLDP System Capabilities bitmap.

    ``0x0014`` (bridge + router) -> ``{"bridge", "router"}``. Reserved bits are ignored.
    """
    return frozenset(name for bit, name in CAPABILITY_BITS if bitmap & bit)


def normalize_capabilities(value: Any) -> frozenset[str]:
    """Turn ``"B,R"``, ``"Bridge, Router"``, a ``0x0014`` bitmap or a list of any into names."""
    items = _flatten_items(value)
    result: set[str] = set()
    for item in items:
        for token in re.split(r"[,;/\s]+", str(item or "").strip().lower()):
            token = token.strip("()")
            if not token or token in _NOISE_TOKENS:
                continue
            if _HEX_BITMAP.fullmatch(token):
                result |= decode_capability_bitmap(int(token, 16))
            elif token in _CAPABILITY_WORDS:
                result.add(_CAPABILITY_WORDS[token])
            elif len(token) == 1 and token in _CAPABILITY_CODES:
                result.add(_CAPABILITY_CODES[token])
            else:
                result.add(token)
    return frozenset(result)


@dataclass(frozen=True)
class LldpNeighbor:
    """One LLDP neighbor as seen from one local interface."""

    local_interface: str
    neighbor_name: str = ""
    neighbor_interface: str = ""
    chassis_id: str = ""
    mgmt_ips: tuple[str, ...] = ()
    description: str = ""
    capabilities: frozenset[str] = frozenset()
    # Which discovery protocol(s) reported this neighbor, and the neighbor's hardware platform
    # (CDP / FDP report one, e.g. "cisco WS-C3850-48P"). The class keeps its LLDP-era name.
    protocols: frozenset[str] = frozenset({"lldp"})
    platform: str = ""
    # Set by the parser (src/parser.py ``classify_endpoint``): ``endpoint_phone`` /
    # ``endpoint_ap`` / ``endpoint_server`` / ``endpoint_generic`` for a leaf endpoint that
    # must never be logged in to, ``""`` for anything that may be network infrastructure.
    endpoint_class: str = ""


@dataclass(frozen=True)
class PortChannelMember:
    """One physical port of a port-channel and its state in the bundle."""

    interface: str
    state: str = "other"  # bundled | down | suspended | standalone | standby | waiting | other


@dataclass(frozen=True)
class PortChannel:
    """A link-aggregation bundle (LACP, PAgP or static) as one device reports it."""

    name: str  # Po1, Eth-Trunk1, ...
    protocol: str  # LACP | PAgP | static
    up: bool = True
    members: tuple[PortChannelMember, ...] = ()


_MEMBER_STATES = {
    "P": "bundled",  # IOS / NX-OS / EOS: bundled in port-channel
    "p": "bundled",  # NX-OS: up in delay-lacp mode
    "D": "down",
    "s": "suspended",
    "I": "standalone",  # IOS / NX-OS: stand-alone / individual
    "i": "standalone",  # EOS: individual
    "H": "standby",  # hot-standby (LACP only)
    "w": "waiting",  # EOS: waiting for partner
}
_HUAWEI_STATES = {"selected": "bundled", "up": "bundled", "unselected": "down", "down": "down"}


def lag_member_state(code: object) -> str:
    """Normalise a member flag (``P``, ``D``, ``s``, ``PG+``) or Huawei status (``Selected``)."""
    text = str(code or "").strip()
    if text.lower() in _HUAWEI_STATES:
        return _HUAWEI_STATES[text.lower()]
    return _MEMBER_STATES.get(text[:1], "other")


def lag_protocol(text: object) -> str:
    """Normalise the bundle protocol column to ``LACP``, ``PAgP`` or ``static``."""
    value = str(text or "").strip().lower()
    if value.startswith("lacp"):
        return "LACP"
    if value.startswith("pagp"):
        return "PAgP"
    return "static"  # "-", NONE, on, manual, normal ...


@dataclass(frozen=True)
class FieldMap:
    """TextFSM column names (first non-empty wins) for each field of ``LldpNeighbor``.

    Column names differ per template (``NEIGHBOR_NAME`` vs ``NEIGHBOR``,
    ``MGMT_ADDRESS`` vs ``MANAGEMENT_IP``), so every platform profile supplies one.
    """

    local_interface: tuple[str, ...] = ("LOCAL_INTERFACE",)
    neighbor_name: tuple[str, ...] = ("NEIGHBOR_NAME",)
    neighbor_interface: tuple[str, ...] = ("NEIGHBOR_INTERFACE",)
    chassis_id: tuple[str, ...] = ("CHASSIS_ID",)
    mgmt_address: tuple[str, ...] = ("MGMT_ADDRESS",)
    description: tuple[str, ...] = ("NEIGHBOR_DESCRIPTION",)
    capabilities: tuple[str, ...] = ("CAPABILITIES",)
    platform: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any], where: str) -> FieldMap:
        """Build from a ``fields:`` mapping of a custom vendor (values: column name or list).

        Raises:
            ParserError: On an unknown field name or a malformed column list.
        """
        allowed = set(cls.__dataclass_fields__)
        unknown = set(mapping) - allowed
        if unknown:
            raise ParserError(
                f"{where}: unknown field(s) {', '.join(sorted(unknown))}; "
                f"allowed: {', '.join(sorted(allowed))}"
            )
        values: dict[str, tuple[str, ...]] = {}
        for key, raw in mapping.items():
            columns = [raw] if isinstance(raw, str) else raw
            if not isinstance(columns, (list, tuple)) or not all(
                isinstance(c, str) and c for c in columns
            ):
                raise ParserError(f"{where}: fields.{key} must be a column name or a list of them")
            values[key] = tuple(c.upper() for c in columns)
        return cls(**values)


def _text(value: Any) -> str:
    """First meaningful string of a TextFSM value (they are strings or lists of strings)."""
    items = value if isinstance(value, (list, tuple)) else [value]
    for item in items:
        text = " ".join(str(item or "").split())
        if text.lower() not in _NO_VALUE:
            return text
    return ""


def _first(data: Mapping[str, Any], columns: tuple[str, ...]) -> str:
    for column in columns:
        text = _text(data.get(column))
        if text:
            return text
    return ""


def _interface(data: Mapping[str, Any], columns: tuple[str, ...]) -> str:
    """Prefer a real interface name over a MAC-style Port ID; fall back to whatever exists."""
    candidates = [_text(data.get(c)) for c in columns]
    candidates = [c for c in candidates if c]
    for candidate in candidates:
        if not looks_like_mac(candidate):
            return candidate
    return candidates[0] if candidates else ""


_SERIAL_SUFFIX = re.compile(r"\s*\([^)]*\)\s*$")


def row_to_neighbor(
    row: Mapping[str, Any], fields: FieldMap, protocol: str = "lldp"
) -> LldpNeighbor | None:
    """Convert one TextFSM row into an ``LldpNeighbor`` (``None`` if it identifies nothing).

    ``protocol`` tags where the row came from (``lldp``, ``cdp``, ...). CDP device IDs often
    carry the serial number in parentheses (``dist1(FDO24350KX1)``); it is dropped.
    """
    data = {str(key).upper(): value for key, value in row.items()}
    name = _first(data, fields.neighbor_name)
    if protocol != "lldp":
        name = _SERIAL_SUFFIX.sub("", name)
    chassis = _first(data, fields.chassis_id)
    mgmt = extract_ipv4([data[c] for c in fields.mgmt_address if data.get(c) not in (None, "")])
    if not (name or chassis or mgmt):
        return None
    return LldpNeighbor(
        local_interface=_first(data, fields.local_interface),
        neighbor_name=name,
        neighbor_interface=_interface(data, fields.neighbor_interface),
        chassis_id=chassis,
        mgmt_ips=tuple(mgmt),
        description=_first(data, fields.description),
        capabilities=normalize_capabilities(
            [data[c] for c in fields.capabilities if data.get(c) not in (None, "")]
        ),
        protocols=frozenset({protocol}),
        platform=_first(data, fields.platform),
    )


def _same_neighbor(a: LldpNeighbor, b: LldpNeighbor) -> bool:
    """Do two reports (typically LLDP and CDP) describe the same neighbor on the same port?

    Two neighbors with different names are different devices, whatever their ports say: several
    devices behind one port may well all report the port ID ``eth0``.
    """
    local = (
        normalize_interface(a.local_interface).lower(),
        normalize_interface(b.local_interface).lower(),
    )
    if local[0] != local[1]:
        return False
    name_a, name_b = normalize_hostname(a.neighbor_name), normalize_hostname(b.neighbor_name)
    if name_a and name_b:
        return name_a == name_b
    ports = (
        normalize_interface(a.neighbor_interface).lower(),
        normalize_interface(b.neighbor_interface).lower(),
    )
    if ports[0] and ports[0] == ports[1]:
        return True
    return bool(set(a.mgmt_ips) & set(b.mgmt_ips))


def merge_neighbors(groups: Sequence[Sequence[LldpNeighbor]]) -> list[LldpNeighbor]:
    """Combine the neighbors found by several discovery protocols into one list.

    The groups are in priority order (LLDP first). A neighbor reported by more than one
    protocol on the same local port becomes one record: the first report's values win and the
    gaps (management IP, platform, description) are filled from the others.
    """
    merged: list[LldpNeighbor] = []
    for group in groups:
        for neighbor in group:
            for index, existing in enumerate(merged):
                # Only reports from different protocols can be the same neighbor.
                if not (existing.protocols & neighbor.protocols) and _same_neighbor(
                    existing, neighbor
                ):
                    merged[index] = replace(
                        existing,
                        neighbor_name=existing.neighbor_name or neighbor.neighbor_name,
                        neighbor_interface=existing.neighbor_interface
                        or neighbor.neighbor_interface,
                        chassis_id=existing.chassis_id or neighbor.chassis_id,
                        mgmt_ips=tuple(dict.fromkeys(existing.mgmt_ips + neighbor.mgmt_ips)),
                        description=existing.description or neighbor.description,
                        capabilities=existing.capabilities | neighbor.capabilities,
                        protocols=existing.protocols | neighbor.protocols,
                        platform=existing.platform or neighbor.platform,
                        endpoint_class=existing.endpoint_class or neighbor.endpoint_class,
                    )
                    break
            else:
                merged.append(neighbor)
    return merged


# --------------------------------------------------------- interface status
@dataclass(frozen=True)
class InterfaceStatus:
    """What a device says about one of its own ports (operating state, not capability)."""

    name: str
    status: str = "unknown"  # up | down | disabled | err | unknown
    speed: int | None = None  # Mbps actually in use (None: unknown or still negotiating)
    duplex: str | None = None  # full | half
    auto: bool | None = None  # speed was auto-negotiated


_SPEED_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([GgMmKk])?", re.IGNORECASE)


def parse_speed(value: object) -> tuple[int | None, bool | None]:
    """Parse ``a-1000``, ``1000``, ``10G``, ``100M``, ``1000Mb/s``, ``auto`` -> ``(Mbps, auto)``."""
    text = str(value or "").strip()
    if not text or text.lower() in {"-", "--", "unknown", "n/a"}:
        return None, None
    auto = text.lower().startswith("a-") or text.lower() == "auto"
    text = re.sub(r"^a-", "", text, flags=re.IGNORECASE)
    match = _SPEED_RE.search(text)
    if not match or text.lower() == "auto":
        return None, auto or None
    number = float(match.group(1))
    unit = (match.group(2) or "m").lower()
    mbps = number * 1000 if unit == "g" else number / 1000 if unit == "k" else number
    return int(round(mbps)), (True if auto else None)


def parse_duplex(value: object) -> tuple[str | None, bool | None]:
    """Parse ``a-full``, ``full``, ``Half-duplex``, ``HALF``, ``auto`` -> ``(duplex, auto)``."""
    text = str(value or "").strip().lower()
    auto = text.startswith("a-") or text == "auto"
    if "full" in text:
        return "full", True if auto else None
    if "half" in text:
        return "half", True if auto else None
    return None, auto or None


_UP = {"connected", "up", "monitoring"}
_DISABLED = {"disabled", "administratively"}
_ERR = {"err-disabled", "errdisabled"}


def parse_link_state(value: object) -> str:
    """Normalise a port status word (``connected``, ``UP``, ``notconnect``, ``err-disabled``)."""
    text = str(value or "").strip().lower()
    if not text:
        return "unknown"
    if text in _ERR or "err" in text and "disabled" in text:
        return "err"
    if text.startswith("admin") or text in _DISABLED:
        return "disabled"
    if text in _UP or text.startswith("up"):
        return "up"
    return "down"
