"""
==============================================================================
Module Name:   parser.py
Description:   Turns one device's poll result into normalised neighbor records
               (LLDP, CDP, ... merged), port-channels and interface status. Live
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
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src import MapperError, normalize_interface, short_name
from src.poller import RawLldp
from src.textparse import TemplateMismatch, parse_text
from src.vendors import get_profile, merge_neighbors, row_to_neighbor
from src.vendors.base import InterfaceStatus, LldpNeighbor, ParserError, PortChannel

__all__ = ["BaseParser", "LldpParser", "ParsedDevice", "ParserError"]

logger = logging.getLogger(__name__)


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

        neighbors = merge_neighbors(groups)
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
