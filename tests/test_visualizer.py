import json
import re
from urllib.parse import unquote

import pytest

from src.crawler import Crawler
from src.graph_engine import GraphEngine
from src.parser import LldpParser
from src.poller import FilePoller
from src.vendors import PortChannel, PortChannelMember
from src.visualizer import (
    COLOR_CRAWLED,
    COLOR_DISCOVERED,
    COLOR_FAILED,
    COLOR_SEED_BORDER,
    TopologyVisualizer,
    VisualizationError,
    VisualOptions,
)

from .conftest import make_config


@pytest.fixture
def demo_engine(demo_dir, tmp_path):
    engine = GraphEngine(data_dir=tmp_path)
    Crawler(
        FilePoller(demo_dir), LldpParser(), engine, make_config(), "10.0.0.1", "cisco_ios"
    ).run()
    return engine


def vis_data(page):
    match = re.search(
        r"nodes = new vis\.DataSet\((\[.*?\])\);\s*edges = new vis\.DataSet\((\[.*?\])\);",
        page,
        re.S,
    )
    return json.loads(match.group(1)), json.loads(match.group(2))


def detail(page):
    match = re.search(r"window\.LLDP_DETAIL=(\{.*?\});\nwindow\.LLDP_STYLE", page, re.S)
    return json.loads(match.group(1))


def render(engine, tmp_path, metadata=None, **options):
    out = tmp_path / "out" / "topology.html"
    TopologyVisualizer(out, VisualOptions(**options)).render(
        engine.graph, metadata or {"seed": "10.0.0.1"}
    )
    return out.read_text(encoding="utf-8")


def two_devices(tmp_path, links, channels_a=(), channels_b=()):
    """Devices a and b joined by ``links`` [(port_a, port_b), ...]."""
    engine = GraphEngine(data_dir=tmp_path)
    a, b = engine.resolve(name="a"), engine.resolve(name="b")
    for port_a, port_b in links:
        engine.add_link(a, port_a, b, port_b)
    engine.set_port_channels(a, channels_a)
    engine.set_port_channels(b, channels_b)
    engine.set_seed(a)
    engine.annotate_bundles()
    return engine


def channel(name, protocol, ports, state="bundled"):
    return PortChannel(name, protocol, True, tuple(PortChannelMember(p, state) for p in ports))


# ------------------------------------------------------------------- icons, status
def icon_of(node):
    return unquote(node["image"].split(",", 1)[1])


def test_devices_are_icons_coloured_by_status_with_a_ring_on_the_seed(demo_engine, tmp_path):
    nodes, _ = vis_data(render(demo_engine, tmp_path))
    by_id = {n["id"]: n for n in nodes}
    assert all(n["shape"] == "image" and n["image"].startswith("data:image/svg+xml") for n in nodes)
    assert COLOR_CRAWLED in icon_of(by_id["core2"]) and COLOR_FAILED in icon_of(by_id["fw1"])
    assert COLOR_DISCOVERED in icon_of(by_id["ap-lobby"])
    assert COLOR_SEED_BORDER in icon_of(by_id["core1"])
    assert COLOR_SEED_BORDER not in icon_of(by_id["core2"])
    assert by_id["core1"]["size"] > by_id["core2"]["size"]


def test_icon_type_follows_the_device(demo_engine, tmp_path):
    page = render(demo_engine, tmp_path)
    kinds = {n["label"]: n["kind"] for n in detail(page)["nodes"].values()}
    assert kinds == {
        "core1": "switch",
        "core2": "switch",
        "dist1": "switch",
        "acc1": "switch",
        "acc2": "switch",
        "acc3": "switch",
        "fw1": "firewall",
        "isp-edge": "router",
        "ap-lobby": "ap",
        "sw-legacy": "switch",
        "SEP001122334455": "phone",
    }
    nodes, _ = vis_data(page)
    icons = {n["id"]: icon_of(n) for n in nodes}
    assert icons["fw1"] != icons["core1"] and icons["ap-lobby"] != icons["isp-edge"]


