import pytest

from src.parser import (
    ENDPOINT_AP,
    ENDPOINT_GENERIC,
    ENDPOINT_PHONE,
    ENDPOINT_SERVER,
    LldpParser,
    ParserError,
    classify_endpoint,
)
from src.poller import FilePoller, ProtocolData, RawLldp
from src.vendors.base import (
    LldpNeighbor,
    decode_capability_bitmap,
    normalize_capabilities,
)

CASES = [
    # host, device_type, hostname, neighbor count, first neighbor (name, remote port, mgmt)
    ("10.0.0.1", "cisco_ios", "core1", 6, ("dist1.lab.local", "Eth1/1", ("10.0.1.1",))),
    ("10.0.0.2", "cisco_xe", "core2", 3, ("core1.lab.local", "Gi1/0/47", ("10.0.0.1",))),
    ("10.0.1.1", "cisco_nxos", "dist1", 10, ("core1.lab.local", "Gi1/0/1", ("10.0.0.1",))),
    ("10.0.2.1", "arista_eos", "acc1", 3, ("dist1.lab.local", "Eth1/10", ("10.0.1.1",))),
    ("10.0.2.2", "huawei", "acc2", 2, ("dist1.lab.local", "Eth1/11", ("10.0.1.1",))),
    ("10.0.2.3", "aruba_aoscx", "acc3", 2, ("dist1.lab.local", "Eth1/12", ("10.0.1.1",))),
]


@pytest.mark.parametrize(("host", "device_type", "hostname", "count", "first"), CASES)
def test_every_bundled_capture_parses(demo_dir, host, device_type, hostname, count, first):
    parsed = LldpParser().parse(FilePoller(demo_dir).poll(host, device_type))
    assert parsed.hostname == hostname
    assert len(parsed.neighbors) == count
    n = parsed.neighbors[0]
    assert (n.neighbor_name, n.neighbor_interface, n.mgmt_ips) == first


def test_arista_neighbor_without_management_address(demo_dir):
    parsed = LldpParser().parse(FilePoller(demo_dir).poll("10.0.2.1", "arista_eos"))
    ap = next(n for n in parsed.neighbors if n.neighbor_name == "ap-lobby")
    assert ap.mgmt_ips == () and ap.neighbor_interface == "eth0"


def test_structured_rows_skip_the_text_path():
    raw = RawLldp(
        host="10.0.0.1",
        device_type="cisco_ios",
        command="x",
        collected_at="now",
        hostname="Core1.lab.local",
        rows=[{"LOCAL_INTERFACE": "Gi1", "NEIGHBOR_NAME": "sw2", "MGMT_ADDRESS": "10.0.0.2"}],
    )
    parsed = LldpParser().parse(raw)
    assert parsed.hostname == "Core1"  # domain stripped, case kept for the label
    assert parsed.neighbors[0].neighbor_name == "sw2"


@pytest.mark.parametrize("wrong_type", ["arista_eos", "cisco_ios", "huawei", "aruba_aoscx"])
def test_wrong_device_type_is_a_clear_error(demo_dir, wrong_type):
    raw = FilePoller(demo_dir).poll("10.0.1.1", "cisco_nxos")  # NX-OS text ...
    wrong = RawLldp(
        **{**raw.__dict__, "device_type": wrong_type}
    )  # ... parsed with another template
    with pytest.raises(ParserError, match="Wrong device_type"):
        LldpParser().parse(wrong)


def test_non_lldp_text_raises_parser_error():
    raw = RawLldp(
        host="h",
        device_type="aruba_aoscx",
        command="x",
        collected_at="now",
        text="this is not\nLLDP output at all\n",
    )
    with pytest.raises(ParserError):
        LldpParser().parse(raw)


def test_empty_output_raises_parser_error():
    raw = RawLldp(host="h", device_type="cisco_ios", command="x", collected_at="now", text="  \n")
    with pytest.raises(ParserError, match="no output"):
        LldpParser().parse(raw)


def test_unknown_device_type_raises_parser_error():
    raw = RawLldp(host="h", device_type="nope", command="x", collected_at="now", text="x")
    with pytest.raises(ParserError, match="not supported"):
        LldpParser().parse(raw)


def test_no_neighbors_is_not_an_error(caplog):
    raw = RawLldp(host="h", device_type="cisco_ios", command="x", collected_at="now", rows=[])
    with caplog.at_level("WARNING"):
        parsed = LldpParser().parse(raw)
    assert parsed.neighbors == [] and "reported no neighbors" in caplog.text


