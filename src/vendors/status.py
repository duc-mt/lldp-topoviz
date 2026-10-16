"""
==============================================================================
Module Name:   status.py
Description:   Per-platform normalisers that turn the TextFSM rows of the
               interface commands (show interfaces status, display interface,
               show interface, ...) into InterfaceStatus records: what each port
               is really doing (state, speed, duplex), used for link checks.
Author:        Mai Tan Duc <ducmai.network@gmail.com>
Created:       2026-10-12
Version:       1.3.0
License:       MIT
==============================================================================
Usage:         imported by src/vendors/__init__.py (not run directly)
Notes:         Requires Python 3.10+. Netmiko returns lower-case column names and
               the templates declare upper-case ones, so rows are read either way.
==============================================================================
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.vendors.base import InterfaceStatus, parse_duplex, parse_link_state, parse_speed


def _row(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key).upper(): value for key, value in raw.items()}


def _text(value: Any) -> str:
    items = value if isinstance(value, (list, tuple)) else [value]
    return " ".join(str(items[0] if items else "").split())


def parse_status_table(rows: Sequence[Mapping[str, Any]]) -> list[InterfaceStatus]:
    """``show interfaces status`` (IOS, NX-OS, EOS): PORT / STATUS / DUPLEX / SPEED columns."""
    result = []
    for raw in rows:
        row = _row(raw)
        name = _text(row.get("PORT"))
        if not name:
            continue
        speed, speed_auto = parse_speed(_text(row.get("SPEED")))
        duplex, duplex_auto = parse_duplex(_text(row.get("DUPLEX")))
        result.append(
            InterfaceStatus(
                name=name,
                status=parse_link_state(_text(row.get("STATUS"))),
                speed=speed,
                duplex=duplex,
                auto=speed_auto if speed_auto is not None else duplex_auto,
            )
        )
    return result


def parse_huawei_interface(rows: Sequence[Mapping[str, Any]]) -> list[InterfaceStatus]:
    """Huawei VRP ``display interface``: LINK_STATUS, SPEED (Mbps), DUPLEX, NEGOTIATION."""
    result = []
    for raw in rows:
        row = _row(raw)
        name = _text(row.get("INTERFACE"))
        if not name:
            continue
        speed, _ = parse_speed(_text(row.get("SPEED")))
        duplex, _ = parse_duplex(_text(row.get("DUPLEX")))
        negotiation = _text(row.get("NEGOTIATION")).lower()
        result.append(
            InterfaceStatus(
                name=name,
                status=parse_link_state(_text(row.get("LINK_STATUS"))),
                speed=speed,
                duplex=duplex,
                auto=(negotiation == "enable") if negotiation else None,
            )
        )
    return result


def parse_aoscx_interface(rows: Sequence[Mapping[str, Any]]) -> list[InterfaceStatus]:
    """Aruba AOS-CX ``show interface``: LINK_STATUS, SPEED (``1000 Mb/s``), DUPLEX, AUTO_NEG."""
    result = []
    for raw in rows:
        row = _row(raw)
        name = _text(row.get("INTERFACE"))
        if not name:
            continue
        admin = _text(row.get("LINK_ADMIN")).lower()
        state = parse_link_state(_text(row.get("LINK_STATUS")))
        if admin == "down":
            state = "disabled"
        speed, _ = parse_speed(_text(row.get("SPEED")))
        duplex, _ = parse_duplex(_text(row.get("DUPLEX")))
        auto = _text(row.get("AUTO_NEG")).lower()
        result.append(
            InterfaceStatus(
                name=name,
                status=state,
                speed=speed,
                duplex=duplex,
                auto=(auto == "on") if auto else None,
            )
        )
    return result


def parse_xr_interfaces(rows: Sequence[Mapping[str, Any]]) -> list[InterfaceStatus]:
    """IOS-XR ``show interfaces``: LINK_STATUS, SPEED (``1000Mb/s``), DUPLEX."""
    result = []
    for raw in rows:
        row = _row(raw)
        name = _text(row.get("INTERFACE"))
        if not name:
            continue
        speed, _ = parse_speed(_text(row.get("SPEED")))
        duplex, _ = parse_duplex(_text(row.get("DUPLEX")))
        result.append(
            InterfaceStatus(
                name=name,
                status=parse_link_state(_text(row.get("LINK_STATUS"))),
                speed=speed,
                duplex=duplex,
            )
        )
    return result


def parse_vyos_interfaces(rows: Sequence[Mapping[str, Any]]) -> list[InterfaceStatus]:
    """VyOS ``show interfaces``: INTERFACE, STATUS (``u/u``, ``A/D``, ...), DESCRIPTION."""
    result = []
    for raw in rows:
        row = _row(raw)
        name = _text(row.get("INTERFACE"))
        if not name or name == "lo":
            continue
        status_raw = _text(row.get("STATUS")).lower()
        if "a" in status_raw:
            state = "disabled"
        elif "u" in status_raw:
            state = "up"
        elif "d" in status_raw:
            state = "down"
        else:
            state = parse_link_state(status_raw)
        result.append(
            InterfaceStatus(
                name=name,
                status=state,
            )
        )
    return result


def parse_junos_interfaces(rows: Sequence[Mapping[str, Any]]) -> list[InterfaceStatus]:
    """Juniper Junos ``show interfaces``: INTERFACE, ADMIN_STATE, LINK_STATUS, SPEED, DUPLEX."""
    result = []
    for raw in rows:
        row = _row(raw)
        name = _text(row.get("INTERFACE"))
        if not name:
            continue
        admin = _text(row.get("ADMIN_STATE")).lower()
        link = _text(row.get("LINK_STATUS")).lower()
        if "down" in admin or "disabled" in admin:
            state = "disabled"
        elif "up" in link:
            state = "up"
        elif "down" in link:
            state = "down"
        else:
            state = parse_link_state(link)
        speed, _ = parse_speed(_text(row.get("SPEED")))
        duplex, _ = parse_duplex(_text(row.get("DUPLEX")))
        result.append(
            InterfaceStatus(
                name=name,
                status=state,
                speed=speed,
                duplex=duplex,
            )
        )
    return result
