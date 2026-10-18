"""
==============================================================================
Module Name:   visualizer.py
Description:   Renders the NetworkX topology as a single self-contained,
               interactive Pyvis HTML file: device-type icons coloured by crawl
               status, a Core / Distribution / Access / End hierarchy, interface
               names at both ends of every link, port-channels drawn as one
               bundle, link colour by nominal speed, failures shown on the
               diagram, hover tooltips and a click-to-open details panel.
Author:        Mai Tan Duc <ducmai.network@gmail.com>
Created:       2026-10-10
Version:       1.2.0
License:       MIT
==============================================================================
Usage:         imported by main.py (not run directly)
Notes:         Requires Python 3.10+
==============================================================================
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import networkx as nx
from pyvis.network import Network

from src import MapperError, normalize_interface
from src.diagram_assets import PAGE_CSS, PAGE_JS
from src.enrichment import (
    STYLE_COLORS,
    STYLE_ORDER,
    TIER_TITLES,
    NodeTypeRules,
    TierLayout,
    TierRules,
    assign_tiers,
    classify_node,
    failure_info,
    os_label,
    skip_badge,
    speed_label,
    speed_style,
    sub_label,
)
from src.graph_engine import STATUS_CRAWLED, STATUS_DISCOVERED, STATUS_FAILED
from src.icons import icon_uri
from src.linkcheck import SEVERITY_DEGRADED, SEVERITY_MISMATCH, End, effective_speed

logger = logging.getLogger(__name__)

COLOR_CRAWLED = "#2F9E6E"
COLOR_FAILED = "#D64545"
COLOR_DISCOVERED = "#9AA5B1"
COLOR_SEED_BORDER = "#E0A100"
_NO_ARROWS = {"to": {"enabled": False}, "from": {"enabled": False}}

_FILL = {
    STATUS_CRAWLED: COLOR_CRAWLED,
    STATUS_FAILED: COLOR_FAILED,
    STATUS_DISCOVERED: COLOR_DISCOVERED,
}
_LAYOUTS = ("hierarchical", "physics")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
# Pyvis still links Bootstrap from a CDN (card styling and menus we do not use), even
# when vis-network itself is inlined.
_EXTERNAL_LINK = re.compile(r"<link\b[^>]*\bhref=\"https?://[^>]*>", re.IGNORECASE)
_EXTERNAL_SCRIPT = re.compile(r"<script\b[^>]*\bsrc=\"https?://[^>]*>\s*</script>", re.IGNORECASE)

# (src, dst, src port, dst port, src bundle, dst bundle, edge data) with src < dst by node id
_Link = tuple[str, str, str, str, str, str, dict[str, Any]]

COLOR_DEGRADED = "#E08A00"
COLOR_MISMATCH = "#D9480F"
_WARN_COLORS = {SEVERITY_DEGRADED: COLOR_DEGRADED, SEVERITY_MISMATCH: COLOR_MISMATCH}


class VisualizationError(MapperError):
    """Raised when the diagram cannot be rendered."""


def _safe(text: object) -> str:
    """Neighbors choose their own LLDP strings: drop control characters before drawing them."""
    return _CONTROL_CHARS.sub("", str(text or ""))


def _short(port: str) -> str:
    """Compact interface name for drawing on the canvas (Gi0/1, Eth1/1, ...)."""
    return _safe(normalize_interface(port))


def _script_json(data: Any) -> str:
    """JSON that is safe to embed inside a <script> element (no ``</script>`` breakout)."""
    return (
        json.dumps(data, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


@dataclass(frozen=True)
class VisualOptions:
    """Look-and-feel settings (``visualization:`` in settings.yaml)."""

    layout: str = "hierarchical"  # hierarchical (Core on top) | physics (force-directed)
    height: str = "900px"
    width: str = "100%"
    bgcolor: str = "#ffffff"
    font_color: str = "#1f2933"
    collapse_parallel_links: bool = False  # True: merge non-bundled parallel links, labelled xN
    port_labels: bool = True  # draw interface names at both ends of every link
    inline_resources: bool = True  # embed the JS in the HTML so it works offline
    tier_rules: TierRules = field(default_factory=lambda: TierRules.from_settings(None))
    type_rules: NodeTypeRules = field(default_factory=NodeTypeRules)

    @classmethod
    def from_settings(cls, settings: dict[str, Any] | None) -> VisualOptions:
        """Build from the ``visualization`` mapping (missing keys use the defaults).

        Raises:
            VisualizationError: On an unknown layout or a non-mapping section.
            ConfigError: On an invalid ``tiers`` / ``node_types`` entry.
        """
        settings = settings or {}
        if not isinstance(settings, dict):
            raise VisualizationError("'visualization' in settings.yaml must be a mapping")
        defaults = cls()
        options = cls(
            layout=str(settings.get("layout", defaults.layout)),
            height=str(settings.get("height", defaults.height)),
            width=str(settings.get("width", defaults.width)),
            bgcolor=str(settings.get("bgcolor", defaults.bgcolor)),
            font_color=str(settings.get("font_color", defaults.font_color)),
            collapse_parallel_links=bool(
                settings.get("collapse_parallel_links", defaults.collapse_parallel_links)
            ),
            port_labels=bool(settings.get("port_labels", defaults.port_labels)),
            inline_resources=bool(settings.get("inline_resources", defaults.inline_resources)),
            tier_rules=TierRules.from_settings(settings.get("tiers"), settings.get("root", "")),
            type_rules=NodeTypeRules.from_settings(settings.get("node_types")),
        )
        if options.layout not in _LAYOUTS:
            raise VisualizationError(f"visualization.layout must be one of {', '.join(_LAYOUTS)}")
        return options


class TopologyVisualizer:
    """Draws the topology graph as an interactive HTML page."""

    def __init__(
        self, output_path: str | os.PathLike, options: VisualOptions | None = None
    ) -> None:
        self.output_path = Path(output_path)
        self.options = options or VisualOptions()

    # ------------------------------------------------------------------ render
    def render(self, graph: nx.MultiGraph, metadata: dict[str, Any] | None = None) -> Path:
        """Write the HTML file and return its path.

        Raises:
            VisualizationError: If the graph is empty or the file cannot be written.
        """
        if graph.number_of_nodes() == 0:
            raise VisualizationError("The topology is empty; nothing to draw")
        opts = self.options
        kinds = {
            node: classify_node(attrs, str(attrs.get("label") or node), opts.type_rules)
            for node, attrs in graph.nodes(data=True)
        }
        layout = (
            assign_tiers(graph, kinds, opts.tier_rules) if opts.layout == "hierarchical" else None
        )
        infos = self._node_infos(graph, kinds, layout)

        # directed=True only so Pyvis keeps parallel edges (an undirected Network silently
        # drops a second edge between the same two nodes); the arrows are switched off per edge.
        net = Network(
            height=opts.height,
            width=opts.width,
            bgcolor=opts.bgcolor,
            font_color=opts.font_color,
            directed=True,
            cdn_resources="in_line" if opts.inline_resources else "remote",
        )
        net.set_options(json.dumps(self._vis_options()))
        for node_id in sorted(graph.nodes):
            self._add_node(net, node_id, infos[node_id], layout)
        links = self._add_edges(net, graph)

        page = net.generate_html(notebook=False)
        if opts.inline_resources:
            page = self._make_self_contained(page)
        page = self._inject_page(page, graph, infos, links, layout, metadata or {})
        try:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            self.output_path.write_text(page, encoding="utf-8")
        except OSError as exc:
            raise VisualizationError(f"Could not write {self.output_path}: {exc}") from exc
        logger.info("Diagram written to %s", self.output_path)
        return self.output_path

    # ------------------------------------------------------------------- nodes
    def _node_infos(
        self, graph: nx.MultiGraph, kinds: dict[str, str], layout: TierLayout | None
    ) -> dict[str, dict[str, Any]]:
        """Everything known about each device, as plain JSON-able data."""
        infos: dict[str, dict[str, Any]] = {}
        for node_id, attrs in graph.nodes(data=True):
            status = attrs["status"]
            badge, tone, hint = "", "", ""
            if status == STATUS_FAILED:
                badge, hint = failure_info(attrs.get("error_kind") or "other")
                tone = "failed"
            elif status == STATUS_DISCOVERED and attrs.get("skip_code"):
                badge, tone = skip_badge(attrs["skip_code"]), "muted"
            peers = []
            for _u, other, data in graph.edges(node_id, data=True):
                peers.append(
                    {
                        "peer": _safe(other),
                        "peer_label": _safe(graph.nodes[other]["label"] or other),
                        "local": _safe(data["ports"].get(node_id, "")),
                        "remote": _safe(data["ports"].get(other, "")),
                        "bundle": _safe(data.get("bundles", {}).get(node_id, "")),
                        "peer_bundle": _safe(data.get("bundles", {}).get(other, "")),
                    }
                )
            peers.sort(key=lambda p: (p["local"], p["peer_label"], p["remote"]))
            infos[node_id] = {
                "label": _safe(attrs["label"] or node_id),
                "status": status,
                "seed": bool(attrs.get("seed")),
                "kind": kinds[node_id],
                "tier": TIER_TITLES[layout.tiers[node_id]] if layout else "",
                "os": _safe(os_label(attrs)),
                "sub": _safe(sub_label(attrs)),
                "badge": _safe(badge),
                "tone": tone,
                "hint": hint,
                "address": _safe(attrs.get("address")),
                "mgmt_ips": [_safe(ip) for ip in attrs.get("mgmt_ips", [])],
                "device_type": _safe(attrs.get("device_type")),
                "chassis_id": _safe(attrs.get("chassis_id")),
                "capabilities": [_safe(c) for c in attrs.get("capabilities", [])],
                "description": _safe(attrs.get("description")),
                "platform": _safe(attrs.get("platform")),
                "discovery": [_safe(p) for p in attrs.get("discovery", [])],
                "protocol_notes": {
                    _safe(k): _safe(v) for k, v in attrs.get("protocol_notes", {}).items()
                },
                "status_note": _safe(attrs.get("status_note")),
                "depth": attrs.get("depth"),
                "error": _safe(attrs.get("error")),
                "skip_reason": _safe(attrs.get("skip_reason")),
                "port_channels": [
                    {
                        "name": _safe(pc["name"]),
                        "protocol": _safe(pc["protocol"]),
                        "up": bool(pc["up"]),
                        "members": [
                            {"interface": _safe(m["interface"]), "state": _safe(m["state"])}
                            for m in pc["members"]
                        ],
                    }
                    for pc in attrs.get("port_channels", [])
                ],
                "lag_note": _safe(attrs.get("lag_note")),
                "links": peers,
            }
        return infos

    def _add_node(
        self, net: Network, node_id: str, info: dict[str, Any], layout: TierLayout | None
    ) -> None:
        extra: dict[str, Any] = {}
        if layout is not None:
            extra["level"] = layout.levels[node_id]
        net.add_node(
            _safe(node_id),
            label=" ",  # the name and the lines under it are drawn by the page script
            title=self._node_tooltip(info),
            shape="image",
            image=icon_uri(info["kind"], _FILL[info["status"]], info["seed"]),
            size=26 if info["seed"] else 22,
            shapeProperties={"useBorderWithImage": False},
            **extra,
        )

    @staticmethod
    def _node_tooltip(info: dict[str, Any]) -> str:
        status = info["status"] + (" (seed)" if info["seed"] else "")
        lines = [info["label"], f"Status: {status}", f"Type: {info['kind']}"]
        if info["tier"]:
            lines.append(f"Tier: {info['tier']}")
        if info["address"]:
            lines.append(f"Address: {info['address']}")
        if info["mgmt_ips"]:
            lines.append(f"Management IPs: {', '.join(info['mgmt_ips'])}")
        if info["os"]:
            lines.append(f"OS: {info['os']}")
        if info["device_type"]:
            lines.append(f"Device type: {info['device_type']}")
        if info["chassis_id"]:
            lines.append(f"Chassis ID: {info['chassis_id']}")
        if info["capabilities"]:
            lines.append(f"Capabilities: {', '.join(info['capabilities'])}")
        if info["platform"]:
            lines.append(f"Hardware: {info['platform']}")
        if info["description"]:
            lines.append(f"Platform: {info['description']}")
        if info["discovery"]:
            lines.append("Discovery: " + " + ".join(p.upper() for p in info["discovery"]))
        if info["depth"] is not None:
            lines.append(f"Depth: {info['depth']}")
        if info["port_channels"]:
            lines.append(
                "Port-channels: "
                + ", ".join(f"{pc['name']} ({pc['protocol']})" for pc in info["port_channels"])
            )
        if info["error"]:
            lines.append(f"Error: {info['error']}")
        if info["hint"]:
            lines.append(f"Hint: {info['hint']}")
        if info["skip_reason"]:
            lines.append(f"Not crawled: {info['skip_reason']}")
        lines.append("Click for details")
        return _safe("\n".join(lines))

    # ------------------------------------------------------------------- edges
    def _add_edges(self, net: Network, graph: nx.MultiGraph) -> list[dict[str, Any]]:
        """Add the vis edges and return one record per edge for the page script.

        Links that are members of the same port-channel become one thicker edge labelled
        with the bundle; other links get one edge each (or one per device pair with
        ``collapse_parallel_links``). Parallel edges are bowed apart symmetrically.
        """
        grouped: dict[tuple[str, str], list[_Link]] = defaultdict(list)
        for u, v, data in graph.edges(data=True):
            first, second = sorted((u, v))
            ports, bundles = data["ports"], data.get("bundles", {})
            grouped[(first, second)].append(
                (
                    first,
                    second,
                    ports.get(first, ""),
                    ports.get(second, ""),
                    bundles.get(first, ""),
                    bundles.get(second, ""),
                    data,
                )
            )
        member_states = self._member_states(graph)

        records: list[dict[str, Any]] = []
        for pair in sorted(grouped):
            links = sorted(grouped[pair], key=lambda x: (x[4], x[5], x[2], x[3]))
            entries = self._entries(links)
            for index, (group, is_bundle) in enumerate(entries):
                offset = index - (len(entries) - 1) / 2
                smooth: dict[str, Any] | bool = (
                    False if self.options.layout == "hierarchical" else {"type": "continuous"}
                )
                if offset:
                    smooth = {
                        "type": "curvedCW" if offset > 0 else "curvedCCW",
                        "roundness": round(min(abs(offset) * 0.3, 0.6), 2),
                    }
                records.append(self._emit(net, graph, group, is_bundle, smooth, member_states))
        return records

    def _entries(self, links: list[_Link]) -> list[tuple[list[_Link], bool]]:
        """Split the links of one device pair into drawable groups: ``(links, is_bundle)``."""
        bundled: dict[tuple[str, str], list[_Link]] = defaultdict(list)
        plain: list[_Link] = []
        for link in links:
            if link[4] or link[5]:
                bundled[(link[4], link[5])].append(link)
            else:
                plain.append(link)
        entries: list[tuple[list[_Link], bool]] = [
            (group, True) for _key, group in sorted(bundled.items())
        ]
        if self.options.collapse_parallel_links and plain:
            entries.append((plain, False))
        else:
            entries.extend(([link], False) for link in plain)
        return entries

    @staticmethod
    def _member_states(graph: nx.MultiGraph) -> dict[str, dict[str, str]]:
        states: dict[str, dict[str, str]] = {}
        for node_id, attrs in graph.nodes(data=True):
            table: dict[str, str] = {}
            for channel in attrs.get("port_channels", []):
                for member in channel["members"]:
                    table[normalize_interface(member["interface"]).lower()] = member["state"]
            states[node_id] = table
        return states

    @staticmethod
    def _ends(graph: nx.MultiGraph, link: _Link) -> list[End]:
        """The ends of a link that reported on their port (speed, duplex, state)."""
        ends = []
        for node, port in ((link[0], link[2]), (link[1], link[3])):
            state = link[6].get("oper", {}).get(node)
            if state:
                label = _safe(graph.nodes[node]["label"] or node)
                ends.append(
                    End(label, port, state.get("speed"), state.get("duplex"), state.get("status"))
                )
        return ends

    @staticmethod
    def _oper_text(link: _Link, node: str) -> str:
        """What one end reports about its port, e.g. ``1G full``, ``100M half`` or ``down``."""
        state = link[6].get("oper", {}).get(node)
        if not state:
            return ""
        if state.get("status") in ("down", "disabled", "err"):
            return str(state["status"])
        return " ".join(
            part
            for part in (
                speed_label(state["speed"]) if state.get("speed") else "",
                state.get("duplex") or "",
            )
            if part
        )

    def _emit(
        self,
        net: Network,
        graph: nx.MultiGraph,
        group: list[_Link],
        is_bundle: bool,
        smooth: dict[str, Any] | bool,
        member_states: dict[str, dict[str, str]],
    ) -> dict[str, Any]:
        first, second = group[0][0], group[0][1]
        label_a, label_b = (_safe(graph.nodes[n]["label"] or n) for n in (first, second))
        count = len(group)
        bundle_a, bundle_b = group[0][4], group[0][5]

        # Speed: what the ports are really running at where a device says so, else their type.
        speeds: list[int | None] = []
        origins: set[str] = set()
        for link in group:
            mbps, origin = effective_speed(self._ends(graph, link), link[2], link[3])
            speeds.append(mbps)
            origins.add(origin)
        known = all(speeds)
        aggregate = sum(s for s in speeds if s) if known else None
        if count == 1 or not known:
            speed_text = speed_label(speeds[0] if count == 1 else None)
        elif len(set(speeds)) == 1:
            speed_text = f"{count}×{speed_label(speeds[0])}"
        else:
            speed_text = " + ".join(speed_label(m) for m in sorted(set(speeds)))
        speed_source = (
            "operating"
            if "operating" in origins
            else "nominal"
            if "nominal" in origins
            else "unknown"
        )
        style_name, color, width = speed_style(aggregate)

        # Findings and where the link was seen.
        warnings: list[dict[str, str]] = []
        for link in group:
            for warning in link[6].get("warnings", []):
                if not any(
                    w["kind"] == warning["kind"] and w["text"] == warning["text"] for w in warnings
                ):
                    warnings.append({k: _safe(v) for k, v in warning.items()})
        severity = (
            SEVERITY_MISMATCH
            if any(w["severity"] == SEVERITY_MISMATCH for w in warnings)
            else SEVERITY_DEGRADED
            if warnings
            else ""
        )
        sources = sorted(set().union(*(link[6].get("sources", set()) for link in group)))
        cdp_only = bool(sources) and "lldp" not in sources

        bundle = self._bundle_info(graph, first, second, group) if is_bundle else None
        if bundle:
            label = f"{bundle['protocol']} ×{count}"
            if bundle["total"] > count:
                label += f"/{bundle['total']}"
            width += 2
            title_head = (
                f"{label_a} {bundle_a or '?'}  <->  {label_b} {bundle_b or '?'}  ·  "
                f"{bundle['protocol']} port-channel  ·  {count} link(s)  ·  {speed_text}"
            )
        else:
            label = f"×{count}" if count > 1 else ""
            width += min(count - 1, 4)
            title_head = f"{speed_text} · " + (f"{count} parallel links" if count > 1 else "link")
        suffix = {"operating": "operating", "nominal": "port type", "unknown": ""}[speed_source]
        if suffix and speed_text != "?":
            title_head += f" ({suffix})"

        pairs = [
            [
                _safe(link[2]),
                _safe(link[3]),
                member_states[first].get(normalize_interface(link[2]).lower(), ""),
                member_states[second].get(normalize_interface(link[3]).lower(), ""),
                self._oper_text(link, first),
                self._oper_text(link, second),
            ]
            for link in group
        ]
        lines = [title_head]
        for a_port, b_port, a_state, b_state, a_oper, b_oper in pairs:
            state = f"  ({a_state or '?'} / {b_state or '?'})" if bundle else ""
            oper = f"  [{a_oper or '?'} / {b_oper or '?'}]" if (a_oper or b_oper) else ""
            lines.append(f"{label_a} {a_port or '?'}  <->  {label_b} {b_port or '?'}{state}{oper}")
        if sources:
            lines.append("Seen by: " + " + ".join(s.upper() for s in sources))
        lines.extend(f"WARNING ({w['severity']}): {w['text']}" for w in warnings)

        edge_id = f"e{len(net.edges)}"
        options: dict[str, Any] = {}
        if cdp_only:
            options["dashes"] = [9, 6]
        if severity:
            glow = _WARN_COLORS[severity]
            options["shadow"] = {"enabled": True, "color": glow, "size": 14, "x": 0, "y": 0}
        font = {"size": 12, "strokeWidth": 4, "strokeColor": self.options.bgcolor}
        if severity and label:
            font["vadjust"] = -16  # leave the middle of the link to the warning marker
        net.add_edge(
            _safe(first),
            _safe(second),
            id=edge_id,
            title=_safe("\n".join(lines)),
            label=label,
            width=width,
            color=color,
            arrows=_NO_ARROWS,
            smooth=smooth,
            font=font,
            **options,
        )

        def end_labels(bundle_name: str, index: int) -> list[str]:
            if bundle_name:
                return [_safe(bundle_name)]
            return [_short(item[index]) for item in group if item[index]]

        return {
            "id": edge_id,
            "from": _safe(first),
            "to": _safe(second),
            "a": label_a,
            "b": label_b,
            "pairs": pairs,
            "fromPorts": end_labels(bundle_a, 2),
            "toPorts": end_labels(bundle_b, 3),
            "bundle": bundle,
            "speed": speed_text,
            "speed_style": style_name,
            "speed_source": speed_source,
            "color": color,
            "sources": sources,
            "warn": severity,
            "warnings": warnings,
            "checked": any(link[6].get("oper") for link in group),
            "label": label,
        }

    @staticmethod
    def _bundle_info(
        graph: nx.MultiGraph, first: str, second: str, group: list[_Link]
    ) -> dict[str, Any]:
        """Name, protocol and size of the port-channel a group of links belongs to."""
        bundle_a, bundle_b = group[0][4], group[0][5]
        channels = [
            channel
            for node, name in ((first, bundle_a), (second, bundle_b))
            if name
            for channel in graph.nodes[node].get("port_channels", [])
            if channel["name"] == name
        ]
        protocols = {c["protocol"] for c in channels}
        protocol = "LACP" if "LACP" in protocols else "PAgP" if "PAgP" in protocols else "static"
        total = max((len(c["members"]) for c in channels), default=len(group))
        return {
            "a": _safe(bundle_a),
            "b": _safe(bundle_b),
            "protocol": protocol,
            "total": max(total, len(group)),
            "links": len(group),
            "up": all(c["up"] for c in channels) if channels else True,
        }

    # ------------------------------------------------------------------ layout
    def _vis_options(self) -> dict[str, Any]:
        options: dict[str, Any] = {
            "interaction": {
                "hover": True,
                "tooltipDelay": 120,
                "navigationButtons": True,
                "keyboard": True,
            },
            "edges": {"smooth": {"type": "continuous"}},
        }
        if self.options.layout == "hierarchical":
            options["layout"] = {
                "hierarchical": {
                    "enabled": True,
                    "direction": "UD",
                    "levelSeparation": 210,
                    "nodeSpacing": 200,
                    "treeSpacing": 240,
                    "sortMethod": "hubsize",
                    "blockShifting": True,
                    "edgeMinimization": True,
                    "parentCentralization": True,
                }
            }
            options["physics"] = {"enabled": False}
        else:
            options["physics"] = {
                "barnesHut": {
                    "gravitationalConstant": -9000,
                    "centralGravity": 0.25,
                    "springLength": 190,
                    "springConstant": 0.04,
                    "damping": 0.12,
                    "avoidOverlap": 0.4,
                },
                "stabilization": {"enabled": True, "iterations": 200, "updateInterval": 25},
            }
        return options

    # ------------------------------------------------------------------- offline
    @staticmethod
    def _make_self_contained(page: str) -> str:
        """Drop the CDN references Pyvis leaves in, so the file opens on an offline laptop."""
        page = _EXTERNAL_LINK.sub("", page)
        page = _EXTERNAL_SCRIPT.sub("", page)
        page = page.replace(
            "</head>", "<style>body{margin:0}.card{border:0;width:100%}</style></head>", 1
        )
        leftover = _EXTERNAL_LINK.findall(page) + _EXTERNAL_SCRIPT.findall(page)
        if leftover:
            logger.warning("The page still references %d external resource(s)", len(leftover))
        return page

    # ----------------------------------------------------------- legend + details
    def _detail_data(
        self,
        graph: nx.MultiGraph,
        infos: dict[str, dict[str, Any]],
        links: list[dict[str, Any]],
        layout: TierLayout | None,
    ) -> dict[str, Any]:
        """Everything the page script needs, as plain JSON data."""
        problems = [
            {
                "id": _safe(node_id),
                "label": info["label"],
                "address": info["address"],
                "badge": info["badge"],
                "error": info["error"],
                "hint": info["hint"],
            }
            for node_id, info in sorted(infos.items())
            if info["status"] == STATUS_FAILED
        ]
        tiers = []
        if layout is not None:
            for level, (_name, title) in enumerate(layout.rows):
                members = sorted(n for n, lv in layout.levels.items() if lv == level)
                tiers.append({"name": title, "level": level, "nodes": [_safe(n) for n in members]})
        return {
            "nodes": {_safe(n): info for n, info in infos.items()},
            "links": {
                r["id"]: {
                    k: r[k]
                    for k in (
                        "from",
                        "to",
                        "a",
                        "b",
                        "pairs",
                        "bundle",
                        "speed",
                        "speed_source",
                        "speed_style",
                        "color",
                        "sources",
                        "warn",
                        "warnings",
                        "checked",
                        "label",
                    )
                }
                for r in links
            },
            "warnings": [
                {
                    "id": r["id"],
                    "a": r["a"],
                    "b": r["b"],
                    "severity": w["severity"],
                    "text": w["text"],
                }
                for r in links
                for w in r["warnings"]
            ],
            "draw": (
                [{k: r[k] for k in ("id", "from", "to", "fromPorts", "toPorts")} for r in links]
                if self.options.port_labels
                else []
            ),
            "tiers": tiers,
            "problems": problems,
        }

    def _inject_page(
        self,
        page: str,
        graph: nx.MultiGraph,
        infos: dict[str, dict[str, Any]],
        links: list[dict[str, Any]],
        layout: TierLayout | None,
        metadata: dict[str, Any],
    ) -> str:
        counts = {STATUS_CRAWLED: 0, STATUS_FAILED: 0, STATUS_DISCOVERED: 0}
        for info in infos.values():
            counts[info["status"]] += 1
        seed = html.escape(str(metadata.get("seed") or ""))
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        intro = f"Seed {seed} · " if seed else ""

        def item(key: str, inner: str) -> str:
            return (
                f'<li><button type="button" class="lg-item" data-filter="{html.escape(key)}" '
                f'aria-pressed="false">{inner}</button></li>'
            )

        def dot(color: str, border: str | None = None) -> str:
            return f'<span class="sw" style="background:{color};border-color:{border or color}"></span>'

        def line(color: str, css: str = "") -> str:
            return f'<span class="ln {css}" style="border-top-color:{color}"></span>'

        def count_of(kind: str) -> int:
            return sum(1 for info in infos.values() if info["kind"] == kind)

        status_rows = "".join(
            [
                item("status:crawled", dot(COLOR_CRAWLED) + f"Crawled ({counts[STATUS_CRAWLED]})"),
                item("status:failed", dot(COLOR_FAILED) + f"Failed ({counts[STATUS_FAILED]})"),
                item(
                    "status:discovered",
                    dot(COLOR_DISCOVERED) + f"Neighbor only ({counts[STATUS_DISCOVERED]})",
                ),
                item("seed", dot(COLOR_CRAWLED, COLOR_SEED_BORDER) + "Seed (gold ring)"),
            ]
        )
        kind_rows = "".join(
            item(
                f"kind:{kind}",
                f'<img class="ico" alt="" src="{icon_uri(kind, "#52606d")}">'
                f"{html.escape(kind)} ({count_of(kind)})",
            )
            for kind in sorted({info["kind"] for info in infos.values()})
        )
        tier_rows = (
            "".join(
                item(f"tier:{title}", f"<span class='tier-key'></span>{html.escape(title)}")
                for _name, title in layout.rows
            )
            if layout
            else ""
        )
        used_speeds = {r["speed_style"] for r in links}
        link_rows = "".join(
            item(f"speed:{name}", line(STYLE_COLORS[name]) + html.escape(name))
            for name in STYLE_ORDER
            if name in used_speeds
        )
        if any(r["bundle"] for r in links):
            link_rows += item("bundle", line("#5b6573", "thick") + "Port-channel")
        if any(r["sources"] and "lldp" not in r["sources"] for r in links):
            link_rows += item("src:cdp", line("#5b6573", "dashed") + "Seen by CDP only")
        degraded = sum(1 for r in links if r["warn"] == SEVERITY_DEGRADED)
        mismatch = sum(1 for r in links if r["warn"] == SEVERITY_MISMATCH)
        warn_rows = ""
        if degraded:
            warn_rows += item(
                "warn:degraded",
                f'<span class="wm" style="background:{COLOR_DEGRADED}">!</span>Degraded ({degraded})',
            )
        if mismatch:
            warn_rows += item(
                "warn:mismatch",
                f'<span class="wm" style="background:{COLOR_MISMATCH}">!</span>Mismatch ({mismatch})',
            )
        checked = sum(1 for r in links if r["checked"])
        if warn_rows:
            warn_note = f"{checked} of {len(links)} links checked."
        elif checked:
            warn_note = f"{checked} of {len(links)} links checked: no problems found."
        else:
            warn_note = "No interface speed/duplex data, so links were not checked."

        markup = f"""
