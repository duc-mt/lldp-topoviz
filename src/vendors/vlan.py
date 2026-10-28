"""
==============================================================================
Module Name:   vlan.py
Description:   Per-platform normalisers that turn the TextFSM rows of the
               switchport commands (show interfaces switchport on IOS / IOS-XE,
               show interface switchport on NX-OS) into PortVlanInfo records:
               the port mode, its access VLAN, its native VLAN and the VLANs a
               trunk allows, used for the VLAN consistency link checks.
Author:        Mai Tan Duc <ducmai.network@gmail.com>
Created:       2026-10-27
Version:       1.0.0
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

from src.models import VLAN_MAX, VLAN_MIN, PortVlanInfo, parse_vlan_list

__all__ = ["parse_switchport"]


def _row(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key).upper(): value for key, value in raw.items()}


def _text(value: Any) -> str:
    items = value if isinstance(value, (list, tuple)) else [value]
    return " ".join(str(items[0] if items else "").split())


def _vlan_id(value: Any) -> int | None:
    """One VLAN ID (``10``, ``10 (VLAN0010)``); ``unassigned`` / empty / out of range: None."""
    token = _text(value).split(" ", 1)[0]
    if not token.isdigit():
        return None
    number = int(token)
    return number if VLAN_MIN <= number <= VLAN_MAX else None


def _mode(switchport: str, operational: str, administrative: str) -> str:
    """``access`` / ``trunk`` / ``routed`` / ``unknown`` from the words a switchport entry uses.

    The operational mode wins; a port that is down reports ``down`` there, so the administrative
    mode decides. A dynamic (``dynamic auto`` / ``desirable``) port that is not up has no
    definite mode yet and stays ``unknown``.
    """
    if switchport.lower().startswith("disab"):
        return "routed"  # a Layer 3 port: not a switchport at all
    for text in (operational, administrative):
        words = text.lower()
        if "trunk" in words:
            return "trunk"
        if "access" in words:
            return "access"
    return "unknown"


def parse_switchport(rows: Sequence[Mapping[str, Any]]) -> list[PortVlanInfo]:
    """``show interfaces switchport`` (IOS, IOS-XE) / ``show interface switchport`` (NX-OS).

    Columns used: INTERFACE, SWITCHPORT, MODE (operational), ADMIN_MODE (IOS only), ACCESS_VLAN,
    NATIVE_VLAN and TRUNKING_VLANS (a list of pieces on IOS, one string on NX-OS).
    """
    result = []
    for raw in rows:
        row = _row(raw)
        name = _text(row.get("INTERFACE"))
        if not name:
            continue
        mode = _mode(
            _text(row.get("SWITCHPORT")),
            _text(row.get("MODE")),
            _text(row.get("ADMIN_MODE")),
        )
        result.append(
            PortVlanInfo(
                interface=name,
                mode=mode,
                access_vlan=_vlan_id(row.get("ACCESS_VLAN")) if mode == "access" else None,
                native_vlan=_vlan_id(row.get("NATIVE_VLAN")) if mode == "trunk" else None,
                allowed_vlans=(
                    parse_vlan_list(row.get("TRUNKING_VLANS")) if mode == "trunk" else None
                ),
            )
        )
    return result