def test_hover_tooltip_has_the_whole_description_and_the_hint(demo_engine, tmp_path):
    nodes, _ = vis_data(render(demo_engine, tmp_path))
    core2 = next(n for n in nodes if n["id"] == "core2")
    assert "RELEASE SOFTWARE (fc5)" in core2["title"] and "…" not in core2["title"]
    assert "Type: switch" in core2["title"] and "Tier: Core" in core2["title"]
    assert "OS: IOS-XE 17.9.4" in core2["title"] and core2["title"].endswith("Click for details")
    fw1 = next(n for n in nodes if n["id"] == "fw1")
    assert "Error: No replay data" in fw1["title"] and "Hint:" in fw1["title"]


# ------------------------------------------------------------- text under a device
def test_each_device_has_an_ip_and_os_line_and_a_status_badge(demo_engine, tmp_path):
    nodes = detail(render(demo_engine, tmp_path))["nodes"]
    assert nodes["dist1"]["sub"] == "10.0.1.1 · NX-OS 9.3(8)" and nodes["dist1"]["badge"] == ""
    assert nodes["fw1"]["badge"] == "No replay data" and nodes["fw1"]["tone"] == "failed"
    assert nodes["isp-edge"]["badge"] == "outside scope" and nodes["isp-edge"]["tone"] == "muted"
    assert nodes["ap-lobby"]["badge"] == "no mgmt IP"
    assert nodes["isp-edge"]["sub"] == "203.0.113.1 · Junos 22.4R2"  # advertised IP + platform


def test_vis_labels_are_blank_because_the_page_script_draws_the_text(demo_engine, tmp_path):
    nodes, _ = vis_data(render(demo_engine, tmp_path))
    assert all(n["label"].strip() == "" for n in nodes)


# ------------------------------------------------------------------------ hierarchy
def test_default_layout_is_hierarchical_with_one_row_per_tier(demo_engine, tmp_path):
    page = render(demo_engine, tmp_path)
    nodes, _ = vis_data(page)
    levels = {n["id"]: n["level"] for n in nodes}
    assert levels == {
        "fw1": 0,
        "isp-edge": 0,
        "core1": 1,
        "core2": 1,
        "dist1": 2,
        "acc1": 3,
        "acc2": 3,
        "acc3": 3,
        "ap-lobby": 4,
        "sw-legacy": 2,
        "sep001122334455": 4,
    }
    assert [t["name"] for t in detail(page)["tiers"]] == [
        "Edge",
        "Core",
        "Distribution",
        "Access",
        "End devices",
    ]
    assert '"hierarchical"' in page and '"enabled": false' in page  # physics off


def test_physics_layout_has_no_levels_or_tier_rows(demo_engine, tmp_path):
    page = render(demo_engine, tmp_path, layout="physics")
    nodes, _ = vis_data(page)
    assert all("level" not in n for n in nodes) and detail(page)["tiers"] == []
    assert "barnesHut" in page


def test_custom_tier_rules_are_applied(demo_engine, tmp_path):
    from src.enrichment import TierRules

    rules = TierRules.from_settings({"core": ["^dist"], "distribution": ["^core"]})
    nodes, _ = vis_data(render(demo_engine, tmp_path, tier_rules=rules))
    levels = {n["id"]: n["level"] for n in nodes}
    assert levels["dist1"] < levels["core1"]


# --------------------------------------------------------------------- port-channels
def test_member_links_of_a_bundle_become_one_labelled_edge(demo_engine, tmp_path):
    page = render(demo_engine, tmp_path)
    _, edges = vis_data(page)
    assert len(edges) == 11  # 16 links: five device pairs are bundles, six single links remain
    lag = next(e for e in edges if {e["from"], e["to"]} == {"core1", "dist1"})
    assert lag["label"] == "LACP ×2" and lag["title"].count("<->") == 3  # head + 2 members
    static = next(e for e in edges if {e["from"], e["to"]} == {"core2", "dist1"})
    assert static["label"] == "static ×2"
    single = next(e for e in edges if {e["from"], e["to"]} == {"core1", "core2"})
    assert single["label"] == ""
    assert lag["width"] > single["width"]  # a bundle is drawn thicker


