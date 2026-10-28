"""Embedded HTTP server: endpoints, JSON envelopes, the crawl trigger and its lock."""

from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

from src import MapperError
from src.graph_engine import GraphEngine, HistoryStore
from src.web.server import LOCK_NAME, CrawlController, CrawlLock, create_server

PAGE = "<!doctype html><title>explorer</title><p>hello</p>"
JSON_HEADERS = {"Content-Type": "application/json"}


class Client:
    def __init__(self, port: int) -> None:
        self.port = port

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            raw = response.read()
            return response.status, dict(response.getheaders()), raw
        finally:
            conn.close()

    def json(self, method, path, body=None, headers=None):
        status, response_headers, raw = self.request(method, path, body, headers)
        return status, json.loads(raw), response_headers

    def get(self, path):
        return self.json("GET", path)

    def post(self, body=b"", headers=None):
        return self.json("POST", "/api/crawl", body, headers if headers is not None else JSON_HEADERS)


class Harness:
    def __init__(self, tmp_path, runner):
        self.data_dir = tmp_path / "data"
        self.page = tmp_path / "topology.html"
        self.server = create_server(
            "127.0.0.1", 0, data_dir=self.data_dir, page_path=self.page, crawl_runner=runner
        )
        self.client = Client(self.server.server_address[1])
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self.thread.start()
        self.controller = self.server.context.controller

    def close(self):
        self.controller.wait(5)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)


@pytest.fixture
def make_server(tmp_path):
    made = []

    def build(runner=None):
        harness = Harness(tmp_path, runner)
        made.append(harness)
        return harness

    yield build
    for harness in made:
        harness.close()


def write_snapshot(data_dir, vlan=False):
    engine = GraphEngine(data_dir=data_dir)
    a = engine.resolve(name="a", ips=["10.0.0.1"])
    b = engine.resolve(name="b", ips=["10.0.0.2"])
    c = engine.resolve(name="c", ips=["10.9.9.9"])
    for node in (a, b):
        engine.mark_crawled(node, host="x", device_type="cisco_ios", depth=0, hostname=node)
    engine.mark_skipped(c, "outside crawl.allowed_subnets", "out_of_scope")
    engine.add_link(a, "Gi1", b, "Gi1")
    engine.add_link(a, "Gi2", c, "Gi1")
    warnings = {
        frozenset((a, b)): {
            "kind": "vlan_native_mismatch" if vlan else "speed_mismatch",
            "severity": "mismatch",
            "text": "t",
        },
        frozenset((a, c)): {"kind": "below_nominal", "severity": "degraded", "text": "u"},
    }
    for u, v, data in engine.graph.edges(data=True):
        data["warnings"] = [warnings[frozenset((u, v))]]
    engine.save({"seed": "10.0.0.1", "seeds": ["10.0.0.1"], "blind_spots": [{"hostname": "c"}]},
                history=HistoryStore(data_dir))  # fmt: skip
    return engine


# ------------------------------------------------------------------- startup
def test_create_server_binds_and_closes_cleanly():
    server = create_server("127.0.0.1", 0)
    try:
        assert isinstance(server, ThreadingHTTPServer) and server.server_address[1] > 0
    finally:
        server.server_close()


def test_the_default_listen_address_is_this_machine_only():
    import inspect

    assert inspect.signature(create_server).parameters["host"].default == "127.0.0.1"


# ------------------------------------------------------------------- the page
def test_root_serves_the_explorer_page(make_server):
    harness = make_server()
    harness.page.write_text(PAGE, encoding="utf-8")
    status, headers, body = harness.client.request("GET", "/")
    assert status == 200 and body.decode() == PAGE
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert harness.client.request("GET", "/index.html")[0] == 200


def test_root_without_a_page_explains_what_to_do(make_server):
    status, _headers, body = make_server().client.request("GET", "/")
    assert status == 404 and b"No topology yet" in body


