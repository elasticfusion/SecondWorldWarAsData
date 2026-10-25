"""REAL-WRITE-PATH regression for the Phase-3 review BLOCKERs (B1/B2/B3). The prior tests asserted
in-memory dict state and so MISSED that the resolver wrote schema-invalid records that the guard
silently blocked while the pipeline reported 'resolved'. These drive records through the ACTUAL
write_json_with_lock guard and assert they persist (B1 availability='local', B2 source_metadata),
and that a genuinely-invalid record is NOT counted as resolved (B3)."""

import json

import jsonschema
import pytest

from src.schemas.bibliography_output import BIBLIOGRAPHY_OUTPUT_SCHEMA as SCHEMA
from src.utils.file_lock import write_json_with_lock


def _base():
    return {
        "BibliographyID": "01HX7YZABCDEFGHJKMNPQRSTVW",
        "title": "V Corps Operations in the ETO",
        "citation": {},
        "_schema_version": "2.29",
    }


# --- B1: availability='local' now persists through the real guard --------------------------
def test_b1_availability_local_writes(tmp_path):
    d = tmp_path / "bibliography"
    d.mkdir(parents=True)
    f = d / "bib_1.json"
    rec = _base()
    rec["availability"] = "local"
    rec["search_status"] = "resolved"
    assert write_json_with_lock(f, rec, entity="bibliography") is True
    assert f.exists()  # actually persisted (was silently blocked before)
    assert json.loads(f.read_text())["availability"] == "local"


@pytest.mark.parametrize("av", ["online", "offline", "archive", "local", "unknown"])
def test_b1_all_availability_values_validate(av):
    rec = _base()
    rec["availability"] = av
    jsonschema.Draft7Validator(SCHEMA).validate(rec)  # must not raise


# --- B2: source_metadata now persists ------------------------------------------------------
def test_b2_source_metadata_writes(tmp_path):
    d = tmp_path / "bibliography"
    d.mkdir(parents=True)
    f = d / "bib_2.json"
    rec = _base()
    rec["search_status"] = "resolved"
    rec["resource_urls"] = ["https://archive.org/details/x"]
    rec["source_metadata"] = {
        "findings": [{"url": "https://archive.org/details/x", "source": "archive_org"}],
        "biblio_category": "published_work",
    }
    assert write_json_with_lock(f, rec, entity="bibliography") is True
    assert (
        json.loads(f.read_text())["source_metadata"]["findings"][0]["source"]
        == "archive_org"
    )


# --- B3: a blocked write is NOT reported as resolved (raises -> errors channel) -------------
def test_b3_blocked_write_not_counted_resolved(tmp_path, monkeypatch):
    import src.enrichment.bibliography_resolver as B

    d = tmp_path / "bibliography"
    d.mkdir(parents=True)
    f = d / "bib_3.json"
    f.write_text(json.dumps({"BibliographyID": "x", "title": "T", "citation": {}}))

    # Force the guard to BLOCK (simulate a residual schema-invalid record) + a resolved status.
    monkeypatch.setattr(
        "src.utils.file_lock.write_json_with_lock", lambda *a, **k: False
    )
    monkeypatch.setattr(
        B,
        "_resolve_or_reuse",
        lambda data, *a, **k: (data.update({"search_status": "resolved"}) or None),
    )
    observed = {"called": False}
    monkeypatch.setattr(
        B, "_observe_resolved", lambda *a, **k: observed.update(called=True)
    )

    with pytest.raises(RuntimeError, match="schema guard refused"):
        B._process_bib_file(f, object(), {}, {})
    assert observed["called"] is False  # NOT reported as a resolution
