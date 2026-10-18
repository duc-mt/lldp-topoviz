import networkx as nx
import pytest

from src.config import ConfigError
from src.enrichment import (
    NodeTypeRules,
    TierRules,
    assign_tiers,
    classify_node,
    failure_info,
    link_speed,
    os_label,
    port_speed,
    skip_badge,
    speed_label,
    speed_style,
    sub_label,
)


def node(**attrs):
    return {"description": "", "capabilities": [], "device_type": "", **attrs}


# ------------------------------------------------------------------- node type
@pytest.mark.parametrize(
    ("attrs", "label", "kind"),
    [
        (node(capabilities=["bridge", "router"]), "x", "switch"),
        (node(capabilities=["router"]), "x", "router"),
        (node(capabilities=["wlan"]), "x", "ap"),
        (node(capabilities=["telephone"]), "x", "phone"),
        (node(capabilities=["station"]), "x", "endpoint"),
        (node(description="pfSense 2.7.2-RELEASE", capabilities=["router"]), "x", "firewall"),
        (
            node(description="FortiGate-100F v7.2", capabilities=["bridge", "router"]),
            "x",
            "firewall",
        ),
        (node(capabilities=["bridge", "router"]), "edge-fw-01", "firewall"),
        (node(capabilities=["router"]), "FW1", "firewall"),
        (
            node(
                description="Juniper Networks, Inc. mx204 Junos 22.4R2",
                capabilities=["bridge", "router"],
            ),
            "x",
            "router",
        ),
        (node(description="Aruba AP-535"), "ap-lobby", "ap"),
        (node(device_type="arista_eos"), "acc1", "switch"),  # the EOS template has no capabilities
        (node(device_type="cisco_xr"), "pe1", "router"),
        (node(), "mystery", "unknown"),
    ],
)
def test_classify_node(attrs, label, kind):
    assert classify_node(attrs, label) == kind


def test_a_hostname_rule_overrides_everything():
    rules = NodeTypeRules.from_settings({"firewall": ["^gw-"], "router": ["^wan"]})
    assert classify_node(node(capabilities=["bridge"]), "gw-dc1", rules) == "firewall"
    assert classify_node(node(capabilities=["bridge"]), "WAN-edge", rules) == "router"
    assert classify_node(node(capabilities=["bridge"]), "other", rules) == "switch"


def test_node_type_rules_are_validated():
    with pytest.raises(ConfigError, match="unknown type"):
        NodeTypeRules.from_settings({"toaster": ["x"]})
    with pytest.raises(ConfigError, match="invalid regex"):
        NodeTypeRules.from_settings({"switch": ["("]})


# -------------------------------------------------------------------- OS label
@pytest.mark.parametrize(
    ("attrs", "expected"),
    [
        (node(description="Cisco Nexus Operating System (NX-OS) Software 9.3(8)"), "NX-OS 9.3(8)"),
        (
            node(
                description="Cisco IOS Software [Cupertino], Catalyst L3 Switch Software (CAT9K_IOSXE), Version 17.9.4, RELEASE SOFTWARE (fc5)",
                device_type="cisco_ios",
            ),
            "IOS-XE 17.9.4",
        ),
        (
            node(
                description="Cisco IOS Software, C2960 Software, Version 15.0(2)SE11, RELEASE",
                device_type="cisco_ios",
            ),
            "IOS 15.0(2)SE11",
        ),
        (
            node(
                description="Arista Networks EOS version 4.28.3M running on an Arista Networks DCS-7050SX-64"
            ),
            "EOS 4.28.3M",
        ),
        (
            node(
                description="Huawei Versatile Routing Platform Software VRP (R) software, Version 5.170 (S5720)"
            ),
            "VRP 5.170",
        ),
        (node(description="ArubaOS-CX 6200F 10.10.1010"), "AOS-CX 10.10.1010"),
        (node(description="Juniper Networks, Inc. mx204 Junos 22.4R2"), "Junos 22.4R2"),
        (node(description="pfSense 2.7.2-RELEASE amd64"), "pfSense 2.7.2"),
        (node(device_type="cisco_nxos"), "NX-OS"),  # no description: the platform alone
        (node(description="Aruba AP-535"), ""),
        (node(), ""),
    ],
)
def test_os_label(attrs, expected):
    assert os_label(attrs) == expected


