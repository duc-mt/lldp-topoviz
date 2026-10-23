# Specification: Telegram Alerting Pipeline & Production Scheduling

## 1. Problem Statement & Motivation
Currently, `lldp-topoviz` executes crawls interactively and dumps results to the console, an HTML topology file, and JSON snapshots. In production environments where topology discovery and network monitoring are daemonized or executed on a recurring schedule (cron/systemd), operators have no real-time awareness of:
- Discovered link anomalies or topology changes.
- Device crawl failures (authentication drops, timeouts, connection errors).
- Incomplete/truncated crawls (due to max devices limit or blind spots).
- Catastrophic pipeline crashes or seed reachability outages.

This specification defines a non-blocking, fault-tolerant alerting module using Telegram Bot API, integrating it into the crawler/mapper lifecycle without adding fragile external HTTP dependencies, ensuring zero crash risk, anti-flap dampening, proxy compatibility, and hands-free cron/systemd automation.

---

## 2. Requirements & Scope

### In Scope
1. **Notifier Interface & Telegram Implementation (`src/notifier.py`)**:
   - `BaseNotifier` abstract base class defining `send_alert(title, message, level="warning", attachment_path=None, metadata=None) -> bool`.
   - `TelegramNotifier` concrete implementation conforming strictly to the contract.
   - Standard library-only implementation (`urllib.request`, `http.client`, `ssl`, `mimetypes`, `hashlib`) - zero external pip dependencies.
   - Support for text alerts (`/sendMessage`), image attachments (`/sendPhoto`), and document/log attachments (`/sendDocument`).
   - Telegram payload limit handling:
     - Photo caption > 1024 chars: Truncated to 1000 chars with `... [truncated]`.
     - Text message > 4096 chars: Automatically uploaded as an in-memory document (`alert_details.html`).
   - Strict HTML entity escaping (`html.escape`) to prevent Telegram 400 Bad Request parse errors.
   - Anti-flap / cooldown dampening using in-memory fingerprint cache (`md5(f"{level}:{title}")`).
   - Zero-crash policy: All network I/O wrapped in top-level error handling, 5s connect timeout, 15s read timeout, automatic retry on HTTP 429 (`retry_after`), and secret bot token masking in all log messages and exceptions.
   - Out-gates: If disabled or unconfigured, log once and return False silently. If attachment does not exist, log warning and fallback to text alert.
   - Proxy routing via `TELEGRAM_PROXY_URL` using standard `urllib.request.ProxyHandler`.
   - Forum Topic / Thread support via `TELEGRAM_MESSAGE_THREAD_ID`.

2. **Configuration (`src/config.py` & `.env.example`)**:
   - Environment variable support with YAML fallback in `config/settings.yaml` under `telegram:` section:
     - `TELEGRAM_ENABLED`: bool (default: `false`)
     - `TELEGRAM_BOT_TOKEN`: str (secret, redacted in logs)
     - `TELEGRAM_CHAT_ID`: str | int
     - `TELEGRAM_MESSAGE_THREAD_ID`: Optional[int] (supergroup forum topic ID)
     - `TELEGRAM_MIN_SEVERITY`: "info" | "warning" | "critical" (default: "warning")
     - `TELEGRAM_COOLDOWN_SECONDS`: int (default: `300`)
     - `TELEGRAM_SILENT`: bool (default: `false`)
     - `TELEGRAM_SEND_IMAGE`: bool (default: `true`)
     - `TELEGRAM_PROXY_URL`: Optional[str] (e.g., `http://proxy.corp.internal:8080`)
   - `TelegramSettings` dataclass in `src/config.py`.

3. **Application Integration Hooks (`main.py`)**:
   - Initialize notifier from settings.
   - Trigger `critical` alert on pipeline abort / unhandled `MapperError` / seed failure.
   - Trigger `warning` alert on partial crawl (`result.partial`, `result.failures`, or `limit_hit`).
   - Trigger `info` alert on successful complete crawl (dispatched when `min_severity == "info"`).
   - Non-blocking: notifier failures never affect CLI exit code or topology rendering.

