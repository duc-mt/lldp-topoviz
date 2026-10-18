import socket

import pytest

from src.crawler import CrawlConfig, Crawler, CrawlError
from src.graph_engine import STATUS_CRAWLED, STATUS_DISCOVERED, STATUS_FAILED, GraphEngine
from src.parser import LldpParser
from src.poller import FilePoller, PollerError

from .conftest import FakePoller, make_config, row


def crawl(devices, seed="10.0.0.1", tmp_path=None, **config):
    poller = FakePoller(devices)
    engine = GraphEngine(data_dir=tmp_path or "/tmp/lldp-test-unused")
    crawler = Crawler(poller, LldpParser(), engine, make_config(**config), seed, "cisco_ios")
    return crawler.run(), engine, poller


def statuses(engine):
    return {engine.label(n): a["status"] for n, a in engine.graph.nodes(data=True)}


def reason(engine, label):
    return next(
        a["skip_reason"] for n, a in engine.graph.nodes(data=True) if engine.label(n) == label
    )


# ---------------------------------------------------------------- demo replay
def test_demo_replay_end_to_end(demo_dir):
    engine = GraphEngine(data_dir="/tmp/lldp-test-unused")
    crawler = Crawler(
        FilePoller(demo_dir),
        LldpParser(),
        engine,
        make_config(max_devices=50),
        "10.0.0.1",
        "cisco_ios",
    )
    result = crawler.run()
    assert statuses(engine) == {
        "core1": STATUS_CRAWLED,
        "core2": STATUS_CRAWLED,
        "dist1": STATUS_CRAWLED,
        "acc1": STATUS_CRAWLED,
        "acc2": STATUS_CRAWLED,
        "acc3": STATUS_CRAWLED,
        "fw1": STATUS_FAILED,
        "ap-lobby": STATUS_DISCOVERED,
        "isp-edge": STATUS_DISCOVERED,
        "sw-legacy": STATUS_CRAWLED,
        "SEP001122334455": STATUS_DISCOVERED,
    }
    # 16 physical links: every one reported from both ends (or by both protocols) is stored once
    assert engine.graph.number_of_edges() == 16
    assert result.attempted == 8 and result.crawled == 7 and result.partial
    assert [h for h, _ in result.failures] == ["10.0.0.250"]
    assert "outside crawl.allowed_subnets" in reason(engine, "isp-edge")
    assert "no management IP" in reason(engine, "ap-lobby")
    assert engine.graph.nodes["core1"]["seed"] is True
    # 5 device pairs run over port-channels (10 links); the AOS-CX end is not known to the tool
    assert result.bundled_links == 10 and result.warned_links == 5
    core1 = engine.graph.nodes["core1"]["port_channels"]
    assert [(c["name"], c["protocol"]) for c in core1] == [("Po1", "LACP")]
    assert "no port-channel" in engine.graph.nodes["acc3"]["lag_note"]
    assert engine.graph.nodes["fw1"]["error_kind"] == "no_data"
    assert engine.graph.nodes["isp-edge"]["skip_code"] == "out_of_scope"
    assert engine.graph.nodes["ap-lobby"]["skip_code"] == "no_ip"
    assert engine.graph.nodes["sep001122334455"]["skip_code"] == "capabilities"


# ------------------------------------------------------------------ the loop
RING = {
    "10.0.0.1": ("a", [row("Gi1", "b", "Gi1", "10.0.0.2"), row("Gi2", "c", "Gi1", "10.0.0.3")]),
    "10.0.0.2": ("b", [row("Gi1", "a", "Gi1", "10.0.0.1"), row("Gi2", "c", "Gi2", "10.0.0.3")]),
    "10.0.0.3": ("c", [row("Gi1", "a", "Gi2", "10.0.0.1"), row("Gi2", "b", "Gi2", "10.0.0.2")]),
}


def test_a_loop_is_visited_once_per_device():
    result, engine, poller = crawl(RING)
    assert sorted(poller.calls) == ["10.0.0.1", "10.0.0.2", "10.0.0.3"]
    assert engine.graph.number_of_edges() == 3 and not result.partial


def test_failed_device_is_marked_and_the_crawl_moves_on():
    devices = {
        "10.0.0.1": ("a", [row("Gi1", "b", "Gi1", "10.0.0.2"), row("Gi2", "c", "Gi1", "10.0.0.3")]),
        "10.0.0.2": PollerError("Authentication failed for 10.0.0.2"),
        "10.0.0.3": ("c", [row("Gi1", "a", "Gi2", "10.0.0.1")]),
    }
    result, engine, _ = crawl(devices)
    assert statuses(engine) == {"a": STATUS_CRAWLED, "b": STATUS_FAILED, "c": STATUS_CRAWLED}
    assert engine.graph.nodes["b"]["error"].startswith("Authentication failed")
    assert engine.graph.has_edge("a", "b")  # the link to the failed device is still drawn
    assert result.partial and result.failures[0][0] == "10.0.0.2"


def test_unreachable_seed_raises_with_the_cause():
    with pytest.raises(CrawlError, match="seed device 10.0.0.1.*unreachable"):
        crawl({})


