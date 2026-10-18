"""
==============================================================================
Module Name:   models.py
Description:   Plain data records shared by the crawler, the text report and the
               HTML viewer. Currently the crawl blind-spot record: a neighbor
               that was seen on the wire but could not be crawled.
Author:        Mai Tan Duc <ducmai.network@gmail.com>
Created:       2026-10-17
Version:       1.0.0
License:       MIT
==============================================================================
Usage:         imported by src/crawler.py, src/report.py and main.py
Notes:         Requires Python 3.10+. No I/O and no dependencies on other modules.
==============================================================================
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

__all__ = [
    "BLIND_SPOT_REASONS",
    "REASON_AUTH_FAILURE",
    "REASON_CONNECT_FAILURE",
    "REASON_CRAWL_ERROR",
    "REASON_EXCLUDED",
    "REASON_LIMIT_REACHED",
    "REASON_NO_MGMT_IP",
    "REASON_OUT_OF_SCOPE",
    "REASON_TIMEOUT",
    "REASON_UNSUPPORTED_OS",
    "BlindSpot",
]

REASON_OUT_OF_SCOPE = "OUT_OF_SCOPE"  # management IP outside crawl.allowed_subnets
REASON_AUTH_FAILURE = "AUTH_FAILURE"  # SSH login rejected (bad credentials, TACACS+/RADIUS ...)
REASON_TIMEOUT = "TIMEOUT"  # SSH session or command timed out
REASON_UNSUPPORTED_OS = "UNSUPPORTED_OS"  # no LLDP profile for the platform

# The four reasons above are the core set. The rest keep every other uncrawled infrastructure
# neighbor in the audit too: a coverage report that silently drops them would claim more than
# the crawl knows.
REASON_CONNECT_FAILURE = "CONNECT_FAILURE"  # SSH could not be opened (refused, unreachable)
REASON_NO_MGMT_IP = "NO_MGMT_IP"  # the neighbor advertised no management address
REASON_EXCLUDED = "EXCLUDED"  # matches crawl.exclude_hostnames
REASON_LIMIT_REACHED = "LIMIT_REACHED"  # crawl.max_depth / crawl.max_devices stopped the walk
REASON_CRAWL_ERROR = "CRAWL_ERROR"  # command rejected, output not parseable, other errors

BLIND_SPOT_REASONS = (
    REASON_OUT_OF_SCOPE,
    REASON_AUTH_FAILURE,
    REASON_TIMEOUT,
    REASON_UNSUPPORTED_OS,
    REASON_CONNECT_FAILURE,
    REASON_NO_MGMT_IP,
    REASON_EXCLUDED,
    REASON_LIMIT_REACHED,
    REASON_CRAWL_ERROR,
)


@dataclass(frozen=True)
class BlindSpot:
    """One infrastructure neighbor that was seen but not crawled, so its branch is unmapped.

    Leaf endpoints (phones, access points, servers) are never blind spots: they have no
    downstream network to miss.
    """

    hostname: str
    chassis_id: str
    management_ip: str | None
    discovered_via: str  # the crawled switch that reported it (its name as drawn)
    parent_port: str  # the port on that switch the neighbor was seen on
    reason: str  # one of BLIND_SPOT_REASONS
    detail: str  # the error text, or why the crawler skipped it
    node_id: str = ""  # the neighbor's node in the topology graph (lets the viewer focus it)
    parent_id: str = ""  # the reporting switch's node in the topology graph

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly form (all fields), used by the snapshot and the HTML viewer."""
        return asdict(self)
