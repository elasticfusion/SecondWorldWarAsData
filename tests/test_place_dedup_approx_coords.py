"""Guard: LLM-guessed (approximate, ungeocoded) coordinates must not power the weak
"nearby + partial name" place-dedup rule.

XChannel ch8: Merderet River vs Ste. Mère-Eglise (42% name similarity, Phase-2 guessed coords
~2.4 km apart) was the run's only place candidate — a false positive.
"""

import json

import src.dedup.find_duplicate_places_v2 as fdp

GUESS = {"precision": "approximate", "confidence": 0.8}


def _p(name, lat, lon, verified):
    return {
        "name": name,
        "filename": f"{name}.json",
        "lat": lat,
        "lon": lon,
        "coords_verified": verified,
    }


def test_approximate_coords_do_not_trigger_weak_rule():
    a = _p("Merderet River", 49.40, -1.35, False)
    b = _p("Ste. Mère-Eglise", 49.41, -1.32, False)
    ok, _ = fdp._check_match(a, b)
    assert ok is False


def test_verified_coords_still_trigger_weak_rule():
    ok, reasons = fdp._evaluate_match(0.45, 1.0, True, verified=True)
    assert ok and any("nearby" in r for r in reasons)


def test_strong_name_match_still_reported_and_annotated():
    ok, reasons = fdp._evaluate_match(0.9, 1.0, True, verified=False)
    assert ok and "within 5km (approximate coords)" in reasons


def test_coords_verified_detection():
    xy = {"coordinates": {"latitude": 1, "longitude": 2}}
    assert fdp._coords_verified({**xy, "geocode_source": "nominatim"})
    assert fdp._coords_verified({**xy, "enrichment_status": "geocoded"})
    assert fdp._coords_verified(
        {"coordinates": {"latitude": 1, "longitude": 2, "precision": "exact"}}
    )
    assert not fdp._coords_verified(
        {"coordinates": {"latitude": 1, "longitude": 2, **GUESS}}
    )
    assert not fdp._coords_verified({})
    # a not-found geocode still records its source: no coordinates is nothing verified
    assert not fdp._coords_verified({"geocode_source": "nominatim"})


def test_run1_shapes_end_to_end(tmp_path):
    for n, lat, lon in (
        ("merderet river", 49.40, -1.35),
        ("ste mere-eglise", 49.41, -1.32),
    ):
        rec = {
            "name": n.title(),
            "PlaceID": n,
            "coordinates": {"latitude": lat, "longitude": lon, **GUESS},
        }
        (tmp_path / f"{n}.json").write_text(json.dumps(rec), encoding="utf-8")
    places = fdp._load_places(tmp_path)
    assert all(p["coords_verified"] is False for p in places)
    assert fdp._check_match(places[0], places[1])[0] is False


def test_mixed_verification_is_unverified():
    a = _p("Merderet River", 49.40, -1.35, True)
    b = _p("Ste. Mère-Eglise", 49.41, -1.32, False)
    assert fdp._check_match(a, b)[0] is False


def test_geocoded_twin_does_match(tmp_path):
    for n, lat, lon in (("merderet river", 49.40, -1.35), ("merderet", 49.401, -1.351)):
        rec = {
            "name": n.title(),
            "PlaceID": n,
            "geocode_source": "nominatim",
            "coordinates": {"latitude": lat, "longitude": lon, **GUESS},
        }
        (tmp_path / f"{n}.json").write_text(json.dumps(rec), encoding="utf-8")
    places = fdp._load_places(tmp_path)
    assert all(p["coords_verified"] for p in places)
    assert fdp._check_match(places[0], places[1])[0] is True


def test_suppressed_pairs_are_counted(monkeypatch, caplog, tmp_path):
    import logging

    # Same START ("merderet"), partial name match (~50%), close, but only LLM-guessed coords: the
    # weak rule must suppress AND count it. (The original XChannel pair, Merderet River vs
    # Ste. Mere-Eglise, now never gets this far: different starts are rejected by the start gate.)
    for n, lat, lon in (
        ("merderet river", 49.40, -1.35),
        ("merderet bridge causeway", 49.41, -1.32),
    ):
        rec = {
            "name": n.title(),
            "coordinates": {"latitude": lat, "longitude": lon, **GUESS},
        }
        (tmp_path / f"{n}.json").write_text(json.dumps(rec), encoding="utf-8")
    monkeypatch.setattr(fdp, "_load_exclusions", lambda d: (set(), set()))
    monkeypatch.setattr(
        "src.dedup.exclusions.load_reviewed_pairs", lambda *a, **k: set()
    )
    monkeypatch.setattr("src.dedup.incremental.get_last_dedup_run", lambda *a: None)
    caplog.set_level(logging.INFO)
    assert fdp.find_duplicate_places(tmp_path) == []
    assert any("weak_suppressed_unverified=1" in r.getMessage() for r in caplog.records)
