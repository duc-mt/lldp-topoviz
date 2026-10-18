"""Shared fixtures and helpers for the test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.crawler import CrawlConfig
from src.graph_engine import GraphEngine
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


def make_campus(data_dir) -> GraphEngine:
    """A small campus with every case the failure simulation cares about.

    core1 -- core2, each linked to dist1 and dist2. acc1 is dual-homed (dist1, dist2); acc2
    hangs off dist1 only and acc3 off dist2 only (both single-homed); acc4 has two cables to
    dist1. acc1 carries 5 phones and two servers (srv1 single-homed, srv2 also wired to acc3),
    acc2 carries 3 access points, and SW-ACC-BLDG-B is a single-homed switch on core1 that the
    crawl could not enter.
    """
    engine = GraphEngine(data_dir=data_dir)

    def switch(name, depth, ip):
        node = engine.resolve(name=name, ips=[ip], capabilities=["bridge", "router"])
        engine.mark_crawled(node, host=ip, device_type="cisco_ios", depth=depth, hostname=name)
        return node

    def leaf(name, endpoint_class, **kw):
        node = engine.resolve(name=name, **kw)
        engine.set_endpoint_class(node, endpoint_class)
        return node

    core1, core2 = switch("core1", 0, "10.0.0.1"), switch("core2", 1, "10.0.0.2")
    dist1, dist2 = switch("dist1", 1, "10.0.1.1"), switch("dist2", 1, "10.0.1.2")
    acc1, acc2, acc3, acc4 = (switch(f"acc{i}", 2, f"10.0.2.{i}") for i in range(1, 5))
    engine.set_seed(core1)
    link = engine.add_link
    link(core1, "Gi1/0/1", core2, "Gi1/0/1")
    for i, dist in enumerate((dist1, dist2)):
        link(core1, f"Gi1/0/{10 + i}", dist, "Gi1/0/1")
        link(core2, f"Gi1/0/{10 + i}", dist, "Gi1/0/2")
    link(dist1, "Gi1/0/10", acc1, "Gi1/0/1")
    link(dist2, "Gi1/0/10", acc1, "Gi1/0/2")
    link(dist1, "Gi1/0/11", acc2, "Gi1/0/1")
    link(dist2, "Gi1/0/11", acc3, "Gi1/0/1")
    link(dist1, "Gi1/0/12", acc4, "Gi1/0/1")
    link(dist1, "Gi1/0/13", acc4, "Gi1/0/2")
    for i in range(5):
        phone = leaf(
            f"SEP00{i}",
            "endpoint_phone",
            chassis_id=f"00:11:22:33:44:{i:02x}",
            capabilities=["telephone", "bridge"],
        )
        link(acc1, f"Gi1/0/{20 + i}", phone, "P1")
    for i in range(3):
        link(acc2, f"Gi1/0/{30 + i}", leaf(f"ap{i}", "endpoint_ap", capabilities=["wlan"]), "eth0")
    srv1 = leaf("srv1", "endpoint_server", capabilities=["station"])
    srv2 = leaf("srv2", "endpoint_server", capabilities=["station"])
    link(acc1, "Gi1/0/40", srv1, "eth0")
    link(acc1, "Gi1/0/41", srv2, "eth0")
    link(acc3, "Gi1/0/40", srv2, "eth1")
    blind = engine.resolve(name="SW-ACC-BLDG-B", ips=["10.10.99.12"], capabilities=["bridge"])
    engine.mark_skipped(
        blind, "management IP 10.10.99.12 is outside crawl.allowed_subnets", "out_of_scope"
    )
    link(core1, "Gi1/0/24", blind, "Gi1/0/1")
    engine.annotate_bundles()
    engine.annotate_links()
    return engine
