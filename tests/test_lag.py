import pytest

from src.vendors import get_profile
from src.vendors.base import lag_member_state, lag_protocol
from src.vendors.lag import parse_flag_style, parse_huawei


@pytest.mark.parametrize(
    ("code", "state"),
    [
        ("P", "bundled"),
        ("PG+", "bundled"),
        ("p", "bundled"),
        ("D", "down"),
        ("s", "suspended"),
        ("I", "standalone"),
        ("i", "standalone"),
        ("H", "standby"),
        ("w", "waiting"),
        ("Selected", "bundled"),
        ("Unselected", "down"),
        ("", "other"),
        ("??", "other"),
    ],
)
def test_member_states(code, state):
    assert lag_member_state(code) == state


@pytest.mark.parametrize(
    ("text", "protocol"),
    [
        ("LACP", "LACP"),
        ("LACP(a)", "LACP"),
        ("lacp", "LACP"),
        ("PAgP", "PAgP"),
        ("-", "static"),
        ("NONE", "static"),
        ("None", "static"),
        ("MANUAL", "static"),
        ("", "static"),
    ],
)
def test_protocols(text, protocol):
    assert lag_protocol(text) == protocol


def test_rows_are_accepted_in_either_case():
    """Netmiko hands back lower-case column names; the templates declare upper-case ones."""
    row = {
        "bundle_name": "Po1",
        "bundle_status": "SU",
        "bundle_protocol": "LACP",
        "member_interface": ["Gi1", "Gi2"],
        "member_interface_status": ["P", "D"],
    }
    upper = {key.upper(): value for key, value in row.items()}
    for source in (row, upper):
        (channel,) = parse_flag_style([source])
        assert channel.name == "Po1" and channel.protocol == "LACP" and channel.up
        assert [(m.interface, m.state) for m in channel.members] == [
            ("Gi1", "bundled"),
            ("Gi2", "down"),
        ]


def test_a_down_bundle_and_filler_members():
    rows = [
        {
            "BUNDLE_NAME": "Po9",
            "BUNDLE_STATUS": "SD",
            "BUNDLE_PROTOCOL": "NONE",
            "MEMBER_INTERFACE": ["Eth1/1", "--"],
            "MEMBER_INTERFACE_STATUS": ["D", ""],
        },
        {"BUNDLE_NAME": ""},  # a row without a name is ignored
    ]
    (channel,) = parse_flag_style(rows)
    assert not channel.up and channel.protocol == "static"
    assert [m.interface for m in channel.members] == ["Eth1/1"]


def test_huawei_ignores_the_partner_rows_and_groups_by_trunk():
    def row(trunk, interface, status, mode="LACP"):
        return {
            "eth_trunk": trunk,
            "working_mode": mode,
            "operate_status": "up",
            "interface": interface,
            "status": status,
        }

    rows = [
        row("Eth-Trunk1", "GE0/0/1", "Selected"),
        row("Eth-Trunk1", "GE0/0/2", "Unselected"),
        row("Eth-Trunk1", "GE0/0/1", ""),  # partner section
        row("Eth-Trunk2", "GE0/0/3", "Selected", mode="NORMAL"),
    ]
    one, two = parse_huawei(rows)
    assert [(m.interface, m.state) for m in one.members] == [
        ("GE0/0/1", "bundled"),
        ("GE0/0/2", "down"),
    ]
    assert two.protocol == "static" and len(two.members) == 1


def test_junos_lacp_parsing():
    from src.vendors.lag import parse_junos_lacp

    rows = [
        {
            "BUNDLE_NAME": "ae0",
            "MEMBER_INTERFACE": ["ge-0/0/1", "ge-0/0/2"],
            "RECEIVE_STATE": ["Current", "Current"],
            "TRANSMIT_STATE": ["Fast periodic", "Fast periodic"],
            "MUX_STATE": ["Collecting distributing", "Detached"],
        }
    ]
    (channel,) = parse_junos_lacp(rows)
    assert channel.name == "ae0" and channel.protocol == "LACP" and channel.up
    assert [(m.interface, m.state) for m in channel.members] == [
        ("ge-0/0/1", "bundled"),
        ("ge-0/0/2", "down"),
    ]


def test_platforms_with_port_channel_profile():
    with_lag = {
        k
        for k in (
            "cisco_ios",
            "cisco_xe",
            "cisco_nxos",
            "arista_eos",
            "huawei",
            "huawei_vrp",
            "huawei_vrpv8",
            "juniper_junos",
        )
        if get_profile(k).lag
    }
    assert len(with_lag) == 8
    assert (
        get_profile("aruba_aoscx").lag is None
        and get_profile("cisco_xr").lag is None
        and get_profile("vyos").lag is None
    )
    for key in with_lag:
        assert get_profile(key).lag.resolve_template().is_file()