def test_device_reached_by_two_addresses_is_crawled_once():
    devices = {
        "10.0.0.1": ("a", [row("Gi1", "b", "Gi1", "10.0.0.2")]),
        # b answers on its LLDP address, and c advertises b's second address
        "10.0.0.2": ("b", [row("Gi1", "a", "Gi1", "10.0.0.1"), row("Gi2", "c", "Gi1", "10.0.0.3")]),
        "10.0.0.3": ("c", [row("Gi1", "b", "Gi2", "10.0.0.2")]),
    }
    result, engine, poller = crawl(devices)
    assert len(poller.calls) == 3 and engine.graph.number_of_nodes() == 3


def test_device_seen_under_two_lldp_names_is_merged_into_one_node():
    devices = {
        "10.0.0.1": (
            "a",
            [row("Gi1", "b", "Gi1", "10.0.0.2"), row("Gi2", "b-alt", "Gi2", "10.0.0.22")],
        ),
        "10.0.0.2": ("b", []),
        "10.0.0.22": ("b", []),  # the same device, advertised under another name and address
    }
    result, engine, _ = crawl(devices)
    assert result.duplicates == 1
    assert sorted(engine.graph.nodes) == ["a", "b"]
    assert engine.graph.nodes["b"]["status"] == STATUS_CRAWLED
    assert sorted(engine.graph.nodes["b"]["mgmt_ips"]) == ["10.0.0.2", "10.0.0.22"]
    assert engine.graph.number_of_edges() == 2  # a-b over Gi1 and over Gi2
    assert engine.resolve(name="b-alt") == "b"


# --------------------------------------------------------------- scope guard
def test_seed_outside_allowed_subnets_raises_crawl_error():
    devices = {"192.0.2.1": ("far", [])}
    with pytest.raises(CrawlError, match="Seed device 192.0.2.1 is outside crawl.allowed_subnets"):
        crawl(devices, seed="192.0.2.1")


def test_seed_with_mapped_ip_outside_allowed_subnets_raises():
    devices = {"192.0.2.1": ("far", [])}
    with pytest.raises(CrawlError, match="Seed device far-seed is outside crawl.allowed_subnets"):
        crawl(devices, seed="far-seed", name_to_ip={"far-seed": "192.0.2.1"})


def test_seed_hostname_resolves_outside_allowed_subnets_raises(monkeypatch):
    devices = {"192.0.2.1": ("far", [])}
    monkeypatch.setattr(socket, "gethostbyname", lambda host: "192.0.2.1")
    with pytest.raises(CrawlError, match="Seed device far-host is outside crawl.allowed_subnets"):
        crawl(devices, seed="far-host")


def test_seed_hostname_unresolvable_raises(monkeypatch):
    devices = {"10.0.0.1": ("core1", [])}

    def mock_gethostbyname(host):
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(socket, "gethostbyname", mock_gethostbyname)
    with pytest.raises(CrawlError, match="Could not resolve seed host 'bad-host' to verify scope"):
        crawl(devices, seed="bad-host")


def test_seed_hostname_resolves_in_scope_succeeds(monkeypatch):
    devices = {"10.0.0.1": ("core1", [])}
    monkeypatch.setattr(socket, "gethostbyname", lambda host: "10.0.0.1")
    result, engine, poller = crawl(devices, seed="core1.local")
    assert result.crawled == 1
    assert poller.calls == ["10.0.0.1"]
    assert statuses(engine) == {"core1": STATUS_CRAWLED}


def test_nothing_outside_allowed_subnets_is_contacted():
    devices = {
        "10.0.0.1": ("a", [row("Gi1", "far", "Gi1", "192.0.2.50")]),
        "192.0.2.50": ("far", []),
    }
    _, engine, poller = crawl(devices)
    assert poller.calls == ["10.0.0.1"]
    assert "192.0.2.50 is outside" in reason(engine, "far")


def test_second_advertised_address_inside_scope_is_used():
    devices = {
        "10.0.0.1": ("a", [row("Gi1", "b", "Gi1", "192.0.2.9 10.0.0.2")]),
        "10.0.0.2": ("b", []),
    }
    _, _, poller = crawl(devices)
    assert poller.calls == ["10.0.0.1", "10.0.0.2"]


def test_max_depth_limits_the_walk():
    chain = {
        "10.0.0.1": ("a", [row("Gi1", "b", "Gi1", "10.0.0.2")]),
        "10.0.0.2": ("b", [row("Gi1", "c", "Gi1", "10.0.0.3")]),
        "10.0.0.3": ("c", []),
    }
    _, engine, poller = crawl(chain, max_depth=1)
    assert poller.calls == ["10.0.0.1", "10.0.0.2"]
    assert "max_depth (1)" in reason(engine, "c")
    _, _, poller = crawl(chain, max_depth=0)
    assert poller.calls == ["10.0.0.1"]


def test_max_devices_stops_the_crawl_and_flags_it():
    devices = {
        "10.0.0.1": ("a", [row("Gi1", f"n{i}", "Gi1", f"10.0.0.{i + 10}") for i in range(5)]),
        **{f"10.0.0.{i + 10}": (f"n{i}", []) for i in range(5)},
    }
    result, engine, poller = crawl(devices, max_devices=3)
    assert len(poller.calls) == 3 and result.limit_hit and result.partial
    assert "max_devices limit (3)" in reason(engine, "n4")


