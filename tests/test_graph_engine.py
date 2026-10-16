import json

import pytest

from src.graph_engine import (
    STATUS_CRAWLED,
    STATUS_DISCOVERED,
    STATUS_FAILED,
    GraphEngine,
    GraphEngineError,
)


@pytest.fixture
def engine(tmp_path):
    return GraphEngine(data_dir=tmp_path)


def test_same_device_under_different_identifiers_is_one_node(engine):
    a = engine.resolve(name="SW1.corp.local", ips=["10.0.0.1"], chassis_id="0c4d.e900.0101")
    assert engine.resolve(name="sw1") == a
    assert engine.resolve(ips=["10.0.0.1"]) == a
    assert engine.resolve(chassis_id="0C:4D:E9:00:01:01") == a
    assert engine.graph.number_of_nodes() == 1


def test_node_created_from_ip_gets_a_better_label_when_a_name_shows_up(engine):
    node = engine.resolve(ips=["10.0.0.2"])
    assert engine.label(node) == "10.0.0.2"
    assert engine.resolve(name="Core2.lab", ips=["10.0.0.2"]) == node
    assert engine.label(node) == "Core2"
    assert engine.resolve(name="core2") == node  # the new name is an alias too


def test_mac_in_the_name_field_is_treated_as_a_chassis_id(engine):
    node = engine.resolve(name="0c4d.e900.0101", ips=["10.0.0.1"])
    assert engine.resolve(chassis_id="0c:4d:e9:00:01:01") == node


def test_forced_node_gets_the_new_aliases(engine):
    node = engine.resolve(name="sw-from-lldp")
    assert engine.resolve(name="real-hostname", ips=["10.0.0.5"], node_id=node) == node
    assert engine.resolve(name="real-hostname") == node and engine.resolve(ips=["10.0.0.5"]) == node


def test_nothing_to_identify_by_is_an_error(engine):
    with pytest.raises(GraphEngineError):
        engine.resolve()


def test_link_seen_from_both_ends_is_stored_once(engine):
    a, b = engine.resolve(name="a"), engine.resolve(name="b")
    assert engine.add_link(a, "Gi1/0/1", b, "Eth1/1") is True
    assert engine.add_link(b, "Ethernet1/1", a, "GigabitEthernet1/0/1") is False
    assert engine.graph.number_of_edges() == 1


def test_parallel_links_are_kept_apart(engine):
    a, b = engine.resolve(name="a"), engine.resolve(name="b")
    engine.add_link(a, "Gi1/0/1", b, "Eth1/1")
    engine.add_link(a, "Gi1/0/2", b, "Eth1/2")
    engine.add_link(b, "Eth1/1", a, "Gi1/0/1")  # repeat of the first
    assert engine.graph.number_of_edges() == 2


def test_missing_port_is_a_wildcard_and_gets_filled_in(engine):
    a, b = engine.resolve(name="a"), engine.resolve(name="b")
    engine.add_link(a, "Gi1", b, "")
    assert engine.add_link(b, "Eth7", a, "Gi1") is False
    ((_, _, data),) = engine.graph.edges(data=True)
    assert data["ports"] == {a: "Gi1", b: "Eth7"}


def test_self_link_is_ignored(engine, caplog):
    a = engine.resolve(name="a")
    assert engine.add_link(a, "Gi1", a, "Gi2") is False
    assert engine.graph.number_of_edges() == 0


def test_status_transitions_and_skip_reasons(engine):
    n = engine.resolve(name="n")
    assert engine.status(n) == STATUS_DISCOVERED
    assert engine.mark_skipped(n, "outside scope") is True
    assert engine.mark_skipped(n, "outside scope") is False  # unchanged: not reported twice
    engine.mark_crawled(n, host="10.0.0.9", device_type="cisco_ios", depth=2, hostname="N1")
    assert engine.status(n) == STATUS_CRAWLED and engine.label(n) == "N1"
    assert engine.graph.nodes[n]["skip_reason"] == ""
    assert engine.mark_skipped(n, "late reason") is False  # only discovered nodes get reasons
    engine.mark_failed(n, "boom\nmore", host="10.0.0.9")
    assert engine.status(n) == STATUS_FAILED and engine.graph.nodes[n]["error"] == "boom more"


