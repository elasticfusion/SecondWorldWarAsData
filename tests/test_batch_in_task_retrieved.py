"""A batch answered in-task is not left for the poller, and a retrieve writes the cache where the
re-run reads it (2026-11-10, Cross-Channel Attack: the optional-entity batch was polled and
retrieved inside the Phase 2 task, but recorded "pending"; the poller started a second,
--retrieve-only task that wrote its answers under bare keys the re-run never found, re-called Grok
for every request and rewrote 22 entity files after dedup)."""

import json
import os
from unittest import mock

import pytest

os.environ.setdefault("S3_BUCKET", "test-bucket")

import ecs_entrypoint as ep  # noqa: E402


def _metrics(tmp_path, monkeypatch, **m):
    d = tmp_path / "output" / "metrics"
    d.mkdir(parents=True)
    (d / "batch_batch_x_1.json").write_text(
        json.dumps({"batch_id": "batch_x", "total_requests": 99, **m})
    )
    monkeypatch.setattr(ep, "WORKDIR", tmp_path)
    monkeypatch.setenv("BOOK_NAME", "B")


@pytest.fixture
def queue():
    with (
        mock.patch("src.utils.job_queue.get_active_jobs", return_value=[]),
        mock.patch("src.utils.job_queue._get_table") as table,
        mock.patch("src.utils.job_queue.enqueue_job") as enqueue,
        mock.patch("src.utils.job_queue.get_job", return_value=object()),
        mock.patch.object(ep, "_notify_batch_submitted") as notify,
    ):
        table.return_value.scan.return_value = {"Items": []}
        yield enqueue, notify


def test_a_batch_answered_in_task_is_recorded_retrieved(tmp_path, monkeypatch, queue):
    enqueue, notify = queue
    _metrics(tmp_path, monkeypatch, api_successes=99, api_errors=0, valid=99)
    assert ep._enqueue_from_metrics("phase2_extract.py") is True
    assert enqueue.call_args.args[0].status == "retrieved"
    notify.assert_not_called()  # nothing was left waiting


def test_a_submitted_batch_still_goes_to_the_poller(tmp_path, monkeypatch, queue):
    enqueue, notify = queue
    _metrics(tmp_path, monkeypatch, api_successes=0, api_errors=0)
    assert ep._enqueue_from_metrics("phase2_extract.py") is True
    assert enqueue.call_args.args[0].status == "pending"
    notify.assert_called_once()


def test_a_partly_answered_batch_is_not_called_retrieved():
    assert not ep._retrieved_in_task({"total_requests": 99, "api_successes": 50})
    assert ep._retrieved_in_task(
        {"total_requests": 99, "api_successes": 97, "api_errors": 2}
    )
    assert not ep._retrieved_in_task({"total_requests": 0})
    # every request errored: left for the poller, not called retrieved
    assert not ep._retrieved_in_task(
        {"total_requests": 5, "api_successes": 0, "api_errors": 5}
    )


def test_a_retrieve_writes_under_the_books_prefix(monkeypatch):
    from src.grok_client import current_book

    monkeypatch.setenv("BOOK_NAME", "USA-E-XChannel")
    monkeypatch.delenv("GROK_CACHE_DISABLED", raising=False)
    seen = {}

    def get_cache(cache_type):
        seen[cache_type] = current_book.get()
        return {}

    grok = mock.Mock()
    grok._get_cache.side_effect = get_cache
    n = ep._populate_cache(grok, {"casualties": {"a": "1", "b": "2"}}, "batch_x")
    assert n == 2 and seen == {"casualties": "USA-E-XChannel"}
    assert current_book.get() is None  # restored


def test_a_retrieve_respects_the_cache_switch(monkeypatch):
    monkeypatch.setenv("GROK_CACHE_DISABLED", "1")
    grok = mock.Mock()
    assert ep._populate_cache(grok, {"casualties": {"a": "1"}}, "batch_x") == 0
    grok._get_cache.assert_not_called()


def test_the_book_cache_prefix_is_the_one_the_rerun_reads(tmp_path, monkeypatch):
    """End to end on the real client: what the retrieve stores, a per-book lookup finds."""
    import src.grok_client as gc
    from src.utils.cache_backend import DiskCacheBackend

    monkeypatch.setenv("GROK_API_KEY", "test-not-a-key")
    monkeypatch.setenv("BOOK_NAME", "B")
    monkeypatch.delenv("GROK_CACHE_DISABLED", raising=False)
    grok = gc.GrokClient(DiskCacheBackend(tmp_path / "c"))
    ep._populate_cache(grok, {"casualties": {"k1": "answer"}}, "batch_x")
    token = gc.current_book.set("B")
    try:
        assert grok._get_cache("casualties")["k1"] == "answer"
    finally:
        gc.current_book.reset(token)
