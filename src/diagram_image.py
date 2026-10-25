"""A still picture of the topology (PNG, SVG, PDF) drawn by Graphviz.

The explorer page (``visualizer.py``) is the tool for looking around; this is the picture for a
report, a ticket or a wiki: the same tiers (Core on top), the same colour and thickness per speed,
port-channels, findings and endpoint groups, laid out by ``dot``. The module only builds the
Graphviz graph; ``TopologyVisualizer.render_image`` prepares what goes in and writes the file, so
everything here can be tested without the ``dot`` program.
"""

from __future__ import annotations

import html
from typing import Any

import graphviz

FONT = "Helvetica"
INK = "#1f2933"
MUTED = "#6b7785"
GROUP_FILL = "#eef2f6"
GROUP_LINE = "#9aa5b1"

#: icon type -> (Graphviz shape, extra style)
_SHAPES = {
    "switch": ("box", "rounded"),
    "router": ("ellipse", ""),
    "firewall": ("box3d", ""),
    "ap": ("hexagon", ""),
    "phone": ("box", "rounded"),
    "endpoint": ("note", ""),
    "unknown": ("diamond", ""),
}
#: Text on a green or red device is white; on the grey neighbor-only device it is dark.
_LIGHT_TEXT = {"crawled", "failed"}
_RASTER = {"png", "jpg", "jpeg", "gif", "bmp", "webp", "tif", "tiff"}


def _esc(text: Any) -> str:
    return html.escape(str(text), quote=True)


def _node_label(info: dict[str, Any], ink: str, muted: str) -> str:
    """HTML-like label: bold name, muted ``management IP . OS``, and the failure / neighbor note."""
    name = _esc(info["label"])
    rows = [f'<TR><TD><B><FONT COLOR="{ink}" POINT-SIZE="13">{name}</FONT></B></TD></TR>']
    if info.get("sub"):
        sub = _esc(info["sub"])
        rows.append(f'<TR><TD><FONT COLOR="{muted}" POINT-SIZE="10">{sub}</FONT></TD></TR>')
    if info.get("badge"):
        badge = _esc(info["badge"])
        rows.append(f'<TR><TD><FONT COLOR="{ink}" POINT-SIZE="9"><B>{badge}</B></FONT></TD></TR>')
    table = '<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="0" CELLPADDING="1">'
    return "<" + table + "".join(rows) + "</TABLE>>"


def _legend(
    counts: dict[str, int],
    speeds: list[tuple[str, str]],
    palette: dict[str, str],
    link_findings: bool,
) -> str:
    """One line under the title: what the colours of the devices and of the links mean."""

    def swatch(color: str, glyph: str, text: str) -> str:
        return f'<FONT COLOR="{color}">{glyph}</FONT> {_esc(text)}'

    items = [
        swatch(palette["crawled"], "\u25a0", f"Crawled ({counts['crawled']})"),
        swatch(palette["failed"], "\u25a0", f"Failed ({counts['failed']})"),
        swatch(palette["discovered"], "\u25a0", f"Neighbor only ({counts['discovered']})"),
        swatch(palette["seed"], "\u25cb", "Seed"),
    ]
    items += [swatch(color, "\u2501", name) for name, color in speeds]
    if link_findings:
        items.append(swatch(palette["degraded"], "\u25b2", "degraded link"))
        items.append(swatch(palette["mismatch"], "\u25b2", "mismatch"))
    return "&nbsp;&nbsp;&nbsp;".join(items)