# ------------------------------------------------------------------ /api/status
def test_status_before_any_crawl(make_server):
    status, body, headers = make_server().client.get("/api/status")
    assert status == 200 and body["status"] == "ok"
    assert body["data"]["snapshot"] is None and body["data"]["crawl"]["state"] == "idle"
    assert body["data"]["history"]["latest"] is None and body["data"]["can_crawl"] is False
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert headers["Cache-Control"] == "no-store"


def test_status_summarises_the_snapshot(make_server):
    harness = make_server()
    write_snapshot(harness.data_dir, vlan=True)
    data = harness.client.get("/api/status")[1]["data"]
    snap = data["snapshot"]
    assert snap["seeds"] == ["10.0.0.1"] and snap["device_count"] == 3 and snap["link_count"] == 2
    assert snap["counts"] == {"crawled": 2, "failed": 0, "discovered": 1}
    assert snap["blind_spots"] == {"count": 1, "items": [{"hostname": "c"}]}
    assert snap["warnings"] == {"links_with_warnings": 2, "mismatch": 1, "degraded": 1, "vlan": 1}
    assert data["history"]["latest"]["devices"] == 3


# ----------------------------------------------------------------- /api/history
def test_history_endpoints(make_server):
    harness = make_server()
    assert harness.client.get("/api/history")[1] == {"status": "ok", "data": []}
    write_snapshot(harness.data_dir)
    write_snapshot(harness.data_dir)
    runs = harness.client.get("/api/history")[1]["data"]
    assert len(runs) == 2 and runs[0]["diff"]["changed"] is False
    status, body, _ = harness.client.get(f"/api/history/{runs[1]['run_id']}")
    assert status == 200 and body["status"] == "ok"
    data = body["data"]
    assert data["run_id"] == runs[1]["run_id"] and len(data["graph"]["nodes"]) == 3
    assert data["diff"]["first_run"] is True and "links_added" in data["diff"]


def test_history_errors(make_server):
    harness = make_server()
    write_snapshot(harness.data_dir)
    status, body, _ = harness.client.get("/api/history/20990101T000000Z-abcdef")
    assert status == 404 and body["status"] == "error" and "no such run" in body["error"]
    for bad in ("a%20b", "..%2Fetc", "x" * 80):
        status, body, _ = harness.client.get(f"/api/history/{bad}")
        assert status == 400 and body["status"] == "error", bad


def test_a_damaged_history_is_a_500_with_a_message_not_a_dropped_connection(make_server):
    harness = make_server()
    (harness.data_dir / "history").mkdir(parents=True)
    (harness.data_dir / "history" / "history.db").write_bytes(b"junk" * 100)
    status, body, _ = harness.client.get("/api/history")
    assert status == 500 and "Could not read the history" in body["error"]


# --------------------------------------------------------------- routing errors
def test_unknown_paths_and_wrong_methods(make_server):
    client = make_server().client
    assert client.get("/nowhere")[0] == 404
    status, body, headers = client.get("/api/crawl")
    assert status == 405 and headers["Allow"] == "POST" and body["status"] == "error"
    status, _body, headers = client.json("POST", "/api/status", b"")
    assert status == 405 and headers["Allow"] == "GET"
    assert client.json("POST", "/nowhere", b"")[0] == 404


# --------------------------------------------------------------- /api/crawl
def test_crawl_runs_in_the_background_and_reports_its_outcome(make_server):
    release, seen = threading.Event(), []

    def runner():
        seen.append(threading.current_thread().name)
        assert release.wait(5)
        return 3

    harness = make_server(runner)
    status, body, _ = harness.client.post()
    assert status == 200 and body["data"]["state"] == "started"
    assert (harness.data_dir / LOCK_NAME).exists()  # the lock is held while it runs
    running = harness.client.get("/api/status")[1]["data"]["crawl"]
    assert running["state"] == "running" and running["exit_code"] is None
    release.set()
    harness.controller.wait(5)
    done = harness.client.get("/api/status")[1]["data"]["crawl"]
    assert done["state"] == "idle" and done["exit_code"] == 3 and done["error"] is None
    assert done["finished_at"] and seen == ["lldp-crawl"]
    assert not (harness.data_dir / LOCK_NAME).exists()  # and released afterwards