def test_excluded_hostnames_are_drawn_but_not_crawled():
    devices = {
        "10.0.0.1": (
            "a",
            [row("Gi1", "ap-lobby", "e0", "10.0.0.50"), row("Gi2", "sw", "Gi1", "10.0.0.51")],
        ),
        "10.0.0.51": ("sw", []),
    }
    _, engine, poller = crawl(devices, exclude_hostnames=["^ap-"])
    assert poller.calls == ["10.0.0.1", "10.0.0.51"]
    assert "exclude_hostnames" in reason(engine, "ap-lobby")


def test_phones_and_aps_are_skipped_by_capability():
    devices = {
        "10.0.0.1": (
            "a",
            [
                row("Gi1", "phone", "p1", "10.0.0.60", caps="T"),
                row("Gi2", "ap", "e0", "10.0.0.61", caps="W"),
                row(
                    "Gi3", "mystery", "x", "10.0.0.62", caps=""
                ),  # unknown capabilities: still crawled
            ],
        ),
        "10.0.0.62": ("mystery", []),
    }
    _, engine, poller = crawl(devices)
    assert poller.calls == ["10.0.0.1", "10.0.0.62"]
    assert "capabilities (telephone)" in reason(engine, "phone")


def test_empty_require_capabilities_disables_the_filter():
    devices = {
        "10.0.0.1": ("a", [row("Gi1", "phone", "p1", "10.0.0.60", caps="T")]),
        "10.0.0.60": ("phone", []),
    }
    _, _, poller = crawl(devices, require_capabilities=[])
    assert "10.0.0.60" in poller.calls


def test_neighbor_without_management_ip_can_be_given_one():
    devices = {"10.0.0.1": ("a", [row("Gi1", "Edge-SW", "Gi1", "")]), "10.0.0.77": ("edge-sw", [])}
    _, engine, _ = crawl(devices)
    assert "no management IP" in reason(engine, "Edge-SW")
    _, _, poller = crawl(devices, name_to_ip={"edge-sw": "10.0.0.77"})
    assert "10.0.0.77" in poller.calls


def test_device_type_comes_from_rules_overrides_and_default():
    seen = {}

    class Spy(FakePoller):
        def poll(self, host, device_type):
            seen[host] = device_type
            return super().poll(host, device_type)

    devices = {
        "10.0.0.1": (
            "a",
            [
                row("Gi1", "nx", "e1", "10.0.0.2", desc="Cisco Nexus Operating System (NX-OS)"),
                row("Gi2", "weird", "e1", "10.0.0.3", desc="Acme OS"),
                row("Gi3", "forced", "e1", "10.0.0.4", desc="Acme OS"),
                row("Gi4", "custom", "e1", "10.0.0.5", desc="Acme OS 2"),
            ],
        ),
        "10.0.0.2": ("nx", []),
        "10.0.0.3": ("weird", []),
        "10.0.0.4": ("forced", []),
        "10.0.0.5": ("custom", []),
    }
    engine = GraphEngine(data_dir="/tmp/lldp-test-unused")
    cfg = make_config(
        device_overrides={"forced": {"device_type": "arista_eos"}},
        device_type_rules=[{"pattern": "acme os 2", "device_type": "huawei"}],
    )
    Crawler(Spy(devices), LldpParser(), engine, cfg, "10.0.0.1", "cisco_ios").run()
    assert seen["10.0.0.2"] == "cisco_nxos"  # built-in description rule
    assert seen["10.0.0.3"] == "cisco_ios"  # nothing matched: the seed's type
    assert seen["10.0.0.4"] == "arista_eos"  # explicit per-device override
    assert seen["10.0.0.5"] == "huawei"  # user rule (tried before the built-ins)


def test_platform_without_a_profile_is_shown_with_the_reason():
    devices = {
        "10.0.0.1": ("a", [row("Gi1", "fgt", "port1", "10.0.0.9", desc="Fortinet FortiOS 7.2")])
    }
    _, engine, poller = crawl(devices)
    assert poller.calls == ["10.0.0.1"]
    assert "no LLDP profile for device_type 'fortinet'" in reason(engine, "fgt")


def test_neighbor_that_resolves_to_the_device_itself_is_ignored():
    devices = {"10.0.0.1": ("a", [row("Gi1", "a", "Gi9", "10.0.0.1")])}
    _, engine, poller = crawl(devices)
    assert engine.graph.number_of_edges() == 0 and poller.calls == ["10.0.0.1"]


def test_raw_sink_receives_every_successful_poll(demo_dir):
    captured = []
    engine = GraphEngine(data_dir="/tmp/lldp-test-unused")
    Crawler(
        FilePoller(demo_dir),
        LldpParser(),
        engine,
        make_config(),
        "10.0.0.1",
        "cisco_ios",
        raw_sink=captured.append,
    ).run()
    assert sorted(r.host for r in captured) == [
        "10.0.0.1",
        "10.0.0.2",
        "10.0.0.30",
        "10.0.1.1",
        "10.0.2.1",
        "10.0.2.2",
        "10.0.2.3",
    ]


