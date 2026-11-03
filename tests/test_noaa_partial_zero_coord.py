"""PR3 guard: NOAA weather enrichment must treat a MISSING or 0.0 coordinate on EITHER axis as an
un-geocoded placeholder and skip it. On origin/main the gate was `lat == 0.0 and lon == 0.0`, so a
missing `latitude` (defaulting to 0.0) paired with a real `longitude` slipped through and NOAA was
queried at a bogus (0.0, lon) equator point. ETO places are never at the equator/prime meridian.
"""

import json

import src.enrichment.noaa_weather as noaa


def _wf(tmp_path, loc):
    f = tmp_path / "w.json"
    f.write_text(json.dumps({"location": loc, "date": "1944-06-06"}), encoding="utf-8")
    return f


def test_missing_latitude_with_real_longitude_skipped(tmp_path):
    # latitude key absent -> defaults 0.0; longitude is a real ETO value. Must skip (None).
    f = _wf(tmp_path, {"longitude": 6.08})
    assert noaa._load_enrichable_weather(f) is None


def test_zero_latitude_real_longitude_skipped(tmp_path):
    f = _wf(tmp_path, {"latitude": 0.0, "longitude": 6.08})
    assert noaa._load_enrichable_weather(f) is None


def test_both_zero_skipped(tmp_path):
    f = _wf(tmp_path, {"latitude": 0.0, "longitude": 0.0})
    assert noaa._load_enrichable_weather(f) is None


def test_real_coords_loaded(tmp_path):
    f = _wf(tmp_path, {"latitude": 50.78, "longitude": 6.08})
    loaded = noaa._load_enrichable_weather(f)
    assert loaded is not None
    _data, lat, lon, date = loaded
    assert (lat, lon, date) == (50.78, 6.08, "1944-06-06")
