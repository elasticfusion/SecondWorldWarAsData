"""Tests for M2 per-document locks (§12.2 of CONCURRENCY_AND_NAT_SPEC).

Correctness discipline (§15): with the multi_doc kill-switch OFF, the lock key
MUST be byte-identical to the pre-M2 singleton per-phase key, so pool=1 behaves
exactly like today's serial pipeline. With it ON and a book set, the key becomes
per-document so different books can hold the same phase concurrently while the
same book+phase still serializes.
"""

import os
from unittest.mock import patch

# Required env for importing ecs_entrypoint at module load (matches
# tests/test_notification_preflight.py convention).
os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import ecs_entrypoint

# --- _lock_key: serial mode is unchanged (the migration invariant) ---


def test_lock_key_serial_mode_is_legacy_singleton():
    """multi_doc OFF → historical key, regardless of BOOK_NAME."""
    with patch.object(ecs_entrypoint, "_multi_doc_enabled", return_value=False):
        with patch.dict(os.environ, {"ENV_NAME": "dev", "BOOK_NAME": "SomeBook"}):
            assert (
                ecs_entrypoint._lock_key("phase2_extract.py")
                == "lock#dev-wwii-phase2-extract"
            )


def test_lock_key_multi_doc_with_book_is_per_document():
    """multi_doc ON + book set → per-document key."""
    with patch.object(ecs_entrypoint, "_multi_doc_enabled", return_value=True):
        with patch.dict(os.environ, {"ENV_NAME": "dev", "BOOK_NAME": "St. Vith"}):
            assert (
                ecs_entrypoint._lock_key("phase2_extract.py")
                == "lock#dev-wwii-phase2-extract#St. Vith"
            )


def test_lock_key_multi_doc_no_book_falls_back_to_singleton():
    """multi_doc ON but no book → singleton (can't per-doc-scope without an id)."""
    with patch.object(ecs_entrypoint, "_multi_doc_enabled", return_value=True):
        with patch.dict(os.environ, {"ENV_NAME": "dev", "BOOK_NAME": ""}):
            assert (
                ecs_entrypoint._lock_key("phase2_extract.py")
                == "lock#dev-wwii-phase2-extract"
            )


def test_lock_key_unknown_phase_is_empty():
    """Unknown phase script → empty key (acquire/remove no-op, as before)."""
    assert ecs_entrypoint._lock_key("not_a_phase.py") == ""


def test_lock_key_respects_env_name():
    with patch.object(ecs_entrypoint, "_multi_doc_enabled", return_value=False):
        with patch.dict(os.environ, {"ENV_NAME": "prod", "BOOK_NAME": ""}):
            assert (
                ecs_entrypoint._lock_key("phase1_parse.py")
                == "lock#prod-wwii-phase1-parse"
            )


# --- _other_phase_locked: same phase (another book) is NOT "another phase" ---


def test_other_phase_locked_ignores_same_phase_different_book():
    """Two books both in phase2 → not 'another phase'; must return False."""
    held = [
        "lock#dev-wwii-phase2-extract#BookA",
        "lock#dev-wwii-phase2-extract#BookB",
    ]
    with patch.object(ecs_entrypoint, "_multi_doc_enabled", return_value=True):
        with patch.object(ecs_entrypoint, "_held_lock_keys", return_value=held):
            with patch.dict(os.environ, {"ENV_NAME": "dev"}):
                assert ecs_entrypoint._other_phase_locked("phase2_extract.py") is False


def test_other_phase_locked_detects_different_phase():
    """A phase3 lock while we're phase2 → another phase IS active."""
    held = [
        "lock#dev-wwii-phase2-extract#BookA",
        "lock#dev-wwii-phase3-enrich#BookA",
    ]
    with patch.object(ecs_entrypoint, "_multi_doc_enabled", return_value=True):
        with patch.object(ecs_entrypoint, "_held_lock_keys", return_value=held):
            with patch.dict(os.environ, {"ENV_NAME": "dev"}):
                assert ecs_entrypoint._other_phase_locked("phase2_extract.py") is True


