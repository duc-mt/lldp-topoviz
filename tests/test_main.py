import json
from pathlib import Path

import pytest
import yaml

import main as cli

DEMO = Path(__file__).resolve().parent.parent / "samples" / "demo"


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "DATA_DIR", tmp_path / "data")
    return tmp_path / "data"


def run_demo(tmp_path, *extra):
    out = tmp_path / "topology.html"
    argv = [
        "--config",
        str(DEMO / "settings.yaml"),
        "--replay",
        str(DEMO),
        "--output",
        str(out),
        *extra,
    ]
    return cli.main(argv), out


def test_demo_run_writes_diagram_and_snapshot_and_exits_3(tmp_path, isolated_data_dir, capsys):
    code, out = run_demo(tmp_path)
    assert code == cli.EXIT_PARTIAL  # fw1 has no capture: a partial map, not a failure
    assert out.is_file() and out.stat().st_size > 100_000
    snapshot = json.loads((isolated_data_dir / "current_state.json").read_text())
    assert snapshot["counts"] == {"crawled": 7, "failed": 1, "discovered": 3}
    assert snapshot["metadata"]["partial"] is True and snapshot["metadata"]["source"].endswith(
        "demo"
    )
    text = capsys.readouterr().out
    assert "11 (7 crawled, 1 failed, 3 not crawled)" in text and "fw1 (10.0.0.250)" in text
    assert "16 (10 in port-channels, 5 with warnings)" in text


def test_dry_run_writes_no_snapshot(tmp_path, isolated_data_dir):
    code, out = run_demo(tmp_path, "--dry-run")
    assert code == cli.EXIT_PARTIAL and out.is_file()
    assert not isolated_data_dir.exists()


def test_fully_successful_crawl_exits_0(tmp_path):
    # limit the walk so the failing fw1 is never attempted
    code, _ = run_demo(tmp_path, "--max-depth", "0", "--dry-run")
    assert code == cli.EXIT_OK


def test_missing_allowed_subnets_is_an_expected_error(tmp_path, capsys):
    settings = tmp_path / "s.yaml"
    settings.write_text(yaml.safe_dump({"seed": {"host": "10.0.0.1"}}))
    code = cli.main(["--config", str(settings), "--replay", str(DEMO), "--dry-run"])
    assert code == cli.EXIT_ERROR


def test_allow_flag_supplies_the_scope(tmp_path):
    settings = tmp_path / "s.yaml"
    settings.write_text(yaml.safe_dump({"seed": {"host": "10.0.0.1", "device_type": "cisco_ios"}}))
    out = tmp_path / "t.html"
    code = cli.main(
        [
            "--config",
            str(settings),
            "--replay",
            str(DEMO),
            "--dry-run",
            "--allow",
            "10.0.0.0/16",
            "--output",
            str(out),
        ]
    )
    assert code == cli.EXIT_PARTIAL and out.is_file()


def test_unreachable_seed_exits_1(tmp_path, capsys):
    code = cli.main(
        [
            "--config",
            str(DEMO / "settings.yaml"),
            "--replay",
            str(DEMO),
            "--dry-run",
            "--seed",
            "10.9.9.9",
            "--allow",
            "10.9.0.0/16",
            "--output",
            str(tmp_path / "t.html"),
        ]
    )
    assert code == cli.EXIT_ERROR


