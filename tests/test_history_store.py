"""Crawl history: persistence, the diff between runs, retention and the --history listing."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest

import main as cli
from src.graph_engine import (
    GraphEngine,
    GraphEngineError,
    HistoryStore,
    is_run_id,
    new_run_id,
)
from src.report import describe_changes, format_history

DEMO = Path(__file__).resolve().parent.parent / "samples" / "demo"


def link(a, a_port, b, b_port, speed=None, warnings=()):
    data = {
        "source": a,
        "target": b,
        "source_port": a_port,
        "target_port": b_port,
        "warnings": list(warnings),
    }
    if speed:
        data["source_oper"] = {"speed": speed, "status": "up"}
        data["target_oper"] = {"speed": speed, "status": "up"}
    return data


def snap(nodes, links, spots=(), seeds=("a",)):
    return {
        "counts": {"crawled": len(nodes), "failed": 0, "discovered": 0},
        "metadata": {
            "seed": seeds[0],
            "seeds": list(seeds),
            "saved_at": "2026-10-27T08:00:00+00:00",
            "blind_spots": list(spots),
            "partial": bool(spots),
        },
        "nodes": [{"id": n, "label": n.upper()} for n in nodes],
        "links": list(links),
    }


@pytest.fixture
def store(tmp_path):
    return HistoryStore(tmp_path)


# ------------------------------------------------------------------ run ids
def test_run_ids_are_sortable_and_url_safe():
    first, second = new_run_id(), new_run_id()
    assert first != second and is_run_id(first) and first[:8].isdigit()
    for bad in ("", "a b", "../x", "a/b", "x" * 65, None, ".hidden"):
        assert not is_run_id(bad)


# ----------------------------------------------------------- persistence
def test_record_then_list_and_get_round_trip(store):
    graph = snap(["a", "b"], [link("a", "Gi1", "b", "Gi1", 1000)], seeds=("a", "z"))
    run = store.record(graph, run_id="r1", timestamp="2026-10-27T08:00:00+00:00")
    assert run["run_id"] == "r1" and run["devices"] == 2 and run["links"] == 1
    assert run["seeds"] == ["a", "z"] and run["diff"]["first_run"] is True
    listed = store.runs()
    assert [r["run_id"] for r in listed] == ["r1"] and listed[0]["devices"] == 2
    full = store.get("r1")
    assert full["graph"] == graph and full["diff"]["devices_added"] == []


def test_runs_are_listed_newest_first_and_can_be_limited(store):
    for i in range(4):
        store.record(snap(["a"], []), run_id=f"r{i}")
    assert [r["run_id"] for r in store.runs()] == ["r3", "r2", "r1", "r0"]
    assert [r["run_id"] for r in store.runs(limit=2)] == ["r3", "r2"]


def test_history_survives_a_new_store_object(tmp_path):
    HistoryStore(tmp_path).record(snap(["a"], []), run_id="r1")
    assert [r["run_id"] for r in HistoryStore(tmp_path).runs()] == ["r1"]


def test_reading_never_creates_the_directory(tmp_path):
    fresh = HistoryStore(tmp_path / "nothing")
    assert fresh.runs() == [] and fresh.get("r1") is None
    assert not (tmp_path / "nothing").exists()


def test_unknown_or_invalid_run_ids_give_none(store):
    store.record(snap(["a"], []), run_id="r1")
    assert store.get("missing") is None and store.get("../etc/passwd") is None


def test_a_duplicate_or_invalid_run_id_is_rejected(store):
    store.record(snap(["a"], []), run_id="r1")
    with pytest.raises(GraphEngineError, match="already exists"):
        store.record(snap(["a"], []), run_id="r1")
    with pytest.raises(GraphEngineError, match="Invalid history run ID"):
        store.record(snap(["a"], []), run_id="bad id")
    assert len(store.runs()) == 1  # the rejected runs left nothing behind


def test_a_damaged_database_is_a_clear_error_not_a_crash(tmp_path):
    store = HistoryStore(tmp_path)
    store.directory.mkdir(parents=True)
    store.path.write_bytes(b"this is not a sqlite file" * 50)
    with pytest.raises(GraphEngineError, match="Could not read the history"):
        store.runs()
    with pytest.raises(GraphEngineError, match="Could not record"):
        store.record(snap(["a"], []))


def test_concurrent_recording_loses_nothing(store):
    errors = []

    def work(i):
        try:
            store.record(snap(["a"], []), run_id=f"t{i}")
        except Exception as exc:  # pragma: no cover - the assertion below reports it
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors and len(store.runs()) == 8


# --------------------------------------------------------------- retention
def test_the_oldest_runs_are_dropped_beyond_max_runs(tmp_path):
    store = HistoryStore(tmp_path, max_runs=3)
    for i in range(5):
        store.record(snap(["a"], []), run_id=f"r{i}")
    assert [r["run_id"] for r in store.runs()] == ["r4", "r3", "r2"]
    assert store.get("r0") is None


def test_max_runs_zero_keeps_everything(tmp_path):
    store = HistoryStore(tmp_path, max_runs=0)
    for i in range(5):
        store.record(snap(["a"], []), run_id=f"r{i}")
    assert len(store.runs()) == 5


def test_graphs_are_stored_compressed(store):
    big = snap([f"n{i}" for i in range(300)], [link(f"n{i}", "Gi1", f"n{i+1}", "Gi2") for i in range(299)])
    store.record(big, run_id="r1")
    raw = len(json.dumps(big).encode())
    with sqlite3.connect(store.path) as db:
        (stored,) = db.execute("SELECT length(graph) FROM runs").fetchone()
    assert stored < raw / 3


# -------------------------------------------------------------------- diff
def diff(before, after):
    return HistoryStore.diff(before, after)


def test_first_run_has_no_changes_listed():
    result = diff(None, snap(["a", "b"], [link("a", "Gi1", "b", "Gi1")]))
    assert result["first_run"] is True and result["changed"] is False
    assert result["devices_added"] == [] and result["links_added"] == []


def test_new_and_disappeared_devices():
    before = snap(["a", "b"], [])
    after = snap(["a", "c"], [])
    result = diff(before, after)
    assert result["devices_added"] == [{"id": "c", "label": "C"}]
    assert result["devices_removed"] == [{"id": "b", "label": "B"}]
    assert result["summary"]["devices_added"] == 1 and result["changed"] is True


def test_added_and_removed_links_keep_the_original_port_spelling():
    before = snap(["a", "b"], [link("a", "GigabitEthernet1/0/1", "b", "Eth1/1")])
    after = snap(["a", "b"], [link("a", "GigabitEthernet1/0/2", "b", "Eth1/2")])
    result = diff(before, after)
    assert result["links_removed"] == [
        {"a": "A", "a_port": "GigabitEthernet1/0/1", "b": "B", "b_port": "Eth1/1"}
    ]
    assert result["links_added"][0]["a_port"] == "GigabitEthernet1/0/2"


def test_link_identity_ignores_end_order_and_port_case():
    before = snap(["a", "b"], [link("a", "Gi1", "b", "Gi2")])
    after = snap(["a", "b"], [link("b", "gi2", "a", "GI1")])  # the same cable, written the other way
    assert diff(before, after)["changed"] is False


def test_a_moved_cable_is_a_reconnection():
    before = snap(["a", "b", "c"], [link("a", "Gi1", "b", "Gi1")])
    after = snap(["a", "b", "c"], [link("a", "Gi1", "c", "Gi2")])
    result = diff(before, after)
    assert result["reconnections"] == [
        {
            "device": "A", "port": "Gi1",
            "from_device": "B", "from_port": "Gi1",
            "to_device": "C", "to_port": "Gi2",
        }
    ]  # fmt: skip
    assert result["summary"]["links_added"] == 1 and result["summary"]["links_removed"] == 1


def test_a_cable_that_only_disappears_is_not_a_reconnection():
    result = diff(snap(["a", "b"], [link("a", "Gi1", "b", "Gi1")]), snap(["a", "b"], []))
    assert result["reconnections"] == [] and result["summary"]["links_removed"] == 1


def test_speed_downgrades_and_upgrades_are_told_apart():
    before = snap(["a", "b", "c"], [link("a", "Gi1", "b", "Gi1", 1000), link("a", "Gi2", "c", "Gi1", 100)])
    after = snap(["a", "b", "c"], [link("a", "Gi1", "b", "Gi1", 100), link("a", "Gi2", "c", "Gi1", 1000)])
    result = diff(before, after)
    changes = {c["b"]: (c["from"], c["to"], c["direction"]) for c in result["speed_changes"]}
    assert changes == {"B": (1000, 100, "down"), "C": (100, 1000, "up")}
    assert result["summary"]["speed_downgrades"] == 1 and result["summary"]["speed_changes"] == 2


def test_the_speed_in_use_is_the_slowest_end_that_is_up():
    one_end_slow = link("a", "Gi1", "b", "Gi1", 1000)
    one_end_slow["target_oper"] = {"speed": 100, "status": "up"}
    down_end = link("a", "Gi1", "b", "Gi1", 1000)
    down_end["target_oper"] = {"speed": 10, "status": "down"}  # a down port says nothing
    assert diff(snap(["a", "b"], [down_end]), snap(["a", "b"], [one_end_slow]))["speed_changes"][0]["to"] == 100
    assert diff(snap(["a", "b"], [down_end]), snap(["a", "b"], [down_end]))["changed"] is False


def test_only_new_blind_spots_are_reported():
    spot = {"node_id": "x", "hostname": "x", "reason": "TIMEOUT", "discovered_via": "A"}
    other = {"node_id": "y", "hostname": "y", "reason": "AUTH_FAILURE", "discovered_via": "A"}
    result = diff(snap(["a"], [], [spot]), snap(["a"], [], [spot, other]))
    assert [b["hostname"] for b in result["new_blind_spots"]] == ["y"]
    assert diff(snap(["a"], [], [spot]), snap(["a"], [], [spot]))["new_blind_spots"] == []


def test_the_stored_diff_is_against_the_previous_run(store):
    store.record(snap(["a"], []), run_id="r1")
    store.record(snap(["a", "b"], [link("a", "Gi1", "b", "Gi1")]), run_id="r2")
    third = store.record(snap(["a", "b"], [link("a", "Gi1", "b", "Gi1")]), run_id="r3")
    assert store.get("r2")["diff"]["devices_added"] == [{"id": "b", "label": "B"}]
    assert third["diff"]["changed"] is False and store.get("r3")["diff"]["devices_added"] == []


# ------------------------------------------------------- engine integration
def test_save_records_the_same_snapshot_in_the_history(tmp_path):
    engine = GraphEngine(data_dir=tmp_path)
    a = engine.resolve(name="a", ips=["10.0.0.1"])
    engine.mark_crawled(a, host="10.0.0.1", device_type="cisco_ios", depth=0, hostname="a")
    path = engine.save({"seed": "10.0.0.1", "seeds": ["10.0.0.1"]}, history=HistoryStore(tmp_path))
    run = engine.last_run
    assert run and run["seeds"] == ["10.0.0.1"] and run["diff"]["first_run"]
    assert HistoryStore(tmp_path).get(run["run_id"])["graph"] == json.loads(path.read_text())


def test_saving_without_history_creates_no_history_directory(tmp_path):
    engine = GraphEngine(data_dir=tmp_path)
    engine.save({"seed": "x"})
    assert engine.last_run is None and not (tmp_path / "history").exists()


def test_a_history_failure_never_costs_the_snapshot(tmp_path, caplog):
    class Broken(HistoryStore):
        def record(self, *args, **kwargs):
            raise GraphEngineError("disk full")

    engine = GraphEngine(data_dir=tmp_path)
    path = engine.save({"seed": "x"}, history=Broken(tmp_path))
    assert path.is_file() and engine.last_run is None
    assert "The crawl history was not updated: disk full" in caplog.text


# ----------------------------------------------------------------- listing
def test_describe_changes_wording():
    assert describe_changes({"first_run": True}) == "first recorded run"
    assert describe_changes({"first_run": False}) == "no changes"
    text = describe_changes(
        {
            "devices_added": 2, "links_removed": 1, "reconnections": 1,
            "speed_changes": 3, "speed_downgrades": 2, "new_blind_spots": 1,
        }
    )  # fmt: skip
    assert text == "+2 devices, -1 link, 1 cable moved, 2 speed downgrades, 1 speed upgrade, 1 new blind spot"


def test_format_history_lists_each_run():
    assert "No crawl has been recorded" in format_history([])
    runs = [
        {"run_id": "r2", "timestamp": "2026-10-27T09:00:00+00:00", "seeds": ["a", "b"], "devices": 5,
         "links": 6, "blind_spots": 1, "warnings": 2, "partial": True,
         "diff": {"first_run": False, "devices_added": 1}},
    ]  # fmt: skip
    text = format_history(runs)
    assert "2026-10-27 09:00:00  r2  5 devices, 6 links, 1 blind spot(s), 2 link warning(s)  [partial]" in text
    assert "seeds: a, b" in text and "changes: +1 device" in text


# --------------------------------------------------------------------- CLI
@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "DATA_DIR", tmp_path / "data")
    return tmp_path / "data"


def run_cli(tmp_path, *extra):
    argv = ["--replay", str(DEMO), "--seed", "10.0.0.1", "--allow", "10.0.0.0/8",
            "--output", str(tmp_path / "t.html"), *extra]  # fmt: skip
    return cli.main(argv)


def test_a_crawl_is_recorded_and_listed_by_history(tmp_path, data_dir, capsys):
    run_cli(tmp_path)
    run_cli(tmp_path, "--max-depth", "0")  # a smaller map the second time
    capsys.readouterr()
    assert cli.main(["--history"]) == cli.EXIT_OK
    text = capsys.readouterr().out
    assert "Crawl history (2 run(s), newest first)" in text
    assert "first recorded run" in text and "-" in text.split("changes:")[1]
    runs = HistoryStore(data_dir).runs()
    assert runs[0]["devices"] < runs[1]["devices"] and runs[0]["diff"]["devices_removed"] > 0


def test_each_crawl_prints_what_changed(tmp_path, data_dir, capsys):
    run_cli(tmp_path)
    assert "History   : run " in capsys.readouterr().out
    run_cli(tmp_path)
    assert "no changes" in capsys.readouterr().out


def test_dry_run_records_nothing(tmp_path, data_dir):
    run_cli(tmp_path, "--dry-run")
    assert not data_dir.exists()


def test_history_can_be_switched_off_in_the_settings(tmp_path, data_dir):
    settings = tmp_path / "s.yaml"
    settings.write_text("history:\n  enabled: false\ncrawl:\n  allowed_subnets: [10.0.0.0/8]\n")
    cli.main(["--config", str(settings), "--replay", str(DEMO), "--seed", "10.0.0.1",
              "--output", str(tmp_path / "t.html")])  # fmt: skip
    assert (data_dir / "current_state.json").is_file() and not (data_dir / "history").exists()


def test_history_option_works_with_nothing_recorded_and_creates_nothing(data_dir, capsys):
    assert cli.main(["--history"]) == cli.EXIT_OK
    assert "No crawl has been recorded" in capsys.readouterr().out
    assert not data_dir.exists()


def test_history_count_limits_the_listing_and_zero_means_all(tmp_path, data_dir, capsys):
    for _ in range(3):
        run_cli(tmp_path)
    capsys.readouterr()
    cli.main(["--history", "2"])
    assert "2 run(s)" in capsys.readouterr().out
    cli.main(["--history", "0"])
    assert "3 run(s)" in capsys.readouterr().out