def test_a_second_crawl_while_one_runs_is_a_409(make_server):
    release = threading.Event()
    harness = make_server(lambda: release.wait(5) and 0)
    assert harness.client.post()[0] == 200
    status, body, _ = harness.client.post()
    assert status == 409 and body == {"status": "error", "error": "a crawl is already running"}
    release.set()
    harness.controller.wait(5)
    assert harness.client.post()[0] == 200  # free again
    harness.controller.wait(5)


def test_parallel_triggers_start_exactly_one_crawl(make_server):
    calls, release = [], threading.Event()

    def runner():
        calls.append(1)
        release.wait(5)
        return 0

    harness = make_server(runner)
    codes = []
    threads = [threading.Thread(target=lambda: codes.append(harness.client.post()[0])) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    release.set()
    harness.controller.wait(5)
    assert sorted(codes) == [200, 409, 409, 409, 409, 409] and len(calls) == 1


def test_a_lock_held_by_another_process_blocks_the_trigger(make_server):
    ran = []
    harness = make_server(lambda: ran.append(1) or 0)
    harness.data_dir.mkdir(parents=True)
    (harness.data_dir / LOCK_NAME).write_text(json.dumps({"pid": os.getpid(), "started_at": "x"}))
    assert harness.client.post()[0] == 409 and ran == []
    assert harness.client.get("/api/status")[1]["data"]["crawl"]["state"] == "running"


def test_a_failing_crawl_is_recorded_and_frees_the_lock(make_server):
    def runner():
        raise MapperError("Could not crawl the seed device 10.0.0.1: unreachable")

    harness = make_server(runner)
    assert harness.client.post()[0] == 200
    harness.controller.wait(5)
    state = harness.client.get("/api/status")[1]["data"]["crawl"]
    assert state["state"] == "idle" and state["exit_code"] is None
    assert "unreachable" in state["error"] and not (harness.data_dir / LOCK_NAME).exists()


def test_an_unexpected_crash_does_not_kill_the_server(make_server):
    def runner():
        raise RuntimeError("boom")

    harness = make_server(runner)
    harness.client.post()
    harness.controller.wait(5)
    assert "unexpected error: boom" in harness.client.get("/api/status")[1]["data"]["crawl"]["error"]
    assert not (harness.data_dir / LOCK_NAME).exists()


def test_without_a_runner_the_trigger_is_501(make_server):
    status, body, _ = make_server().client.post()
    assert status == 501 and "cannot start crawls" in body["error"]


# ------------------------------------------------------- /api/crawl: bad requests
def test_empty_or_empty_object_bodies_are_accepted(make_server):
    harness = make_server(lambda: 0)
    assert harness.client.post(b"", {})[0] == 200
    harness.controller.wait(5)
    assert harness.client.post(b"{}", JSON_HEADERS)[0] == 200
    harness.controller.wait(5)


@pytest.mark.parametrize(
    ("body", "headers"),
    [
        (b'{"seed": "10.1.1.1"}', JSON_HEADERS),  # a crawl takes no options: no remote retargeting
        (b"[]", JSON_HEADERS),
        (b"not json", JSON_HEADERS),
        (b"\xff\xfe", JSON_HEADERS),
        (b"a=b", {"Content-Type": "application/x-www-form-urlencoded"}),
        (b"{}", {"Content-Type": "text/plain"}),
        (b"x" * 5000, JSON_HEADERS),
    ],
)
def test_bad_trigger_bodies_are_400_and_start_nothing(make_server, body, headers):
    ran = []
    harness = make_server(lambda: ran.append(1) or 0)
    status, response, _ = harness.client.post(body, headers)
    assert status == 400 and response["status"] == "error"
    assert ran == [] and not (harness.data_dir / LOCK_NAME).exists()


def test_a_bad_content_length_is_400(make_server):
    harness = make_server(lambda: 0)
    assert harness.client.post(b"", {"Content-Length": "abc"})[0] == 400


# ------------------------------------------------------------------- security
def test_a_cross_origin_page_cannot_trigger_a_crawl(make_server):
    ran = []
    harness = make_server(lambda: ran.append(1) or 0)
    port = harness.client.port
    status, body, _ = harness.client.post(b"", {"Origin": "http://evil.example"})
    assert status == 403 and "cross-origin" in body["error"] and ran == []
    same = harness.client.post(b"", {"Origin": f"http://127.0.0.1:{port}"})
    assert same[0] == 200  # the explorer page itself is same-origin
    harness.controller.wait(5)


def test_a_dns_rebinding_host_is_refused_on_a_loopback_listener(make_server):
    harness = make_server(lambda: 0)
    for method, path in (("GET", "/api/status"), ("GET", "/"), ("POST", "/api/crawl")):
        status, body, _ = harness.client.json(method, path, b"" if method == "POST" else None,
                                              {"Host": "evil.example:80"})  # fmt: skip
        assert status == 403 and body["error"] == "host not allowed", path
    for good in ("localhost:8080", "127.0.0.1", "[::1]:9"):
        assert harness.client.get("/api/status")[0] == 200
        assert harness.client.json("GET", "/api/status", None, {"Host": good})[0] == 200


def test_the_server_sets_no_cors_headers(make_server):
    _status, headers, _body = make_server().client.request("GET", "/api/status", None, {"Origin": "http://x"})
    assert not any(name.lower().startswith("access-control") for name in headers)
    assert headers["X-Content-Type-Options"] == "nosniff"


# ------------------------------------------------------------------ the lock
def test_lock_acquire_release_and_exclusion(tmp_path):
    first, second = CrawlLock(tmp_path / "l"), CrawlLock(tmp_path / "l")
    assert first.acquire() and first.held()
    assert not second.acquire()
    second.release()  # not the owner: leaves it alone
    assert first.held()
    first.release()
    first.release()  # idempotent
    assert not first.held() and second.acquire()
    second.release()


def test_an_old_lock_is_a_crash_leftover_and_is_taken_over(tmp_path):
    path = tmp_path / "l"
    path.write_text(json.dumps({"pid": os.getpid()}))
    old = time.time() - 7 * 3600
    os.utime(path, (old, old))
    assert CrawlLock(path).acquire()


@pytest.mark.skipif(os.name != "posix", reason="process liveness is only checked on POSIX")
def test_a_lock_of_a_dead_process_is_taken_over(tmp_path):
    done = subprocess.run([sys.executable, "-c", "import os;print(os.getpid())"], capture_output=True, text=True)
    path = tmp_path / "l"
    path.write_text(json.dumps({"pid": int(done.stdout)}))
    lock = CrawlLock(path)
    assert not lock.held() and lock.acquire()
    lock.release()


def test_an_unreadable_lock_is_respected_not_stolen(tmp_path):
    path = tmp_path / "l"
    path.write_text("garbage")
    assert not CrawlLock(path).acquire()


def test_controller_state_machine(tmp_path):
    controller = CrawlController(CrawlLock(tmp_path / "l"), None)
    assert controller.can_crawl is False and controller.status()["state"] == "idle"
    with pytest.raises(MapperError, match="without a crawl runner"):
        controller.start()


# ------------------------------------------------------------------- settings
def test_web_settings_precedence(monkeypatch):
    from src.config import ConfigError, WebSettings

    monkeypatch.delenv("LLDP_MONITOR_PORT", raising=False)
    monkeypatch.delenv("LLDP_MONITOR_BIND", raising=False)
    assert WebSettings.from_settings({}) == WebSettings(8080, "127.0.0.1")
    settings = {"web": {"port": 9000, "bind": "0.0.0.0"}}
    assert WebSettings.from_settings(settings) == WebSettings(9000, "0.0.0.0")
    monkeypatch.setenv("LLDP_MONITOR_PORT", "9100")
    monkeypatch.setenv("LLDP_MONITOR_BIND", "10.1.1.1")
    assert WebSettings.from_settings(settings) == WebSettings(9100, "10.1.1.1")
    assert WebSettings.from_settings(settings, port=9200, bind="::1") == WebSettings(9200, "::1")
    for bad in ("abc", "0", "70000", "-5"):
        monkeypatch.setenv("LLDP_MONITOR_PORT", bad)
        with pytest.raises(ConfigError, match="web.port"):
            WebSettings.from_settings({})


def test_history_settings():
    from src.config import ConfigError, HistorySettings

    assert HistorySettings.from_settings({}) == HistorySettings(True, 500)
    assert HistorySettings.from_settings({"history": {"enabled": False, "max_runs": 0}}) == HistorySettings(False, 0)
    with pytest.raises(ConfigError, match="history.max_runs"):
        HistorySettings.from_settings({"history": {"max_runs": -1}})


# ------------------------------------------------------------------------ CLI
def free_port():
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def serve_capture(tmp_path, monkeypatch):
    import main as cli

    monkeypatch.setattr(cli, "DATA_DIR", tmp_path / "data")
    monkeypatch.delenv("LLDP_MONITOR_PORT", raising=False)
    monkeypatch.delenv("LLDP_MONITOR_BIND", raising=False)
    captured = {}

    def fake_serve(server):
        captured["server"] = server
        captured["run"] = captured.get("hook", lambda s: None)(server)
        server.server_close()

    monkeypatch.setattr(cli, "serve", fake_serve)
    return cli, captured


def test_cli_serve_binds_the_requested_port_without_needing_a_settings_file(serve_capture, tmp_path, capsys):
    cli, captured = serve_capture
    port = free_port()
    code = cli.main(["--serve", "--port", str(port), "--config", str(tmp_path / "missing.yaml")])
    server = captured["server"]
    assert code == cli.EXIT_OK and server.server_address == ("127.0.0.1", port)
    assert server.context.controller.can_crawl and server.context.data_dir == tmp_path / "data"
    assert f"http://127.0.0.1:{port}/" in capsys.readouterr().out


def test_cli_serve_port_comes_from_the_environment_and_bind_from_the_flag(serve_capture, monkeypatch):
    cli, captured = serve_capture
    port = free_port()
    monkeypatch.setenv("LLDP_MONITOR_PORT", str(port))
    cli.main(["--serve", "--bind", "127.0.0.1"])
    assert captured["server"].server_address == ("127.0.0.1", port)


def test_cli_serve_rejects_a_bad_port_as_an_expected_error(serve_capture, caplog):
    cli, captured = serve_capture
    assert cli.main(["--serve", "--port", "99999"]) == cli.EXIT_ERROR
    assert "web.port must be <= 65535" in caplog.text and "server" not in captured


def test_a_crawl_started_through_the_server_runs_the_real_pipeline(serve_capture, tmp_path):
    cli, captured = serve_capture
    from pathlib import Path

    sample = Path(__file__).resolve().parent.parent / "samples" / "demo"
    page = tmp_path / "t.html"

    def hook(server):
        controller = server.context.controller
        assert controller.start() is not None
        controller.wait(60)
        return controller.status()

    captured["hook"] = hook
    code = cli.main(
        ["--serve", "--port", str(free_port()), "--replay", str(sample), "--seeds", "10.0.0.1,10.0.0.2",
         "--allow", "10.0.0.0/8", "--output", str(page)]  # fmt: skip
    )
    status = captured["run"]
    assert code == cli.EXIT_OK
    assert status["state"] == "idle" and status["error"] is None and status["exit_code"] == cli.EXIT_PARTIAL
    snapshot = json.loads((tmp_path / "data" / "current_state.json").read_text())
    assert snapshot["metadata"]["seeds"] == ["10.0.0.1", "10.0.0.2"]  # the crawl got the server's options
    assert page.is_file() and len(HistoryStore(tmp_path / "data").runs()) == 1
    assert not (tmp_path / "data" / LOCK_NAME).exists()
