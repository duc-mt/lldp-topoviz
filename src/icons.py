"""
==============================================================================
Module Name:   icons.py
Description:   Inline SVG icons (as data URIs) for each device type, filled
               with the crawl-status colour; the seed device gets a gold ring.
Author:        Mai Tan Duc <ducmai.network@gmail.com>
Created:       2026-10-11
Version:       1.2.0
License:       MIT
==============================================================================
Usage:         imported by src/visualizer.py (not run directly)
Notes:         Requires Python 3.10+. Data URIs keep the diagram a single
               self-contained file: nothing is fetched at view time.
==============================================================================
"""

from __future__ import annotations

from functools import cache
from urllib.parse import quote

RING = "#E0A100"
_STROKE = (
    'stroke="#fff" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" fill="none"'
)

# Glyphs are drawn in a 64x64 box; "{f}" is the status colour.
_GLYPHS = {
    # Square with two opposite arrows: a switch.
    "switch": (
        '<rect x="7" y="14" width="50" height="36" rx="7" fill="{f}"/>'
        f'<path d="M16 26h27M37 20l7 6-7 6M48 38H21M27 32l-7 6 7 6" {_STROKE}/>'
    ),
    # Circle with arrows pointing every way: a router.
    "router": (
        '<circle cx="32" cy="32" r="26" fill="{f}"/>'
        f'<path d="M17 32h30M41 26l6 6-6 6M23 26l-6 6 6 6M32 17v30M26 41l6 6 6-6M26 23l6-6 6 6" {_STROKE}/>'
    ),
    # A brick wall: a firewall.
    "firewall": (
        '<rect x="7" y="11" width="50" height="42" rx="5" fill="{f}"/>'
        '<path d="M7 25h50M7 39h50M24 11v14M40 11v14M16 25v14M32 25v14M48 25v14M24 39v14M40 39v14"'
        ' stroke="#fff" stroke-width="3" fill="none"/>'
    ),
    # Radio waves over a dot: a wireless access point.
    "ap": (
        '<circle cx="32" cy="32" r="26" fill="{f}"/>'
        '<circle cx="32" cy="42" r="3.5" fill="#fff"/>'
        f'<path d="M23 36a13 13 0 0 1 18 0M17 30a21 21 0 0 1 30 0" {_STROKE}/>'
    ),
    # Handset body with a keypad: an IP phone.
    "phone": (
        '<rect x="14" y="7" width="36" height="50" rx="7" fill="{f}"/>'
        '<rect x="20" y="13" width="24" height="10" rx="2" fill="#fff"/>'
        '<g fill="#fff"><circle cx="24" cy="32" r="2.6"/><circle cx="32" cy="32" r="2.6"/>'
        '<circle cx="40" cy="32" r="2.6"/><circle cx="24" cy="40" r="2.6"/>'
        '<circle cx="32" cy="40" r="2.6"/><circle cx="40" cy="40" r="2.6"/>'
        '<circle cx="24" cy="48" r="2.6"/><circle cx="32" cy="48" r="2.6"/>'
        '<circle cx="40" cy="48" r="2.6"/></g>'
    ),
    # A monitor on a stand: an endpoint.
    "endpoint": (
        '<rect x="7" y="11" width="50" height="34" rx="4" fill="{f}"/>'
        '<rect x="13" y="17" width="38" height="22" rx="2" stroke="#fff" stroke-width="2.5" fill="none"/>'
        '<rect x="28" y="45" width="8" height="6" fill="{f}"/>'
        '<rect x="19" y="51" width="26" height="4" rx="2" fill="{f}"/>'
    ),
    # A question mark: not identified.
    "unknown": (
        '<circle cx="32" cy="32" r="26" fill="{f}"/>'
        f'<path d="M24 26a8 8 0 1 1 11 7c-3 2-3 4-3 7" {_STROKE}/>'
        '<circle cx="32" cy="48" r="3" fill="#fff"/>'
    ),
}

ICON_KINDS = tuple(_GLYPHS)


@cache
def icon_svg(kind: str, fill: str, seed: bool = False) -> str:
    """The icon as an SVG document (unknown kinds fall back to the question mark)."""
    glyph = _GLYPHS.get(kind, _GLYPHS["unknown"]).replace("{f}", fill)
    if seed:  # shrink the glyph a little and put a gold ring around it
        body = (
            f'<circle cx="32" cy="32" r="30" fill="#fff" stroke="{RING}" stroke-width="4"/>'
            f'<g transform="translate(5.1 5.1) scale(0.84)">{glyph}</g>'
        )
    else:
        body = glyph
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">'
        f"{body}</svg>"
    )


def icon_uri(kind: str, fill: str, seed: bool = False) -> str:
    """The icon as a ``data:`` URI, ready for a vis-network ``image`` node."""
    return "data:image/svg+xml;charset=utf-8," + quote(icon_svg(kind, fill, seed), safe="")
