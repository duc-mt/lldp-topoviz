"""
==============================================================================
Module Name:   config.py
Description:   Settings loading and validation: YAML file, environment-variable
               credential overrides, seed device and SSH connection settings.
Author:        Mai Tan Duc <ducmai.network@gmail.com>
Created:       2026-10-10
Version:       1.0.0
License:       MIT
==============================================================================
Usage:         imported by main.py / poller.py (not run directly)
Notes:         Requires Python 3.10+
==============================================================================
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src import MapperError

DEFAULT_CONFIG_PATH = Path("config/settings.yaml")

#: Environment variables that override credentials from the YAML file, so the
#: password does not have to live on disk.
ENV_OVERRIDES = {
    "username": "LLDP_MAPPER_USERNAME",
    "password": "LLDP_MAPPER_PASSWORD",
    "secret": "LLDP_MAPPER_SECRET",
}


class ConfigError(MapperError):
    """Raised when settings.yaml is missing, malformed or incomplete."""


def load_settings(path: str | os.PathLike, required: bool = True) -> dict[str, Any]:
    """Read the YAML settings file into a dict.

    Args:
        path: Settings file.
        required: If False, a missing file yields ``{}`` (used by ``--replay``).

    Raises:
        ConfigError: If the file is unreadable or not a YAML mapping.
    """
    path = Path(path)
    try:
        with path.open("r", encoding="utf-8") as handle:
            settings = yaml.safe_load(handle)
    except FileNotFoundError as exc:
        if not required:
            return {}
        raise ConfigError(f"Settings file not found: {path}") from exc
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Could not read settings file {path}: {exc}") from exc
    if settings is None:
        settings = {}
    if not isinstance(settings, dict):
        raise ConfigError(f"{path} must contain a YAML mapping at the top level")
    return settings


def section(settings: dict[str, Any], name: str) -> dict[str, Any]:
    """Return ``settings[name]`` as a dict (``{}`` when absent).

    Raises:
        ConfigError: If the section exists but is not a mapping.
    """
    value = settings.get(name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"'{name}' in the settings file must be a mapping")
    return value


def as_int(value: Any, name: str, minimum: int = 0) -> int:
    """Convert ``value`` to an int >= ``minimum`` or raise ``ConfigError``."""
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be an integer (got {value!r})") from exc
    if number < minimum:
        raise ConfigError(f"{name} must be >= {minimum} (got {number})")
    return number


@dataclass(frozen=True)
class SeedSettings:
    """Where the crawl starts."""

    host: str
    device_type: str = "cisco_ios"

    @classmethod
    def from_settings(
        cls,
        settings: dict[str, Any],
        host: str | None = None,
        device_type: str | None = None,
    ) -> SeedSettings:
        """Build from the ``seed`` mapping; ``host`` / ``device_type`` (CLI) win over the file.

        Raises:
            ConfigError: If no seed host is configured anywhere.
        """
        seed = section(settings, "seed")
        resolved_host = str(host or seed.get("host") or "").strip()
        if not resolved_host:
            raise ConfigError("No seed device: set seed.host in the settings file or pass --seed")
        resolved_type = str(device_type or seed.get("device_type") or "cisco_ios").strip()
        return cls(host=resolved_host, device_type=resolved_type)


@dataclass(frozen=True)
class ConnectionSettings:
    """SSH credentials and timeouts shared by every device in the crawl."""

    username: str = ""
    password: str = field(default="", repr=False)
    secret: str = field(default="", repr=False)
    port: int = 22
    conn_timeout: int = 15
    auth_timeout: int = 20
    banner_timeout: int = 20
    read_timeout: int = 60

    @classmethod
    def from_settings(
        cls,
        settings: dict[str, Any],
        source: str | os.PathLike = DEFAULT_CONFIG_PATH,
        require_credentials: bool = True,
    ) -> ConnectionSettings:
        """Build from the ``device`` mapping, with ``LLDP_MAPPER_*`` env vars on top.

        Args:
            settings: Parsed settings file.
            source: Settings path, used only in error messages.
            require_credentials: False for ``--replay`` (nothing connects).

        Raises:
            ConfigError: If credentials are missing or a numeric setting is invalid.
        """
        device = dict(section(settings, "device"))
        for key, env_name in ENV_OVERRIDES.items():
            if os.environ.get(env_name):
                device[key] = os.environ[env_name]

        if require_credentials:
            missing = [k for k in ("username", "password") if not device.get(k)]
            if missing:
                env_names = ", ".join(ENV_OVERRIDES[k] for k in missing)
                raise ConfigError(
                    f"{source}: missing device setting(s): {', '.join(missing)} "
                    f"(set them in the file or via {env_names})"
                )

        return cls(
            username=str(device.get("username") or ""),
            password=str(device.get("password") or ""),
            secret=str(device.get("secret") or ""),
            port=as_int(device.get("port", 22), "device.port", 1),
            conn_timeout=as_int(device.get("conn_timeout", 15), "device.conn_timeout", 1),
            auth_timeout=as_int(device.get("auth_timeout", 20), "device.auth_timeout", 1),
            banner_timeout=as_int(device.get("banner_timeout", 20), "device.banner_timeout", 1),
            read_timeout=as_int(device.get("read_timeout", 60), "device.read_timeout", 1),
        )


@dataclass(frozen=True)
class TelegramSettings:
    """Telegram alerting and notification parameters."""

    enabled: bool = False
    bot_token: str = field(default="", repr=False)
    chat_id: str = ""
    message_thread_id: int | None = None
    min_severity: str = "warning"
    cooldown_seconds: int = 300
    silent: bool = False
    send_image: bool = True
    proxy_url: str | None = None

    @classmethod
    def from_settings(
        cls,
        settings: dict[str, Any],
        source: str | os.PathLike = DEFAULT_CONFIG_PATH,
    ) -> TelegramSettings:
        """Build from the ``telegram`` mapping, with ``TELEGRAM_*`` env vars on top."""
        telegram_cfg = dict(section(settings, "telegram"))

        def _bool_val(raw: Any) -> bool:
            if isinstance(raw, bool):
                return raw
            if isinstance(raw, str):
                return raw.strip().lower() in ("true", "1", "yes", "on", "t")
            if isinstance(raw, (int, float)):
                return bool(raw)
            return False

        if "TELEGRAM_ENABLED" in os.environ:
            telegram_cfg["enabled"] = _bool_val(os.environ["TELEGRAM_ENABLED"])

        if os.environ.get("TELEGRAM_BOT_TOKEN"):
            telegram_cfg["bot_token"] = os.environ["TELEGRAM_BOT_TOKEN"]

        if os.environ.get("TELEGRAM_CHAT_ID"):
            telegram_cfg["chat_id"] = os.environ["TELEGRAM_CHAT_ID"]

        if "TELEGRAM_MESSAGE_THREAD_ID" in os.environ:
            val = os.environ["TELEGRAM_MESSAGE_THREAD_ID"].strip()
            telegram_cfg["message_thread_id"] = int(val) if val else None

        if os.environ.get("TELEGRAM_MIN_SEVERITY"):
            telegram_cfg["min_severity"] = os.environ["TELEGRAM_MIN_SEVERITY"]

        if os.environ.get("TELEGRAM_COOLDOWN_SECONDS"):
            telegram_cfg["cooldown_seconds"] = os.environ["TELEGRAM_COOLDOWN_SECONDS"]

        if "TELEGRAM_SILENT" in os.environ:
            telegram_cfg["silent"] = _bool_val(os.environ["TELEGRAM_SILENT"])

        if "TELEGRAM_SEND_IMAGE" in os.environ:
            telegram_cfg["send_image"] = _bool_val(os.environ["TELEGRAM_SEND_IMAGE"])

        if os.environ.get("TELEGRAM_PROXY_URL"):
            telegram_cfg["proxy_url"] = os.environ["TELEGRAM_PROXY_URL"]

        enabled = _bool_val(telegram_cfg.get("enabled", False))
        bot_token = str(telegram_cfg.get("bot_token") or "").strip()
        chat_id_val = telegram_cfg.get("chat_id")
        chat_id = str(chat_id_val).strip() if chat_id_val is not None else ""

        thread_id_val = telegram_cfg.get("message_thread_id")
        thread_id: int | None = None
        if thread_id_val is not None and str(thread_id_val).strip() != "":
            thread_id = as_int(thread_id_val, "telegram.message_thread_id", 1)

        min_sev = str(telegram_cfg.get("min_severity") or "warning").strip().lower()
        if min_sev not in ("info", "warning", "critical"):
            min_sev = "warning"

        cooldown = as_int(
            telegram_cfg.get("cooldown_seconds", 300),
            "telegram.cooldown_seconds",
            0,
        )
        silent = _bool_val(telegram_cfg.get("silent", False))
        send_image = _bool_val(telegram_cfg.get("send_image", True))
        proxy_url = str(telegram_cfg.get("proxy_url") or "").strip() or None

        return cls(
            enabled=enabled,
            bot_token=bot_token,
            chat_id=chat_id,
            message_thread_id=thread_id,
            min_severity=min_sev,
            cooldown_seconds=cooldown,
            silent=silent,
            send_image=send_image,
            proxy_url=proxy_url,
        )
