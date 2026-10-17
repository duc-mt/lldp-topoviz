"""
==============================================================================
Module Name:   __init__.py
Description:   Vendor profile registry: per-platform LLDP command, TextFSM
               template, column map and error markers; custom vendors from the
               settings file; device-type guessing from the LLDP system description.
Author:        Mai Tan Duc <ducmai.network@gmail.com>
Created:       2026-10-10
Version:       1.0.0
License:       MIT
==============================================================================
Usage:         imported by src/poller.py, src/parser.py and src/crawler.py
Notes:         Requires Python 3.10+
==============================================================================
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from netmiko.utilities import get_template_dir

from src import MapperError
from src.config import ConfigError
from src.vendors.base import (
    FieldMap,
    InterfaceStatus,
    LldpNeighbor,
    ParserError,
    PortChannel,
    PortChannelMember,
    merge_neighbors,
    row_to_neighbor,
)
from src.vendors.lag import parse_flag_style, parse_huawei, parse_junos_lacp
from src.vendors.status import (
    parse_aoscx_interface,
    parse_huawei_interface,
    parse_junos_interfaces,
    parse_status_table,
    parse_vyos_interfaces,
    parse_xr_interfaces,
)

__all__ = [
    "PROFILES",
    "FieldMap",
    "InterfaceStatus",
    "LagProfile",
    "LldpNeighbor",
    "ParserError",
    "PortChannel",
    "PortChannelMember",
    "ProtocolProfile",
    "StatusProfile",
    "UnsupportedVendorError",
    "VendorProfile",
    "get_profile",
    "guess_device_type",
    "is_supported",
    "load_custom_vendors",
    "merge_neighbors",
    "register",
    "row_to_neighbor",
    "supported_device_types",
]

logger = logging.getLogger(__name__)


class UnsupportedVendorError(MapperError):
    """Raised for a device_type that has no registered profile."""

    kind = "unsupported"  # failure class shown in the diagram


def _get_builtin_templates_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "templates"
    return Path(__file__).resolve().parent.parent.parent / "templates"


_BUILTIN_TEMPLATES_DIR = _get_builtin_templates_dir()


def _resolve_template(template: str, label: str, search_dirs: Sequence[Path]) -> Path:
    """Locate a TextFSM template: an absolute path, search dirs, templates/, or ntc-templates."""
    candidates: list[Path] = []
    own = Path(template)
    if own.is_absolute():
        candidates.append(own)
    candidates.extend(Path(d) / template for d in search_dirs)
    candidates.append(_BUILTIN_TEMPLATES_DIR / template)
    candidates.append(_BUILTIN_TEMPLATES_DIR / own.name)
    candidates.append(Path(get_template_dir()) / template)
    for candidate in candidates:
        if candidate.is_file():
            logger.debug("Resolved template '%s' for %s -> %s", template, label, candidate)
            return candidate
    raise ConfigError(
        f"TextFSM template '{template}' for {label} was not found "
        f"(looked in: {', '.join(str(c.parent) for c in candidates)})"
    )


@dataclass(frozen=True)
class LagProfile:
    """How to read a platform's port-channels (an extra command after the LLDP one)."""

    command: str
    template: str
    parser: Callable[[Sequence[Mapping[str, Any]]], list[PortChannel]]
    error_markers: tuple[str, ...] = ()

    def resolve_template(self, search_dirs: Sequence[Path] = ()) -> Path:
        return _resolve_template(self.template, f"'{self.command}'", search_dirs)


@dataclass(frozen=True)
class ProtocolProfile:
    """One neighbor-discovery protocol on a platform (``lldp``, ``cdp``, ``fdp``, ...)."""

    name: str
    command: str
    template: str
    fields: FieldMap
    error_markers: tuple[str, ...] = ()

    def resolve_template(self, search_dirs: Sequence[Path] = ()) -> Path:
        return _resolve_template(self.template, f"'{self.command}'", search_dirs)


