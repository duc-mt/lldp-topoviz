from unittest.mock import MagicMock, patch

import pytest
import yaml
from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException

from src.config import ConnectionSettings
from src.poller import DevicePoller, FilePoller, PollerError, RawCapture, RawLldp

from .conftest import DEMO_DIR

CONN = ConnectionSettings(username="u", password="p", secret="")


class FakeConn:
    """Stands in for a Netmiko connection (used as a context manager)."""

    base_prompt = "core1"

    def __init__(
        self, structured, raw_text="", enable=False, lag_rows=None, lag_text="", lag_exc=None
    ):
        self.structured = structured
        self.raw_text = raw_text
        self.lag_rows = lag_rows if lag_rows is not None else []
        self.lag_text = lag_text
        self.lag_exc = lag_exc
        self.calls: list[dict] = []
        self.enabled = enable

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def check_enable_mode(self):
        return False

    def enable(self):
        self.enabled = True

    def send_command(self, command, **kwargs):
        self.calls.append({"command": command, **kwargs})
        if command == "show etherchannel summary":
            if self.lag_exc:
                raise self.lag_exc
            return self.lag_rows if kwargs.get("use_textfsm") else self.lag_text
        return self.structured if kwargs.get("use_textfsm") else self.raw_text


def poll_with(conn_or_exc, **poller_kwargs):
    side_effect = conn_or_exc if isinstance(conn_or_exc, Exception) else None
    with patch("src.poller.ConnectHandler", side_effect=side_effect) as handler:
        if side_effect is None:
            handler.return_value = conn_or_exc
        poller = DevicePoller(poller_kwargs.pop("connection", CONN), **poller_kwargs)
        return poller.poll("10.0.0.1", poller_kwargs.pop("device_type", "cisco_ios")), handler


def test_live_poll_uses_use_textfsm_with_an_explicit_template():
    rows = [{"NEIGHBOR_NAME": "sw2"}]
    conn = FakeConn(rows)
    raw, handler = poll_with(conn)
    assert raw.rows == rows and raw.hostname == "core1" and raw.text == ""
    assert handler.call_args.kwargs["device_type"] == "cisco_ios"
    call = conn.calls[0]
    assert call["command"] == "show lldp neighbors detail"
    assert call["use_textfsm"] is True
    assert call["textfsm_template"].endswith("cisco_ios_show_lldp_neighbors_detail.textfsm")


def test_secret_triggers_enable():
    conn = FakeConn([{"NEIGHBOR_NAME": "x"}])
    poll_with(conn, connection=ConnectionSettings(username="u", password="p", secret="s"))
    assert conn.enabled


def test_keep_raw_fetches_the_text_too():
    conn = FakeConn([{"NEIGHBOR_NAME": "x"}], raw_text="Local Intf: Gi1\n")
    raw, _ = poll_with(conn, keep_raw=True)
    assert raw.text == "Local Intf: Gi1\n" and raw.rows
    lldp = [c.get("use_textfsm", False) for c in conn.calls if "lldp" in c["command"]]
    assert lldp == [False, True]


def test_empty_result_is_checked_for_a_rejected_command():
    conn = FakeConn([], raw_text="% Invalid input detected at '^' marker.")
    with pytest.raises(PollerError, match="rejected 'show lldp neighbors detail'"):
        poll_with(conn)


def test_empty_result_without_error_means_no_neighbors():
    conn = FakeConn([], raw_text="LLDP is not enabled\n")
    raw, _ = poll_with(conn)
    assert raw.rows == [] and "not enabled" in raw.text


def test_unparsed_output_is_returned_as_text():
    conn = FakeConn("some unexpected output", raw_text="")
    raw, _ = poll_with(conn)
    assert raw.rows is None and raw.text == "some unexpected output"


def test_unparsed_output_that_is_a_cli_error_is_rejected():
    conn = FakeConn("% Invalid input detected at '^' marker.")
    with pytest.raises(PollerError, match="rejected"):
        poll_with(conn)