def test_sub_label_is_address_and_os():
    attrs = node(
        address="10.0.1.1",
        device_type="cisco_nxos",
        description="Cisco Nexus (NX-OS) Software 9.3(8)",
    )
    assert sub_label(attrs) == "10.0.1.1 · NX-OS 9.3(8)"
    assert sub_label(node(mgmt_ips=["10.0.9.9"])) == "10.0.9.9"  # not crawled: the advertised IP
    assert sub_label(node()) == ""


# ------------------------------------------------------------------------ tiers
def graph_of(edges, **attrs_by_node):
    g = nx.MultiGraph()
    for u, v in edges:
        for n in (u, v):
            if n not in g:
                g.add_node(n, label=n, seed=attrs_by_node.get("seed") == n)
        g.add_edge(u, v)
    return g


def test_hostname_rules_decide_the_tier_and_unused_rows_are_dropped():
    g = graph_of([("core1", "dist1"), ("dist1", "acc1"), ("fw1", "core1"), ("acc1", "ap1")])
    kinds = {"core1": "switch", "dist1": "switch", "acc1": "switch", "fw1": "firewall", "ap1": "ap"}
    layout = assign_tiers(g, kinds, TierRules.from_settings(None))
    assert layout.tiers == {
        "core1": "core",
        "dist1": "distribution",
        "acc1": "access",
        "fw1": "edge",
        "ap1": "end",
    }
    assert [t for t, _ in layout.rows] == ["edge", "core", "distribution", "access", "end"]
    assert layout.levels["fw1"] == 0 and layout.levels["ap1"] == 4
    g.remove_node("fw1")
    layout = assign_tiers(
        g, {k: v for k, v in kinds.items() if k != "fw1"}, TierRules.from_settings(None)
    )
    assert [t for t, _ in layout.rows] == ["core", "distribution", "access", "end"]
    assert layout.levels["core1"] == 0  # numbered over the rows in use


def test_unmatched_devices_are_placed_below_their_placed_neighbour():
    g = graph_of([("core1", "mystery"), ("mystery", "other")])
    kinds = {n: "switch" for n in g}
    layout = assign_tiers(g, kinds, TierRules.from_settings(None))
    assert layout.tiers == {"core1": "core", "mystery": "distribution", "other": "access"}


def test_without_any_matching_name_the_seed_is_the_top():
    g = graph_of([("a", "b"), ("b", "c"), ("c", "d")], seed="a")
    layout = assign_tiers(g, {n: "switch" for n in g}, TierRules.from_settings(None))
    assert [layout.tiers[n] for n in "abcd"] == ["core", "distribution", "access", "access"]


def test_the_root_can_be_named():
    g = graph_of([("x1", "x2"), ("x2", "x3")], seed="x1")
    layout = assign_tiers(g, {n: "switch" for n in g}, TierRules.from_settings(None, root="X3"))
    assert layout.tiers["x3"] == "core" and layout.tiers["x1"] == "access"


def test_user_tier_rules_replace_the_defaults_of_that_tier_only():
    rules = TierRules.from_settings({"core": ["^bb"], "access": ["^edge-sw"]})
    assert rules.match("bb-01") == "core" and rules.match("core1") is None  # replaced
    assert rules.match("dist1") == "distribution"  # untouched
    assert rules.match("edge-sw-3") == "access"


def test_tier_rules_are_validated():
    with pytest.raises(ConfigError, match="unknown tier"):
        TierRules.from_settings({"spine": ["x"]})
    with pytest.raises(ConfigError, match="invalid regex"):
        TierRules.from_settings({"core": ["("]})


# ------------------------------------------------------------------- link speed
@pytest.mark.parametrize(
    ("port", "mbps"),
    [
        ("Fa0/1", 100),
        ("FastEthernet0/1", 100),
        ("Gi1/0/1", 1000),
        ("GigabitEthernet0/0/24", 1000),
        ("GE0/0/1", 1000),
        ("ge-0/0/0", 1000),
        ("Te1/0/1", 10000),
        ("TenGigabitEthernet1/1", 10000),
        ("TenGigE0/0/0/0", 10000),
        ("xe-0/0/0", 10000),
        ("XGigabitEthernet0/0/1", 10000),
        ("10GE1/0/1", 10000),
        ("Twe1/0/1", 25000),
        ("TwentyFiveGigE0/0/0/1", 25000),
        ("Fo1/0/1", 40000),
        ("FortyGigabitEthernet1/1", 40000),
        ("Hu1/0/1", 100000),
        ("HundredGigE0/0/0/0", 100000),
        ("100GE0/0/1", 100000),
        ("Eth1/1", None),
        ("Ethernet1", None),
        ("Et1", None),
        ("1/1/1", None),
        ("et-0/0/0", None),
        ("mgmt0", None),
        ("", None),
    ],
)
def test_port_speed(port, mbps):
    assert port_speed(port) == mbps