@dataclass(frozen=True)
class StatusProfile:
    """How to read what each port is really doing: operating state, speed and duplex."""

    command: str
    template: str
    parser: Callable[[Sequence[Mapping[str, Any]]], list[InterfaceStatus]]
    error_markers: tuple[str, ...] = ()

    def resolve_template(self, search_dirs: Sequence[Path] = ()) -> Path:
        return _resolve_template(self.template, f"'{self.command}'", search_dirs)


@dataclass(frozen=True)
class VendorProfile:
    """Everything platform-specific about discovering neighbors on a device.

    ``command`` / ``template`` / ``fields`` describe LLDP (leave ``command`` empty for a
    platform that is only read through another protocol). ``template`` is either the file name
    of an ntc-templates template or the path of a custom TextFSM template. It is passed to
    Netmiko explicitly, so it does not matter that Netmiko device types and ntc-templates
    platform names differ (e.g. ``huawei`` vs ``huawei_vrp``).

    ``protocols`` adds vendor protocols (CDP, FDP, ...) that are read in the same session and
    merged with LLDP. ``lag`` and ``status`` (optional) describe the port-channel and
    interface-status commands; without them the device's links are drawn without bundles or
    link checks.
    """

    key: str
    description: str
    command: str
    template: str
    fields: FieldMap
    error_markers: tuple[str, ...] = field(default_factory=tuple)
    lag: LagProfile | None = None
    protocols: tuple[ProtocolProfile, ...] = ()
    status: StatusProfile | None = None

    def resolve_template(self, search_dirs: Sequence[Path] = ()) -> Path:
        """Locate the (LLDP) TextFSM template file.

        Search order: the path itself (if absolute), each of ``search_dirs``, then the
        ntc-templates directory.

        Raises:
            ConfigError: If the template cannot be found anywhere.
        """
        return _resolve_template(self.template, f"device_type '{self.key}'", search_dirs)

    def protocol_profiles(self) -> dict[str, ProtocolProfile]:
        """Every discovery protocol of the platform by name, LLDP first (if it has one)."""
        result: dict[str, ProtocolProfile] = {}
        if self.command:
            result["lldp"] = ProtocolProfile(
                "lldp", self.command, self.template, self.fields, self.error_markers
            )
        for protocol in self.protocols:
            result[protocol.name] = protocol
        return result


# ------------------------------------------------------------------- registry
PROFILES: dict[str, VendorProfile] = {}


def register(profile: VendorProfile, *device_types: str) -> None:
    """Register ``profile`` under one or more Netmiko device types."""
    for device_type in device_types or (profile.key,):
        PROFILES[device_type] = profile


_IOS_ERRORS = (
    "Invalid input detected",
    "% Incomplete command",
    "% Ambiguous command",
    "% Unknown command",
)

