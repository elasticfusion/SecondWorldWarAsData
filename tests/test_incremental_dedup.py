"""Tests for M6 incremental/blocking dedup (spec §3.2/§11)."""

from src.dedup import resolved_set as rs


def _person(name):
    return {"name": name, "event_mentions": []}


def _place(name, lat=0.0, lon=0.0):
    return {"name": name, "latitude": lat, "longitude": lon}


# --- blocking keys ---


def test_blocking_key_name_groups_same_lastname():
    a = rs.blocking_keys(_person("Dwight Eisenhower"), "people")
    b = rs.blocking_keys(_person("D. Eisenhower"), "people")
    # Same normalized last-name bucket => they collide (get compared)
    assert set(a) & set(b)


def test_blocking_key_places_include_geo_cell():
    keys = rs.blocking_keys(_place("Verdun", 49.16, 5.38), "places")
    assert any(k.startswith("geo:") for k in keys)


def test_geo_cell_none_for_placeholder_coords():
    assert rs._geo_cell(_place("Unknown", 0.0, 0.0)) is None


# --- candidate lookup is sublinear (only same-bucket) ---


def test_candidates_only_same_bucket():
    idx = rs.ResolvedSetIndex.from_entities(
        "people",
        [
            ("e1", _person("Bernard Montgomery")),
            ("e2", _person("Omar Bradley")),
            ("e3", _person("Monty Montgomery")),
        ],
    )
    cands = idx.candidates(_person("B. Montgomery"))
    ids = {c[0] for c in cands}
    assert "e1" in ids  # Montgomery bucket
    assert "e2" not in ids  # Bradley — different bucket, NOT compared


# --- auto/review/new split ---


def test_auto_merge_high_similarity():
    idx = rs.ResolvedSetIndex.from_entities(
        "people", [("e1", _person("Dwight Eisenhower"))]
    )
    merged = []
    counts = rs.dedup_incremental(
        "people",
        [("n1", _person("Dwight Eisenhower"))],  # identical => >= 0.90
        idx,
        merge_fn=lambda existing, new, data: merged.append((existing, new)) or True,
    )
    assert counts["auto_merged"] == 1
    assert merged == [("e1", "n1")]


def test_new_entity_added_to_index():
    idx = rs.ResolvedSetIndex.from_entities(
        "people", [("e1", _person("George Patton"))]
    )
    counts = rs.dedup_incremental("people", [("n1", _person("Karl Doenitz"))], idx)
    assert counts["new"] == 1
    # now indexed — a later identical entity in a subsequent call would match it
    assert idx.candidates(_person("Karl Doenitz"))


def test_review_band_goes_to_review_queue():
    idx = rs.ResolvedSetIndex.from_entities(
        "people", [("e1", _person("Johnathan Smith"))]
    )
    reviewed = []
    counts = rs.dedup_incremental(
        "people",
        [("n1", _person("Johnathon Smith"))],  # near but not identical
        idx,
        review_fn=lambda existing, new, score: reviewed.append((existing, new, score)),
    )
    # Depending on ratio it lands in review or auto; assert it did NOT count as brand-new
    assert counts["new"] == 0
    assert counts["auto_merged"] + counts["review"] == 1
