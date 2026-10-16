"""
==============================================================================
Module Name:   linkcheck.py
Description:   Link health checks from what each end of a link reports about its
               own port: speed mismatch between the two ends, duplex mismatch,
               half duplex, a port running below its type (a Gigabit port at
               100 Mbps) and port-channel members at different speeds.
Author:        Mai Tan Duc <ducmai.network@gmail.com>
Created:       2026-10-12
Version:       1.3.0
License:       MIT
==============================================================================
Usage:         imported by src/graph_engine.py / src/crawler.py (not run directly)
Notes:         Requires Python 3.10+. Pure functions. A port that is not up is never
               judged: its speed and duplex mean nothing.
==============================================================================
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from src import normalize_interface
from src.config import ConfigError
from src.enrichment import link_speed, port_speed, speed_label

SEVERITY_MISMATCH = "mismatch"  # the two ends disagree: the link is misconfigured or flapping
SEVERITY_DEGRADED = "degraded"  # the link works but below what its ports can do


@dataclass(frozen=True)
class LinkCheckConfig:
    """Which links are checked (``link_checks:`` in settings.yaml)."""

    enabled: bool = True
    ignore_ports: tuple[re.Pattern[str], ...] = ()  # interface names, e.g. "^Fa" for 100M ports
    ignore_hostnames: tuple[re.Pattern[str], ...] = ()  # devices whose ports are never judged

    @classmethod
    def from_settings(cls, section: dict[str, Any] | None) -> LinkCheckConfig:
        """Build from the ``link_checks`` mapping.

        Raises:
            ConfigError: If the section is not a mapping or a regex is invalid.
        """
        if section is None:
            return cls()
        if not isinstance(section, dict):
            raise ConfigError("'link_checks' in the settings file must be a mapping")
        try:
            return cls(
                enabled=bool(section.get("enabled", True)),
                ignore_ports=tuple(
                    re.compile(str(p), re.IGNORECASE) for p in section.get("ignore_ports") or []
                ),
                ignore_hostnames=tuple(
                    re.compile(str(p), re.IGNORECASE) for p in section.get("ignore_hostnames") or []
                ),
            )
        except re.error as exc:
            raise ConfigError(f"link_checks: invalid regex ({exc})") from exc

    def ignores(self, label: str, port: str) -> bool:
        """True if this end of a link must not be judged."""
        short = normalize_interface(port)
        return any(p.search(port) or p.search(short) for p in self.ignore_ports) or any(
            p.search(label) for p in self.ignore_hostnames
        )


@dataclass(frozen=True)
class End:
    """One end of a link as the device on that end reports it."""

    label: str  # device name
    port: str
    speed: int | None = None  # Mbps in use
    duplex: str | None = None  # full | half
    status: str | None = None  # up | down | ...

    @property
    def usable(self) -> bool:
        """Speed and duplex only mean something on a port that is up."""
        return self.status in (None, "unknown", "up")


@dataclass(frozen=True)
class LinkWarning:
    """One finding about a link."""

    kind: str  # speed_mismatch | duplex_mismatch | half_duplex | below_nominal | bundle_speed_mix
    severity: str
    text: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "severity": self.severity, "text": self.text}


def check_link(ends: Sequence[End]) -> list[LinkWarning]:
    """Check one link from the ends that reported on their port (one or two)."""
    ends = [e for e in ends if e.usable]
    warnings: list[LinkWarning] = []
    mismatch = False

    if len(ends) == 2:
        a, b = ends
        if a.speed and b.speed and a.speed != b.speed:
            mismatch = True
            warnings.append(
                LinkWarning(
                    "speed_mismatch",
                    SEVERITY_MISMATCH,
                    f"Speed mismatch: {a.label} {a.port} runs at {speed_label(a.speed)} but "
                    f"{b.label} {b.port} at {speed_label(b.speed)}",
                )
            )
        if a.duplex and b.duplex and a.duplex != b.duplex:
            warnings.append(
                LinkWarning(
                    "duplex_mismatch",
                    SEVERITY_MISMATCH,
                    f"Duplex mismatch: {a.label} {a.port} is {a.duplex} but "
                    f"{b.label} {b.port} is {b.duplex}",
                )
            )
            half = []
        else:
            half = [e for e in ends if e.duplex == "half"]
    else:
        half = [e for e in ends if e.duplex == "half"]
    for end in half:
        warnings.append(
            LinkWarning("half_duplex", SEVERITY_DEGRADED, f"{end.label} {end.port} is half duplex")
        )

    if not mismatch:
        slow = [
            e for e in ends if e.speed and (nominal := port_speed(e.port)) and e.speed < nominal
        ]
        if len(slow) == 2 and slow[0].speed == slow[1].speed:
            a, b = slow
            warnings.append(
                LinkWarning(
                    "below_nominal",
                    SEVERITY_DEGRADED,
                    f"{a.label} {a.port} and {b.label} {b.port} run at {speed_label(a.speed)} "
                    f"but the ports are {speed_label(port_speed(a.port))}",
                )
            )
        else:
            for end in slow:
                warnings.append(
                    LinkWarning(
                        "below_nominal",
                        SEVERITY_DEGRADED,
                        f"{end.label} {end.port} runs at {speed_label(end.speed)} but the port "
                        f"is {speed_label(port_speed(end.port))}",
                    )
                )
    return warnings


def operating_speed(ends: Sequence[End]) -> int | None:
    """Speed in use on a link: the slowest known operating speed of its usable ends."""
    known = [e.speed for e in ends if e.usable and e.speed]
    return min(known) if known else None


def effective_speed(ends: Sequence[End], port_a: str, port_b: str) -> tuple[int | None, str]:
    """``(Mbps, source)``: what the link runs at if known, else the nominal speed of its ports.

    ``source`` is ``operating``, ``nominal`` or ``unknown``.
    """
    running = operating_speed(ends)
    if running:
        return running, "operating"
    nominal = link_speed(port_a, port_b)
    return (nominal, "nominal") if nominal else (None, "unknown")


def check_bundle(member_speeds: Sequence[int | None]) -> list[LinkWarning]:
    """Port-channel members that run at different speeds."""
    known = sorted({s for s in member_speeds if s})
    if len(known) < 2:
        return []
    return [
        LinkWarning(
            "bundle_speed_mix",
            SEVERITY_MISMATCH,
            "Port-channel members run at different speeds ("
            + ", ".join(speed_label(s) for s in known)
            + ")",
        )
    ]
