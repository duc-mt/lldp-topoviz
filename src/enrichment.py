"""
==============================================================================
Module Name:   enrichment.py
Description:   Everything the diagram derives from the crawl data: node type
               (switch / router / firewall / ...), OS label, hierarchy tier,
               nominal link speed from interface names, and the short wording
               for failures and "not crawled" reasons.
Author:        Mai Tan Duc <ducmai.network@gmail.com>
Created:       2026-10-11
Version:       1.2.0
License:       MIT
==============================================================================
Usage:         imported by src/visualizer.py (not run directly)
Notes:         Requires Python 3.10+. Pure functions: no I/O, easy to test.
               LLDP carries no role (core / access) and no "firewall" capability,
               so those are inferred from names, descriptions and topology; every
               inference can be overridden from settings.yaml.
==============================================================================
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import networkx as nx

from src.config import ConfigError

# ------------------------------------------------------------------ node type
NODE_KINDS = ("switch", "router", "firewall", "ap", "phone", "endpoint", "unknown")

_FIREWALL_DESC = re.compile(
    r"pfsense|opnsense|fortigate|fortios|check ?point|\bgaia\b|palo alto|pan-?os|"
    r"adaptive security|cisco asa|firepower|sophos|sonicwall|watchguard|netscreen|"
    r"\bsrx\d|barracuda|stormshield",
    re.IGNORECASE,
)
_FIREWALL_NAME = re.compile(r"(^|[-_])(fw|firewall|ngfw|fgt|asa)\d*([-_]|$)", re.IGNORECASE)
_ROUTER_DESC = re.compile(
    r"router|\b(?:asr|isr|csr|ncs|ptx|acx)[ -]?\d|\bmx\d{2,4}\b|vyos|mikrotik|routeros",
    re.IGNORECASE,
)
_AP_DESC = re.compile(r"access point|wireless|\bAP[- ]?\d{2,3}\b", re.IGNORECASE)
_PHONE_DESC = re.compile(r"\bphone\b|\bsip\b", re.IGNORECASE)
# CDP / FDP report the hardware platform ("cisco AIR-AP2802I", "Cisco IP Phone 7960")
_AP_PLATFORM = re.compile(r"\bAIR-|aironet|\bC9[1-3]\d{2}[A-Z]*AX", re.IGNORECASE)
_PHONE_PLATFORM = re.compile(r"ip phone|\bphone\b", re.IGNORECASE)
# Leaf classes the crawler records (src/parser.py classify_endpoint) -> icon type.
_ENDPOINT_KINDS = {
    "endpoint_phone": "phone",
    "endpoint_ap": "ap",
    "endpoint_server": "endpoint",
    "endpoint_generic": "endpoint",
}
_SWITCH_TYPES = {
    "cisco_ios",
    "cisco_xe",
    "cisco_nxos",
    "arista_eos",
    "aruba_aoscx",
    "huawei",
    "hp_procurve",
}


@dataclass(frozen=True)
class NodeTypeRules:
    """Hostname regexes that force a node type (``visualization.node_types``)."""

    overrides: tuple[tuple[re.Pattern[str], str], ...] = ()

    @classmethod
    def from_settings(cls, section: dict[str, Any] | None) -> NodeTypeRules:
        """Build from ``{kind: [regex, ...]}``.

        Raises:
            ConfigError: On an unknown kind or an invalid regex.
        """
        overrides: list[tuple[re.Pattern[str], str]] = []
        for kind, patterns in (section or {}).items():
            if kind not in NODE_KINDS:
                raise ConfigError(
                    f"visualization.node_types: unknown type '{kind}' (use {', '.join(NODE_KINDS)})"
                )
            for pattern in patterns or []:
                try:
                    overrides.append((re.compile(str(pattern), re.IGNORECASE), kind))
                except re.error as exc:
                    raise ConfigError(
                        f"visualization.node_types.{kind}: invalid regex {pattern!r}: {exc}"
                    ) from exc
        return cls(tuple(overrides))

    def match(self, label: str) -> str | None:
        for pattern, kind in self.overrides:
            if pattern.search(label):
                return kind
        return None


def classify_node(attrs: dict[str, Any], label: str, rules: NodeTypeRules | None = None) -> str:
    """Pick the icon type of a device.

    Order: a settings override, the leaf class the crawler recorded, firewall hints (LLDP has no
    firewall capability, so this is a keyword guess on the description, the CDP hardware platform
    and the hostname), phone / AP
    platforms, router hints, then the system capabilities (WLAN AP, telephone, router, bridge),
    then the platform.
    """
    forced = rules.match(label) if rules else None
    if forced:
        return forced
    leaf = _ENDPOINT_KINDS.get(str(attrs.get("endpoint_class") or ""))
    if leaf:  # the crawler's leaf guard already decided what this is
        return leaf
    description = str(attrs.get("description") or "")
    platform = str(attrs.get("platform") or "")
    text = f"{description} {platform}"
    if _FIREWALL_DESC.search(text) or _FIREWALL_NAME.search(label):
        return "firewall"
    if _PHONE_PLATFORM.search(platform):
        return "phone"
    if _AP_PLATFORM.search(platform):
        return "ap"
    if _ROUTER_DESC.search(text):
        return "router"

    caps = set(attrs.get("capabilities") or [])
    if "wlan" in caps:
        return "ap"
    if "telephone" in caps:
        return "phone"
    if caps & {"router", "bridge"}:
        # A device advertising both (an L3 switch) is drawn as a switch.
        return "switch" if "bridge" in caps else "router"
    if caps & {"station", "docsis", "other"}:
        return "endpoint"

    # No capabilities advertised (the Arista template has none): fall back to hints.
    if _AP_DESC.search(description):
        return "ap"
    if _PHONE_DESC.search(description):
        return "phone"
    device_type = str(attrs.get("device_type") or "")
    if device_type in _SWITCH_TYPES:
        return "switch"
    if device_type in ("cisco_xr", "vyos"):
        return "router"
    return "unknown"


# ------------------------------------------------------------------- OS label
_OS_BY_DEVICE_TYPE = {
    "cisco_ios": "IOS",
    "cisco_xe": "IOS-XE",
    "cisco_nxos": "NX-OS",
    "cisco_xr": "IOS-XR",
    "arista_eos": "EOS",
    "aruba_aoscx": "AOS-CX",
    "huawei": "VRP",
    "huawei_vrp": "VRP",
    "huawei_vrpv8": "VRP",
    "juniper_junos": "Junos",
    "vyos": "VyOS",
    "hp_procurve": "ProCurve",
}
# Description keywords, most specific first (the description tells IOS from IOS-XE).
_OS_KEYWORDS = (
    (re.compile(r"nx-?os|nexus", re.I), "NX-OS"),
    (re.compile(r"ios[ -]?xr", re.I), "IOS-XR"),
    (re.compile(r"ios[ -]?xe|iosxe", re.I), "IOS-XE"),
    (re.compile(r"cisco ios", re.I), "IOS"),
    (re.compile(r"\barista\b|\beos\b", re.I), "EOS"),
    (re.compile(r"aos-?cx", re.I), "AOS-CX"),
    (re.compile(r"huawei|\bvrp\b", re.I), "VRP"),
    (re.compile(r"junos|juniper", re.I), "Junos"),
    (re.compile(r"\bvyos\b|\bvyatta\b", re.I), "VyOS"),
    (re.compile(r"procurve|arubaos-s", re.I), "ProCurve"),
    (re.compile(r"pfsense", re.I), "pfSense"),
    (re.compile(r"fortios|fortigate", re.I), "FortiOS"),
    (re.compile(r"\bgaia\b|check ?point", re.I), "Gaia"),
    (re.compile(r"pan-?os|palo alto", re.I), "PAN-OS"),
    (re.compile(r"adaptive security|cisco asa", re.I), "ASA"),
)
_CISCO_VERSION = r"Version\s+([\w.()]+?)(?:[,\s]|$)"
_VERSION_PATTERNS = {
    "NX-OS": r"Software\s+(\d[\w.()]*)",
    "IOS": _CISCO_VERSION,
    "IOS-XE": _CISCO_VERSION,
    "IOS-XR": _CISCO_VERSION,
    "EOS": r"version\s+([\w.]+)",
    "VRP": r"Version\s+([\d.]+)",
    "AOS-CX": r"(\d+\.\d+\.\d+)",
    "Junos": r"Junos:?\s+([\w.\-]+)",
    "VyOS": r"VyOS\s+([\w.\-]+)",
    "ProCurve": r"revision\s+([\w.]+)",
    "pfSense": r"pfSense\s+([\d.]+)",
    "FortiOS": r"v?(\d+\.\d+\.\d+)",
}


def os_label(attrs: dict[str, Any]) -> str:
    """Short OS name with version when the description has one: ``NX-OS 9.3(8)``."""
    description = str(attrs.get("description") or "")
    name = next((label for pattern, label in _OS_KEYWORDS if pattern.search(description)), None)
    name = name or _OS_BY_DEVICE_TYPE.get(str(attrs.get("device_type") or ""))
    if not name:
        return ""
    pattern = _VERSION_PATTERNS.get(name)
    match = re.search(pattern, description, re.IGNORECASE) if pattern and description else None
    return f"{name} {match.group(1)}".strip() if match else name


def sub_label(attrs: dict[str, Any]) -> str:
    """The small line under a hostname: ``10.0.1.1 · NX-OS 9.3(8)`` (or the CDP platform)."""
    address = attrs.get("address") or next(iter(attrs.get("mgmt_ips") or []), "")
    detail = os_label(attrs) or str(attrs.get("platform") or "")
    parts = [part for part in (address, detail) if part]
    return " · ".join(parts)


# ----------------------------------------------------------------- hierarchy
TIER_ORDER = ("edge", "core", "distribution", "access", "end")
TIER_TITLES = {
    "edge": "Edge",
    "core": "Core",
    "distribution": "Distribution",
    "access": "Access",
    "end": "End devices",
}
DEFAULT_TIER_RULES = {
    "edge": (r"^(isp|wan|inet|internet)([-_\d]|$)", r"(^|[-_])(isp|wan)([-_\d]|$)"),
    "core": (r"^core", r"(^|[-_])core([-_\d]|$)", r"^spine", r"^cr[-_]?\d"),
    "distribution": (r"^dist", r"^dsw", r"(^|[-_])(dist|agg|aggr)([-_\d]|$)", r"^ds[-_]?\d"),
    "access": (
        r"^acc",
        r"^asw",
        r"^sw[-_]?\d",
        r"(^|[-_])(acc|access|leaf|tor)([-_\d]|$)",
        r"^as[-_]?\d",
    ),
}
_STRUCTURAL = ("core", "distribution", "access")


@dataclass(frozen=True)
class TierRules:
    """Hostname regexes per tier, plus the device to treat as the top when none match."""

    rules: dict[str, tuple[re.Pattern[str], ...]] = field(default_factory=dict)
    root: str = ""

    @classmethod
    def from_settings(cls, tiers: dict[str, Any] | None, root: str = "") -> TierRules:
        """Defaults, with any tier named in ``visualization.tiers`` replaced by the user's list.

        Raises:
            ConfigError: On an unknown tier name or an invalid regex.
        """
        merged: dict[str, list[str]] = {t: list(p) for t, p in DEFAULT_TIER_RULES.items()}
        for tier, patterns in (tiers or {}).items():
            if tier not in ("edge", *_STRUCTURAL):
                raise ConfigError(
                    f"visualization.tiers: unknown tier '{tier}' "
                    f"(use edge, core, distribution, access; end devices are detected by type)"
                )
            merged[tier] = [str(p) for p in patterns or []]
        try:
            compiled = {
                tier: tuple(re.compile(p, re.IGNORECASE) for p in patterns)
                for tier, patterns in merged.items()
            }
        except re.error as exc:
            raise ConfigError(f"visualization.tiers: invalid regex ({exc})") from exc
        return cls(compiled, str(root or ""))

    def match(self, label: str) -> str | None:
        for tier in ("edge", *_STRUCTURAL):
            if any(p.search(label) for p in self.rules.get(tier, ())):
                return tier
        return None


@dataclass
class TierLayout:
    """Where each device sits in the hierarchy."""

    tiers: dict[str, str]  # node id -> tier name
    levels: dict[str, int]  # node id -> row index (only the tiers in use are numbered)
    rows: list[tuple[str, str]]  # (tier name, title) top to bottom


def assign_tiers(graph: nx.MultiGraph, kinds: dict[str, str], rules: TierRules) -> TierLayout:
    """Place every device in Edge / Core / Distribution / Access / End devices.

    1. Hostname rules decide (``core*``, ``dist*``, ``acc*`` ... or your own regexes).
    2. Firewalls go to Edge; APs, phones and endpoints go to End devices.
    3. Everything else is placed relative to what is already placed: one row below its
       highest placed neighbor (never above Core, never below Access).
    4. If no hostname matched anywhere, the root device (``visualization.root``, else the
       seed) is Core and the rest is arranged by hops from it.
    """
    index = {tier: i for i, tier in enumerate(TIER_ORDER)}
    tier_of: dict[str, str] = {}
    for node, attrs in graph.nodes(data=True):
        label = str(attrs.get("label") or node)
        matched = rules.match(label)
        if matched:
            tier_of[node] = matched
        elif kinds.get(node) == "firewall":
            tier_of[node] = "edge"
        elif kinds.get(node) in ("ap", "phone", "endpoint"):
            tier_of[node] = "end"

    if not any(t in _STRUCTURAL for t in tier_of.values()):
        root = _find_root(graph, rules.root)
        if root is not None and root not in tier_of:
            tier_of[root] = "core"

    while True:  # level by level, so each node takes the minimum over its placed neighbors
        placed = {}
        for node in sorted(graph.nodes):
            if node in tier_of:
                continue
            neighbors = [index[tier_of[n]] for n in graph.neighbors(node) if n in tier_of]
            if neighbors:
                row = max(min(min(neighbors) + 1, index["access"]), index["core"])
                placed[node] = TIER_ORDER[row]
        if not placed:
            break
        tier_of.update(placed)
    for node in graph.nodes:
        tier_of.setdefault(node, "access")

    used = [tier for tier in TIER_ORDER if tier in set(tier_of.values())]
    return TierLayout(
        tiers=tier_of,
        levels={node: used.index(tier) for node, tier in tier_of.items()},
        rows=[(tier, TIER_TITLES[tier]) for tier in used],
    )


def _find_root(graph: nx.MultiGraph, wanted: str) -> str | None:
    nodes = list(graph.nodes(data=True))
    if wanted:
        key = wanted.strip().lower()
        for node, attrs in nodes:
            if key in (str(node).lower(), str(attrs.get("label") or "").lower()):
                return node
    for node, attrs in nodes:
        if attrs.get("seed"):
            return node
    return nodes[0][0] if nodes else None


# --------------------------------------------------------------- link speed
_SPEED_PREFIXES = (
    (100_000, r"hundred-?gig\w*|hu|100ge"),
    (40_000, r"forty-?gig\w*|fo|40ge"),
    (25_000, r"twenty-?five-?gig\w*|twe|25ge"),
    (10_000, r"ten-?gig\w*|te|xgigabitethernet|xge|xe-|10ge"),
    (1_000, r"gigabitethernet|gi|ge-|ge"),
    (100, r"fastethernet|fa"),
)
_SPEED_RES = tuple(
    (mbps, re.compile(rf"^(?:{alternatives})\d", re.IGNORECASE))
    for mbps, alternatives in _SPEED_PREFIXES
)

#: (minimum Mbps, label, colour, width). Colours avoid the status colours (green/red/grey/gold).
SPEED_STYLES = (
    (100, "100M", "#A8B3C2", 1.5),
    (1_000, "1G", "#7A869A", 2.5),
    (10_000, "10G", "#2F6FDE", 4.0),
    (25_000, "25G", "#0E9AA7", 5.0),
    (40_000, "40G", "#7B4FD6", 6.0),
    (100_000, "100G", "#B83280", 8.0),
)
UNKNOWN_SPEED_STYLE = ("?", "#B8C0CC", 1.5)
#: Colour by style label (for the legend), unknown included.
STYLE_COLORS = {label: color for _mbps, label, color, _width in SPEED_STYLES} | {
    UNKNOWN_SPEED_STYLE[0]: UNKNOWN_SPEED_STYLE[1]
}
STYLE_ORDER = [label for _mbps, label, _color, _width in SPEED_STYLES] + [UNKNOWN_SPEED_STYLE[0]]


def port_speed(port: str) -> int | None:
    """Nominal speed in Mbps implied by an interface name, or ``None``.

    Only names that encode a speed count (``Gi``, ``Te``, ``xe-``, ``Hu``, ...). ``Ethernet1/1``
    (NX-OS, EOS) and ``1/1/1`` (AOS-CX) say nothing. This is the port *type*, not the
    negotiated speed.
    """
    text = str(port or "").strip()
    for mbps, pattern in _SPEED_RES:
        if pattern.match(text):
            return mbps
    return None


def link_speed(port_a: str, port_b: str) -> int | None:
    """Nominal link speed: the slower of the two ends when both are known, else the known one."""
    known = [s for s in (port_speed(port_a), port_speed(port_b)) if s]
    return min(known) if known else None


def speed_label(mbps: int | None) -> str:
    if not mbps:
        return "?"
    return f"{mbps / 1000:g}G" if mbps >= 1000 else f"{mbps}M"


def speed_style(mbps: int | None) -> tuple[str, str, float]:
    """``(label, colour, width)`` for a speed; aggregates take the highest tier they reach."""
    if not mbps:
        return UNKNOWN_SPEED_STYLE
    chosen = SPEED_STYLES[0]
    for style in SPEED_STYLES:
        if mbps >= style[0]:
            chosen = style
    return chosen[1], chosen[2], chosen[3]


# --------------------------------------------------------- failure wording
FAILURE_BADGES = {
    "auth": "SSH auth failed",
    "timeout": "SSH timeout",
    "connect": "SSH connect failed",
    "rejected": "Command rejected",
    "parse": "Parse failed",
    "unsupported": "Unsupported platform",
    "no_data": "No replay data",
    "other": "Crawl failed",
}
FAILURE_HINTS = {
    "auth": "Check LLDP_MAPPER_USERNAME / LLDP_MAPPER_PASSWORD (and the enable secret).",
    "timeout": "The device did not answer in time: check routing, ACLs and the device.* timeouts.",
    "connect": "SSH could not be opened: is SSH enabled on this device and port, and not refused?",
    "rejected": "The device refused the LLDP command: check device_type and that LLDP is on.",
    "parse": "The output did not match the template: check device_type (--save-raw to inspect).",
    "unsupported": "No LLDP profile for this platform: add one under custom_vendors.",
    "no_data": "The replay directory has no capture for this device.",
    "other": "See the error text for details.",
}
SKIP_BADGES = {
    "excluded": "excluded",
    "capabilities": "end device",
    "no_ip": "no mgmt IP",
    "out_of_scope": "outside scope",
    "max_depth": "depth limit",
    "max_devices": "device limit",
    "no_profile": "no profile",
}


def failure_info(kind: str) -> tuple[str, str]:
    """``(badge, hint)`` for a failure class."""
    kind = kind if kind in FAILURE_BADGES else "other"
    return FAILURE_BADGES[kind], FAILURE_HINTS[kind]


def skip_badge(code: str) -> str:
    """Short label for a "not crawled" reason code ('' when there is none)."""
    return SKIP_BADGES.get(code, "not crawled" if code else "")