@pytest.mark.parametrize(
    ("exc", "message"),
    [
        (
            NetmikoAuthenticationException("Authentication failed."),
            "Authentication failed for 10.0.0.1",
        ),
        (
            NetmikoTimeoutException("TCP connection to device failed.\n\nCommon causes: ..."),
            "Timed out",
        ),
        (TimeoutError("timed out"), "Timed out"),
        (ConnectionRefusedError("refused"), "SSH session to 10.0.0.1 failed"),
        (ValueError("Unsupported 'device_type'"), "Could not open a session"),
    ],
)
def test_connection_errors_become_poller_errors(exc, message):
    with pytest.raises(PollerError, match=message):
        poll_with(exc)


def test_error_text_is_one_line():
    with pytest.raises(PollerError) as info:
        poll_with(NetmikoTimeoutException("first line\n\nCommon causes of this problem:\n1. x"))
    assert "Common causes" not in str(info.value)


def test_unsupported_device_type_fails_before_connecting():
    with patch("src.poller.ConnectHandler") as handler:
        with pytest.raises(PollerError, match="not supported"):
            DevicePoller(CONN).poll("10.0.0.1", "juniper_junos")
        handler.assert_not_called()


# ------------------------------------------------------------------ replay
def test_file_poller_reads_index_hostname_and_type(demo_dir):
    raw = FilePoller(demo_dir).poll("10.0.1.1", "cisco_ios")
    assert (raw.hostname, raw.device_type) == ("dist1", "cisco_nxos")
    assert "Chassis id" in raw.text and raw.rows is None


def test_file_poller_missing_host(demo_dir):
    with pytest.raises(PollerError, match="No replay data for 10.0.0.250"):
        FilePoller(demo_dir).poll("10.0.0.250", "cisco_ios")


def test_file_poller_without_index_uses_host_named_files(tmp_path):
    (tmp_path / "10.1.1.1.txt").write_text("hello\n")
    raw = FilePoller(tmp_path).poll("10.1.1.1", "cisco_ios")
    assert raw.text == "hello\n" and raw.hostname is None and raw.device_type == "cisco_ios"


def test_file_poller_rejects_empty_file(tmp_path):
    (tmp_path / "10.1.1.1.txt").write_text("\n")
    with pytest.raises(PollerError, match="empty"):
        FilePoller(tmp_path).poll("10.1.1.1", "cisco_ios")


def test_raw_capture_roundtrips_through_file_poller(tmp_path):
    capture = RawCapture(tmp_path / "cap")
    capture.write(RawLldp("10.0.0.1", "cisco_ios", "cmd", "now", hostname="core1", text="A\n"))
    capture.write(RawLldp("10.0.0.2", "arista_eos", "cmd", "now", hostname="acc1", text="B\n"))
    capture.write(RawLldp("10.0.0.3", "arista_eos", "cmd", "now", rows=[]))  # no text: skipped
    index = yaml.safe_load((tmp_path / "cap" / "index.yaml").read_text())
    assert set(index) == {"10.0.0.1", "10.0.0.2"}
    replay = FilePoller(tmp_path / "cap").poll("10.0.0.2", "cisco_ios")
    assert (replay.text, replay.hostname, replay.device_type) == ("B\n", "acc1", "arista_eos")


def test_connection_failure_does_not_leak_credentials():
    with pytest.raises(PollerError) as info:
        poll_with(
            NetmikoAuthenticationException("bad"),
            connection=ConnectionSettings(username="admin", password="hunter2"),
        )
    assert "hunter2" not in str(info.value)


def test_mock_is_used_not_the_network():
    with patch("src.poller.ConnectHandler", MagicMock()) as handler:
        handler.side_effect = OSError("no route to host")
        with pytest.raises(PollerError, match="SSH session"):
            DevicePoller(CONN).poll("10.0.0.1", "cisco_ios")


# ---------------------------------------------------------- port-channels
LAG_ROWS = [
    {
        "bundle_name": "Po1",
        "bundle_status": "SU",
        "bundle_protocol": "LACP",
        "member_interface": ["Gi1/0/1"],
        "member_interface_status": ["P"],
    }
]


