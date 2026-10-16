import re

import pytest

from src.config import ConfigError
from src.vendors import (
    PROFILES,
    UnsupportedVendorError,
    get_profile,
    guess_device_type,
    is_supported,
    load_custom_vendors,
    row_to_neighbor,
)
from src.vendors.base import FieldMap, ParserError, normalize_capabilities


def test_builtin_profiles_resolve_their_templates():
    for device_type in (
        "cisco_ios",
        "cisco_xe",
        "cisco_nxos",
        "cisco_xr",
        "arista_eos",
        "aruba_aoscx",
        "huawei",
        "huawei_vrp",
        "huawei_vrpv8",
        "juniper_junos",
        "vyos",
    ):
        profile = get_profile(device_type)
        assert profile.resolve_template().is_file(), device_type
        assert profile.command


def test_unsupported_platforms_explain_why():
    with pytest.raises(UnsupportedVendorError, match="per-port"):
        get_profile("fortinet")
    with pytest.raises(UnsupportedVendorError, match="Supported:"):
        get_profile("pfsense")
    assert not is_supported("fortinet") and is_supported("arista_eos")
    assert is_supported("vyos") and is_supported("juniper_junos")


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("Cisco Nexus Operating System (NX-OS) Software 9.3(8)", "cisco_nxos"),
        ("Cisco IOS XR Software, Version 7.9.2", "cisco_xr"),
        ("Cisco IOS Software [Cupertino], Catalyst L3 Switch Software (CAT9K_IOSXE)", "cisco_ios"),
        ("Arista Networks EOS version 4.28.3M", "arista_eos"),
        ("ArubaOS-CX 6200F 10.10.1010", "aruba_aoscx"),
        ("Huawei Versatile Routing Platform Software", "huawei"),
        ("Juniper Networks, Inc. mx204 Junos 22.4R2", "juniper_junos"),
        ("VyOS 1.3-rolling-201912230217", "vyos"),
        ("pfSense 2.7.2-RELEASE", None),
        ("", None),
    ],
)
def test_guess_device_type(description, expected):
    assert guess_device_type(description) == expected


def test_user_rules_win_over_builtin_rules():
    rules = [(re.compile("catalyst", re.I), "cisco_xe")]
    assert guess_device_type("Catalyst 9300", rules) == "cisco_xe"


def test_capability_normalisation():
    assert normalize_capabilities("B,R") == {"bridge", "router"}
    assert normalize_capabilities("Bridge, Router") == {"bridge", "router"}
    assert normalize_capabilities(["W", "wlan-access-point"]) == {"wlan"}
    assert normalize_capabilities("not advertised") == frozenset()
    assert normalize_capabilities("") == frozenset()


def test_row_to_neighbor_uses_column_fallbacks_and_skips_macs_as_interfaces():
    fields = get_profile("cisco_ios").fields
    neighbor = row_to_neighbor(
        {
            "LOCAL_INTERFACE": "Gi1/0/1",
            "NEIGHBOR_NAME": "sw2.lab",
            "NEIGHBOR_PORT_ID": "0011.2233.4455",  # MAC-style Port ID
            "NEIGHBOR_INTERFACE": "GigabitEthernet0/2",
            "MGMT_ADDRESS": "10.0.0.2",
            "CAPABILITIES": "B,R",
        },
        fields,
    )
    assert neighbor.neighbor_interface == "GigabitEthernet0/2"
    assert neighbor.mgmt_ips == ("10.0.0.2",)
    assert neighbor.capabilities == {"bridge", "router"}


def test_row_without_identity_is_dropped_and_ipv6_mgmt_is_ignored():
    fields = FieldMap()
    assert row_to_neighbor({"LOCAL_INTERFACE": "Gi1"}, fields) is None
    neighbor = row_to_neighbor({"NEIGHBOR_NAME": "x", "MGMT_ADDRESS": "fe80::1"}, fields)
    assert neighbor.mgmt_ips == ()


def test_xr_profile_maps_its_own_column_names():
    neighbor = row_to_neighbor(
        {
            "LOCAL_INTERFACE": "Hu0/0/0/1",
            "NEIGHBOR": "pe1",
            "NEIGHBOR_PORT_ID": "Hu0/0/0/2",
            "MANAGEMENT_IP": "10.0.0.9",
            "SYSTEM_DESCRIPTION": "Cisco IOS XR",
            "CAPABILITIES": "R",
        },
        get_profile("cisco_xr").fields,
    )
    assert (neighbor.neighbor_name, neighbor.mgmt_ips) == ("pe1", ("10.0.0.9",))


