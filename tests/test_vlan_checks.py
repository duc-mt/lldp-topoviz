"""VLAN data model, parsing and the two-ended consistency checks."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.crawler import Crawler
from src.graph_engine import GraphEngine
from src.linkcheck import (
    KIND_VLAN_ALLOWED,
    KIND_VLAN_MODE,
    KIND_VLAN_NATIVE,
    SEVERITY_DEGRADED,
    SEVERITY_MISMATCH,
    LinkCheckConfig,
    check_vlan_consistency,
    is_vlan_warning,
)
from src.models import PortVlanInfo, format_vlan_list, parse_vlan_list
from src.parser import LldpParser
from src.poller import FilePoller
from src.vendors import PROFILES
from src.vendors.base import PortChannel, PortChannelMember
from src.vendors.vlan import parse_switchport

from .conftest import make_config

VLAN_DIR = Path(__file__).resolve().parent.parent / "samples" / "vlan"


def trunk(interface="Gi1", native=1, allowed=(10, 20, 30)):
    return PortVlanInfo(interface, "trunk", None, native, None if allowed is None else set(allowed))


def access(interface="Gi1", vlan=10):
    return PortVlanInfo(interface, "access", vlan)


# ------------------------------------------------------------------ VLAN lists
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1-10,20,30-40", set(range(1, 11)) | {20} | set(range(30, 41))),
        (" 5 - 7 , 9 ", {5, 6, 7, 9}),
        (["1-3", "7"], {1, 2, 3, 7}),  # the pieces TextFSM hands over for IOS
        ("40-38", {38, 39, 40}),  # a reversed range is still a range
        ("0,5000,10-12", {10, 11, 12}),  # IDs outside 1-4094 are dropped
        ("10,abc,20", {10, 20}),  # one odd piece never costs the rest
        ("NONE", set()),
        ("none", set()),
        ("ALL", set(range(1, 4095))),
        (["ALL"], set(range(1, 4095))),
    ],
)
def test_parse_vlan_list(text, expected):
    assert parse_vlan_list(text) == expected


@pytest.mark.parametrize("text", [None, "", "  ", "abc", [], "9999"])
def test_unparseable_lists_are_unknown_not_empty(text):
    assert parse_vlan_list(text) is None  # empty set would mean "this trunk carries nothing"


def test_format_vlan_list_uses_ranges():
    assert format_vlan_list({1, 2, 3, 7, 9, 10}) == "1-3,7,9-10"
    assert format_vlan_list(range(1, 4095)) == "1-4094"
    assert format_vlan_list(set()) == "none"
    assert format_vlan_list(None) == ""
    assert parse_vlan_list(format_vlan_list({4, 5, 6, 100})) == {4, 5, 6, 100}


def test_port_vlan_info_round_trips_through_json_form():
    info = PortVlanInfo("Gi1/0/1", "trunk", None, 99, {10, 11, 12, 40})
    data = info.to_dict()
    assert data["allowed_vlans"] == "10-12,40"  # compact, so snapshots stay small
    assert PortVlanInfo.from_dict(data) == info
    unknown = PortVlanInfo.from_dict({"interface": "Gi1", "mode": "weird", "native_vlan": "x"})
    assert (unknown.mode, unknown.native_vlan, unknown.allowed_vlans) == ("unknown", None, None)


# ---------------------------------------------------------------------- checks
def test_native_vlan_mismatch_is_critical():
    found = check_vlan_consistency(trunk(native=1), trunk(native=99), "core1", "dist1")
    assert [(w.kind, w.severity) for w in found] == [(KIND_VLAN_NATIVE, SEVERITY_MISMATCH)]
    assert "core1 Gi1" in found[0].text and "VLAN 1" in found[0].text and "VLAN 99" in found[0].text


@pytest.mark.parametrize("pair", [(trunk(), access()), (access(), trunk())])
def test_trunk_against_access_is_critical_in_either_direction(pair):
    found = check_vlan_consistency(*pair)
    assert [(w.kind, w.severity) for w in found] == [(KIND_VLAN_MODE, SEVERITY_MISMATCH)]
    assert "trunk" in found[0].text and "access" in found[0].text


def test_a_mode_mismatch_is_not_buried_under_secondary_findings():
    assert len(check_vlan_consistency(trunk(native=1), access())) == 1


def test_allowed_vlan_difference_is_degraded_and_names_the_vlans():
    found = check_vlan_consistency(
        trunk(allowed=(10, 20, 30, 40)), trunk("Eth1", allowed=(10, 20, 50)), "a", "b"
    )
    assert [(w.kind, w.severity) for w in found] == [(KIND_VLAN_ALLOWED, SEVERITY_DEGRADED)]
    text = found[0].text
    assert "VLAN 30,40 allowed on a Gi1 but not on b Eth1" in text
    assert "VLAN 50 allowed on b Eth1 but not on a Gi1" in text


def test_native_and_allowed_problems_are_both_reported():
    found = check_vlan_consistency(trunk(native=1, allowed=(10,)), trunk(native=2, allowed=(20,)))
    assert [w.kind for w in found] == [KIND_VLAN_NATIVE, KIND_VLAN_ALLOWED]


def test_matching_ends_are_clean_and_all_equals_the_full_range():
    assert check_vlan_consistency(trunk(), trunk()) == []
    everything = trunk(allowed=range(1, 4095))
    assert check_vlan_consistency(parse_switchport_all(), everything) == []
    assert check_vlan_consistency(access(vlan=10), access(vlan=10)) == []


def parse_switchport_all():
    return PortVlanInfo("Gi2", "trunk", None, 1, parse_vlan_list("ALL"))


@pytest.mark.parametrize(
    "pair",
    [
        (PortVlanInfo("a", "routed"), trunk()),
        (trunk(), PortVlanInfo("b", "unknown")),
        (trunk(native=None), trunk(native=5)),  # one end did not report a native VLAN
        (trunk(allowed=None), trunk(allowed=(1,))),  # nor an allowed list
        (access(vlan=10), access(vlan=20)),  # two access ports are outside the three rules
    ],
)
def test_what_cannot_be_judged_is_not_flagged(pair):
    assert check_vlan_consistency(*pair) == []


def test_labels_are_optional():
    assert check_vlan_consistency(trunk("Gi1", 1), trunk("Gi2", 2))[0].text.startswith(
        "Native VLAN mismatch: Gi1 uses VLAN 1 but Gi2 uses VLAN 2"
    )


def test_is_vlan_warning_only_matches_vlan_kinds():
    assert all(is_vlan_warning(k) for k in (KIND_VLAN_MODE, KIND_VLAN_NATIVE, KIND_VLAN_ALLOWED))
    assert not is_vlan_warning("speed_mismatch")


def test_link_check_config_vlan_switch():
    assert LinkCheckConfig.from_settings(None).vlan is True
    assert LinkCheckConfig.from_settings({"vlan": False}).vlan is False


# ----------------------------------------------------------------- normaliser
def test_parse_switchport_ios_rows():
    rows = [
        {"INTERFACE": "Gi1/0/1", "SWITCHPORT": "Enabled", "MODE": "trunk", "ADMIN_MODE": "trunk",
         "ACCESS_VLAN": "1", "NATIVE_VLAN": "99", "TRUNKING_VLANS": ["10-20", "30"]},
        {"INTERFACE": "Gi1/0/2", "SWITCHPORT": "Enabled", "MODE": "static access",
         "ADMIN_MODE": "static access", "ACCESS_VLAN": "10", "NATIVE_VLAN": "1",
         "TRUNKING_VLANS": ["ALL"]},
        {"INTERFACE": "Gi1/0/3", "SWITCHPORT": "Disabled", "MODE": "", "ADMIN_MODE": ""},
        {"INTERFACE": "Gi1/0/4", "SWITCHPORT": "Enabled", "MODE": "down",
         "ADMIN_MODE": "dynamic auto"},
        {"INTERFACE": "Gi1/0/5", "SWITCHPORT": "Enabled", "MODE": "down", "ADMIN_MODE": "trunk",
         "NATIVE_VLAN": "1", "TRUNKING_VLANS": ["ALL"]},
        {"INTERFACE": ""},
    ]
    ports = {p.interface: p for p in parse_switchport(rows)}
    assert len(ports) == 5
    assert ports["Gi1/0/1"] == PortVlanInfo("Gi1/0/1", "trunk", None, 99, set(range(10, 21)) | {30})
    assert ports["Gi1/0/2"] == PortVlanInfo("Gi1/0/2", "access", 10, None, None)  # no trunk data
    assert ports["Gi1/0/3"].mode == "routed"
    assert ports["Gi1/0/4"].mode == "unknown"  # dynamic and down: no definite mode
    assert ports["Gi1/0/5"].mode == "trunk"  # down, but statically a trunk


def test_parse_switchport_accepts_netmiko_lowercase_columns_and_nxos_strings():
    rows = [{"interface": "Ethernet1/1", "switchport": "Enabled", "mode": "trunk",
             "access_vlan": "1", "native_vlan": "99", "trunking_vlans": "1,10-12"}]
    (port,) = parse_switchport(rows)
    assert (port.mode, port.native_vlan, port.allowed_vlans) == ("trunk", 99, {1, 10, 11, 12})


def test_cisco_profiles_have_a_vlan_command_and_others_do_not():
    assert PROFILES["cisco_ios"].vlan.command == "show interfaces switchport"
    assert PROFILES["cisco_xe"].vlan.command == "show interfaces switchport"
    assert PROFILES["cisco_nxos"].vlan.command == "show interface switchport"
    assert PROFILES["arista_eos"].vlan is None
    assert PROFILES["cisco_ios"].vlan.resolve_template().is_file()  # the ntc template is shipped


# ------------------------------------------------- real captures, whole pipeline
def crawl_vlan_demo(tmp_path, **poller_args):
    engine = GraphEngine(data_dir=tmp_path)
    crawler = Crawler(
        FilePoller(VLAN_DIR, **poller_args),
        LldpParser(),
        engine,
        make_config(allowed_subnets=["10.0.0.0/16"]),
        "10.0.0.11",
        "cisco_ios",
    )
    return crawler.run(), engine


def link_warnings(engine):
    found = {}
    for u, v, data in engine.graph.edges(data=True):
        port = data["ports"][u] if engine.label(u) == "core1" else data["ports"][v]
        found[port] = [(w["kind"], w["severity"]) for w in data["warnings"]]
    return found


def test_vlan_demo_captures_produce_the_expected_findings(tmp_path):
    result, engine = crawl_vlan_demo(tmp_path)
    assert link_warnings(engine) == {
        "Gi1/0/1": [(KIND_VLAN_NATIVE, SEVERITY_MISMATCH)],
        "Gi1/0/2": [(KIND_VLAN_ALLOWED, SEVERITY_DEGRADED)],
        "Gi1/0/3": [(KIND_VLAN_MODE, SEVERITY_MISMATCH)],
        "Gi1/0/4": [],  # ALL on one end, 1-4094 on the other: identical
    }
    assert result.warned_links == 3


def test_snapshot_links_carry_each_ends_vlan_setup(tmp_path):
    _result, engine = crawl_vlan_demo(tmp_path)
    links = {link["source_port"] or link["target_port"]: link for link in engine.to_dict()["links"]}
    native = next(
        link for link in engine.to_dict()["links"] if "Gi1/0/1" in (link["source_port"], link["target_port"])
    )
    ends = {native["source"]: native["source_vlan"], native["target"]: native["target_vlan"]}
    assert ends["core1"]["native_vlan"] == 1 and ends["dist1"]["native_vlan"] == 99
    assert ends["core1"]["allowed_vlans"] == "10,20,30" and ends["dist1"]["mode"] == "trunk"
    assert links  # every link was written


def test_the_routed_port_and_unused_ports_are_not_stored(tmp_path):
    _result, engine = crawl_vlan_demo(tmp_path)
    ports = engine.graph.nodes["core1"]["vlan_ports"]
    assert sorted(ports) == ["gi1/0/1", "gi1/0/2", "gi1/0/3", "gi1/0/4"]  # only ports with links


def test_switching_vlan_collection_off_removes_findings(tmp_path):
    result, engine = crawl_vlan_demo(tmp_path, collect_vlan=False)
    assert result.warned_links == 0
    assert engine.graph.nodes["core1"]["vlan_ports"] == {}
    assert "switched off" in engine.graph.nodes["core1"]["vlan_note"]


def test_link_check_config_can_skip_only_the_vlan_comparison(tmp_path):
    _result, engine = crawl_vlan_demo(tmp_path)
    assert engine.annotate_links(LinkCheckConfig(vlan=False)) == 0
    assert engine.annotate_links(LinkCheckConfig()) == 3


# --------------------------------------------------------------- port-channels
def bundled_pair(tmp_path, *, with_po_data: bool):
    engine = GraphEngine(data_dir=tmp_path)
    a = engine.resolve(name="a", ips=["10.0.0.1"])
    b = engine.resolve(name="b", ips=["10.0.0.2"])
    for node in (a, b):
        engine.mark_crawled(node, host="x", device_type="cisco_ios", depth=0, hostname=node)
    for i in (1, 2, 3):
        engine.add_link(a, f"Gi1/0/{i}", b, f"Gi1/0/{i}")
    members = tuple(PortChannelMember(f"Gi1/0/{i}", "bundled") for i in (1, 2, 3))
    for node in (a, b):
        engine.set_port_channels(node, [PortChannel("Po1", "LACP", True, members)])
    ports = {f"gi1/0/{i}": trunk(f"Gi1/0/{i}", 1, (10,)) for i in (1, 2, 3)}
    other = {f"gi1/0/{i}": trunk(f"Gi1/0/{i}", 99, (10,)) for i in (1, 2, 3)}
    if with_po_data:
        ports["po1"], other["po1"] = trunk("Po1", 1, (10,)), trunk("Po1", 99, (10,))
    engine.set_vlan_info(a, ports)
    engine.set_vlan_info(b, other)
    engine.annotate_bundles()
    engine.annotate_links(LinkCheckConfig())
    return engine


def test_a_bundle_is_judged_once_on_its_port_channels(tmp_path):
    engine = bundled_pair(tmp_path, with_po_data=True)
    texts = {w["text"] for _u, _v, d in engine.graph.edges(data=True) for w in d["warnings"]}
    assert len(texts) == 1  # three member links, one finding, so the page lists it once
    assert "Po1" in next(iter(texts))


def test_without_port_channel_data_every_member_link_is_judged(tmp_path):
    engine = bundled_pair(tmp_path, with_po_data=False)
    counts = [len(d["warnings"]) for _u, _v, d in engine.graph.edges(data=True)]
    assert counts == [1, 1, 1]
