"""The failure simulation runs in the browser; these tests run the same script under Node.

``SIM_ENGINE_JS`` is plain JavaScript with no DOM, so it is loaded into a bare ``vm`` context and
fed the exact data the page embeds (``window.LLDP_DETAIL``). Results are checked against
scenarios worked out by hand and against an independent model built with networkx.
"""

import json
import random
import shutil
import subprocess

import networkx as nx
import pytest

from src.crawler import Crawler
from src.diagram_assets import PAGE_JS, SIM_ENGINE_JS, SIM_UI_JS
from src.graph_engine import GraphEngine
from src.parser import LldpParser
from src.poller import FilePoller

from .conftest import make_campus, make_config
from .test_visualizer import detail, render

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node.js is needed to run the page script")

HARNESS = """
const vm = require('vm');
const context = vm.createContext({});
vm.runInContext(%s, context);
const cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const results = cases.map((c) => {
  const model = context.LLDPSim.build(c.nodes, c.links);
  const r = context.LLDPSim.evaluate(model, new Set(c.failedNodes), new Set(c.failedLinks));
  return { roots: model.roots.slice().sort(), failed: r.failed, isolated: r.isolated,
           active: r.active, spof: r.spof };
});
process.stdout.write(JSON.stringify(results));
"""


def run_engine(cases):
    """Evaluate ``cases`` [{nodes, links, failedNodes, failedLinks}] in the page's engine."""
    done = subprocess.run(
        [NODE, "-e", HARNESS % json.dumps(SIM_ENGINE_JS)],
        input=json.dumps(cases),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def evaluate(data, nodes=(), links=()):
    case = {
        "nodes": data["nodes"],
        "links": data["links"],
        "failedNodes": list(nodes),
        "failedLinks": list(links),
    }
    return run_engine([case])[0]


def links_between(data, a, b):
    return sorted(eid for eid, k in data["links"].items() if {k["from"], k["to"]} == {a, b})


@pytest.fixture
def demo_engine(demo_dir, tmp_path):
    engine = GraphEngine(data_dir=tmp_path)
    Crawler(
        FilePoller(demo_dir), LldpParser(), engine, make_config(), "10.0.0.1", "cisco_ios"
    ).run()
    return engine


@pytest.fixture(scope="module")
def campus(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("campus")
    return detail(render(make_campus(tmp), tmp))


# ---------------------------------------------------------------- the page scripts parse
@needs_node
@pytest.mark.parametrize(
    "script", [PAGE_JS, SIM_ENGINE_JS, SIM_UI_JS], ids=["page", "engine", "ui"]
)
def test_the_page_scripts_are_valid_javascript(script, tmp_path):
    path = tmp_path / "script.js"
    path.write_text(script, encoding="utf-8")
    done = subprocess.run([NODE, "--check", str(path)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


# ---------------------------------------------------------------------- the healthy network
@needs_node
def test_nothing_is_cut_nothing_is_isolated_and_the_single_uplinks_are_the_spofs(campus):
    result = evaluate(campus)
    assert result["roots"] == ["core1", "core2"]
    assert result["failed"] == [] and result["isolated"] == []
    # acc2 / acc3 / the building-B switch / srv1 each have one cable towards a higher tier.
    # Not flagged: acc1 and srv2 (two uplinks), acc4 (two cables), dist1/dist2 (two cores),
    # phones and access points (single-homed by nature), the Core itself.
    assert result["spof"] == ["acc2", "acc3", "srv1", "sw-acc-bldg-b"]


# ------------------------------------------------------------------- blast radius, by hand
@needs_node
def test_a_failed_distribution_switch_isolates_what_hangs_only_off_it(campus):
    result = evaluate(campus, nodes=["dist1"])
    assert result["failed"] == ["dist1"]
    # acc2 (single-homed) and acc4 (both cables) lose the Core, and so do acc2's access points
    assert result["isolated"] == ["acc2", "acc4", "ap0", "ap1", "ap2"]
    # the survivors that are now on a single uplink
    assert result["spof"] == ["acc1", "acc3", "srv1", "sw-acc-bldg-b"]


@needs_node
def test_a_cable_cut_isolates_the_single_homed_switch(campus):
    (cable,) = links_between(campus, "dist2", "acc3")
    result = evaluate(campus, links=[cable])
    assert result["failed"] == []
    assert result["isolated"] == ["acc3"]


@needs_node
def test_a_server_wired_to_two_switches_is_not_a_path_between_them(campus):
    # acc3 still touches srv2, which is wired to acc1: that must not keep acc3 "connected"
    (cable,) = links_between(campus, "dist2", "acc3")
    result = evaluate(campus, links=[cable])
    assert "acc3" in result["isolated"] and "acc1" in result["active"]
    assert "srv2" in result["active"]  # the host itself is still reachable through acc1
    assert "srv2" in result["spof"]  # ... on a single working uplink now


@needs_node
def test_one_of_two_parallel_cables_keeps_the_switch_up_but_makes_it_a_spof(campus):
    first, second = links_between(campus, "dist1", "acc4")
    one = evaluate(campus, links=[first])
    assert "acc4" in one["active"] and "acc4" in one["spof"]
    both = evaluate(campus, links=[first, second])
    assert "acc4" in both["isolated"]


@needs_node
def test_failing_one_core_device_leaves_the_other_carrying_the_network(campus):
    result = evaluate(campus, nodes=["core1"])
    assert result["isolated"] == ["sw-acc-bldg-b"]  # its only uplink was core1
    assert result["spof"] == ["acc2", "acc3", "dist1", "dist2", "srv1"]


@needs_node
def test_with_every_core_device_down_everything_else_is_isolated(campus):
    result = evaluate(campus, nodes=["core1", "core2"])
    assert result["failed"] == ["core1", "core2"]
    assert len(result["isolated"]) == len(campus["nodes"]) - 2
    assert result["active"] == [] and result["spof"] == []


@needs_node
def test_losing_both_distribution_switches_strands_every_access_switch_and_its_hosts(campus):
    result = evaluate(campus, nodes=["dist1", "dist2"])
    stranded = {"acc1", "acc2", "acc3", "acc4", "srv1", "srv2", "ap0", "ap1", "ap2"}
    stranded |= {f"sep00{i}" for i in range(5)}
    assert set(result["isolated"]) == stranded
    assert "sw-acc-bldg-b" in result["active"]


@needs_node
def test_a_failed_end_device_isolates_nobody(campus):
    result = evaluate(campus, nodes=["sep000"])
    assert result["failed"] == ["sep000"] and result["isolated"] == []


@needs_node
def test_a_cable_id_the_page_does_not_know_is_ignored(campus):
    assert evaluate(campus, links=["e9999"])["isolated"] == []


@needs_node
def test_restoring_everything_gives_the_healthy_result_back(campus):
    assert evaluate(campus, nodes=[], links=[]) == evaluate(campus)


# ------------------------------------------------------------ small hand-built topologies
def node(rank, core=False, leaf="", spof_ok=True):
    return {"rank": rank, "core": core, "leaf": leaf, "spof_ok": spof_ok}


def link(a, b, cables=1):
    return {"from": a, "to": b, "pairs": [["p", "q", "", "", "", ""]] * cables}


@needs_node
def test_a_port_channel_of_two_cables_is_not_a_single_uplink():
    nodes = {"core": node(1, core=True), "a": node(3), "b": node(3)}
    links = {"e0": link("core", "a", cables=2), "e1": link("core", "b", cables=1)}
    result = run_engine([{"nodes": nodes, "links": links, "failedNodes": [], "failedLinks": []}])[0]
    assert result["spof"] == ["b"]


@needs_node
def test_without_a_core_tier_the_devices_with_nothing_above_them_are_the_roots():
    nodes = {"d1": node(2), "d2": node(2), "a1": node(3), "a2": node(3)}
    links = {
        "e0": link("d1", "a1"),
        "e1": link("d2", "a1"),
        "e2": link("d1", "a2"),
        "e3": link("d1", "d2"),
    }
    case = {"nodes": nodes, "links": links, "failedNodes": [], "failedLinks": []}
    healthy, d1_down = run_engine([case, {**case, "failedNodes": ["d1"]}])
    assert healthy["roots"] == ["d1", "d2"]
    assert healthy["spof"] == ["a2"]
    assert d1_down["roots"] == ["d1", "d2"]  # roots are decided on the healthy network
    assert d1_down["failed"] == ["d1"]
    assert d1_down["isolated"] == ["a2"]  # a1 still reaches the other root, d2; a2 hung off d1


@needs_node
def test_a_lone_top_device_failing_isolates_the_rest():
    nodes = {"top": node(1, core=True), "a": node(3), "b": node(3)}
    links = {"e0": link("top", "a"), "e1": link("a", "b")}
    case = {"nodes": nodes, "links": links, "failedNodes": ["top"], "failedLinks": []}
    result = run_engine([case])[0]
    assert result["isolated"] == ["a", "b"] and result["active"] == []


# ----------------------------------------------- against an independent model, many networks
def oracle(nodes, links, failed_nodes, failed_links):
    """The same question answered with networkx: directed arcs instead of a queue loop.

    A cable is two arcs, except that no arc leaves an end device (it can be reached, never
    crossed), so ``descendants`` of the live Core devices is exactly the set that still has a
    path to the Core.
    """
    roots = [n for n, v in nodes.items() if v["core"]]
    if not roots:
        has_up = set()
        for k in links.values():
            low, high = sorted((k["from"], k["to"]), key=lambda n: nodes[n]["rank"])
            if nodes[low]["rank"] < nodes[high]["rank"]:
                has_up.add(high)
        roots = [n for n in nodes if n not in has_up]
    live = {
        eid: k
        for eid, k in links.items()
        if eid not in failed_links and k["from"] not in failed_nodes and k["to"] not in failed_nodes
    }
    arcs = nx.DiGraph()
    arcs.add_nodes_from(n for n in nodes if n not in failed_nodes)
    for k in live.values():
        for src, dst in ((k["from"], k["to"]), (k["to"], k["from"])):
            if not nodes[src]["leaf"]:
                arcs.add_edge(src, dst)
    reached = set()
    for root in roots:
        if root in arcs:
            reached |= {root} | nx.descendants(arcs, root)
    uplinks = dict.fromkeys(nodes, 0)
    for k in live.values():
        a, b = k["from"], k["to"]
        if a in reached and b in reached:
            cables = len(k["pairs"])
            if nodes[a]["rank"] < nodes[b]["rank"]:
                uplinks[b] += cables
            elif nodes[b]["rank"] < nodes[a]["rank"]:
                uplinks[a] += cables
    return {
        "failed": sorted(failed_nodes & set(nodes)),
        "isolated": sorted(n for n in nodes if n not in failed_nodes and n not in reached),
        "spof": sorted(
            n for n in reached if nodes[n]["spof_ok"] and n not in roots and uplinks[n] == 1
        ),
    }


def random_case(rng):
    count = rng.randint(4, 14)
    nodes = {}
    for i in range(count):
        rank = rng.randint(0, 4)
        leaf = rng.choice(["phone", "ap", "server", "generic"]) if rank == 4 else ""
        nodes[f"n{i}"] = {
            "rank": rank,
            "core": rank == 1 and rng.random() < 0.7,
            "leaf": leaf,
            "spof_ok": leaf in ("", "server"),
        }
    names = list(nodes)
    links = {}
    for i in range(rng.randint(count - 1, count * 2)):
        a, b = rng.sample(names, 2)
        links[f"e{i}"] = link(a, b, cables=rng.randint(1, 3))
    return {
        "nodes": nodes,
        "links": links,
        "failedNodes": rng.sample(names, rng.randint(0, 3)),
        "failedLinks": rng.sample(list(links), rng.randint(0, min(3, len(links)))),
    }


@needs_node
def test_the_page_engine_agrees_with_an_independent_model_on_random_networks():
    rng = random.Random(20261018)
    cases = [random_case(rng) for _ in range(300)]
    for case, got in zip(cases, run_engine(cases), strict=True):
        expected = oracle(
            case["nodes"], case["links"], set(case["failedNodes"]), set(case["failedLinks"])
        )
        assert {k: got[k] for k in ("failed", "isolated", "spof")} == expected, case


@needs_node
def test_every_single_device_failure_of_the_bundled_demo_matches_the_independent_model(
    demo_engine, tmp_path
):
    data = detail(render(demo_engine, tmp_path))
    cases = [
        {"nodes": data["nodes"], "links": data["links"], "failedNodes": [n], "failedLinks": []}
        for n in data["nodes"]
    ] + [
        {"nodes": data["nodes"], "links": data["links"], "failedNodes": [], "failedLinks": [e]}
        for e in data["links"]
    ]
    for case, got in zip(cases, run_engine(cases), strict=True):
        expected = oracle(
            case["nodes"], case["links"], set(case["failedNodes"]), set(case["failedLinks"])
        )
        assert {k: got[k] for k in ("failed", "isolated", "spof")} == expected, case
