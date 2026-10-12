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


def test_launch_gate_multifile_single_title_all_proceed():
    from lambda_handlers import trigger_handler as th

    keys = [
        "contentrepository/Book/chapter2/chapter2a-content.md",
        "contentrepository/Book/chapter3/chapter3a-content.md",
    ]
    with (
        patch.object(th, "boto3") as b3,
        patch.object(th, "_title_registry_get", return_value=None),
        patch.object(th, "_title_registry_put", return_value=True) as putreg,
    ):
        b3.client.return_value = _s3_bodies({k: k.encode() for k in keys})
        out = th._enforce_title_uniqueness_at_launch(keys)
    assert set(out) == set(keys)  # one title, all proceed
    putreg.assert_called_once()  # registered once (marker starts 'llm:')
    assert putreg.call_args.args[1].startswith("llm:")


def test_launch_gate_single_repeat_rejected():
    from lambda_handlers import trigger_handler as th

    key = "contentrepository/Solo/solo.md"
    with (
        patch.object(th, "boto3") as b3,
        patch.object(th, "_title_checksum", return_value="sha256:SAME"),
        patch.object(th, "_title_registry_get", return_value="sha256:SAME"),
        patch("src.ingestion.review_reject.reject_to_review") as reject,
    ):
        b3.client.return_value = MagicMock()
        out = th._enforce_title_uniqueness_at_launch([key])
    assert out == []
    assert reject.call_args.kwargs.get("category") == "duplicate"


def test_launch_gate_multi_collision_uses_llm_and_rejects():
    from lambda_handlers import trigger_handler as th

    keys = ["contentrepository/Clash/a.md", "contentrepository/Clash/b.md"]
    with (
        patch.object(th, "boto3") as b3,
        patch.object(th, "_multi_fingerprint", return_value="fp-new"),
        patch.object(th, "_title_registry_get", return_value="llm:fp-old"),
        patch.object(th, "_llm_same_work", return_value=False) as llm,
        patch("src.ingestion.review_reject.reject_to_review") as reject,
    ):
        b3.client.return_value = MagicMock()
        out = th._enforce_title_uniqueness_at_launch(keys)
    assert out == []
    llm.assert_called()  # multi-file -> LLM path exercised
    assert reject.call_args.kwargs.get("category") == "title-collision"


def test_llm_same_work_fails_safe_without_key():
    from lambda_handlers import trigger_handler as th

    with patch.object(th, "_grok_api_key", return_value=""):
        assert th._llm_same_work("T", "old", "new") is False


def test_launch_gate_lost_claim_redecides_to_repeat():
    """First-writer-wins loser: put->False, re-decide against the winner's marker."""
    from lambda_handlers import trigger_handler as th

    key = "contentrepository/Race/solo.md"
    gets = iter([None, "sha256:SAME"])  # new on first look, winner's marker on retry
    with (
        patch.object(th, "boto3") as b3,
        patch.object(th, "_title_checksum", return_value="sha256:SAME"),
        patch.object(th, "_title_registry_get", side_effect=lambda t: next(gets)),
        patch.object(th, "_title_registry_put", return_value=False),
        patch("src.ingestion.review_reject.reject_to_review") as reject,
    ):
        b3.client.return_value = MagicMock()
        out = th._enforce_title_uniqueness_at_launch([key])
    assert out == []  # loser rejected as duplicate
    assert reject.call_args.kwargs.get("category") == "duplicate"


def test_launch_gate_single_collision_rejected():
    from lambda_handlers import trigger_handler as th

    key = "contentrepository/Solo2/solo.md"
    with (
        patch.object(th, "boto3") as b3,
        patch.object(th, "_title_checksum", return_value="sha256:NEW"),
        patch.object(th, "_title_registry_get", return_value="sha256:OLD"),
        patch("src.ingestion.review_reject.reject_to_review") as reject,
    ):
        b3.client.return_value = MagicMock()
        out = th._enforce_title_uniqueness_at_launch([key])
    assert out == []
    assert reject.call_args.kwargs.get("category") == "title-collision"


def test_launch_gate_untitled_key_proceeds_ungated():
    from lambda_handlers import trigger_handler as th

    key = "contentrepository/..%2f/evil"  # _title_from_key -> "" (unsafe)
    with patch.object(th, "boto3") as b3:
        b3.client.return_value = MagicMock()
        out = th._enforce_title_uniqueness_at_launch([key])
    assert out == [key]  # untitled -> ungated passthrough
