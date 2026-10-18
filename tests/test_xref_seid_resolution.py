"""Cross-reference integrity: batch entity mentions must anchor to REAL event Sub-eventIDs.

The LLM echoes the sub_event_id we embed in the prompt, but does not reliably reproduce a 26-char
ULID. The resolver uses EXACT match -> UNIQUE cosmetic-noise recovery -> DROP. It must NEVER guess
by response position (the LLM omits entity-less sub-events, so a positional guess would silently
mis-attribute an entity to the wrong-but-real sub-event — undetectable corruption).
"""

import json
from src.extraction import batch_parallel as bp

_R1 = "REAL0000000000000000000001"
_R2 = "REAL0000000000000000000002"


def _run(tmp_path, response):
    entity_dir = tmp_path / "places"
    entity_dir.mkdir()
    se_by_id = {_R1: {"Sub-event_summary": "s1"}, _R2: {"Sub-event_summary": "s2"}}
    res = bp._process_batch_response(
        response=response,
        response_key="places",
        inner_key="places",
        make_key=lambda o: (o.get("name") or "").lower(),
        make_record=lambda o: {
            "PlaceID": "01PLACE0000000000000000AA",
            "current_name": o.get("name"),
        },
        entity_dir=entity_dir,
        index={},
        id_field="PlaceID",
        se_by_id=se_by_id,
        event_id="EVT0000000000000000000001",
        event_name="ch1",
        meta={"book": "B"},
        sub_event_key="places",
    )
    recs = {}
    for f in entity_dir.glob("*.json"):
        d = json.loads(f.read_text())
        recs[d.get("current_name")] = [
            m["Sub_eventID"] for m in d.get("event_mentions", [])
        ]
    return res, recs


def test_exact_match_anchors_to_real(tmp_path):
    _, recs = _run(
        tmp_path, {"places": [{"sub_event_id": _R1, "places": [{"name": "Schmidt"}]}]}
    )
    assert recs["Schmidt"] == [_R1]


def test_cosmetic_mangle_recovers_to_unique_real(tmp_path):
    # Case/dash noise the LLM might introduce -> normalizes to exactly one real id -> recovered.
    mangled = "real0000-0000-0000-0000-000002"
    _, recs = _run(
        tmp_path,
        {"places": [{"sub_event_id": mangled, "places": [{"name": "Aachen"}]}]},
    )
    assert recs["Aachen"] == [_R2]


def test_truly_bogus_id_is_dropped_not_misattributed(tmp_path):
    # A hallucinated id matching NO real sub-event must be DROPPED — never mapped to a real one.
    res, recs = _run(
        tmp_path,
        {
            "places": [
                {
                    "sub_event_id": "7YK3SCMNYNNMBWG43EPKZC6E1H",
                    "places": [{"name": "Ghost"}],
                }
            ]
        },
    )
    assert recs == {}  # nothing written
    assert res["count"] == 0


def test_omission_shift_does_not_misattribute(tmp_path):
    # The CORE corruption scenario: LLM returns one item (for what was really sub-event #2) with a
    # bogus id, at response position 0. A positional remap would wrongly anchor it to _R1. It must
    # instead be DROPPED (bogus id, no unique recovery) — NOT written to _R1.
    _, recs = _run(
        tmp_path,
        {
            "places": [
                {
                    "sub_event_id": "BOGUSmangledfromLLM00000099",
                    "places": [{"name": "Vossenack"}],
                }
            ]
        },
    )
    assert "Vossenack" not in recs  # not mis-attributed to _R1


def test_mixed_exact_and_bogus_only_exact_written(tmp_path):
    # item[0] exact-matches _R1; item[1] bogus. The bogus one must NOT be positionally mapped to _R2.
    _, recs = _run(
        tmp_path,
        {
            "places": [
                {"sub_event_id": _R1, "places": [{"name": "Real"}]},
                {"sub_event_id": "BOGUS", "places": [{"name": "Bogus"}]},
            ]
        },
    )
    assert recs.get("Real") == [_R1]
    assert "Bogus" not in recs


def test_normalize_ulid_cosmetic_only():
    assert bp._normalize_ulid("ReAl-0000_0000 0001") == "real000000000001"
    # distinct ids stay distinct (no fuzzy collapse)
    assert bp._normalize_ulid(_R1) != bp._normalize_ulid(_R2)


def test_fix_invalid_ulids_skip_keys_preserves_reference():
    """HIGH#1: _fix_invalid_ulids must NOT randomize a skipped reference id (sub_event_id) — doing so
    destroys the cross-reference before the resolver can recover it. Other invalid ids still fixed.
    """
    from src.utils.json_validator import _fix_invalid_ulids

    data = {
        "places": [
            {
                "sub_event_id": "BOGUSmangled",
                "places": [{"name": "X", "PlaceID": "bad-id"}],
            }
        ]
    }
    out = _fix_invalid_ulids(data, skip_keys={"sub_event_id"})
    it = out["places"][0]
    assert it["sub_event_id"] == "BOGUSmangled"  # preserved
    assert it["places"][0]["PlaceID"] != "bad-id"  # other invalid id still fixed
    # default (no skip) still randomizes sub_event_id -> unchanged for other callers
    out2 = _fix_invalid_ulids(data)
    assert out2["places"][0]["sub_event_id"] != "BOGUSmangled"


def test_cosmetic_mangle_recovers_end_to_end(tmp_path):
    """HIGH#1 end-to-end: a sub_event_id echoed with cosmetic lowercase noise survives the fix pass
    (skip_keys) AND recovers to the real Sub-eventID in _process_batch_response (recovery path live).
    """
    from src.utils.json_validator import _fix_invalid_ulids

    real = "REAL0000000000000000000001"
    resp = {"places": [{"sub_event_id": real.lower(), "places": [{"name": "Schmidt"}]}]}
    resp = _fix_invalid_ulids(resp, skip_keys={"sub_event_id"})  # preserve
    assert resp["places"][0]["sub_event_id"] == real.lower()
    _, recs = _run(tmp_path, resp)  # resolver recovers via _normalize_ulid unique match
    assert recs.get("Schmidt") == [real]
