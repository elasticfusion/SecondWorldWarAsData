"""Tests for places OpenSERP enrichment with multi-language name variants."""

import json
from unittest.mock import patch

import jsonschema

import src.enrichment.openserp_enrichment as oe
from src.schemas.places_output import PLACES_OUTPUT_SCHEMA


def test_place_name_variants_includes_native_names():
    v = oe.place_name_variants(
        {
            "current_name": "Palatinate",
            "aliases": ["The Pfalz"],
            "historical_names": [{"name": "Pfalz", "language": "German"}],
        }
    )
    assert v == ["Palatinate", "The Pfalz", "Pfalz"]


def test_place_name_variants_dedupes_case_insensitive():
    v = oe.place_name_variants(
        {"current_name": "Aachen", "name": "aachen", "aliases": ["Aachen"]}
    )
    assert v == ["Aachen"]


def test_places_openserp_searches_each_variant(tmp_path):
    pd = tmp_path / "places"
    pd.mkdir()
    (pd / "p.json").write_text(
        json.dumps(
            {
                "PlaceID": "01HX7YZABCDEFGHJKMNPQRSTVW",
                "current_name": "Palatinate",
                "historical_names": [{"name": "Pfalz", "language": "German"}],
            }
        )
    )
    seen = []

    def fake(q, url, limit=10):
        seen.append(q)
        return [
            {"url": f"http://{abs(hash(q)) % 100}", "title": "x WWII", "snippet": ""}
        ]

    with (
        patch.object(oe, "_openserp_reachable", return_value=True),
        patch.object(oe, "_search_openserp", side_effect=fake),
        patch.object(oe, "_verify_result", return_value=True),
        patch.object(
            oe,
            "process_positive_url",
            return_value={
                "url": "http://s",
                "title": "t",
                "summary": "sum",
                "source": "openserp",
            },
        ),
    ):
        n = oe.enrich_places_with_openserp(pd, "http://x")

    assert n == 1
    assert any("Palatinate" in q for q in seen)  # English
    assert any("Pfalz" in q for q in seen)  # native German WWII name
    p = json.loads((pd / "p.json").read_text())
    assert p["images"] and p["web_results"]
    assert p["openserp_searched"] is True
    jsonschema.validate(p, PLACES_OUTPUT_SCHEMA)
