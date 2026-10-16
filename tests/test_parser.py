import pytest

from src.parser import LldpParser, ParserError
from src.poller import FilePoller, RawLldp

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