def test_port_channels_are_read_on_the_same_session():
    conn = FakeConn([{"NEIGHBOR_NAME": "x"}], lag_rows=LAG_ROWS)
    raw, handler = poll_with(conn)
    assert raw.lag_rows == LAG_ROWS and raw.lag_note == ""
    lag_call = next(c for c in conn.calls if c["command"] == "show etherchannel summary")
    assert lag_call["use_textfsm"] is True
    assert lag_call["textfsm_template"].endswith("cisco_ios_show_etherchannel_summary.textfsm")
    assert handler.call_count == 1  # no second login


def test_port_channel_failure_never_loses_the_lldp_data():
    conn = FakeConn([{"NEIGHBOR_NAME": "x"}], lag_exc=NetmikoTimeoutException("slow\n\nmore"))
    raw, _ = poll_with(conn)
    assert raw.rows == [{"NEIGHBOR_NAME": "x"}]
    assert raw.lag_rows is None and "failed: slow" in raw.lag_note


def test_rejected_port_channel_command_is_noted():
    conn = FakeConn([{"NEIGHBOR_NAME": "x"}], lag_text="% Invalid input detected at '^'")
    conn.lag_rows = []
    raw, _ = poll_with(conn)
    assert raw.lag_rows is None and "rejected" in raw.lag_note


def test_port_channels_can_be_switched_off():
    conn = FakeConn([{"NEIGHBOR_NAME": "x"}])
    raw, _ = poll_with(conn, collect_lag=False)
    assert raw.lag_rows is None and "switched off" in raw.lag_note
    assert all("etherchannel" not in c["command"] for c in conn.calls)


def test_platform_without_a_port_channel_command_is_noted():
    conn = FakeConn([{"NEIGHBOR_NAME": "x"}])
    conn.base_prompt = "acc3"
    with patch("src.poller.ConnectHandler", return_value=conn):
        raw = DevicePoller(CONN).poll("10.0.2.3", "aruba_aoscx")
    assert raw.lag_rows is None and "no port-channel command" in raw.lag_note


def test_keep_raw_also_captures_the_port_channel_text(tmp_path):
    conn = FakeConn(
        [{"NEIGHBOR_NAME": "x"}], raw_text="LLDP text", lag_rows=LAG_ROWS, lag_text="LAG text"
    )
    raw, _ = poll_with(conn, keep_raw=True)
    assert raw.lag_text == "LAG text"
    RawCapture(tmp_path).write(raw)
    index = yaml.safe_load((tmp_path / "index.yaml").read_text())
    assert index["10.0.0.1"]["lag_file"] == "10.0.0.1.lag.txt"
    assert (tmp_path / "10.0.0.1.lag.txt").read_text() == "LAG text"
    replay = FilePoller(tmp_path).poll("10.0.0.1", "cisco_ios")
    assert replay.lag_text == "LAG text" and replay.lag_note == ""


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (NetmikoAuthenticationException("bad"), "auth"),
        (NetmikoTimeoutException("slow"), "timeout"),
        (TimeoutError("slow"), "timeout"),
        (ConnectionRefusedError("no"), "connect"),
        (ValueError("bad type"), "connect"),
    ],
)
def test_failures_are_classified(exc, kind):
    with pytest.raises(PollerError) as info:
        poll_with(exc)
    assert info.value.kind == kind


def test_other_failure_kinds():
    with pytest.raises(PollerError) as info:
        poll_with(FakeConn("% Invalid input detected at '^' marker."))
    assert info.value.kind == "rejected"
    with patch("src.poller.ConnectHandler"), pytest.raises(PollerError) as info:
        DevicePoller(CONN).poll("10.0.0.1", "juniper_junos")
    assert info.value.kind == "unsupported"
    with pytest.raises(PollerError) as info:
        FilePoller(DEMO_DIR).poll("10.9.9.9", "cisco_ios")
    assert info.value.kind == "no_data"


