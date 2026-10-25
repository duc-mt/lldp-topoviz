"""Unit tests for src/notifier.py (Telegram alerting pipeline)."""

from __future__ import annotations

import io
import json
import logging
import urllib.error
from unittest.mock import MagicMock

import pytest

from src.config import TelegramSettings
from src.notifier import (
    BaseNotifier,
    TelegramNotifier,
)


@pytest.fixture
def valid_settings() -> TelegramSettings:
    return TelegramSettings(
        enabled=True,
        bot_token="123456789:ABC_FAKE_TOKEN_XYZ",
        chat_id="-1001234567890",
        message_thread_id=42,
        min_severity="warning",
        cooldown_seconds=300,
        silent=False,
        send_image=True,
    )


@pytest.fixture
def mock_opener(monkeypatch):
    """Provides a mocked urllib opener that simulates successful Telegram API replies."""
    fake_opener = MagicMock()
    success_resp = MagicMock()
    success_resp.read.return_value = b'{"ok": true, "result": {"message_id": 101}}'
    fake_opener.open.return_value.__enter__.return_value = success_resp
    return fake_opener


def test_base_notifier_interface():
    class DummyNotifier(BaseNotifier):
        def send_alert(self, title, message, level="warning", attachment_path=None, metadata=None):
            return True

    notifier = DummyNotifier()
    assert notifier.send_alert("title", "msg") is True


def test_telegram_notifier_out_gates():
    # 1. Disabled
    notifier_disabled = TelegramNotifier(
        TelegramSettings(enabled=False, bot_token="tok", chat_id="123")
    )
    assert notifier_disabled.send_alert("Test", "Msg", "warning") is False

    # 2. Missing token
    notifier_no_token = TelegramNotifier(
        TelegramSettings(enabled=True, bot_token="", chat_id="123")
    )
    assert notifier_no_token.send_alert("Test", "Msg", "warning") is False

    # 3. Missing chat_id
    notifier_no_chat = TelegramNotifier(TelegramSettings(enabled=True, bot_token="tok", chat_id=""))
    assert notifier_no_chat.send_alert("Test", "Msg", "warning") is False

    # 4. Severity threshold below min_severity (info < warning)
    notifier_thresh = TelegramNotifier(
        TelegramSettings(enabled=True, bot_token="tok", chat_id="123", min_severity="warning")
    )
    assert notifier_thresh.send_alert("Low severity", "Msg", "info") is False


def test_message_formatting_and_strict_html_escaping(valid_settings):
    notifier = TelegramNotifier(valid_settings)

    raw_title = "Switch Core-01 <Critical & Urgent>"
    raw_msg = "Link <Eth1/1> & <Eth1/2> flapping: speed <= 100M"
    raw_target = "core-sw01.corp.internal & backup"
    raw_hint = "Run `show interfaces` & check cabling."

    html_msg = notifier.format_message(
        title=raw_title,
        message=raw_msg,
        level="critical",
        metadata={"target": raw_target, "hint": raw_hint, "timestamp": "2026-10-23 16:00:00 UTC"},
    )

    # Validate HTML escaping
    assert "&lt;Critical &amp; Urgent&gt;" in html_msg
    assert "&lt;Eth1/1&gt; &amp; &lt;Eth1/2&gt;" in html_msg
    assert "&lt;= 100M" in html_msg
    assert "core-sw01.corp.internal &amp; backup" in html_msg
    assert "&amp; check cabling." in html_msg

    # Validate layout markers
    assert "<b>[🔴 CRITICAL]" in html_msg
    assert "• <b>Time:</b> 2026-10-23 16:00:00 UTC" in html_msg
    assert "• <b>Target:</b> <code>" in html_msg
    assert "• <b>Severity:</b> CRITICAL" in html_msg
    assert "━━━━━━━━━━━━━━━━━━━━━━━━━━" in html_msg


def test_send_alert_text_success(valid_settings, mock_opener):
    notifier = TelegramNotifier(valid_settings)
    notifier._opener = mock_opener

    sent = notifier.send_alert(
        title="BGP Flap",
        message="Neighbor 10.1.1.1 reset",
        level="warning",
        metadata={"target": "10.1.1.1"},
    )
    assert sent is True

    # Check request call
    assert mock_opener.open.called
    req = mock_opener.open.call_args[0][0]
    assert req.full_url.endswith("/sendMessage")
    payload = json.loads(req.data.decode("utf-8"))
    assert payload["chat_id"] == "-1001234567890"
    assert payload["message_thread_id"] == "42"
    assert "BGP Flap" in payload["text"]
    assert payload["parse_mode"] == "HTML"


