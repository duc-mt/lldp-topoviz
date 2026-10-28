"""
==============================================================================
Module Name:   parser.py
Description:   Turns one device's poll result into normalised neighbor records
               (LLDP, CDP, ... merged), port-channels, interface status and the
               VLAN setup of the ports (switchport mode, native / allowed VLANs). Live
               polls arrive already parsed by Netmiko's use_textfsm=True; replayed
               text goes through the same Netmiko TextFSM helper and templates.
Author:        Mai Tan Duc <ducmai.network@gmail.com>
Created:       2026-10-10
Version:       1.3.0
License:       MIT
==============================================================================
Usage:         imported by main.py / crawler.py (not run directly)
Notes:         Requires Python 3.10+
==============================================================================
"""

from __future__ import annotations

import logging
import os
import re
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from src import MapperError, normalize_interface, short_name
from src.models import PortVlanInfo
from src.poller import RawLldp
from src.textparse import TemplateMismatch, parse_text
from src.vendors import get_profile, merge_neighbors, row_to_neighbor
from src.vendors.base import (
    KNOWN_CAPABILITIES,
    InterfaceStatus,
    LldpNeighbor,
    ParserError,
    PortChannel,
)

__all__ = [
    "ENDPOINT_AP",
    "ENDPOINT_GENERIC",
    "ENDPOINT_LABELS",
    "ENDPOINT_PHONE",
    "ENDPOINT_SERVER",
    "BaseParser",
    "LldpParser",
    "ParsedDevice",
    "ParserError",
    "classify_endpoint",
]

logger = logging.getLogger(__name__)

# ------------------------------------------------------------ leaf endpoint classes
ENDPOINT_PHONE = "endpoint_phone"
ENDPOINT_AP = "endpoint_ap"
ENDPOINT_SERVER = "endpoint_server"
ENDPOINT_GENERIC = "endpoint_generic"
ENDPOINT_LABELS = {
    ENDPOINT_PHONE: "IP phone",
    ENDPOINT_AP: "WiFi access point",
    ENDPOINT_SERVER: "server / host",
    ENDPOINT_GENERIC: "end device",
}
# High-precision wording only: a leaf misread as infrastructure silently drops a device from the
# coverage audit, so these match the phrases phones and APs really send, not loose keywords.
_PHONE_HINT = re.compile(r"\bip[- ]?phone\b|\bvoip phone\b|\bsip phone\b", re.IGNORECASE)
_AP_HINT = re.compile(r"\baccess point\b|\baironet\b|\bAIR-(?:AP|CAP|LAP)", re.IGNORECASE)
_GENERIC_HINT = re.compile(
    r"\bip[- ]?cam(?:era)?\b|\bnetwork camera\b|\bprinter\b|\blaserjet\b|\bjetdirect\b",
    re.IGNORECASE,
)


def classify_endpoint(neighbor: LldpNeighbor) -> str:
    """Classify a neighbor as a leaf endpoint that must never be logged in to.

    Returns ``endpoint_phone``, ``endpoint_ap``, ``endpoint_server`` or ``endpoint_generic``,
    or ``""`` when the neighbor may be network infrastructure (or nothing says otherwise).

    * A telephone or WLAN-AP capability is decisive even next to ``bridge``: IP phones with a
      PC port advertise bridge + telephone, and access points often advertise bridge or router.
      The same goes for a CDP platform / system description that names a phone or access point.
    * Otherwise a neighbor advertising ``bridge`` or ``router`` is infrastructure.
    * A neighbor advertising only end-device capabilities (station, other, repeater, docsis) is
      a leaf: ``endpoint_server`` for station, ``endpoint_generic`` for the rest.
    * A neighbor advertising nothing is *not* assumed to be a leaf (some switch templates carry
      no capabilities at all) unless it names itself a camera or printer.
    """
    caps = neighbor.capabilities
    text = f"{neighbor.platform} {neighbor.description}"
    if "telephone" in caps or _PHONE_HINT.search(text):
        return ENDPOINT_PHONE
    if "wlan" in caps or _AP_HINT.search(text):
        return ENDPOINT_AP
    if caps & {"router", "bridge"}:
        return ""
    if caps & KNOWN_CAPABILITIES:
        return ENDPOINT_SERVER if "station" in caps else ENDPOINT_GENERIC
    return ENDPOINT_GENERIC if _GENERIC_HINT.search(text) else ""