# ------------------------------------------------- several discovery protocols
LLDP = "show lldp neighbors detail"
CDP = "show cdp neighbors detail"
STATUS = "show interfaces status"
LAG = "show etherchannel summary"
REJECT = "% Invalid input detected at '^' marker."
GARBAGE = "\n".join(f"unrelated line number {i} that no template could read" for i in range(8))


class ScriptConn(FakeConn):
    """A connection that answers each command from a script: ``{command: (parsed, raw)}``."""

    def __init__(self, script):
        super().__init__([])
        self.script = script

    def send_command(self, command, **kwargs):
        self.calls.append({"command": command, **kwargs})
        parsed, raw = self.script.get(command, ([], ""))
        return parsed if kwargs.get("use_textfsm") else raw


def poll_script(script, protocols=("lldp", "cdp"), device_type="cisco_ios", **kwargs):
    conn = ScriptConn(script)
    with patch("src.poller.ConnectHandler", return_value=conn):
        poller = DevicePoller(CONN, protocols=protocols, **kwargs)
        return poller.poll("10.0.0.1", device_type), conn


ROW = [{"NEIGHBOR_NAME": "x"}]


def test_lldp_and_cdp_are_both_read_on_one_session():
    raw, conn = poll_script({LLDP: (ROW, "lldp text"), CDP: ([{"NEIGHBOR_NAME": "y"}], "cdp text")})
    assert raw.rows == ROW and raw.extras["cdp"].rows == [{"NEIGHBOR_NAME": "y"}]
    assert raw.lldp_note == "" and raw.command == LLDP
    cdp_call = next(c for c in conn.calls if c["command"] == CDP)
    assert cdp_call["textfsm_template"].endswith("cisco_ios_show_cdp_neighbors_detail.textfsm")


def test_lldp_rejected_but_cdp_working_still_yields_the_device():
    raw, _ = poll_script({LLDP: ([], REJECT), CDP: (ROW, "cdp text")})
    assert raw.rows is None and raw.text == "" and "rejected" in raw.lldp_note
    assert raw.extras["cdp"].rows == ROW


def test_every_protocol_rejected_fails_the_device_with_the_first_reason():
    with pytest.raises(PollerError, match="rejected 'show lldp neighbors detail'") as info:
        poll_script({LLDP: ([], REJECT), CDP: ([], REJECT)})
    assert info.value.kind == "rejected"


def test_cdp_failing_is_only_a_note_when_lldp_works():
    raw, _ = poll_script({LLDP: (ROW, "t"), CDP: ([], REJECT)})
    assert (
        raw.rows == ROW and "rejected" in raw.extras["cdp"].note and raw.extras["cdp"].rows is None
    )


def test_only_the_requested_protocols_are_run():
    raw, conn = poll_script({CDP: (ROW, "t")}, protocols=("cdp",))
    assert raw.rows is None and raw.lldp_note == "LLDP is not enabled for this crawl"
    assert raw.command == CDP and all(c["command"] != LLDP for c in conn.calls)


def test_a_platform_without_any_requested_protocol_is_unsupported():
    with (
        patch("src.poller.ConnectHandler") as handler,
        pytest.raises(PollerError, match="none of the requested") as info,
    ):
        DevicePoller(CONN, protocols=("cdp",)).poll("10.0.0.1", "arista_eos")
    assert info.value.kind == "unsupported"
    handler.assert_not_called()


def test_netmikos_raw_string_for_an_empty_result_is_an_empty_answer():
    """Netmiko returns the raw text, not [], when the template finds no records."""
    raw, _ = poll_script({LLDP: ("% LLDP is not enabled", "")}, protocols=("lldp",))
    assert raw.rows == [] and "not enabled" in raw.text


def test_a_string_that_is_really_a_mismatch_stays_unparsed():
    raw, _ = poll_script({LLDP: (GARBAGE, "")}, protocols=("lldp",))
    assert raw.rows is None and "unrelated line number 0" in raw.text


def test_no_port_channels_configured_is_an_empty_list_not_a_failure():
    raw, _ = poll_script(
        {
            LLDP: (ROW, ""),
            LAG: ("Number of channel-groups in use: 0\nNumber of aggregators: 0", ""),
        },
        protocols=("lldp",),
    )
    assert raw.lag_rows == [] and raw.lag_note == ""


