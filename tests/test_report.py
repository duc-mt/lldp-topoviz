import pytest

from src.models import BLIND_SPOT_REASONS, BlindSpot
from src.report import blind_spots_payload, format_blind_spots


def make(**kw):
    values = {
        "hostname": "SW-ACC-BLDG-B",
        "chassis_id": "00:1a:2b:3c:4d:5e",
        "management_ip": "10.10.99.12",
        "discovered_via": "SW-CORE-1",
        "parent_port": "Gi1/0/24",
        "reason": "OUT_OF_SCOPE",
        "detail": "management IP 10.10.99.12 is outside crawl.allowed_subnets",
    }
    return BlindSpot(**{**values, **kw})


def test_the_report_follows_the_documented_layout():
    text = format_blind_spots(
        [
            make(),
            make(
                hostname="SW-ACC-FLOOR3",
                chassis_id="",
                management_ip=None,
                discovered_via="SW-DIS-02",
                parent_port="Gi1/0/12",
                reason="AUTH_FAILURE",
                detail="Authentication failed for 10.0.2.3",
            ),
        ],
        crawled=14,
    )
    assert text.splitlines() == [
        "[WARN] TOPOLOGY BLIND SPOTS (INCOMPLETE PHYSICAL COVERAGE):",
        "- 14 device(s) successfully crawled.",
        "- 2 blind spot(s) detected (downstream branches missing):",
        "  1. Hostname: 'SW-ACC-BLDG-B' (Chassis: 00:1a:2b:3c:4d:5e)",
        "     -> Discovered via: SW-CORE-1 on port Gi1/0/24 (IP: 10.10.99.12)",
        "     -> Reason: OUT_OF_SCOPE (IP not in crawl.allowed_subnets)",
        "     -> Impact: anything behind 'SW-ACC-BLDG-B' is not mapped; "
        "the diagram ends at SW-CORE-1.",
        "     -> Fix: Add 10.10.99.0/24 to crawl.allowed_subnets in settings.yaml "
        "(or pass --allow 10.10.99.0/24).",
        "  2. Hostname: 'SW-ACC-FLOOR3'",
        "     -> Discovered via: SW-DIS-02 on port Gi1/0/12",
        "     -> Reason: AUTH_FAILURE (SSH authentication failed)",
        "     -> Detail: Authentication failed for 10.0.2.3",
        "     -> Impact: anything behind 'SW-ACC-FLOOR3' is not mapped; "
        "the diagram ends at SW-DIS-02.",
        "     -> Fix: Check the credentials (LLDP_MAPPER_USERNAME / LLDP_MAPPER_PASSWORD, enable "
        "secret) and that the device's AAA (local, TACACS+ or RADIUS) lets this account log in.",
    ]


def test_no_blind_spots_prints_nothing():
    assert format_blind_spots([], crawled=7) == ""


def test_the_impact_line_never_claims_a_device_count():
    # nothing behind an uncrawled device is ever seen, so a number would be a guess
    assert "~" not in format_blind_spots([make()], crawled=3)
    assert "switches" not in format_blind_spots([make()], crawled=3).split("Impact:")[1]


def test_every_reason_has_a_fix_and_an_explanation():
    for reason in BLIND_SPOT_REASONS:
        text = format_blind_spots([make(reason=reason, management_ip=None)], crawled=1)
        assert f"Reason: {reason} (" in text, reason
        assert "-> Fix: " in text and "See the detail above" not in text, reason


def test_the_scope_fix_falls_back_when_there_is_no_usable_address():
    text = format_blind_spots([make(management_ip=None)], crawled=1)
    assert "Add its management subnet to crawl.allowed_subnets" in text


def test_terminal_escape_sequences_from_the_network_are_stripped():
    text = format_blind_spots(
        [make(hostname="evil\x1b[2J\x07name", discovered_via="sw\r\n1", detail="x\x1b[31m")],
        crawled=1,
    )
    assert "\x1b" not in text and "\x07" not in text and "\r" not in text
    assert "evil[2Jname" in text


def test_a_missing_hostname_falls_back_to_the_chassis_then_the_address():
    assert "Hostname: '00:1a:2b:3c:4d:5e'" in format_blind_spots([make(hostname="")], crawled=1)
    text = format_blind_spots([make(hostname="", chassis_id="")], crawled=1)
    assert "Hostname: '10.10.99.12'" in text


def test_the_payload_is_json_friendly_and_complete():
    payload = blind_spots_payload([make(node_id="sw-acc-bldg-b", parent_id="sw-core-1")])
    assert payload == [
        {
            "hostname": "SW-ACC-BLDG-B",
            "chassis_id": "00:1a:2b:3c:4d:5e",
            "management_ip": "10.10.99.12",
            "discovered_via": "SW-CORE-1",
            "parent_port": "Gi1/0/24",
            "reason": "OUT_OF_SCOPE",
            "detail": "management IP 10.10.99.12 is outside crawl.allowed_subnets",
            "node_id": "sw-acc-bldg-b",
            "parent_id": "sw-core-1",
        }
    ]


# ------------------------------------------------------------- long lists are cut short
def many(count):
    return [make(hostname=f"SW-{i:02d}", parent_port=f"Gi1/0/{i}") for i in range(1, count + 1)]


def entries(text):
    return [line for line in text.splitlines() if "Hostname:" in line]


def test_more_blind_spots_than_the_limit_are_cut_with_a_summary_line():
    text = format_blind_spots(many(20), crawled=3)
    assert len(entries(text)) == 15  # the default limit
    assert "  1. Hostname: 'SW-01'" in text and "  15. Hostname: 'SW-15'" in text
    assert "SW-16" not in text and "SW-20" not in text
    assert text.splitlines()[-1] == (
        "  ... and 5 more blind spot(s) (see HTML viewer or snapshot for full list)."
    )
    assert "- 20 blind spot(s) detected" in text  # the headline counts all of them


def test_a_list_at_the_limit_is_printed_whole_without_a_summary():
    text = format_blind_spots(many(15), crawled=3)
    assert len(entries(text)) == 15 and "more blind spot(s)" not in text
    assert "SW-15" in text


def test_one_over_the_limit_says_one_more():
    text = format_blind_spots(many(16), crawled=3)
    assert len(entries(text)) == 15
    assert text.splitlines()[-1].startswith("  ... and 1 more blind spot(s) (see HTML viewer")


def test_the_limit_can_be_set():
    text = format_blind_spots(many(5), crawled=3, limit=2)
    assert [e.strip().split(" Hostname:")[0] for e in entries(text)] == ["1.", "2."]
    assert "  ... and 3 more blind spot(s)" in text
    assert "SW-03" not in text


@pytest.mark.parametrize("limit", [0, -1, -7])
def test_a_limit_below_one_lists_none_and_never_slices_from_the_end(limit):
    text = format_blind_spots(many(4), crawled=3, limit=limit)
    assert entries(text) == []
    assert text.splitlines()[-1].startswith("  ... and 4 more blind spot(s)")


def test_cutting_the_console_list_does_not_cut_the_export():
    spots = many(20)
    assert len(blind_spots_payload(spots)) == 20  # the viewer and the snapshot keep everything