register(
    VendorProfile(
        key="cisco_ios",
        description="Cisco IOS / IOS-XE",
        command="show lldp neighbors detail",
        template="cisco_ios_show_lldp_neighbors_detail.textfsm",
        fields=FieldMap(
            neighbor_interface=("NEIGHBOR_PORT_ID", "NEIGHBOR_INTERFACE"),
        ),
        error_markers=_IOS_ERRORS,
    ),
    "cisco_ios",
    "cisco_xe",
)
register(
    VendorProfile(
        key="cisco_nxos",
        description="Cisco NX-OS",
        command="show lldp neighbors detail",
        template="cisco_nxos_show_lldp_neighbors_detail.textfsm",
        fields=FieldMap(),
        error_markers=_IOS_ERRORS + ("Invalid command", "% Invalid"),
    )
)
register(
    VendorProfile(
        key="cisco_xr",
        description="Cisco IOS-XR",
        command="show lldp neighbors detail",
        template="cisco_xr_show_lldp_neighbors_detail.textfsm",
        fields=FieldMap(
            neighbor_name=("NEIGHBOR",),
            neighbor_interface=("NEIGHBOR_PORT_ID", "NEIGHBOR_PORT_DESCRIPTION"),
            mgmt_address=("MANAGEMENT_IP",),
            description=("SYSTEM_DESCRIPTION",),
        ),
        error_markers=_IOS_ERRORS,
    )
)
register(
    VendorProfile(
        key="arista_eos",
        description="Arista EOS (the template carries no capabilities)",
        command="show lldp neighbors detail",
        template="arista_eos_show_lldp_neighbors_detail.textfsm",
        fields=FieldMap(),
        error_markers=("% Invalid input", "% Incomplete command", "% Ambiguous command"),
    )
)
register(
    VendorProfile(
        key="aruba_aoscx",
        description="Aruba AOS-CX",
        command="show lldp neighbor-info detail",
        template="aruba_aoscx_show_lldp_neighbors-info_detail.textfsm",
        fields=FieldMap(
            neighbor_interface=("NEIGHBOR_PORT_ID", "NEIGHBOR_INTERFACE"),
        ),
        error_markers=("Invalid input", "% Unknown command", "Command incomplete"),
    )
)
register(
    VendorProfile(
        key="huawei",
        description="Huawei VRP",
        command="display lldp neighbor-information verbose",
        template="huawei_vrp_display_lldp_neighbor.textfsm",
        fields=FieldMap(
            neighbor_interface=("NEIGHBOR_PORT_ID", "NEIGHBOR_INTERFACE"),
        ),
        error_markers=(
            "Error: Wrong parameter",
            "Error: Unrecognized command",
            "Error: Incomplete command",
        ),
    ),
    "huawei",
    "huawei_vrp",
    "huawei_vrpv8",
)

register(
    VendorProfile(
        key="vyos",
        description="VyOS / FRR (text parser)",
        command="show lldp neighbors detail",
        template="vyos_show_lldp_neighbors_detail.textfsm",
        fields=FieldMap(
            neighbor_interface=("NEIGHBOR_PORT_ID", "NEIGHBOR_INTERFACE"),
            description=("SYSTEM_DESCRIPTION",),
        ),
        error_markers=(
            "Invalid command",
            "Unknown command",
            "Command incomplete",
            "% Unknown command",
            "% Command incomplete",
        ),
    )
)
register(
    VendorProfile(
        key="juniper_junos",
        description="Juniper Junos",
        command="show lldp neighbors detail",
        template="juniper_junos_show_lldp_neighbors_detail.textfsm",
        fields=FieldMap(
            neighbor_interface=("NEIGHBOR_PORT_ID", "NEIGHBOR_INTERFACE"),
            description=("SYSTEM_DESCRIPTION",),
        ),
        error_markers=("syntax error", "unknown command", "error:"),
    )
)
register(
    VendorProfile(
        key="hp_procurve",
        description="HP ProCurve / ArubaOS-S",
        command="show lldp info remote-device detail",
        template="hp_procurve_show_lldp_info_remote-device_detail.textfsm",
        fields=FieldMap(
            neighbor_name=("NEIGHBOR_NAME",),
            neighbor_interface=("NEIGHBOR_INTERFACE",),
            chassis_id=("CHASSIS_ID",),
            mgmt_address=("MGMT_ADDRESS",),
            capabilities=("CAPABILITIES",),
        ),
        error_markers=("Invalid input", "% Unknown command", "Command incomplete"),
        protocols=(
            ProtocolProfile(
                name="cdp",
                command="show cdp neighbors detail",
                template="hp_procurve_show_cdp_neighbors_detail.textfsm",
                fields=FieldMap(
                    neighbor_name=("CHASSIS_ID",),
                    neighbor_interface=("NEIGHBOR_INTERFACE",),
                    mgmt_address=("MGMT_ADDRESS",),
                    capabilities=("CAPABILITIES",),
                    description=("PLATFORM",),
                ),
                error_markers=("Invalid input", "% Unknown command", "Command incomplete"),
            ),
        ),
    )
)