def test_failure_kind_reaches_the_node():
    devices = {
        "10.0.0.1": ("a", [row("Gi1", "b", "Gi1", "10.0.0.2"), row("Gi2", "c", "Gi1", "10.0.0.3")]),
        "10.0.0.2": PollerError("Authentication failed for 10.0.0.2", kind="auth"),
        "10.0.0.3": PollerError("Timed out talking to 10.0.0.3", kind="timeout"),
    }
    _, engine, _ = crawl(devices)
    assert engine.graph.nodes["b"]["error_kind"] == "auth"
    assert engine.graph.nodes["c"]["error_kind"] == "timeout"


def test_skip_codes_match_the_reasons():
    devices = {
        "10.0.0.1": (
            "a",
            [
                row("Gi1", "far", "x", "192.0.2.5"),
                row("Gi2", "noip", "x", ""),
                row("Gi3", "ap-1", "x", "10.0.0.8"),
                row("Gi4", "phone", "x", "10.0.0.9", caps="T"),
                row("Gi5", "fgt", "x", "10.0.0.10", desc="Fortinet FortiOS 7.2"),
            ],
        )
    }
    _, engine, _ = crawl(devices, exclude_hostnames=["^ap-"])
    codes = {engine.label(n): a["skip_code"] for n, a in engine.graph.nodes(data=True) if n != "a"}
    assert codes == {
        "far": "out_of_scope",
        "noip": "no_ip",
        "ap-1": "excluded",
        "phone": "capabilities",
        "fgt": "no_profile",
    }


def test_depth_and_device_limits_have_codes():
    chain = {
        "10.0.0.1": ("a", [row("Gi1", "b", "Gi1", "10.0.0.2")]),
        "10.0.0.2": ("b", [row("Gi1", "c", "Gi1", "10.0.0.3")]),
    }
    _, engine, _ = crawl(chain, max_depth=1)
    assert engine.graph.nodes["c"]["skip_code"] == "max_depth"


# ------------------------------------------------- CDP, LLDP-less devices, link checks
def cdp_row(local, name, port, ip="", platform="", caps="Router Switch IGMP", desc=""):
    return {
        "LOCAL_INTERFACE": local,
        "NEIGHBOR_NAME": name,
        "NEIGHBOR_INTERFACE": port,
        "MGMT_ADDRESS": ip,
        "PLATFORM": platform,
        "CAPABILITIES": caps,
        "NEIGHBOR_DESCRIPTION": desc,
    }


class MultiPoller(FakePoller):
    """Serves canned LLDP rows, CDP rows and interface rows per host.

    ``devices[host]`` is a dict: hostname, lldp (rows or None), cdp (rows or None), status (rows or None).
    """

    def poll(self, host, device_type):
        from src.poller import ProtocolData, RawLldp

        self.calls.append(host)
        entry = self.devices.get(host)
        if entry is None:
            raise PollerError(f"{host}: unreachable")
        extras = {"cdp": ProtocolData(rows=entry["cdp"])} if entry.get("cdp") is not None else {}
        status = entry.get("status")
        return RawLldp(
            host=host,
            device_type="cisco_ios",
            command="show lldp neighbors detail",
            collected_at="now",
            hostname=entry["hostname"],
            rows=entry.get("lldp"),
            lldp_note="" if entry.get("lldp") is not None else "LLDP is not available",
            extras=extras,
            status_rows=status,
            status_note="" if status is not None else "no status",
        )


def crawl_multi(devices, seed="10.0.0.1", **config):
    engine = GraphEngine(data_dir="/tmp/lldp-test-unused")
    crawler = Crawler(
        MultiPoller(devices), LldpParser(), engine, make_config(**config), seed, "cisco_ios"
    )
    return crawler.run(), engine


def test_cdp_finds_a_neighbor_that_lldp_does_not_see():
    devices = {
        "10.0.0.1": {
            "hostname": "a",
            "lldp": [row("Gi1", "b", "Gi1", "10.0.0.2")],
            "cdp": [
                cdp_row("GigabitEthernet1", "b", "GigabitEthernet1", "10.0.0.2"),
                cdp_row("GigabitEthernet2", "old", "Gi0/1", "10.0.0.3", platform="cisco WS-C2960"),
            ],
        },
        "10.0.0.2": {"hostname": "b", "lldp": []},
        "10.0.0.3": {
            "hostname": "old",
            "lldp": None,
            "cdp": [cdp_row("Gi0/1", "a", "Gi2", "10.0.0.1")],
        },
    }
    result, engine = crawl_multi(devices)
    assert statuses(engine) == {"a": STATUS_CRAWLED, "b": STATUS_CRAWLED, "old": STATUS_CRAWLED}
    sources = {
        tuple(sorted((engine.label(u), engine.label(v)))): sorted(d["sources"])
        for u, v, d in engine.graph.edges(data=True)
    }
    assert sources == {("a", "b"): ["cdp", "lldp"], ("a", "old"): ["cdp"]}
    assert (
        engine.graph.number_of_edges() == 2
    )  # the link seen from both ends and by both protocols: once
    assert engine.graph.nodes["old"]["platform"] == "cisco WS-C2960"


