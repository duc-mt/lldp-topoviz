"""
==============================================================================
Module Name:   __init__.py
Description:   Package root: version, base exception and the name / address /
               interface normalisers shared by every pipeline stage.
Author:        Mai Tan Duc <ducmai.network@gmail.com>
Created:       2026-10-10
Version:       1.0.0
License:       MIT
==============================================================================
Usage:         imported by the other modules (not run directly)
Notes:         Requires Python 3.10+
==============================================================================
"""

from __future__ import annotations

import ipaddress
import re

__version__ = "1.0.0"


class MapperError(Exception):
    """Base class for expected, user-reportable failures in any pipeline stage."""


_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_MAC_RE = re.compile(
    r"^(?:[0-9a-f]{2}[:\-]){5}[0-9a-f]{2}$"  # 00:11:22:33:44:55 / 00-11-...
    r"|^(?:[0-9a-f]{4}[.\-]){2}[0-9a-f]{4}$"  # 0011.2233.4455 / 0011-2233-4455
    r"|^[0-9a-f]{12}$",  # 001122334455
    re.IGNORECASE,
)

#: Long / short interface prefixes -> one canonical short form, so that
#: ``GigabitEthernet0/1`` and ``Gi0/1`` (or ``Ethernet1/1`` and ``Eth1/1``) compare equal.
_IFACE_ALIASES = {
    "hundredgige": "Hu",
    "hundredgigabitethernet": "Hu",
    "hu": "Hu",
    "fortygigabitethernet": "Fo",
    "fortygige": "Fo",
    "fo": "Fo",
    "twentyfivegige": "Twe",
    "twe": "Twe",
    "tengigabitethernet": "Te",
    "tengige": "Te",
    "te": "Te",
    "xgigabitethernet": "XGi",
    "xgi": "XGi",
    "gigabitethernet": "Gi",
    "gi": "Gi",
    "ge": "Gi",
    "fastethernet": "Fa",
    "fa": "Fa",
    "ethernet": "Eth",
    "eth": "Eth",
    "et": "Eth",
    "port-channel": "Po",
    "portchannel": "Po",
    "po": "Po",
    "management": "Ma",
    "ma": "Ma",
    "loopback": "Lo",
    "lo": "Lo",
    "vlan": "Vl",
    "vl": "Vl",
}
_IFACE_RE = re.compile(r"^([A-Za-z][A-Za-z\-]*?)\s*(\d[\w/.:\-]*)$")


def is_ipv4(text: object) -> bool:
    """True if ``text`` is a valid dotted-quad IPv4 address."""
    try:
        ipaddress.IPv4Address(str(text).strip())
    except ValueError:
        return False
    return True


def extract_ipv4(value: object) -> list[str]:
    """Every valid IPv4 address found in ``value`` (a string or a list of strings).

    Order is preserved and duplicates are dropped. IPv6 and MAC-style management
    addresses are ignored: they cannot be used to open an SSH session here.
    """
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    found: list[str] = []
    for item in items:
        for candidate in _IPV4_RE.findall(str(item)):
            if is_ipv4(candidate) and candidate not in found:
                found.append(candidate)
    return found


def looks_like_mac(text: object) -> bool:
    """True for the usual MAC notations (colon, dash, Cisco dotted, bare hex)."""
    return bool(_MAC_RE.match(str(text or "").strip()))


def normalize_chassis_id(value: object) -> str:
    """Canonical chassis-ID key: MACs lose their separators, anything else is lower-cased."""
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if looks_like_mac(text):
        return re.sub(r"[^0-9a-f]", "", text)
    return text


def short_name(name: object) -> str:
    """Strip quotes, whitespace and the DNS domain from a device name, keeping its case."""
    text = str(name or "").strip().strip("\"'").strip()
    if not text or is_ipv4(text) or looks_like_mac(text):
        return text
    return text.split(".", 1)[0]


def normalize_hostname(name: object) -> str:
    """Canonical hostname key: lower-case, domain-stripped (``SW1.corp.local`` -> ``sw1``)."""
    return short_name(name).lower()


def normalize_interface(name: object) -> str:
    """Canonical short interface name; unrecognised names are returned trimmed but unchanged."""
    text = str(name or "").strip().strip("\"'")
    if not text:
        return ""
    match = _IFACE_RE.match(text)
    if not match:
        return text
    prefix, rest = match.groups()
    canonical = _IFACE_ALIASES.get(prefix.lower())
    return f"{canonical}{rest}" if canonical else f"{prefix}{rest}"