def test_bundle_edges_are_labelled_with_the_bundle_names_at_each_end(demo_engine, tmp_path):
    draw = {
        frozenset((d["from"], d["to"])): d for d in detail(render(demo_engine, tmp_path))["draw"]
    }
    core_dist = draw[frozenset(("core1", "dist1"))]
    names = {core_dist["from"]: core_dist["fromPorts"], core_dist["to"]: core_dist["toPorts"]}
    assert names == {"core1": ["Po1"], "dist1": ["Po10"]}
    assert draw[frozenset(("dist1", "acc1"))]["toPorts"] in (["Po1"], ["Po30"])
    huawei = draw[frozenset(("dist1", "acc2"))]
    assert sorted(huawei["fromPorts"] + huawei["toPorts"]) == ["Eth-Trunk1", "Po40"]
    plain = draw[frozenset(("core1", "core2"))]
    assert plain["fromPorts"] == plain["toPorts"] == ["Gi1/0/47"]


def test_a_bundle_known_on_one_end_shows_the_members_on_the_other(demo_engine, tmp_path):
    draw = {
        frozenset((d["from"], d["to"])): d for d in detail(render(demo_engine, tmp_path))["draw"]
    }
    acc3 = draw[frozenset(("acc3", "dist1"))]
    ends = {acc3["from"]: acc3["fromPorts"], acc3["to"]: acc3["toPorts"]}
    assert ends == {"dist1": ["Po50"], "acc3": ["1/1/1", "1/1/2"]}  # AOS-CX bundles are unknown


def test_link_details_carry_protocol_members_and_states(demo_engine, tmp_path):
    links = detail(render(demo_engine, tmp_path))["links"]
    lag = next(k for k in links.values() if {k["a"], k["b"]} == {"core1", "dist1"})
    assert lag["bundle"] == {
        "a": "Po1",
        "b": "Po10",
        "protocol": "LACP",
        "total": 2,
        "links": 2,
        "up": True,
    }
    assert [p[2:4] for p in lag["pairs"]] == [["bundled", "bundled"], ["bundled", "bundled"]]
    assert [p[4:] for p in lag["pairs"]] == [["1G full", "1G full"], ["1G full", "1G full"]]
    assert lag["speed"] == "2×1G"
    one_sided = next(k for k in links.values() if {k["a"], k["b"]} == {"acc3", "dist1"})
    assert one_sided["bundle"]["a"] == "" and one_sided["bundle"]["b"] == "Po50"
    assert one_sided["pairs"][0][2:4] == ["", "bundled"]


def test_a_bundle_with_members_down_says_so(tmp_path):
    engine = two_devices(
        tmp_path,
        [("Gi1", "Gi1")],
        channels_a=[
            PortChannel(
                "Po1",
                "LACP",
                True,
                (PortChannelMember("Gi1", "bundled"), PortChannelMember("Gi2", "down")),
            )
        ],
    )
    _, edges = vis_data(render(engine, tmp_path))
    assert edges[0]["label"] == "LACP ×1/2"


def test_unbundled_parallel_links_are_bowed_apart_symmetrically(tmp_path):
    engine = two_devices(tmp_path, [("Gi1", "Gi1"), ("Gi2", "Gi2")])
    _, edges = vis_data(render(engine, tmp_path))
    assert len(edges) == 2 and all(e["label"] == "" for e in edges)
    assert {e["smooth"]["type"] for e in edges} == {"curvedCW", "curvedCCW"}
    assert edges[0]["smooth"]["roundness"] == edges[1]["smooth"]["roundness"]