_EOS_ERRORS = ("% Invalid input", "% Incomplete command", "% Ambiguous command")
_LAG_PROFILES = {
    ("cisco_ios", "cisco_xe"): LagProfile(
        "show etherchannel summary",
        "cisco_ios_show_etherchannel_summary.textfsm",
        parse_flag_style,
        _IOS_ERRORS,
    ),
    ("cisco_nxos",): LagProfile(
        "show port-channel summary",
        "cisco_nxos_show_port-channel_summary.textfsm",
        parse_flag_style,
        _IOS_ERRORS + ("Invalid command", "% Invalid"),
    ),
    ("arista_eos",): LagProfile(
        "show port-channel summary",
        "arista_eos_show_port-channel_summary.textfsm",
        parse_flag_style,
        _EOS_ERRORS,
    ),
    ("huawei", "huawei_vrp", "huawei_vrpv8"): LagProfile(
        "display eth-trunk",
        "huawei_vrp_display_eth-trunk.textfsm",
        parse_huawei,
        ("Error: Wrong parameter", "Error: Unrecognized command", "Error: Incomplete command"),
    ),
    ("juniper_junos",): LagProfile(
        "show lacp interfaces",
        "juniper_junos_show_lacp_interfaces.textfsm",
        parse_junos_lacp,
        ("syntax error", "unknown command", "error:"),
    ),
}
for _device_types, _lag in _LAG_PROFILES.items():
    for _device_type in _device_types:
        PROFILES[_device_type] = replace(PROFILES[_device_type], lag=_lag)

_CDP_FIELDS = FieldMap(
    chassis_id=(),
    mgmt_address=("MGMT_ADDRESS",),
    platform=("PLATFORM",),
)
_CDP_PROFILES = {
    ("cisco_ios", "cisco_xe"): ProtocolProfile(
        "cdp",
        "show cdp neighbors detail",
        "cisco_ios_show_cdp_neighbors_detail.textfsm",
        _CDP_FIELDS,
        _IOS_ERRORS,
    ),
    ("cisco_nxos",): ProtocolProfile(
        "cdp",
        "show cdp neighbors detail",
        "cisco_nxos_show_cdp_neighbors_detail.textfsm",
        replace(
            _CDP_FIELDS,
            neighbor_name=("NEIGHBOR_NAME", "CHASSIS_ID"),
            mgmt_address=("MGMT_ADDRESS", "INTERFACE_IP"),
        ),
        _IOS_ERRORS + ("Invalid command", "% Invalid"),
    ),
    ("cisco_xr",): ProtocolProfile(
        "cdp",
        "show cdp neighbors detail",
        "cisco_xr_show_cdp_neighbors_detail.textfsm",
        replace(_CDP_FIELDS, neighbor_name=("NEIGHBOR_NAME", "CHASSIS_ID")),
        _IOS_ERRORS,
    ),
}
for _device_types, _cdp in _CDP_PROFILES.items():
    for _device_type in _device_types:
        PROFILES[_device_type] = replace(
            PROFILES[_device_type], protocols=PROFILES[_device_type].protocols + (_cdp,)
        )

