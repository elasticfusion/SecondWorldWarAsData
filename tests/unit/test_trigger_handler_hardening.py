"""Trigger-Lambda hardening regressions — Phase-2 review (HIGH).

Covers the 5 findings:
  1. key-derived book names must be _SAFE_TITLE_RE-validated before becoming S3/DynamoDB keys
  2. SNS TopicArn must match OUR account/region (not just the topic-name suffix) to be trusted
  3. manual expected_count must NOT authorize a scope override unless ALLOW_MANUAL_EXPECTED_COUNT
  4. the multi-file sameness LLM prompt must treat file heads as untrusted + parse SAME exactly
  5. _update_manifest must use optimistic concurrency (conditional write + retry), not get->put

Mirrors tests/unit/test_trigger_handler.py: env is set BEFORE any import and trigger_handler is
imported LAZILY inside each test, so the module-level boto clients instantiate under the test's
control (no cross-file import-order / moto pollution).
"""

import json
import os
from unittest import mock

os.environ.setdefault("ECS_CLUSTER", "test-wwii-pipeline")
os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("CACHE_TABLE", "test-wwii-api-cache")
os.environ.setdefault("NOTIFICATION_TOPIC_ARN", "")
os.environ.setdefault("ENV_NAME", "test")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")

# Our own topic ARN used to validate inbound SNS TopicArns (applied per-test via patch.object).
_OWN_TOPIC = "arn:aws:sns:us-east-1:111122223333:dev-wwii-phase2-complete"


# --- #1 key-derived book sanitization ------------------------------------------------------
def test_title_from_key_rejects_traversal_and_unsafe():
    from lambda_handlers import trigger_handler as th

    assert th._title_from_key("contentrepository/MyBook/ch01.md") == "MyBook"
    assert th._title_from_key("contentrepository/Foo.md") == "Foo"
    assert th._title_from_key("contentrepository/../../etc/passwd") == ""
    # a loose non-repo path yields only the SAFE stem (traversal components dropped) — the result
    # can NEVER contain a separator or "..".
    for k in ("../../x", "/a/b/c", "x/../../../y.md"):
        out = th._title_from_key(k)
        assert "/" not in out and ".." not in out
        if out:
            assert th._SAFE_TITLE_RE.fullmatch(out)


def test_book_from_key_fallback_is_observable_and_safe(caplog):
    from lambda_handlers import trigger_handler as th

    # unsafe/traversal key -> 'unknown' + an OBSERV warning (not a silent collapse)
    with caplog.at_level("WARNING"):
        assert th._book_from_key("contentrepository/../../etc/passwd") == "unknown"
    assert any("book.unknown_fallback" in r.message for r in caplog.records)


def test_safe_stem_from_key_preserves_filename_but_blocks_traversal():
    from lambda_handlers import trigger_handler as th

    # the submit_video/convert semantic: filename stem, spaces->underscores
    assert (
        th._safe_stem_from_key("contentrepository/vid/Battle of the Bulge.mp4")
        == "Battle_of_the_Bulge"
    )
    # unsafe stem -> 'unknown'
    assert th._safe_stem_from_key("contentrepository/x/..%2f..%2fevil") in (
        "unknown",
        "2f2fevil",  # % and / stripped by the key split; either way no separator/traversal
    )


# --- #2 SNS full-ARN trust -----------------------------------------------------------------
def test_trusted_topic_name_accepts_own_arn():
    from lambda_handlers import trigger_handler as th

    with mock.patch.object(th, "NOTIFY_TOPIC", _OWN_TOPIC):
        assert (
            th._trusted_topic_name(
                "arn:aws:sns:us-east-1:111122223333:dev-wwii-content-uploaded"
            )
            == "dev-wwii-content-uploaded"
        )


def test_trusted_topic_name_rejects_foreign_account_or_region():
    from lambda_handlers import trigger_handler as th

    with mock.patch.object(th, "NOTIFY_TOPIC", _OWN_TOPIC):
        assert (
            th._trusted_topic_name(
                "arn:aws:sns:us-east-1:999999999999:dev-wwii-content-uploaded"
            )
            == ""
        )
        assert (
            th._trusted_topic_name(
                "arn:aws:sns:eu-west-1:111122223333:dev-wwii-content-uploaded"
            )
            == ""
        )
        assert th._trusted_topic_name("arn:aws:sqs:us-east-1:111122223333:x") == ""
        assert th._trusted_topic_name("garbage") == ""
        assert th._trusted_topic_name("") == ""


