"""Title uniqueness: title = first indicator; single=checksum, multi=LLM."""

# pylint: disable=missing-function-docstring,import-error

import io
import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("CACHE_TABLE", "test-cache")
os.environ.setdefault("ENV_NAME", "test")

from src.ingestion.title_uniqueness import (
    IdentityDecision,
    content_checksum,
    decide_identity,
)


# --- single-file: checksum identity ---
def test_single_new_title():
    d = decide_identity(
        "T",
        ["k"],
        lookup=lambda t: None,
        single_checksum=lambda: "sha256:a",
        multi_same=lambda: False,
    )
    assert d.action == "new" and d.method == "checksum"


def test_single_repeat_same_checksum():
    d = decide_identity(
        "T",
        ["k"],
        lookup=lambda t: "sha256:a",
        single_checksum=lambda: "sha256:a",
        multi_same=lambda: False,
    )
    assert d.action == "repeat" and d.method == "checksum"


def test_single_collision_diff_checksum_renamed():
    d = decide_identity(
        "T",
        ["k"],
        lookup={"T": "sha256:a"}.get,
        single_checksum=lambda: "sha256:b",
        multi_same=lambda: False,
        rand=lambda: "zzzzzz",
    )
    assert d.action == "renamed" and d.title == "T-zzzzzz"


# --- multi-file: LLM identity ---
def test_multi_new_title_uses_llm_method():
    d = decide_identity(
        "T",
        ["k1", "k2"],
        lookup=lambda t: None,
        single_checksum=lambda: "",
        multi_same=lambda: False,
    )
    assert d.action == "new" and d.method == "llm"


def test_multi_repeat_when_llm_says_same():
    d = decide_identity(
        "T",
        ["k1", "k2"],
        lookup=lambda t: "llm:fp",
        single_checksum=lambda: "",
        multi_same=lambda: True,
    )
    assert d.action == "repeat" and d.method == "llm"


def test_multi_collision_when_llm_says_different():
    d = decide_identity(
        "T",
        ["k1", "k2"],
        lookup={"T": "llm:fp"}.get,
        single_checksum=lambda: "",
        multi_same=lambda: False,
        rand=lambda: "q7q7q7",
    )
    assert d.action == "renamed" and d.title == "T-q7q7q7"


def test_checksum_helper():
    assert content_checksum(b"a") == content_checksum(b"a")
    assert content_checksum(b"a") != content_checksum(b"b")


# --- launch-time gate: concurrent partial delivery no longer false-collides ---
def _s3_bodies(mapping):
    s3 = MagicMock()
    s3.get_object.side_effect = lambda Bucket, Key, **kw: {
        "Body": io.BytesIO(mapping.get(Key, b""))
    }
    return s3


# --- launch-time gate on the CLAIM model (subset-independent) ---
def test_gate_fresh_claim_proceeds_and_finalizes():
    from lambda_handlers import trigger_handler as th

    keys = ["contentrepository/New/a.md", "contentrepository/New/b.md"]
    with (
        patch.object(th, "boto3") as b3,
        patch.object(th, "_title_claim", return_value=("claimed", None)),
        patch.object(th, "_multi_fingerprint", return_value="fp"),
        patch.object(th, "_title_finalize") as fin,
    ):
        b3.client.return_value = MagicMock()
        out = th._enforce_title_uniqueness_at_launch(keys)
    assert set(out) == set(keys)  # all of the title proceed
    fin.assert_called_once()  # marker finalized once


def test_gate_inflight_sibling_proceeds_no_false_collision():
    """THE regression: a sibling invocation of the SAME upload wave (claim already
    held) must PROCEED, never be rejected as a collision."""
    from lambda_handlers import trigger_handler as th

    keys = ["contentrepository/Wave/a.md"]
    with (
        patch.object(th, "boto3") as b3,
        patch.object(th, "_title_claim", return_value=("inflight", None)),
        patch("src.ingestion.review_reject.reject_to_review") as reject,
    ):
        b3.client.return_value = MagicMock()
        out = th._enforce_title_uniqueness_at_launch(keys)
    assert out == keys  # proceeds — same submission in pieces
    reject.assert_not_called()  # NOT a collision