def test_field_map_rejects_unknown_fields():
    with pytest.raises(ParserError, match="unknown field"):
        FieldMap.from_mapping({"mgmt": "X"}, "custom_vendors.x")


def _custom_entry(tmp_path):
    template = tmp_path / "templates" / "junos_mgmt.textfsm"
    template.parent.mkdir()
    template.write_text(
        "Value LOCAL_INTERFACE (\\S+)\nValue NBR_NAME (\\S+)\n"
        "Value NBR_IP (\\d+\\.\\d+\\.\\d+\\.\\d+)\n\n"
        "Start\n  ^${LOCAL_INTERFACE}\\s+${NBR_NAME}\\s+${NBR_IP}\\s*$$ -> Record\n"
    )
    return {
        "juniper_junos": {
            "command": "show lldp neighbors",
            "template": "templates/junos_mgmt.textfsm",
            "fields": {
                "local_interface": "LOCAL_INTERFACE",
                "neighbor_name": "NBR_NAME",
                "mgmt_address": "NBR_IP",
            },
            "error_markers": ["syntax error"],
        }
    }


def test_custom_vendor_registers_and_parses_text(tmp_path):
    from src.parser import LldpParser
    from src.poller import RawLldp

    saved = PROFILES.copy()
    try:
        assert load_custom_vendors(_custom_entry(tmp_path), base_dir=tmp_path) == ["juniper_junos"]
        assert is_supported("juniper_junos")
        raw = RawLldp(
            host="10.0.0.9",
            device_type="juniper_junos",
            command="show lldp neighbors",
            collected_at="now",
            text="xe-0/0/0 core1 10.0.0.1\nxe-0/0/1 core2 10.0.0.2\n",
        )
        parsed = LldpParser().parse(raw)
        assert [(n.local_interface, n.neighbor_name, n.mgmt_ips) for n in parsed.neighbors] == [
            ("xe-0/0/0", "core1", ("10.0.0.1",)),
            ("xe-0/0/1", "core2", ("10.0.0.2",)),
        ]
    finally:
        PROFILES.clear()
        PROFILES.update(saved)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda e: e.pop("command"), "missing command"),
        (lambda e: e.update(template="templates/absent.textfsm"), "template file not found"),
        (lambda e: e.update(fields={"bogus": "X"}), "unknown field"),
        (lambda e: e.update(error_markers="oops"), "error_markers"),
    ],
)
def test_custom_vendor_validation(tmp_path, mutate, message):
    section = _custom_entry(tmp_path)
    mutate(section["juniper_junos"])
    saved = PROFILES.copy()
    try:
        with pytest.raises(ConfigError, match=message):
            load_custom_vendors(section, base_dir=tmp_path)
    finally:
        PROFILES.clear()
        PROFILES.update(saved)


def test_custom_vendor_has_no_port_channel_profile(tmp_path):
    saved = PROFILES.copy()
    try:
        load_custom_vendors(_custom_entry(tmp_path), base_dir=tmp_path)
        assert get_profile("juniper_junos").lag is None
    finally:
        PROFILES.clear()
        PROFILES.update(saved)


# ------------------------------------------------- protocols, CDP and custom FDP
ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent

