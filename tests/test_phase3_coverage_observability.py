"""Q2 regression: Phase-3 bibliography + weather enrichers emit a machine-parseable coverage
summary (OBSERV *.summary) and a 0-of-N anomaly (OBSERV *.anomaly), mirroring enrich.summary.
"""

import json
import logging
from pathlib import Path


def _mk(dirp: Path, name: str, body: dict) -> Path:
    f = dirp / name
    f.write_text(json.dumps(body))
    return f


def test_bibliography_summary_and_anomaly(tmp_path, monkeypatch, caplog):
    import src.extraction.supplemental_advanced as S

    _mk(tmp_path, "bib_1.json", {"BibliographyID": "a", "title": "A"})
    _mk(tmp_path, "bib_2.json", {"BibliographyID": "b", "title": "B"})
    # nothing enriches -> 0-of-2 anomaly
    monkeypatch.setattr(S, "_enrich_bib_file", lambda *a, **k: False)
    with caplog.at_level(logging.INFO):
        n = S.enrich_bibliography(tmp_path, {}, object())
    assert n == 0
    msgs = "\n".join(r.getMessage() for r in caplog.records)
    assert "OBSERV bibliography.summary total=2 enriched=0 skipped=2" in msgs
    assert "OBSERV bibliography.anomaly 0-of-2" in msgs


def test_bibliography_summary_no_anomaly_when_enriched(tmp_path, monkeypatch, caplog):
    import src.extraction.supplemental_advanced as S

    _mk(tmp_path, "bib_1.json", {"BibliographyID": "a", "title": "A"})
    monkeypatch.setattr(S, "_enrich_bib_file", lambda *a, **k: True)
    with caplog.at_level(logging.INFO):
        S.enrich_bibliography(tmp_path, {}, object())
    msgs = "\n".join(r.getMessage() for r in caplog.records)
    assert "OBSERV bibliography.summary total=1 enriched=1 skipped=0" in msgs
    assert "bibliography.anomaly" not in msgs


def test_weather_summary_and_anomaly(tmp_path, monkeypatch, caplog):
    import src.enrichment.noaa_weather as N

    _mk(tmp_path, "w1.json", {"WeatherID": "a", "date": "1944-12-16"})
    monkeypatch.setattr(N, "_enrich_weather_file", lambda f, t: False)
    monkeypatch.setattr(N, "_request_count", 5, raising=False)
    with caplog.at_level(logging.INFO):
        N.enrich_weather_with_noaa(tmp_path, "token")
    msgs = "\n".join(r.getMessage() for r in caplog.records)
    assert "OBSERV weather.summary scanned=1 enriched=0 api_calls=5" in msgs
    assert "OBSERV weather.anomaly 0-of-1" in msgs


def test_weather_no_anomaly_when_no_api_calls(tmp_path, monkeypatch, caplog):
    """All files legitimately skipped (no API exercised) -> summary but NO anomaly."""
    import src.enrichment.noaa_weather as N

    _mk(tmp_path, "w1.json", {"WeatherID": "a"})
    monkeypatch.setattr(N, "_enrich_weather_file", lambda f, t: False)
    monkeypatch.setattr(N, "_request_count", 0, raising=False)
    with caplog.at_level(logging.INFO):
        N.enrich_weather_with_noaa(tmp_path, "token")
    msgs = "\n".join(r.getMessage() for r in caplog.records)
    assert "OBSERV weather.summary scanned=1 enriched=0 api_calls=0" in msgs
    assert "weather.anomaly" not in msgs
