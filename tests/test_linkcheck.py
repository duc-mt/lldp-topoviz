import pytest

from src.config import ConfigError
from src.linkcheck import (
    SEVERITY_DEGRADED,
    SEVERITY_MISMATCH,
    End,
    LinkCheckConfig,
    check_bundle,
    check_link,
    effective_speed,
    operating_speed,
)


def end(label="a", port="Gi1/0/1", speed=1000, duplex="full", status="up"):
    return End(label, port, speed, duplex, status)


def kinds(warnings):
    return [(w.kind, w.severity) for w in warnings]


def test_a_healthy_link_has_no_warnings():
    assert check_link([end("a", "Gi1/0/1"), end("b", "Gi0/1")]) == []


def test_a_gigabit_port_running_at_100_mbps_is_degraded():
    """The case that motivated the checks: a Gigabit link that came up at 100 Mbps."""
    (warning,) = check_link([end("core1", "Gi1/0/10", speed=100), end("fw1", "port1", speed=None)])
    assert (warning.kind, warning.severity) == ("below_nominal", SEVERITY_DEGRADED)
    assert "core1 Gi1/0/10 runs at 100M but the port is 1G" in warning.text


def test_both_ends_degraded_to_the_same_speed_is_one_warning():
    (warning,) = check_link([end("core1", "Gi1/0/20", speed=100), end("sw", "Gi0/1", speed=100)])
    assert warning.kind == "below_nominal"
    assert "core1 Gi1/0/20 and sw Gi0/1 run at 100M but the ports are 1G" in warning.text


def test_speed_mismatch_between_the_ends():
    warnings = check_link(
        [end("dist1", "Eth1/15", speed=1000), end("acc2", "GigabitEthernet0/0/23", speed=100)]
    )
    assert kinds(warnings) == [("speed_mismatch", SEVERITY_MISMATCH)]  # no extra "below nominal"
    assert "dist1 Eth1/15 runs at 1G but acc2 GigabitEthernet0/0/23 at 100M" in warnings[0].text


def test_duplex_mismatch_between_the_ends():
    warnings = check_link(
        [end("dist1", "Eth1/12", duplex="full"), end("acc3", "1/1/1", duplex="half")]
    )
    assert kinds(warnings) == [("duplex_mismatch", SEVERITY_MISMATCH)]
    assert "dist1 Eth1/12 is full but acc3 1/1/1 is half" in warnings[0].text


def test_half_duplex_on_one_end_with_no_peer_data_is_degraded():
    warnings = check_link([end("a", "Gi1", duplex="half")])
    assert kinds(warnings) == [("half_duplex", SEVERITY_DEGRADED)]


def test_half_duplex_on_both_ends_is_two_degraded_findings_not_a_mismatch():
    warnings = check_link(
        [end("a", "Eth1", speed=100, duplex="half"), end("b", "Eth2", speed=100, duplex="half")]
    )
    assert kinds(warnings) == [("half_duplex", SEVERITY_DEGRADED)] * 2


def test_ports_that_are_not_up_are_never_judged():
    assert (
        check_link([end("a", "Gi1", speed=100, status="down"), end("b", "Gi2", speed=1000)]) == []
    )
    assert check_link([end("a", "Gi1", speed=10, status="err")]) == []


def test_names_without_a_speed_prefix_are_only_compared_with_the_peer():
    assert check_link([end("a", "Eth1/1", speed=100)]) == []  # nothing says Eth1/1 should be faster
    assert kinds(check_link([end("a", "Eth1/1", speed=100), end("b", "Eth1/2", speed=1000)])) == [
        ("speed_mismatch", SEVERITY_MISMATCH)
    ]


def test_unknown_speed_or_duplex_is_not_a_finding():
    assert (
        check_link(
            [end("a", "Gi1", speed=None, duplex=None), end("b", "Gi2", speed=1000, duplex="full")]
        )
        == []
    )


def test_bundle_members_at_different_speeds_are_flagged():
    (warning,) = check_bundle([1000, 100])
    assert (warning.kind, warning.severity) == ("bundle_speed_mix", SEVERITY_MISMATCH)
    assert "100M, 1G" in warning.text
    assert (
        check_bundle([1000, 1000]) == []
        and check_bundle([1000, None]) == []
        and check_bundle([]) == []
    )


def test_effective_speed_prefers_what_the_ports_run_at():
    assert effective_speed([end(speed=100)], "Gi1", "Gi2") == (100, "operating")
    assert effective_speed([end(speed=10000), end(speed=1000)], "Te1", "Gi2") == (1000, "operating")
    assert effective_speed([], "Gi1", "Eth1") == (1000, "nominal")
    assert effective_speed([], "Eth1", "Eth2") == (None, "unknown")
    assert effective_speed([end(speed=None)], "Eth1", "Eth2") == (None, "unknown")
    assert operating_speed([end(speed=1000, status="down")]) is None


def test_the_check_config_can_ignore_ports_and_devices():
    config = LinkCheckConfig.from_settings(
        {"ignore_ports": ["^Fa", "phone"], "ignore_hostnames": ["^lab-"]}
    )
    assert config.ignores("sw1", "Fa0/5") and config.ignores("sw1", "FastEthernet0/5")
    assert config.ignores("sw1", "Gi0/1 phone") and config.ignores("lab-sw", "Gi0/1")
    assert not config.ignores("sw1", "Gi0/1")
    assert LinkCheckConfig.from_settings(None).enabled
    assert not LinkCheckConfig.from_settings({"enabled": False}).enabled


def test_the_check_config_is_validated():
    with pytest.raises(ConfigError, match="invalid regex"):
        LinkCheckConfig.from_settings({"ignore_ports": ["("]})
    with pytest.raises(ConfigError, match="mapping"):
        LinkCheckConfig.from_settings(["x"])


def test_vyos_to_junos_link_check():
    # VyOS (eth0, status up, unknown speed prefix) to Junos (ge-0/0/0, 1000M full up)
    warnings = check_link(
        [
            end("vyos-gw1", "eth0", speed=None, duplex=None, status="up"),
            end("edge-rtr2", "ge-0/0/0", speed=1000, duplex="full", status="up"),
        ]
    )
    assert warnings == []

    # When Junos ge-0/0/0 (Gigabit) negotiates down to 100M, it's flagged as degraded
    degraded = check_link(
        [
            end("vyos-gw1", "eth0", speed=None, duplex=None, status="up"),
            end("edge-rtr2", "ge-0/0/0", speed=100, duplex="full", status="up"),
        ]
    )
    assert kinds(degraded) == [("below_nominal", SEVERITY_DEGRADED)]