def test_live_mode_needs_credentials(tmp_path, monkeypatch):
    for name in ("LLDP_MAPPER_USERNAME", "LLDP_MAPPER_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    settings = tmp_path / "s.yaml"
    settings.write_text(
        yaml.safe_dump(
            {
                "seed": {"host": "10.0.0.1"},
                "crawl": {"allowed_subnets": ["10.0.0.0/8"]},
            }
        )
    )
    assert cli.main(["--config", str(settings), "--dry-run"]) == cli.EXIT_ERROR


def test_save_raw_output_can_be_replayed(tmp_path):
    capture = tmp_path / "captures"
    code, _ = run_demo(tmp_path, "--dry-run", "--save-raw", str(capture))
    assert code == cli.EXIT_PARTIAL
    assert (capture / "index.yaml").is_file() and (capture / "10.0.1.1.txt").is_file()
    out = tmp_path / "again.html"
    code = cli.main(
        [
            "--config",
            str(DEMO / "settings.yaml"),
            "--replay",
            str(capture),
            "--dry-run",
            "--output",
            str(out),
        ]
    )
    assert code == cli.EXIT_PARTIAL and out.is_file()


def test_unexpected_errors_return_2(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(cli, "run", boom)
    assert cli.main(["--dry-run"]) == cli.EXIT_UNEXPECTED


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as info:
        cli.main(["--version"])
    assert info.value.code == 0 and "1.0.0" in capsys.readouterr().out


def test_port_channels_can_be_switched_off_on_the_command_line(tmp_path, isolated_data_dir, capsys):
    code, out = run_demo(tmp_path, "--no-port-channels")
    assert code == cli.EXIT_PARTIAL
    assert "16 (0 in port-channels, 4 with warnings)" in capsys.readouterr().out
    snapshot = json.loads((isolated_data_dir / "current_state.json").read_text())
    assert all(
        link["source_bundle"] == "" and link["target_bundle"] == "" for link in snapshot["links"]
    )
    assert (
        "LACP"
        not in out.read_text(encoding="utf-8")
        .split("window.LLDP_DETAIL=", 1)[1]
        .split("</script>", 1)[0]
    )


def test_collect_port_channels_can_be_set_in_the_settings_file(tmp_path, capsys):
    settings = tmp_path / "s.yaml"
    settings.write_text(
        yaml.safe_dump(
            {
                "seed": {"host": "10.0.0.1", "device_type": "cisco_ios"},
                "crawl": {"allowed_subnets": ["10.0.0.0/16"], "collect_port_channels": False},
            }
        )
    )
    code = cli.main(
        [
            "--config",
            str(settings),
            "--replay",
            str(DEMO),
            "--dry-run",
            "--output",
            str(tmp_path / "t.html"),
        ]
    )
    assert code == cli.EXIT_PARTIAL and "(0 in port-channels" in capsys.readouterr().out


def test_link_checks_can_be_switched_off_on_the_command_line(tmp_path, isolated_data_dir, capsys):
    code, _ = run_demo(tmp_path, "--no-link-checks")
    assert code == cli.EXIT_PARTIAL
    assert "(10 in port-channels, 0 with warnings)" in capsys.readouterr().out
    snapshot = json.loads((isolated_data_dir / "current_state.json").read_text())
    assert all(link["warnings"] == [] and link["source_oper"] == {} for link in snapshot["links"])


def test_the_snapshot_records_warnings_and_where_links_were_seen(tmp_path, isolated_data_dir):
    run_demo(tmp_path)
    snapshot = json.loads((isolated_data_dir / "current_state.json").read_text())
    warned = [link for link in snapshot["links"] if link["warnings"]]
    assert len(warned) == 5
    kinds = {w["kind"] for link in warned for w in link["warnings"]}
    assert kinds == {"below_nominal", "speed_mismatch", "duplex_mismatch", "bundle_speed_mix"}
    cdp_only = [link for link in snapshot["links"] if link["sources"] == ["cdp"]]
    assert {(link["source"], link["target"]) for link in cdp_only} == {
        ("core1", "sw-legacy"),
        ("sep001122334455", "sw-legacy"),
    }


def test_protocols_flag_limits_discovery_to_lldp(tmp_path, capsys):
    code, _ = run_demo(tmp_path, "--protocols", "lldp", "--dry-run")
    out = capsys.readouterr().out
    assert code == cli.EXIT_PARTIAL
    assert "9 (6 crawled, 1 failed, 2 not crawled)" in out  # sw-legacy and its phone are CDP-only


def test_cdp_alone_maps_the_cisco_part_of_the_network(tmp_path, capsys):
    code, _ = run_demo(tmp_path, "--protocols", "cdp", "--dry-run")
    out = capsys.readouterr().out
    assert code == cli.EXIT_OK  # fw1 and the other non-CDP neighbors are simply never discovered
    assert "Devices   : 5 (4 crawled, 0 failed, 1 not crawled)" in out
