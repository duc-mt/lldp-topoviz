"""
==============================================================================
Module Name:   notifier.py
Description:   Decoupled, production-grade alerting pipeline for Telegram.
               Supports HTML formatting, attachments, Supergroup forum topics,
               flap dampening, proxy routing, and zero-crash fault tolerance.
Author:        Antigravity
Created:       2026-10-23
Version:       1.0.0
License:       MIT
==============================================================================
Usage:         from src.notifier import TelegramNotifier
Notes:         Requires Python 3.10+ (pure standard library, zero external pip deps)
==============================================================================
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import mimetypes
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import TelegramSettings

logger = logging.getLogger("lldp_mapper.notifier")

SEVERITY_LEVELS: dict[str, int] = {
    "info": 10,
    "warning": 20,
    "critical": 30,
}

SEVERITY_EMOJI: dict[str, str] = {
    "info": "🔵",
    "warning": "🟡",
    "critical": "🔴",
}

# Telegram hard limits
MAX_TEXT_LENGTH = 4096
MAX_CAPTION_LENGTH = 1024
TRUNCATED_CAPTION_TARGET = 1000
CONNECT_TIMEOUT_SECONDS = 5.0
READ_TIMEOUT_SECONDS = 15.0
MAX_429_RETRIES = 2


class BaseNotifier(ABC):
    """Abstract interface for application alerting backends."""

    @abstractmethod
    def send_alert(
        self,
        title: str,
        message: str,
        level: str = "warning",
        attachment_path: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Dispatches alert to external channel.

        Args:
            title: Short description or summary of the alert.
            message: Detailed event narrative or error message.
            level: Severity level ('info' | 'warning' | 'critical').
            attachment_path: Path to optional image (.png/.jpg) or document (.txt/.log).
            metadata: Additional contextual key-value pairs (e.g., target device, hints).

        Returns:
            True if accepted and sent, False if suppressed or delivery failed.
        """