def test_other_phase_locked_serial_singleton_same_phase():
    """Serial singleton key of the same phase → not 'another phase'."""
    held = ["lock#dev-wwii-phase2-extract"]
    with patch.object(ecs_entrypoint, "_multi_doc_enabled", return_value=False):
        with patch.object(ecs_entrypoint, "_held_lock_keys", return_value=held):
            with patch.dict(os.environ, {"ENV_NAME": "dev"}):
                assert ecs_entrypoint._other_phase_locked("phase2_extract.py") is False


def test_any_pipeline_lock_held_true_when_any():
    with patch.object(
        ecs_entrypoint, "_held_lock_keys", return_value=["lock#dev-wwii-phase1-parse"]
    ):
        assert ecs_entrypoint._any_pipeline_lock_held() is True


# --- _openserp_demand_present: operator rule — multiple jobs in play => keep up;
#     no jobs => allow shutdown (aggregate demand, not single-job completion) ---


def test_openserp_demand_true_when_another_book_holds_phase2_lock():
    """Doc A finishing must NOT shut OpenSERP while Doc B holds a phase2 lock."""
    held = [
        "lock#dev-wwii-phase2-extract#BookA",
        "lock#dev-wwii-phase2-extract#BookB",
    ]
    with patch.object(ecs_entrypoint, "_held_lock_keys", return_value=held):
        with patch.dict(os.environ, {"ENV_NAME": "dev"}):
            assert ecs_entrypoint._openserp_demand_present() is True


def test_openserp_demand_true_when_phase3_lock_held():
    held = ["lock#dev-wwii-phase3-enrich#BookA"]
    with patch.object(ecs_entrypoint, "_held_lock_keys", return_value=held):
        with patch.dict(os.environ, {"ENV_NAME": "dev"}):
            assert ecs_entrypoint._openserp_demand_present() is True


def test_openserp_demand_false_when_no_serp_locks_and_no_tasks():
    """No serp-phase lock + no serp-phase tasks running => safe to shut down."""
    fake_ecs = type(
        "E", (), {"list_tasks": staticmethod(lambda **k: {"taskArns": []})}
    )()
    with patch.object(ecs_entrypoint, "_held_lock_keys", return_value=[]):
        with patch.object(ecs_entrypoint.boto3, "client", return_value=fake_ecs):
            with patch.dict(os.environ, {"ENV_NAME": "dev"}):
                assert ecs_entrypoint._openserp_demand_present() is False


def test_openserp_demand_true_when_serp_task_running_no_lock():
    """A running phase2 task with no lock yet still counts as demand."""
    fake_ecs = type(
        "E",
        (),
        {"list_tasks": staticmethod(lambda **k: {"taskArns": ["arn:task/x"]})},
    )()
    with patch.object(ecs_entrypoint, "_held_lock_keys", return_value=[]):
        with patch.object(ecs_entrypoint.boto3, "client", return_value=fake_ecs):
            with patch.dict(os.environ, {"ENV_NAME": "dev"}):
                assert ecs_entrypoint._openserp_demand_present() is True


def test_openserp_demand_failsafe_true_on_error():
    """On error, keep OpenSERP up (fail-safe) rather than risk pulling it."""
    with patch.object(
        ecs_entrypoint, "_held_lock_keys", side_effect=RuntimeError("boom")
    ):
        with patch.dict(os.environ, {"ENV_NAME": "dev"}):
            assert ecs_entrypoint._openserp_demand_present() is True


def test_any_pipeline_lock_held_false_when_none():
    with patch.object(ecs_entrypoint, "_held_lock_keys", return_value=[]):
        assert ecs_entrypoint._any_pipeline_lock_held() is False


def test_any_pipeline_lock_held_assumes_locked_on_error():
    """On scan error, must assume locked (don't tear down NAT prematurely)."""
    with patch.object(
        ecs_entrypoint, "_held_lock_keys", side_effect=RuntimeError("boom")
    ):
        assert ecs_entrypoint._any_pipeline_lock_held() is True
