"""
==============================================================================
Module Name:   poller.py
Description:   Data sources for one device at a time: DevicePoller (SSH via
               Netmiko with use_textfsm=True), FilePoller (offline replay) and
               RawCapture (--save-raw writer that FilePoller can read back).
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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from netmiko import ConnectHandler
from netmiko.exceptions import (
    NetmikoAuthenticationException,
    NetmikoBaseException,
    NetmikoTimeoutException,
    ReadTimeout,
)
from paramiko.ssh_exception import SSHException

from src import MapperError
from src.config import ConfigError, ConnectionSettings
from src.textparse import TemplateMismatch, parse_text
from src.vendors import (
    ProtocolProfile,
    UnsupportedVendorError,
    VendorProfile,
    get_profile,
)

logger = logging.getLogger(__name__)

__all__ = [
    "BasePoller",
    "ConfigError",
    "DevicePoller",
    "FilePoller",
    "PollerError",
    "ProtocolData",
    "RawCapture",
    "RawLldp",
]

INDEX_FILE = "index.yaml"


def _first_line(exc: BaseException) -> str:
    """Netmiko exceptions carry multi-paragraph help text; keep only the first line."""
    lines = [line.strip() for line in str(exc).splitlines() if line.strip()]
    return lines[0].rstrip(".") if lines else exc.__class__.__name__


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PollerError(MapperError):
    """Raised when a device cannot be reached or returns unusable output.

    ``kind`` classifies the failure for the diagram: ``auth``, ``timeout``, ``connect``,
    ``rejected``, ``unsupported``, ``no_data`` or ``other``.
    """

    def __init__(self, message: str, kind: str = "other") -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class ProtocolData:
    """The result of one discovery command: parsed rows, raw text, or why there is neither."""

    rows: list[dict[str, Any]] | None = None
    text: str = ""
    note: str = ""


@dataclass(frozen=True)
class RawLldp:
    """What one poll of one device returned.

    ``rows`` holds the structured TextFSM result of the LLDP command when Netmiko already
    parsed it (live SSH). ``text`` holds the raw CLI output (replay, ``--save-raw``, or a failed
    parse). ``extras`` has the same for every other discovery protocol (``cdp``, ...).
    Port-channels and interface status are optional extra commands with the same split.
    """

    host: str  # address the poll was made to
    device_type: str
    command: str
    collected_at: str
    hostname: str | None = None  # the device's own name (from its prompt), if known
    rows: list[dict[str, Any]] | None = None
    text: str = ""
    lldp_note: str = ""  # why LLDP gave nothing although another protocol did
    extras: dict[str, ProtocolData] = field(default_factory=dict)
    # Port-channels (optional extra command); same rows / text split as above.
    lag_rows: list[dict[str, Any]] | None = None
    lag_text: str = ""
    lag_note: str = ""  # why there is no port-channel data (shown in the details panel)
    # Operating state, speed and duplex of the ports (optional extra command).
    status_rows: list[dict[str, Any]] | None = None
    status_text: str = ""
    status_note: str = ""
    # VLAN setup of the ports: mode, access / native VLAN, trunk allowed VLANs (optional command).
    vlan_rows: list[dict[str, Any]] | None = None
    vlan_text: str = ""
    vlan_note: str = ""


class BasePoller(ABC):
    """Interface every poller implements."""

    @abstractmethod
    def poll(self, host: str, device_type: str) -> RawLldp:
        """Collect the neighbor-discovery output of one device."""


class DevicePoller(BasePoller):
    """Collect neighbors (LLDP, CDP, ...) from a network device over SSH."""

    def __init__(
        self,
        connection: ConnectionSettings,
        template_dirs: Sequence[str | os.PathLike] = (),
        keep_raw: bool = False,
        collect_lag: bool = True,
        protocols: Sequence[str] = ("lldp",),
        collect_status: bool = True,
        collect_vlan: bool = True,
    ) -> None:
        """
        Args:
            connection: Credentials and timeouts shared by every device.
            template_dirs: Extra directories searched for TextFSM templates.
            keep_raw: Also fetch the raw CLI text (one extra command per device and
                per command) so ``--save-raw`` can store it for later replay.
            collect_lag: Also read the device's port-channels (one extra command) so
                LAG member links can be drawn as one bundle.
            protocols: Discovery protocols to run, in priority order. A protocol the platform
                does not have is skipped; a protocol that fails is tolerated as long as another
                one works.
            collect_status: Also read each port's operating speed/duplex (one extra command)
                so mismatched links can be flagged.
            collect_vlan: Also read each port's VLAN setup (one extra command: switchport
                mode, access / native VLAN, trunk allowed VLANs) so VLAN mismatches between the
                two ends of a link can be flagged.
        """
        self.connection = connection
        self.template_dirs = [Path(d) for d in template_dirs]
        self.keep_raw = keep_raw
        self.collect_lag = collect_lag
        self.protocols = tuple(protocols)
        self.collect_status = collect_status
        self.collect_vlan = collect_vlan

    def _connection_params(self, host: str, device_type: str) -> dict[str, Any]:
        c = self.connection
        return {
            "device_type": device_type,
            "host": host,
            "username": c.username,
            "password": c.password,
            "secret": c.secret,
            "port": c.port,
            "conn_timeout": c.conn_timeout,
            "auth_timeout": c.auth_timeout,
            "banner_timeout": c.banner_timeout,
        }

    # ----------------------------------------------------------------- polling
    def poll(self, host: str, device_type: str) -> RawLldp:
        """Connect to ``host``, run the platform's discovery commands and return the result.

        Raises:
            PollerError: On an unsupported device type, authentication failure, timeout,
                SSH/socket error, an unusable template, or when *every* discovery protocol
                was rejected or unusable.
        """
        try:
            profile = get_profile(device_type)
            protocols = {
                name: proto
                for name, proto in profile.protocol_profiles().items()
                if name in self.protocols
            }
            if not protocols:
                raise PollerError(
                    f"{host}: {device_type} has none of the requested discovery protocols "
                    f"({', '.join(self.protocols)})",
                    kind="unsupported",
                )
            templates = {n: p.resolve_template(self.template_dirs) for n, p in protocols.items()}
        except UnsupportedVendorError as exc:
            raise PollerError(f"{host}: {exc}", kind="unsupported") from exc
        except PollerError:
            raise
        except MapperError as exc:
            raise PollerError(f"{host}: {exc}") from exc

        logger.debug("Connecting to %s:%s (%s)", host, self.connection.port, device_type)
        try:
            with ConnectHandler(**self._connection_params(host, device_type)) as conn:
                if self.connection.secret and not conn.check_enable_mode():
                    conn.enable()
                hostname = (conn.base_prompt or "").strip() or host
                collected: dict[str, ProtocolData] = {}
                errors: list[PollerError] = []
                for name, proto in protocols.items():
                    try:
                        collected[name] = self._collect_protocol(conn, proto, templates[name], host)
                    except PollerError as exc:
                        errors.append(exc)
                        collected[name] = ProtocolData(note=str(exc))
                if len(errors) == len(protocols):
                    raise errors[0]
                lag_rows, lag_text, lag_note = self._collect_lag(conn, profile)
                status_rows, status_text, status_note = self._collect_status(conn, profile)
                vlan_rows, vlan_text, vlan_note = self._collect_vlan(conn, profile)
        except NetmikoAuthenticationException as exc:
            raise PollerError(
                f"Authentication failed for {host}: check username, password and enable secret",
                kind="auth",
            ) from exc
        except (TimeoutError, NetmikoTimeoutException, ReadTimeout) as exc:
            raise PollerError(
                f"Timed out talking to {host}: {_first_line(exc)}. Check reachability, SSH "
                "access and the timeout settings",
                kind="timeout",
            ) from exc
        except (NetmikoBaseException, SSHException, OSError) as exc:
            raise PollerError(
                f"SSH session to {host} failed: {_first_line(exc)}", kind="connect"
            ) from exc
        except ValueError as exc:  # e.g. a device_type Netmiko does not know
            raise PollerError(f"Could not open a session to {host}: {exc}", kind="connect") from exc

        lldp = collected.get("lldp")
        lldp_note = ""
        if lldp is None:
            lldp_note = (
                "LLDP is not enabled for this crawl"
                if "lldp" not in self.protocols
                else f"no LLDP command is known for {device_type}"
            )
        elif lldp.rows is None and not lldp.text:
            lldp_note = lldp.note
        extras = {name: data for name, data in collected.items() if name != "lldp"}

        logger.debug("Collected %s from %s", ", ".join(collected), host)
        return RawLldp(
            host=host,
            device_type=device_type,
            command=(protocols.get("lldp") or next(iter(protocols.values()))).command,
            collected_at=_now(),
            hostname=hostname or None,
            rows=lldp.rows if lldp else None,
            text=lldp.text if lldp else "",
            lldp_note=lldp_note,
            extras=extras,
            lag_rows=lag_rows,
            lag_text=lag_text,
            lag_note=lag_note,
            status_rows=status_rows,
            status_text=status_text,
            status_note=status_note,
            vlan_rows=vlan_rows,
            vlan_text=vlan_text,
            vlan_note=vlan_note,
        )

    def _collect_protocol(
        self, conn: Any, proto: ProtocolProfile, template: Path, host: str
    ) -> ProtocolData:
        """Run one discovery command with ``use_textfsm=True``.

        Raises:
            PollerError: If the device rejects the command (kind ``rejected``).
        """
        timeout = self.connection.read_timeout
        text = self._run_raw(conn, proto, host) if self.keep_raw else ""
        result = conn.send_command(
            proto.command,
            read_timeout=timeout,
            use_textfsm=True,
            textfsm_template=str(template),
        )
        if isinstance(result, list):
            if not result and not text:
                # An empty list is either "no neighbors" or a command the device rejected
                # (the templates swallow errors). Fetch the text to tell.
                text = self._run_raw(conn, proto, host)
            return ProtocolData(rows=result, text=text)
        # Netmiko hands back the raw output both when there are no records and when the template
        # does not match. Tell them apart: "no records" is a valid (empty) answer.
        text = text or str(result)
        self._check_markers(proto, text, host)
        try:
            return ProtocolData(rows=parse_text(text, template), text=text)
        except TemplateMismatch:
            return ProtocolData(rows=None, text=text)

    def _collect_optional(
        self, conn: Any, command: str, template: Path, markers: tuple[str, ...], label: str
    ) -> tuple[list[dict[str, Any]] | None, str, str]:
        """Run an enrichment command: ``(rows, raw text, note)``; never raises for the device."""
        timeout = self.connection.read_timeout
        try:
            result = conn.send_command(
                command, read_timeout=timeout, use_textfsm=True, textfsm_template=str(template)
            )
            text = str(conn.send_command(command, read_timeout=timeout)) if self.keep_raw else ""
            if isinstance(result, list) and not result and not text:
                text = str(conn.send_command(command, read_timeout=timeout))
            if not isinstance(result, list):
                text = text or str(result)
            if any(marker in text for marker in markers):
                return None, text, f"'{command}' was rejected by the device"
            if not isinstance(result, list):
                try:
                    return parse_text(text, template), text, ""
                except TemplateMismatch:
                    return None, text, f"the output of '{command}' did not match its template"
            return result, text, ""
        except (MapperError, NetmikoBaseException, SSHException, OSError, ValueError) as exc:
            logger.warning("%s collection failed (%s): %s", label, command, _first_line(exc))
            return None, "", f"'{command}' failed: {_first_line(exc)}"

    def _collect_lag(
        self, conn: Any, profile: VendorProfile
    ) -> tuple[list[dict[str, Any]] | None, str, str]:
        """Read the port-channels on the open session: ``(rows, raw text, note)``.

        Never raises for a device problem: the neighbor data is already in hand, so a failing
        or missing port-channel command only means this device's links are drawn without
        bundles. The note says why.
        """
        lag = profile.lag
        if not self.collect_lag:
            return None, "", "port-channel collection is switched off"
        if lag is None:
            return None, "", f"no port-channel command is known for {profile.key}"
        try:
            template = lag.resolve_template(self.template_dirs)
        except MapperError as exc:
            return None, "", str(exc)
        return self._collect_optional(
            conn, lag.command, template, lag.error_markers, "Port-channel"
        )

    def _collect_status(
        self, conn: Any, profile: VendorProfile
    ) -> tuple[list[dict[str, Any]] | None, str, str]:
        """Read each port's operating state, speed and duplex: ``(rows, raw text, note)``.

        Never raises for a device problem; without it the device's links are not checked.
        """
        status = profile.status
        if not self.collect_status:
            return None, "", "interface status collection is switched off"
        if status is None:
            return None, "", f"no interface status command is known for {profile.key}"
        try:
            template = status.resolve_template(self.template_dirs)
        except MapperError as exc:
            return None, "", str(exc)
        return self._collect_optional(
            conn, status.command, template, status.error_markers, "Interface status"
        )

    def _collect_vlan(
        self, conn: Any, profile: VendorProfile
    ) -> tuple[list[dict[str, Any]] | None, str, str]:
        """Read each port's VLAN setup on the open session: ``(rows, raw text, note)``.

        Never raises for a device problem; without it the device's links get no VLAN check.
        """
        vlan = profile.vlan
        if not self.collect_vlan:
            return None, "", "VLAN collection is switched off"
        if vlan is None:
            return None, "", f"no VLAN (switchport) command is known for {profile.key}"
        try:
            template = vlan.resolve_template(self.template_dirs)
        except MapperError as exc:
            return None, "", str(exc)
        return self._collect_optional(conn, vlan.command, template, vlan.error_markers, "VLAN")

    def _run_raw(self, conn: Any, proto: ProtocolProfile, host: str) -> str:
        """Run the command without parsing and reject obvious CLI errors."""
        text = str(conn.send_command(proto.command, read_timeout=self.connection.read_timeout))
        self._check_markers(proto, text, host)
        return text

    @staticmethod
    def _check_markers(proto: ProtocolProfile, text: str, host: str) -> None:
        if any(marker in text for marker in proto.error_markers):
            snippet = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
            raise PollerError(f"{host} rejected '{proto.command}': {snippet}", kind="rejected")


# --------------------------------------------------------------------- replay
def load_index(directory: Path) -> dict[str, dict[str, Any]]:
    """Read ``index.yaml`` (host -> file names, hostname, device_type) from a capture directory."""
    path = directory / INDEX_FILE
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise PollerError(f"Could not read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PollerError(f"{path} must be a mapping of host -> entry")
    return {str(host): dict(entry or {}) for host, entry in data.items()}


def _safe_name(host: str) -> str:
    return re.sub(r"[^\w.\-]", "_", host)


class FilePoller(BasePoller):
    """Replay previously saved CLI output (offline testing, demos, regression tests).

    The directory holds one ``<host>.txt`` per device (LLDP), ``<host>.<protocol>.txt`` for other
    discovery protocols, ``<host>.lag.txt``, ``<host>.status.txt``, ``<host>.vlan.txt`` and an
    optional ``index.yaml`` that maps hosts to files and records each device's own hostname and
    type.
    """

    def __init__(
        self,
        directory: str | os.PathLike,
        collect_lag: bool = True,
        protocols: Sequence[str] = ("lldp", "cdp"),
        collect_status: bool = True,
        collect_vlan: bool = True,
    ) -> None:
        self.directory = Path(directory)
        self.collect_lag = collect_lag
        self.protocols = tuple(protocols)
        self.collect_status = collect_status
        self.collect_vlan = collect_vlan
        self._index = load_index(self.directory)

    def _read(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise PollerError(f"Could not read replay file {path}: {exc}", kind="no_data") from exc

    def poll(self, host: str, device_type: str) -> RawLldp:
        """Return the saved output for ``host``.

        Raises:
            PollerError: If there is no capture of any discovery protocol for the host.
        """
        entry = self._index.get(host, {})
        resolved_type = str(entry.get("device_type") or device_type)
        try:
            profile = get_profile(resolved_type)
            available = set(profile.protocol_profiles())
            command = profile.command or next(iter(profile.protocol_profiles().values())).command
        except MapperError:
            profile, available, command = None, {"lldp"}, "(replay)"

        text, lldp_note = "", ""
        lldp_path = self.directory / str(entry.get("file") or f"{host}.txt")
        if "lldp" not in self.protocols:
            lldp_note = "LLDP is not enabled for this crawl"
        elif "lldp" not in available:
            lldp_note = f"no LLDP command is known for {resolved_type}"
        elif lldp_path.is_file():
            text = self._read(lldp_path)
            if not text.strip():
                raise PollerError(f"Replay file {lldp_path} is empty", kind="no_data")
        else:
            lldp_note = "no LLDP capture in the replay directory"

        extras: dict[str, ProtocolData] = {}
        for name in self.protocols:
            if name == "lldp" or name not in available:
                continue
            path = self.directory / str(entry.get(f"{name}_file") or f"{host}.{name}.txt")
            if path.is_file():
                extras[name] = ProtocolData(text=self._read(path))
            else:
                extras[name] = ProtocolData(
                    note=f"no {name.upper()} capture in the replay directory"
                )

        if not text.strip() and not any(d.text.strip() for d in extras.values()):
            raise PollerError(f"No replay data for {host} in {self.directory}", kind="no_data")

        lag_text, lag_note = self._optional(entry, host, "lag", self.collect_lag, "port-channel")
        status_text, status_note = self._optional(
            entry, host, "status", self.collect_status, "interface status"
        )
        vlan_text, vlan_note = self._optional(entry, host, "vlan", self.collect_vlan, "VLAN")
        return RawLldp(
            host=host,
            device_type=resolved_type,
            command=command,
            collected_at=_now(),
            hostname=str(entry["hostname"]) if entry.get("hostname") else None,
            text=text,
            lldp_note=lldp_note,
            extras=extras,
            lag_text=lag_text,
            lag_note=lag_note,
            status_text=status_text,
            status_note=status_note,
            vlan_text=vlan_text,
            vlan_note=vlan_note,
        )

    def _optional(
        self, entry: dict[str, Any], host: str, name: str, enabled: bool, label: str
    ) -> tuple[str, str]:
        """Text of an enrichment capture (``lag`` / ``status``), or ``("", why not)``."""
        if not enabled:
            return "", f"{label} collection is switched off"
        path = self.directory / str(entry.get(f"{name}_file") or f"{host}.{name}.txt")
        if not path.is_file():
            return "", f"no {label} capture in the replay directory"
        try:
            return path.read_text(encoding="utf-8"), ""
        except OSError as exc:
            return "", f"could not read {path.name}: {exc}"


class RawCapture:
    """Write each device's raw CLI output to a directory that ``FilePoller`` can replay."""

    def __init__(self, directory: str | os.PathLike) -> None:
        self.directory = Path(directory)

    def write(self, raw: RawLldp) -> None:
        """Save one device (no-op when the poll carried no raw text).

        Raises:
            PollerError: If the directory cannot be written.
        """
        pieces: dict[str, str] = {}  # index key -> text
        if raw.text:
            pieces["file"] = raw.text
        for name, data in raw.extras.items():
            if data.text:
                pieces[f"{name}_file"] = data.text
        if raw.lag_text:
            pieces["lag_file"] = raw.lag_text
        if raw.status_text:
            pieces["status_file"] = raw.status_text
        if raw.vlan_text:
            pieces["vlan_file"] = raw.vlan_text
        if not pieces:
            logger.debug("No raw text for %s; nothing to capture", raw.host)
            return
        stem = _safe_name(raw.host)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            index = load_index(self.directory)
            entry = {"hostname": raw.hostname or "", "device_type": raw.device_type}
            for key, text in pieces.items():
                suffix = "" if key == "file" else "." + key.removesuffix("_file")
                file_name = f"{stem}{suffix}.txt"
                (self.directory / file_name).write_text(text, encoding="utf-8")
                entry[key] = file_name
            index[raw.host] = entry
            (self.directory / INDEX_FILE).write_text(
                yaml.safe_dump(index, sort_keys=False), encoding="utf-8"
            )
        except OSError as exc:
            raise PollerError(f"Could not write raw capture to {self.directory}: {exc}") from exc
        logger.debug("Raw output of %s saved to %s", raw.host, self.directory)