class TelegramNotifier(BaseNotifier):
    """Production-grade Telegram Bot notifier with anti-flap dampening and proxy support."""

    def __init__(self, settings: TelegramSettings | None = None) -> None:
        self.settings = settings or TelegramSettings()
        self.enabled = self.settings.enabled
        self.bot_token = self.settings.bot_token
        self.chat_id = self.settings.chat_id
        self.message_thread_id = self.settings.message_thread_id
        self.min_severity = self.settings.min_severity.lower()
        self.cooldown_seconds = self.settings.cooldown_seconds
        self.silent = self.settings.silent
        self.send_image = self.settings.send_image
        self.proxy_url = self.settings.proxy_url

        self._cooldown_cache: dict[str, float] = {}
        self._opener = self._build_opener()

        if not self.enabled:
            logger.debug("Telegram notifier is disabled via configuration.")
        elif not self.bot_token or not self.chat_id:
            logger.warning("Telegram notifier enabled but BOT_TOKEN or CHAT_ID is missing.")

    def _build_opener(self) -> urllib.request.OpenerDirector:
        """Build a urllib opener configured with proxy if specified."""
        handlers: list[urllib.request.BaseHandler] = []
        if self.proxy_url:
            handlers.append(
                urllib.request.ProxyHandler(
                    {
                        "http": self.proxy_url,
                        "https": self.proxy_url,
                    }
                )
            )
        return urllib.request.build_opener(*handlers)

    def _mask_token(self, text: str) -> str:
        """Redact bot token secrets from strings, urls, and log records."""
        if not self.bot_token:
            return text
        masked = text.replace(self.bot_token, "[REDACTED_BOT_TOKEN]")
        return re.sub(r"bot\d+:[A-Za-z0-9_-]+", "bot[REDACTED_BOT_TOKEN]", masked)

    def _meets_severity(self, level: str) -> bool:
        """Check if alert level meets or exceeds the minimum configured severity."""
        configured_weight = SEVERITY_LEVELS.get(self.min_severity, 20)
        current_weight = SEVERITY_LEVELS.get(level.lower(), 20)
        return current_weight >= configured_weight

    def _is_rate_dampened(self, level: str, title: str) -> bool:
        """Check in-memory flap dampening cache. Returns True if alert should be suppressed."""
        if self.cooldown_seconds <= 0:
            return False

        fingerprint = hashlib.md5(f"{level.lower()}:{title.strip()}".encode()).hexdigest()
        now = time.time()
        last_sent = self._cooldown_cache.get(fingerprint)

        if last_sent is not None and (now - last_sent) < self.cooldown_seconds:
            remaining = self.cooldown_seconds - (now - last_sent)
            logger.debug(
                "Alert '%s' suppressed by cooldown dampening (remaining: %.1fs).",
                title,
                remaining,
            )
            return True

        return False

    def _record_sent(self, level: str, title: str) -> None:
        """Record timestamp of successfully dispatched alert for flap dampening."""
        fingerprint = hashlib.md5(f"{level.lower()}:{title.strip()}".encode()).hexdigest()
        self._cooldown_cache[fingerprint] = time.time()

    def format_message(
        self,
        title: str,
        message: str,
        level: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Format alert content into standardized HTML layout with strict escaping."""
        meta = metadata or {}
        norm_level = level.lower()
        emoji = SEVERITY_EMOJI.get(norm_level, "⚠️")

        # Determine target and footer
        target = meta.get("target") or meta.get("device") or meta.get("host") or "N/A"
        hint = meta.get("hint") or meta.get("footer") or "LLDP TopoViz Automated Telemetry"
        ts = meta.get("timestamp") or datetime.now(timezone.utc).astimezone().strftime(
            "%Y-%m-%d %H:%M:%S %Z"
        )

        safe_title = html.escape(str(title).strip())
        safe_time = html.escape(str(ts).strip())
        safe_target = html.escape(str(target).strip())
        safe_level = html.escape(norm_level.upper())
        safe_event = html.escape(str(message).strip())
        safe_hint = html.escape(str(hint).strip())

        return (
            f"<b>[{emoji} {safe_level}] {safe_title}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Time:</b> {safe_time}\n"
            f"• <b>Target:</b> <code>{safe_target}</code>\n"
            f"• <b>Severity:</b> {safe_level}\n"
            f"• <b>Event:</b> {safe_event}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>{safe_hint}</i>"
        )

    def _truncate_caption(self, caption: str) -> str:
        """Truncate image/document captions exceeding Telegram limits."""
        if len(caption) <= MAX_CAPTION_LENGTH:
            return caption
        suffix = "... [truncated]"
        limit = TRUNCATED_CAPTION_TARGET - len(suffix)
        return caption[:limit] + suffix

    def _encode_multipart_formdata(
        self,
        fields: dict[str, str],
        files: dict[str, tuple[str, bytes, str]],
    ) -> tuple[bytes, str]:
        """Encode fields and files into standard multipart/form-data payload."""
        boundary = f"----TopoVizBoundary{uuid.uuid4().hex}"
        body = bytearray()

        for key, value in fields.items():
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
            body.extend(f"{value}\r\n".encode())

        for field_name, (filename, file_data, content_type) in files.items():
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(
                (
                    f'Content-Disposition: form-data; name="{field_name}"; '
                    f'filename="{filename}"\r\n'
                ).encode()
            )
            body.extend(f"Content-Type: {content_type}\r\n\r\n".encode())
            body.extend(file_data)
            body.extend(b"\r\n")

        body.extend(f"--{boundary}--\r\n".encode())
        content_type_header = f"multipart/form-data; boundary={boundary}"
        return bytes(body), content_type_header

    def _execute_api_call(
        self,
        endpoint: str,
        data: bytes,
        headers: dict[str, str],
    ) -> bool:
        """Execute HTTP request to Telegram API with 429 retry backoff and token masking."""
        url = f"https://api.telegram.org/bot{self.bot_token}/{endpoint}"
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        retries = 0
        while retries <= MAX_429_RETRIES:
            try:
                # Combined timeout for connection and socket read
                with self._opener.open(req, timeout=READ_TIMEOUT_SECONDS) as resp:
                    resp_body = resp.read().decode("utf-8", errors="replace")
                    result = json.loads(resp_body)
                    if result.get("ok"):
                        return True
                    logger.warning(
                        "Telegram API returned ok=false: %s",
                        self._mask_token(resp_body),
                    )
                    return False
            except urllib.error.HTTPError as exc:
                resp_text = exc.read().decode("utf-8", errors="replace")
                masked_err = self._mask_token(resp_text)
                if exc.code == 429 and retries < MAX_429_RETRIES:
                    retries += 1
                    retry_after = 1.0
                    try:
                        err_json = json.loads(resp_text)
                        retry_after = float(err_json.get("parameters", {}).get("retry_after", 1.0))
                    except Exception:
                        pass
                    logger.warning(
                        "Telegram rate limit (429) hit; retrying after %.1fs (attempt %d/%d).",
                        retry_after,
                        retries,
                        MAX_429_RETRIES,
                    )
                    time.sleep(retry_after)
                    continue

                masked_url = self._mask_token(str(getattr(exc, "url", "") or f"/{endpoint}"))
                logger.warning(
                    "Telegram HTTP error %d (%s) for %s: %s",
                    exc.code,
                    exc.reason,
                    masked_url,
                    masked_err,
                )
                return False
            except Exception as exc:
                logger.warning(
                    "Telegram dispatch failed: %s",
                    self._mask_token(str(exc)),
                )
                return False

        return False

    def send_alert(
        self,
        title: str,
        message: str,
        level: str = "warning",
        attachment_path: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Dispatches an alert to Telegram under strict zero-crash fault isolation."""
        try:
            # Out-gate 1: disabled or missing credentials
            if not self.enabled or not self.bot_token or not self.chat_id:
                return False

            # Out-gate 2: minimum severity check
            if not self._meets_severity(level):
                logger.debug(
                    "Alert '%s' dropped: level '%s' below min_severity '%s'.",
                    title,
                    level,
                    self.min_severity,
                )
                return False

            # Out-gate 3: flap dampening / cooldown
            if self._is_rate_dampened(level, title):
                return False

            formatted_html = self.format_message(title, message, level, metadata)

            # Resolve attachment
            valid_attachment: Path | None = None
            if attachment_path:
                att_p = Path(attachment_path)
                if att_p.is_file():
                    valid_attachment = att_p
                else:
                    logger.warning(
                        "Attachment path '%s' does not exist on disk; falling back to text alert.",
                        attachment_path,
                    )

            # Common payload fields
            base_fields: dict[str, str] = {
                "chat_id": str(self.chat_id),
                "parse_mode": "HTML",
            }
            if self.silent:
                base_fields["disable_notification"] = "true"
            if self.message_thread_id is not None:
                base_fields["message_thread_id"] = str(self.message_thread_id)

            success = False

            # Route 1: Image attachment
            if (
                valid_attachment
                and self.send_image
                and valid_attachment.suffix.lower()
                in (
                    ".png",
                    ".jpg",
                    ".jpeg",
                )
            ):
                fields = dict(base_fields)
                fields["caption"] = self._truncate_caption(formatted_html)
                mime = mimetypes.guess_type(str(valid_attachment))[0] or "image/png"
                files = {
                    "photo": (
                        valid_attachment.name,
                        valid_attachment.read_bytes(),
                        mime,
                    )
                }
                data, content_type = self._encode_multipart_formdata(fields, files)
                headers = {"Content-Type": content_type}
                success = self._execute_api_call("sendPhoto", data, headers)

            # Route 2: Document attachment
            elif valid_attachment:
                fields = dict(base_fields)
                fields["caption"] = self._truncate_caption(formatted_html)
                mime = mimetypes.guess_type(str(valid_attachment))[0] or "application/octet-stream"
                files = {
                    "document": (
                        valid_attachment.name,
                        valid_attachment.read_bytes(),
                        mime,
                    )
                }
                data, content_type = self._encode_multipart_formdata(fields, files)
                headers = {"Content-Type": content_type}
                success = self._execute_api_call("sendDocument", data, headers)

            # Route 3: Text message > 4096 chars -> attach as in-memory HTML document
            elif len(formatted_html) > MAX_TEXT_LENGTH:
                doc_caption = self._truncate_caption(
                    f"<b>[🔴 ALERT OVERFLOW] {html.escape(title)}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Event details exceed Telegram message limit "
                    f"({len(formatted_html)} chars).\n"
                    f"Attached complete diagnostic log in <code>alert_details.html</code>."
                )
                fields = dict(base_fields)
                fields["caption"] = doc_caption
                files = {
                    "document": (
                        "alert_details.html",
                        formatted_html.encode("utf-8"),
                        "text/html; charset=utf-8",
                    )
                }
                data, content_type = self._encode_multipart_formdata(fields, files)
                headers = {"Content-Type": content_type}
                success = self._execute_api_call("sendDocument", data, headers)

            # Route 4: Standard text message (<= 4096 chars)
            else:
                fields = dict(base_fields)
                fields["text"] = formatted_html
                data = json.dumps(fields).encode("utf-8")
                headers = {"Content-Type": "application/json"}
                success = self._execute_api_call("sendMessage", data, headers)

            if success:
                self._record_sent(level, title)

            return success

        except Exception as exc:
            # Zero-crash contract: never bubble exceptions
            logger.warning("Unhandled exception in notifier: %s", self._mask_token(str(exc)))
            return False
