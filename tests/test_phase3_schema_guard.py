"""Q3 regression: Phase-3 weather (NOAA) + bibliography enrichment now persist via the central
write-guard (write_json_with_lock), so a schema-invalid enriched record is BLOCKED + recorded —
not silently raw-written (the pre-fix bypass at noaa_weather.py:327 / supplemental_advanced.py:348).
"""

import json
from pathlib import Path
from unittest import mock


def test_noaa_enrich_routes_through_write_guard(tmp_path, monkeypatch):
    import src.enrichment.noaa_weather as N

    wf = tmp_path / "w.json"
    wf.write_text(
        json.dumps({"WeatherID": "01HX7YZABCDEFGHJKMNPQRSTVW", "date": "1944-12-16"})
    )
    monkeypatch.setattr(
        N, "_load_enrichable_weather", lambda f: ({"x": 1}, 50.0, 6.0, "1944-12-16")
    )
    monkeypatch.setattr(N, "find_nearest_station", lambda *a, **k: {"id": "ST1"})
    monkeypatch.setattr(N, "fetch_noaa_weather", lambda *a, **k: {"temp": -5})
    calls = {}

    def _guard(f, data, entity=None):
        calls["entity"] = entity
        return True

    monkeypatch.setattr("src.utils.file_lock.write_json_with_lock", _guard)
    assert N._enrich_weather_file(wf, "token") is True
    assert (
        calls["entity"] == "weather"
    )  # routed through the guard with the right entity


def test_noaa_enrich_blocked_write_returns_false(tmp_path, monkeypatch):
    import src.enrichment.noaa_weather as N

    monkeypatch.setattr(
        N, "_load_enrichable_weather", lambda f: ({"x": 1}, 50.0, 6.0, "1944-12-16")
    )
    monkeypatch.setattr(N, "find_nearest_station", lambda *a, **k: {"id": "ST1"})
    monkeypatch.setattr(N, "fetch_noaa_weather", lambda *a, **k: {"temp": -5})
    monkeypatch.setattr(
        "src.utils.file_lock.write_json_with_lock", lambda f, d, entity=None: False
    )
    assert (
        N._enrich_weather_file(tmp_path / "w.json", "token") is False
    )  # blocked -> not enriched


def test_bibliography_save_routes_through_guard(tmp_path, monkeypatch):
    import src.extraction.supplemental_advanced as S

    calls = {}

    def _guard(f, data, entity=None):
        calls["entity"] = entity
        return True

    monkeypatch.setattr("src.utils.file_lock.write_json_with_lock", _guard)
    ok = S._save_enriched_data(
        tmp_path / "bib.json",
        {"BibliographyID": "x", "title": "T"},
        1,
        entity="bibliography",
    )
    assert ok is True and calls["entity"] == "bibliography"


def test_supplemental_container_still_raw_write(tmp_path):
    """entity=None path stays a raw write (the supplemental container is not a schema entity)."""
    import src.extraction.supplemental_advanced as S

    f = tmp_path / "supp.json"
    assert S._save_enriched_data(f, {"Supplemental_Material": []}, 1) is True
    assert f.exists()  # written raw, no guard
