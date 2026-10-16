"""
==============================================================================
Module Name:   lag.py
Description:   Per-platform normalisers that turn the TextFSM rows of the
               port-channel commands (show etherchannel summary, show
               port-channel summary, display eth-trunk) into PortChannel records.
Author:        Mai Tan Duc <ducmai.network@gmail.com>
Created:       2026-10-11
Version:       1.2.0
License:       MIT
==============================================================================
Usage:         imported by src/vendors/__init__.py (not run directly)
Notes:         Requires Python 3.10+
==============================================================================
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.vendors.base import PortChannel, PortChannelMember, lag_member_state, lag_protocol


def _upper(row: Mapping[str, Any]) -> dict[str, Any]:
    """Netmiko returns lower-case column names, the templates declare them upper-case."""
    return {str(key).upper(): value for key, value in row.items()}


def _as_list(value: Any) -> list[str]:
    """TextFSM ``List`` values are lists; be tolerant of a bare string."""
    if value is None or value == "":
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    return [str(item).strip() for item in items if str(item).strip()]


def _is_up(status: str) -> bool:
    """Bundle flags: ``SU`` / ``RU`` / ``U`` are in use, ``SD`` / ``RD`` / ``D`` are down."""
    flags = status.strip()
    return "U" in flags or not flags


def _flag_members(row: Mapping[str, Any]) -> tuple[PortChannelMember, ...]:
    """Members of a Cisco-style row: parallel lists of interface names and flag strings."""
    interfaces = _as_list(row.get("MEMBER_INTERFACE"))
    states = _as_list(row.get("MEMBER_INTERFACE_STATUS"))
    members = []
    for index, interface in enumerate(interfaces):
        if interface == "--":  # NX-OS filler for an empty slot
            continue
        state = lag_member_state(states[index] if index < len(states) else "")
        members.append(PortChannelMember(interface=interface, state=state))
    return tuple(members)


def parse_flag_style(rows: Sequence[Mapping[str, Any]]) -> list[PortChannel]:
    """IOS / IOS-XE, NX-OS and EOS: one row per bundle with member lists."""
    channels = []
    for raw_row in rows:
        row = _upper(raw_row)
        name = str(row.get("BUNDLE_NAME") or "").strip()
        if not name:
            continue
        channels.append(
            PortChannel(
                name=name,
                protocol=lag_protocol(row.get("BUNDLE_PROTOCOL")),
                up=_is_up(str(row.get("BUNDLE_STATUS") or "")),
                members=_flag_members(row),
            )
        )
    return channels


def parse_huawei(rows: Sequence[Mapping[str, Any]]) -> list[PortChannel]:
    """Huawei VRP ``display eth-trunk``: one row per member port, trunk fields filled down.

    The ``Partner:`` section yields extra rows without a ``STATUS``; they are ignored.
    """
    trunks: dict[str, dict[str, Any]] = {}
    for raw_row in rows:
        row = _upper(raw_row)
        name = str(row.get("ETH_TRUNK") or "").strip()
        status = str(row.get("STATUS") or "").strip()
        interface = str(row.get("INTERFACE") or "").strip()
        if not name:
            continue
        trunk = trunks.setdefault(
            name,
            {
                "mode": str(row.get("WORKING_MODE") or ""),
                "up": str(row.get("OPERATE_STATUS") or "up").lower() != "down",
                "members": [],
            },
        )
        if status and interface:
            trunk["members"].append(
                PortChannelMember(interface=interface, state=lag_member_state(status))
            )
    return [
        PortChannel(
            name=name,
            protocol=lag_protocol(trunk["mode"]),
            up=trunk["up"],
            members=tuple(trunk["members"]),
        )
        for name, trunk in trunks.items()
    ]
