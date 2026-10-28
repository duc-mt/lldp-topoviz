"""VLAN findings in the interactive page and the still picture, and the atomic file writes."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from src import atomic_write_text
from src.crawler import Crawler
from src.graph_engine import GraphEngine
from src.parser import LldpParser
from src.poller import FilePoller
from src.visualizer import (
    TopologyVisualizer,
    VisualizationError,
    VisualOptions,
    graph_from_snapshot,
    load_snapshot,
)

from .conftest import make_config
from .test_visualizer import detail

VLAN_DIR = Path(__file__).resolve().parent.parent / "samples" / "vlan"
NODE = shutil.which("node")
DOT = shutil.which("dot")


@pytest.fixture
def vlan_engine(tmp_path):
    engine = GraphEngine(data_dir=tmp_path / "data")
    Crawler(
        FilePoller(VLAN_DIR),
        LldpParser(),
        engine,
        make_config(allowed_subnets=["10.0.0.0/16"]),
        "10.0.0.11",
        "cisco_ios",
    ).run()
    return engine


def render_page(graph, tmp_path, metadata=None):
    out = tmp_path / "out" / "topology.html"
    TopologyVisualizer(out, VisualOptions()).render(graph, metadata or {"seed": "10.0.0.11"})
    return out.read_text(encoding="utf-8")


def links_by_port(data):
    return {r["fromPorts"][0] if r["fromPorts"] else "": r for r in data["links"].values()}


# ------------------------------------------------------------ interactive page
def test_page_marks_vlan_findings_per_link(vlan_engine, tmp_path):
    data = detail(render_page(vlan_engine.graph, tmp_path))
    found = {
        tuple(sorted(r["pairs"][0][:2])): (r["vlan_warn"], r["warn"]) for r in data["links"].values()
    }
    assert found == {
        ("Eth1/1", "Gi1/0/1"): ("mismatch", "mismatch"),  # native VLAN
        ("Eth1/2", "Gi1/0/2"): ("degraded", "degraded"),  # allowed list
        ("Eth1/3", "Gi1/0/3"): ("mismatch", "mismatch"),  # trunk against access
        ("Eth1/4", "Gi1/0/4"): ("", ""),  # clean
    }


def test_link_panel_data_has_both_ends_vlan_setup(vlan_engine, tmp_path):
    data = detail(render_page(vlan_engine.graph, tmp_path))
    native = next(r for r in data["links"].values() if "Gi1/0/1" in r["pairs"][0])
    (row,) = native["vlan"]
    ends = {"core1": row["a"], "dist1": row["b"]} if native["a"] == "core1" else {"dist1": row["a"], "core1": row["b"]}
    assert ends["core1"] == {
        "interface": "Gi1/0/1", "mode": "trunk", "access_vlan": None,
        "native_vlan": 1, "allowed_vlans": "10,20,30",
    }  # fmt: skip
    assert ends["dist1"]["native_vlan"] == 99 and ends["dist1"]["interface"] == "Ethernet1/1"
    access = next(r for r in data["links"].values() if "Gi1/0/3" in r["pairs"][0])
    modes = {side["mode"] for side in access["vlan"][0].values()}
    assert modes == {"access", "trunk"}


def test_vlan_findings_are_tagged_in_the_warning_list_and_tooltip(vlan_engine, tmp_path):
    data = detail(render_page(vlan_engine.graph, tmp_path))
    assert sum(1 for w in data["warnings"] if w["vlan"]) == 3
    assert {w["severity"] for w in data["warnings"]} == {"mismatch", "degraded"}
    page = render_page(vlan_engine.graph, tmp_path)
    assert "\\u26a0 [VLAN] " in page  # the sidebar prefix
    assert "[VLAN] (3)" in page  # the legend row that filters to VLAN findings
    assert "warn:vlan" in page


def test_a_page_without_vlan_data_has_no_vlan_legend_row(tmp_path):
    from .conftest import make_campus

    page = render_page(make_campus(tmp_path / "d").graph, tmp_path)
    assert "warn:vlan" not in page and detail(page)["warnings"] is not None
    assert not any(r["vlan_warn"] for r in detail(page)["links"].values())


def test_vlan_data_survives_the_snapshot_round_trip(vlan_engine, tmp_path):
    path = vlan_engine.save({"seed": "10.0.0.11"})
    rebuilt = graph_from_snapshot(load_snapshot(path))
    live = detail(render_page(vlan_engine.graph, tmp_path))
    again = detail(render_page(rebuilt, tmp_path / "again"))
    pick = lambda d: sorted((r["pairs"][0][0], r["vlan_warn"], json.dumps(r["vlan"], sort_keys=True)) for r in d["links"].values())  # noqa: E731
    assert pick(live) == pick(again)


def test_node_panel_names_the_seed_that_led_to_it(vlan_engine, tmp_path):
    data = detail(render_page(vlan_engine.graph, tmp_path, {"seed": "10.0.0.11", "seeds": ["10.0.0.11", "10.0.9.9"]}))
    assert {n["seed_origin"] for n in data["nodes"].values()} == {"10.0.0.11"}
    assert data["meta"]["seeds"] == ["10.0.0.11", "10.0.9.9"]


@pytest.mark.skipif(NODE is None, reason="Node.js is needed to check the page scripts")
def test_every_inline_script_of_the_page_is_valid_javascript(vlan_engine, tmp_path):
    page = render_page(vlan_engine.graph, tmp_path)
    scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", page, re.S)
    assert len(scripts) >= 8
    for index, source in enumerate(scripts):
        file = tmp_path / f"s{index}.js"
        file.write_text(source, encoding="utf-8")
        done = subprocess.run([NODE, "--check", str(file)], capture_output=True, text=True)
        assert done.returncode == 0, f"script {index}: {done.stderr}"


def test_the_page_stays_self_contained(vlan_engine, tmp_path):
    page = render_page(vlan_engine.graph, tmp_path)
    assert not re.findall(r"(?:src|href)=[\"']https?://", page)  # no external asset was added


# ------------------------------------------------------------- still picture
def test_picture_labels_vlan_findings_and_adds_a_legend_entry(vlan_engine, tmp_path):
    viz = TopologyVisualizer(tmp_path / "t.html", VisualOptions())
    source = viz.build_image_graph(vlan_engine.graph, {"seed": "10.0.0.11"}).source
    assert source.count("[VLAN]") == 4  # three links plus the legend
    assert "[VLAN] finding" in source


def test_picture_without_vlan_findings_has_no_vlan_text(tmp_path):
    from .conftest import make_campus

    viz = TopologyVisualizer(tmp_path / "t.html", VisualOptions())
    assert "[VLAN]" not in viz.build_image_graph(make_campus(tmp_path / "d").graph, {}).source


# ------------------------------------------------------------- atomic writes
def test_atomic_write_replaces_the_file_and_leaves_no_temp(tmp_path):
    target = tmp_path / "sub" / "page.html"
    assert atomic_write_text(target, "one \u00e9") == target
    assert target.read_text(encoding="utf-8") == "one \u00e9"
    atomic_write_text(target, "two")
    assert target.read_text(encoding="utf-8") == "two"
    assert [p.name for p in target.parent.iterdir()] == ["page.html"]


def test_a_failed_write_keeps_the_old_file_and_removes_the_temp(tmp_path, monkeypatch):
    target = tmp_path / "page.html"
    atomic_write_text(target, "old")

    def boom(*_args):
        raise OSError("disk gone")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk gone"):
        atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["page.html"]


def test_the_page_is_written_through_a_temp_file(vlan_engine, tmp_path, monkeypatch):
    out = tmp_path / "out" / "topology.html"
    seen = []
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (seen.append((Path(a).name, Path(b).name)), real(a, b))[1])
    TopologyVisualizer(out, VisualOptions()).render(vlan_engine.graph, {"seed": "x"})
    assert seen == [("topology.html.tmp", "topology.html")] and out.is_file()


def test_a_page_write_failure_is_a_visualization_error_and_keeps_the_old_page(vlan_engine, tmp_path, monkeypatch):
    out = tmp_path / "out" / "topology.html"
    viz = TopologyVisualizer(out, VisualOptions())
    viz.render(vlan_engine.graph, {"seed": "x"})
    before = out.read_bytes()
    monkeypatch.setattr(os, "replace", lambda *_: (_ for _ in ()).throw(OSError("nope")))
    with pytest.raises(VisualizationError):
        viz.render(vlan_engine.graph, {"seed": "y"})
    assert out.read_bytes() == before and not list(out.parent.glob("*.tmp"))


@pytest.mark.skipif(DOT is None, reason="the Graphviz 'dot' program is needed")
def test_the_picture_is_drawn_under_a_temp_name_and_renamed(vlan_engine, tmp_path, monkeypatch):
    seen = []
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (seen.append((Path(a).name, Path(b).name)), real(a, b))[1])
    out = TopologyVisualizer(tmp_path / "t.html", VisualOptions()).render_image(
        vlan_engine.graph, {"seed": "x"}, tmp_path / "pics" / "topology.png"
    )
    assert out == tmp_path / "pics" / "topology.png" and out.read_bytes()[:4] == b"\x89PNG"
    assert seen == [("topology.tmp.png", "topology.png")]
    assert sorted(p.name for p in out.parent.iterdir()) == ["topology.png"]
