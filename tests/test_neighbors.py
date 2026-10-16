from src.vendors import get_profile
from src.vendors.base import (
    FieldMap,
    LldpNeighbor,
    merge_neighbors,
    normalize_capabilities,
    row_to_neighbor,
)


def nb(local="Gi1/0/1", name="sw2", port="Gi0/1", ips=(), protocol="lldp", **kw):
    return LldpNeighbor(
        local_interface=local,
        neighbor_name=name,
        neighbor_interface=port,
        mgmt_ips=tuple(ips),
        protocols=frozenset({protocol}),
        **kw,
    )


def test_the_same_neighbor_from_lldp_and_cdp_becomes_one_record():
    lldp = nb(
        name="dist1.lab.local", port="Eth1/1", ips=("10.0.1.1",), capabilities=frozenset({"bridge"})
    )
    cdp = nb(
        local="GigabitEthernet1/0/1",
        name="dist1.lab.local",
        port="Ethernet1/1",
        protocol="cdp",
        ips=("10.0.1.1", "10.0.1.2"),
        platform="cisco N9K",
        description="NX-OS 9.3",
        capabilities=frozenset({"router"}),
    )
    (merged,) = merge_neighbors([[lldp], [cdp]])
    assert merged.protocols == {"lldp", "cdp"}
    assert merged.neighbor_interface == "Eth1/1"  # the first protocol's value wins ...
    assert merged.mgmt_ips == ("10.0.1.1", "10.0.1.2")  # ... and the gaps are filled
    assert merged.platform == "cisco N9K" and merged.description == "NX-OS 9.3"
    assert merged.capabilities == {"bridge", "router"}


def test_a_cdp_only_neighbor_is_kept():
    merged = merge_neighbors(
        [[nb(name="a", local="Gi1")], [nb(name="legacy", local="Gi2", protocol="cdp")]]
    )
    assert [(n.neighbor_name, sorted(n.protocols)) for n in merged] == [
        ("a", ["lldp"]),
        ("legacy", ["cdp"]),
    ]


def test_neighbors_with_different_names_are_never_merged_even_if_their_ports_match():
    merged = merge_neighbors(
        [[nb(name="ap-1", port="eth0")], [nb(name="ap-2", port="eth0", protocol="cdp")]]
    )
    assert len(merged) == 2


def test_neighbors_of_one_protocol_are_never_merged_with_each_other():
    """Several devices behind one port (a hub, a hypervisor) may all report the same port ID."""
    five = [nb(name=f"n{i}", port="Gi1") for i in range(5)]
    assert len(merge_neighbors([five])) == 5
    unnamed = [nb(name="", port="eth0", ips=(f"10.0.0.{i}",)) for i in range(3)]
    assert len(merge_neighbors([unnamed])) == 3


def test_a_report_without_a_name_matches_on_the_port_or_the_address():
    by_port = merge_neighbors(
        [[nb(name="sw2", port="Gi0/1")], [nb(name="", port="GigabitEthernet0/1", protocol="cdp")]]
    )
    assert len(by_port) == 1
    by_ip = merge_neighbors(
        [
            [nb(name="sw2", port="", ips=("10.0.0.2",))],
            [nb(name="", port="", ips=("10.0.0.2",), protocol="cdp")],
        ]
    )
    assert len(by_ip) == 1
    assert (
        len(
            merge_neighbors(
                [[nb(name="sw2", port="Gi1")], [nb(name="", port="Gi9", protocol="cdp")]]
            )
        )
        == 2
    )


def test_different_local_ports_are_different_neighbors():
    merged = merge_neighbors(
        [[nb(local="Gi1", name="sw2")], [nb(local="Gi2", name="sw2", protocol="cdp")]]
    )
    assert len(merged) == 2


def test_cdp_rows_drop_the_serial_number_from_the_device_id():
    fields = get_profile("cisco_nxos").protocol_profiles()["cdp"].fields
    row = {
        "CHASSIS_ID": "core1.lab.local(FDO1234ABCD)",
        "NEIGHBOR_NAME": "",
        "LOCAL_INTERFACE": "Ethernet1/1",
        "NEIGHBOR_INTERFACE": "GigabitEthernet1/0/1",
        "MGMT_ADDRESS": "10.0.0.1",
        "PLATFORM": "cisco C9300",
        "CAPABILITIES": "Router Switch IGMP Filtering",
    }
    neighbor = row_to_neighbor(row, fields, "cdp")
    assert neighbor.neighbor_name == "core1.lab.local" and neighbor.protocols == {"cdp"}
    assert neighbor.platform == "cisco C9300" and neighbor.mgmt_ips == ("10.0.0.1",)
    assert neighbor.chassis_id == ""  # a CDP "chassis" is a serial number, not a MAC: not an alias
    assert neighbor.capabilities == {
        "router",
        "bridge",
    }  # IGMP / Filtering say nothing about the role


def test_lldp_names_keep_their_parentheses():
    neighbor = row_to_neighbor(
        {"NEIGHBOR_NAME": "core (lab)", "LOCAL_INTERFACE": "Gi1"}, FieldMap()
    )
    assert neighbor.neighbor_name == "core (lab)"


def test_cdp_capability_words():
    assert normalize_capabilities("Router Source-Route-Bridge") == {"router", "bridge"}
    assert normalize_capabilities("Host Phone") == {"station", "telephone"}
    assert normalize_capabilities("Trans-Bridge Switch IGMP") == {"bridge"}
    assert normalize_capabilities("Supports-STP-Dispute Two-Port-Mac-Relay") == frozenset()
