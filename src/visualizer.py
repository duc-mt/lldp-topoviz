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
from src.diagram_assets import (
    OPS_CSS,
    OPS_ENGINE_JS,
    OPS_UI_JS,
    PAGE_CSS,
    PAGE_JS,
    SIM_CSS,
    SIM_ENGINE_JS,
    SIM_UI_JS,
)
from src.enrichment import (
    STYLE_COLORS,
    STYLE_ORDER,
    TIER_ORDER,
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


def load_snapshot(path: str | os.PathLike) -> dict[str, Any] | None:
    """Read a ``current_state.json`` / ``previous_state.json`` snapshot (``None`` if missing or unreadable)."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) and isinstance(payload.get("nodes"), list) else None


def graph_from_snapshot(payload: dict[str, Any]) -> nx.MultiGraph:
    """Rebuild the topology graph from a snapshot written by :meth:`GraphEngine.save`."""
    graph: nx.MultiGraph = nx.MultiGraph()
    for node in payload.get("nodes", []):
        attrs = {k: v for k, v in node.items() if k != "id"}
        attrs.setdefault("status", STATUS_DISCOVERED)
        attrs.setdefault("label", node["id"])
        attrs.setdefault("port_channels", [])
        graph.add_node(node["id"], **attrs)
    for link in payload.get("links", []):
        source, target = link.get("source"), link.get("target")
        if source not in graph or target not in graph:
            continue
        bundles = {
            node: name
            for node, name in (
                (source, link.get("source_bundle")),
                (target, link.get("target_bundle")),
            )
            if name
        }
        oper = {
            node: state
            for node, state in (
                (source, link.get("source_oper")),
                (target, link.get("target_oper")),
            )
            if state
        }
        graph.add_edge(
            source,
            target,
            ports={source: link.get("source_port", ""), target: link.get("target_port", "")},
            bundles=bundles,
            sources=set(link.get("sources", [])),
            oper=oper,
            warnings=list(link.get("warnings", [])),
        )
    return graph


#: A switch needs at least this many leaf endpoints of one class for them to fold into one badge.
GROUP_MIN = 3
_GROUP_STYLE = {  # leaf class -> (icon, plural noun)
    "phone": ("\U0001f4de", "Phones"),
    "ap": ("\U0001f4f6", "Access Points"),
    "server": ("\U0001f5a5", "Hosts"),
    "generic": ("\U0001f5a5", "Endpoints"),
}
_LEAF_BY_CLASS = {
    "endpoint_phone": "phone",
    "endpoint_ap": "ap",
    "endpoint_server": "server",
    "endpoint_generic": "generic",
}
_LEAF_BY_KIND = {"phone": "phone", "ap": "ap", "endpoint": "generic"}


def _leaf_class(attrs: dict[str, Any], kind: str) -> str:
    """``phone`` / ``ap`` / ``server`` / ``generic`` for an end device, ``""`` for infrastructure.

    The class the crawler recorded wins; a graph built another way falls back to the icon type.
    """
    return _LEAF_BY_CLASS.get(str(attrs.get("endpoint_class") or "")) or _LEAF_BY_KIND.get(kind, "")


def _leaf_groups(graph: nx.MultiGraph, infos: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Leaf endpoints that fold into one badge per switch: ``[{id, edge, sw, leaf, text, ...}]``.

    A device joins a group only if it is a leaf, hangs off exactly one link (a dual-homed server
    is never hidden), is not the seed and its link has no link-check finding to keep visible.
    A switch needs :data:`GROUP_MIN` such devices of one class to get a group.
    """
    warned = {
        node for u, v, data in graph.edges(data=True) if data.get("warnings") for node in (u, v)
    }
    buckets: dict[tuple[str, str], list[str]] = defaultdict(list)
    for node_id in sorted(graph.nodes):
        info = infos[node_id]
        if not info["leaf"] or info["seed"] or node_id in warned or len(info["links"]) != 1:
            continue
        buckets[(info["links"][0]["peer"], info["leaf"])].append(node_id)
    groups = []
    for (switch, leaf), members in sorted(buckets.items()):
        if len(members) < GROUP_MIN:
            continue
        icon, noun = _GROUP_STYLE[leaf]
        text = f"{icon} {len(members)} {noun}"
        shown = [infos[m]["label"] for m in members[:20]]
        more = f"\n+{len(members) - 20} more" if len(members) > 20 else ""
        groups.append(
            {
                "id": _safe(f"grp:{switch}|{leaf}"),
                "edge": _safe(f"ge:{switch}|{leaf}"),
                "sw": _safe(switch),
                "leaf": leaf,
                "text": text,
                "title": _safe(
                    f"{text} on {infos[switch]['label']}\n"
                    + "\n".join(shown)
                    + more
                    + "\nClick to expand or collapse"
                ),
                "members": [_safe(m) for m in members],
            }
        )
    return groups


class _EdgeSink:
    """Stands in for the Pyvis network where only the drawn links matter (the still picture is made by Graphviz)."""

    def __init__(self) -> None:
        self.edges: list[dict[str, Any]] = []

    def add_edge(self, source: str, to: str, **options: Any) -> None:
        self.edges.append({"from": source, "to": to, **options})


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
    def render(
        self,
        graph: nx.MultiGraph,
        metadata: dict[str, Any] | None = None,
        previous: nx.MultiGraph | dict[str, Any] | None = None,
    ) -> Path:
        """Write the HTML file and return its path.

        ``previous`` is the snapshot of the run before this one (a graph, or the dict read from
        ``previous_state.json``). With it the page gets a Current / Previous / Diff switch: what was
        removed since is drawn too (hidden in the current view), what was added or changed is marked.

        Raises:
            VisualizationError: If the graph is empty or the file cannot be written.
        """
        if graph.number_of_nodes() == 0:
            raise VisualizationError("The topology is empty; nothing to draw")
        opts = self.options
        # What is drawn is the current topology plus, when a previous snapshot is given, what has
        # disappeared since (flagged ``_ghost``; the page hides it in the current view).
        drawn, diff, link_diff = self._compare(graph, previous)
        kinds = {
            node: classify_node(attrs, str(attrs.get("label") or node), opts.type_rules)
            for node, attrs in drawn.nodes(data=True)
        }
        # The simulation needs every device's tier even when the force-directed layout is drawn.
        tiering = assign_tiers(drawn, kinds, opts.tier_rules)
        layout = tiering if opts.layout == "hierarchical" else None
        infos = self._node_infos(drawn, kinds, layout, tiering.tiers)

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
        # A group badge goes in just before its first member, so the layout keeps it in that slot.
        present = {
            node: {**infos[node], "links": [p for p in infos[node]["links"] if not p.get("ghost")]}
            for node in graph.nodes
        }
        groups = _leaf_groups(graph, present)
        group_at = {g["members"][0]: g for g in groups}
        for node_id in sorted(drawn.nodes):
            if _safe(node_id) in group_at:
                self._add_group(net, group_at[_safe(node_id)], layout, infos[node_id])
            self._add_node(net, node_id, infos[node_id], layout)
        links = self._add_edges(net, drawn, link_diff)
        if diff["baseline"]:
            diff["links"] = {r["id"]: r["diff"] for r in links if r.get("diff")}
        for group in groups:
            net.add_edge(
                group["sw"],
                group["id"],
                id=group["edge"],
                hidden=True,
                width=2,
                color="#9AA5B1",
                arrows=_NO_ARROWS,
                smooth=False if opts.layout == "hierarchical" else {"type": "continuous"},
            )

        page = net.generate_html(notebook=False)
        if opts.inline_resources:
            page = self._make_self_contained(page)
        page = self._inject_page(page, graph, infos, links, layout, metadata or {}, groups, diff)
        try:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            self.output_path.write_text(page, encoding="utf-8")
        except OSError as exc:
            raise VisualizationError(f"Could not write {self.output_path}: {exc}") from exc
        logger.info("Diagram written to %s", self.output_path)
        return self.output_path

    # ------------------------------------------------------------- still picture (PNG / SVG / PDF)
    def build_image_graph(
        self,
        graph: nx.MultiGraph,
        metadata: dict[str, Any] | None = None,
        image_format: str = "png",
    ) -> Any:
        """The Graphviz graph of the topology: the explorer's tiers, speeds, port-channels and endpoint groups."""
        from src.diagram_image import (  # Graphviz is only needed for the picture, not for the page
            build_graph,
        )

        opts = self.options
        kinds = {
            node: classify_node(attrs, str(attrs.get("label") or node), opts.type_rules)
            for node, attrs in graph.nodes(data=True)
        }
        tiering = assign_tiers(graph, kinds, opts.tier_rules)
        infos = self._node_infos(graph, kinds, tiering, tiering.tiers)
        groups = _leaf_groups(graph, infos)
        sink = _EdgeSink()
        records = self._add_edges(sink, graph)
        links = [
            {
                "from": record["from"],
                "to": record["to"],
                "color": record["color"],
                "width": edge["width"],
                "dashed": bool(edge.get("dashes")),
                "label": record["label"],
                "fromPorts": record["fromPorts"],
                "toPorts": record["toPorts"],
                "warn": record["warn"],
                "speed_style": record["speed_style"],
            }
            for record, edge in zip(records, sink.edges, strict=True)
        ]
        seed = str((metadata or {}).get("seed") or "")
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return build_graph(
            infos={_safe(node): info for node, info in infos.items()},
            links=links,
            groups=groups,
            tier_names={info["rank"]: info["tier"] for info in infos.values()},
            title="LLDP topology",
            subtitle=(f"Seed {seed} · " if seed else "")
            + f"{graph.number_of_nodes()} devices, {graph.number_of_edges()} cables · {stamp}",
            palette={
                STATUS_CRAWLED: COLOR_CRAWLED,
                STATUS_FAILED: COLOR_FAILED,
                STATUS_DISCOVERED: COLOR_DISCOVERED,
                "seed": COLOR_SEED_BORDER,
                **_WARN_COLORS,
            },
            port_labels=opts.port_labels,
            image_format=image_format,
        )

    def render_image(
        self,
        graph: nx.MultiGraph,
        metadata: dict[str, Any] | None = None,
        path: str | os.PathLike = "output/topology.png",
    ) -> Path:
        """Write the topology as a picture (the file extension sets the format: png, svg, pdf, ...).

        Raises:
            VisualizationError: On an unknown format, a missing ``graphviz`` package or ``dot`` program, or a
                failed run of ``dot`` (the DOT source is kept next to the picture then).
        """
        out = Path(path)
        try:
            import graphviz
        except ImportError as exc:
            raise VisualizationError(
                "The 'graphviz' Python package is not installed (pip install graphviz; the Graphviz program "
                "'dot' is needed as well: apt install graphviz / brew install graphviz)"
            ) from exc
        fmt = out.suffix.lstrip(".").lower()
        if fmt not in graphviz.FORMATS:
            raise VisualizationError(
                f"Cannot draw a '.{fmt}' picture: use png, svg, pdf, ... (a .html path writes the interactive page only)"
            )
        dot = self.build_image_graph(graph, metadata, fmt)
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            rendered = dot.render(filename=out.stem, directory=str(out.parent), cleanup=True)
        except graphviz.ExecutableNotFound as exc:
            raise VisualizationError(
                "The Graphviz 'dot' program was not found (install the system package: apt install graphviz / "
                f"brew install graphviz). The DOT source is saved to {self._save_dot_source(dot, out)}"
            ) from exc
        except (graphviz.CalledProcessError, OSError) as exc:
            raise VisualizationError(
                f"Graphviz could not draw the picture ({exc}). The DOT source is saved to {self._save_dot_source(dot, out)}"
            ) from exc
        logger.info("Rendered topology picture to %s", rendered)
        return Path(rendered)

    @staticmethod
    def _save_dot_source(dot: Any, out: Path) -> Path:
        path = out.with_suffix(".gv")
        try:
            path.write_text(dot.source, encoding="utf-8")
        except OSError:
            logger.warning("Could not save the DOT source to %s", path)
        return path

    # ------------------------------------------------------------- snapshot diff
    def _entry_keys(self, graph: nx.MultiGraph) -> dict[str, dict[str, Any]]:
        """One stable key per drawn link (a bundle, a cable, or a collapsed pair) and what it carries."""
        keys: dict[str, dict[str, Any]] = {}
        for (first, second), links in self._grouped(graph).items():
            for group, is_bundle in self._entries(
                sorted(links, key=lambda x: (x[4], x[5], x[2], x[3]))
            ):
                keys[self._entry_key(group, is_bundle)] = {
                    "count": len(group),
                    "bundle": is_bundle,
                    "up": all(
                        d[6].get("oper", {}).get(n, {}).get("status") != "down"
                        for d in group
                        for n in (first, second)
                    ),
                    "links": group,
                }
        return keys

    def _entry_key(self, group: list[_Link], is_bundle: bool) -> str:
        first, second = group[0][0], group[0][1]
        if is_bundle:
            return f"{first}|{second}|{group[0][4]}|{group[0][5]}"
        if (
            len(group) > 1
        ):  # collapse_parallel_links: the plain cables of one pair are one drawn link
            return f"{first}|{second}|*"
        return f"{first}|{second}|{group[0][2]}|{group[0][3]}"

    def _compare(
        self, graph: nx.MultiGraph, previous: nx.MultiGraph | dict[str, Any] | None
    ) -> tuple[nx.MultiGraph, dict[str, Any], dict[str, dict[str, Any]]]:
        """``(graph to draw, diff for the page, status per link key)``.

        Without ``previous`` the graph is returned as it is and the page has no snapshot switch.
        """
        if previous is None:
            return graph, {"baseline": False}, {}
        before = graph_from_snapshot(previous) if isinstance(previous, dict) else previous
        drawn = graph.copy()
        nodes: dict[str, dict[str, str]] = {}
        for node, attrs in before.nodes(data=True):
            if node not in graph:
                drawn.add_node(node, **{**attrs, "_ghost": True})
                nodes[_safe(node)] = {"status": "removed", "detail": "no longer in the topology"}
        for node, attrs in graph.nodes(data=True):
            if node not in before:
                nodes[_safe(node)] = {"status": "added", "detail": "new since the previous run"}
            elif before.nodes[node].get("status") != attrs.get("status"):
                nodes[_safe(node)] = {
                    "status": "changed",
                    "detail": f"{before.nodes[node].get('status')} \u2192 {attrs.get('status')}",
                }
        now, was = self._entry_keys(graph), self._entry_keys(before)
        links: dict[str, dict[str, Any]] = {}
        for key, entry in now.items():
            if key not in was:
                links[key] = {
                    "status": "added",
                    "detail": f"{entry['count']} link(s), new since the previous run",
                }
            elif entry["bundle"] and entry["count"] != was[key]["count"]:
                links[key] = {
                    "status": "changed",
                    "detail": f"port-channel members {was[key]['count']} \u2192 {entry['count']}",
                }
            elif entry["bundle"] is False and entry["count"] != was[key]["count"]:
                links[key] = {
                    "status": "changed",
                    "detail": f"parallel links {was[key]['count']} \u2192 {entry['count']}",
                }
            elif entry["up"] != was[key]["up"]:
                links[key] = {
                    "status": "changed",
                    "detail": "a port reports down" if not entry["up"] else "ports are up again",
                }
        for key, entry in was.items():
            if key in now:
                continue
            links[key] = {"status": "removed", "detail": f"{entry['count']} link(s) gone"}
            for u, v, _a, _b, _ba, _bb, data in entry["links"]:
                drawn.add_edge(u, v, **{**data, "_ghost": True})
        saved = (previous.get("metadata", {}) if isinstance(previous, dict) else {}).get(
            "saved_at", ""
        )
        summary = {
            "nodes_added": sum(1 for n in nodes.values() if n["status"] == "added"),
            "nodes_removed": sum(1 for n in nodes.values() if n["status"] == "removed"),
            "nodes_changed": sum(1 for n in nodes.values() if n["status"] == "changed"),
            "links_added": sum(1 for n in links.values() if n["status"] == "added"),
            "links_removed": sum(1 for n in links.values() if n["status"] == "removed"),
            "links_changed": sum(1 for n in links.values() if n["status"] == "changed"),
        }
        return (
            drawn,
            {"baseline": True, "saved_at": _safe(saved), "nodes": nodes, "summary": summary},
            links,
        )

    # ------------------------------------------------------------------- nodes
    def _node_infos(
        self,
        graph: nx.MultiGraph,
        kinds: dict[str, str],
        layout: TierLayout | None,
        tiers: dict[str, str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Everything known about each device, as plain JSON-able data."""
        tiers = tiers if tiers is not None else (layout.tiers if layout else {})
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
                        **({"ghost": True} if data.get("_ghost") else {}),
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
                # Failure simulation: how far from the Core (0 = top), whether it is a Core device,
                # its leaf class (phone / ap / server / generic, '' for infrastructure) and whether
                # a single uplink makes it a SPOF (infrastructure and servers; not phones, APs ...).
                "rank": TIER_ORDER.index(tiers[node_id]) if node_id in tiers else 3,
                "core": tiers.get(node_id) == "core",
                "leaf": _leaf_class(attrs, kinds[node_id]),
                "spof_ok": _leaf_class(attrs, kinds[node_id]) in ("", "server"),
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
                **({"ghost": True} if attrs.get("_ghost") else {}),
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
            **({"hidden": True} if info.get("ghost") else {}),
            **extra,
        )

    def _add_group(
        self,
        net: Network,
        group: dict[str, Any],
        layout: TierLayout | None,
        first_member: dict[str, Any],
    ) -> None:
        """The (initially hidden) badge that stands for a group of leaf endpoints."""
        extra: dict[str, Any] = {}
        if layout is not None:
            extra["level"] = layout.levels[group["members"][0]]
        net.add_node(
            group["id"],
            label=group["text"],
            title=group["title"],
            shape="box",
            hidden=True,
            margin=8,
            borderWidth=2,
            shapeProperties={"borderRadius": 14},
            font={"size": 13, "color": "#1F2933"},
            color={"background": "#EEF2F6", "border": "#9AA5B1"},
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
    @staticmethod
    def _grouped(graph: nx.MultiGraph) -> dict[tuple[str, str], list[_Link]]:
        """The links of every device pair, as ``(first, second, port, port, bundle, bundle, data)``."""
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
        return grouped

    def _add_edges(
        self, net: Network, graph: nx.MultiGraph, link_diff: dict[str, dict[str, Any]] | None = None
    ) -> list[dict[str, Any]]:
        """Add the vis edges and return one record per edge for the page script.

        Links that are members of the same port-channel become one thicker edge labelled
        with the bundle; other links get one edge each (or one per device pair with
        ``collapse_parallel_links``). Parallel edges are bowed apart symmetrically.
        """
        grouped = self._grouped(graph)
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
                records.append(
                    self._emit(net, graph, group, is_bundle, smooth, member_states, link_diff or {})
                )
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
        link_diff: dict[str, dict[str, Any]] | None = None,
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
        ghost = all(link[6].get("_ghost") for link in group)
        diff = (link_diff or {}).get(self._entry_key(group, is_bundle))
        options: dict[str, Any] = {}
        if ghost:
            options["hidden"] = True  # gone since the previous run: the page shows it in that view
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
            **({"ghost": True} if ghost else {}),
            **({"diff": diff} if diff else {}),
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
        blind_spots: list[dict[str, Any]] | None = None,
        groups: list[dict[str, Any]] | None = None,
        diff: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
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
            if info["status"] == STATUS_FAILED and not info.get("ghost")
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
                        "ghost",
                        "diff",
                    )
                    if k in r
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
            "groups": groups or [],
            "blind_spots": [
                {k: (_safe(v) if isinstance(v, str) else v) for k, v in spot.items()}
                for spot in (blind_spots or [])
            ],
            "diff": diff or {"baseline": False},
            "meta": meta or {},
        }

    def _inject_page(
        self,
        page: str,
        graph: nx.MultiGraph,
        infos: dict[str, dict[str, Any]],
        links: list[dict[str, Any]],
        layout: TierLayout | None,
        metadata: dict[str, Any],
        groups: list[dict[str, Any]] | None = None,
        diff: dict[str, Any] | None = None,
    ) -> str:
        counts = {STATUS_CRAWLED: 0, STATUS_FAILED: 0, STATUS_DISCOVERED: 0}
        for info in infos.values():
            if not info.get("ghost"):
                counts[info["status"]] += 1
        all_links, links = (
            links,
            [r for r in links if not r.get("ghost")],
        )  # legend figures count what is there now
        shown = {node: info for node, info in infos.items() if not info.get("ghost")}
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
            return sum(1 for info in shown.values() if info["kind"] == kind)

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
            for kind in sorted({info["kind"] for info in shown.values()})
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

        status_pills = "".join(
            [
                f'<span class="pill up">\u25cf {counts[STATUS_CRAWLED]} UP</span>',
                f'<span class="pill warn">\u25b2 {degraded + mismatch} DEGRADED</span>'
                if degraded + mismatch
                else "",
                f'<span class="pill down">\u2716 {counts[STATUS_FAILED]} DOWN</span>'
                if counts[STATUS_FAILED]
                else "",
                f'<span class="pill info">{counts[STATUS_DISCOVERED]} NEIGHBOR ONLY</span>'
                if counts[STATUS_DISCOVERED]
                else "",
            ]
        )
        markup = f"""
<style>{PAGE_CSS}{SIM_CSS}{OPS_CSS}</style>
<header id="top" role="banner">
<span id="brand"><svg viewBox="0 0 32 32" width="26" height="26" aria-hidden="true" focusable="false"><g fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M16 6v8M16 14l-8 9M16 14l8 9"/></g><g fill="currentColor"><circle cx="16" cy="6" r="3.2"/><circle cx="16" cy="14" r="2.6"/><circle cx="8" cy="23" r="3.2"/><circle cx="24" cy="23" r="3.2"/></g></svg><span class="brand-title">LLDP TOPOLOGY EXPLORER</span></span>
<span class="top-gap"></span>
<div id="top-search"><input id="q" type="search" placeholder="Search device, IP, port&hellip;  ( / )" autocomplete="off"
aria-label="Search devices" aria-controls="search-results"><div id="search-results" role="listbox" aria-label="Search results" hidden></div></div>
<button type="button" id="b-search" title="Search ( / )">Search</button>
<button type="button" id="b-filter" aria-expanded="false" aria-controls="lldp-toolbar" title="Grouping and focus mode">Filter</button>
<button type="button" id="b-sim" aria-pressed="false" title="Simulate a failure or a maintenance drain ( S )">Simulate</button>
<button type="button" id="b-side" aria-pressed="true" title="Show or hide the sidebar">Panel</button>
<button type="button" id="b-theme" title="Light / dark theme">Theme</button>
<button type="button" id="b-full" title="Full screen">Full</button>
<button type="button" id="b-more" aria-expanded="false" aria-controls="p-more" title="Export">Export</button>
</header>
<div id="p-more" role="menu" aria-label="Export" hidden>
<h3>Documentation</h3>
<button type="button" id="m-docs" role="menuitem" title="Downloads a clean vector SVG and TOPOLOGY_SPEC.md">Export Documentation Bundle</button>
<p>Clean SVG (light, no UI, any resolution) + <b>TOPOLOGY_SPEC.md</b>: the device table and the link matrix.</p>
<button type="button" id="m-docsvg" role="menuitem">Clean SVG only</button>
<button type="button" id="m-docmd" role="menuitem">TOPOLOGY_SPEC.md only</button>
</div>
<div id="lldp-toolbar" class="panel" role="toolbar" aria-label="Failure simulation and grouping" hidden>
<button type="button" id="lldp-sim-toggle" aria-pressed="false"
title="Click a device or a link to see what loses its path to the Core">Simulate</button>
<label id="lldp-group-label" title="Collapse many phones, access points and hosts on one switch into one badge">
<input type="checkbox" id="lldp-group-toggle" checked> Group endpoints</label>
<label id="lldp-ego-label" title="Hover or select a device: light its neighbours up to two hops away and dim the rest to 15%">
<input type="checkbox" id="f-ego" checked> Focus mode (2-hop neighbourhood)</label>
<label id="lldp-ifaces-label" title="Interface names normally show above zoom 1.20 and on the links of the selected or hovered device">
<input type="checkbox" id="f-ifaces"> Always show interface names</label>
</div>
<div id="lldp-sim-banner" role="status" hidden>
<div class="sb-row"><span class="sb-tag">[SIMULATION ACTIVE]</span>
<span class="sb-counts" id="lldp-sim-counts">Isolated: 0 | SPOF: 0</span>
<span class="sb-btns"><button type="button" id="lldp-sim-spof" aria-pressed="false">Highlight SPOF</button>
<button type="button" id="lldp-sim-reset">Reset</button><button type="button" id="lldp-sim-exit">Exit</button></span></div>
<div id="lldp-sim-lines"></div>
</div>
<div id="lldp-sim-pop" role="dialog" aria-label="Simulate a failure" hidden></div>
<aside id="side" aria-label="Sidebar">
<div id="metrics" class="metrics"><div class="metric"><b class="num">{graph.number_of_nodes()}</b><span>Devices</span></div>
<div class="metric"><b class="num">{graph.number_of_edges()}</b><span>Cables</span></div>
<div class="metric"><b class="num">{len(links)}</b><span>Links</span></div>
<div class="metric"><b class="num">{len(layout.rows) if layout else "-"}</b><span>Tiers</span></div></div>
<div id="mpills" class="mrow">{status_pills}</div>
<div id="insp-empty" class="empty">Click any device or cable to inspect port status, metrics, and neighbor adjacencies, or press <kbd>/</kbd> to search.</div>
<div id="lldp-legend" role="note" aria-label="Legend">
<div class="lg-head"><h1>LLDP topology</h1>
<button type="button" id="lldp-legend-toggle" aria-expanded="true" aria-controls="lldp-legend-body"
title="Collapse or expand the legend">&#8211;</button></div>
<div id="lldp-legend-body">
<p>{intro}{graph.number_of_nodes()} devices, {graph.number_of_edges()} links &middot; {stamp}</p>
<details class="group" open><summary>Devices</summary><ul>{status_rows}</ul></details>
<details class="group" open><summary>Types</summary><ul class="cols">{kind_rows}</ul></details>
{f'<details class="group" open><summary>Hierarchy rows</summary><ul class="cols">{tier_rows}</ul></details>' if tier_rows else ""}
<details class="group" open><summary>Links</summary><ul class="cols">{link_rows}</ul>
<p class="hint">Colour: speed in use where a device reports it, otherwise the port type.</p></details>
<details class="group" open><summary>Link checks</summary><ul>{warn_rows}</ul><p class="hint">{warn_note}</p>
<div id="lldp-warnings"></div></details>
<div id="lldp-problems"></div>
<div id="lldp-blindspots"></div>
<p class="hint">Click a legend item to highlight it, click again to clear. Click a device or a link for details.
Text under a name: management IP &middot; OS. Text along a link: the interface at that end (or the port-channel).</p>
</div>
</div>
</aside>
<aside id="lldp-detail" role="complementary" aria-label="Details" hidden></aside>
<div id="diff-bar" role="group" aria-label="Snapshot time travel" hidden>
<span class="seg" role="radiogroup" aria-label="Snapshot">
<button type="button" id="t-current" data-time="current" role="radio" title="The network as it is now: removed items are hidden">Current State</button>
<button type="button" id="t-previous" data-time="previous" role="radio" title="The network as it was at the previous run: new items are hidden">Previous State</button>
<button type="button" id="t-overlay" data-time="overlay" role="radio" title="Both at once: green added, red dashed removed, orange changed">Diff Overlay</button></span>
<span id="diff-sum"></span>
<span class="key"><i class="k-add"></i>added<i class="k-rm"></i>removed<i class="k-ch"></i>changed</span>
</div>
<canvas id="minimap" width="160" height="100" aria-label="Minimap radar" title="Minimap: click or drag to move the view"></canvas>
<div id="hotkeys"><span class="hk">[ / ]</span> Search &nbsp;&middot;&nbsp; <span class="hk">[ F ]</span> Fit &nbsp;&middot;&nbsp; <span class="hk">[ S ]</span> Simulate &nbsp;&middot;&nbsp; <span class="hk">[ Esc ]</span> Deselect</div>
<script>window.LLDP_DETAIL={_script_json(self._detail_data(graph, infos, all_links, layout, metadata.get("blind_spots"), groups, diff, {"seed": str(metadata.get("seed") or ""), "generated": stamp}))};
window.LLDP_STYLE={_script_json({"bg": self.options.bgcolor, "fg": "#323f4b"})};</script>
<script>{PAGE_JS}</script>
<script>{SIM_ENGINE_JS}</script>
<script>{SIM_UI_JS}</script>
<script>{OPS_ENGINE_JS}</script>
<script>{OPS_UI_JS}</script>
"""
        if "</body>" not in page:
            raise VisualizationError("Pyvis output has no </body>; cannot add the legend")
        return page.replace("</body>", markup + "</body>", 1)
