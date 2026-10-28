"""
==============================================================================
Module Name:   graph_engine.py
Description:   NetworkX topology store: resolves each device to one node (name /
               IP / chassis-ID aliases), deduplicates links seen from both ends,
               tracks crawl status (and which seed led to each device), keeps each
               port's VLAN setup, writes the JSON snapshot and records every
               crawl in the history store.
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
import re
import secrets
import shutil
import sqlite3
import zlib
from collections import defaultdict
from collections.abc import Iterable
from contextlib import closing
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
from src.linkcheck import (
    End,
    LinkCheckConfig,
    check_bundle,
    check_link,
    check_vlan_consistency,
    operating_speed,
)
from src.models import PortVlanInfo

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
        self.last_run: dict[str, Any] | None = None  # the history run ``save`` recorded

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
        kept["seed_origin"] = kept["seed_origin"] or dropped["seed_origin"]
        if kept["status"] == STATUS_DISCOVERED:  # a device we logged in to is not a leaf
            kept["endpoint_class"] = kept["endpoint_class"] or dropped["endpoint_class"]
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
                endpoint_class="",
                port_channels=[],
                lag_note="",
                platform="",
                link_status={},
                discovery=[],
                protocol_notes={},
                status_note="",
                seed_origin="",
                vlan_ports={},
                vlan_note="",
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

    def set_seed(self, node_id: str, origin: str = "") -> None:
        """Mark a node as a crawl seed (``origin``: the seed text it was started from)."""
        attrs = self.graph.nodes[node_id]
        attrs["seed"] = True
        if origin:
            attrs["seed_origin"] = origin

    def set_seed_origin(self, node_id: str, origin: str) -> bool:
        """Record which seed led to a device; the first seed to reach it keeps the credit.

        Returns True if the origin was set by this call.
        """
        attrs = self.graph.nodes[node_id]
        if not origin or attrs["seed_origin"]:
            return False
        attrs["seed_origin"] = origin
        return True

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
            endpoint_class="",  # we logged in: whatever it advertised, it is infrastructure
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
            endpoint_class="",
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

    def set_endpoint_class(self, node_id: str, endpoint_class: str) -> None:
        """Record that a neighbor is a leaf endpoint (``endpoint_phone``, ``endpoint_ap``, ...).

        Only a node that was not crawled can be a leaf: a device we logged in to is infrastructure
        by definition, whatever it advertised.
        """
        attrs = self.graph.nodes[node_id]
        if attrs["status"] == STATUS_DISCOVERED:
            attrs["endpoint_class"] = endpoint_class

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

    def set_vlan_info(
        self, node_id: str, ports: dict[str, PortVlanInfo], note: str = ""
    ) -> None:
        """Store the VLAN setup a crawled device reported for the ports that carry links.

        ``ports`` maps a normalised port name to its ``PortVlanInfo`` (kept as plain dicts, so
        the snapshot stays JSON); ``note`` says why there is none.
        """
        attrs = self.graph.nodes[node_id]
        attrs["vlan_ports"] = {port: info.to_dict() for port, info in ports.items()}
        attrs["vlan_note"] = _one_line(note)

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
        bundle_vlan: dict[tuple, list[dict[str, str]] | None] = {}
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
            vlan = self._link_vlan(u, v, data)
            data["vlan"] = {node: info.to_dict() for node, info in vlan.items()}
            if config.enabled and config.vlan and len(vlan) == 2:
                data["warnings"] += self._vlan_warnings(u, v, data, vlan, bundle_vlan)
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

    def _port_vlan(self, node: str, port: str) -> PortVlanInfo | None:
        """The VLAN setup ``node`` reported for ``port`` (``None`` if it reported none)."""
        if not port:
            return None
        entry = self.graph.nodes[node]["vlan_ports"].get(normalize_interface(port).lower())
        return PortVlanInfo.from_dict(entry) if entry else None

    def _link_vlan(self, u: str, v: str, data: dict[str, Any]) -> dict[str, PortVlanInfo]:
        """VLAN setup of each end of a link, for the ends that reported one."""
        found: dict[str, PortVlanInfo] = {}
        for node in (u, v):
            info = self._port_vlan(node, data["ports"].get(node, ""))
            if info:
                found[node] = info
        return found

    def _vlan_warnings(
        self,
        u: str,
        v: str,
        data: dict[str, Any],
        vlan: dict[str, PortVlanInfo],
        bundle_vlan: dict[tuple, list[dict[str, str]] | None],
    ) -> list[dict[str, str]]:
        """VLAN findings of one link.

        The links of one port-channel share its configuration, so when both devices reported the
        port-channel's own switchport the bundle is judged once (on the port-channels) and every
        member link carries that same finding; otherwise each member link is judged on its own
        physical ports. ``bundle_vlan`` caches the verdict per bundle (``None``: the
        port-channels were not both reported, so the members are judged one by one).
        """
        bundles = data.get("bundles") or {}
        label_u, label_v = self.label(u), self.label(v)
        if bundles.get(u) and bundles.get(v):
            key = (frozenset((u, v)), tuple(sorted(bundles.items())))
            if key not in bundle_vlan:
                po_u = self._port_vlan(u, bundles[u])
                po_v = self._port_vlan(v, bundles[v])
                bundle_vlan[key] = (
                    [w.to_dict() for w in check_vlan_consistency(po_u, po_v, label_u, label_v)]
                    if po_u and po_v
                    else None
                )
            judged = bundle_vlan[key]
            if judged is not None:
                return list(judged)
        return [w.to_dict() for w in check_vlan_consistency(vlan[u], vlan[v], label_u, label_v)]

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
            record = {
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
            for end, node in (("source", source), ("target", target)):
                if data.get("vlan", {}).get(node):  # only links with VLAN data carry the key
                    record[f"{end}_vlan"] = data["vlan"][node]
            links.append(record)
        links.sort(key=lambda e: (e["source"], e["target"], e["source_port"], e["target_port"]))
        return {
            "schema_version": SCHEMA_VERSION,
            "metadata": dict(metadata or {}),
            "counts": self.counts(),
            "nodes": nodes,
            "links": links,
        }

    def save(
        self, metadata: dict[str, Any] | None = None, history: HistoryStore | None = None
    ) -> Path:
        """Write the snapshot to ``data/current_state.json`` (previous one is kept).

        The file is written to a temporary name and renamed into place, so a crash can
        never leave a half-written baseline. With ``history`` the same snapshot is also
        recorded as one run of the history store (``last_run`` then describes it); a history
        that cannot be written is logged and never costs the snapshot.

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
        self.last_run = None
        if history is not None:
            try:
                self.last_run = history.record(payload)
            except GraphEngineError as exc:
                logger.warning("The crawl history was not updated: %s", exc)
        return self.current_path


