"""
==============================================================================
Module Name:   linkcheck.py
Description:   Link health checks from what each end of a link reports about its
               own port: speed mismatch between the two ends, duplex mismatch,
               half duplex, a port running below its type (a Gigabit port at
               100 Mbps), port-channel members at different speeds and VLAN
               consistency (trunk against access, native VLAN mismatch, allowed
               VLAN lists that differ between the two ends of a trunk).
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
from src.models import PortVlanInfo, format_vlan_list

SEVERITY_MISMATCH = "mismatch"  # the two ends disagree: the link is misconfigured or flapping
SEVERITY_DEGRADED = "degraded"  # the link works but below what its ports can do


@dataclass(frozen=True)
class LinkCheckConfig:
    """Which links are checked (``link_checks:`` in settings.yaml)."""

    enabled: bool = True
    ignore_ports: tuple[re.Pattern[str], ...] = ()  # interface names, e.g. "^Fa" for 100M ports
    ignore_hostnames: tuple[re.Pattern[str], ...] = ()  # devices whose ports are never judged
    vlan: bool = True  # also compare the VLAN setup of the two ends (needs the VLAN capture)

    @classmethod
    def from_settings(cls, section: dict[str, Any] | None) -> LinkCheckConfig:
        """Build from the ``link_checks`` mapping (``vlan: false`` skips the VLAN comparison).

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
                vlan=bool(section.get("vlan", True)),
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

    # speed_mismatch | duplex_mismatch | half_duplex | below_nominal | bundle_speed_mix, and the
    # VLAN findings vlan_mode_mismatch | vlan_native_mismatch | vlan_allowed_mismatch
    kind: str
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


KIND_VLAN_MODE = "vlan_mode_mismatch"
KIND_VLAN_NATIVE = "vlan_native_mismatch"
KIND_VLAN_ALLOWED = "vlan_allowed_mismatch"
VLAN_KINDS = frozenset({KIND_VLAN_MODE, KIND_VLAN_NATIVE, KIND_VLAN_ALLOWED})


def is_vlan_warning(kind: str) -> bool:
    """True for a VLAN finding (the diagram marks those ``[VLAN]``)."""
    return kind in VLAN_KINDS


def _vlan_words(vlans: set[int]) -> str:
    return format_vlan_list(vlans) or "none"


def check_vlan_consistency(
    local: PortVlanInfo,
    remote: PortVlanInfo,
    local_label: str = "",
    remote_label: str = "",
) -> list[LinkWarning]:
    """Compare the VLAN setup of the two ends of an inter-switch link.

    * trunk on one end, access on the other: ``mismatch`` (the link carries one VLAN untagged on
      one side and tagged frames on the other);
    * both ends trunk with different native VLANs: ``mismatch`` (untagged traffic leaks between
      the two VLANs);
    * both ends trunk and one allows VLANs the other does not (the symmetric difference of the
      two allowed lists): ``degraded`` (the link works, but those VLANs are cut on this hop).

    A port that is ``routed`` or ``unknown`` on either end is never judged, and neither is a
    value one end did not report (a ``None`` native VLAN or allowed list). ``local_label`` and
    ``remote_label`` are the device names used in the text (the interface alone when omitted).
    """
    a = f"{local_label} {local.interface}".strip()
    b = f"{remote_label} {remote.interface}".strip()
    modes = {local.mode, remote.mode}
    if modes != {"access", "trunk"} and not (local.mode == remote.mode == "trunk"):
        return []
    warnings: list[LinkWarning] = []
    if modes == {"access", "trunk"}:
        warnings.append(
            LinkWarning(
                KIND_VLAN_MODE,
                SEVERITY_MISMATCH,
                f"VLAN mode mismatch: {a} is {local.mode} but {b} is {remote.mode}",
            )
        )
        return warnings
    if local.native_vlan and remote.native_vlan and local.native_vlan != remote.native_vlan:
        warnings.append(
            LinkWarning(
                KIND_VLAN_NATIVE,
                SEVERITY_MISMATCH,
                f"Native VLAN mismatch: {a} uses VLAN {local.native_vlan} but "
                f"{b} uses VLAN {remote.native_vlan}",
            )
        )
    if local.allowed_vlans is not None and remote.allowed_vlans is not None:
        only_local = local.allowed_vlans - remote.allowed_vlans
        only_remote = remote.allowed_vlans - local.allowed_vlans
        if only_local or only_remote:
            parts = []
            if only_local:
                parts.append(f"VLAN {_vlan_words(only_local)} allowed on {a} but not on {b}")
            if only_remote:
                parts.append(f"VLAN {_vlan_words(only_remote)} allowed on {b} but not on {a}")
            warnings.append(
                LinkWarning(
                    KIND_VLAN_ALLOWED,
                    SEVERITY_DEGRADED,
                    "Allowed VLAN mismatch: " + "; ".join(parts),
                )
            )
    return warnings