PORT_CHANNELS = [
    # host, device_type, expected [(name, protocol, members)]
    ("10.0.0.1", "cisco_ios", [("Po1", "LACP", ["Gi1/0/1", "Gi1/0/2"])]),
    ("10.0.0.2", "cisco_xe", [("Po2", "static", ["Gi1/0/1", "Gi1/0/2"])]),
    (
        "10.0.1.1",
        "cisco_nxos",
        [
            ("Po10", "LACP", ["Eth1/1", "Eth1/2"]),
            ("Po20", "static", ["Eth1/3", "Eth1/4"]),
            ("Po30", "LACP", ["Eth1/10", "Eth1/14"]),
            ("Po40", "LACP", ["Eth1/11", "Eth1/15"]),
            ("Po50", "LACP", ["Eth1/12", "Eth1/16"]),
        ],
    ),
    ("10.0.2.1", "arista_eos", [("Po1", "LACP", ["Et1", "Et2"])]),
    (
        "10.0.2.2",
        "huawei",
        [("Eth-Trunk1", "LACP", ["GigabitEthernet0/0/23", "GigabitEthernet0/0/24"])],
    ),
]


@pytest.mark.parametrize(("host", "device_type", "expected"), PORT_CHANNELS)
def test_port_channels_of_every_platform_parse(demo_dir, host, device_type, expected):
    parsed = LldpParser().parse(FilePoller(demo_dir).poll(host, device_type))
    assert parsed.lag_note == ""
    assert [
        (pc.name, pc.protocol, [m.interface for m in pc.members]) for pc in parsed.port_channels
    ] == expected
    assert all(m.state == "bundled" for pc in parsed.port_channels for m in pc.members)
    assert all(pc.up for pc in parsed.port_channels)


def test_platform_without_a_port_channel_profile_explains_itself(demo_dir):
    parsed = LldpParser().parse(FilePoller(demo_dir).poll("10.0.2.3", "aruba_aoscx"))
    assert parsed.port_channels == [] and "no port-channel" in parsed.lag_note


def test_port_channel_trouble_never_fails_the_device(tmp_path):
    (tmp_path / "10.1.1.1.txt").write_text(
        "Local Intf: Gi1\nChassis id: 0011.2233.4455\nPort id: Gi2\n"
        "System Name: sw2\n\nTime remaining: 9 seconds\n"
        "System Capabilities: B,R\nEnabled Capabilities: B,R\n"
        "Management Addresses:\n    IP: 10.1.1.2\nVlan ID: - not advertised\n"
    )
    (tmp_path / "10.1.1.1.lag.txt").write_text(
        "this is not etherchannel output\nit has several lines\nand none of them is a bundle\n"
        "so it cannot be an empty answer\neither\n"
    )
    parsed = LldpParser().parse(FilePoller(tmp_path).poll("10.1.1.1", "cisco_ios"))
    assert parsed.neighbors and parsed.port_channels == []
    assert parsed.lag_note  # says why


def test_switching_collection_off_skips_the_port_channels(demo_dir):
    parsed = LldpParser().parse(
        FilePoller(demo_dir, collect_lag=False).poll("10.0.0.1", "cisco_ios")
    )
    assert parsed.port_channels == [] and "switched off" in parsed.lag_note


def test_vyos_sample_parses():
    from pathlib import Path

    base = Path(__file__).resolve().parent.parent / "samples" / "vyos"
    parsed = LldpParser().parse(FilePoller(base).poll("10.0.3.1", "vyos"))
    assert parsed.hostname == "vyos-gw1"
    assert len(parsed.neighbors) == 2
    assert parsed.neighbors[0].neighbor_name == "dist1.lab.local"
    assert parsed.neighbors[1].neighbor_name == "edge-rtr2.lab.local"
    assert parsed.interfaces["eth0"].status == "up"


def test_juniper_junos_sample_parses():
    from pathlib import Path

    base = Path(__file__).resolve().parent.parent / "samples" / "juniper_junos"
    parsed = LldpParser().parse(FilePoller(base).poll("10.0.4.1", "juniper_junos"))
    assert parsed.hostname == "edge-rtr2"
    assert len(parsed.neighbors) == 2
    assert parsed.neighbors[0].neighbor_name == "dist1.lab.local"
    assert parsed.neighbors[1].neighbor_name == "vyos-gw1.lab.local"
    assert parsed.interfaces["ge-0/0/0"].status == "up"
    assert parsed.interfaces["ge-0/0/0"].speed == 1000
    assert len(parsed.port_channels) == 1
    assert parsed.port_channels[0].name == "ae0"


def test_hp_procurve_sample_parses():
    from pathlib import Path

    base = Path(__file__).resolve().parent.parent / "samples" / "hp_procurve"
    parsed = LldpParser().parse(FilePoller(base).poll("10.0.5.1", "hp_procurve"))
    assert parsed.hostname == "procurve-sw1"
    assert len(parsed.neighbors) == 2
    assert parsed.neighbors[0].neighbor_name == "dist1.lab.local"
    assert parsed.neighbors[1].neighbor_name == "vyos-gw1.lab.local"
    assert parsed.interfaces["a1"].status == "up"
    assert parsed.interfaces["a1"].speed == 1000


# ------------------------------------------------------------ leaf endpoint classification
def leaf(caps="", platform="", description=""):
    return classify_endpoint(
        LldpNeighbor(
            "Gi1/0/1",
            capabilities=normalize_capabilities(caps),
            platform=platform,
            description=description,
        )
    )