# ----------------------------------------------------------- interface status
def test_interface_status_is_read_on_the_same_session():
    rows = [{"port": "Gi1/0/1", "status": "connected", "speed": "a-1000", "duplex": "a-full"}]
    raw, conn = poll_script({LLDP: (ROW, ""), STATUS: (rows, "")}, protocols=("lldp",))
    assert raw.status_rows == rows and raw.status_note == ""
    call = next(c for c in conn.calls if c["command"] == STATUS)
    assert call["textfsm_template"].endswith("cisco_ios_show_interfaces_status.textfsm")


def test_interface_status_failure_never_loses_the_neighbors():
    raw, _ = poll_script({LLDP: (ROW, ""), STATUS: ([], REJECT)}, protocols=("lldp",))
    assert raw.rows == ROW and raw.status_rows is None and "rejected" in raw.status_note


def test_interface_status_can_be_switched_off_or_missing():
    raw, conn = poll_script({LLDP: (ROW, "")}, protocols=("lldp",), collect_status=False)
    assert "switched off" in raw.status_note and all(c["command"] != STATUS for c in conn.calls)
    conn2 = ScriptConn({LLDP: (ROW, "")})
    with patch("src.poller.ConnectHandler", return_value=conn2):
        crow = DevicePoller(CONN, protocols=("lldp",)).poll("10.0.0.1", "cisco_xr")
    assert crow.status_rows is not None or crow.status_note  # XR has a command, so it is attempted


def test_keep_raw_captures_every_command(tmp_path):
    raw, _ = poll_script(
        {
            LLDP: (ROW, "lldp text"),
            CDP: (ROW, "cdp text"),
            STATUS: ([], "status text"),
            LAG: ([], "lag text"),
        },
        keep_raw=True,
    )
    assert (raw.text, raw.extras["cdp"].text, raw.status_text, raw.lag_text) == (
        "lldp text",
        "cdp text",
        "status text",
        "lag text",
    )
    RawCapture(tmp_path).write(raw)
    index = yaml.safe_load((tmp_path / "index.yaml").read_text())["10.0.0.1"]
    assert index["cdp_file"] == "10.0.0.1.cdp.txt" and index["status_file"] == "10.0.0.1.status.txt"
    assert index["lag_file"] == "10.0.0.1.lag.txt" and index["file"] == "10.0.0.1.txt"
    replay = FilePoller(tmp_path).poll("10.0.0.1", "cisco_ios")
    assert (replay.text, replay.extras["cdp"].text, replay.status_text, replay.lag_text) == (
        "lldp text",
        "cdp text",
        "status text",
        "lag text",
    )


# --------------------------------------------------------------------- replay
def test_replay_reads_every_protocol_and_the_status_capture(demo_dir):
    raw = FilePoller(demo_dir).poll("10.0.0.1", "cisco_ios")
    assert (
        raw.text
        and raw.extras["cdp"].text
        and raw.status_text
        and raw.lag_text
        and raw.lldp_note == ""
    )


def test_replay_of_a_device_without_lldp_uses_its_cdp_capture(demo_dir):
    raw = FilePoller(demo_dir).poll("10.0.0.30", "cisco_ios")
    assert raw.text == "" and "no LLDP capture" in raw.lldp_note and raw.extras["cdp"].text


def test_replay_ignores_protocols_that_were_not_asked_for(demo_dir):
    raw = FilePoller(demo_dir, protocols=("lldp",)).poll("10.0.0.1", "cisco_ios")
    assert raw.extras == {}
    with pytest.raises(PollerError, match="No replay data for 10.0.0.30"):
        FilePoller(demo_dir, protocols=("lldp",)).poll("10.0.0.30", "cisco_ios")


def test_replay_skips_protocols_the_platform_does_not_have(demo_dir):
    raw = FilePoller(demo_dir).poll("10.0.2.1", "arista_eos")  # EOS has no CDP profile
    assert "cdp" not in raw.extras
