"""
==============================================================================
Module Name:   report.py
Description:   Text report for the crawl blind-spot audit: which neighbors were
               seen but not crawled, where they were seen, why, what is missing
               from the diagram as a result and how to fix it. Also the per-seed
               status lines of a multi-seed crawl, the VLAN mismatch list and the
               crawl history listing.
Author:        Mai Tan Duc <ducmai.network@gmail.com>
Created:       2026-10-17
Version:       1.0.0
License:       MIT
==============================================================================
Usage:         imported by main.py (not run directly)
Notes:         Requires Python 3.10+. Pure functions: no I/O. Names and error text come
               from the network, so control characters are stripped before printing.
==============================================================================
"""

from __future__ import annotations

import ipaddress
import re
import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, TextIO

from src.models import (
    REASON_AUTH_FAILURE,
    REASON_CONNECT_FAILURE,
    REASON_CRAWL_ERROR,
    REASON_EXCLUDED,
    REASON_LIMIT_REACHED,
    REASON_NO_MGMT_IP,
    REASON_OUT_OF_SCOPE,
    REASON_TIMEOUT,
    REASON_UNSUPPORTED_OS,
    BlindSpot,
)

if TYPE_CHECKING:
    from src.crawler import SeedResult

__all__ = [
    "blind_spots_payload",
    "describe_changes",
    "format_blind_spots",
    "format_history",
    "format_seed_summary",
    "seeds_payload",
    "status_symbols",
]

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

#: One-line explanation printed after the reason code.
_EXPLANATION = {
    REASON_OUT_OF_SCOPE: "IP not in crawl.allowed_subnets",
    REASON_AUTH_FAILURE: "SSH authentication failed",
    REASON_TIMEOUT: "SSH session timed out",
    REASON_UNSUPPORTED_OS: "no LLDP profile for this platform",
    REASON_CONNECT_FAILURE: "SSH session could not be opened",
    REASON_NO_MGMT_IP: "no management IP advertised",
    REASON_EXCLUDED: "matches crawl.exclude_hostnames",
    REASON_LIMIT_REACHED: "crawl.max_depth / crawl.max_devices reached",
    REASON_CRAWL_ERROR: "the device could not be read",
}
#: Reasons whose ``detail`` is a device error worth printing (the others are fully explained above).
_SHOW_DETAIL = {
    REASON_AUTH_FAILURE,
    REASON_TIMEOUT,
    REASON_CONNECT_FAILURE,
    REASON_CRAWL_ERROR,
    REASON_UNSUPPORTED_OS,
}
_FIX = {
    REASON_AUTH_FAILURE: (
        "Check the credentials (LLDP_MAPPER_USERNAME / LLDP_MAPPER_PASSWORD, enable secret) and "
        "that the device's AAA (local, TACACS+ or RADIUS) lets this account log in."
    ),
    REASON_TIMEOUT: (
        "Check routing and ACLs from the crawler host to the device, and the device.* timeouts "
        "in settings.yaml."
    ),
    REASON_UNSUPPORTED_OS: (
        "Add a profile under custom_vendors, or set crawl.device_overrides for this device."
    ),
    REASON_CONNECT_FAILURE: "Check that SSH is enabled on the device and not blocked by an ACL.",
    REASON_NO_MGMT_IP: "Give it an address under crawl.name_to_ip in settings.yaml.",
    REASON_EXCLUDED: "Remove it from crawl.exclude_hostnames if it should be mapped.",
    REASON_LIMIT_REACHED: "Raise crawl.max_depth / crawl.max_devices (or the CLI flags).",
    REASON_CRAWL_ERROR: "See the error above; re-run with --save-raw to inspect the output.",
}


def _clean(text: object) -> str:
    return _CONTROL_CHARS.sub("", str(text or "")).strip()


def _scope_fix(spot: BlindSpot) -> str:
    """The allowed_subnets entry that would bring the device into scope (its /24, as a start)."""
    try:
        subnet = ipaddress.ip_network(f"{spot.management_ip}/24", strict=False)
    except ValueError:
        return "Add its management subnet to crawl.allowed_subnets in settings.yaml."
    return f"Add {subnet} to crawl.allowed_subnets in settings.yaml (or pass --allow {subnet})."


def _fix(spot: BlindSpot) -> str:
    if spot.reason == REASON_OUT_OF_SCOPE:
        return _scope_fix(spot)
    return _FIX.get(spot.reason, "See the detail above.")


def format_blind_spots(blind_spots: Sequence[BlindSpot], crawled: int, limit: int = 15) -> str:
    """The blind-spot section of the console report ('' when there are none).

    Args:
        blind_spots: What ``CrawlResult.blind_spots`` holds.
        crawled: How many devices were crawled successfully (for the coverage line).
        limit: How many blind spots to list in full. A bigger list is cut to the first
            ``limit`` followed by one summary line; the counts above it always cover all of
            them, and the HTML viewer and the snapshot carry the full list. ``0`` lists none.
    """
    if not blind_spots:
        return ""
    count = len(blind_spots)
    lines = [
        "[WARN] TOPOLOGY BLIND SPOTS (INCOMPLETE PHYSICAL COVERAGE):",
        f"- {crawled} device(s) successfully crawled.",
        f"- {count} blind spot(s) detected (downstream branches missing):",
    ]
    shown = blind_spots[: max(limit, 0)]
    for number, spot in enumerate(shown, start=1):
        name = _clean(spot.hostname) or _clean(spot.chassis_id) or _clean(spot.management_ip)
        chassis = f" (Chassis: {_clean(spot.chassis_id)})" if _clean(spot.chassis_id) else ""
        lines.append(f"  {number}. Hostname: '{name}'{chassis}")

        parent = _clean(spot.discovered_via) or "an unknown device"
        port = f" on port {_clean(spot.parent_port)}" if _clean(spot.parent_port) else ""
        address = f" (IP: {_clean(spot.management_ip)})" if _clean(spot.management_ip) else ""
        lines.append(f"     -> Discovered via: {parent}{port}{address}")

        explanation = _EXPLANATION.get(spot.reason, "")
        lines.append(
            f"     -> Reason: {spot.reason}" + (f" ({explanation})" if explanation else "")
        )
        if spot.reason in _SHOW_DETAIL and _clean(spot.detail):
            lines.append(f"     -> Detail: {_clean(spot.detail)}")
        # What is missing cannot be counted: nothing behind an uncrawled device is ever seen.
        lines.append(
            f"     -> Impact: anything behind '{name}' is not mapped; the diagram ends at {parent}."
        )
        lines.append(f"     -> Fix: {_fix(spot)}")
    if len(blind_spots) > len(shown):
        lines.append(
            f"  ... and {len(blind_spots) - len(shown)} more blind spot(s) "
            "(see HTML viewer or snapshot for full list)."
        )
    return "\n".join(lines)