def test_cooldown_dampening_logic(valid_settings, mock_opener, monkeypatch):
    notifier = TelegramNotifier(valid_settings)
    notifier._opener = mock_opener

    fake_time = 1000.0
    monkeypatch.setattr("time.time", lambda: fake_time)

    # First dispatch succeeds
    res1 = notifier.send_alert("CPU Spike", "Load > 95%", "warning")
    assert res1 is True
    assert mock_opener.open.call_count == 1

    # Immediate second identical dispatch suppressed by cooldown
    res2 = notifier.send_alert("CPU Spike", "Load > 95%", "warning")
    assert res2 is False
    assert mock_opener.open.call_count == 1  # No additional network request

    # Different title or severity succeeds
    res3 = notifier.send_alert("Memory Spike", "Load > 95%", "warning")
    assert res3 is True
    assert mock_opener.open.call_count == 2

    res4 = notifier.send_alert("CPU Spike", "Load > 95%", "critical")
    assert res4 is True
    assert mock_opener.open.call_count == 3

    # Fast-forward time past cooldown window (300s)
    fake_time += 301.0
    res5 = notifier.send_alert("CPU Spike", "Load > 95%", "warning")
    assert res5 is True
    assert mock_opener.open.call_count == 4


def test_caption_truncation(valid_settings):
    notifier = TelegramNotifier(valid_settings)
    long_caption = "A" * 1500
    truncated = notifier._truncate_caption(long_caption)
    assert len(truncated) == 1000
    assert truncated.endswith("... [truncated]")


def test_message_overflow_routes_to_document(valid_settings, mock_opener):
    notifier = TelegramNotifier(valid_settings)
    notifier._opener = mock_opener

    huge_message = "X" * 4500
    sent = notifier.send_alert("Big Log", huge_message, "warning")
    assert sent is True

    req = mock_opener.open.call_args[0][0]
    assert req.full_url.endswith("/sendDocument")
    assert b'filename="alert_details.html"' in req.data
    assert b"Content-Type: text/html; charset=utf-8" in req.data


def test_attachment_handling_image(tmp_path, valid_settings, mock_opener):
    notifier = TelegramNotifier(valid_settings)
    notifier._opener = mock_opener

    img_file = tmp_path / "topology.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\nFakePngData")

    sent = notifier.send_alert(
        "Topo Map", "Generated map", "warning", attachment_path=str(img_file)
    )
    assert sent is True

    req = mock_opener.open.call_args[0][0]
    assert req.full_url.endswith("/sendPhoto")
    assert b'filename="topology.png"' in req.data
    assert b"FakePngData" in req.data


def test_attachment_handling_document(tmp_path, valid_settings, mock_opener):
    notifier = TelegramNotifier(valid_settings)
    notifier._opener = mock_opener

    doc_file = tmp_path / "crawl.log"
    doc_file.write_text("Detailed crawl log contents...")

    sent = notifier.send_alert("Crawl Log", "Log report", "warning", attachment_path=str(doc_file))
    assert sent is True

    req = mock_opener.open.call_args[0][0]
    assert req.full_url.endswith("/sendDocument")
    assert b'filename="crawl.log"' in req.data


def test_missing_attachment_fallback_to_text(valid_settings, mock_opener, caplog):
    notifier = TelegramNotifier(valid_settings)
    notifier._opener = mock_opener

    non_existent = "/tmp/does_not_exist_file_9999.png"
    with caplog.at_level(logging.WARNING):
        sent = notifier.send_alert(
            "Fallback test", "File was not found", "warning", attachment_path=non_existent
        )

    assert sent is True
    assert "does not exist on disk; falling back to text alert" in caplog.text
    req = mock_opener.open.call_args[0][0]
    assert req.full_url.endswith("/sendMessage")


def test_network_exception_swallowing_500(valid_settings, caplog):
    notifier = TelegramNotifier(valid_settings)
    fake_opener = MagicMock()
    err_body = io.BytesIO(b'{"ok": false, "description": "Internal server error"}')
    fake_opener.open.side_effect = urllib.error.HTTPError(
        url="https://api.telegram.org/bot123456789:ABC_FAKE_TOKEN_XYZ/sendMessage",
        code=500,
        msg="Internal Server Error",
        hdrs={},
        fp=err_body,
    )
    notifier._opener = fake_opener

    with caplog.at_level(logging.WARNING):
        # Must return False and NOT raise
        res = notifier.send_alert("Crash Alert", "Test server 500 error", "critical")

    assert res is False
    # Validate token masking in error log
    assert "[REDACTED_BOT_TOKEN]" in caplog.text
    assert "123456789:ABC_FAKE_TOKEN_XYZ" not in caplog.text


