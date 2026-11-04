"""Guard: the provenance audit must recognize every REAL provenance shape the pipeline writes.

XChannel ch8 e2e raised "115 records cannot be traced to source", of which 109 were false:
  * bibliography (101): footnote mentions pin book+chapter+reference_number+verbatim text
    but carry no EventID;
  * casualties (7): EventID nested under ``source`` / ``event_context``;
  * people (1): ``not_people.json`` (an exclusion list) audited as a person record.
A record that truly has no link (an image with EventID/SourceSectionID null) must still flag.
"""

import json

from src.utils import provenance_integrity as pi


def test_bibliography_footnote_mention_traces():
    rec = {
        "BibliographyID": "B1",
        "mentions": [
            {
                "MentionID": "M1",
                "book": "XChannel",
                "chapter": "chapter8b",
                "reference_type": "footnote",
                "reference_number": "54",
                "verbatim_reference": "See Phantom Intercept in 21 A Gp Sitrep 4.",
            }
        ],
    }
    assert pi._record_has_populated_provenance(rec) is True


def test_mention_without_locator_or_event_does_not_trace():
    rec = {"mentions": [{"book": "XChannel", "chapter": "chapter8b"}]}
    assert pi._record_has_populated_provenance(rec) is False
    rec = {"mentions": [{"verbatim_reference": "x"}]}  # no book/chapter
    assert pi._record_has_populated_provenance(rec) is False


def _note(**kw):
    m = {"book": "XChannel", "chapter": "chapter8b", "reference_type": "footnote"}
    m.update(kw)
    return {"mentions": [m]}


def test_bad_locators_do_not_trace():
    for bad in (False, True, 0, -3, "", "   ", None):
        assert pi._record_has_populated_provenance(_note(reference_number=bad)) is False
    assert pi._record_has_populated_provenance(_note(paragraph_number=11)) is True


def test_locator_rule_is_scoped_to_note_mentions():
    rec = {"mentions": [{"book": "X", "chapter": "c", "paragraph_number": 3}]}
    assert pi._record_has_populated_provenance(rec) is False


def test_empty_or_non_dict_nested_ref_does_not_trace():
    for v in ({"EventID": ""}, None, "E1", {"EventID": "  "}):
        assert pi._record_has_populated_provenance({"source": v}) is False


def test_casualty_nested_event_refs_trace():
    for key in ("source", "event_context"):
        rec = {"CasualtyID": "C1", key: {"EventID": "E1", "Sub-eventID": "S1"}}
        assert pi._record_has_populated_provenance(rec) is True


def test_image_with_null_links_still_flags():
    rec = {
        "ImageID": "I1",
        "EventID": None,
        "Sub-eventID": None,
        "SourceSectionID": None,
    }
    assert pi._record_has_populated_provenance(rec) is False


def test_not_star_aux_files_are_skipped(tmp_path):
    d = tmp_path / "people"
    d.mkdir()
    (d / "not_people.json").write_text(
        json.dumps({"patterns": [], "auto_caught": ["X"]}), encoding="utf-8"
    )
    (d / "jane doe.json").write_text(json.dumps({"PersonID": "P"}), encoding="utf-8")
    (d / "not_a real record.json").write_text(json.dumps({"PersonID": "Q"}), "utf-8")
    names = sorted(n for n, _ in pi._iter_entity_records(tmp_path, "people/*.json"))
    assert names == ["jane doe.json", "not_a real record.json"]


def test_unreadable_record_is_loud(tmp_path, monkeypatch):
    import src.utils.observe as obs

    seen = []
    monkeypatch.setattr(obs, "observe_warn", lambda *a, **k: seen.append((a, k)))
    d = tmp_path / "people"
    d.mkdir()
    (d / "broken.json").write_text("{nope", encoding="utf-8")
    assert pi._iter_entity_records(tmp_path, "people/*.json") == []
    assert seen and seen[0][0] == ("provenance", "record_unreadable")
