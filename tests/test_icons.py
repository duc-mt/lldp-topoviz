import xml.etree.ElementTree as ET
from urllib.parse import unquote

import pytest

from src.icons import ICON_KINDS, RING, icon_svg, icon_uri


@pytest.mark.parametrize("kind", ICON_KINDS)
@pytest.mark.parametrize("seed", [False, True])
def test_every_icon_is_well_formed_svg(kind, seed):
    root = ET.fromstring(icon_svg(kind, "#2F9E6E", seed))
    assert root.tag.endswith("svg") and root.get("viewBox") == "0 0 64 64"
    assert "{f}" not in icon_svg(kind, "#2F9E6E", seed)  # the status colour was applied


def test_all_expected_types_exist_and_differ():
    assert set(ICON_KINDS) == {"switch", "router", "firewall", "ap", "phone", "endpoint", "unknown"}
    assert len({icon_svg(k, "#2F9E6E") for k in ICON_KINDS}) == len(ICON_KINDS)


def test_status_colour_and_seed_ring():
    assert "#D64545" in icon_svg("switch", "#D64545") and RING not in icon_svg("switch", "#D64545")
    assert RING in icon_svg("switch", "#D64545", seed=True)


def test_unknown_kind_falls_back_to_the_question_mark():
    assert icon_svg("toaster", "#000") == icon_svg("unknown", "#000")


def test_data_uri_roundtrips_and_is_self_contained():
    uri = icon_uri("router", "#2F9E6E")
    assert uri.startswith("data:image/svg+xml;charset=utf-8,")
    assert unquote(uri.split(",", 1)[1]) == icon_svg("router", "#2F9E6E")
    assert "http" not in unquote(uri).replace("http://www.w3.org/2000/svg", "")  # nothing to fetch