_STATUS_PROFILES = {
    ("cisco_ios", "cisco_xe"): StatusProfile(
        "show interfaces status",
        "cisco_ios_show_interfaces_status.textfsm",
        parse_status_table,
        _IOS_ERRORS,
    ),
    ("cisco_nxos",): StatusProfile(
        "show interface status",
        "cisco_nxos_show_interface_status.textfsm",
        parse_status_table,
        _IOS_ERRORS + ("Invalid command", "% Invalid"),
    ),
    ("cisco_xr",): StatusProfile(
        "show interfaces",
        "cisco_xr_show_interfaces.textfsm",
        parse_xr_interfaces,
        _IOS_ERRORS,
    ),
    ("arista_eos",): StatusProfile(
        "show interfaces status",
        "arista_eos_show_interfaces_status.textfsm",
        parse_status_table,
        _EOS_ERRORS,
    ),
    ("aruba_aoscx",): StatusProfile(
        "show interface",
        "aruba_aoscx_show_interface.textfsm",
        parse_aoscx_interface,
        ("Invalid input", "% Unknown command", "Command incomplete"),
    ),
    ("huawei", "huawei_vrp", "huawei_vrpv8"): StatusProfile(
        "display interface",
        "huawei_vrp_display_interface.textfsm",
        parse_huawei_interface,
        ("Error: Wrong parameter", "Error: Unrecognized command", "Error: Incomplete command"),
    ),
    ("juniper_junos",): StatusProfile(
        "show interfaces",
        "juniper_junos_show_interfaces.textfsm",
        parse_junos_interfaces,
        ("syntax error", "unknown command", "error:"),
    ),
    ("vyos",): StatusProfile(
        "show interfaces",
        "vyatta_vyos_show_interfaces.textfsm",
        parse_vyos_interfaces,
        ("Invalid command", "Unknown command", "Command incomplete"),
    ),
    ("hp_procurve",): StatusProfile(
        "show interfaces status",
        "hp_procurve_show_interfaces_status.textfsm",
        parse_status_table,
        ("Invalid input", "% Unknown command", "Command incomplete"),
    ),
}
for _device_types, _status in _STATUS_PROFILES.items():
    for _device_type in _device_types:
        PROFILES[_device_type] = replace(PROFILES[_device_type], status=_status)

#: Platforms that are known but deliberately not built in, with the reason.
UNSUPPORTED_HINTS = {
    "fortinet": (
        "ntc-templates only has a per-port FortiOS template ('diagnose lldprx port neighbor "
        "details <port>'), not a whole-device LLDP command. Add a custom_vendors entry if you "
        "have a template for your FortiOS version."
    ),
}


def supported_device_types() -> list[str]:
    """Sorted list of registered Netmiko device types."""
    return sorted(PROFILES)


def is_supported(device_type: str) -> bool:
    """True if ``device_type`` has a registered profile."""
    return device_type in PROFILES


def get_profile(device_type: str) -> VendorProfile:
    """Look up the profile for a device type.

    Raises:
        UnsupportedVendorError: With a specific explanation for known-unsupported platforms.
    """
    try:
        return PROFILES[device_type]
    except KeyError:
        hint = UNSUPPORTED_HINTS.get(device_type, "")
        raise UnsupportedVendorError(
            f"device_type '{device_type}' is not supported. "
            f"Supported: {', '.join(supported_device_types())}. {hint}".rstrip()
        ) from None


# ----------------------------------------------------- device-type guessing
#: LLDP system-description patterns -> Netmiko device type. First match wins, and
#: more specific platforms come first (NX-OS and IOS-XR before generic Cisco IOS).
DEFAULT_DEVICE_TYPE_RULES: tuple[tuple[str, str], ...] = (
    (r"nx-?os|nexus", "cisco_nxos"),
    (r"ios[ -]?xr", "cisco_xr"),
    (r"cisco ios|ios[ -]?xe|catalyst", "cisco_ios"),
    (r"arista|\beos\b", "arista_eos"),
    (r"aos-?cx|arubaos-cx", "aruba_aoscx"),
    (r"huawei|\bvrp\b", "huawei"),
    (r"junos|juniper", "juniper_junos"),
    (r"vyos|\bvyatta\b", "vyos"),
    (r"procurve|arubaos-s", "hp_procurve"),
    (r"fortios|fortigate", "fortinet"),
)