def test_network_timeout_swallowing(valid_settings, caplog):
    notifier = TelegramNotifier(valid_settings)
    fake_opener = MagicMock()
    fake_opener.open.side_effect = TimeoutError("Connection timed out after 15s")
    notifier._opener = fake_opener

    with caplog.at_level(logging.WARNING):
        res = notifier.send_alert("Timeout Alert", "Connection timed out", "warning")

    assert res is False
    assert "Telegram dispatch failed" in caplog.text


def test_transient_429_retry_handling(valid_settings, monkeypatch):
    notifier = TelegramNotifier(valid_settings)
    fake_opener = MagicMock()

    # First call raises 429 with retry_after = 0.1
    err_body = io.BytesIO(b'{"ok": false, "parameters": {"retry_after": 0.05}}')
    http_429 = urllib.error.HTTPError(
        url="https://api.telegram.org/bot123456789:ABC_FAKE_TOKEN_XYZ/sendMessage",
        code=429,
        msg="Too Many Requests",
        hdrs={},
        fp=err_body,
    )

    success_resp = MagicMock()
    success_resp.read.return_value = b'{"ok": true, "result": {"message_id": 202}}'

    sleep_calls = []
    monkeypatch.setattr("time.sleep", lambda s: sleep_calls.append(s))

    # Side effect: first call 429, second call success
    fake_opener.open.side_effect = [
        http_429,
        MagicMock(__enter__=MagicMock(return_value=success_resp)),
    ]
    notifier._opener = fake_opener

    res = notifier.send_alert("Retry 429 Alert", "Rate limit check", "warning")
    assert res is True
    assert len(sleep_calls) == 1
    assert sleep_calls[0] == 0.05


def test_proxy_and_silent_configuration():
    settings = TelegramSettings(
        enabled=True,
        bot_token="tok",
        chat_id="123",
        silent=True,
        proxy_url="http://proxy.corp.internal:8080",
    )
    notifier = TelegramNotifier(settings)
    assert notifier.silent is True
    assert notifier.proxy_url == "http://proxy.corp.internal:8080"
    # Ensure opener has proxy handler
    handlers = [type(h).__name__ for h in notifier._opener.handlers]
    assert "ProxyHandler" in handlers


def test_main_partial_crawl_triggers_warning_alert(tmp_path, monkeypatch):
    import main as cli

    mock_notifier = MagicMock()
    monkeypatch.setattr(cli, "TelegramNotifier", lambda *a, **kw: mock_notifier)

    demo_dir = cli.BASE_DIR / "samples" / "demo"
    out_file = tmp_path / "topo.html"
    argv = [
        "--config",
        str(demo_dir / "settings.yaml"),
        "--replay",
        str(demo_dir),
        "--output",
        str(out_file),
        "--dry-run",
    ]
    code = cli.main(argv)
    assert code == cli.EXIT_PARTIAL
    assert mock_notifier.send_alert.called
    call_kwargs = mock_notifier.send_alert.call_args[1]
    assert call_kwargs["level"] == "warning"
    assert "Topology Crawl Incomplete / Warnings" in call_kwargs["title"]
    assert "Crawl completed with issues" in call_kwargs["message"]
    assert call_kwargs["attachment_path"] is None


def test_main_pipeline_attaches_png_image(tmp_path, monkeypatch):
    import main as cli

    mock_notifier = MagicMock()
    monkeypatch.setattr(cli, "TelegramNotifier", lambda *a, **kw: mock_notifier)

    demo_dir = cli.BASE_DIR / "samples" / "demo"
    out_file = tmp_path / "topo.png"
    argv = [
        "--config",
        str(demo_dir / "settings.yaml"),
        "--replay",
        str(demo_dir),
        "--output",
        str(out_file),
        "--dry-run",
    ]
    code = cli.main(argv)
    assert code == cli.EXIT_PARTIAL
    assert mock_notifier.send_alert.called
    call_kwargs = mock_notifier.send_alert.call_args[1]
    assert call_kwargs["attachment_path"] == str(out_file)
    assert out_file.is_file()


def test_main_mapper_error_triggers_critical_alert(monkeypatch):
    import main as cli
    from src import MapperError

    mock_notifier = MagicMock()
    monkeypatch.setattr(cli, "TelegramNotifier", lambda *a, **kw: mock_notifier)

    def fake_run(*a, **kw):
        raise MapperError("Fatal network crawl failure")

    monkeypatch.setattr(cli, "run", fake_run)

    code = cli.main(["--seed", "1.1.1.1"])
    assert code == cli.EXIT_ERROR
    assert mock_notifier.send_alert.called
    call_kwargs = mock_notifier.send_alert.call_args[1]
    assert call_kwargs["level"] == "critical"
    assert "Topology Crawl Aborted" in call_kwargs["title"]
    assert "Fatal network crawl failure" in call_kwargs["message"]
