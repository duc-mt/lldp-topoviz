"""
==============================================================================
Module Name:   graph_engine.py
Description:   NetworkX topology store: resolves each device to one node (name /
               IP / chassis-ID aliases), deduplicates links seen from both ends,
               tracks crawl status and writes the JSON snapshot.
Author:        Mai Tan Duc <ducmai.network@gmail.com>
Created:       2026-10-10
Version:       1.0.0
License:       MIT
==============================================================================
Usage:         imported by main.py / crawler.py / visualizer.py (not run directly)
Notes:         Requires Python 3.10+
==============================================================================
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import networkx as nx

from src import (
    MapperError,
    is_ipv4,
    looks_like_mac,
    normalize_chassis_id,
    normalize_hostname,
    normalize_interface,
    short_name,
)
from src.linkcheck import End, LinkCheckConfig, check_bundle, check_link, operating_speed

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

STATUS_CRAWLED = "crawled"  # we logged in and read its LLDP table
STATUS_FAILED = "failed"  # we tried and could not
STATUS_DISCOVERED = "discovered"  # seen as a neighbor only (not crawled)


class GraphEngineError(MapperError):
    """Raised when the graph cannot be built or its snapshot cannot be saved."""


def _one_line(text: str) -> str:
    return " ".join(str(text or "").split())


class GraphEngine:
    """Builds and persists the LLDP topology graph.

    Nodes are devices, keyed by a stable ID (normalised hostname, else IP, else chassis
    ID). Edges are physical links in an undirected ``MultiGraph`` so parallel links and
    LAG members survive; each edge records the port used at each end.
    """

    #: Default data directory, resolved from this file so it works regardless of CWD.
    _DEFAULT_DATA_DIR: Path = Path(__file__).resolve().parent.parent / "data"

    def __init__(self, data_dir: str | os.PathLike | None = None) -> None:
        self.data_dir = Path(data_dir) if data_dir is not None else self._DEFAULT_DATA_DIR
        self.current_path = self.data_dir / "current_state.json"
        self.previous_path = self.data_dir / "previous_state.json"
        self.graph: nx.MultiGraph = nx.MultiGraph()
        self._by_name: dict[str, str] = {}
        self._by_ip: dict[str, str] = {}
        self._by_chassis: dict[str, str] = {}

    # ------------------------------------------------------------- identity
    def _lookup(self, name_key: str, ips: Iterable[str], chassis_key: str) -> str | None:
        """Find an existing node: IP first (most specific), then chassis ID, then name."""
        for ip in ips:
            if ip in self._by_ip:
                return self._by_ip[ip]
        if chassis_key and chassis_key in self._by_chassis:
            return self._by_chassis[chassis_key]
        if name_key and name_key in self._by_name:
            return self._by_name[name_key]
        return None

    def find(self, name: str = "", ips: Iterable[str] = (), chassis_id: str = "") -> str | None:
        """Look a device up by any identifier without creating it (``None`` if unknown)."""
        name_key = normalize_hostname(name)
        chassis_key = normalize_chassis_id(chassis_id)
        if name_key and looks_like_mac(name_key):
            chassis_key = chassis_key or normalize_chassis_id(name_key)
            name_key = ""
        return self._lookup(name_key, [ip for ip in ips if ip], chassis_key)

    def has_node(self, node_id: str) -> bool:
        return self.graph.has_node(node_id)

    def merge(self, keep: str, drop: str) -> None:
        """Fold node ``drop`` into ``keep``: the same device that was seen under two identities.

        Links are re-pointed (and deduplicated), aliases and attributes are combined, and
        ``drop`` disappears from the graph.
        """
        if keep == drop or not (self.graph.has_node(keep) and self.graph.has_node(drop)):
            return
        for _u, other, data in list(self.graph.edges(drop, data=True)):
            ports = data["ports"]
            if other != keep:
                self.add_link(keep, ports.get(drop, ""), other, ports.get(other, ""))
        kept, dropped = self.graph.nodes[keep], self.graph.nodes[drop]
        kept["mgmt_ips"] += [ip for ip in dropped["mgmt_ips"] if ip not in kept["mgmt_ips"]]
        kept["capabilities"] = sorted(set(kept["capabilities"]) | set(dropped["capabilities"]))
        for key in ("chassis_id", "description", "platform"):
            kept[key] = kept[key] or dropped[key]
        kept["seed"] = kept["seed"] or dropped["seed"]
        self.graph.remove_node(drop)
        for index in (self._by_name, self._by_ip, self._by_chassis):
            for key, owner in index.items():
                if owner == drop:
                    index[key] = keep
        logger.debug("Merged node %s into %s", drop, keep)

    def resolve(
        self,
        name: str = "",
        ips: Iterable[str] = (),
        chassis_id: str = "",
        description: str = "",
        capabilities: Iterable[str] = (),
        node_id: str | None = None,
        platform: str = "",
    ) -> str:
        """Return the node ID for a device, creating the node if it is new.

        Every identifier given is recorded as an alias, so the same device reported later
        under another name, address or chassis ID resolves to the same node.

        Args:
            name: Device name (hostname or LLDP system name; any domain suffix is ignored).
            ips: Management addresses.
            chassis_id: LLDP chassis ID (MAC in any notation, or free text).
            description: LLDP system description.
            capabilities: Canonical capability names.
            node_id: Force this existing node (used when the caller already knows which
                device it is talking to) and just add the aliases to it.

        Raises:
            GraphEngineError: If nothing identifies the device.
        """
        name_key = normalize_hostname(name)
        chassis_key = normalize_chassis_id(chassis_id)
        if name_key and looks_like_mac(name_key):  # some devices put their MAC in the name
            chassis_key = chassis_key or normalize_chassis_id(name_key)
            name_key = ""
        ip_list = [ip for ip in ips if ip]

        target = node_id if node_id and self.graph.has_node(node_id) else None
        if target is None:
            target = self._lookup(name_key, ip_list, chassis_key)
        if target is None:
            target = name_key or (ip_list[0] if ip_list else "") or chassis_key
            if not target:
                raise GraphEngineError(
                    "Cannot identify a device without a name, address or chassis ID"
                )
            self.graph.add_node(
                target,
                label=short_name(name) or (ip_list[0] if ip_list else "") or chassis_id,
                status=STATUS_DISCOVERED,
                seed=False,
                address="",
                device_type="",
                mgmt_ips=[],
                chassis_id="",
                description="",
                capabilities=[],
                depth=None,
                error="",
                error_kind="",
                skip_reason="",
                skip_code="",
                port_channels=[],
                lag_note="",
                platform="",
                link_status={},
                discovery=[],
                protocol_notes={},
                status_note="",
            )
        attrs = self.graph.nodes[target]

        if name_key:
            self._alias(self._by_name, name_key, target)
            label = attrs["label"]
            if not label or is_ipv4(label) or looks_like_mac(label):
                attrs["label"] = short_name(name)
        for ip in ip_list:
            self._alias(self._by_ip, ip, target)
            if ip not in attrs["mgmt_ips"]:
                attrs["mgmt_ips"].append(ip)
        if chassis_key:
            self._alias(self._by_chassis, chassis_key, target)
            attrs["chassis_id"] = attrs["chassis_id"] or str(chassis_id).strip()
        if description and not attrs["description"]:
            attrs["description"] = _one_line(description)
        if platform and not attrs["platform"]:
            attrs["platform"] = _one_line(platform)
        caps = set(attrs["capabilities"]) | set(capabilities)
        attrs["capabilities"] = sorted(caps)
        return target

    @staticmethod
    def _alias(index: dict[str, str], key: str, node_id: str) -> None:
        existing = index.setdefault(key, node_id)
        if existing != node_id:
            logger.debug(
                "Alias %r already belongs to %s; not reassigning it to %s",
                key,
                existing,
                node_id,
            )

    # -------------------------------------------------------------- status
    def status(self, node_id: str) -> str:
        return self.graph.nodes[node_id]["status"]

    def label(self, node_id: str) -> str:
        return self.graph.nodes[node_id]["label"] or node_id

    def set_seed(self, node_id: str) -> None:
        self.graph.nodes[node_id]["seed"] = True

    def mark_crawled(
        self,
        node_id: str,
        host: str,
        device_type: str,
        depth: int,
        hostname: str = "",
    ) -> None:
        attrs = self.graph.nodes[node_id]
        attrs.update(
            status=STATUS_CRAWLED,
            address=host,
            device_type=device_type,
            depth=depth,
            error="",
            error_kind="",
            skip_reason="",
            skip_code="",
        )
        if hostname:
            attrs["label"] = short_name(hostname)

    def mark_failed(
        self,
        node_id: str,
        error: str,
        host: str = "",
        device_type: str = "",
        kind: str = "other",
    ) -> None:
        """Record a failed crawl; ``kind`` classifies it (auth, timeout, connect, ...)."""
        attrs = self.graph.nodes[node_id]
        attrs.update(
            status=STATUS_FAILED,
            error=_one_line(error),
            error_kind=kind,
            skip_reason="",
            skip_code="",
        )
        if host:
            attrs["address"] = host
        if device_type:
            attrs["device_type"] = device_type

    def mark_skipped(self, node_id: str, reason: str, code: str = "") -> bool:
        """Record why a discovered node is not being crawled ('' clears it).

        ``code`` is a short machine-readable class of the reason (``out_of_scope``,
        ``no_ip``, ...). Returns True if the stored reason changed (so callers log it once).
        """
        attrs = self.graph.nodes[node_id]
        if attrs["status"] != STATUS_DISCOVERED or attrs["skip_reason"] == reason:
            return False
        attrs["skip_reason"] = reason
        attrs["skip_code"] = code if reason else ""
        return True

    def set_port_channels(self, node_id: str, channels: Iterable[Any], note: str = "") -> None:
        """Store the bundles a crawled device reported (or ``note`` saying why there are none)."""
        attrs = self.graph.nodes[node_id]
        attrs["port_channels"] = [
            {
                "name": pc.name,
                "protocol": pc.protocol,
                "up": pc.up,
                "members": [{"interface": m.interface, "state": m.state} for m in pc.members],
            }
            for pc in channels
        ]
        attrs["lag_note"] = _one_line(note)

    def annotate_bundles(self) -> int:
        """Mark every link whose port is a member of a port-channel on either end.

        Sets ``data["bundles"] = {node: bundle_name}`` on each link and returns how many
        links belong to a bundle. Call it once the crawl has finished (ports are final then).
        """
        membership: dict[str, dict[str, str]] = {}
        for node_id, attrs in self.graph.nodes(data=True):
            members: dict[str, str] = {}
            for channel in attrs["port_channels"]:
                for member in channel["members"]:
                    members[normalize_interface(member["interface"]).lower()] = channel["name"]
            membership[node_id] = members
        bundled = 0
        for u, v, data in self.graph.edges(data=True):
            bundles: dict[str, str] = {}
            for node_id in (u, v):
                port = data["ports"].get(node_id, "")
                name = membership[node_id].get(normalize_interface(port).lower()) if port else None
                if name:
                    bundles[node_id] = name
            data["bundles"] = bundles
            bundled += bool(bundles)
        return bundled

    # --------------------------------------------------------------- links
    def add_link(
        self,
        a: str,
        port_a: str,
        b: str,
        port_b: str,
        protocols: Iterable[str] = ("lldp",),
    ) -> bool:
        """Add the link ``a:port_a <-> b:port_b`` unless it is already known.

        A link reported by both ends (or by several discovery protocols) is stored once, and
        remembers every protocol that saw it. A missing port on either side is a wildcard (and
        is filled in when a later report knows it), so two reports of the same link match even
        if one end's Port ID was unusable.

        Returns:
            True if a new link was added, False if an existing one was matched or merged.
        """
        if a == b:
            logger.warning("Ignoring a link from %s to itself (%s <-> %s)", a, port_a, port_b)
            return False
        new_ports = {a: port_a or "", b: port_b or ""}
        sources = set(protocols)
        if self.graph.has_edge(a, b):
            for _key, data in self.graph[a][b].items():
                if self._same_link(data["ports"], new_ports):
                    for node, port in new_ports.items():
                        if port and not data["ports"].get(node):
                            data["ports"][node] = port
                    data["sources"] |= sources
                    return False
        self.graph.add_edge(a, b, ports=new_ports, sources=sources)
        return True

    @staticmethod
    def _same_link(existing: dict[str, str], new: dict[str, str]) -> bool:
        for node, port in new.items():
            old = existing.get(node, "")
            if (
                port
                and old
                and (normalize_interface(port).lower() != normalize_interface(old).lower())
            ):
                return False
        return True

    def set_link_status(self, node_id: str, entries: dict[str, dict[str, Any]]) -> None:
        """Store what a crawled device reports about the ports that carry links.

        ``entries`` maps a normalised port name to ``{status, speed, duplex, auto}``.
        """
        self.graph.nodes[node_id]["link_status"] = entries

    def set_discovery(
        self, node_id: str, used: Iterable[str], notes: dict[str, str], status_note: str = ""
    ) -> None:
        """Record which discovery protocols produced data for a device, and why others did not."""
        attrs = self.graph.nodes[node_id]
        attrs["discovery"] = sorted(used)
        attrs["protocol_notes"] = {k: _one_line(v) for k, v in notes.items()}
        attrs["status_note"] = _one_line(status_note)

    def annotate_links(self, config: LinkCheckConfig | None = None) -> int:
        """Attach what each end reports (``oper``) and the link checks (``warnings``) to every link.

        Call it after ``annotate_bundles`` once the crawl has finished. Returns the number of
        links with at least one warning.
        """
        config = config or LinkCheckConfig()
        member_speeds: dict[tuple, list[int | None]] = defaultdict(list)
        member_edges: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
        warned = 0
        for u, v, data in self.graph.edges(data=True):
            oper: dict[str, dict[str, Any]] = {}
            ends: list[End] = []
            for node in (u, v):
                port = data["ports"].get(node, "")
                state = (
                    self.graph.nodes[node]["link_status"].get(normalize_interface(port).lower())
                    if port
                    else None
                )
                if not state:
                    continue
                oper[node] = state
                label = self.graph.nodes[node]["label"] or node
                if config.enabled and not config.ignores(label, port):
                    ends.append(
                        End(
                            label,
                            port,
                            state.get("speed"),
                            state.get("duplex"),
                            state.get("status"),
                        )
                    )
            data["oper"] = oper
            data["warnings"] = [w.to_dict() for w in check_link(ends)] if config.enabled else []
            if data.get("bundles"):
                key = (frozenset((u, v)), tuple(sorted(data["bundles"].items())))
                member_speeds[key].append(operating_speed(ends))
                member_edges[key].append(data)
        if config.enabled:
            for key, speeds in member_speeds.items():
                for warning in check_bundle(speeds):
                    for data in member_edges[key]:
                        data["warnings"].append(warning.to_dict())
        for _u, _v, data in self.graph.edges(data=True):
            warned += bool(data["warnings"])
        return warned

    # ------------------------------------------------------------- reports
    def counts(self) -> dict[str, int]:
        """Number of nodes per status."""
        result = {STATUS_CRAWLED: 0, STATUS_FAILED: 0, STATUS_DISCOVERED: 0}
        for _node, attrs in self.graph.nodes(data=True):
            result[attrs["status"]] += 1
        return result

    def to_dict(self, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        """JSON-friendly snapshot of the topology (deterministic order)."""
        nodes = [
            {"id": node_id, **{k: v for k, v in sorted(attrs.items())}}
            for node_id, attrs in sorted(self.graph.nodes(data=True))
        ]
        links = []
        for u, v, data in self.graph.edges(data=True):
            source, target = sorted((u, v))
            links.append(
                {
                    "source": source,
                    "target": target,
                    "source_port": data["ports"].get(source, ""),
                    "target_port": data["ports"].get(target, ""),
                    "source_bundle": data.get("bundles", {}).get(source, ""),
                    "target_bundle": data.get("bundles", {}).get(target, ""),
                    "sources": sorted(data.get("sources", ())),
                    "source_oper": data.get("oper", {}).get(source, {}),
                    "target_oper": data.get("oper", {}).get(target, {}),
                    "warnings": data.get("warnings", []),
                }
            )
        links.sort(key=lambda e: (e["source"], e["target"], e["source_port"], e["target_port"]))
        return {
            "schema_version": SCHEMA_VERSION,
            "metadata": dict(metadata or {}),
            "counts": self.counts(),
            "nodes": nodes,
            "links": links,
        }

    def save(self, metadata: dict[str, Any] | None = None) -> Path:
        """Write the snapshot to ``data/current_state.json`` (previous one is kept).

        The file is written to a temporary name and renamed into place, so a crash can
        never leave a half-written baseline.

        Raises:
            GraphEngineError: If the data directory cannot be written.
        """
        payload = self.to_dict(
            {
                "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                **(metadata or {}),
            }
        )
        tmp = self.current_path.with_suffix(".json.tmp")
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            tmp.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            if self.current_path.exists():
                shutil.copy2(self.current_path, self.previous_path)
            os.replace(tmp, self.current_path)
        except OSError as exc:
            raise GraphEngineError(f"Could not save state to {self.data_dir}: {exc}") from exc
        logger.info("Topology snapshot saved to %s", self.current_path)
        return self.current_path
