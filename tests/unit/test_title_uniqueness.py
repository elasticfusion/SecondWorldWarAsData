"""Title uniqueness decisions."""

# pylint: disable=missing-function-docstring,import-error

import os

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("CACHE_TABLE", "test-cache")
os.environ.setdefault("ENV_NAME", "test")

from src.ingestion.title_uniqueness import (
    TitleDecision,
    content_checksum,
    decide_title,
)


def test_new_title_registers():
    d = decide_title("SiegfriedLine", "sha256:aaa", lookup=lambda t: None)
    assert d.action == "new" and d.title == "SiegfriedLine"


def test_exact_repeat_is_rejected():
    # Same title + identical content -> repeat (caller should reject/no-op).
    d = decide_title("SiegfriedLine", "sha256:aaa", lookup=lambda t: "sha256:aaa")
    assert d.action == "repeat" and d.title == "SiegfriedLine"


def test_collision_different_content_is_renamed():
    reg = {"SiegfriedLine": "sha256:aaa"}
    d = decide_title(
        "SiegfriedLine",
        "sha256:bbb",  # different content, same title
        lookup=reg.get,
        _rand=lambda: "x9y8z7",
    )
    assert d.action == "renamed"
    assert d.title == "SiegfriedLine-x9y8z7"
    assert d.title not in reg  # unique


def test_rename_retries_when_suffix_also_taken():
    reg = {"T": "sha256:aaa", "T-aaaaaa": "sha256:ccc"}
    seq = iter(["aaaaaa", "bbbbbb"])  # first suffix taken, second free
    d = decide_title("T", "sha256:bbb", lookup=reg.get, _rand=lambda: next(seq))
    assert d.action == "renamed" and d.title == "T-bbbbbb"


def test_content_checksum_stable_and_distinguishing():
    assert content_checksum(b"hello") == content_checksum(b"hello")
    assert content_checksum(b"hello") != content_checksum(b"world")
    assert content_checksum(b"x").startswith("sha256:")


# --- trigger wiring: _enforce_title_uniqueness ---
import io  # noqa: E402
from unittest.mock import MagicMock, patch  # noqa: E402


def _fake_s3_with_body(body: bytes):
    s3 = MagicMock()
    s3.get_object.return_value = {"Body": io.BytesIO(body)}
    return s3


def test_enforce_uniqueness_new_title_proceeds_and_registers():
    from lambda_handlers import trigger_handler as th

    key = "contentrepository/NewBook/chapter1a-content.md"
    with (
        patch.object(th, "boto3") as b3,
        patch.object(th, "_title_registry_get", return_value=None) as getreg,
        patch.object(th, "_title_registry_put", return_value=True) as putreg,
    ):
        b3.client.return_value = _fake_s3_with_body(b"fresh content")
        out = th._enforce_title_uniqueness([key])
    assert out == [key]
    getreg.assert_called_with("NewBook")
    putreg.assert_called_once()


def test_enforce_uniqueness_multifile_same_title_all_proceed():
    """CRITICAL regression: all files of ONE title share ONE identity; a normal
    multi-chapter book must NOT self-collide."""
    from lambda_handlers import trigger_handler as th

    keys = [
        "contentrepository/Siegfried/chapter2/chapter2a-content.md",
        "contentrepository/Siegfried/chapter3/chapter3a-content.md",
        "contentrepository/Siegfried/chapter14/chapter14a-content.md",
    ]

    def _s3():
        m = MagicMock()
        m.get_object.side_effect = lambda Bucket, Key: {
            "Body": io.BytesIO(Key.encode())
        }
        return m

    with (
        patch.object(th, "boto3") as b3,
        patch.object(th, "_title_registry_get", return_value=None),
        patch.object(th, "_title_registry_put", return_value=True) as putreg,
    ):
        b3.client.return_value = _s3()
        out = th._enforce_title_uniqueness(keys)
    assert set(out) == set(keys)
    putreg.assert_called_once()


def test_enforce_uniqueness_exact_repeat_rejected():
    from lambda_handlers import trigger_handler as th

    key = "contentrepository/Dup/chapter1a-content.md"
    with (
        patch.object(th, "boto3") as b3,
        patch.object(th, "_title_checksum", return_value="sha256:SAME"),
        patch.object(th, "_title_registry_get", return_value="sha256:SAME"),
        patch.object(th, "_title_registry_put") as putreg,
        patch("src.ingestion.review_reject.reject_to_review") as reject,
    ):
        b3.client.return_value = MagicMock()
        out = th._enforce_title_uniqueness([key])
    assert out == []
    putreg.assert_not_called()
    assert reject.call_args.kwargs.get("category") == "duplicate"


def test_enforce_uniqueness_collision_different_content_rejected():
    from lambda_handlers import trigger_handler as th

    key = "contentrepository/Clash/chapter1a-content.md"
    with (
        patch.object(th, "boto3") as b3,
        patch.object(th, "_title_checksum", return_value="sha256:NEW"),
        patch.object(th, "_title_registry_get", return_value="sha256:OLDDIFFERENT"),
        patch("src.ingestion.review_reject.reject_to_review") as reject,
    ):
        b3.client.return_value = MagicMock()
        out = th._enforce_title_uniqueness([key])
    assert out == []
    assert reject.call_args.kwargs.get("category") == "title-collision"


def test_enforce_uniqueness_checksum_error_fails_open():
    from lambda_handlers import trigger_handler as th

    key = "contentrepository/Oops/chapter1a-content.md"
    with (
        patch.object(th, "boto3") as b3,
        patch.object(th, "_title_checksum", side_effect=RuntimeError("s3 down")),
    ):
        b3.client.return_value = MagicMock()
        out = th._enforce_title_uniqueness([key])
    assert out == [key]


def test_enforce_uniqueness_lost_claim_redecides_to_repeat():
    """Concurrent-claim loser: put->False, registry now shows SAME content -> repeat."""
    from lambda_handlers import trigger_handler as th

    key = "contentrepository/Race/chapter1a-content.md"
    with (
        patch.object(th, "boto3") as b3,
        patch.object(th, "_title_checksum", return_value="sha256:SAME"),
        patch.object(th, "_title_registry_get", side_effect=[None, "sha256:SAME"]),
        patch.object(th, "_title_registry_put", return_value=False),
        patch("src.ingestion.review_reject.reject_to_review") as reject,
    ):
        b3.client.return_value = MagicMock()
        out = th._enforce_title_uniqueness([key])
    assert out == []  # loser rejected as duplicate
    assert reject.call_args.kwargs.get("category") == "duplicate"


def test_enforce_uniqueness_transient_write_error_fails_open_once():
    """put->False but registry still None (transient write err) -> proceed once."""
    from lambda_handlers import trigger_handler as th

    key = "contentrepository/Transient/chapter1a-content.md"
    with (
        patch.object(th, "boto3") as b3,
        patch.object(th, "_title_checksum", return_value="sha256:NEW"),
        patch.object(th, "_title_registry_get", side_effect=[None, None]),
        patch.object(th, "_title_registry_put", return_value=False),
    ):
        b3.client.return_value = MagicMock()
        out = th._enforce_title_uniqueness([key])
    assert out == [key]  # fail-open exactly once
