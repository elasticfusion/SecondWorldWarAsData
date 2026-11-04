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
    names = [n for n, _ in pi._iter_entity_records(tmp_path, "people/*.json")]
    assert names == ["jane doe.json"]