def test_unbundled_parallel_links_can_be_collapsed(tmp_path):
    engine = two_devices(tmp_path, [("Gi1", "Gi1"), ("Gi2", "Gi2"), ("Gi3", "Gi3")])
    _, edges = vis_data(render(engine, tmp_path, collapse_parallel_links=True))
    assert len(edges) == 1 and edges[0]["label"] == "×3"
    assert detail(render(engine, tmp_path, collapse_parallel_links=True))["draw"][0][
        "fromPorts"
    ] == ["Gi1", "Gi2", "Gi3"]


def test_a_bundle_and_a_loose_link_between_the_same_devices_both_show(tmp_path):
    engine = two_devices(
        tmp_path,
        [("Gi1", "Gi1"), ("Gi2", "Gi2"), ("Gi9", "Gi9")],
        channels_a=[channel("Po1", "LACP", ["Gi1", "Gi2"])],
        channels_b=[channel("Po1", "LACP", ["Gi1", "Gi2"])],
    )
    _, edges = vis_data(render(engine, tmp_path))
    assert sorted(e["label"] for e in edges) == ["", "LACP ×2"]
    assert {e["smooth"]["type"] for e in edges} == {"curvedCW", "curvedCCW"}


def test_two_bundles_between_the_same_devices_stay_separate(tmp_path):
    engine = two_devices(
        tmp_path,
        [("Gi1", "Gi1"), ("Gi2", "Gi2"), ("Gi3", "Gi3"), ("Gi4", "Gi4")],
        channels_a=[
            channel("Po1", "LACP", ["Gi1", "Gi2"]),
            channel("Po2", "static", ["Gi3", "Gi4"]),
        ],
        channels_b=[
            channel("Po7", "LACP", ["Gi1", "Gi2"]),
            channel("Po8", "static", ["Gi3", "Gi4"]),
        ],
    )
    _, edges = vis_data(render(engine, tmp_path))
    assert sorted(e["label"] for e in edges) == ["LACP ×2", "static ×2"]


def test_port_labels_can_be_switched_off(demo_engine, tmp_path):
    assert detail(render(demo_engine, tmp_path, port_labels=False))["draw"] == []


# ---------------------------------------------------------------------- link speed
def test_link_colour_and_width_follow_the_nominal_speed(tmp_path):
    engine = GraphEngine(data_dir=tmp_path)
    ids = [engine.resolve(name=n) for n in ("a", "b", "c", "d")]
    engine.add_link(ids[0], "Te1/0/1", ids[1], "Te1/0/1")  # 10G
    engine.add_link(ids[0], "Gi1/0/1", ids[2], "Eth1/1")  # 1G (the slower known end)
    engine.add_link(ids[0], "Eth1/2", ids[3], "Eth1/2")  # unknown
    engine.set_seed(ids[0])
    _, edges = vis_data(render(engine, tmp_path))
    by_to = {e["to"]: e for e in edges}
    assert by_to["b"]["color"] == "#2F6FDE" and by_to["c"]["color"] == "#7A869A"
    assert by_to["d"]["color"] == "#B8C0CC"
    assert by_to["b"]["width"] > by_to["c"]["width"] > by_to["d"]["width"]


def test_legend_lists_only_what_is_in_the_diagram(demo_engine, tmp_path):
    page = render(demo_engine, tmp_path)
    legend = page.split('id="lldp-legend"', 1)[1].split('<aside id="lldp-detail"', 1)[0]
    assert "firewall" in legend and "phone" in legend and "toaster" not in legend
    assert "1G" in legend and "10G" in legend and "100G" not in legend
    assert "Port-channel" in legend and "Seen by CDP only" in legend


# ---------------------------------------------------------------- failures on the page
def test_failures_are_listed_with_a_reason_and_a_hint(demo_engine, tmp_path):
    page = render(demo_engine, tmp_path)
    (problem,) = detail(page)["problems"]
    assert problem["label"] == "fw1" and problem["badge"] == "No replay data"
    assert problem["address"] == "10.0.0.250" and "replay directory" in problem["hint"]
    assert "lldp-problems" in page


