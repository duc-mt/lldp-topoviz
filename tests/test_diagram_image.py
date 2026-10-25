"""The still picture (PNG / SVG / PDF) next to the interactive page: what is drawn, and what ``--output`` writes."""

import shutil
import struct
import sys
from pathlib import Path

import pytest

import main as cli
from src.diagram_image import build_graph
from src.visualizer import TopologyVisualizer, VisualizationError, VisualOptions

from .conftest import make_campus

DEMO = Path(__file__).resolve().parent.parent / "samples" / "demo"
DOT = shutil.which("dot")
needs_dot = pytest.mark.skipif(
    DOT is None, reason="the Graphviz 'dot' program is needed to draw the picture"
)
graphviz = pytest.importorskip("graphviz")

PALETTE = {
    "crawled": "#2F9E6E",
    "failed": "#D64545",
    "discovered": "#9AA5B1",
    "seed": "#E0A100",
    "degraded": "#E08A00",
    "mismatch": "#D9480F",
}


def campus_graph(tmp_path):
    return make_campus(tmp_path).graph


def viz(tmp_path, **options):
    return TopologyVisualizer(tmp_path / "out" / "topology.html", VisualOptions(**options))


# ------------------------------------------------------------------------------ what is drawn
def test_the_picture_has_one_row_per_tier_with_its_name(tmp_path):
    source = viz(tmp_path).build_image_graph(campus_graph(tmp_path), {"seed": "10.0.0.1"}).source
    assert source.count("rank=same") == 4
    for tier in ("CORE", "DISTRIBUTION", "ACCESS", "END DEVICES"):
        assert tier in source


def test_the_picture_says_what_it_shows(tmp_path):
    source = viz(tmp_path).build_image_graph(campus_graph(tmp_path), {"seed": "10.0.0.1"}).source
    assert (
        "LLDP topology" in source
        and "Seed 10.0.0.1" in source
        and "19 devices, 23 cables" in source
    )
    assert (
        "Crawled (8)" in source
        and "Failed (0)" in source
        and "Neighbor only (11)" in source
        and "1G" in source
    )


def test_devices_are_coloured_by_status_and_the_seed_has_a_ring(tmp_path):
    source = viz(tmp_path).build_image_graph(campus_graph(tmp_path), {"seed": "10.0.0.1"}).source
    assert "fillcolor=" + '"#2F9E6E"' in source or 'fillcolor="#2F9E6E"' in source
    assert source.count("penwidth=4") == 1 and '"#E0A100"' in source  # the seed (core1) only
    assert "outside scope" in source  # a neighbor that was not crawled says why


def test_endpoints_fold_into_one_badge_per_switch_like_on_the_page(tmp_path):
    source = viz(tmp_path).build_image_graph(campus_graph(tmp_path), {}).source
    assert "5 Phones" in source and "3 Access Points" in source
    assert (
        "grp:" not in source and "ge:" not in source
    )  # Graphviz reads ':' as a port: plain ids only
    assert source.count("style=dashed") == 2  # the two badges hang off their switches


def test_interface_names_sit_at_both_ends_unless_switched_off(tmp_path):
    graph = campus_graph(tmp_path)
    with_ports = viz(tmp_path).build_image_graph(graph, {}).source
    without = viz(tmp_path, port_labels=False).build_image_graph(graph, {}).source
    assert "taillabel" in with_ports and "headlabel" in with_ports and "Gi1/0/1" in with_ports
    assert "taillabel" not in without and "headlabel" not in without


def test_a_port_channel_and_a_finding_are_written_on_the_link():
    infos = {
        n: {
            "label": n,
            "sub": "",
            "badge": "",
            "kind": "switch",
            "status": "crawled",
            "seed": False,
            "rank": r,
        }
        for n, r in (("a", 1), ("b", 2))
    }
    link = {
        "from": "a",
        "to": "b",
        "color": "#7B8794",
        "width": 4,
        "dashed": False,
        "label": "LACP \u00d72",
        "fromPorts": ["Po1"],
        "toPorts": ["Po7"],
        "warn": "degraded",
        "speed_style": "10G",
    }
    source = build_graph(
        infos=infos,
        links=[link],
        groups=[],
        tier_names={1: "Core", 2: "Access"},
        title="t",
        subtitle="s",
        palette=PALETTE,
    ).source
    assert (
        "LACP \u00d72" in source
        and "\u25b2 degraded" in source
        and "Po1" in source
        and "Po7" in source
    )
    assert (
        'fontcolor="#E08A00"' in source and "degraded link" in source
    )  # the legend explains the marker


def test_a_cdp_only_link_is_dashed_like_on_the_page(tmp_path):
    infos = {
        n: {
            "label": n,
            "sub": "",
            "badge": "",
            "kind": "switch",
            "status": "crawled",
            "seed": False,
            "rank": 1,
        }
        for n in ("a", "b")
    }
    link = {
        "from": "a",
        "to": "b",
        "color": "#7B8794",
        "width": 2,
        "dashed": True,
        "label": "",
        "fromPorts": [],
        "toPorts": [],
        "warn": "",
        "speed_style": "1G",
    }
    assert (
        "style=dashed"
        in build_graph(
            infos=infos,
            links=[link],
            groups=[],
            tier_names={},
            title="t",
            subtitle="s",
            palette=PALETTE,
        ).source
    )


# ------------------------------------------------------------------------------ the file
def png_size(path):
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


@needs_dot
def test_render_image_writes_a_png(tmp_path):
    out = viz(tmp_path).render_image(
        campus_graph(tmp_path), {"seed": "10.0.0.1"}, tmp_path / "out" / "topology.png"
    )
    assert out == tmp_path / "out" / "topology.png"
    width, height = png_size(out)
    assert width > 800 and height > 400
    assert sorted(p.name for p in out.parent.iterdir()) == ["topology.png"]  # no .gv left behind


