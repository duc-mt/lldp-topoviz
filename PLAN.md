# Execution Plan: Telegram Alerting Pipeline & Production Scheduling

## Prerequisites
- [x] Requirements approved in `SPEC.md`.
- [x] Working tree clean (`git status`).

---

## Step-by-Step Implementation

### Step 1: Update Configuration Schema & Settings
- **Target File(s)**: `src/config.py`, `config/settings.yaml`
- **Action**: 
  - Add `TelegramSettings` dataclass in `src/config.py` parsing `telegram` section with environment variable overrides (`TELEGRAM_ENABLED`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_MESSAGE_THREAD_ID`, `TELEGRAM_MIN_SEVERITY`, `TELEGRAM_COOLDOWN_SECONDS`, `TELEGRAM_SILENT`, `TELEGRAM_SEND_IMAGE`, `TELEGRAM_PROXY_URL`).
  - Add sample `telegram:` section to `config/settings.yaml`.
- **Verification**: Run `python3 -c "from src.config import TelegramSettings; print(TelegramSettings)"` and existing `tests/test_config.py`.

### Step 2: Implement Notifier Architecture & Telegram Pipeline
- **Target File(s)**: `src/notifier.py`
- **Action**:
  - Implement `BaseNotifier` abstract interface.
  - Implement `TelegramNotifier` using Python stdlib (`urllib.request`, `http.client`, `mimetypes`, `hashlib`, `html`).
  - Implement text `/sendMessage`, photo `/sendPhoto`, document `/sendDocument`.
  - Implement >1024 char caption truncation and >4096 char document fallback (`alert_details.html`).
  - Implement anti-flap MD5 cooldown cache.
  - Implement retry logic for HTTP 429 (`parameters.retry_after`).
  - Implement strict token masking in logs and exceptions.
  - Implement proxy routing support via `ProxyHandler`.
  - Implement thread support via `message_thread_id`.
- **Verification**: Import module, verify type annotations and lint syntax via `ruff check src/notifier.py`.

### Step 3: Implement Integration Hooks in Application Flow
- **Target File(s)**: `main.py`
- **Action**:
  - Load `TelegramSettings` in `main.py`.
  - Initialize notifier (or a no-op fallback if disabled).
  - Add dispatch hooks for:
    - Unhandled exceptions / `MapperError` (severity: `critical`).
    - Partial crawl or device failures in `CrawlResult` (severity: `warning`).
    - Crawl success summary when `min_severity == "info"`.
- **Verification**: Dry-run check and existing test suite `pytest tests/test_main.py`.

### Step 4: Write Comprehensive Offline Unit Tests
- **Target File(s)**: `tests/test_notifier.py`
- **Action**:
  - Test HTML escaping of dynamic text.
  - Test caption truncation (>1024 chars).
  - Test large text message (>4096 chars) fallback to `sendDocument`.
  - Test anti-flap cooldown cache (drop duplicate alert within window).
  - Test severity thresholds (info vs warning vs critical).
  - Test network error swallowing (HTTP 500, Timeout, socket error).
  - Test HTTP 429 retry-after handling.
  - Test missing attachment path fallback.
  - Test token masking in logger records.
  - Test supergroup `message_thread_id` and silent notifications.
- **Verification**: Run `.venv/bin/pytest tests/test_notifier.py -v`.

### Step 5: Deliver Production Scheduling & Operational Deployment Artifacts
- **Target File(s)**:
  - `.env.example`
  - `scripts/run_crawl_cron.sh` (with `flock -n /tmp/lldp_topoviz.lock`)
  - `systemd/lldp-topoviz.service`
  - `systemd/lldp-topoviz.timer`
  - `docs/TELEGRAM_ALERTING.md`
- **Action**:
  - Create template configuration and daemonized automation scripts with safe permissions.
  - Document setup steps for @BotFather, @userinfobot, Supergroup topics, crontab, and systemd units.
- **Verification**: Run bash lint/test on `scripts/run_crawl_cron.sh` and validate systemd unit files syntax.

---

### Step 6: Update Launchers for Automatic .env Bootstrapping
- **Target File(s)**: `scripts/run.sh`, `scripts/run.bat`
- **Action**:
  - In `scripts/run.sh`: Add check for `.env`; if absent, copy `.env.example` -> `.env`, apply `chmod 600`, and source with `set -a`.
  - In `scripts/run.bat`: Add check for `.env`; if absent, copy `.env.example` -> `.env`, and parse key-value lines into Windows environment variables.
- **Verification**: Run `./run --help` or test script execution in dry environment to confirm `.env` auto-generation and sourcing without syntax regressions.

## Final Validation & Review
- [ ] Run linter / formatting checks: `ruff check .`
- [ ] Run full automated test suite: `pytest`
- [ ] Review diff (`git diff`).