def test_snapshot_is_deterministic_and_rotates(engine, tmp_path):
    a, b = engine.resolve(name="a"), engine.resolve(name="b")
    engine.add_link(b, "Eth1", a, "Gi1")
    engine.mark_crawled(a, "10.0.0.1", "cisco_ios", 0)
    engine.set_seed(a)
    first = engine.save({"seed": "10.0.0.1"})
    data = json.loads(first.read_text())
    assert data["schema_version"] == 1 and data["metadata"]["seed"] == "10.0.0.1"
    assert data["counts"] == {"crawled": 1, "failed": 0, "discovered": 1}
    assert [n["id"] for n in data["nodes"]] == ["a", "b"]
    assert data["links"] == [
        {
            "source": "a",
            "target": "b",
            "source_port": "Gi1",
            "target_port": "Eth1",
            "source_bundle": "",
            "target_bundle": "",
            "sources": ["lldp"],
            "source_oper": {},
            "target_oper": {},
            "warnings": [],
        }
    ]
    assert not engine.previous_path.exists()
    engine.save()
    assert engine.previous_path.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_unwritable_data_dir_is_a_clear_error(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(GraphEngineError, match="Could not save"):
        GraphEngine(data_dir=blocker / "sub").save()


def test_find_does_not_create_nodes(engine):
    assert engine.find(name="ghost") is None and engine.graph.number_of_nodes() == 0
    node = engine.resolve(name="sw1", ips=["10.0.0.1"])
    assert engine.find(ips=["10.0.0.1"]) == node


def test_merge_repoints_links_aliases_and_attributes(engine):
    a, b, c = (engine.resolve(name=n) for n in ("a", "b", "c"))
    c2 = engine.resolve(
        name="c-alt", ips=["10.0.0.9"], chassis_id="aa:bb:cc:dd:ee:ff", capabilities=["router"]
    )
    engine.add_link(a, "Gi1", c, "Gi1")
    engine.add_link(b, "Gi1", c2, "Gi5")
    engine.add_link(a, "Gi1", c2, "Gi1")  # the same a-c link, reported against the other identity
    engine.set_seed(c2)
    engine.merge(keep=c, drop=c2)
    assert not engine.graph.has_node(c2)
    assert engine.graph.number_of_edges() == 2  # a-c collapsed, b-c re-pointed
    assert engine.resolve(name="c-alt") == c and engine.find(ips=["10.0.0.9"]) == c
    assert engine.find(chassis_id="aabb.ccdd.eeff") == c
    assert engine.graph.nodes[c]["seed"] and engine.graph.nodes[c]["capabilities"] == ["router"]


def _channel(name, protocol, members, up=True):
    from src.vendors import PortChannel, PortChannelMember

    return PortChannel(name, protocol, up, tuple(PortChannelMember(i, st) for i, st in members))


def test_links_are_marked_with_the_bundles_of_both_ends(engine):
    a, b = engine.resolve(name="a"), engine.resolve(name="b")
    engine.add_link(a, "Gi1/0/1", b, "Ethernet1")
    engine.add_link(a, "Gi1/0/2", b, "Ethernet2")
    engine.add_link(a, "Gi1/0/9", b, "Ethernet9")  # not in any bundle
    engine.set_port_channels(
        a, [_channel("Po1", "LACP", [("Gi1/0/1", "bundled"), ("Gi1/0/2", "bundled")])]
    )
    engine.set_port_channels(b, [_channel("Po5", "LACP", [("Et1", "bundled"), ("Et2", "bundled")])])
    assert engine.annotate_bundles() == 2
    bundles = {
        tuple(sorted(d["ports"].values())): d["bundles"]
        for _u, _v, d in engine.graph.edges(data=True)
    }
    assert bundles[("Ethernet1", "Gi1/0/1")] == {a: "Po1", b: "Po5"}  # "Et1" matches "Ethernet1"
    assert bundles[("Ethernet9", "Gi1/0/9")] == {}


def test_a_bundle_known_on_one_end_only_still_marks_the_link(engine):
    a, b = engine.resolve(name="a"), engine.resolve(name="b")
    engine.add_link(a, "Eth1/12", b, "1/1/1")
    engine.set_port_channels(a, [_channel("Po50", "LACP", [("Eth1/12", "bundled")])])
    engine.set_port_channels(b, [], note="no port-channel command")
    assert engine.annotate_bundles() == 1
    ((_, _, data),) = engine.graph.edges(data=True)
    assert data["bundles"] == {a: "Po50"}
    assert engine.graph.nodes[b]["lag_note"] == "no port-channel command"


def test_annotating_twice_gives_the_same_result(engine):
    a, b = engine.resolve(name="a"), engine.resolve(name="b")
    engine.add_link(a, "Gi1", b, "Gi1")
    engine.set_port_channels(a, [_channel("Po1", "static", [("Gi1", "bundled")])])
    assert engine.annotate_bundles() == engine.annotate_bundles() == 1


def test_snapshot_carries_bundles_and_port_channels(engine):
    a, b = engine.resolve(name="a"), engine.resolve(name="b")
    engine.add_link(a, "Gi1", b, "Gi1")
    engine.set_port_channels(a, [_channel("Po1", "LACP", [("Gi1", "bundled")])])
    engine.annotate_bundles()
    data = engine.to_dict()
    assert data["links"][0]["source_bundle"] == "Po1" and data["links"][0]["target_bundle"] == ""
    node = next(n for n in data["nodes"] if n["id"] == a)
    assert node["port_channels"] == [
        {
            "name": "Po1",
            "protocol": "LACP",
            "up": True,
            "members": [{"interface": "Gi1", "state": "bundled"}],
        }
    ]


def test_failure_kind_and_skip_code_are_recorded(engine):
    n = engine.resolve(name="n")
    engine.mark_skipped(n, "outside scope", "out_of_scope")
    assert engine.graph.nodes[n]["skip_code"] == "out_of_scope"
    engine.mark_skipped(n, "")
    assert engine.graph.nodes[n]["skip_code"] == ""
    engine.mark_failed(n, "bad password", kind="auth")
    assert engine.graph.nodes[n]["error_kind"] == "auth"
    engine.mark_crawled(n, "10.0.0.9", "cisco_ios", 1)
    assert engine.graph.nodes[n]["error_kind"] == ""


# ----------------------------------------------------------- sources, status, checks
def up(speed, duplex="full", status="up"):
    return {"status": status, "speed": speed, "duplex": duplex, "auto": True}


def test_a_link_remembers_every_protocol_that_saw_it(engine):
    a, b = engine.resolve(name="a"), engine.resolve(name="b")
    assert engine.add_link(a, "Gi1/0/1", b, "Eth1/1", protocols={"lldp"}) is True
    assert engine.add_link(a, "GigabitEthernet1/0/1", b, "Ethernet1/1", protocols={"cdp"}) is False
    assert engine.add_link(b, "Eth1/1", a, "Gi1/0/1", protocols={"cdp"}) is False
    ((_, _, data),) = engine.graph.edges(data=True)
    assert data["sources"] == {"lldp", "cdp"}
    engine.add_link(a, "Gi1/0/9", b, "Eth1/9", protocols={"cdp"})
    assert sorted(sorted(d["sources"]) for _, _, d in engine.graph.edges(data=True)) == [
        ["cdp"],
        ["cdp", "lldp"],
    ]
    assert engine.to_dict()["links"][0]["sources"] in (["cdp", "lldp"], ["cdp"])


def test_what_each_end_reports_is_attached_to_the_link_and_checked(engine):
    a, b = engine.resolve(name="core1"), engine.resolve(name="fw1")
    engine.add_link(a, "Gi1/0/10", b, "port1")
    engine.set_link_status(a, {"gi1/0/10": up(100)})
    assert engine.annotate_links() == 1
    ((_, _, data),) = engine.graph.edges(data=True)
    assert data["oper"] == {a: up(100)} and data["warnings"][0]["kind"] == "below_nominal"
    snapshot = engine.to_dict()["links"][0]
    assert snapshot["source_oper"] == up(100) and snapshot["target_oper"] == {}
    assert snapshot["warnings"][0]["severity"] == "degraded"


def test_a_mismatch_needs_both_ends(engine):
    a, b = engine.resolve(name="a"), engine.resolve(name="b")
    engine.add_link(a, "Eth1/1", b, "Eth1/2")
    engine.set_link_status(a, {"eth1/1": up(1000)})
    engine.set_link_status(b, {"eth1/2": up(100)})
    assert engine.annotate_links() == 1
    assert (
        engine.graph.edges(data=True).__iter__().__next__()[2]["warnings"][0]["kind"]
        == "speed_mismatch"
    )


def test_checks_can_be_switched_off_or_told_to_ignore_things(engine):
    from src.linkcheck import LinkCheckConfig

    a, b = engine.resolve(name="a"), engine.resolve(name="b")
    engine.add_link(a, "Gi1", b, "Gi2")
    engine.set_link_status(a, {"gi1": up(100)})
    assert engine.annotate_links(LinkCheckConfig(enabled=False)) == 0
    ((_, _, data),) = engine.graph.edges(data=True)
    assert data["oper"] and data["warnings"] == []  # still recorded, just not judged
    cfg = LinkCheckConfig.from_settings({"ignore_ports": ["^Gi1$"]})
    assert engine.annotate_links(cfg) == 0
    assert engine.annotate_links(LinkCheckConfig()) == 1


def test_bundle_members_at_different_speeds_warn_on_every_member(engine):
    a, b = engine.resolve(name="a"), engine.resolve(name="b")
    engine.add_link(a, "Gi1", b, "Gi1")
    engine.add_link(a, "Gi2", b, "Gi2")
    engine.set_link_status(a, {"gi1": up(1000), "gi2": up(1000)})
    engine.set_link_status(b, {"gi1": up(1000), "gi2": up(100)})
    engine.set_port_channels(a, [_channel("Po1", "LACP", [("Gi1", "bundled"), ("Gi2", "bundled")])])
    engine.annotate_bundles()
    assert engine.annotate_links() == 2
    kinds = [sorted(w["kind"] for w in d["warnings"]) for _, _, d in engine.graph.edges(data=True)]
    assert sorted(kinds) == [["bundle_speed_mix"], ["bundle_speed_mix", "speed_mismatch"]]


def test_discovery_and_platform_are_stored_on_the_device(engine):
    n = engine.resolve(name="n", platform="cisco WS-C3850")
    engine.set_discovery(n, ["lldp", "cdp"], {"fdp": "not known\nhere"}, "no status")
    attrs = engine.graph.nodes[n]
    assert attrs["platform"] == "cisco WS-C3850" and attrs["discovery"] == ["cdp", "lldp"]
    assert (
        attrs["protocol_notes"] == {"fdp": "not known here"} and attrs["status_note"] == "no status"
    )
    assert (
        engine.resolve(name="n", platform="something else") == n
        and attrs["platform"] == "cisco WS-C3850"
    )