def guess_device_type(
    description: str,
    extra_rules: Iterable[tuple[re.Pattern[str], str]] = (),
) -> str | None:
    """Guess a Netmiko device type from an LLDP system description.

    ``extra_rules`` (from ``crawl.device_type_rules``) are tried before the built-ins.
    Returns ``None`` when nothing matches.
    """
    if not description:
        return None
    for pattern, device_type in extra_rules:
        if pattern.search(description):
            return device_type
    for pattern_text, device_type in DEFAULT_DEVICE_TYPE_RULES:
        if re.search(pattern_text, description, re.IGNORECASE):
            return device_type
    return None


# ------------------------------------------------------------- custom vendors
def _protocol_from_entry(
    name: str, entry: Any, where: str, base_dir: str | Path
) -> ProtocolProfile:
    """Build a ``ProtocolProfile`` from a ``{command, template, fields, error_markers}`` mapping."""
    if not isinstance(entry, dict):
        raise ConfigError(f"{where} must be a mapping")
    missing = [k for k in ("command", "template", "fields") if not entry.get(k)]
    if missing:
        raise ConfigError(f"{where}: missing {', '.join(missing)}")
    template = Path(str(entry["template"]))
    if not template.is_absolute():
        template = Path(base_dir) / template
    if not template.is_file():
        raise ConfigError(f"{where}: template file not found: {template}")
    if not isinstance(entry["fields"], dict):
        raise ConfigError(f"{where}.fields must be a mapping")
    markers = entry.get("error_markers") or []
    if not isinstance(markers, list):
        raise ConfigError(f"{where}.error_markers must be a list")
    try:
        fields = FieldMap.from_mapping(entry["fields"], where)
    except ParserError as exc:
        raise ConfigError(str(exc)) from exc
    return ProtocolProfile(
        name=name,
        command=str(entry["command"]),
        template=str(template.resolve()),
        fields=fields,
        error_markers=tuple(str(m) for m in markers),
    )


def load_custom_vendors(section: dict[str, Any] | None, base_dir: str | Path = ".") -> list[str]:
    """Register the ``custom_vendors`` mapping of settings.yaml; return the device types added.

    Each entry maps a Netmiko device type to LLDP settings (``command``, ``template`` (a path
    relative to ``base_dir``) and ``fields`` (TextFSM column names)) and/or a ``protocols``
    mapping of further discovery protocols (``cdp``, ``fdp``, ...) with the same three keys.
    Optional: ``description`` and ``error_markers``. An entry replaces a built-in profile of the
    same name.

    Raises:
        ConfigError: If an entry is malformed, has no protocol at all, or a template file is
            missing.
    """
    if not section:
        return []
    if not isinstance(section, dict):
        raise ConfigError("'custom_vendors' in the settings file must be a mapping")
    added: list[str] = []
    for device_type, entry in section.items():
        where = f"custom_vendors.{device_type}"
        if not isinstance(entry, dict):
            raise ConfigError(f"{where} must be a mapping")
        lldp_keys = [k for k in ("command", "template", "fields") if entry.get(k)]
        extras = entry.get("protocols") or {}
        if not isinstance(extras, dict):
            raise ConfigError(f"{where}.protocols must be a mapping")
        if not lldp_keys and not extras:
            raise ConfigError(f"{where}: missing command, template and fields (or protocols)")
        protocols = tuple(
            _protocol_from_entry(str(name), value, f"{where}.protocols.{name}", base_dir)
            for name, value in extras.items()
        )
        if lldp_keys:
            lldp = _protocol_from_entry("lldp", entry, where, base_dir)
            command, template, fields, markers = (
                lldp.command,
                lldp.template,
                lldp.fields,
                lldp.error_markers,
            )
        else:
            command, template, fields, markers = "", "", FieldMap(), ()
        register(
            VendorProfile(
                key=str(device_type),
                description=str(entry.get("description") or f"Custom ({device_type})"),
                command=command,
                template=template,
                fields=fields,
                error_markers=markers,
                protocols=protocols,
            )
        )
        added.append(str(device_type))
    return added