def test_a_device_whose_lldp_is_unavailable_is_crawled_through_cdp_and_says_so():
    devices = {
        "10.0.0.1": {
            "hostname": "a",
            "lldp": None,
            "cdp": [cdp_row("Gi1", "b", "Gi1", "10.0.0.2")],
        },
        "10.0.0.2": {"hostname": "b", "lldp": []},
    }
    result, engine = crawl_multi(devices)
    attrs = engine.graph.nodes["a"]
    assert attrs["status"] == STATUS_CRAWLED and attrs["discovery"] == ["cdp"]
    assert attrs["protocol_notes"] == {"lldp": "LLDP is not available"}
    assert not result.partial and engine.graph.nodes["b"]["discovery"] == ["lldp"]


def test_a_device_with_no_usable_protocol_is_a_failure():
    devices = {"10.0.0.1": {"hostname": "a", "lldp": None, "cdp": None}}
    with pytest.raises(CrawlError, match="no discovery protocol could be read|no output"):
        crawl_multi(devices)


def test_interface_status_flows_into_link_warnings():
    devices = {
        "10.0.0.1": {
            "hostname": "a",
            "lldp": [row("Gi1/0/1", "b", "Gi1/0/1", "10.0.0.2")],
            "status": [
                {"port": "Gi1/0/1", "status": "connected", "duplex": "a-full", "speed": "a-100"}
            ],
        },
        "10.0.0.2": {
            "hostname": "b",
            "lldp": [row("Gi1/0/1", "a", "Gi1/0/1", "10.0.0.1")],
            "status": [
                {"port": "Gi1/0/1", "status": "connected", "duplex": "a-full", "speed": "a-100"}
            ],
        },
    }
    result, engine = crawl_multi(devices)
    assert result.warned_links == 1
    ((_, _, data),) = engine.graph.edges(data=True)
    assert [w["kind"] for w in data["warnings"]] == ["below_nominal"]
    assert data["oper"][next(iter(data["oper"]))]["speed"] == 100


def test_link_checks_can_be_ignored_by_port_name():
    devices = {
        "10.0.0.1": {
            "hostname": "a",
            "lldp": [row("Fa0/5", "ph", "p1", "10.0.0.9", caps="B")],
            "status": [{"port": "Fa0/5", "status": "connected", "duplex": "half", "speed": "10"}],
        },
        "10.0.0.9": {"hostname": "ph", "lldp": []},
    }
    engine = GraphEngine(data_dir="/tmp/lldp-test-unused")
    cfg = CrawlConfig.from_settings(
        {"allowed_subnets": ["10.0.0.0/16"]}, link_checks={"ignore_ports": ["^Fa"]}
    )
    result = Crawler(MultiPoller(devices), LldpParser(), engine, cfg, "10.0.0.1", "cisco_ios").run()
    assert result.warned_links == 0


def test_the_discovery_protocols_are_configurable():
    cfg = CrawlConfig.from_settings({"allowed_subnets": ["10.0.0.0/8"]})
    assert (
        cfg.protocols == ("lldp", "cdp")
        and cfg.collect_interface_status
        and cfg.link_checks.enabled
    )
    cfg = CrawlConfig.from_settings(
        {"allowed_subnets": ["10.0.0.0/8"], "discovery": ["CDP", "lldp", "cdp"]}
    )
    assert cfg.protocols == ("cdp", "lldp")
    cfg = CrawlConfig.from_settings({"allowed_subnets": ["10.0.0.0/8"]}, protocols=["lldp"])
    assert cfg.protocols == ("lldp",)
    from src.config import ConfigError

    with pytest.raises(ConfigError, match="at least one protocol"):
        CrawlConfig.from_settings(
            {"allowed_subnets": ["10.0.0.0/8"], "discovery": []}, protocols=[" "]
        )


def test_a_cdp_only_phone_is_classified_by_its_platform():
    devices = {
        "10.0.0.1": {
            "hostname": "a",
            "lldp": None,
            "cdp": [
                cdp_row(
                    "Fa0/5",
                    "SEP0011",
                    "Port 1",
                    "10.0.0.9",
                    platform="Cisco IP Phone 7960",
                    caps="Host Phone",
                )
            ],
        }
    }
    _, engine = crawl_multi(devices)
    assert engine.graph.nodes["sep0011"]["skip_code"] == "capabilities"
    assert engine.graph.nodes["sep0011"]["capabilities"] == ["station", "telephone"]


# -------------------------------------------------------- multi-IP fallback
def test_multi_ip_neighbor_falls_back_to_second_ip_on_failure():
    devices = {
        "10.0.0.1": ("a", [row("Gi1", "b", "Gi1", "10.0.0.2 10.0.0.22")]),
        "10.0.0.2": PollerError("Connection timed out talking to 10.0.0.2"),
        "10.0.0.22": ("b", []),
    }
    result, engine, poller = crawl(devices)
    assert poller.calls == ["10.0.0.1", "10.0.0.2", "10.0.0.22"]
    assert statuses(engine) == {"a": STATUS_CRAWLED, "b": STATUS_CRAWLED}
    assert result.crawled == 2
    assert not result.partial
    assert not result.failures