RUCKUS_FDP = """Device ID: Router
Entry address(es):
  IP address: 10.95.6.143
Platform: cisco RSP4,  Capabilities: Router
Interface: Eth 1/1,  Port ID (outgoing port): FastEthernet5/0/0
Holdtime : 150 seconds
Version :

IOS (tm) RSP Software (RSP-JSV-M), Version 12.0(5)T1,  RELEASE SOFTWARE
"""
RUCKUS_FDP_0860 = """Device ID: Router
Entry address(es):
			IP address: 10.95.6.143
Platform: cisco RSP4, Capabilities: Router
Interface: Eth 1/1/1, Port ID (outgoing port): FastEthernet5/0/0
Holdtime : 150 seconds
Version :

IOS (tm) RSP Software (RSP-JSV-M), Version 12.0(5)T1, RELEASE SOFTWARE
"""
FDP_ENTRY = {
    "ruckus_fastiron": {
        "description": "Ruckus ICX (FDP only)",
        "protocols": {
            "fdp": {
                "command": "show fdp neighbors detail",
                "template": "templates/ruckus_fastiron_show_fdp_neighbors_detail.textfsm",
                "fields": {
                    "local_interface": "LOCAL_INTERFACE",
                    "neighbor_name": "NEIGHBOR_NAME",
                    "neighbor_interface": "NEIGHBOR_INTERFACE",
                    "mgmt_address": "MGMT_ADDRESS",
                    "description": "NEIGHBOR_DESCRIPTION",
                    "capabilities": "CAPABILITIES",
                    "platform": "PLATFORM",
                },
            }
        },
    }
}


def test_cisco_platforms_have_cdp_and_the_others_do_not():
    for key in ("cisco_ios", "cisco_xe", "cisco_nxos", "cisco_xr"):
        assert list(get_profile(key).protocol_profiles()) == ["lldp", "cdp"], key
        assert get_profile(key).protocol_profiles()["cdp"].resolve_template().is_file()
    for key in ("arista_eos", "aruba_aoscx", "huawei", "juniper_junos", "vyos"):
        assert list(get_profile(key).protocol_profiles()) == ["lldp"], key


def test_every_builtin_platform_has_an_interface_status_profile():
    for key in (
        "cisco_ios",
        "cisco_xe",
        "cisco_nxos",
        "cisco_xr",
        "arista_eos",
        "aruba_aoscx",
        "huawei",
        "huawei_vrp",
        "huawei_vrpv8",
        "juniper_junos",
        "vyos",
    ):
        status = get_profile(key).status
        assert status is not None and status.resolve_template().is_file(), key


def test_a_custom_vendor_may_have_only_a_vendor_protocol():
    from src.parser import LldpParser
    from src.poller import ProtocolData, RawLldp

    saved = PROFILES.copy()
    try:
        assert load_custom_vendors(FDP_ENTRY, base_dir=ROOT) == ["ruckus_fastiron"]
        profile = get_profile("ruckus_fastiron")
        assert profile.command == "" and list(profile.protocol_profiles()) == ["fdp"]
        for text, local in ((RUCKUS_FDP, "Eth 1/1"), (RUCKUS_FDP_0860, "Eth 1/1/1")):
            raw = RawLldp(
                "10.0.0.9",
                "ruckus_fastiron",
                "show fdp neighbors detail",
                "now",
                extras={"fdp": ProtocolData(text=text)},
                lldp_note="no LLDP command is known",
            )
            parsed = LldpParser().parse(raw)
            (neighbor,) = parsed.neighbors
            assert (
                neighbor.local_interface,
                neighbor.neighbor_name,
                neighbor.neighbor_interface,
            ) == (local, "Router", "FastEthernet5/0/0")
            assert neighbor.mgmt_ips == ("10.95.6.143",) and neighbor.platform == "cisco RSP4"
            assert neighbor.capabilities == {"router"} and neighbor.protocols == {"fdp"}
            assert parsed.protocols_used == ["fdp"] and parsed.protocol_notes == {}
    finally:
        PROFILES.clear()
        PROFILES.update(saved)


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ({"x": {"description": "nothing at all"}}, "missing command, template and fields"),
        ({"x": {"protocols": ["fdp"]}}, "protocols must be a mapping"),
        (
            {"x": {"protocols": {"fdp": {"command": "c"}}}},
            "protocols.fdp: missing template, fields",
        ),
        ({"x": {"protocols": {"fdp": "oops"}}}, "protocols.fdp must be a mapping"),
        (
            {
                "x": {
                    "protocols": {
                        "fdp": {
                            "command": "c",
                            "template": "templates/nope.textfsm",
                            "fields": {"platform": "P"},
                        }
                    }
                }
            },
            "template file not found",
        ),
    ],
)
def test_custom_protocol_entries_are_validated(entry, message):
    saved = PROFILES.copy()
    try:
        with pytest.raises(ConfigError, match=message):
            load_custom_vendors(entry, base_dir=ROOT)
    finally:
        PROFILES.clear()
        PROFILES.update(saved)
