"""The history scrubber of the explorer page, run under Node.

``HISTORY_ENGINE_JS`` is plain JavaScript with no DOM, so its functions are called directly. The UI
script is run against a tiny DOM / fetch stub: it must stay silent when the page is opened from
disk, and when served it must fetch the history and hide what did not exist in the run picked.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from src.diagram_assets import HISTORY_CSS, HISTORY_ENGINE_JS, HISTORY_UI_JS
from src.report import describe_changes

from .conftest import make_campus
from .test_visualizer import detail, render

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node.js is needed to run the page script")

ENGINE_HARNESS = """
const vm = require('vm');
const context = vm.createContext({});
vm.runInContext(%s, context);
const cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const H = context.LLDPHistory;
const out = cases.map((c) => {
  if (c.fn === 'linkKey') return H.linkKey(...c.args);
  if (c.fn === 'presence') return H.presence(c.graph, c.nodes, c.links);
  if (c.fn === 'describe') return H.describe(c.diff);
  if (c.fn === 'lines') return H.lines(c.diff);
});
process.stdout.write(JSON.stringify(out));
"""

UI_HARNESS = """
const vm = require('vm');
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const made = [], calls = {fetch: [], nodes: [], edges: [], appended: 0, regroup: 0, setTime: null};
function el(tag) {
  return { tag, children: [], handlers: {}, attrs: {}, hidden: false, className: '', textContent: '',
           value: '', disabled: false, id: '',
           appendChild(c) { this.children.push(c); return c; },
           addEventListener(t, f) { this.handlers[t] = f; },
           setAttribute(k, v) { this.attrs[k] = v; } };
}
const win = {
  LLDP_DETAIL: input.data, LLDP_TIMEHIDE: { nodes: {}, edges: {} }, location: { protocol: input.protocol },
  LLDP_REGROUP() { calls.regroup++; }, LLDP_RESTYLE() {}, LLDP_SET_TIME(m) { calls.setTime = m; },
};
const network = { body: { data: { nodes: { update(l) { calls.nodes.push(l); } },
                                  edges: { update(l) { calls.edges.push(l); } } } }, redraw() {} };