def test_link_speed_is_the_slower_known_end():
    assert link_speed("Gi1/0/1", "Eth1/1") == 1000  # NX-OS names carry no speed
    assert (
        link_speed("Te1/0/1", "Gi0/1") == 10000 if False else link_speed("Te1/0/1", "Gi0/1") == 1000
    )
    assert link_speed("Eth1/1", "Ethernet1") is None


def test_speed_labels_and_styles():
    assert [speed_label(m) for m in (None, 100, 1000, 2000, 25000)] == [
        "?",
        "100M",
        "1G",
        "2G",
        "25G",
    ]
    assert speed_style(None)[0] == "?"
    assert (
        speed_style(1000)[0] == "1G" and speed_style(2000)[0] == "1G"
    )  # aggregates: highest tier reached
    assert speed_style(20000)[0] == "10G"
    widths = [speed_style(m)[2] for m in (100, 1000, 10000, 25000, 40000, 100000)]
    assert widths == sorted(widths) and len(set(widths)) == 6


# ------------------------------------------------------------ failure wording
def test_failure_and_skip_wording():
    assert failure_info("auth")[0] == "SSH auth failed"
    assert failure_info("timeout")[0] == "SSH timeout"
    assert failure_info("nonsense") == failure_info("other")
    assert all(
        failure_info(k)[1]
        for k in ("auth", "timeout", "connect", "rejected", "parse", "unsupported", "no_data")
    )
    assert skip_badge("out_of_scope") == "outside scope" and skip_badge("") == ""
    assert skip_badge("something_new") == "not crawled"


# ------------------------------------------------------- CDP platform hints
@pytest.mark.parametrize(
    ("attrs", "kind"),
    [
        (node(platform="Cisco IP Phone 7960", capabilities=["station", "telephone"]), "phone"),
        (node(platform="cisco AIR-AP2802I-E-K9", capabilities=["bridge"]), "ap"),
        (node(platform="cisco C9120AXI-E", capabilities=["bridge"]), "ap"),
        (node(platform="cisco ASA5516", capabilities=["router"]), "firewall"),
        (node(platform="cisco WS-C3850-48P", capabilities=["bridge", "router"]), "switch"),
        (node(platform="cisco ISR4331", description="Cisco IOS Software, ISR Software"), "router"),
    ],
)
def test_the_cdp_platform_helps_to_classify(attrs, kind):
    assert classify_node(attrs, "x") == kind


def test_the_sub_label_falls_back_to_the_platform_when_the_os_is_unknown():
    assert (
        sub_label(node(mgmt_ips=["10.0.0.131"], platform="Cisco IP Phone 7960"))
        == "10.0.0.131 · Cisco IP Phone 7960"
    )
    both = node(address="10.0.0.1", device_type="cisco_nxos", platform="cisco N9K-C93180YC-FX")
    assert sub_label(both) == "10.0.0.1 · NX-OS"  # a known OS wins


# ------------------------------------------------- the crawler's leaf class wins
@pytest.mark.parametrize(
    ("endpoint_class", "kind"),
    [
        ("endpoint_phone", "phone"),
        ("endpoint_ap", "ap"),
        ("endpoint_server", "endpoint"),
        ("endpoint_generic", "endpoint"),
    ],
)
def test_the_recorded_leaf_class_decides_the_icon_even_next_to_bridge(endpoint_class, kind):
    attrs = node(capabilities=["bridge", "router"], endpoint_class=endpoint_class)
    assert classify_node(attrs, "x") == kind


def test_no_leaf_class_changes_nothing():
    assert classify_node(node(capabilities=["bridge"], endpoint_class=""), "x") == "switch"
    assert classify_node(node(capabilities=["bridge"]), "x") == "switch"