4. **Production Scheduling & Concurrency Safety (`scripts/` & systemd units)**:
   - Flock-based cron wrapper script (`scripts/run_crawl_cron.sh`) preventing job overlap (`flock -n /tmp/lldp_topoviz.lock`).
   - Production systemd service unit (`lldp-topoviz.service`, `Type=oneshot`, `EnvironmentFile`) and timer unit (`lldp-topoviz.timer`).
   - Comprehensive operations guide in `docs/TELEGRAM_ALERTING.md`.

5. **Launcher Automation (`scripts/run.sh`, `scripts/run.bat`)**:
   - Auto-bootstrap `.env` from `.env.example` if `.env` does not exist.
   - Enforce safe permissions (`chmod 600 .env`).
   - Automatically export / load `.env` variables into execution scope for `main.py`.

6. **100% Offline Unit Testing (`tests/test_notifier.py`)**:
   - HTML entity escaping and message formatting.
   - Large payload handling (>4096 chars converted to document, caption >1024 chars truncated).
   - Cooldown dampening logic (suppression of duplicate alert within window).
   - Network exception swallowing (HTTP 500, network timeouts do not raise).
   - HTTP 429 rate limit backoff / retry.
   - Bot token redaction in logs/exceptions.
   - Attachment fallback when file is missing.

### Out of Scope (Non-Goals)
- Bidirectional Telegram bot commands (`/start`, `/status`, polling, webhooks).
- Third-party heavy async frameworks (`python-telegram-bot`, `aiohttp`, `requests`).
- External persistent Redis or SQL cache for cooldown (in-memory cache is sufficient for periodic batch runs).

---

## 3. Technical Design & Contracts

### Interface Contract
```python
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class BaseNotifier(ABC):
    @abstractmethod
    def send_alert(
        self,
        title: str,
        message: str,
        level: str = "warning",  # "info" | "warning" | "critical"
        attachment_path: Optional[str] = None,  # Path to image (.png/.jpg) or doc (.txt/.log)
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Dispatches alert to external channel. Returns True if accepted, False if suppressed/failed."""
```

### Standardized Message Layout
```html
<b>[{EMOJI} {LEVEL.upper()}] {title}</b>
━━━━━━━━━━━━━━━━━━━━━━━━━━
• <b>Time:</b> {timestamp_iso}
• <b>Target:</b> <code>{target_or_device}</code>
• <b>Severity:</b> {level.upper()}
• <b>Event:</b> {message}
━━━━━━━━━━━━━━━━━━━━━━━━━━
<i>{actionable_hint_or_footer}</i>
```
Severity Mapping:
- `info`: `[🔵 INFO]`
- `warning`: `[🟡 WARNING]`
- `critical`: `[🔴 CRITICAL]`

---

## 4. Verification & Acceptance Criteria
- [ ] Criterion 1: `TelegramNotifier` passes all unit tests offline with 100% mocked network I/O.
- [ ] Criterion 2: Messages >4096 characters are seamlessly uploaded as in-memory documents without error.
- [ ] Criterion 3: Captions >1024 characters are truncated cleanly to 1000 characters with `... [truncated]`.
- [ ] Criterion 4: Duplicate alerts within `cooldown_seconds` return `False` and are dropped.
- [ ] Criterion 5: Flap/network errors (HTTP 500, ConnectionRefused, Timeout) return `False` without raising exceptions.
- [ ] Criterion 6: Bot token is strictly redacted/masked in all log lines.
- [ ] Criterion 7: Missing attachments degrade gracefully to text messages without aborting.
- [ ] Criterion 8: Integration hooks in `main.py` dispatch alerts on partial crawls and failure states.
- [ ] Criterion 9: All linters (`ruff check .`) and tests (`pytest`) pass with zero regressions.
