import pytest

from src.textparse import TemplateMismatch, looks_empty, parse_text

TEMPLATE = """Value NAME (\\S+)
Value PORT (\\S+)

Start
  ^${NAME}\\s+${PORT}\\s*$$ -> Record
"""


@pytest.fixture
def template(tmp_path):
    path = tmp_path / "t.textfsm"
    path.write_text(TEMPLATE)
    return path


def test_rows_come_back_as_dicts(template):
    rows = parse_text("sw1 Gi1\nsw2 Gi2\n", template)
    assert rows == [{"NAME": "sw1", "PORT": "Gi1"}, {"NAME": "sw2", "PORT": "Gi2"}]


@pytest.mark.parametrize(
    "text",
    [
        "",
        "\n\n",
        "LLDP is not enabled\n",
        "% no neighbors\n",
        "Total entries displayed : 0\n",
        "Number of channel-groups in use: 0\nNumber of aggregators:           0\n",
    ],
)
def test_output_that_says_there_is_nothing_is_an_empty_answer_not_a_failure(template, text):
    assert parse_text(text, template) == []


def test_long_output_without_a_single_record_is_a_mismatch(template):
    text = "\n".join(
        f"this is some other platform's line {i} with several words in it" for i in range(8)
    )
    with pytest.raises(TemplateMismatch, match="no record found"):
        parse_text(text, template)


def test_a_template_that_rejects_the_text_is_a_mismatch(tmp_path):
    strict = tmp_path / "strict.textfsm"
    strict.write_text("Value A (\\S+)\n\nStart\n  ^ok ${A} -> Record\n  ^. -> Error\n")
    with pytest.raises(TemplateMismatch):
        parse_text("ok x\nsurprise line\nanother\nmore\n", strict)


def test_a_missing_or_broken_template_is_a_mismatch(tmp_path):
    with pytest.raises(TemplateMismatch):
        parse_text("x y\n", tmp_path / "absent.textfsm")


def test_looks_empty():
    assert (
        looks_empty("")
        and looks_empty("one\ntwo\nthree")
        and looks_empty("a\nb\nc\nd\n% LLDP is not enabled")
    )
    assert not looks_empty("a\nb\nc\nd\ne")
