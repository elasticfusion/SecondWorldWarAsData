"""HIGH-1 regression (2nd Phase-3 review): a transient geocode outage sets enrichment_status=
'geocode_error', which was NOT in the places schema enum -> the write-guard silently BLOCKED it,
defeating the H2 transient-retry guarantee. This drives a transient place through the REAL
write_json_with_lock guard (the round-trip the H2 tests missed) and asserts it PERSISTS.
"""

import json
import jsonschema

from src.schemas.places_output import PLACES_OUTPUT_SCHEMA as SCHEMA
from src.enrichment.places_grok_geocode import (
    GeocodeResult,
    _apply_result,
    _STATUS_ERROR,
)
from src.utils.file_lock import write_json_with_lock


def test_high1_geocode_error_in_schema_enum():
    for st in (
        "geocoded",
        "geocode_not_found",
        "geocode_low_confidence",
        "geocode_error",
    ):
        rec = {
            "PlaceID": "01HX7YZABCDEFGHJKMNPQRSTVW",
            "name": "X",
            "enrichment_status": st,
        }
        assert not list(
            jsonschema.Draft7Validator(SCHEMA).iter_errors(rec)
        ), f"{st} should be valid"


def test_high1_transient_place_persists_through_guard(tmp_path):
    """The round-trip the H2 tests missed: a transient-status place must actually WRITE."""
    d = tmp_path / "places"
    d.mkdir(parents=True)
    f = d / "p.json"
    place = {"PlaceID": "01HX7YZABCDEFGHJKMNPQRSTVW", "name": "Aachen"}
    status = _apply_result(
        place,
        GeocodeResult(found=False, confidence=0.0, transient=True, source="nominatim"),
    )
    assert status == _STATUS_ERROR  # transient -> retriable
    assert (
        write_json_with_lock(f, place, entity="places") is True
    )  # PERSISTS (was blocked before)
    assert json.loads(f.read_text())["enrichment_status"] == "geocode_error"


def test_med1_blocked_bibliography_write_not_counted(tmp_path, monkeypatch):
    """MED-1: _enrich_bib_file must return False when the guarded write is blocked (so the
    OBSERV bibliography.summary enriched count is not inflated)."""
    import src.extraction.supplemental_advanced as S

    bib = tmp_path / "b.json"
    bib.write_text(json.dumps({"BibliographyID": "x", "citation": {"title": "T"}}))
    monkeypatch.setattr(
        S, "_enrich_alt_title", lambda *a, **k: True
    )  # force changed=True
    monkeypatch.setattr(S, "_enrich_isbn", lambda *a, **k: False)
    monkeypatch.setattr(S, "_enrich_copyright", lambda *a, **k: False)
    monkeypatch.setattr(S, "_enrich_archive_url", lambda *a, **k: False)
    monkeypatch.setattr(S, "_save_enriched_data", lambda *a, **k: False)  # guard blocks
    assert S._enrich_bib_file(bib, {}, object()) is False  # not counted as enriched