@pytest.mark.parametrize(
    ("caps", "platform", "description", "expected"),
    [
        # system capabilities alone
        ("T", "", "", ENDPOINT_PHONE),
        ("W", "", "", ENDPOINT_AP),
        ("S", "", "", ENDPOINT_SERVER),
        ("O", "", "", ENDPOINT_GENERIC),
        ("P", "", "", ENDPOINT_GENERIC),  # a repeater / hub cannot be logged in to either
        ("B", "", "", ""),
        ("R", "", "", ""),
        ("B,R", "", "", ""),
        ("S,B", "", "", ""),  # a host that also bridges (lldpd on a hypervisor) may be crawlable
        # a leaf that also claims bridge: IP phones with a PC port, access points
        ("B,T", "", "", ENDPOINT_PHONE),
        ("B,W", "", "", ENDPOINT_AP),
        ("B,R,W", "", "", ENDPOINT_AP),
        # CDP flags
        ("Host Phone", "Cisco IP Phone 7960", "", ENDPOINT_PHONE),
        ("Router Trans-Bridge", "cisco AIR-AP2802I-E-K9", "", ENDPOINT_AP),
        ("Router Switch IGMP", "cisco WS-C3850-48P", "", ""),
        # nothing advertised: only a neighbor that names itself is a leaf
        ("", "", "", ""),
        ("", "", "Arista Networks EOS version 4.28", ""),
        ("", "", "Cisco IP Phone 8841, V1, sip88xx.12-8-1", ENDPOINT_PHONE),
        ("", "", "Cisco Aironet 1815 Series Access Point", ENDPOINT_AP),
        ("", "", "AXIS P3245 Network Camera", ENDPOINT_GENERIC),
        ("", "", "HP LaserJet Pro M404", ENDPOINT_GENERIC),
        # a switch description must not trip the hints
        ("B", "", "Cisco IOS Software, C2960X Software, Version 15.2(7)E", ""),
    ],
)
def test_neighbors_are_classified_as_leaf_endpoints_or_infrastructure(
    caps, platform, description, expected
):
    assert leaf(caps, platform, description) == expected


@pytest.mark.parametrize(
    ("bitmap", "names"),
    [
        (0x0014, {"bridge", "router"}),
        (0x0004, {"bridge"}),
        (0x0008, {"wlan"}),  # IEEE 802.1AB: bit 0x0008 is WLAN access point ...
        (0x0020, {"telephone"}),  # ... and 0x0020 is telephone
        (0x0080, {"station"}),
        (0x0001, {"other"}),
        (0x0024, {"bridge", "telephone"}),
        (0x0000, set()),
        (0xFF00, set()),  # reserved bits are ignored
    ],
)
def test_lldp_capability_bitmap_decoding(bitmap, names):
    assert decode_capability_bitmap(bitmap) == names
    assert normalize_capabilities(f"0x{bitmap:04x}") == names


def test_a_hex_bitmap_mixes_with_names():
    assert normalize_capabilities(["0x0004", "Telephone"]) == {"bridge", "telephone"}


def lldp_and_cdp_poll(lldp_row, cdp_row):
    return RawLldp(
        host="10.0.0.1",
        device_type="cisco_ios",
        command="show lldp neighbors detail",
        collected_at="now",
        hostname="core1",
        rows=[lldp_row],
        extras={"cdp": ProtocolData(rows=[cdp_row])},
    )


def test_the_parser_classifies_after_merging_lldp_and_cdp():
    # LLDP says bridge (and nothing else), CDP names the platform: together it is a phone
    lldp = {
        "LOCAL_INTERFACE": "Gi1/0/5",
        "NEIGHBOR_NAME": "SEP001122334455",
        "NEIGHBOR_INTERFACE": "port 1",
        "CAPABILITIES": "B",
        "MGMT_ADDRESS": "10.0.0.60",
    }
    cdp = {
        "LOCAL_INTERFACE": "GigabitEthernet1/0/5",
        "NEIGHBOR_NAME": "SEP001122334455",
        "NEIGHBOR_INTERFACE": "Port 1",
        "PLATFORM": "Cisco IP Phone 8841",
        "CAPABILITIES": "Host Phone",
    }
    parsed = LldpParser().parse(lldp_and_cdp_poll(lldp, cdp))
    assert len(parsed.neighbors) == 1
    assert parsed.neighbors[0].endpoint_class == ENDPOINT_PHONE
    assert parsed.neighbors[0].protocols == {"lldp", "cdp"}


def test_the_bundled_captures_have_one_phone_and_no_other_leaf(demo_dir):
    classes = {}
    for host, device_type in (("10.0.0.1", "cisco_ios"), ("10.0.0.30", "cisco_ios")):
        raw = FilePoller(demo_dir).poll(host, device_type)
        for n in LldpParser().parse(raw).neighbors:
            if n.endpoint_class:
                classes[n.neighbor_name] = n.endpoint_class
    assert classes == {"SEP001122334455": ENDPOINT_PHONE}