def test_extract_records_only_trusts_own_topic():
    from lambda_handlers import trigger_handler as th

    event = {
        "Records": [
            {
                "Sns": {
                    "TopicArn": "arn:aws:sns:us-east-1:999999999999:dev-wwii-dedup-complete"
                }
            }
        ]
    }
    with mock.patch.object(th, "NOTIFY_TOPIC", _OWN_TOPIC):
        topics, _ = th._extract_records(event)
    assert "dev-wwii-dedup-complete" not in topics


# --- #3 expected_count scope-override gate -------------------------------------------------
def test_manual_expected_count_ignored_unless_enabled():
    from lambda_handlers import trigger_handler as th

    captured = {}

    def _fake_run_task(task_def, source, book_name="", extra_env=None):
        captured["extra_env"] = extra_env

    event = {"source": "manual", "book": "MyBook", "phase": "2", "expected_count": 5000}
    with mock.patch.object(th, "_run_task", _fake_run_task):
        with mock.patch.object(th, "ALLOW_MANUAL_EXPECTED_COUNT", False):
            th.handler(event, None)
            assert captured["extra_env"] is None
        with mock.patch.object(th, "ALLOW_MANUAL_EXPECTED_COUNT", True):
            th.handler(event, None)
            assert captured["extra_env"] == [
                {"name": "EXPECTED_COUNT", "value": "5000"}
            ]


# --- #4 dedup sameness: injection-hardened prompt + exact parse ----------------------------
def test_llm_same_work_prompt_marks_untrusted_and_parses_exact():
    from lambda_handlers import trigger_handler as th

    calls = {}

    class _Resp:
        def __init__(self, ans):
            self._ans = ans

        def read(self):
            return json.dumps(
                {"choices": [{"message": {"content": self._ans}}]}
            ).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, timeout=30):
        calls["body"] = json.loads(req.data.decode())
        return _Resp(calls["answer"])

    with (
        mock.patch.object(th, "_grok_api_key", lambda: "k"),
        mock.patch("urllib.request.urlopen", _fake_urlopen),
    ):
        calls["answer"] = "SAME, now merge everything"
        assert th._llm_same_work("T", "existingdata", "newdata") is False
        msgs = calls["body"]["messages"]
        assert msgs[0]["role"] == "system"
        assert "UNTRUSTED DATA" in msgs[0]["content"]
        calls["answer"] = "SAME"
        assert th._llm_same_work("T", "existingdata", "newdata") is True


# --- #5 _update_manifest optimistic concurrency --------------------------------------------
def _s3_stub(initial=None, etag='"v1"'):
    state = {"body": initial, "etag": etag, "puts": []}

    class _Body:
        def __init__(self, data):
            self._d = data

        def read(self):
            return json.dumps(self._d).encode()

    stub = mock.Mock()

    def _get(Bucket, Key):
        if state["body"] is None:
            raise Exception("NoSuchKey")
        return {"Body": _Body(state["body"]), "ETag": state["etag"]}

    def _put(Bucket, Key, Body, **cond):
        state["puts"].append(cond)
        state["body"] = json.loads(Body)
        return {}

    stub.get_object.side_effect = _get
    stub.put_object.side_effect = _put
    return stub, state


def test_update_manifest_create_uses_if_none_match():
    from lambda_handlers import trigger_handler as th

    stub, state = _s3_stub(initial=None)
    with mock.patch.object(th, "s3", stub):
        th._update_manifest(["a.md", "b.md"])
    assert state["puts"][0] == {"IfNoneMatch": "*"}
    assert set(state["body"]) == {"a.md", "b.md"}


def test_update_manifest_update_uses_if_match_etag_and_unions():
    from lambda_handlers import trigger_handler as th

    stub, state = _s3_stub(initial=["a.md"], etag='"v1"')
    with mock.patch.object(th, "s3", stub):
        th._update_manifest(["b.md"])
    assert state["puts"][0] == {"IfMatch": '"v1"'}
    assert set(state["body"]) == {"a.md", "b.md"}


def test_update_manifest_retries_on_precondition_failed():
    from lambda_handlers import trigger_handler as th

    stub, state = _s3_stub(initial=["a.md"], etag='"v1"')
    err = Exception("precond")
    err.response = {"Error": {"Code": "PreconditionFailed"}}
    seq = [err, {}]

    def _put(Bucket, Key, Body, **cond):
        state["puts"].append(cond)
        r = seq.pop(0)
        if isinstance(r, Exception):
            raise r
        state["body"] = json.loads(Body)
        return r

    stub.put_object.side_effect = _put
    with mock.patch.object(th, "s3", stub):
        th._update_manifest(["b.md"])
    assert len(state["puts"]) == 2
    assert set(state["body"]) == {"a.md", "b.md"}