def test_multi_ip_neighbor_marks_failed_if_all_ips_fail():
    devices = {
        "10.0.0.1": ("a", [row("Gi1", "b", "Gi1", "10.0.0.2 10.0.0.22")]),
        "10.0.0.2": PollerError("Connection timed out talking to 10.0.0.2"),
        "10.0.0.22": PollerError("Connection timed out talking to 10.0.0.22"),
    }
    result, engine, poller = crawl(devices)
    assert poller.calls == ["10.0.0.1", "10.0.0.2", "10.0.0.22"]
    assert statuses(engine) == {"a": STATUS_CRAWLED, "b": STATUS_FAILED}
    assert result.crawled == 1
    assert result.partial
    assert len(result.failures) == 1
    assert result.failures[0][0] == "10.0.0.22"


def test_multi_ip_neighbor_fallback_respects_max_devices():
    devices = {
        "10.0.0.1": ("a", [row("Gi1", "b", "Gi1", "10.0.0.2 10.0.0.22")]),
        "10.0.0.2": PollerError("Timeout"),
        "10.0.0.22": ("b", []),
    }
    result, engine, poller = crawl(devices, max_devices=2)
    assert poller.calls == ["10.0.0.1", "10.0.0.2"]
    assert result.limit_hit
    assert "max_devices limit (2)" in reason(engine, "b")


# ------------------------------------------------------------------- leaf guard
LEAVES = [
    # name, LLDP capabilities, description, expected class
    ("phone-1", "T", "", "endpoint_phone"),
    (
        "phone-2",
        "B,T",
        "Cisco IP Phone 8841",
        "endpoint_phone",
    ),  # PC-port phone: bridge + telephone
    ("ap-1", "W", "", "endpoint_ap"),
    ("ap-2", "B,R", "Cisco Aironet 2802 Access Point", "endpoint_ap"),
    ("srv-1", "S", "", "endpoint_server"),
    ("cam-1", "", "AXIS P3245 Network Camera", "endpoint_generic"),
    ("hub-1", "O", "", "endpoint_generic"),
]


def leaf_devices():
    rows = [
        row(f"Gi1/0/{i}", name, "p1", f"10.0.0.{50 + i}", caps=caps, desc=desc)
        for i, (name, caps, desc, _cls) in enumerate(LEAVES, start=1)
    ]
    # every leaf answers on SSH in this fake network: only the guard keeps the crawler away
    devices = {f"10.0.0.{50 + i}": (name, []) for i, (name, *_rest) in enumerate(LEAVES, start=1)}
    return {"10.0.0.1": ("a", rows), **devices}


def test_leaf_endpoints_are_drawn_but_never_logged_in_to():
    result, engine, poller = crawl(leaf_devices())
    assert poller.calls == ["10.0.0.1"]
    assert result.attempted == 1 and not result.partial
    for name, _caps, _desc, expected in LEAVES:
        attrs = engine.graph.nodes[name]
        assert attrs["endpoint_class"] == expected, name
        assert attrs["status"] == STATUS_DISCOVERED and attrs["skip_code"] == "capabilities"
        assert engine.graph.has_edge("a", name)  # still part of the diagram


def test_leaf_guard_reason_names_the_class_and_the_evidence():
    _, engine, _ = crawl(leaf_devices())
    assert "leaf endpoint (IP phone, from bridge, telephone): never logged in to" in reason(
        engine, "phone-2"
    )
    assert "capabilities (telephone) do not include any of" in reason(engine, "phone-1")
    assert "leaf endpoint (end device, from platform / description)" in reason(engine, "cam-1")


def test_leaf_endpoints_are_never_blind_spots():
    # out of scope, no address, excluded: whatever else is true of them, a phone has no branch
    devices = {
        "10.0.0.1": (
            "a",
            [
                row("Gi1", "far-phone", "p1", "192.0.2.60", caps="T"),
                row("Gi2", "noip-ap", "e0", "", caps="B,R", desc="Cisco Aironet Access Point"),
            ],
        )
    }
    result, engine, _ = crawl(devices)
    assert engine.graph.nodes["far-phone"]["endpoint_class"] == "endpoint_phone"
    assert engine.graph.nodes["noip-ap"]["endpoint_class"] == "endpoint_ap"
    assert result.blind_spots == []


def test_unknown_capabilities_are_still_crawled():
    devices = {
        "10.0.0.1": ("a", [row("Gi1", "mystery", "x", "10.0.0.62", caps="")]),
        "10.0.0.62": ("mystery", []),
    }
    _, engine, poller = crawl(devices)
    assert poller.calls == ["10.0.0.1", "10.0.0.62"]
    assert engine.graph.nodes["mystery"]["endpoint_class"] == ""