def blind_spots_payload(blind_spots: Sequence[BlindSpot]) -> list[dict[str, Any]]:
    """The ``blind_spots`` list as written to the snapshot metadata and the HTML viewer."""
    return [spot.to_dict() for spot in blind_spots]


# ------------------------------------------------------------ status symbols
def status_symbols(stream: TextIO | None = None) -> dict[str, str]:
    """``ok`` / ``fail`` / ``warn`` marks for the console: ``✔ ✖ ⚠``, or ASCII where the
    console encoding (a Windows code page, a C locale) cannot print them."""
    encoding = getattr(stream or sys.stdout, "encoding", None) or "ascii"
    try:
        "\u2714\u2716\u26a0".encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return {"ok": "[ok]", "fail": "[x]", "warn": "[!]"}
    return {"ok": "\u2714", "fail": "\u2716", "warn": "\u26a0"}


# ------------------------------------------------------------------- seeds
_SEED_MARK = {"crawled": "ok", "duplicate": "ok", "failed": "fail"}
_SEED_WORDS = {
    "crawled": "crawled",
    "duplicate": "already crawled",
    "failed": "failed",
    "out_of_scope": "out of scope (never contacted)",
    "skipped": "not reached",
}


def format_seed_summary(
    seeds: Sequence[SeedResult], symbols: dict[str, str] | None = None
) -> list[str]:
    """One line per seed of a multi-seed crawl: its status and how much it led to.

    Returns ``[]`` for a crawl with a single seed (the summary names it on one line then).
    """
    if len(seeds) < 2:
        return []
    marks = symbols or status_symbols()
    width = max(len(_clean(seed.host)) for seed in seeds)
    lines = []
    for seed in seeds:
        mark = marks[_SEED_MARK.get(seed.status, "warn")]
        text = _SEED_WORDS.get(seed.status, seed.status)
        if seed.status == "crawled":
            text += f": {seed.crawled} device(s) crawled, {seed.discovered} discovered"
        elif seed.detail:
            text += f": {_clean(seed.detail)}"
        lines.append(f"   {mark} {_clean(seed.host):<{width}}  {text}")
    return lines


def seeds_payload(seeds: Sequence[SeedResult]) -> list[dict[str, Any]]:
    """The per-seed results as written to the snapshot metadata and the web API."""
    return [
        {
            "host": seed.host,
            "device_type": seed.device_type,
            "status": seed.status,
            "detail": seed.detail,
            "crawled": seed.crawled,
            "discovered": seed.discovered,
        }
        for seed in seeds
    ]


# ----------------------------------------------------------------- history
def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


def describe_changes(diff: dict[str, Any]) -> str:
    """One line for the ``diff`` summary of a history run: ``+1 device, 1 cable moved ...``."""
    if diff.get("first_run"):
        return "first recorded run"
    bits = []
    for key, sign, noun in (
        ("devices_added", "+", "device"),
        ("devices_removed", "-", "device"),
        ("links_added", "+", "link"),
        ("links_removed", "-", "link"),
    ):
        if diff.get(key):
            bits.append(f"{sign}{_plural(diff[key], noun)}")
    if diff.get("reconnections"):
        bits.append(f"{_plural(diff['reconnections'], 'cable')} moved")
    if diff.get("speed_downgrades"):
        bits.append(_plural(diff["speed_downgrades"], "speed downgrade"))
    other = diff.get("speed_changes", 0) - diff.get("speed_downgrades", 0)
    if other > 0:
        bits.append(_plural(other, "speed upgrade"))
    if diff.get("new_blind_spots"):
        bits.append(_plural(diff["new_blind_spots"], "new blind spot"))
    return ", ".join(bits) or "no changes"


def format_history(runs: Sequence[dict[str, Any]]) -> str:
    """The ``--history`` listing: one entry per recorded crawl, newest first."""
    if not runs:
        return "No crawl has been recorded yet (a crawl without --dry-run adds one)."
    lines = [f"Crawl history ({len(runs)} run(s), newest first):"]
    for run in runs:
        when = _clean(run.get("timestamp"))[:19].replace("T", " ")
        seeds = ", ".join(_clean(seed) for seed in run.get("seeds") or []) or "?"
        lines.append(
            f"  {when}  {_clean(run.get('run_id'))}  "
            f"{run.get('devices', 0)} devices, {run.get('links', 0)} links, "
            f"{run.get('blind_spots', 0)} blind spot(s), {run.get('warnings', 0)} link warning(s)"
            + ("  [partial]" if run.get("partial") else "")
        )
        lines.append(f"      seeds: {seeds}")
        lines.append(f"      changes: {describe_changes(run.get('diff') or {})}")
    return "\n".join(lines)
