"""Snapshot comparison, blast radius, CLI runbook and documentation spec of the explorer page.

The Python half renders the page with a previous snapshot and checks what the diff says. The JavaScript half runs the
page's pure engine (``SIM_ENGINE_JS`` + ``OPS_ENGINE_JS``, no DOM) under Node on the data the page embeds.
"""

import json
import shutil
import subprocess

import pytest

from src.diagram_assets import OPS_ENGINE_JS, OPS_UI_JS, SIM_ENGINE_JS
from src.visualizer import (
    TopologyVisualizer,
    VisualOptions,
    graph_from_snapshot,
    load_snapshot,
)

from .conftest import make_campus
from .test_visualizer import detail, render, vis_data

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node.js is needed to run the page script")


def previous_run(engine):
    """The campus as it was one run ago: no ap2 yet, acc4 had one cable, and ``oldsw`` still existed."""
    before = graph_from_snapshot(engine.to_dict({"saved_at": "2026-10-21T08:00:00+00:00"}))
    before.remove_node("ap2")
    for u, v, key, data in list(before.edges(keys=True, data=True)):
        if {u, v} == {"acc4", "dist1"} and data["ports"].get("acc4") == "Gi1/0/2":
            before.remove_edge(u, v, key)
            break
    before.add_node(
        "oldsw",
        status="crawled",
        label="oldsw",
        port_channels=[],
        seed=False,
        capabilities=["bridge"],
        address="10.0.9.9",
    )
    before.add_edge(
        "oldsw",
        "core2",
        ports={"oldsw": "Gi0/1", "core2": "Gi1/0/20"},
        bundles={},
        sources={"lldp"},
        oper={},
        warnings=[],
    )
    return before


def render_with(engine, previous, tmp_path):
    out = tmp_path / "out" / "diff.html"
    TopologyVisualizer(out, VisualOptions()).render(
        engine.graph, {"seed": "10.0.0.1"}, previous=previous
    )
    return out.read_text(encoding="utf-8")


# ------------------------------------------------------------------------------ snapshots
def test_a_snapshot_rebuilds_the_graph_the_page_is_drawn_from(tmp_path):
    engine = make_campus(tmp_path)
    again = graph_from_snapshot(engine.to_dict({"saved_at": "x"}))
    live, rebuilt = render(engine, tmp_path), None
    out = tmp_path / "again.html"
    TopologyVisualizer(out, VisualOptions()).render(again, {"seed": "10.0.0.1"})
    rebuilt = out.read_text(encoding="utf-8")
    assert detail(rebuilt) == detail(live) and vis_data(rebuilt) == vis_data(live)


def test_a_missing_or_broken_snapshot_is_none(tmp_path):
    assert load_snapshot(tmp_path / "nope.json") is None
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert load_snapshot(tmp_path / "bad.json") is None
    (tmp_path / "odd.json").write_text("[1, 2]", encoding="utf-8")
    assert load_snapshot(tmp_path / "odd.json") is None
    engine = make_campus(tmp_path)
    engine.save({"seed": "x"})
    assert load_snapshot(engine.current_path)["nodes"]


def test_without_a_previous_snapshot_the_page_has_no_diff(tmp_path):
    data = detail(render(make_campus(tmp_path), tmp_path))
    assert data["diff"] == {"baseline": False}
    assert not any(n.get("ghost") for n in data["nodes"].values())
    assert not any(k.get("ghost") or k.get("diff") for k in data["links"].values())


