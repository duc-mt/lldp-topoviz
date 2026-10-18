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