@dataclass
class ParsedDevice:
    """One device and the neighbors it reported (all discovery protocols merged)."""

    host: str
    device_type: str
    hostname: str  # the device's own name (label case), "" when unknown
    collected_at: str
    neighbors: list[LldpNeighbor] = field(default_factory=list)
    rows_seen: int = 0  # LLDP rows
    port_channels: list[PortChannel] = field(default_factory=list)
    lag_note: str = ""  # why port_channels is empty although the device may have bundles
    protocols_used: list[str] = field(default_factory=list)
    protocol_notes: dict[str, str] = field(default_factory=dict)  # protocol -> why it gave nothing
    interfaces: dict[str, InterfaceStatus] = field(default_factory=dict)  # normalised port -> state
    status_note: str = ""  # why interfaces is empty
    vlan_ports: dict[str, PortVlanInfo] = field(default_factory=dict)  # normalised port -> VLANs
    vlan_note: str = ""  # why vlan_ports is empty


class BaseParser(ABC):
    """Interface for parsers; keeps the pipeline independent of the data source."""

    @abstractmethod
    def parse(self, raw: RawLldp) -> ParsedDevice:
        """Return the neighbors found in one poll result."""


class LldpParser(BaseParser):
    """Normalise TextFSM rows into neighbor records for any registered platform."""

    def __init__(self, template_dirs: Sequence[str | os.PathLike] = ()) -> None:
        self.template_dirs = [Path(d) for d in template_dirs]

    # ------------------------------------------------------------------ parse
    def parse(self, raw: RawLldp) -> ParsedDevice:
        """Parse one poll result.

        LLDP and every other discovery protocol are parsed separately and merged. A protocol that
        gave nothing is only a note as long as another one worked.

        Raises:
            ParserError: If no discovery protocol produced usable output.
        """
        try:
            profile = get_profile(raw.device_type)
        except MapperError as exc:
            raise ParserError(f"{raw.host}: {exc}") from exc
        protocols = profile.protocol_profiles()
        has_extras = any(d.rows is not None or d.text.strip() for d in raw.extras.values())

        groups: list[list[LldpNeighbor]] = []
        notes: dict[str, str] = {}
        used: list[str] = []
        rows_seen = 0

        lldp = protocols.get("lldp")
        if lldp is not None:
            rows = raw.rows
            if rows is None and raw.text.strip():
                try:
                    rows = self._textfsm(
                        raw.host, raw.command, lldp.resolve_template(self.template_dirs), raw.text
                    )
                except ParserError as exc:
                    if not has_extras:
                        raise
                    notes["lldp"] = str(exc)
            elif rows is None:
                if not (raw.lldp_note or has_extras):
                    raise ParserError(
                        f"{raw.host}: the device returned no output for '{raw.command}'"
                    )
                notes["lldp"] = raw.lldp_note or "no LLDP output"
            if rows is not None:
                groups.append(self._neighbors(raw.host, "lldp", lldp.fields, rows))
                used.append("lldp")
                rows_seen = len(rows)

        for name, data in raw.extras.items():
            proto = protocols.get(name)
            if proto is None:
                notes[name] = f"{name.upper()} is not known for {raw.device_type}"
                continue
            rows = data.rows
            if rows is None:
                if not data.text.strip():
                    notes[name] = data.note or f"no {name.upper()} output"
                    continue
                try:
                    rows = self._textfsm(
                        raw.host,
                        proto.command,
                        proto.resolve_template(self.template_dirs),
                        data.text,
                    )
                except (ParserError, MapperError) as exc:
                    notes[name] = str(exc)
                    continue
            groups.append(self._neighbors(raw.host, name, proto.fields, rows))
            used.append(name)

        if not used:
            reasons = "; ".join(f"{n}: {r}" for n, r in notes.items()) or "no output"
            raise ParserError(f"{raw.host}: no discovery protocol could be read ({reasons})")
        for name, note in notes.items():
            logger.info("%s: %s not used (%s)", raw.host, name.upper(), note)

        # Classified after the merge: LLDP capabilities and the CDP platform are judged together.
        neighbors = [
            replace(neighbor, endpoint_class=classify_endpoint(neighbor))
            for neighbor in merge_neighbors(groups)
        ]
        if not neighbors:
            logger.warning(
                "%s reported no neighbors via %s (is discovery enabled on it?)",
                raw.host,
                ", ".join(p.upper() for p in used),
            )
        channels, lag_note = self._parse_lag(raw, profile)
        if lag_note:
            logger.info("%s: no port-channel data (%s)", raw.host, lag_note)
        interfaces, status_note = self._parse_status(raw, profile)
        if status_note:
            logger.info("%s: no interface status (%s)", raw.host, status_note)
        vlan_ports, vlan_note = self._parse_vlan(raw, profile)
        if vlan_note:
            logger.info("%s: no VLAN data (%s)", raw.host, vlan_note)
        if vlan_ports:  # each neighbor remembers the VLAN setup of the port it was seen on
            neighbors = [
                replace(
                    n,
                    local_vlan_info=vlan_ports.get(normalize_interface(n.local_interface).lower()),
                )
                for n in neighbors
            ]
        return ParsedDevice(
            host=raw.host,
            device_type=raw.device_type,
            hostname=short_name(raw.hostname),
            collected_at=raw.collected_at,
            neighbors=neighbors,
            rows_seen=rows_seen,
            port_channels=channels,
            lag_note=lag_note,
            protocols_used=used,
            protocol_notes=notes,
            interfaces=interfaces,
            status_note=status_note,
            vlan_ports=vlan_ports,
            vlan_note=vlan_note,
        )

    @staticmethod
    def _neighbors(
        host: str, protocol: str, fields: Any, rows: Sequence[dict[str, Any]]
    ) -> list[LldpNeighbor]:
        neighbors = []
        for row in rows:
            neighbor = row_to_neighbor(row, fields, protocol)
            if neighbor is None:
                logger.debug("%s: skipped a %s row with no identity: %s", host, protocol, row)
                continue
            neighbors.append(neighbor)
        return neighbors

    def _textfsm(self, host: str, command: str, template: Path, text: str) -> list[dict[str, Any]]:
        """Parse raw text (replay, or a live parse that did not match) with a TextFSM template."""
        if not text.strip():
            raise ParserError(f"{host}: the device returned no output for '{command}'")
        try:
            return parse_text(text, template)
        except TemplateMismatch as exc:
            raise ParserError(
                f"{host}: the output of '{command}' does not match the {template.name} template "
                f"({exc}). Wrong device_type?"
            ) from exc

    # --------------------------------------------------------- enrichment data
    def _enrichment_rows(
        self,
        raw: RawLldp,
        kind: str,
        profile_part: Any,
        rows: list[dict[str, Any]] | None,
        text: str,
        note: str,
    ) -> tuple[list[dict[str, Any]] | None, str]:
        """Rows of a port-channel / status command, or ``(None, why not)``. Never raises."""
        if profile_part is None:
            return None, note or f"no {kind} command is known for {raw.device_type}"
        if rows is not None:
            return rows, ""
        if note:
            return None, note
        if not text.strip():
            return None, f"no {kind} output"
        try:
            template = profile_part.resolve_template(self.template_dirs)
            return parse_text(text, template), ""
        except TemplateMismatch as exc:
            return (
                None,
                f"the output of '{profile_part.command}' did not match its template ({exc})",
            )
        except MapperError as exc:
            return None, f"the {kind} output could not be parsed: {exc}"

    def _parse_lag(self, raw: RawLldp, profile: Any) -> tuple[list[PortChannel], str]:
        """Port-channels of one device as ``(channels, note)``; any problem becomes the note."""
        rows, note = self._enrichment_rows(
            raw, "port-channel", profile.lag, raw.lag_rows, raw.lag_text, raw.lag_note
        )
        if rows is None:
            return [], note
        try:
            return profile.lag.parser(rows), ""
        except (KeyError, TypeError, ValueError) as exc:
            return [], f"the port-channel rows were not understood: {exc!r}"

    def _parse_status(self, raw: RawLldp, profile: Any) -> tuple[dict[str, InterfaceStatus], str]:
        """Operating state per port as ``({normalised port: status}, note)``."""
        rows, note = self._enrichment_rows(
            raw,
            "interface status",
            profile.status,
            raw.status_rows,
            raw.status_text,
            raw.status_note,
        )
        if rows is None:
            return {}, note
        try:
            parsed = profile.status.parser(rows)
        except (KeyError, TypeError, ValueError) as exc:
            return {}, f"the interface status rows were not understood: {exc!r}"
        return {normalize_interface(item.name).lower(): item for item in parsed}, ""

    def _parse_vlan(self, raw: RawLldp, profile: Any) -> tuple[dict[str, PortVlanInfo], str]:
        """VLAN setup per port as ``({normalised port: PortVlanInfo}, note)``; never raises."""
        rows, note = self._enrichment_rows(
            raw,
            "VLAN",
            profile.vlan,
            raw.vlan_rows,
            raw.vlan_text,
            raw.vlan_note,
        )
        if rows is None:
            return {}, note
        try:
            parsed = profile.vlan.parser(rows)
        except (KeyError, TypeError, ValueError) as exc:
            return {}, f"the VLAN rows were not understood: {exc!r}"
        return {normalize_interface(item.interface).lower(): item for item in parsed}, ""