def test_failure_kinds_have_their_own_wording(tmp_path):
    engine = GraphEngine(data_dir=tmp_path)
    seed = engine.resolve(name="seed")
    engine.set_seed(seed)
    engine.mark_crawled(seed, "10.0.0.1", "cisco_ios", 0)
    for name, kind in (("n1", "auth"), ("n2", "timeout"), ("n3", "connect"), ("n4", "parse")):
        node = engine.resolve(name=name)
        engine.add_link(seed, "Gi1", node, "Gi1")
        engine.mark_failed(node, "boom", kind=kind)
    badges = {p["label"]: p["badge"] for p in detail(render(engine, tmp_path))["problems"]}
    assert badges == {
        "n1": "SSH auth failed",
        "n2": "SSH timeout",
        "n3": "SSH connect failed",
        "n4": "Parse failed",
    }


def test_no_problems_section_content_when_everything_worked(tmp_path):
    engine = two_devices(tmp_path, [("Gi1", "Gi1")])
    engine.mark_crawled("a", "10.0.0.1", "cisco_ios", 0)
    engine.mark_crawled("b", "10.0.0.2", "cisco_ios", 1)
    assert detail(render(engine, tmp_path))["problems"] == []


# --------------------------------------------------------------------- details panel
def test_details_panel_has_the_full_description_and_every_link(demo_engine, tmp_path):
    data = detail(render(demo_engine, tmp_path))
    core2 = data["nodes"]["core2"]
    assert core2["description"].startswith("Cisco IOS Software [Cupertino], Catalyst L3 Switch")
    assert core2["description"].endswith("RELEASE SOFTWARE (fc5)")  # not truncated
    dist1 = data["nodes"]["dist1"]
    assert dist1["description"] == "Cisco Nexus Operating System (NX-OS) Software 9.3(8)"
    assert dist1["os"] == "NX-OS 9.3(8)" and dist1["tier"] == "Distribution"
    assert len(dist1["links"]) == 10
    assert {
        "peer": "acc2",
        "peer_label": "acc2",
        "local": "Eth1/11",
        "remote": "GigabitEthernet0/0/24",
        "bundle": "Po40",
        "peer_bundle": "Eth-Trunk1",
    } in dist1["links"]
    assert [pc["name"] for pc in dist1["port_channels"]] == ["Po10", "Po20", "Po30", "Po40", "Po50"]
    assert data["nodes"]["acc3"]["lag_note"] and data["nodes"]["acc3"]["port_channels"] == []
    assert data["nodes"]["fw1"]["error"].startswith("No replay data")
    assert "outside crawl.allowed_subnets" in data["nodes"]["isp-edge"]["skip_reason"]


def test_page_wires_up_click_details_text_and_tiers(demo_engine, tmp_path):
    page = render(demo_engine, tmp_path)
    assert 'id="lldp-detail"' in page and "network.on('click'" in page
    assert "afterDrawing" in page and "beforeDrawing" in page
    assert "white-space:pre-wrap!important" in page
    script = page.split('id="lldp-detail"', 1)[1]
    assert "innerHTML" not in script  # device text only ever goes in via textContent


# --------------------------------------------------------------------- page hygiene
def test_page_is_self_contained(demo_engine, tmp_path):
    page = render(demo_engine, tmp_path)
    assert not re.search(r'<(?:script|link)\b[^>]*(?:src|href)="https?://', page)
    assert not re.search(r"<img\b[^>]*src=\"https?://", page)
    assert 'id="lldp-legend"' in page and "Crawled (7)" in page and "Failed (1)" in page
    assert "Seed 10.0.0.1" in page


def test_remote_resources_option_keeps_the_cdn_links(demo_engine, tmp_path):
    page = render(demo_engine, tmp_path, inline_resources=False)
    assert re.search(r'<script\b[^>]*src="https?://', page)