function fetchStub(path) {
  calls.fetch.push(path);
  const body = input.responses[path];
  const reply = body === undefined ? { status: 'error', error: 'no such run' } : { status: 'ok', data: body };
  return Promise.resolve({ ok: body !== undefined, json: () => Promise.resolve(reply) });
}
const ctx = vm.createContext({
  window: win, location: win.location, network, fetch: fetchStub,
  document: { createElement(t) { const e = el(t); made.push(e); return e; },
              body: { appendChild() { calls.appended++; } } },
  setTimeout, clearTimeout, setInterval() {}, console,
});
vm.runInContext(input.engine, ctx);
vm.runInContext(input.ui, ctx);
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  await wait(30);
  const bar = made.find((e) => e.id === 'hist-bar');
  const slider = made.find((e) => e.tag === 'input');
  const state = { barHidden: bar ? bar.hidden : null };
  if (bar) {
    state.label = made.find((e) => e.className === 'hb-label').textContent;
    state.sliderMax = slider.max;
    if (input.pick != null) {
      slider.value = String(input.pick);
      slider.handlers.input();
      await wait(300);
      state.afterPick = made.find((e) => e.className === 'hb-sum').textContent;
    }
  }
  process.stdout.write(JSON.stringify({ calls, state, hidden: win.LLDP_TIMEHIDE }));
})();
"""


def run_node(script, payload):
    done = subprocess.run(
        [NODE, "-e", script], input=json.dumps(payload), capture_output=True, text=True, timeout=60
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def engine(cases):
    return run_node(ENGINE_HARNESS % json.dumps(HISTORY_ENGINE_JS), cases)


# ----------------------------------------------------------------- the engine
@needs_node
def test_link_key_ignores_end_order_and_port_case():
    keys = engine(
        [
            {"fn": "linkKey", "args": ["a", "Gi1", "b", "Gi2"]},
            {"fn": "linkKey", "args": ["b", "gi2", "a", "GI1"]},
            {"fn": "linkKey", "args": ["a", "Gi1", "b", "Gi3"]},
        ]
    )
    assert keys[0] == keys[1] and keys[0] != keys[2]


NODES = {"a": {}, "b": {}, "c": {}}
LINKS = {
    "e1": {"from": "a", "to": "b", "pairs": [["Gi1", "Gi1"], ["Gi2", "Gi2"]]},  # two cables
    "e2": {"from": "b", "to": "c", "pairs": [["Gi3", "Gi1"]]},
}


def graph(nodes, links):
    return {
        "nodes": [{"id": n, "label": n.upper()} for n in nodes],
        "links": [
            {"source": a, "source_port": pa, "target": b, "target_port": pb}
            for a, pa, b, pb in links
        ],
    }


@needs_node
def test_presence_tells_what_existed_in_a_recorded_run():
    everything = graph("abc", [("a", "Gi1", "b", "Gi1"), ("b", "Gi3", "c", "Gi1")])
    no_c = graph("ab", [("a", "Gi1", "b", "Gi1")])
    one_cable = graph("abc", [("a", "Gi2", "b", "Gi2"), ("b", "Gi3", "c", "Gi1")])
    moved = graph("abc", [("b", "Gi9", "a", "Gi1"), ("b", "Gi3", "c", "Gi1")])
    cases = [
        {"fn": "presence", "nodes": NODES, "links": LINKS, "graph": g}
        for g in (everything, no_c, one_cable, moved)
    ]
    full, without_c, partial, rewired = engine(cases)
    assert full["nodes"] == {"a": True, "b": True, "c": True}
    assert full["links"] == {"e1": True, "e2": True}
    assert without_c["nodes"]["c"] is False and without_c["links"] == {"e1": True, "e2": False}
    assert partial["links"]["e1"] is True  # one of the two cables of e1 is enough to draw it
    assert rewired["links"] == {"e1": False, "e2": True}  # the cable a Gi1 - b Gi1 was elsewhere then


@needs_node
def test_a_link_with_no_recorded_cable_is_hidden():
    only_nodes = graph("abc", [("a", "Gi7", "b", "Gi7")])
    (result,) = engine([{"fn": "presence", "nodes": NODES, "links": LINKS, "graph": only_nodes}])
    assert result["links"] == {"e1": False, "e2": False} and result["nodes"] == {"a": True, "b": True, "c": True}


@needs_node
def test_devices_the_page_does_not_draw_are_listed_as_missing():
    (result,) = engine(
        [{"fn": "presence", "nodes": {"a": {}}, "links": {}, "graph": graph("azb", [])}]
    )
    assert result["missing"] == ["B", "Z"] and result["nodes"] == {"a": True}


DIFFS = [
    {"first_run": True},
    {"first_run": False},
    {"first_run": False, "devices_added": 1, "devices_removed": 2, "links_added": 1, "links_removed": 3},
    {"first_run": False, "reconnections": 2, "speed_changes": 3, "speed_downgrades": 3},
    {"first_run": False, "speed_changes": 2, "speed_downgrades": 1, "new_blind_spots": 4},
]


@needs_node
def test_change_wording_matches_the_cli_listing():
    got = engine([{"fn": "describe", "diff": d} for d in DIFFS])
    # the page uses a real minus sign where the CLI types a hyphen; everything else is identical
    assert [g.replace("\u2212", "-") for g in got] == [describe_changes(d) for d in DIFFS]


@needs_node
def test_detail_lines_cover_every_kind_of_change():
    diff = {
        "devices_added": [{"id": "n", "label": "NEW"}],
        "devices_removed": [{"id": "o", "label": "OLD"}],
        "links_added": [{"a": "A", "a_port": "Gi1", "b": "B", "b_port": "Gi2"}],
        "links_removed": [{"a": "A", "a_port": "Gi3", "b": "C", "b_port": "Gi4"}],
        "reconnections": [{"device": "A", "port": "Gi1", "from_device": "B", "from_port": "Gi1",
                           "to_device": "C", "to_port": "Gi9"}],  # fmt: skip
        "speed_changes": [{"a": "A", "a_port": "Gi5", "b": "B", "b_port": "Gi5", "from": 1000,
                           "to": 100, "direction": "down"}],  # fmt: skip
        "new_blind_spots": [{"hostname": "x", "reason": "TIMEOUT", "discovered_via": "A"}],
    }
    (rows,) = engine([{"fn": "lines", "diff": diff}])
    text = "\n".join(rows)
    assert "+ device NEW" in text and "\u2212 device OLD (disappeared)" in text
    assert "+ link A Gi1 \u2194 B Gi2" in text and "\u2212 link A Gi3 \u2194 C Gi4" in text
    assert "cable moved on A Gi1: B Gi1 \u2192 C Gi9" in text
    assert "speed downgrade A Gi5 \u2194 B Gi5: 1G \u2192 100M" in text
    assert "new blind spot x (TIMEOUT) via A" in text
    assert engine([{"fn": "lines", "diff": {}}]) == [[]]


@needs_node
def test_presence_works_on_the_real_page_data(tmp_path):
    campus = make_campus(tmp_path)
    page_data = detail(render(campus, tmp_path))
    snapshot = campus.to_dict()
    (everything,) = engine(
        [{"fn": "presence", "nodes": page_data["nodes"], "links": page_data["links"], "graph": snapshot}]
    )
    assert all(everything["nodes"].values()) and everything["missing"] == []
    assert all(everything["links"].values())  # the page's own graph contains every link it draws
    trimmed = {**snapshot, "nodes": [n for n in snapshot["nodes"] if n["id"] != "core2"]}
    (without,) = engine(
        [{"fn": "presence", "nodes": page_data["nodes"], "links": page_data["links"], "graph": trimmed}]
    )
    assert without["nodes"]["core2"] is False
    hidden_links = [eid for eid, shown in without["links"].items() if not shown]
    assert hidden_links and all("core2" in (page_data["links"][e]["from"], page_data["links"][e]["to"]) for e in hidden_links)


# --------------------------------------------------------------------- the UI
DATA = {"nodes": {"a": {}, "b": {}, "c": {}}, "links": LINKS, "groups": [], "diff": {}}
RUN_NEW = {"run_id": "r2", "timestamp": "2026-10-27T09:00:00+00:00", "devices": 3, "links": 2,
           "diff": {"first_run": False, "devices_added": 1}}  # fmt: skip
RUN_OLD = {"run_id": "r1", "timestamp": "2026-10-27T08:00:00+00:00", "devices": 2, "links": 1,
           "diff": {"first_run": True}}  # fmt: skip


def ui(protocol, responses=None, pick=None):
    payload = {"engine": HISTORY_ENGINE_JS, "ui": HISTORY_UI_JS, "protocol": protocol,
               "data": DATA, "responses": responses or {}, "pick": pick}  # fmt: skip
    return run_node(UI_HARNESS, payload)


@needs_node
def test_opened_from_disk_the_scrubber_does_nothing():
    result = ui("file:")
    assert result["calls"]["fetch"] == [] and result["calls"]["appended"] == 0


@needs_node
def test_served_without_history_the_bar_stays_hidden():
    result = ui("http:", {"/api/history": []})
    assert result["calls"]["fetch"] == ["/api/history"] and result["state"]["barHidden"] is True


@needs_node
def test_served_with_history_the_bar_shows_the_newest_run():
    result = ui("http:", {"/api/history": [RUN_NEW, RUN_OLD]})
    state = result["state"]
    assert state["barHidden"] is False and state["sliderMax"] == "1"
    assert "r2" in state["label"] and "2026-10-27 09:00:00" in state["label"]
    assert result["calls"]["setTime"] is None and result["calls"]["nodes"][-1]  # live view restored


@needs_node
def test_scrubbing_back_hides_what_did_not_exist_then():
    old_graph = graph("ab", [("a", "Gi1", "b", "Gi1")])
    responses = {
        "/api/history": [RUN_NEW, RUN_OLD],
        "/api/history/r1": {**RUN_OLD, "graph": old_graph, "diff": {"first_run": True, "summary": {}}},
    }
    result = ui("http:", responses, pick=0)  # slider position 0 is the oldest run
    assert "/api/history/r1" in result["calls"]["fetch"]
    last_nodes = {u["id"]: u["hidden"] for u in result["calls"]["nodes"][-1]}
    last_edges = {u["id"]: u["hidden"] for u in result["calls"]["edges"][-1]}
    assert last_nodes == {"a": False, "b": False, "c": True}  # c did not exist yet
    assert last_edges == {"e1": False, "e2": True}
    assert result["hidden"]["nodes"]["c"] is True and result["calls"]["regroup"] >= 1


@needs_node
def test_a_run_that_cannot_be_loaded_says_so_and_leaves_the_map_alone():
    responses = {"/api/history": [RUN_NEW, RUN_OLD]}  # /api/history/r1 is missing: ok = false
    before = ui("http:", responses)
    result = ui("http:", responses, pick=0)
    assert result["state"]["afterPick"] == "could not load this crawl"
    assert len(result["calls"]["nodes"]) == len(before["calls"]["nodes"])


# ------------------------------------------------------------------ the page
def test_the_rendered_page_carries_the_history_client(tmp_path):
    campus = make_campus(tmp_path)
    html = render(campus, tmp_path)
    assert "window.LLDPHistory" in html and "hist-bar" in html and "#hist-bar{" in html
    assert HISTORY_CSS.strip() in html and "/api/history" in html
    assert html.index("LLDPHistory = {") < html.index("var DATA = window.LLDP_DETAIL, Hist")