def test_the_guard_keeps_the_existing_explicit_opt_outs():
    devices = {
        "10.0.0.1": ("a", [row("Gi1", "phone", "p1", "10.0.0.60", caps="B,T")]),
        "10.0.0.60": ("phone", []),
    }
    _, _, poller = crawl(devices)
    assert poller.calls == ["10.0.0.1"]
    _, _, poller = crawl(devices, require_capabilities=[])  # the documented switch-off
    assert "10.0.0.60" in poller.calls
    _, _, poller = crawl(devices, device_overrides={"phone": {"host": "10.0.0.60"}})
    assert "10.0.0.60" in poller.calls


def test_a_crawled_device_is_never_relabelled_a_leaf():
    devices = {
        "10.0.0.1": ("a", [row("Gi1", "b", "Gi1", "10.0.0.2", caps="B")]),
        "10.0.0.2": (
            "b",
            [row("Gi1", "a", "Gi1", "10.0.0.1", caps="B,T")],
        ),  # odd, but b is crawled
    }
    _, engine, _ = crawl(devices)
    assert statuses(engine) == {"a": STATUS_CRAWLED, "b": STATUS_CRAWLED}
    assert engine.graph.nodes["a"]["endpoint_class"] == ""


# ------------------------------------------------------------------ blind spots
def spot(result, name):
    return next(b for b in result.blind_spots if b.hostname == name)


def blind_spot_network():
    return {
        "10.0.0.1": (
            "core",
            [
                row("Gi1/0/24", "far", "Gi1", "192.0.2.5", chassis="00:1a:2b:3c:4d:5e"),
                row("Gi1/0/2", "locked", "Gi1", "10.0.0.2"),
                row("Gi1/0/3", "slow", "Gi1", "10.0.0.3"),
                row("Gi1/0/4", "refused", "Gi1", "10.0.0.4"),
                row("Gi1/0/5", "fgt", "port1", "10.0.0.5", desc="Fortinet FortiOS 7.2"),
                row("Gi1/0/6", "noip", "Gi1", ""),
                row("Gi1/0/7", "ap-1", "Gi1", "10.0.0.7"),
                row("Gi1/0/8", "broken", "Gi1", "10.0.0.8"),
                row("Gi1/0/9", "phone", "p1", "10.0.0.9", caps="T"),
            ],
        ),
        "10.0.0.2": PollerError("auth failed for 10.0.0.2", kind="auth"),
        "10.0.0.3": PollerError("timed out talking to 10.0.0.3", kind="timeout"),
        "10.0.0.4": PollerError("connection refused by 10.0.0.4", kind="connect"),
        "10.0.0.8": PollerError("10.0.0.8 rejected the command", kind="rejected"),
    }


def test_every_kind_of_uncrawled_infrastructure_becomes_a_blind_spot():
    result, _, _ = crawl(blind_spot_network(), exclude_hostnames=["^ap-"])
    reasons = {b.hostname: b.reason for b in result.blind_spots}
    assert reasons == {
        "far": "OUT_OF_SCOPE",
        "locked": "AUTH_FAILURE",
        "slow": "TIMEOUT",
        "refused": "CONNECT_FAILURE",
        "fgt": "UNSUPPORTED_OS",
        "noip": "NO_MGMT_IP",
        "ap-1": "EXCLUDED",
        "broken": "CRAWL_ERROR",
    }  # the phone is not here


def test_a_blind_spot_records_who_saw_it_where_and_why():
    result, _, _ = crawl(blind_spot_network())
    far = spot(result, "far")
    assert far.chassis_id == "00:1a:2b:3c:4d:5e"
    assert far.management_ip == "192.0.2.5"
    assert (far.discovered_via, far.parent_port) == ("core", "Gi1/0/24")
    assert "192.0.2.5 is outside crawl.allowed_subnets" in far.detail
    assert (far.node_id, far.parent_id) == ("far", "core")
    locked = spot(result, "locked")
    assert locked.management_ip == "10.0.0.2"  # the address the crawler tried
    assert locked.detail == "auth failed for 10.0.0.2"
    assert spot(result, "noip").management_ip is None


def test_blind_spots_are_listed_by_parent_port_and_name():
    result, _, _ = crawl(blind_spot_network())
    ports = [b.parent_port for b in result.blind_spots]
    assert ports == sorted(ports)


def test_the_blind_spot_names_the_crawled_switch_closest_to_the_seed():
    devices = {
        "10.0.0.1": ("a", [row("Gi1", "b", "Gi1", "10.0.0.2")]),
        "10.0.0.2": (
            "b",
            [row("Gi1", "a", "Gi1", "10.0.0.1"), row("Gi2", "c", "Gi1", "10.0.0.3")],
        ),
        "10.0.0.3": (
            "c",
            [row("Gi1", "b", "Gi2", "10.0.0.2"), row("Gi2", "far", "Gi1", "192.0.2.9")],
        ),
    }
    devices["10.0.0.2"][1].append(row("Gi3", "far", "Gi2", "192.0.2.9"))  # b sees it too
    result, _, _ = crawl(devices)
    far = spot(result, "far")
    assert (far.discovered_via, far.parent_port) == ("b", "Gi3")  # depth 1 beats c at depth 2