def test_hostile_lldp_strings_cannot_break_out_of_the_page(tmp_path):
    engine = GraphEngine(data_dir=tmp_path)
    a = engine.resolve(name="a")
    evil = engine.resolve(
        name="</script><script>alert(1)</script>\x07", description="<img src=x onerror=alert(1)>"
    )
    engine.add_link(a, "Gi1</script>", evil, "Gi1")
    engine.set_port_channels(a, [channel("Po</script>", "LACP", ["Gi1</script>"])])
    engine.mark_failed(evil, "</script><b>x</b>", kind="auth")
    engine.set_seed(a)
    engine.annotate_bundles()
    page = render(engine, tmp_path)
    assert "</script><script>alert(1)" not in page and "\x07" not in page
    data_block = page.split("window.LLDP_DETAIL=", 1)[1].split("</script>", 1)[0]
    assert "<img src=x" not in data_block and "</script" not in data_block
    evil_node = next(n for n in detail(page)["nodes"].values() if "alert(1)" in n["label"])
    assert (
        evil_node["description"] == "<img src=x onerror=alert(1)>"
    )  # intact, only escaped in the page


def test_metadata_is_escaped_in_the_legend(demo_engine, tmp_path):
    out = tmp_path / "x.html"
    TopologyVisualizer(out).render(demo_engine.graph, {"seed": "<b>x</b>"})
    assert "<b>x</b>" not in out.read_text(encoding="utf-8")


def test_empty_graph_is_an_error(tmp_path):
    with pytest.raises(VisualizationError, match="empty"):
        TopologyVisualizer(tmp_path / "x.html").render(GraphEngine(data_dir=tmp_path).graph)


def test_invalid_options_are_rejected():
    with pytest.raises(VisualizationError, match="layout"):
        VisualOptions.from_settings({"layout": "spiral"})
    from src.config import ConfigError

    with pytest.raises(ConfigError):
        VisualOptions.from_settings({"tiers": {"nope": ["x"]}})
    with pytest.raises(ConfigError):
        VisualOptions.from_settings({"node_types": {"toaster": ["x"]}})
    options = VisualOptions.from_settings(
        {"tiers": {"core": ["^bb"]}, "root": "bb1", "node_types": {"firewall": ["^gw"]}}
    )
    assert options.tier_rules.root == "bb1" and options.layout == "hierarchical"


