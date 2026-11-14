"""Batch collect pass: a chapter whose request was QUEUED is not a failed chapter.

XChannel-8 batch E2E (2026-11-07): in batch mode every chapter raises BatchModeCollecting on the
collect pass by design (the request goes to the Grok Batch API; the retrieve re-run processes
it). _process_batch_results counted it as a failure, emitted ``phase2.chapter_failed
status=error`` (forwarded to Slack as a red X) and a bogus "Clear cache: rm -rf ..." hint - a
non-actionable alert on every batch run.
"""

import logging

from src.extraction import batch_parallel
from src.grok_client import BatchModeCollecting


def test_collecting_chapter_is_counted_as_collecting_not_failed(caplog):
    caplog.set_level(logging.INFO)
    results = {"processed": 0, "failed": 0, "chapters": []}
    batch_parallel._process_batch_results(
        [("Book", "ch-parsed.json", None)],
        [BatchModeCollecting("f8b74b90")],
        results,
        expect_collecting=True,
    )
    assert results["failed"] == 0 and results["collecting"] == 1
    msgs = [r.getMessage() for r in caplog.records]
    assert any("phase2.chapter_collecting" in m for m in msgs)
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)
    assert not any("chapter_failed" in m or "Clear cache" in m for m in msgs)


def test_real_failure_next_to_a_collecting_chapter_is_still_alerted(caplog):
    caplog.set_level(logging.INFO)
    results = {"processed": 0, "failed": 0, "chapters": []}
    batch_parallel._process_batch_results(
        [("Book", "a-parsed.json", None), ("Book", "b-parsed.json", None)],
        [BatchModeCollecting("k"), ValueError("bad json")],
        results,
        expect_collecting=True,
    )
    assert (results["failed"], results["collecting"]) == (1, 1)
    assert any("chapter_failed" in r.getMessage() for r in caplog.records)


def test_collecting_outside_a_batch_collect_pass_is_a_real_failure(caplog):
    # Retrieve re-run / sync run: a BatchModeCollecting there means a request was RE-QUEUED
    # instead of answered - a real failure that must alert.
    caplog.set_level(logging.INFO)
    results = {"processed": 0, "failed": 0, "chapters": []}
    batch_parallel._process_batch_results(
        [("Book", "ch-parsed.json", None)],
        [BatchModeCollecting("k")],
        results,
        expect_collecting=False,
    )
    assert results["failed"] == 1 and not results.get("collecting")
    assert any("chapter_failed" in r.getMessage() for r in caplog.records)


def test_retrieve_rerun_env_is_synchronous(monkeypatch):
    # Other phases: the retrieve re-run drops --batch; BATCH_PHASE2=true in the task env must
    # not re-enable batch mode (phase2_extract._resolve_batch_mode lets the env win).
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    import ecs_entrypoint

    monkeypatch.setenv("BATCH_PHASE2", "true")
    env = ecs_entrypoint._retrieve_rerun_env("phase3_enrich_data.py")
    assert "BATCH_PHASE2" not in env
    assert env["SKIP_RETRY"] == "1" and env["PIPELINE_PHASE"] == "phase3_enrich_data"


def test_the_phase2_retrieve_rerun_resumes_its_batch_rounds(monkeypatch):
    # Phase 2 resumes its rounds in batch mode, counting on from the retrieved batch's round.
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    import ecs_entrypoint

    monkeypatch.setenv("BATCH_PHASE2", "true")
    env = ecs_entrypoint._retrieve_rerun_env(
        "phase2_extract.py", "phase2-USA-E-XChannel-15files-r3-812reqs"
    )
    assert env["BATCH_PHASE2"] == "true" and env["BATCH_ROUND_START"] == "4"
    assert env["SKIP_RETRY"] == "1" and env["PIPELINE_PHASE"] == "phase2_extract"
