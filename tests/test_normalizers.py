from src import (
    extract_ipv4,
    is_ipv4,
    looks_like_mac,
    normalize_chassis_id,
    normalize_hostname,
    normalize_interface,
    short_name,
)


def test_hostname_strips_domain_and_case():
    assert normalize_hostname("SW1.Corp.Local") == "sw1"
    assert normalize_hostname('"core1"') == "core1"
    assert short_name("SW1.corp.local") == "SW1"


def test_hostname_keeps_ips_and_macs_intact():
    assert normalize_hostname("10.0.0.1") == "10.0.0.1"
    assert normalize_hostname("0c4d.e900.0a01") == "0c4d.e900.0a01"


def test_interface_long_and_short_forms_match():
    assert normalize_interface("GigabitEthernet0/1") == normalize_interface("Gi0/1") == "Gi0/1"
    assert normalize_interface("Ethernet1/1") == normalize_interface("Eth1/1") == "Eth1/1"
    assert normalize_interface("Ethernet1") == "Eth1"
    assert normalize_interface("Port-channel10") == "Po10"
    assert normalize_interface("GE0/0/1") == "Gi0/0/1"


def test_interface_unknown_forms_pass_through():
    assert normalize_interface("1/1/1") == "1/1/1"
    assert normalize_interface("xe-0/0/0") == "xe-0/0/0"
    assert normalize_interface("") == ""
    assert normalize_interface("uplink to core") == "uplink to core"


def test_chassis_id_mac_formats_collapse():
    keys = {
        normalize_chassis_id(v)
        for v in ("0c4d.e900.0a01", "0c:4d:e9:00:0a:01", "0C-4D-E9-00-0A-01", "0c4d-e900-0a01")
    }
    assert keys == {"0c4de9000a01"}
    assert normalize_chassis_id("Switch-A") == "switch-a"


def test_ipv4_helpers():
    assert is_ipv4("10.0.0.1") and not is_ipv4("10.0.0.256") and not is_ipv4("fe80::1")
    assert extract_ipv4("IP: 10.0.0.1, also 10.0.0.1 and 10.0.0.2") == ["10.0.0.1", "10.0.0.2"]
    assert extract_ipv4(["fe80::1", "192.0.2.7"]) == ["192.0.2.7"]
    assert extract_ipv4(None) == []
    assert looks_like_mac("0c4d.e900.0a01") and not looks_like_mac("core1")
