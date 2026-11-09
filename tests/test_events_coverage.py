"""Event coverage: every entity extractor reads the sub-events' fulltext, so a chapter whose
sub-events leave out much of its narrative is an actionable error, not a silent partial yield.
"""

import json
from unittest.mock import patch

from src.extraction import events as E


def _parsed(tmp_path, n=10, footnotes=0):
    paras = [{"absolute_number": i, "text": "x" * 100} for i in range(1, n + 1)]
    if footnotes:
        paras.append({"absolute_number": n + 1, "text": "### Footnotes"})
        paras += [
            {"absolute_number": n + 2 + i, "text": "note " * 50}
            for i in range(footnotes)
        ]
    f = tmp_path / "chapter1-parsed.json"
    f.write_text(json.dumps({"paragraphs": paras}))
    return f


def _response(*paragraph_sets):
    return {
        "Event": {
            "Sub-events": [
                {"Sub-event_fulltext": {f"Paragraph_{n}": "x" for n in ps}}
                for ps in paragraph_sets
            ]
        }
    }


def test_full_coverage_raises_no_alert(tmp_path):
    with patch("src.utils.observe.observe_error") as alert:
        E._observe_coverage(_response(range(1, 6), range(6, 11)), _parsed(tmp_path))
    alert.assert_not_called()


def test_low_coverage_alerts_with_the_uncovered_ranges(tmp_path):
    with patch("src.utils.observe.observe_error") as alert:
        E._observe_coverage(_response(range(1, 4)), _parsed(tmp_path))
    assert alert.call_args[0][:2] == ("events", "coverage_low")
    assert alert.call_args.kwargs["uncovered"] == ["4-10"]
    assert alert.call_args.kwargs["share"] == 0.3


def test_footnotes_do_not_count_as_uncovered_narrative(tmp_path):
    with patch("src.utils.observe.observe_error") as alert:
        E._observe_coverage(_response(range(1, 11)), _parsed(tmp_path, footnotes=5))
    alert.assert_not_called()


def test_an_unreadable_parsed_file_never_breaks_extraction(tmp_path):
    E._observe_coverage(_response(range(1, 3)), tmp_path / "missing-parsed.json")


def test_ranges():
    assert E._ranges([3, 4, 5, 9, 11, 12]) == ["3-5", "9", "11-12"]
    assert E._ranges([]) == []


def test_tables_are_not_narrative_the_extractors_must_cover(tmp_path):
    paras = [{"absolute_number": i, "text": "x" * 100} for i in range(1, 4)]
    paras += [{"absolute_number": 4, "text": "<table>" + "x" * 5000 + "</table>"}]
    f = tmp_path / "chapter1-parsed.json"
    f.write_text(json.dumps({"paragraphs": paras}))
    with patch("src.utils.observe.observe_error") as alert:
        E._observe_coverage(_response(range(1, 4)), f)
    alert.assert_not_called()