# ===================================================================== history
HISTORY_DIR_NAME = "history"
HISTORY_DB_NAME = "history.db"
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def new_run_id(now: datetime | None = None) -> str:
    """A sortable, URL-safe run ID: ``20261027T031500Z-a1b2c3``."""
    moment = now or datetime.now(timezone.utc)
    return f"{moment.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"


def is_run_id(text: object) -> bool:
    """True if ``text`` can be a run ID (letters, digits, ``.``, ``_``, ``-``; at most 64)."""
    return bool(_RUN_ID.match(str(text or "")))


def _norm_port(port: str) -> str:
    return normalize_interface(port).lower()


def _link_key(link: dict[str, Any]) -> tuple[tuple[str, str], tuple[str, str]]:
    """Identity of a link in a snapshot: its two (device, port) ends, in a fixed order."""
    ends = (
        (str(link.get("source") or ""), _norm_port(link.get("source_port") or "")),
        (str(link.get("target") or ""), _norm_port(link.get("target_port") or "")),
    )
    return tuple(sorted(ends))  # type: ignore[return-value]


def _link_speed(link: dict[str, Any]) -> int | None:
    """Speed in use on a link: the slowest known speed of its ends that are up."""
    known = [
        oper["speed"]
        for oper in (link.get("source_oper") or {}, link.get("target_oper") or {})
        if oper.get("speed") and oper.get("status") in (None, "unknown", "up")
    ]
    return min(known) if known else None


