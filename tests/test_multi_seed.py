"""Multi-seed crawling: one queue, one visited set, islands merged, scope guard kept."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

import main as cli
from src.config import ConfigError, SeedSettings, parse_seed_list
from src.crawler import (
    SEED_CRAWLED,
    SEED_DUPLICATE,
    SEED_FAILED,
    SEED_OUT_OF_SCOPE,
    Crawler,
    CrawlError,
)
from src.graph_engine import STATUS_CRAWLED, STATUS_DISCOVERED, GraphEngine
from src.models import REASON_OUT_OF_SCOPE
from src.parser import LldpParser
from src.poller import PollerError
from src.report import format_seed_summary, status_symbols

from .conftest import FakePoller, make_config, row

DEMO = Path(__file__).resolve().parent.parent / "samples" / "demo"

# Two islands that no cable joins: a-b (10.0.0.x) and c-d (10.0.1.x); d also sees an ISP router.
ISLANDS = {
    "10.0.0.1": ("a", [row("Gi1", "b", "Gi1", "10.0.0.2")]),
    "10.0.0.2": ("b", [row("Gi1", "a", "Gi1", "10.0.0.1")]),
    "10.0.1.1": ("c", [row("Gi1", "d", "Gi1", "10.0.1.2")]),
    "10.0.1.2": (
        "d",
        [row("Gi1", "c", "Gi1", "10.0.1.1"), row("Gi2", "isp", "Gi0", "203.0.113.9")],
    ),
}


def crawl(devices, seeds, tmp_path, **config):
    poller = FakePoller(devices)
    engine = GraphEngine(data_dir=tmp_path)
    (first_host, first_type), *rest = seeds
    crawler = Crawler(
        poller,
        LldpParser(),
        engine,
        make_config(**config),
        first_host,
        first_type,
        extra_seeds=rest,
    )
    return crawler.run(), engine, poller


def seeds(*hosts):
    return [(h, "cisco_ios") for h in hosts]


def origins(engine):
    return {engine.label(n): a["seed_origin"] for n, a in engine.graph.nodes(data=True)}


# ------------------------------------------------------------------- islands
def test_one_seed_only_sees_its_own_island(tmp_path):
    _result, engine, _ = crawl(ISLANDS, seeds("10.0.0.1"), tmp_path)
    assert sorted(engine.label(n) for n in engine.graph) == ["a", "b"]


def test_seeds_in_disconnected_islands_merge_into_one_graph(tmp_path):
    result, engine, poller = crawl(ISLANDS, seeds("10.0.0.1", "10.0.1.1"), tmp_path)
    crawled = {engine.label(n) for n, a in engine.graph.nodes(data=True) if a["status"] == STATUS_CRAWLED}
    assert crawled == {"a", "b", "c", "d"}
    assert engine.graph.number_of_edges() == 3  # a-b, c-d, and d-isp (a node that was not crawled)
    assert result.crawled == 4 and result.attempted == 4
    assert sorted(poller.calls) == ["10.0.0.1", "10.0.0.2", "10.0.1.1", "10.0.1.2"]


def test_seed_origin_records_which_seed_led_to_each_device(tmp_path):
    result, engine, _ = crawl(ISLANDS, seeds("10.0.0.1", "10.0.1.1"), tmp_path)
    assert origins(engine) == {
        "a": "10.0.0.1",
        "b": "10.0.0.1",
        "c": "10.0.1.1",
        "d": "10.0.1.1",
        "isp": "10.0.1.1",
    }
    assert engine.graph.nodes["a"]["seed"] and engine.graph.nodes["c"]["seed"]
    assert not engine.graph.nodes["b"]["seed"]
    per_seed = {s.host: (s.status, s.crawled, s.discovered) for s in result.seeds}
    assert per_seed == {"10.0.0.1": (SEED_CRAWLED, 2, 2), "10.0.1.1": (SEED_CRAWLED, 2, 3)}


def test_seed_origin_survives_the_snapshot(tmp_path):
    _result, engine, _ = crawl(ISLANDS, seeds("10.0.0.1", "10.0.1.1"), tmp_path)
    nodes = {n["label"]: n for n in engine.to_dict()["nodes"]}
    assert nodes["d"]["seed_origin"] == "10.0.1.1"


# -------------------------------------------------- one visited set, one alias table
SHARED = {
    "10.0.0.1": ("a", [row("Gi1", "m", "Gi1", "10.0.0.9"), row("Gi2", "b", "Gi1", "10.0.0.2")]),
    "10.0.0.2": ("b", [row("Gi1", "a", "Gi2", "10.0.0.1"), row("Gi2", "m", "Gi2", "10.0.0.9")]),
    "10.0.0.9": ("m", [row("Gi1", "a", "Gi1", "10.0.0.1"), row("Gi2", "b", "Gi2", "10.0.0.2")]),
}


def test_a_device_reachable_from_two_seeds_is_polled_once(tmp_path):
    result, engine, poller = crawl(SHARED, seeds("10.0.0.1", "10.0.0.2"), tmp_path)
    assert sorted(poller.calls) == ["10.0.0.1", "10.0.0.2", "10.0.0.9"]  # no host twice
    assert engine.graph.number_of_edges() == 3 and not result.partial


def test_on_a_tie_the_seed_listed_first_gets_the_credit(tmp_path):
    _result, engine, _ = crawl(SHARED, seeds("10.0.0.1", "10.0.0.2"), tmp_path)
    assert origins(engine) == {"a": "10.0.0.1", "b": "10.0.0.2", "m": "10.0.0.1"}
    _result, engine, _ = crawl(SHARED, seeds("10.0.0.2", "10.0.0.1"), tmp_path)
    assert origins(engine)["m"] == "10.0.0.2"


def test_another_address_of_a_crawled_seed_is_reported_as_a_duplicate(tmp_path):
    devices = {**SHARED, "10.0.0.5": ("a", [row("Gi1", "m", "Gi1", "10.0.0.9")])}
    result, engine, poller = crawl(devices, seeds("10.0.0.1", "10.0.0.5"), tmp_path)
    by_host = {s.host: s for s in result.seeds}
    assert by_host["10.0.0.1"].status == SEED_CRAWLED
    assert by_host["10.0.0.5"].status == SEED_DUPLICATE
    assert "10.0.0.1" in by_host["10.0.0.5"].detail
    assert result.duplicates == 1 and engine.label(by_host["10.0.0.5"].node_id) == "a"
    assert poller.calls.count("10.0.0.5") == 1  # it did cost one session to find out


def test_repeated_seeds_are_crawled_once(tmp_path):
    result, _engine, poller = crawl(
        ISLANDS, seeds("10.0.0.1", "10.0.0.1", "10.0.0.1"), tmp_path
    )
    assert poller.calls.count("10.0.0.1") == 1 and len(result.seeds) == 1


def test_max_devices_is_shared_but_every_seed_is_served_before_deeper_devices(tmp_path):
    result, engine, poller = crawl(ISLANDS, seeds("10.0.0.1", "10.0.1.1"), tmp_path, max_devices=2)
    assert sorted(poller.calls) == ["10.0.0.1", "10.0.1.1"]  # a big first island cannot starve the second
    assert result.limit_hit and result.attempted == 2
    assert engine.graph.nodes["b"]["status"] == STATUS_DISCOVERED
    assert engine.graph.nodes["b"]["skip_code"] == "max_devices"


def test_max_depth_counts_from_the_nearest_seed(tmp_path):
    chain = {
        "10.0.0.1": ("a", [row("Gi1", "b", "Gi1", "10.0.0.2")]),
        "10.0.0.2": ("b", [row("Gi1", "a", "Gi1", "10.0.0.1"), row("Gi2", "c", "Gi1", "10.0.0.3")]),
        "10.0.0.3": ("c", [row("Gi1", "b", "Gi2", "10.0.0.2"), row("Gi2", "d", "Gi1", "10.0.0.4")]),
        "10.0.0.4": ("d", [row("Gi1", "c", "Gi2", "10.0.0.3")]),
    }
    _result, _engine, poller = crawl(chain, seeds("10.0.0.1", "10.0.0.4"), tmp_path, max_depth=1)
    assert sorted(poller.calls) == ["10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4"]
    _result, _engine, poller = crawl(chain, seeds("10.0.0.1"), tmp_path, max_depth=1)
    assert sorted(poller.calls) == ["10.0.0.1", "10.0.0.2"]


def test_device_overrides_give_a_seed_its_own_device_type(tmp_path):
    types = {}

    class Recording(FakePoller):
        def poll(self, host, device_type):
            types[host] = device_type
            return super().poll(host, device_type)

    crawler = Crawler(
        Recording(ISLANDS),
        LldpParser(),
        GraphEngine(data_dir=tmp_path),
        make_config(device_overrides={"10.0.1.1": {"device_type": "arista_eos"}}),
        "10.0.0.1",
        "cisco_ios",
        extra_seeds=seeds("10.0.1.1"),
    )
    crawler.run()
    assert types["10.0.0.1"] == "cisco_ios" and types["10.0.1.1"] == "arista_eos"


# ---------------------------------------------------------------- scope guard
def test_a_neighbor_outside_the_allowed_subnets_is_a_blind_spot_not_a_session(tmp_path):
    result, engine, poller = crawl(ISLANDS, seeds("10.0.0.1", "10.0.1.1"), tmp_path)
    assert "203.0.113.9" not in poller.calls
    spot = next(b for b in result.blind_spots if b.hostname == "isp")
    assert spot.reason == REASON_OUT_OF_SCOPE and spot.discovered_via == "d"


def test_an_out_of_scope_seed_is_skipped_logged_and_reported_as_a_blind_spot(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="src.crawler"):
        result, engine, poller = crawl(ISLANDS, seeds("10.0.0.1", "192.0.2.50"), tmp_path)
    assert "192.0.2.50" not in poller.calls  # never contacted
    assert "Skipping seed 192.0.2.50" in caplog.text
    by_host = {s.host: s for s in result.seeds}
    assert by_host["192.0.2.50"].status == SEED_OUT_OF_SCOPE
    assert by_host["10.0.0.1"].status == SEED_CRAWLED  # the other seed went ahead
    spots = [b for b in result.blind_spots if b.management_ip == "192.0.2.50"]
    assert [b.reason for b in spots] == [REASON_OUT_OF_SCOPE]
    assert "outside crawl.allowed_subnets" in spots[0].detail
    assert result.partial and result.failures[0][0] == "192.0.2.50"


def test_a_single_out_of_scope_seed_still_raises_as_before(tmp_path):
    with pytest.raises(CrawlError, match="outside crawl.allowed_subnets"):
        crawl(ISLANDS, seeds("192.0.2.50"), tmp_path)


def test_when_no_seed_is_usable_the_crawl_stops_before_any_session(tmp_path):
    poller = FakePoller(ISLANDS)
    crawler = Crawler(
        poller, LldpParser(), GraphEngine(data_dir=tmp_path), make_config(),
        "192.0.2.1", "cisco_ios", extra_seeds=seeds("198.51.100.1"),
    )  # fmt: skip
    with pytest.raises(CrawlError, match="No seed device can be crawled"):
        crawler.run()
    assert poller.calls == []


# -------------------------------------------------------------- failing seeds
def test_an_unreachable_seed_does_not_stop_the_other_seeds(tmp_path):
    devices = {**ISLANDS, "10.0.1.1": PollerError("Authentication failed for 10.0.1.1")}
    result, engine, _ = crawl(devices, seeds("10.0.0.1", "10.0.1.1"), tmp_path)
    by_host = {s.host: s for s in result.seeds}
    assert by_host["10.0.1.1"].status == SEED_FAILED and "Authentication" in by_host["10.0.1.1"].detail
    assert by_host["10.0.0.1"].crawled == 2
    assert result.partial and result.crawled == 2
    assert engine.graph.nodes[by_host["10.0.1.1"].node_id]["seed"] is True


def test_when_every_seed_fails_the_error_names_them_all(tmp_path):
    with pytest.raises(CrawlError, match=r"any seed device \(10.0.9.1, 10.0.9.2\)"):
        crawl({}, seeds("10.0.9.1", "10.0.9.2"), tmp_path)


def test_the_single_seed_error_message_is_unchanged(tmp_path):
    with pytest.raises(CrawlError, match="Could not crawl the seed device 10.0.9.1: .*unreachable"):
        crawl({}, seeds("10.0.9.1"), tmp_path)


# ------------------------------------------------------------------- settings
def test_parse_seed_list_splits_trims_and_dedupes():
    assert parse_seed_list(" a, b ,,A,c ") == ["a", "b", "c"]
    assert parse_seed_list(["x,y", "Y", ""]) == ["x", "y"]
    assert parse_seed_list(None) == [] and parse_seed_list("") == []


def test_seeds_option_beats_seed_option_beats_the_settings_file():
    settings = {"seed": {"host": "10.0.0.1"}, "crawl": {"seeds": ["10.0.0.2", "10.0.0.1"]}}
    collect = SeedSettings.collect
    assert [s.host for s in collect(settings, hosts="10.9.0.1,10.9.0.2", host="10.8.0.1")] == [
        "10.9.0.1",
        "10.9.0.2",
    ]
    assert [s.host for s in collect(settings, host="10.8.0.1")] == ["10.8.0.1"]
    assert [s.host for s in collect(settings)] == ["10.0.0.1", "10.0.0.2"]  # seed.host first, no repeat
    assert [s.host for s in collect({"crawl": {"seeds": ["10.0.0.7"]}})] == ["10.0.0.7"]


def test_seeds_share_one_device_type():
    found = SeedSettings.collect({}, hosts="a,b", device_type="juniper_junos")
    assert {s.device_type for s in found} == {"juniper_junos"}


def test_no_seed_anywhere_is_a_config_error_and_crawl_seeds_must_be_a_list():
    with pytest.raises(ConfigError, match="No seed device"):
        SeedSettings.collect({})
    with pytest.raises(ConfigError, match="must be a list"):
        SeedSettings.collect({"crawl": {"seeds": "10.0.0.1"}})


# ------------------------------------------------------------------- reporting
def test_seed_summary_lines_use_status_marks(tmp_path):
    result, _engine, _ = crawl(
        ISLANDS, seeds("10.0.0.1", "10.0.1.1", "192.0.2.50"), tmp_path
    )
    marks = {"ok": "OK", "fail": "XX", "warn": "!!"}
    lines = format_seed_summary(result.seeds, marks)
    assert len(lines) == 3
    assert lines[0].startswith("   OK 10.0.0.1") and "2 device(s) crawled, 2 discovered" in lines[0]
    assert lines[2].startswith("   !! 192.0.2.50") and "out of scope" in lines[2]
    assert format_seed_summary(result.seeds[:1], marks) == []  # a single seed has no table


def test_status_symbols_fall_back_to_ascii_on_narrow_consoles():
    class Stream:
        encoding = "cp1252"

    assert status_symbols(Stream())["ok"] == "[ok]"
    assert status_symbols(type("S", (), {"encoding": "utf-8"})())["ok"] == "\u2714"


# ------------------------------------------------------------------------ CLI
@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "DATA_DIR", tmp_path / "data")
    return tmp_path / "data"


def run_cli(tmp_path, *extra):
    out = tmp_path / "topology.html"
    argv = ["--replay", str(DEMO), "--allow", "10.0.0.0/8", "--output", str(out), *extra]
    return cli.main(argv)


def test_cli_multi_seed_replay_reports_every_seed(tmp_path, capsys):
    code = run_cli(tmp_path, "--seeds", "10.0.0.1,10.0.0.2", "--dry-run")
    assert code in (cli.EXIT_OK, cli.EXIT_PARTIAL)
    text = capsys.readouterr().out
    assert "Seeds     : 2 (10.0.0.1, 10.0.0.2)" in text
    assert "10.0.0.1" in text and "10.0.0.2" in text and "device(s) crawled" in text


def test_cli_single_seed_summary_is_unchanged(tmp_path, capsys):
    run_cli(tmp_path, "--seed", "10.0.0.1", "--dry-run")
    text = capsys.readouterr().out
    assert " Seed      : 10.0.0.1" in text and "Seeds" not in text


def test_cli_seeds_takes_precedence_over_seed(tmp_path, capsys, caplog):
    with caplog.at_level(logging.WARNING):
        run_cli(tmp_path, "--seed", "10.0.0.30", "--seeds", "10.0.0.1", "--dry-run")
    assert "--seeds takes precedence" in caplog.text
    assert " Seed      : 10.0.0.1" in capsys.readouterr().out


def test_cli_snapshot_records_the_seeds_and_their_results(tmp_path, isolated_data_dir):
    run_cli(tmp_path, "--seeds", "10.0.0.1,10.0.0.2")
    meta = json.loads((isolated_data_dir / "current_state.json").read_text())["metadata"]
    assert meta["seeds"] == ["10.0.0.1", "10.0.0.2"] and meta["seed"] == "10.0.0.1"
    assert [(s["host"], s["status"]) for s in meta["seed_results"]] == [
        ("10.0.0.1", "crawled"),
        ("10.0.0.2", "crawled"),
    ]


def test_cli_out_of_scope_seed_makes_a_partial_run_not_an_abort(tmp_path, capsys):
    code = run_cli(tmp_path, "--seeds", "10.0.0.1,192.0.2.50", "--dry-run")
    assert code == cli.EXIT_PARTIAL
    assert "out of scope" in capsys.readouterr().out
