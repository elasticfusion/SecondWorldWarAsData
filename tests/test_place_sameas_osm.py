"""T2-C: OSM/Wikidata ids from the Nominatim geocoder fold into places.sameAs[]."""

from src.enrichment.nominatim_geocode import _osm_external_ids, _to_result


def test_osm_external_ids_full():
    raw = {
        "osm_type": "relation",
        "osm_id": 105804,
        "extratags": {"wikidata": "Q181943"},
    }
    assert _osm_external_ids(raw) == [
        "https://www.openstreetmap.org/relation/105804",
        "https://www.wikidata.org/wiki/Q181943",
    ]


def test_osm_external_ids_no_wikidata():
    assert _osm_external_ids({"osm_type": "node", "osm_id": 42}) == [
        "https://www.openstreetmap.org/node/42"
    ]


def test_osm_external_ids_failsafe():
    assert _osm_external_ids({}) == []
    assert _osm_external_ids({"extratags": {"wikidata": "not-a-qid"}}) == []


def test_to_result_carries_external_ids():
    raw = {
        "lat": "49.11",
        "lon": "-1.09",
        "importance": 0.7,
        "address": {"country": "France"},
        "osm_type": "relation",
        "osm_id": 105804,
        "extratags": {"wikidata": "Q181943"},
    }
    r = _to_result(raw)
    assert r.found and "https://www.openstreetmap.org/relation/105804" in r.external_ids


def test_apply_result_folds_external_ids_into_sameas():
    from src.enrichment.places_grok_geocode import GeocodeResult, _apply_result

    place = {"PlaceID": "x"}
    res = GeocodeResult(
        found=True,
        latitude=49.11,
        longitude=-1.09,
        country="France",
        confidence=0.8,
        source="osm",
        external_ids=["https://www.openstreetmap.org/relation/105804"],
    )
    _apply_result(place, res)
    assert "https://www.openstreetmap.org/relation/105804" in place.get("sameAs", [])
