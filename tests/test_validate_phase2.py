"""Tests for scripts/validate_phase2.py — the post-Phase-2 data validator.

Builds tiny synthetic output/ trees and asserts: clean data PASSES; each A-level violation FAILS
(non-zero); B-level issues WARN without failing. No network, no real pipeline.
"""

import json
from pathlib import Path

import pytest

import importlib.util

_SPEC = importlib.util.spec_from_file_location(
    "validate_phase2",
    Path(__file__).resolve().parents[1] / "scripts" / "validate_phase2.py",
)
vp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(vp)

from src.schemas import entity_version

ULID1 = "01HX7YZABCDEFGHJKMNPQRSTVW"
ULID2 = "01HX7YZABCDEFGHJKMNPQRSTVX"
EVID = "01HX7YZABCDEFGHJKMNPQRSTEA"
SEID = "01HX7YZABCDEFGHJKMNPQRSTEB"


def _write(root: Path, entity: str, name: str, rec: dict):
    d = root / entity
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps(rec), encoding="utf-8")


def _valid_person(pid=ULID1, name="Courtney H. Hodges", enriched=True):
    rec = {
        "_schema_version": entity_version("people"),
        "PersonID": pid,
        "name": name,
        "event_mentions": [
            {
                "MentionID": ULID2,
                "EventID": EVID,
                "Sub-eventID": SEID,
                "book": "RoerDams",
                "original_text": "General Hodges directed the crossing.",
            }
        ],
    }
    if enriched:
        rec["enrichment_status"] = "enriched"
        rec["biographical_profile"] = {
            "wikipedia_url": "https://en.wikipedia.org/wiki/Courtney_Hodges",
            "biography_sources": [
                {"url": "https://en.wikipedia.org/wiki/Courtney_Hodges"}
            ],
        }
    return rec


def _write_event(root: Path):
    """Minimal content/<book>/*-event.json so the people mention's EventID/Sub-eventID resolve."""
    d = root / "content" / "RoerDams"
    d.mkdir(parents=True, exist_ok=True)
    (d / "chapter1-event.json").write_text(
        json.dumps(
            {
                "_schema_version": entity_version("events"),
                "Event": {"EventID": EVID, "Sub-events": [{"Sub-eventID": SEID}]},
            }
        ),
        encoding="utf-8",
    )


def _clean_tree(tmp_path: Path) -> Path:
    root = tmp_path / "output"
    _write(root, "people", "hodges.json", _valid_person())
    _write_event(root)  # satisfies referential integrity (EventID/Sub-eventID exist)
    return root


def _run(root: Path, strict=False) -> int:
    return vp.run(str(root), strict)


def test_clean_tree_passes(tmp_path):
    assert _run(_clean_tree(tmp_path)) == 0


def test_missing_output_root_fails(tmp_path):
    assert _run(tmp_path / "nope") == 1


def test_bad_schema_version_fails(tmp_path):
    root = _clean_tree(tmp_path)
    rec = _valid_person()
    rec["_schema_version"] = "99.0"  # != target
    _write(root, "people", "hodges.json", rec)
    assert _run(root) == 1


def test_missing_required_field_fails(tmp_path):
    root = _clean_tree(tmp_path)
    rec = _valid_person()
    del rec["name"]  # people requires [PersonID, name]
    _write(root, "people", "hodges.json", rec)
    assert _run(root) == 1


def test_extra_field_fails(tmp_path):
    root = _clean_tree(tmp_path)
    rec = _valid_person()
    rec["totally_made_up_field"] = "x"  # additionalProperties: false
    _write(root, "people", "hodges.json", rec)
    assert _run(root) == 1


def test_orphan_no_mention_fails(tmp_path):
    root = _clean_tree(tmp_path)
    rec = _valid_person()
    rec["event_mentions"] = []  # no source anchor
    _write(root, "people", "hodges.json", rec)
    assert _run(root) == 1


def test_enriched_but_unlinked_fails(tmp_path):
    root = _clean_tree(tmp_path)
    rec = _valid_person(enriched=True)
    # enriched flag set, but NO source link anywhere (un-linkable to its source)
    rec["enrichment_status"] = "enriched"
    rec["biographical_profile"] = {"biography_sources": []}
    _write(root, "people", "hodges.json", rec)
    assert _run(root) == 1


def test_zero_enriched_warns_not_strict_but_fails_strict(tmp_path):
    root = tmp_path / "output"
    _write(root, "people", "p.json", _valid_person(enriched=False))
    _write_event(root)  # isolate the enrichment signal (no dangling-ref noise)
    # default: 0-of-N enrichment is a WARNING (pre-Phase-3 standalone run) -> PASS
    assert _run(root, strict=False) == 0
    # strict: HARD fail
    assert _run(root, strict=True) == 1


def test_dangling_reference_fails(tmp_path):
    """A mention referencing an EventID with NO matching event file = dangling (A3/B1 hard fail).
    This exercises the headline referential-integrity check for its actual target."""
    root = tmp_path / "output"
    _write(root, "people", "hodges.json", _valid_person())  # references EVID/SEID
    # deliberately NO event file -> EventID/Sub-eventID dangle
    assert _run(root) == 1


def test_missing_schema_version_fails(tmp_path):
    root = _clean_tree(tmp_path)
    rec = _valid_person()
    rec.pop("_schema_version", None)  # un-stamped record must be caught (A1)
    _write(root, "people", "hodges.json", rec)
    assert _run(root) == 1


def test_non_dict_mention_fails_not_crashes(tmp_path):
    root = _clean_tree(tmp_path)
    rec = _valid_person()
    rec["event_mentions"] = ["not-a-dict"]  # malformed — must FAIL, not crash
    _write(root, "people", "hodges.json", rec)
    assert _run(root) == 1  # returns cleanly (no traceback)


def test_mention_without_eventid_fails(tmp_path):
    root = _clean_tree(tmp_path)
    rec = _valid_person()
    rec["event_mentions"] = [
        {"MentionID": ULID2, "book": "RoerDams", "original_text": "x"}
    ]
    _write(root, "people", "hodges.json", rec)
    assert _run(root) == 1


def test_non_dict_biographical_profile_fails_not_crashes(tmp_path):
    root = _clean_tree(tmp_path)
    rec = _valid_person(enriched=False)
    rec["biographical_profile"] = [
        "oops"
    ]  # malformed — must not crash _enrichment_signals
    _write(root, "people", "hodges.json", rec)
    # schema (A2) will reject the list-typed biographical_profile; key point: no crash, exit 1
    assert _run(root) == 1