def test_a_device_skipped_from_one_switch_but_crawled_via_another_is_not_a_blind_spot():
    devices = {
        "10.0.0.1": (
            "a",
            [row("Gi1", "b", "Gi1", "10.0.0.2"), row("Gi2", "x", "Gi1", "192.0.2.9")],
        ),
        "10.0.0.2": ("b", [row("Gi1", "a", "Gi1", "10.0.0.1"), row("Gi2", "x", "Gi2", "10.0.0.9")]),
        "10.0.0.9": ("x", []),
    }
    result, engine, _ = crawl(devices)
    assert statuses(engine)["x"] == STATUS_CRAWLED
    assert result.blind_spots == []


def test_depth_and_device_limits_are_blind_spots_too():
    chain = {
        "10.0.0.1": ("a", [row("Gi1", "b", "Gi1", "10.0.0.2")]),
        "10.0.0.2": ("b", [row("Gi1", "c", "Gi1", "10.0.0.3")]),
    }
    result, _, _ = crawl(chain, max_depth=1)
    assert [(b.hostname, b.reason) for b in result.blind_spots] == [("c", "LIMIT_REACHED")]

    many = {
        "10.0.0.1": ("a", [row("Gi1", f"n{i}", "Gi1", f"10.0.0.{i + 10}") for i in range(5)]),
        **{f"10.0.0.{i + 10}": (f"n{i}", []) for i in range(5)},
    }
    result, engine, _ = crawl(many, max_devices=3)
    assert {b.hostname for b in result.blind_spots} == {"n2", "n3", "n4"}
    assert {b.reason for b in result.blind_spots} == {"LIMIT_REACHED"}
    assert engine.graph.nodes["n4"]["skip_code"] == "max_devices"


def test_an_out_of_scope_neighbor_is_a_blind_spot_but_not_a_failed_crawl():
    devices = {"10.0.0.1": ("a", [row("Gi1", "far", "Gi1", "192.0.2.50")])}
    result, _, _ = crawl(devices)
    assert [b.reason for b in result.blind_spots] == ["OUT_OF_SCOPE"]
    assert not result.partial  # exit codes are unchanged by this report


def test_a_fully_mapped_network_has_no_blind_spots():
    result, _, _ = crawl(RING)
    assert result.blind_spots == []


def test_blind_spots_of_the_bundled_demo(demo_dir):
    engine = GraphEngine(data_dir="/tmp/lldp-test-unused")
    result = Crawler(
        FilePoller(demo_dir),
        LldpParser(),
        engine,
        make_config(max_devices=50),
        "10.0.0.1",
        "cisco_ios",
    ).run()
    assert [
        (b.hostname, b.reason, b.discovered_via, b.parent_port, b.management_ip)
        for b in result.blind_spots
    ] == [
        ("ap-lobby", "NO_MGMT_IP", "acc1", "Ethernet5", None),
        ("fw1", "CRAWL_ERROR", "core1", "Gi1/0/10", "10.0.0.250"),
        ("isp-edge", "OUT_OF_SCOPE", "core1", "Gi1/0/48", "203.0.113.1"),
    ]
    assert engine.graph.nodes["sep001122334455"]["endpoint_class"] == "endpoint_phone"


def test_a_leaf_verdict_from_one_switch_is_not_overturned_by_another_switchs_report():
    devices = {
        "10.0.0.1": (
            "a",
            [row("Gi1", "b", "Gi1", "10.0.0.2"), row("Gi2", "dev", "p1", "10.0.0.9", caps="T")],
        ),
        "10.0.0.2": (
            "b",
            [row("Gi1", "a", "Gi1", "10.0.0.1"), row("Gi2", "dev", "eth0", "10.0.0.9", caps="B,R")],
        ),
        "10.0.0.9": ("dev", []),
    }
    result, engine, poller = crawl(devices)
    assert (
        "10.0.0.9" not in poller.calls
    )  # b calls it a router, a called it a phone: not logged in to
    assert engine.graph.nodes["dev"]["endpoint_class"] == "endpoint_phone"
    assert "another switch's report" in reason(engine, "dev")
    assert result.blind_spots == []


def test_dns_resolution_is_cached(monkeypatch):
    calls = []

    def mock_gethostbyname(host):
        calls.append(host)
        return "10.0.0.1"

    monkeypatch.setattr(socket, "gethostbyname", mock_gethostbyname)
    poller = FakePoller({"10.0.0.1": ("core1", [])})
    engine = GraphEngine(data_dir="/tmp/lldp-test-unused")
    crawler = Crawler(poller, LldpParser(), engine, make_config(), "core1.local", "cisco_ios")

    # First resolution uses socket.gethostbyname and populates cache
    assert crawler._resolve_host("core1.local") == "10.0.0.1"
    assert calls == ["core1.local"]

    # Second resolution uses cache without calling socket.gethostbyname again
    assert crawler._resolve_host("core1.local") == "10.0.0.1"
    assert len(calls) == 1

    # Running crawl also uses the cache
    result = crawler.run()
    assert result.crawled == 1
    assert len(calls) == 1


def test_ipv6_allowed_subnet_logs_warning(caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        cfg = CrawlConfig.from_settings({"allowed_subnets": ["2001:db8::/32", "10.0.0.0/8"]})
    assert any("contains IPv6 network(s)" in r.message for r in caplog.records)
    assert len(cfg.allowed_subnets) == 2