def test_unwritable_output_is_a_clear_error(demo_engine, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(VisualizationError, match="Could not write"):
        TopologyVisualizer(blocker / "x.html").render(demo_engine.graph)


# --------------------------------------------- warnings, CDP-only links, the legend
def edge_between(edges, a, b):
    return next(e for e in edges if {e["from"], e["to"]} == {a, b})


def legend_of(page):
    return page.split('id="lldp-legend"', 1)[1].split('<aside id="lldp-detail"', 1)[0]


def test_links_with_findings_glow_and_are_listed(demo_engine, tmp_path):
    page = render(demo_engine, tmp_path)
    _, edges = vis_data(page)
    data = detail(page)
    glowing = {
        frozenset((e["from"], e["to"])): e["shadow"]["color"] for e in edges if "shadow" in e
    }
    assert glowing == {
        frozenset(("core1", "fw1")): "#E08A00",  # a Gigabit port running at 100M: degraded
        frozenset(("core1", "sw-legacy")): "#E08A00",
        frozenset(
            ("dist1", "acc2")
        ): "#D9480F",  # speed mismatch + bundle members at different speeds
        frozenset(("dist1", "acc3")): "#D9480F",  # duplex mismatch
    }
    assert len(data["warnings"]) == 5  # the acc2 bundle has two findings on two links
    texts = " | ".join(w["text"] for w in data["warnings"])
    assert "core1 Gi1/0/10 runs at 100M but the port is 1G" in texts
    assert "Duplex mismatch: dist1 Eth1/12 is full but acc3 1/1/1 is half" in texts
    assert "Port-channel members run at different speeds (100M, 1G)" in texts


def test_a_labelled_link_with_a_finding_makes_room_for_the_marker(demo_engine, tmp_path):
    _, edges = vis_data(render(demo_engine, tmp_path))
    acc2 = edge_between(edges, "dist1", "acc2")
    assert acc2["label"] == "LACP ×2" and acc2["font"]["vadjust"] < 0
    healthy = edge_between(edges, "core1", "core2")
    assert "shadow" not in healthy and "vadjust" not in healthy["font"]


def test_link_records_say_what_the_ports_run_at_and_how_it_was_seen(demo_engine, tmp_path):
    links = {
        frozenset((k["from"], k["to"])): k
        for k in detail(render(demo_engine, tmp_path))["links"].values()
    }
    acc1 = links[frozenset(("dist1", "acc1"))]
    assert acc1["speed"] == "2×10G" and acc1["speed_source"] == "operating" and acc1["warn"] == ""
    assert links[frozenset(("core1", "fw1"))]["speed"] == "100M"
    mixed = links[frozenset(("dist1", "acc2"))]
    assert (
        mixed["speed"] == "100M + 1G"
        and mixed["warn"] == "mismatch"
        and len(mixed["warnings"]) == 2
    )
    assert links[frozenset(("core1", "core2"))]["sources"] == ["cdp", "lldp"]
    assert links[frozenset(("dist1", "acc1"))]["sources"] == ["lldp"]
    assert links[frozenset(("core1", "sw-legacy"))]["sources"] == ["cdp"]
    assert all(k["checked"] for k in links.values() if frozenset(k["pairs"][0][:1]) != {""})


def test_the_operating_speed_colours_the_link_even_when_the_port_names_say_nothing(
    demo_engine, tmp_path
):
    _, edges = vis_data(render(demo_engine, tmp_path))
    assert edge_between(edges, "dist1", "acc1")["color"] == "#2F6FDE"  # Eth1/10 and Et1 run at 10G
    assert edge_between(edges, "core1", "fw1")["color"] == "#A8B3C2"  # Gi1/0/10 runs at 100M


def test_per_end_status_is_shown_next_to_each_interface(demo_engine, tmp_path):
    pairs = next(
        k
        for k in detail(render(demo_engine, tmp_path))["links"].values()
        if {k["from"], k["to"]} == {"core1", "core2"}
    )["pairs"]
    assert pairs == [["Gi1/0/47", "Gi1/0/47", "", "", "1G full", "1G full"]]
    tooltip = next(
        e
        for e in vis_data(render(demo_engine, tmp_path))[1]
        if {e["from"], e["to"]} == {"dist1", "acc3"}
    )
    assert "[1G half / 1G full]" in tooltip["title"] and "WARNING (mismatch)" in tooltip["title"]
    assert "Seen by: LLDP" in tooltip["title"]


def test_links_seen_only_by_cdp_are_dashed(demo_engine, tmp_path):
    _, edges = vis_data(render(demo_engine, tmp_path))
    dashed = {frozenset((e["from"], e["to"])) for e in edges if e.get("dashes")}
    assert dashed == {
        frozenset(("core1", "sw-legacy")),
        frozenset(("sw-legacy", "sep001122334455")),
    }
    assert "Seen by: CDP" in edge_between(edges, "core1", "sw-legacy")["title"]
    assert "Seen by: CDP + LLDP" in edge_between(edges, "core1", "core2")["title"]


def test_devices_know_how_they_were_discovered(demo_engine, tmp_path):
    nodes = detail(render(demo_engine, tmp_path))["nodes"]
    assert nodes["core1"]["discovery"] == ["cdp", "lldp"] and nodes["acc1"]["discovery"] == ["lldp"]
    legacy = nodes["sw-legacy"]
    assert legacy["discovery"] == ["cdp"] and "no LLDP capture" in legacy["protocol_notes"]["lldp"]
    assert legacy["platform"] == "cisco WS-C2960G-24TC-L" and legacy["kind"] == "switch"
    phone = nodes["sep001122334455"]
    assert phone["kind"] == "phone" and phone["sub"] == "10.0.0.131 · Cisco IP Phone 7960"
    assert nodes["core1"]["platform"] == "cisco C9300-48P"  # learned from its neighbors' CDP
    assert nodes["acc1"]["platform"] == ""  # LLDP carries no hardware platform, acc1 is in no CDP
    page = render(demo_engine, tmp_path)
    tooltip = next(n for n in vis_data(page)[0] if n["id"] == "sw-legacy")["title"]
    assert "Hardware: cisco WS-C2960G-24TC-L" in tooltip and "Discovery: CDP" in tooltip


def test_the_legend_has_a_clickable_item_for_every_group(demo_engine, tmp_path):
    legend = legend_of(render(demo_engine, tmp_path))
    keys = set(re.findall(r'data-filter="([^"]+)"', legend))
    assert {"status:crawled", "status:failed", "status:discovered", "seed"} <= keys
    assert {"kind:switch", "kind:firewall", "kind:phone", "kind:ap", "kind:router"} <= keys
    assert {
        "tier:Edge",
        "tier:Core",
        "tier:Distribution",
        "tier:Access",
        "tier:End devices",
    } <= keys
    assert {
        "speed:100M",
        "speed:1G",
        "speed:10G",
        "bundle",
        "src:cdp",
        "warn:degraded",
        "warn:mismatch",
    } <= keys
    assert "Degraded (2)" in legend and "Mismatch (2)" in legend
    assert "4 of 11 links checked" not in legend and "of 11 links checked" in legend


def test_the_legend_collapses_and_its_buttons_are_accessible(demo_engine, tmp_path):
    page = render(demo_engine, tmp_path)
    legend = legend_of(page)
    assert 'id="lldp-legend-toggle"' in legend and 'aria-expanded="true"' in legend
    assert 'aria-controls="lldp-legend-body"' in legend and 'id="lldp-legend-body"' in legend
    assert legend.count('class="lg-item"') == legend.count('aria-pressed="false"')
    assert "applyFilter" in page and "focusEdge" in page and "drawWarnMarkers" in page


def test_the_hierarchy_rows_are_left_out_of_the_legend_in_physics_layout(demo_engine, tmp_path):
    assert "tier:" not in legend_of(render(demo_engine, tmp_path, layout="physics"))


def test_without_interface_data_the_legend_says_links_were_not_checked(tmp_path):
    engine = two_devices(tmp_path, [("Gi1", "Gi1")])
    page = render(engine, tmp_path)
    assert "No interface speed/duplex data, so links were not checked." in legend_of(page)
    assert "warn:" not in legend_of(page) and detail(page)["warnings"] == []
    (link,) = detail(page)["links"].values()
    assert link["checked"] is False and link["speed_source"] == "nominal" and link["speed"] == "1G"


def test_a_healthy_checked_network_says_so(tmp_path):
    engine = two_devices(tmp_path, [("Gi1", "Gi1")])
    engine.set_link_status(
        "a", {"gi1": {"status": "up", "speed": 1000, "duplex": "full", "auto": True}}
    )
    engine.annotate_links()
    legend = legend_of(render(engine, tmp_path))
    assert "1 of 1 links checked: no problems found." in legend


def test_hostile_status_strings_cannot_break_out_of_the_page(tmp_path):
    engine = two_devices(tmp_path, [("Gi1</script>", "Gi1")])
    engine.set_link_status(
        "a", {"gi1</script>": {"status": "up", "speed": 100, "duplex": "half", "auto": None}}
    )
    engine.set_discovery("a", ["cdp"], {"lldp": "</script><script>alert(1)</script>"}, "<b>x</b>")
    engine.annotate_links()
    page = render(engine, tmp_path)
    assert "</script><script>alert(1)" not in page
    block = page.split("window.LLDP_DETAIL=", 1)[1].split("</script>", 1)[0]
    assert "</script" not in block and "<b>x</b>" not in block


def test_link_records_carry_their_label_so_the_page_can_fade_it_with_the_link(
    demo_engine, tmp_path
):
    links = {
        frozenset((k["from"], k["to"])): k
        for k in detail(render(demo_engine, tmp_path))["links"].values()
    }
    assert links[frozenset(("core1", "dist1"))]["label"] == "LACP ×2"
    assert links[frozenset(("core1", "core2"))]["label"] == ""
