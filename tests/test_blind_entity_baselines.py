"""Step 3: no blind entities. casualties/logistics/equipment get deterministic baselines so
undercounts self-report; casualties/logistics are OBSERVABILITY-ONLY (warn, never fail_stop).
"""

from src.utils.yield_baseline import deterministic_count, classify_run, _SCORED_ENTITIES

_TXT = (
    "LST and LCT landing craft, DUKW, howitzers, tanks and machine guns; "
    "about 700 men lost, two battalions killed or wounded; supplies ferried ashore, "
    "the convoy landed reinforcements, a shortage of ammunition"
)


def test_previously_blind_entities_now_counted():
    assert deterministic_count("equipment", _TXT) >= 5  # was 0 (weak alias dict)
    assert deterministic_count("casualties", _TXT) >= 2  # was 0 (no counter)
    assert deterministic_count("logistics", _TXT) >= 2  # was 0 (no counter)


def test_blind_entities_are_scored():
    for e in ("casualties", "logistics", "equipment"):
        assert e in _SCORED_ENTITIES


def test_casualties_logistics_short_is_warn_not_failstop():
    # the overnight scenario — must NOT halt the run
    v, off = classify_run(
        {
            "groups": (74, 46),
            "people": (82, 65),
            "equipment": (36, 20),
            "places": (0, 46),
            "casualties": (28, 2),
            "logistics": (12, 1),
        }
    )
    assert v == "warn" and "casualties" in off and "logistics" in off


def test_fatal_entities_still_failstop():
    v, _ = classify_run({"people": (82, 0), "casualties": (28, 2)})
    assert v == "fail_stop"  # real starvation still fatal
    v2, _ = classify_run({"groups": (74, 10), "equipment": (36, 4), "people": (82, 70)})
    assert v2 == "fail_stop"  # 2 fatal-eligible short still fatal