def build_graph(
    *,
    infos: dict[str, dict[str, Any]],
    links: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    tier_names: dict[int, str],
    title: str,
    subtitle: str,
    palette: dict[str, str],
    port_labels: bool = True,
    image_format: str = "png",
) -> graphviz.Graph:
    """The Graphviz graph of one topology.

    Args:
        infos: Per device (keyed by its id as used in ``links`` and ``groups``): ``label``,
            ``sub``, ``badge``, ``kind``, ``status``, ``seed``, ``rank``.
        links: One entry per drawn link: ``from``, ``to``, ``color``, ``width``, ``dashed``,
            ``label``, ``fromPorts``, ``toPorts``, ``warn``, ``speed_style``.
        groups: Endpoint badges: ``sw`` (the switch), ``id``, ``text`` and the ``members`` they
            stand for.
        tier_names: Row rank -> tier name, drawn at the left of its row.
        palette: Colour per device status (``crawled``, ``failed``, ``discovered``), ``seed`` for
            the ring around the seed, and per finding severity (``degraded``, ``mismatch``).
            Link colours come with each link.
        port_labels: Write the interface at both ends of every link.
        image_format: ``png``, ``svg``, ``pdf`` ...; sets the resolution for raster formats.
    """
    graph_attr = {
        "rankdir": "TB",
        "splines": "spline",
        "nodesep": "0.55",
        "ranksep": "1.25",
        "pad": "0.4",
        "bgcolor": "white",
        "fontname": FONT,
        "labelloc": "t",
        "labeljust": "l",
        "newrank": "true",
    }
    if image_format in _RASTER:
        graph_attr["dpi"] = "150"
    dot = graphviz.Graph(
        name="lldp_topology",
        format=image_format,
        engine="dot",
        graph_attr=graph_attr,
        node_attr={"fontname": FONT, "margin": "0.16,0.09", "penwidth": "1.6"},
        edge_attr={"fontname": FONT, "fontsize": "10"},
    )

    in_group = {member for group in groups for member in group["members"]}
    shown = {nid: info for nid, info in infos.items() if nid not in in_group}
    # Graphviz reads ':' in a name as a port, so every device gets a plain id.
    ident: dict[str, str] = {}

    def dot_id(name: str) -> str:
        return ident.setdefault(name, f"n{len(ident)}")

    counts = {"crawled": 0, "failed": 0, "discovered": 0}
    for info in infos.values():
        counts[info["status"]] = counts.get(info["status"], 0) + 1
    speeds: dict[str, str] = {}
    for link in links:
        speeds.setdefault(link["speed_style"], link["color"])
    speed_items = [(name, color) for name, color in speeds.items() if name != "?"]
    legend = _legend(counts, speed_items, palette, any(link["warn"] for link in links))
    dot.attr(
        label=(
            '<<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="2"><TR><TD ALIGN="LEFT">'
            f'<B><FONT POINT-SIZE="20" COLOR="{INK}">{_esc(title)}</FONT></B><BR ALIGN="LEFT"/>'
            f'<FONT POINT-SIZE="11" COLOR="{MUTED}">{_esc(subtitle)}</FONT><BR ALIGN="LEFT"/>'
            f'<FONT POINT-SIZE="11" COLOR="{INK}">{legend}</FONT><BR ALIGN="LEFT"/>'
            "</TD></TR></TABLE>>"
        )
    )

    # ------------------------------------------------------------------ devices, a row per tier
    by_rank: dict[int, list[str]] = {}
    for nid, info in shown.items():
        by_rank.setdefault(info["rank"], []).append(nid)
    for group in groups:
        by_rank.setdefault(infos[group["members"][0]]["rank"], []).append(group["id"])

    def sort_key(nid: str) -> str:
        return (shown[nid]["label"] if nid in shown else nid).lower()

    previous_row = None
    for rank in sorted(by_rank):
        row_id = f"tier{rank}"
        tier = _esc(tier_names.get(rank, "").upper())
        with dot.subgraph() as row:
            row.attr(rank="same")
            row.node(
                row_id,
                label=f'<<B><FONT COLOR="{MUTED}" POINT-SIZE="11">{tier}</FONT></B>>',
                shape="plaintext",
            )
            for nid in sorted(by_rank[rank], key=sort_key):
                if nid in shown:
                    info = shown[nid]
                    shape, style = _SHAPES.get(info["kind"], ("box", "rounded"))
                    fill = palette[info["status"]]
                    light = info["status"] in _LIGHT_TEXT
                    ink, muted = ("white", "#e6edf3") if light else (INK, "#3e4c59")
                    style = "filled" + ("," + style if style else "")
                    style += ",bold" if info["seed"] else ""
                    row.node(
                        dot_id(nid),
                        label=_node_label(info, ink, muted),
                        shape=shape,
                        style=style,
                        fillcolor=fill,
                        color=palette["seed"] if info["seed"] else fill,
                        penwidth="4" if info["seed"] else "1.6",
                        tooltip=info["label"],
                    )
                else:
                    group = next(g for g in groups if g["id"] == nid)
                    row.node(
                        dot_id(nid),
                        # without its emoji: not every Graphviz has a font for it
                        label=group["text"].split(" ", 1)[-1],
                        shape="box",
                        style="rounded,dashed,filled",
                        fillcolor=GROUP_FILL,
                        color=GROUP_LINE,
                        fontcolor=INK,
                    )
        if previous_row:
            dot.edge(previous_row, row_id, style="invis")
        previous_row = row_id

    # ------------------------------------------------------------------ cables
    for group in groups:
        dot.edge(
            dot_id(group["sw"]),
            dot_id(group["id"]),
            color=GROUP_LINE,
            penwidth="1.4",
            style="dashed",
        )
    for link in links:
        if link["from"] in in_group or link["to"] in in_group:
            continue  # stands behind its endpoint group
        text = link["label"]
        if link["warn"]:
            text = (text + "\n" if text else "") + f"\u25b2 {link['warn']}"
        attrs = {
            "color": link["color"],
            "penwidth": str(link["width"]),
            "style": "dashed" if link["dashed"] else "solid",
        }
        if text:
            warn_color = palette.get(link["warn"], INK) if link["warn"] else INK
            attrs.update(label=text, fontsize="10", fontcolor=warn_color)
        if port_labels:
            if link["fromPorts"]:
                attrs["taillabel"] = ", ".join(link["fromPorts"][:3])
            if link["toPorts"]:
                attrs["headlabel"] = ", ".join(link["toPorts"][:3])
            attrs.update(
                labelfontsize="9", labelfontcolor=MUTED, labeldistance="2.2", labelangle="-25"
            )
        dot.edge(dot_id(link["from"]), dot_id(link["to"]), **attrs)
    return dot
