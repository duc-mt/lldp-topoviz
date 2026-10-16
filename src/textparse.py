"""
==============================================================================
Module Name:   textparse.py
Description:   Runs a TextFSM template on CLI text and tells "the output has no
               records" apart from "the output is not what the template expects".
               Netmiko's use_textfsm=True cannot: it hands back the raw string in
               both cases.
Author:        Mai Tan Duc <ducmai.network@gmail.com>
Created:       2026-10-13
Version:       1.3.0
License:       MIT
==============================================================================
Usage:         imported by src/poller.py and src/parser.py (not run directly)
Notes:         Requires Python 3.10+
==============================================================================
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

import textfsm

from src import MapperError


class TemplateMismatch(MapperError):
    """The text is not output the template can read (wrong platform, or an unexpected format)."""


# Output that legitimately has no records ("LLDP is not enabled", "Total entries displayed : 0").
_EMPTY_HINT = re.compile(
    r"not (?:enabled|running|configured)"
    r"|no (?:lldp |cdp )?(?:neighbors?|entries|channel|port-channel)"
    r"|entries displayed\s*:\s*0|number of channel-groups in use:\s*0"
    r"|\b0 neighbors?\b|is disabled",
    re.IGNORECASE,
)


def looks_empty(text: str) -> bool:
    """True for output that plainly says there is nothing to list (or is only a line or two)."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return len(lines) <= 3 or any(_EMPTY_HINT.search(line) for line in lines)


def parse_text(text: str, template: Path) -> list[dict[str, Any]]:
    """Parse ``text`` with the TextFSM ``template`` into a list of row dicts.

    An empty list is a valid answer (the device has nothing to report) when the text says so
    or is trivially short; a non-trivial text that yields no record at all is treated as a
    mismatch, because that is what the output of the wrong platform looks like.

    Raises:
        TemplateMismatch: If TextFSM rejects the text, or finds no record in a long text.
    """
    try:
        with Path(template).open(encoding="utf-8") as handle:
            fsm = textfsm.TextFSM(io.StringIO(handle.read()))
        rows = [dict(zip(fsm.header, values, strict=False)) for values in fsm.ParseText(text)]
    except (textfsm.TextFSMError, OSError, ValueError) as exc:
        raise TemplateMismatch(str(exc)) from exc
    if not rows and not looks_empty(text):
        snippet = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        raise TemplateMismatch(f"no record found (first line: {snippet!r})")
    return rows
