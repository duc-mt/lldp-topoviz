import pytest

from src.parser import LldpParser
from src.poller import FilePoller
from src.vendors.base import parse_duplex, parse_link_state, parse_speed
from src.vendors.status import (
    parse_aoscx_interface,
    parse_huawei_interface,
    parse_junos_interfaces,
    parse_status_table,
    parse_vyos_interfaces,
    parse_xr_interfaces,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a-1000", (1000, True)),
        ("1000", (1000, None)),
        ("a-100", (100, True)),
        ("10G", (10000, None)),
        ("1G", (1000, None)),
        ("100M", (100, None)),
        ("1000Mb/s", (1000, None)),
        ("1000 Mb/s", (1000, None)),
        ("10000 Mb/s", (10000, None)),
        ("auto", (None, True)),
        ("", (None, None)),
        ("-", (None, None)),
    ],
)
def test_parse_speed(text, expected):
    assert parse_speed(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a-full", ("full", True)),
        ("full", ("full", None)),
        ("Half-duplex", ("half", None)),
        ("HALF", ("half", None)),
        ("Full", ("full", None)),
        ("auto", (None, True)),
        ("", (None, None)),
    ],
)
def test_parse_duplex(text, expected):
    assert parse_duplex(text) == expected


@pytest.mark.parametrize(
    ("text", "state"),
    [
        ("connected", "up"),
        ("UP", "up"),
        ("UP (spoofing)", "up"),
        ("up", "up"),
        ("notconnect", "down"),
        ("down", "down"),
        ("DOWN", "down"),
        ("err-disabled", "err"),
        ("Administratively DOWN", "disabled"),
        ("disabled", "disabled"),
        ("", "unknown"),
    ],
)
def test_parse_link_state(text, state):
    assert parse_link_state(text) == state


def test_the_status_table_parsers_accept_either_column_case():
    row = {"port": "Gi1/0/1", "status": "connected", "duplex": "a-full", "speed": "a-100"}
    for source in (row, {k.upper(): v for k, v in row.items()}):
        (item,) = parse_status_table([source])
        assert (item.name, item.status, item.speed, item.duplex, item.auto) == (
            "Gi1/0/1",
            "up",
            100,
            "full",
            True,
        )


def test_huawei_aoscx_and_xr_rows():
    (hw,) = parse_huawei_interface(
        [
            {
                "interface": "GE0/0/1",
                "link_status": "UP",
                "speed": "100",
                "duplex": "FULL",
                "negotiation": "ENABLE",
            }
        ]
    )
    assert (hw.status, hw.speed, hw.duplex, hw.auto) == ("up", 100, "full", True)
    (cx,) = parse_aoscx_interface(
        [
            {
                "interface": "1/1/1",
                "link_status": "up",
                "link_admin": "down",
                "speed": "1000 Mb/s",
                "duplex": "Half",
                "auto_neg": "off",
            }
        ]
    )
    assert (cx.status, cx.speed, cx.duplex, cx.auto) == (
        "disabled",
        1000,
        "half",
        False,
    )  # admin down wins
    (xr,) = parse_xr_interfaces(
        [
            {
                "interface": "HundredGigE0/0/0/1",
                "link_status": "up",
                "speed": "100000Mb/s",
                "duplex": "Full-duplex",
            }
        ]
    )
    assert (xr.speed, xr.duplex) == (100000, "full")
    assert parse_status_table([{"port": ""}]) == []


def test_vyos_interface_parsing():
    rows = [
        {"INTERFACE": "eth0", "STATUS": "u/u", "DESCRIPTION": "WAN"},
        {"INTERFACE": "eth1", "STATUS": "A/D", "DESCRIPTION": "DMZ"},
        {"INTERFACE": "eth2", "STATUS": "u/D", "DESCRIPTION": "DOWN"},
        {"INTERFACE": "lo", "STATUS": "u/u", "DESCRIPTION": ""},
    ]
    statuses = parse_vyos_interfaces(rows)
    assert [(s.name, s.status) for s in statuses] == [
        ("eth0", "up"),
        ("eth1", "disabled"),
        ("eth2", "up"),
    ]


def test_junos_interface_parsing():
    rows = [
        {
            "INTERFACE": "ge-0/0/0",
            "ADMIN_STATE": "Enabled",
            "LINK_STATUS": "Up",
            "SPEED": "1000mbps",
            "DUPLEX": "Full-Duplex",
        },
        {
            "INTERFACE": "ge-0/0/1",
            "ADMIN_STATE": "Administratively down",
            "LINK_STATUS": "Down",
            "SPEED": "1000mbps",
            "DUPLEX": "Full-Duplex",
        },
    ]
    statuses = parse_junos_interfaces(rows)
    assert [(s.name, s.status, s.speed, s.duplex) for s in statuses] == [
        ("ge-0/0/0", "up", 1000, "full"),
        ("ge-0/0/1", "disabled", 1000, "full"),
    ]


@pytest.mark.parametrize(
    ("host", "device_type", "port", "expected"),
    [
        ("10.0.0.1", "cisco_ios", "gi1/0/10", ("up", 100, "full")),
        ("10.0.1.1", "cisco_nxos", "eth1/14", ("up", 10000, "full")),
        ("10.0.2.1", "arista_eos", "eth1", ("up", 10000, "full")),
        ("10.0.2.2", "huawei", "gi0/0/23", ("up", 100, "full")),
        ("10.0.2.3", "aruba_aoscx", "1/1/1", ("up", 1000, "half")),
    ],
)
def test_every_bundled_status_capture_parses(demo_dir, host, device_type, port, expected):
    parsed = LldpParser().parse(FilePoller(demo_dir).poll(host, device_type))
    assert parsed.status_note == ""
    item = parsed.interfaces[port]
    assert (item.status, item.speed, item.duplex) == expected


def test_a_missing_status_capture_is_a_note_not_an_error(demo_dir, tmp_path):
    (tmp_path / "10.0.0.1.txt").write_bytes((demo_dir / "10.0.0.1.txt").read_bytes())
    parsed = LldpParser().parse(FilePoller(tmp_path).poll("10.0.0.1", "cisco_ios"))
    assert parsed.interfaces == {} and "no interface status capture" in parsed.status_note
    switched_off = LldpParser().parse(
        FilePoller(demo_dir, collect_status=False).poll("10.0.0.1", "cisco_ios")
    )
    assert "switched off" in switched_off.status_note
