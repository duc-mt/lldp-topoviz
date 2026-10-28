"""
==============================================================================
Module Name:   server.py
Description:   Embedded HTTP server (standard library ThreadingHTTPServer): serves
               the explorer page and a JSON API - crawl status, crawl history, the
               full graph of one past run and an on-demand crawl that runs in a
               background thread, guarded by a lock file so two crawls never overlap.
Author:        Mai Tan Duc <ducmai.network@gmail.com>
Created:       2026-10-27
Version:       1.0.0
License:       MIT
==============================================================================
Usage:         imported by main.py (--serve); create_server() is also usable on its own
Notes:         Requires Python 3.10+. Every API answer is {"status": "ok", "data": ...}
               or {"status": "error", "error": "..."} with a standard HTTP status code.
               The crawl uses the configured device credentials, so the server listens on
               127.0.0.1 unless told otherwise, refuses cross-origin browser requests and,
               on a loopback address, requests whose Host header is not a loopback name.
==============================================================================
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from src import MapperError
from src.graph_engine import GraphEngine, GraphEngineError, HistoryStore, is_run_id
from src.linkcheck import SEVERITY_MISMATCH, is_vlan_warning
from src.visualizer import load_snapshot

logger = logging.getLogger(__name__)

__all__ = [
    "LOCK_NAME",
    "CrawlController",
    "CrawlLock",
    "LldpHttpServer",
    "create_server",
    "serve",
]

#: Default page, resolved from this file so it works regardless of the working directory.
DEFAULT_PAGE = Path(__file__).resolve().parent.parent.parent / "output" / "topology.html"
LOCK_NAME = ".crawl.lock"  # in the data directory
LOCK_STALE_SECONDS = 6 * 3600  # a crawl lock older than this is a crash leftover
MAX_BODY_BYTES = 4096
_LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1"}
_NO_PAGE = (
    "<!doctype html><meta charset=\"utf-8\"><title>LLDP TopoViz</title>"
    "<body style=\"font:16px system-ui;margin:3em\"><h1>No topology yet</h1>"
    "<p>Nothing has been crawled. Start a crawl with <code>POST /api/crawl</code> "
    "or run <code>main.py</code> once; this page then shows the explorer.</p></body>"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ----------------------------------------------------------------------- lock
class CrawlLock:
    """A lock file (``data/.crawl.lock``) that only one crawl at a time can hold.

    The file is created with ``O_EXCL`` (atomic on every platform) and holds the process ID and
    the start time. A lock whose owner process is gone (checked on POSIX only), or that is older
    than ``stale_after`` seconds, is a leftover of a crash and is taken over.
    """

    def __init__(self, path: str | os.PathLike, stale_after: float = LOCK_STALE_SECONDS) -> None:
        self.path = Path(path)
        self.stale_after = stale_after
        self._owned = False

    def acquire(self) -> bool:
        """Take the lock; False if another crawl holds it."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _attempt in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                if self._stale():
                    logger.warning("Taking over a stale crawl lock %s", self.path)
                    self._remove()
                    continue
                return False
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"pid": os.getpid(), "started_at": _utc_now()}, handle)
            self._owned = True
            return True
        return False

    def release(self) -> None:
        """Give the lock back (a no-op when this object does not hold it)."""
        if self._owned:
            self._owned = False
            self._remove()

    def held(self) -> bool:
        """True while some crawl (this process or another) holds the lock."""
        return self.path.exists() and not self._stale()

    def _remove(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def _stale(self) -> bool:
        try:
            age = datetime.now().timestamp() - self.path.stat().st_mtime
        except FileNotFoundError:
            return False
        if age > self.stale_after:
            return True
        if os.name != "posix":  # os.kill(pid, 0) would terminate a process on Windows
            return False
        try:
            pid = int(json.loads(self.path.read_text(encoding="utf-8"))["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except OSError:  # PermissionError: the process exists, it is somebody else's
            return False
        return False


# ----------------------------------------------------------------- controller
class CrawlController:
    """Starts crawls on a background thread, one at a time, and remembers how the last went.

    ``runner`` is the function that performs one whole crawl (the CLI pipeline) and returns its
    exit code; ``None`` means this server cannot start crawls.
    """

    def __init__(self, lock: CrawlLock, runner: Callable[[], int] | None = None) -> None:
        self.lock = lock
        self.runner = runner
        self._state: dict[str, Any] = {
            "state": "idle",
            "started_at": None,
            "finished_at": None,
            "exit_code": None,
            "error": None,
        }
        self._guard = threading.Lock()
        self.thread: threading.Thread | None = None

    @property
    def can_crawl(self) -> bool:
        return self.runner is not None

    def status(self) -> dict[str, Any]:
        """``state`` (idle | running) and how the last crawl ended."""
        with self._guard:
            state = dict(self._state)
        if state["state"] == "idle" and self.lock.held():
            state["state"] = "running"  # another process (the CLI, cron) is crawling
        return state

    def start(self) -> dict[str, Any] | None:
        """Start a crawl and return its status; ``None`` if one is already running."""
        if self.runner is None:
            raise MapperError("This server was started without a crawl runner")
        with self._guard:
            if self._state["state"] == "running" or not self.lock.acquire():
                return None
            self._state.update(
                state="running",
                started_at=_utc_now(),
                finished_at=None,
                exit_code=None,
                error=None,
            )
            self.thread = threading.Thread(target=self._work, name="lldp-crawl", daemon=True)
            self.thread.start()
            return dict(self._state)

    def _work(self) -> None:
        exit_code: int | None = None
        error: str | None = None
        try:
            assert self.runner is not None
            exit_code = self.runner()
        except MapperError as exc:
            logger.error("Web-triggered crawl failed: %s", exc)
            error = str(exc)
        except Exception as exc:  # the thread must never die silently
            logger.exception("Web-triggered crawl crashed")
            error = f"unexpected error: {exc}"
        finally:
            self.lock.release()
            with self._guard:
                self._state.update(
                    state="idle", finished_at=_utc_now(), exit_code=exit_code, error=error
                )

    def wait(self, timeout: float | None = None) -> None:
        """Block until the running crawl (if any) is done (used by tests)."""
        thread = self.thread
        if thread is not None:
            thread.join(timeout)


# --------------------------------------------------------------------- server
@dataclass
class ServerContext:
    """What the request handlers need; one per server."""

    page_path: Path
    data_dir: Path
    history: HistoryStore
    controller: CrawlController
    bind: str


class LldpHttpServer(ThreadingHTTPServer):
    """``ThreadingHTTPServer`` that carries its ``ServerContext``."""

    daemon_threads = True
    context: ServerContext


def _snapshot_status(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Counts the status endpoint reports about one snapshot."""
    metadata = snapshot.get("metadata") or {}
    links = snapshot.get("links", [])
    spots = metadata.get("blind_spots") or []
    mismatch = degraded = vlan = 0
    for link in links:
        found = link.get("warnings") or []
        if not found:
            continue
        if any(w.get("severity") == SEVERITY_MISMATCH for w in found):
            mismatch += 1
        else:
            degraded += 1
        vlan += any(is_vlan_warning(str(w.get("kind"))) for w in found)
    return {
        "saved_at": metadata.get("saved_at"),
        "seeds": metadata.get("seeds") or ([metadata["seed"]] if metadata.get("seed") else []),
        "source": metadata.get("source"),
        "partial": bool(metadata.get("partial")),
        "sessions": metadata.get("sessions"),
        "device_count": len(snapshot.get("nodes", [])),
        "link_count": len(links),
        "counts": snapshot.get("counts", {}),
        "blind_spots": {"count": len(spots), "items": spots},
        "warnings": {
            "links_with_warnings": mismatch + degraded,
            "mismatch": mismatch,
            "degraded": degraded,
            "vlan": vlan,
        },
    }


class _Handler(BaseHTTPRequestHandler):
    server: LldpHttpServer
    server_version = "LLDPTopoViz"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------- plumbing
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 (signature of the base)
        logger.info("%s - %s", self.address_string(), format % args)

    def _send(
        self,
        code: int,
        body: bytes,
        content_type: str,
        extra: dict[str, str] | None = None,
    ) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(
        self,
        code: int,
        data: Any = None,
        error: str | None = None,
        extra: dict[str, str] | None = None,
    ) -> None:
        payload: dict[str, Any] = (
            {"status": "ok", "data": data} if error is None else {"status": "error", "error": error}
        )
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8", extra)

    def _error(self, code: HTTPStatus, message: str, extra: dict[str, str] | None = None) -> None:
        self._json(int(code), error=message, extra=extra)

    def _loopback_bind(self) -> bool:
        bind = self.server.context.bind
        if bind.lower() in _LOOPBACK_NAMES:
            return True
        try:
            return ipaddress.ip_address(bind).is_loopback
        except ValueError:
            return False

    def _host_allowed(self) -> bool:
        """On a loopback listener only loopback host names are served (DNS-rebinding guard)."""
        if not self._loopback_bind():
            return True  # listening on a network address: the Host header cannot be checked
        host = urlsplit("//" + (self.headers.get("Host") or "")).hostname or ""
        return host.lower() in _LOOPBACK_NAMES

    # -------------------------------------------------------------- routing
    def do_GET(self) -> None:  # noqa: N802 (the name the base class calls)
        if not self._host_allowed():
            self._error(HTTPStatus.FORBIDDEN, "host not allowed")
            return
        path = unquote(urlsplit(self.path).path)
        context = self.server.context
        try:
            if path in ("/", "/index.html"):
                self._page()
            elif path == "/api/status":
                self._json(HTTPStatus.OK, self._status())
            elif path in ("/api/history", "/api/history/"):
                self._json(HTTPStatus.OK, context.history.runs())
            elif path.startswith("/api/history/"):
                self._history_run(path[len("/api/history/") :])
            elif path == "/api/crawl":
                self._error(HTTPStatus.METHOD_NOT_ALLOWED, "use POST", {"Allow": "POST"})
            else:
                self._error(HTTPStatus.NOT_FOUND, f"no such endpoint: {path}")
        except GraphEngineError as exc:
            logger.error("History error: %s", exc)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_allowed():
            self._error(HTTPStatus.FORBIDDEN, "host not allowed")
            return
        path = unquote(urlsplit(self.path).path)
        if path != "/api/crawl":
            known = path in ("/", "/index.html", "/api/status", "/api/history") or path.startswith(
                "/api/history/"
            )
            if known:
                self._error(HTTPStatus.METHOD_NOT_ALLOWED, "use GET", {"Allow": "GET"})
            else:
                self._error(HTTPStatus.NOT_FOUND, f"no such endpoint: {path}")
            return
        self._start_crawl()

    # ------------------------------------------------------------- handlers
    def _page(self) -> None:
        try:
            body = self.server.context.page_path.read_bytes()
        except OSError:
            self._send(HTTPStatus.NOT_FOUND, _NO_PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return
        self._send(HTTPStatus.OK, body, "text/html; charset=utf-8", {"Cache-Control": "no-cache"})

    def _status(self) -> dict[str, Any]:
        context = self.server.context
        snapshot = load_snapshot(context.data_dir / "current_state.json")
        runs = context.history.runs(limit=1)
        return {
            "crawl": context.controller.status(),
            "can_crawl": context.controller.can_crawl,
            "snapshot": _snapshot_status(snapshot) if snapshot else None,
            "history": {"latest": runs[0] if runs else None},
        }

    def _history_run(self, run_id: str) -> None:
        if not is_run_id(run_id):
            self._error(HTTPStatus.BAD_REQUEST, f"invalid run id: {run_id!r}")
            return
        run = self.server.context.history.get(run_id)
        if run is None:
            self._error(HTTPStatus.NOT_FOUND, f"no such run: {run_id}")
            return
        self._json(HTTPStatus.OK, run)

    def _start_crawl(self) -> None:
        controller = self.server.context.controller
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc != (self.headers.get("Host") or ""):
            self._error(HTTPStatus.FORBIDDEN, "cross-origin requests are not allowed")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "invalid Content-Length")
            return
        if length < 0 or length > MAX_BODY_BYTES:
            self._error(HTTPStatus.BAD_REQUEST, f"the body must be at most {MAX_BODY_BYTES} bytes")
            return
        raw = self.rfile.read(length) if length else b""
        if raw.strip():
            kind = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if kind != "application/json":
                self._error(HTTPStatus.BAD_REQUEST, "send application/json (or no body)")
                return
            try:
                body = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                self._error(HTTPStatus.BAD_REQUEST, "the body is not valid JSON")
                return
            if not isinstance(body, dict) or body:
                self._error(
                    HTTPStatus.BAD_REQUEST,
                    "the body must be empty or {}: a crawl takes no options here",
                )
                return
        if not controller.can_crawl:
            self._error(HTTPStatus.NOT_IMPLEMENTED, "this server cannot start crawls")
            return
        started = controller.start()
        if started is None:
            self._error(HTTPStatus.CONFLICT, "a crawl is already running")
            return
        self._json(HTTPStatus.OK, {**started, "state": "started"})


def create_server(
    host: str = "127.0.0.1",
    port: int = 8080,
    *,
    data_dir: str | os.PathLike | None = None,
    page_path: str | os.PathLike | None = None,
    crawl_runner: Callable[[], int] | None = None,
    history: HistoryStore | None = None,
) -> LldpHttpServer:
    """Bind the server (it does not serve until ``serve_forever()`` is called).

    Args:
        host: Address to listen on; the default keeps the server on this machine.
        port: TCP port (0 picks a free one; read it back from ``server.server_address``).
        data_dir: Where ``current_state.json``, ``history/`` and the lock live (default: the
            project's ``data`` directory).
        page_path: The explorer page served at ``/`` (default ``output/topology.html``).
        crawl_runner: Performs one whole crawl and returns its exit code; without it
            ``POST /api/crawl`` answers 501.
        history: The history store (default: the one in ``data_dir``).

    Raises:
        OSError: If the address cannot be bound.
    """
    base = Path(data_dir) if data_dir is not None else GraphEngine._DEFAULT_DATA_DIR
    server = LldpHttpServer((host, port), _Handler)
    server.context = ServerContext(
        page_path=Path(page_path) if page_path is not None else DEFAULT_PAGE,
        data_dir=base,
        history=history or HistoryStore(base),
        controller=CrawlController(CrawlLock(base / LOCK_NAME), crawl_runner),
        bind=host,
    )
    return server


def serve(server: LldpHttpServer) -> None:
    """Serve until interrupted (Ctrl+C), then close the socket."""
    host, port = server.server_address[:2]
    logger.info("Serving the explorer on http://%s:%s/ (Ctrl+C to stop)", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server stopped")
    finally:
        server.server_close()
