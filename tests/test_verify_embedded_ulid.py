"""HIGH#7b: inbound->outbound embedded-ULID integrity check (Grok must HONOR embedded ids)."""

from src.utils.deterministic_ids import verify_embedded_ulid

_KNOWN = "01HX7YZABCDEFGHJKMNPQRSTVW"


def test_exact_echo_is_honored():
    assert verify_embedded_ulid(_KNOWN, _KNOWN) == (_KNOWN, "exact")


def test_cosmetic_noise_restored():
    # lowercase + stray dashes/spaces = cosmetic; trusted id is the known one, outcome flags it.
    noisy = _KNOWN.lower()[:8] + "-" + _KNOWN.lower()[8:]
    tid, outcome = verify_embedded_ulid(_KNOWN, noisy)
    assert tid == _KNOWN and outcome == "restored_cosmetic"


def test_substantive_mismatch_restored():
    tid, outcome = verify_embedded_ulid(_KNOWN, "01DIFFERENT0000000000000000")
    assert tid == _KNOWN and outcome == "restored_mismatch"


def test_missing_echo_restored():
    for missing in (None, ""):
        tid, outcome = verify_embedded_ulid(_KNOWN, missing)
        assert tid == _KNOWN and outcome == "restored_mismatch"


def test_never_returns_a_non_known_id():
    # The trusted id is ALWAYS the known embedded id — never a guess/random value.
    for echoed in (_KNOWN, _KNOWN.lower(), "garbage", None, "01XXX"):
        tid, _ = verify_embedded_ulid(_KNOWN, echoed)
        assert tid == _KNOWN


def test_reconcile_echoed_keys_exact_and_cosmetic():
    from src.utils.deterministic_ids import reconcile_echoed_keys

    known = ["01HX7YZABCDEFGHJKMNPQRSTVW", "01AAAAAAAAAAAAAAAAAAAAAAAA"]
    keyed = {
        "01HX7YZABCDEFGHJKMNPQRSTVW": [{"x": 1}],  # exact
        "01aaaaaaaa-aaaaaaaaaaaaaaaa": [{"x": 2}],  # cosmetic noise on the 2nd
    }
    out = reconcile_echoed_keys(known, keyed, "test")
    assert out["01HX7YZABCDEFGHJKMNPQRSTVW"] == [{"x": 1}]
    assert out["01AAAAAAAAAAAAAAAAAAAAAAAA"] == [{"x": 2}]


def test_reconcile_echoed_keys_ambiguous_skipped():
    # Two echoed keys normalize to the SAME known id -> ambiguous -> not recovered (no guess).
    from src.utils.deterministic_ids import reconcile_echoed_keys

    known = ["01AAAAAAAAAAAAAAAAAAAAAAAA"]
    keyed = {
        "01aaaaaaaaaaaaaaaaaaaaaaaa": [{"x": 1}],
        "01-aaaaaaaaaaaaaaaaaaaaaaaa": [{"x": 2}],
    }
    out = reconcile_echoed_keys(known, keyed, "test")
    assert (
        "01AAAAAAAAAAAAAAAAAAAAAAAA" not in out
    )  # ambiguous -> dropped, never guessed


def test_reconcile_echoed_keys_failsafe_non_dict():
    from src.utils.deterministic_ids import reconcile_echoed_keys

    assert reconcile_echoed_keys(["x"], None, "test") is None  # returns input unchanged