def test_the_diff_names_what_was_added_removed_and_changed(tmp_path):
    engine = make_campus(tmp_path)
    page = render_with(engine, previous_run(engine), tmp_path)
    data, (nodes, edges) = detail(page), vis_data(page)
    assert data["diff"]["baseline"] is True
    assert {k: v["status"] for k, v in data["diff"]["nodes"].items()} == {
        "ap2": "added",
        "oldsw": "removed",
    }
    assert data["diff"]["summary"] == {
        "nodes_added": 1,
        "nodes_removed": 1,
        "nodes_changed": 0,
        "links_added": 2,
        "links_removed": 1,
        "links_changed": 0,
    }
    by_status = {}
    for _eid, link in data["links"].items():
        if link.get("diff"):
            by_status.setdefault(link["diff"]["status"], []).append((link["from"], link["to"]))
    assert (
        by_status["removed"] == [("core2", "oldsw")]
        and ("acc2", "ap2") in by_status["added"]
        and ("acc4", "dist1") in by_status["added"]
    )
    # what is gone is drawn (the page hides it in the current view), flagged, and starts hidden
    assert (
        data["nodes"]["oldsw"]["ghost"] is True
        and next(n for n in nodes if n["id"] == "oldsw")["hidden"] is True
    )
    ghost_edges = [
        e for e in edges if e.get("hidden") and not e["id"].startswith("ge:")
    ]  # (collapsed endpoint groups hide theirs too)
    assert len(ghost_edges) == 1 and {ghost_edges[0]["from"], ghost_edges[0]["to"]} == {
        "core2",
        "oldsw",
    }
    assert "ghost" not in data["nodes"]["ap2"]


def test_a_changed_port_channel_is_a_changed_link(tmp_path):
    from .test_visualizer import channel, two_devices

    now = two_devices(
        tmp_path,
        [("Gi1", "Gi1"), ("Gi2", "Gi2")],
        [channel("Po1", "lacp", ["Gi1", "Gi2"])],
        [channel("Po1", "lacp", ["Gi1", "Gi2"])],
    )
    before = graph_from_snapshot(now.to_dict({}))
    for u, v, key, data in list(before.edges(keys=True, data=True)):
        if data["ports"].get("a") == "Gi2":
            before.remove_edge(u, v, key)
    page = render_with(now, before, tmp_path)
    (link,) = [k for k in detail(page)["links"].values() if k.get("diff")]
    assert link["diff"]["status"] == "changed" and "1 \u2192 2" in link["diff"]["detail"]


def test_what_is_gone_is_not_counted_in_the_legend_or_the_problems(tmp_path):
    engine = make_campus(tmp_path)
    plain = render(engine, tmp_path)
    page = render_with(engine, previous_run(engine), tmp_path)
    for needle in ("Crawled (8)", "Neighbor only (11)"):
        assert needle in plain and needle in page
    assert detail(page)["problems"] == detail(plain)["problems"]


def test_the_page_has_the_shell_the_design_asks_for(tmp_path):
    page = render(make_campus(tmp_path), tmp_path)
    for needle in (
        'id="top"',
        "LLDP TOPOLOGY EXPLORER",
        'id="b-search"',
        'id="b-filter"',
        'id="b-sim"',
        'id="b-side"',
        'id="b-theme"',
        'id="b-full"',
        'id="b-more"',
        'id="side"',
        'id="metrics"',
        'id="insp-empty"',
        "Click any device or cable to inspect port status, metrics, and neighbor adjacencies, or press",
        'id="m-docs"',
        "Export Documentation Bundle",
        'id="m-docsvg"',
        'id="m-docmd"',
        'id="minimap"',
        'width="160" height="100"',
        'id="diff-bar"',
        'id="t-current"',
        'id="t-previous"',
        'id="t-overlay"',
        "[ / ]</span> Search",
        "[ Esc ]</span> Deselect",
    ):
        assert needle in page, needle
    assert 'data-theme="dark"' in page and "--accent:#3b82f6" in page and "--accent:#2563eb" in page
    assert "backdrop-filter:blur(12px) saturate(160%)" in page and "-webkit-backdrop-filter" in page
    assert "font-variant-numeric:tabular-nums" in page and '"JetBrains Mono"' in page
    script = page.split('id="lldp-detail"', 1)[1]
    assert "innerHTML" not in script  # device text only ever goes in via textContent


