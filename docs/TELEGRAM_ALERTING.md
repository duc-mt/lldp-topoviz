# Telegram Alerting Pipeline: Operations & Deployment Guide

This document details the configuration, deployment, and operational procedures for the production-grade Telegram alerting pipeline in **LLDP TopoViz**.

---

## 1. Telegram Bot & Channel Provisioning

### Step 1.1: Create Telegram Bot via `@BotFather`
1. Open Telegram and search for the verified bot **`@BotFather`**.
2. Send `/start` and then send `/newbot`.
3. Provide a display name (e.g., `NOC Topology Alert Bot`).
4. Provide a unique username ending in `bot` (e.g., `corp_noc_topoviz_bot`).
5. `@BotFather` will generate an HTTP API token:
   ```text
   7123456789:AAFg-XXXXXXXXXXXXXXXXXXXXXXXXXX
   ```
   > [!CAUTION]
   > Keep this token confidential. Never commit it to git or paste it into shared channels.

### Step 1.2: Obtain `CHAT_ID`
- **Direct User / Group**:
  1. Add the bot to your Telegram group or start a direct chat.
  2. For direct chats, send `/start` to **`@userinfobot`** to view your numerical user ID (e.g., `987654321`).
  3. For supergroups, add **`@RawDataBot`** to the group to inspect the chat object; supergroup IDs start with `-100` (e.g., `-1001987654321`).
  4. Ensure the bot has permission to post messages.

### Step 1.3: Supergroup Forum Topics (`message_thread_id`)
If your Telegram group has **Forum Topics** enabled:
1. Create a dedicated topic (e.g., `#network-topology-alerts`).
2. Right-click or long-press any message in the topic and select **Copy Message Link**.
3. The link format is: `https://t.me/c/<CHAT_ID>/<THREAD_ID>/<MSG_ID>`.
4. The middle integer `<THREAD_ID>` is your `TELEGRAM_MESSAGE_THREAD_ID`.

---

## 2. Configuration Contract

The alerting module supports dual configuration: environment variables take precedence, falling back to `config/settings.yaml`.

### Environment Variables (`.env`)
```bash
# Telegram Alerting Parameters
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN="000000000:AAFg-XXXXXXXXXXXXXXXXXXXXXXXXXX"
TELEGRAM_CHAT_ID="-1001987654321"
TELEGRAM_MESSAGE_THREAD_ID="42"         # Optional forum topic thread ID
TELEGRAM_MIN_SEVERITY="warning"         # info | warning | critical
TELEGRAM_COOLDOWN_SECONDS=300           # Flap dampening window in seconds
TELEGRAM_SILENT=false                   # true to mute push sounds
TELEGRAM_SEND_IMAGE=true                # Attach topology image if available
TELEGRAM_PROXY_URL=""                   # e.g., http://proxy.corp.internal:8080
```

### Settings YAML Fallback (`config/settings.yaml`)
```yaml
telegram:
  enabled: false
  bot_token: ""
  chat_id: ""
  message_thread_id: null
  min_severity: warning
  cooldown_seconds: 300
  silent: false
  send_image: true
  proxy_url: null
```

---

## 3. Production Scheduling & Concurrency Safety

### Option A: Recurring Cronjob with `flock`
Prevent overlapping crawls using the flock wrapper script [`scripts/run_crawl_cron.sh`](file:///home/nms-net/lldp-topoviz/scripts/run_crawl_cron.sh):

```bash
# Edit crontab
crontab -e
```

Add the following entry (runs every 15 minutes, non-blocking lock):
```cron
# LLDP TopoViz automated network crawl (runs every 15 min with flock concurrency protection)
*/15 * * * * /home/nms-net/lldp-topoviz/scripts/run_crawl_cron.sh >> /var/log/lldp-topoviz-cron.log 2>&1
```

How `flock` protects your system:
- When a crawl takes longer than 15 minutes (e.g. slow SSH timeouts across 100+ switches), `flock -n /tmp/lldp_topoviz.lock` fails immediately and exits `0` without queueing duplicate processes.

---

### Option B: Systemd Timer & Service (Recommended for Modern Linux)

Install unit files into `/etc/systemd/system/`:

```bash
# 1. Copy service and timer definitions
sudo cp systemd/lldp-topoviz.service /etc/systemd/system/
sudo cp systemd/lldp-topoviz.timer /etc/systemd/system/

# 2. Reload systemd daemon
sudo systemctl daemon-reload

# 3. Enable and start the timer
sudo systemctl enable --now lldp-topoviz.timer

# 4. Verify status
systemctl list-timers --all | grep lldp-topoviz
```

#### Monitoring with Journalctl:
```bash
# Follow logs in real-time
journalctl -u lldp-topoviz.service -f

# Manually trigger a single execution
sudo systemctl start lldp-topoviz.service
```

---

## 4. Message Format & Fault Tolerance Specifications

### Standardized Layout:
```html
<b>[🟡 WARNING] Topology Crawl Incomplete / Warnings</b>
━━━━━━━━━━━━━━━━━━━━━━━━━━
• <b>Time:</b> 2026-10-23 16:30:00 UTC
• <b>Target:</b> <code>10.255.255.4</code>
• <b>Severity:</b> WARNING
• <b>Event:</b> Crawl completed with issues: 1 failure(s), 0 blind spot(s).

Failed devices:
• 10.0.0.250: SSH connection timed out
━━━━━━━━━━━━━━━━━━━━━━━━━━
<i>Check device credentials, reachability or subnet scope.</i>
```

### Safety Guarantees:
- **Zero-Crash Policy**: All HTTP I/O is wrapped. Notification failures log warnings and never crash the crawler.
- **Token Masking**: `TELEGRAM_BOT_TOKEN` is automatically redacted from all exception messages, URLs, and log strings.
- **Flap Dampening**: Identical alerts (`MD5(level:title)`) are suppressed if fired within `cooldown_seconds`.
- **Payload Splitter**: Messages >4096 characters are automatically uploaded as an HTML file (`alert_details.html`) via `sendDocument`.
- **Air-Gapped Proxy**: Fully routes through forward corporate proxies configured via `TELEGRAM_PROXY_URL`.
