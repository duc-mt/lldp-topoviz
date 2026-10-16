"""Shared fixtures and helpers for the test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.crawler import CrawlConfig
from src.parser import LldpParser
from src.poller import BasePoller, PollerError, RawLldp

DEMO_DIR = Path(__file__).resolve().parent.parent / "samples" / "demo"


@pytest.fixture
def demo_dir() -> Path:
    return DEMO_DIR


@pytest.fixture
def parser() -> LldpParser:
    return LldpParser()


def make_config(**overrides) -> CrawlConfig:
    """CrawlConfig for tests, built the same way as from settings.yaml."""
    settings = {"allowed_subnets": ["10.0.0.0/16"], **overrides}
    return CrawlConfig.from_settings(settings)


def row(local, name, port, ip="", caps="B,R", chassis="", desc=""):
    """One TextFSM-style row as the cisco_ios template would produce it."""
    return {
        "LOCAL_INTERFACE": local,
        "NEIGHBOR_NAME": name,
        "NEIGHBOR_PORT_ID": port,
        "NEIGHBOR_INTERFACE": "",
        "CHASSIS_ID": chassis,
        "MGMT_ADDRESS": ip,
        "NEIGHBOR_DESCRIPTION": desc,
        "CAPABILITIES": caps,
    }


class FakePoller(BasePoller):
    """Serves canned structured rows; an Exception value makes that host fail."""

    def __init__(self, devices: dict):
        self.devices = devices
        self.calls: list[str] = []

    def poll(self, host: str, device_type: str) -> RawLldp:
        self.calls.append(host)
        entry = self.devices.get(host)
        if entry is None:
            raise PollerError(f"{host}: unreachable")
        if isinstance(entry, Exception):
            raise entry
        hostname, rows = entry
        return RawLldp(
            host=host,
            device_type="cisco_ios",
            command="show lldp neighbors detail",
            collected_at="2026-10-10T00:00:00+00:00",
            hostname=hostname,
            rows=rows,
        )