class HistoryStore:
    """Every crawl as one row of ``data/history/history.db`` (SQLite, standard library).

    A row keeps the run ID, the time, the seeds, a summary, the diff against the run before it
    (devices and links added / removed, cables moved to another port, speed changes, new blind
    spots) and the full graph snapshot (zlib-compressed JSON, the same document as
    ``current_state.json``). Each call opens its own connection, so the crawl thread and the web
    server's request threads can use one store. Reading never creates the directory; only
    ``record`` does. ``max_runs`` bounds the size: the oldest runs go first (0 keeps all).
    """

    def __init__(self, data_dir: str | os.PathLike | None = None, max_runs: int = 500) -> None:
        base = Path(data_dir) if data_dir is not None else GraphEngine._DEFAULT_DATA_DIR
        self.directory = base / HISTORY_DIR_NAME
        self.path = self.directory / HISTORY_DB_NAME
        self.max_runs = max(int(max_runs), 0)

    # ------------------------------------------------------------ diff
    @staticmethod
    def diff(before: dict[str, Any] | None, after: dict[str, Any]) -> dict[str, Any]:
        """What changed between two snapshots (``before`` is ``None`` for the first run).

        Returns the lists ``devices_added`` / ``devices_removed`` (``{id, label}``),
        ``links_added`` / ``links_removed`` (``{a, a_port, b, b_port}``), ``speed_changes``
        (``{a, a_port, b, b_port, from, to, direction}``, Mbps, ``direction`` is ``down`` for a
        downgrade), ``reconnections`` (a cable end that now goes to another device or port),
        ``new_blind_spots``, and ``summary`` with the counts of each.
        """
        empty: dict[str, Any] = {
            "first_run": before is None,
            "devices_added": [],
            "devices_removed": [],
            "links_added": [],
            "links_removed": [],
            "speed_changes": [],
            "reconnections": [],
            "new_blind_spots": [],
        }
        if before is not None:
            empty.update(HistoryStore._compare(before, after))
        empty["summary"] = {
            "devices_added": len(empty["devices_added"]),
            "devices_removed": len(empty["devices_removed"]),
            "links_added": len(empty["links_added"]),
            "links_removed": len(empty["links_removed"]),
            "speed_changes": len(empty["speed_changes"]),
            "speed_downgrades": sum(1 for c in empty["speed_changes"] if c["direction"] == "down"),
            "reconnections": len(empty["reconnections"]),
            "new_blind_spots": len(empty["new_blind_spots"]),
        }
        empty["changed"] = any(empty["summary"].values())
        return empty

    @staticmethod
    def _compare(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
        def labels(snapshot: dict[str, Any]) -> dict[str, str]:
            return {
                str(n["id"]): str(n.get("label") or n["id"]) for n in snapshot.get("nodes", [])
            }

        old_names, new_names = labels(before), labels(after)
        old_links = {_link_key(link): link for link in before.get("links", [])}
        new_links = {_link_key(link): link for link in after.get("links", [])}

        # The identity of a port is its normalised name; what is shown keeps the spelling the
        # device used.
        spelling: dict[tuple[str, str], str] = {}
        for link in [*before.get("links", []), *after.get("links", [])]:
            for end in ("source", "target"):
                port = link.get(f"{end}_port") or ""
                spelling[(str(link.get(end) or ""), _norm_port(port))] = port

        def describe(key: Any, names: dict[str, str]) -> dict[str, str]:
            (a, a_port), (b, b_port) = key
            return {
                "a": names.get(a, a),
                "a_port": spelling.get((a, a_port), a_port),
                "b": names.get(b, b),
                "b_port": spelling.get((b, b_port), b_port),
            }

        added = [describe(k, new_names) for k in sorted(set(new_links) - set(old_links))]
        removed = [describe(k, old_names) for k in sorted(set(old_links) - set(new_links))]

        speed_changes = []
        for key in sorted(set(new_links) & set(old_links)):
            was, now = _link_speed(old_links[key]), _link_speed(new_links[key])
            if was and now and was != now:
                speed_changes.append(
                    {
                        **describe(key, new_names),
                        "from": was,
                        "to": now,
                        "direction": "down" if now < was else "up",
                    }
                )

        # A cable moved: an end (device, port) that was in a removed link and is in an added one.
        gone = {end: key for key in set(old_links) - set(new_links) for end in key if end[1]}
        reconnections: list[dict[str, str]] = []
        seen: set[tuple[Any, ...]] = set()
        for key in sorted(set(new_links) - set(old_links)):
            for end in key:
                if end not in gone or end in seen:
                    continue
                seen.add(end)
                old_peer = next(e for e in gone[end] if e != end)
                new_peer = next(e for e in key if e != end)
                reconnections.append(
                    {
                        "device": new_names.get(end[0], end[0]),
                        "port": spelling.get(end, end[1]),
                        "from_device": old_names.get(old_peer[0], old_peer[0]),
                        "from_port": spelling.get(old_peer, old_peer[1]),
                        "to_device": new_names.get(new_peer[0], new_peer[0]),
                        "to_port": spelling.get(new_peer, new_peer[1]),
                    }
                )

        def spots(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
            found = (snapshot.get("metadata") or {}).get("blind_spots") or []
            return {str(b.get("node_id") or b.get("hostname")): b for b in found}

        old_spots, new_spots = spots(before), spots(after)
        return {
            "devices_added": [
                {"id": n, "label": new_names[n]} for n in sorted(set(new_names) - set(old_names))
            ],
            "devices_removed": [
                {"id": n, "label": old_names[n]} for n in sorted(set(old_names) - set(new_names))
            ],
            "links_added": added,
            "links_removed": removed,
            "speed_changes": speed_changes,
            "reconnections": reconnections,
            "new_blind_spots": [
                {
                    "hostname": spot.get("hostname", ""),
                    "reason": spot.get("reason", ""),
                    "discovered_via": spot.get("discovered_via", ""),
                }
                for key, spot in sorted(new_spots.items())
                if key not in old_spots
            ],
        }

    # --------------------------------------------------------- storage
    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    def record(
        self,
        snapshot: dict[str, Any],
        run_id: str | None = None,
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        """Add one crawl and return its run record (``run_id``, ``timestamp``, ``diff`` ...).

        ``snapshot`` is the document ``GraphEngine.to_dict`` / ``save`` writes. The diff is taken
        against the newest run already stored.

        Raises:
            GraphEngineError: If the run ID is not valid or already exists, or the database
                cannot be written.
        """
        run_id = run_id or new_run_id()
        if not is_run_id(run_id):
            raise GraphEngineError(f"Invalid history run ID {run_id!r}")
        metadata = snapshot.get("metadata") or {}
        stamp = timestamp or str(metadata.get("saved_at") or "") or _utc_now()
        seeds = metadata.get("seeds") or ([metadata["seed"]] if metadata.get("seed") else [])
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with closing(self._connect()) as db, db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS runs ("
                    " seq INTEGER PRIMARY KEY AUTOINCREMENT,"
                    " run_id TEXT NOT NULL UNIQUE,"
                    " timestamp TEXT NOT NULL,"
                    " seeds TEXT NOT NULL,"
                    " summary TEXT NOT NULL,"
                    " diff TEXT NOT NULL,"
                    " graph BLOB NOT NULL)"
                )
                db.execute("CREATE INDEX IF NOT EXISTS runs_timestamp ON runs (timestamp)")
                last = db.execute("SELECT graph FROM runs ORDER BY seq DESC LIMIT 1").fetchone()
                before = _unpack(last["graph"]) if last else None
                diff = self.diff(before, snapshot)
                summary = self._summary(snapshot)
                db.execute(
                    "INSERT INTO runs (run_id, timestamp, seeds, summary, diff, graph)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        run_id,
                        stamp,
                        json.dumps(seeds, ensure_ascii=False),
                        json.dumps(summary, ensure_ascii=False),
                        json.dumps(diff, ensure_ascii=False),
                        zlib.compress(json.dumps(snapshot, ensure_ascii=False).encode("utf-8")),
                    ),
                )
                if self.max_runs:
                    db.execute(
                        "DELETE FROM runs WHERE seq NOT IN"
                        " (SELECT seq FROM runs ORDER BY seq DESC LIMIT ?)",
                        (self.max_runs,),
                    )
        except sqlite3.IntegrityError as exc:
            raise GraphEngineError(f"History run {run_id!r} already exists") from exc
        except (sqlite3.Error, OSError, zlib.error) as exc:
            raise GraphEngineError(f"Could not record the crawl in {self.path}: {exc}") from exc
        logger.info("Crawl recorded in the history as %s", run_id)
        return {
            "run_id": run_id,
            "timestamp": stamp,
            "seeds": list(seeds),
            **summary,
            "diff": diff["summary"] | {"first_run": diff["first_run"], "changed": diff["changed"]},
        }

    @staticmethod
    def _summary(snapshot: dict[str, Any]) -> dict[str, Any]:
        metadata = snapshot.get("metadata") or {}
        counts = snapshot.get("counts") or {}
        links = snapshot.get("links", [])
        return {
            "devices": len(snapshot.get("nodes", [])),
            "links": len(links),
            "crawled": counts.get("crawled", 0),
            "failed": counts.get("failed", 0),
            "blind_spots": len(metadata.get("blind_spots") or []),
            "warnings": sum(1 for link in links if link.get("warnings")),
            "partial": bool(metadata.get("partial")),
        }

    def runs(self, limit: int | None = None) -> list[dict[str, Any]]:
        """The stored runs, newest first, each as ``record`` returns it (no graph, short diff).

        An absent history is an empty list. Raises ``GraphEngineError`` if it cannot be read.
        """
        if not self.path.is_file():
            return []
        query = "SELECT run_id, timestamp, seeds, summary, diff FROM runs ORDER BY seq DESC"
        params: tuple[Any, ...] = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (max(int(limit), 0),)
        try:
            with closing(self._connect()) as db:
                rows = db.execute(query, params).fetchall()
        except sqlite3.Error as exc:
            raise GraphEngineError(f"Could not read the history {self.path}: {exc}") from exc
        result = []
        for row in rows:
            diff = json.loads(row["diff"])
            result.append(
                {
                    "run_id": row["run_id"],
                    "timestamp": row["timestamp"],
                    "seeds": json.loads(row["seeds"]),
                    **json.loads(row["summary"]),
                    "diff": diff["summary"]
                    | {"first_run": diff["first_run"], "changed": diff["changed"]},
                }
            )
        return result

    def get(self, run_id: str) -> dict[str, Any] | None:
        """One run with its full diff and its full graph snapshot (``None`` if unknown).

        Raises ``GraphEngineError`` if the history cannot be read.
        """
        if not self.path.is_file() or not is_run_id(run_id):
            return None
        try:
            with closing(self._connect()) as db:
                row = db.execute(
                    "SELECT run_id, timestamp, seeds, summary, diff, graph FROM runs"
                    " WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise GraphEngineError(f"Could not read the history {self.path}: {exc}") from exc
        if row is None:
            return None
        return {
            "run_id": row["run_id"],
            "timestamp": row["timestamp"],
            "seeds": json.loads(row["seeds"]),
            **json.loads(row["summary"]),
            "diff": json.loads(row["diff"]),
            "graph": _unpack(row["graph"]),
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _unpack(blob: bytes) -> dict[str, Any]:
    return json.loads(zlib.decompress(blob).decode("utf-8"))