def test_gate_final_prior_same_checksum_single_repeat():
    from lambda_handlers import trigger_handler as th

    key = "contentrepository/Solo/solo.md"
    with (
        patch.object(th, "boto3") as b3,
        patch.object(th, "_title_claim", return_value=("final", "sha256:SAME")),
        patch.object(th, "_title_checksum", return_value="sha256:SAME"),
        patch("src.ingestion.review_reject.reject_to_review") as reject,
    ):
        b3.client.return_value = MagicMock()
        out = th._enforce_title_uniqueness_at_launch([key])
    assert out == []
    assert reject.call_args.kwargs.get("category") == "duplicate"


def test_gate_final_prior_diff_checksum_single_collision():
    from lambda_handlers import trigger_handler as th

    key = "contentrepository/Solo2/solo.md"
    with (
        patch.object(th, "boto3") as b3,
        patch.object(th, "_title_claim", return_value=("final", "sha256:OLD")),
        patch.object(th, "_title_checksum", return_value="sha256:NEW"),
        patch("src.ingestion.review_reject.reject_to_review") as reject,
    ):
        b3.client.return_value = MagicMock()
        out = th._enforce_title_uniqueness_at_launch([key])
    assert out == []
    assert reject.call_args.kwargs.get("category") == "title-collision"


def test_gate_final_prior_multi_uses_llm():
    from lambda_handlers import trigger_handler as th

    keys = ["contentrepository/Clash/a.md", "contentrepository/Clash/b.md"]
    with (
        patch.object(th, "boto3") as b3,
        patch.object(th, "_title_claim", return_value=("final", "llm:oldfp")),
        patch.object(th, "_multi_fingerprint", return_value="newfp"),
        patch.object(th, "_llm_same_work", return_value=False) as llm,
        patch("src.ingestion.review_reject.reject_to_review") as reject,
    ):
        b3.client.return_value = MagicMock()
        out = th._enforce_title_uniqueness_at_launch(keys)
    assert out == []
    llm.assert_called()
    assert reject.call_args.kwargs.get("category") == "title-collision"


def test_llm_same_work_fails_safe_without_key():
    from lambda_handlers import trigger_handler as th

    with patch.object(th, "_grok_api_key", return_value=""):
        assert th._llm_same_work("T", "old", "new") is False


def test_gate_untitled_key_proceeds_ungated():
    from lambda_handlers import trigger_handler as th

    key = "contentrepository/..%2f/evil"  # _title_from_key -> "" (unsafe)
    with patch.object(th, "boto3") as b3:
        b3.client.return_value = MagicMock()
        out = th._enforce_title_uniqueness_at_launch([key])
    assert out == [key]


def test_title_claim_same_wave_final_is_inflight_not_collision():
    """FINALIZE-RACE regression: a sibling that observes state=='final' from the
    SAME wave (claimed_at within the window) must resolve to 'inflight' (same
    submission), NOT 'final' (which would compare partial fingerprints -> false
    collision — the bug that broke 4 prior designs)."""
    import time
    from unittest.mock import MagicMock, patch
    from lambda_handlers import trigger_handler as th

    class _CCFE(Exception):
        pass

    fake = MagicMock()
    fake.meta.client.exceptions.ConditionalCheckFailedException = _CCFE
    fake.put_item.side_effect = _CCFE()  # claim lost (winner already finalized)
    fake.get_item.return_value = {
        "Item": {
            "state": "final",
            "checksum": "llm:winnerfp",
            "claimed_at": int(time.time()),
        }
    }
    with patch.object(th, "dynamo", fake):
        state, marker = th._title_claim("SameWaveTitle")
    assert state == "inflight"  # same wave -> proceed, no identity compare


def test_title_claim_old_final_is_real_collision_candidate():
    """A GENUINELY PRIOR final entry (claimed_at older than the wave) IS a real
    identity comparison -> returns 'final' with the marker."""
    import time
    from unittest.mock import MagicMock, patch
    from lambda_handlers import trigger_handler as th

    class _CCFE(Exception):
        pass

    fake = MagicMock()
    fake.meta.client.exceptions.ConditionalCheckFailedException = _CCFE
    fake.put_item.side_effect = _CCFE()
    fake.get_item.return_value = {
        "Item": {
            "state": "final",
            "checksum": "sha256:prior",
            "claimed_at": int(time.time()) - th._TITLE_CLAIM_WAVE_S - 100,
        }
    }
    with patch.object(th, "dynamo", fake):
        state, marker = th._title_claim("OldTitle")
    assert state == "final" and marker == "sha256:prior"