# ------------------------------------------------------------------------------ the page scripts
@needs_node
@pytest.mark.parametrize("script", [OPS_ENGINE_JS, OPS_UI_JS], ids=["ops-engine", "ops-ui"])
def test_the_new_page_scripts_are_valid_javascript(script, tmp_path):
    path = tmp_path / "script.js"
    path.write_text(script, encoding="utf-8")
    done = subprocess.run([NODE, "--check", str(path)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


HARNESS = """
const vm = require('vm');
const context = vm.createContext({});
vm.runInContext(%s, context);
vm.runInContext(%s, context);
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const snippet = new Function('Sim', 'Ops', 'input', %s);
process.stdout.write(JSON.stringify(snippet(context.LLDPSim, context.LLDPOps, input)));
"""


def run_js(body, payload):
    done = subprocess.run(
        [
            NODE,
            "-e",
            HARNESS % (json.dumps(SIM_ENGINE_JS), json.dumps(OPS_ENGINE_JS), json.dumps(body)),
        ],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


BLAST = """
const model = Sim.build(input.nodes, input.links);
return input.cases.map((c) => {
  const b = Ops.blast(model, new Set(c.nodes || []), new Set(c.links || []));
  return { active: b.active, failed: b.failed, isolated: b.isolated, spof: b.spof, degraded: b.degraded, rerouted: b.rerouted,
           delta: b.delta, uplinks: b.uplinks, newLinks: b.newLinks, lostLinks: b.lostLinks, state: b.state };
});
"""


def blast(data, *cases):
    return run_js(BLAST, {"nodes": data["nodes"], "links": data["links"], "cases": list(cases)})


@pytest.fixture(scope="module")
def campus(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("campus-ops")
    return detail(render(make_campus(tmp), tmp))


def ring():
    """core(root) - a - b - c - d, plus a shortcut core - d, and x with two cables to a and two to b."""

    def node(rank, **kw):
        return {
            "rank": rank,
            "leaf": "",
            "core": rank == 1,
            "spof_ok": True,
            "kind": "switch",
            **kw,
        }

    nodes = {"core": node(1), "a": node(2), "b": node(2), "c": node(2), "d": node(2), "x": node(3)}

    def link(a, b, n=1):
        return {"from": a, "to": b, "pairs": [["p", "q"]] * n, "bundle": None}

    links = {
        "e0": link("core", "a"),
        "e1": link("a", "b"),
        "e2": link("b", "c"),
        "e3": link("c", "d"),
        "e4": link("core", "d"),
        "e5": link("a", "x", 2),
        "e6": link("b", "x", 2),
    }
    return {"nodes": nodes, "links": links}


@needs_node
def test_no_failure_is_no_blast_radius(campus):
    (b,) = blast(campus, {})
    assert b["active"] is False and not (
        b["isolated"] or b["spof"] or b["rerouted"] or b["newLinks"]
    )


@needs_node
def test_red_isolated_and_amber_lost_redundancy_of_a_failed_switch(campus):
    (b,) = blast(campus, {"nodes": ["dist1"]})
    assert b["failed"] == ["dist1"]
    assert b["isolated"] == ["acc2", "acc4", "ap0", "ap1", "ap2"]
    assert b["spof"] == ["acc1"] and b["uplinks"]["acc1"] == [
        2,
        1,
    ]  # acc3, srv1 and the building-B switch were single-homed already
    assert (
        b["state"]["dist1"] == "failed"
        and b["state"]["acc2"] == "isolated"
        and b["state"]["acc1"] == "spof"
    )


@needs_node
def test_a_failed_core_switch_strands_its_single_homed_neighbour(campus):
    (b,) = blast(campus, {"nodes": ["core1"]})
    assert b["isolated"] == ["sw-acc-bldg-b"] and b["spof"] == ["dist1", "dist2"]


@needs_node
def test_blue_rerouted_devices_pay_more_hops_and_the_new_path_is_listed():
    data = ring()
    (b,) = blast(data, {"links": ["e0"]})  # core - a is cut: a now goes round by d, c, b
    assert b["isolated"] == []
    assert b["delta"]["a"] == 3 and "a" in b["rerouted"]  # 1 hop became 4: core - d - c - b - a
    assert {"e2", "e6"} <= set(
        b["newLinks"]
    )  # c - b now carries a's and x's traffic, and b - x takes over from a - x
    assert (
        "e5" in b["lostLinks"] and "e0" not in b["lostLinks"]
    )  # a - x is no longer on a shortest path; the cut link is not listed as "lost"


@needs_node
def test_fewer_uplinks_that_are_still_two_or_more_are_degraded_not_single_homed():
    data = ring()
    (b,) = blast(data, {"links": ["e5"]})  # x had 2 + 2 cables up (a and b), now 2
    assert b["uplinks"]["x"] == [4, 2] and b["degraded"] == ["x"] and b["spof"] == []


@needs_node
def test_what_was_removed_since_the_previous_run_takes_no_part_in_the_simulation(tmp_path):
    engine = make_campus(tmp_path)
    data = detail(render_with(engine, previous_run(engine), tmp_path))
    out = run_js(
        "const m = Sim.build(input.nodes, input.links); return {nodes: Object.keys(m.nodes), edges: m.edges.length};",
        data,
    )
    assert "oldsw" not in out["nodes"] and "ap2" in out["nodes"]
    assert out["edges"] == len([k for k in data["links"].values() if not k.get("ghost")])


# ------------------------------------------------------------------------------ CLI runbook
RUNBOOK = "return input.map((c) => Ops.runbook(c.choice, c.ctx));"
VENDOR_OF = "return input.map((n) => Ops.vendorOf(n));"


@needs_node
def test_the_platform_is_detected_from_the_device():
    nodes = [
        {"os": "IOS", "device_type": "cisco_ios"},
        {"os": "NX-OS"},
        {"device_type": "arista_eos"},
        {"os": "Junos"},
        {"platform": "VyOS 1.4"},
        {"device_type": "huawei_vrp"},
        {"os": "ArubaOS-CX"},
        {},
    ]
    assert run_js(VENDOR_OF, nodes) == [
        "cisco_ios",
        "cisco_nxos",
        "arista_eos",
        "juniper_junos",
        "vyos",
        "huawei",
        None,
        None,
    ]


@needs_node
def test_a_link_runbook_has_one_block_per_end_in_that_devices_own_syntax():
    link = {
        "kind": "link",
        "bundled": False,
        "ends": [
            {"label": "acc1", "vendor": "cisco_ios", "iface": "Gi1/0/1"},
            {"label": "srv", "vendor": "juniper_junos", "iface": "ge-0/0/3"},
        ],
    }
    (blocks,) = run_js(RUNBOOK, [{"choice": "auto", "ctx": link}])
    assert [b["cmds"] for b in blocks] == [
        ["show lldp neighbors Gi1/0/1 detail", "show interfaces Gi1/0/1"],
        ["show lldp neighbors interface ge-0/0/3", "show interfaces ge-0/0/3 extensive"],
    ]
    (forced,) = run_js(RUNBOOK, [{"choice": "arista_eos", "ctx": link}])
    assert (
        all(b["vendor"] == "arista_eos" for b in forced)
        and forced[0]["cmds"][0] == "show lldp neighbors Gi1/0/1 detail"
    )


@needs_node
def test_a_bundle_adds_the_port_channel_commands_of_each_platform():
    ends = [{"label": "d1", "iface": "Eth1", "bundle": "Port-channel7"}]
    out = run_js(
        RUNBOOK,
        [
            {"choice": v, "ctx": {"kind": "link", "bundled": True, "ends": ends}}
            for v in ("cisco_ios", "arista_eos", "juniper_junos", "huawei")
        ],
    )
    bundle_cmds = [blocks[1]["cmds"] for blocks in out]
    assert bundle_cmds == [
        ["show etherchannel summary", "show interfaces Port-channel7"],
        ["show port-channel summary", "show lacp interface Eth1"],
        ["show lacp interfaces", "show lacp interfaces Port-channel7"],
        ["display eth-trunk", "display eth-trunk 7"],
    ]


@needs_node
def test_a_device_runbook_and_an_unknown_interface_placeholder():
    (node_blocks, link_blocks) = run_js(
        RUNBOOK,
        [
            {
                "choice": "cisco_nxos",
                "ctx": {"kind": "node", "label": "core1", "vendor": None, "hasBundles": True},
            },
            {
                "choice": "cisco_ios",
                "ctx": {"kind": "link", "bundled": False, "ends": [{"label": "x", "iface": ""}]},
            },
        ],
    )
    assert [b["cmds"] for b in node_blocks] == [
        ["show lldp neighbors detail"],
        ["show interface status"],
        ["show port-channel summary"],
    ]
    assert link_blocks[0]["cmds"][0] == "show lldp neighbors <intf> detail"


# ------------------------------------------------------------------------------ TOPOLOGY_SPEC.md
SPEC = "return Ops.buildSpec(input.data, {time: input.time});"


@needs_node
def test_the_spec_has_the_device_table_and_the_link_matrix(campus):
    md = run_js(SPEC, {"data": campus, "time": "current"})
    assert "| Hostname | IP | Role / Tier | Vendor | Software version |" in md
    assert "| core1 | 10.0.0.1 | Core \u00b7 switch | Cisco IOS | IOS |" in md
    assert (
        "| Local device | Local interface | Remote device | Remote interface | Speed | Redundancy |"
        in md
    )
    assert "Change" not in md.split("## Link interconnect matrix")[1]  # no snapshot to compare with
    rows = [
        r for r in md.split("## Link interconnect matrix")[1].splitlines() if r.startswith("| ")
    ][2:]
    assert len(rows) == sum(len(k["pairs"]) for k in campus["links"].values())
    assert any("Single link (SPOF)" in r for r in rows) and any(
        "Redundant (alternate path exists)" in r for r in rows
    )


@needs_node
def test_parallel_cables_and_port_channels_are_worded_as_redundant():
    data = ring()
    for i, (nid, n) in enumerate(data["nodes"].items()):
        n.update(
            label=nid,
            address=f"10.0.0.{i}",
            mgmt_ips=[],
            tier=f"T{n['rank']}",
            os="IOS",
            description="Cisco IOS Software, Version 15.2(7)E3",
        )
    for k in data["links"].values():
        k.update(a=k["from"], b=k["to"], speed="1G", warn="")
    data["links"]["e0"]["bundle"] = {"protocol": "lacp", "links": 2, "total": 3, "up": True}
    md = run_js(
        SPEC, {"data": {**data, "diff": {}, "meta": {"generated": "now"}}, "time": "current"}
    )
    text = "\n".join(md.splitlines())
    assert "Parallel \u00d72 (redundant)" in text  # a - x and b - x: two cables each
    assert "LAG lacp: 2/3 members, redundant" in text  # the bundle on core - a
    assert "Single link (SPOF)" not in text  # the ring and the double cables leave no single point
    assert "| a | 10.0.0.1 | T2 \u00b7 switch | Cisco IOS | IOS 15.2(7)E3 |" in text


@needs_node
def test_the_spec_follows_the_snapshot(tmp_path):
    engine = make_campus(tmp_path)
    data = detail(render_with(engine, previous_run(engine), tmp_path))
    now = run_js(SPEC, {"data": data, "time": "current"})
    was = run_js(SPEC, {"data": data, "time": "previous"})
    assert "| oldsw |" not in now and "| ap2 |" in now
    assert "| oldsw |" in was and "| ap2 |" not in was
    assert (
        "## Changes since the previous snapshot" in now
        and "Added devices: ap2" in now
        and "Removed devices: oldsw" in now
    )
    assert "| Change |" in now.split("## Link interconnect matrix")[1]
