import pytest

from src.config import (
    ENV_OVERRIDES,
    ConfigError,
    ConnectionSettings,
    SeedSettings,
    load_settings,
)
from src.crawler import CrawlConfig


def test_load_settings_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_settings(tmp_path / "nope.yaml")
    assert load_settings(tmp_path / "nope.yaml", required=False) == {}


def test_load_settings_rejects_non_mapping(tmp_path):
    path = tmp_path / "s.yaml"
    path.write_text("- a\n- b\n")
    with pytest.raises(ConfigError, match="mapping"):
        load_settings(path)


def test_env_vars_override_file_credentials(monkeypatch):
    monkeypatch.setenv(ENV_OVERRIDES["username"], "env-user")
    monkeypatch.setenv(ENV_OVERRIDES["password"], "env-pass")
    settings = {"device": {"username": "file-user", "password": "file-pass", "port": 2222}}
    conn = ConnectionSettings.from_settings(settings)
    assert (conn.username, conn.password, conn.port) == ("env-user", "env-pass", 2222)


def test_missing_credentials_name_the_env_vars(monkeypatch):
    for name in ENV_OVERRIDES.values():
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ConfigError, match="LLDP_MAPPER_USERNAME"):
        ConnectionSettings.from_settings({"device": {"username": "", "password": ""}})


def test_credentials_not_required_for_replay(monkeypatch):
    for name in ENV_OVERRIDES.values():
        monkeypatch.delenv(name, raising=False)
    conn = ConnectionSettings.from_settings({}, require_credentials=False)
    assert conn.username == ""


def test_password_is_not_in_repr():
    conn = ConnectionSettings(username="u", password="hunter2", secret="s3cret")
    assert "hunter2" not in repr(conn) and "s3cret" not in repr(conn)


def test_bad_timeout_is_a_config_error():
    with pytest.raises(ConfigError, match="device.read_timeout"):
        ConnectionSettings.from_settings(
            {"device": {"username": "u", "password": "p", "read_timeout": "soon"}}
        )


def test_seed_cli_overrides_file():
    settings = {"seed": {"host": "10.0.0.1", "device_type": "cisco_ios"}}
    seed = SeedSettings.from_settings(settings, host="10.9.9.9", device_type="arista_eos")
    assert (seed.host, seed.device_type) == ("10.9.9.9", "arista_eos")
    assert SeedSettings.from_settings(settings).host == "10.0.0.1"


def test_seed_is_required():
    with pytest.raises(ConfigError, match="No seed device"):
        SeedSettings.from_settings({"seed": {"host": ""}})


def test_allowed_subnets_are_required():
    with pytest.raises(ConfigError, match="allowed_subnets is required"):
        CrawlConfig.from_settings({"max_depth": 3})
    with pytest.raises(ConfigError, match="allowed_subnets is required"):
        CrawlConfig.from_settings(None)


def test_allowed_subnets_can_come_from_the_cli():
    cfg = CrawlConfig.from_settings({}, extra_subnets=["192.0.2.0/24"])
    assert cfg.in_scope("192.0.2.9") and not cfg.in_scope("198.51.100.1")


def test_invalid_subnet_and_regex_are_config_errors():
    with pytest.raises(ConfigError, match="invalid subnet"):
        CrawlConfig.from_settings({"allowed_subnets": ["not-a-subnet"]})
    with pytest.raises(ConfigError, match="invalid"):
        CrawlConfig.from_settings({"allowed_subnets": ["10.0.0.0/8"], "exclude_hostnames": ["("]})


def test_limits_accept_null_and_cli_override():
    cfg = CrawlConfig.from_settings(
        {"allowed_subnets": ["10.0.0.0/8"], "max_depth": None, "max_devices": None}
    )
    assert cfg.max_depth is None and cfg.max_devices is None
    cfg = CrawlConfig.from_settings({"allowed_subnets": ["10.0.0.0/8"]}, max_depth=2)
    assert cfg.max_depth == 2