<style>{PAGE_CSS}</style>
<div id="lldp-legend" role="note" aria-label="Legend">
<div class="lg-head"><h1>LLDP topology</h1>
<button type="button" id="lldp-legend-toggle" aria-expanded="true" aria-controls="lldp-legend-body"
title="Collapse or expand the legend">&#8211;</button></div>
<div id="lldp-legend-body">
<p>{intro}{graph.number_of_nodes()} devices, {graph.number_of_edges()} links · {stamp}</p>
<h2>Devices</h2><ul>{status_rows}</ul>
<h2>Types</h2><ul class="cols">{kind_rows}</ul>
{f'<h2>Hierarchy rows</h2><ul class="cols">{tier_rows}</ul>' if tier_rows else ""}
<h2>Links</h2><ul class="cols">{link_rows}</ul>
<p class="hint">Colour: speed in use where a device reports it, otherwise the port type.</p>
<h2>Link checks</h2><ul>{warn_rows}</ul><p class="hint">{warn_note}</p>
<div id="lldp-warnings"></div>
<div id="lldp-problems"></div>
<p class="hint">Click a legend item to highlight it, click again to clear. Click a device or a link for details.
Text under a name: management IP · OS. Text along a link: the interface at that end (or the port-channel).</p>
</div>
</div>
<aside id="lldp-detail" role="complementary" aria-label="Details" hidden></aside>
<script>window.LLDP_DETAIL={_script_json(self._detail_data(graph, infos, links, layout))};
window.LLDP_STYLE={_script_json({"bg": self.options.bgcolor, "fg": "#323f4b"})};</script>
<script>{PAGE_JS}</script>
"""
        if "</body>" not in page:
            raise VisualizationError("Pyvis output has no </body>; cannot add the legend")
        return page.replace("</body>", markup + "</body>", 1)