@needs_dot
@pytest.mark.parametrize("suffix,marker", [("svg", b"<svg"), ("pdf", b"%PDF")])
def test_the_extension_sets_the_format(tmp_path, suffix, marker):
    out = viz(tmp_path).render_image(campus_graph(tmp_path), {}, tmp_path / f"topology.{suffix}")
    assert marker in out.read_bytes()[:400]


def test_an_unknown_extension_is_an_error_not_a_crash(tmp_path):
    with pytest.raises(VisualizationError, match="Cannot draw a '.xyz' picture"):
        viz(tmp_path).render_image(campus_graph(tmp_path), {}, tmp_path / "topology.xyz")


def test_without_the_dot_program_the_source_is_kept_and_the_error_says_how_to_fix_it(
    tmp_path, monkeypatch
):
    def missing(self, *args, **kwargs):
        raise graphviz.ExecutableNotFound(["dot"])

    monkeypatch.setattr(graphviz.Graph, "render", missing)
    with pytest.raises(VisualizationError, match="apt install graphviz"):
        viz(tmp_path).render_image(campus_graph(tmp_path), {}, tmp_path / "out" / "topology.png")
    assert "rank=same" in (tmp_path / "out" / "topology.gv").read_text()


def test_without_the_graphviz_package_only_the_picture_is_lost(tmp_path, monkeypatch):
    visualizer, graph = viz(tmp_path), campus_graph(tmp_path)
    monkeypatch.setitem(sys.modules, "graphviz", None)  # import graphviz -> ImportError
    with pytest.raises(VisualizationError, match="'graphviz' Python package is not installed"):
        visualizer.render_image(graph, {}, tmp_path / "topology.png")
    assert visualizer.render(graph, {"seed": "10.0.0.1"}).is_file()  # the page does not need it


# ------------------------------------------------------------------------------ --output
def test_output_paths_say_what_each_form_of_output_writes(tmp_path):
    png, html = Path("output/topology.png"), Path("output/topology.html")
    assert cli.output_paths(None) == (png, html)
    assert cli.output_paths(tmp_path / "x.html") == (None, tmp_path / "x.html")
    assert cli.output_paths(tmp_path / "x.HTM") == (None, tmp_path / "x.HTM")
    assert cli.output_paths(tmp_path / "map.png") == (tmp_path / "map.png", tmp_path / "map.html")
    assert cli.output_paths(tmp_path / "map.svg") == (tmp_path / "map.svg", tmp_path / "map.html")


class CampusCrawler:
    """Stands in for the crawl: the campus topology, no SSH and no TextFSM."""

    def __init__(self, *, engine, seed_host, **kwargs):
        self.engine, self.seed_host = engine, seed_host

    def run(self):
        from src.crawler import CrawlResult

        self.engine.graph = make_campus(self.engine.data_dir.parent / "campus").graph
        return CrawlResult(seed=self.seed_host, attempted=19, crawled=8)


@pytest.fixture
def run_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(cli, "Crawler", CampusCrawler)
    monkeypatch.chdir(tmp_path)

    def run(*extra):
        return cli.main(
            ["--config", str(DEMO / "settings.yaml"), "--replay", str(DEMO), "--dry-run", *extra]
        )

    return run


@needs_dot
def test_a_default_run_writes_the_picture_and_the_page(run_cli, tmp_path, capsys):
    assert run_cli() == cli.EXIT_OK
    assert (
        png_size(tmp_path / "output" / "topology.png")[0] > 800
        and (tmp_path / "output" / "topology.html").stat().st_size > 50_000
    )
    text = capsys.readouterr().out
    assert "Diagram   : output/topology.png" in text and "Explorer  : output/topology.html" in text


@needs_dot
def test_a_picture_path_gets_the_page_next_to_it(run_cli, tmp_path):
    assert run_cli("--output", str(tmp_path / "reports" / "lan.png")) == cli.EXIT_OK
    assert (tmp_path / "reports" / "lan.png").is_file() and (
        tmp_path / "reports" / "lan.html"
    ).is_file()


def test_a_html_path_writes_the_page_only(run_cli, tmp_path, capsys):
    assert run_cli("--output", str(tmp_path / "only.html")) == cli.EXIT_OK
    assert (
        sorted(p.name for p in tmp_path.glob("only.*")) == ["only.html"]
        and not (tmp_path / "output").exists()
    )
    assert "Diagram   : " in capsys.readouterr().out


def test_a_default_run_without_graphviz_keeps_the_page_and_says_how_to_get_the_picture(
    run_cli, tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(
        graphviz.Graph,
        "render",
        lambda self, *a, **k: (_ for _ in ()).throw(graphviz.ExecutableNotFound(["dot"])),
    )
    assert run_cli() == cli.EXIT_OK
    assert (tmp_path / "output" / "topology.html").is_file() and not (
        tmp_path / "output" / "topology.png"
    ).exists()
    assert (
        "apt install graphviz" in capsys.readouterr().out
        and (tmp_path / "output" / "topology.gv").is_file()
    )


def test_asking_for_a_picture_by_name_without_graphviz_is_an_error(run_cli, tmp_path, monkeypatch):
    monkeypatch.setattr(
        graphviz.Graph,
        "render",
        lambda self, *a, **k: (_ for _ in ()).throw(graphviz.ExecutableNotFound(["dot"])),
    )
    assert run_cli("--output", str(tmp_path / "wanted.png")) == cli.EXIT_ERROR
    assert (tmp_path / "wanted.html").is_file()  # the page was written before the picture failed
