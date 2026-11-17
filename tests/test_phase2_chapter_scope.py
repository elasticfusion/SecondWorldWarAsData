"""Re-submitting one chapter runs Phase 2 on that chapter only: the Phase 1 manifest's chapters
become PHASE2_CHAPTERS (the book-scoped resume rehydrate pulls every chapter's content, and Phase 2
used to re-extract - and pay for - all of them), and a handed-off round's batch job carries the
scope to the task that resumes it."""

from pathlib import Path
from unittest import mock

import pytest

import phase2_extract as p2


def _files(*names):
    return [Path(f"/w/output/content/B/{n}-parsed.json") for n in names]


def test_only_the_named_chapters_run():
    files = _files("chapter7", "chapter8full", "chapter9")
    got = p2._apply_chapter_scope(files, "chapter8full", mock.Mock())
    assert got == _files("chapter8full")


def test_no_scope_leaves_the_book_whole():
    files = _files("chapter7", "chapter8full")
    assert p2._apply_chapter_scope(files, "", mock.Mock()) == files


def test_an_unknown_chapter_is_a_hard_error_not_a_silent_widen():
    with pytest.raises(SystemExit) as stop:
        p2._apply_chapter_scope(_files("chapter7"), "chapter8full", mock.Mock())
    assert stop.value.code == 2


@pytest.fixture
def ep(monkeypatch):
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    import ecs_entrypoint

    return ecs_entrypoint


def test_the_manifest_chapters_become_the_phase2_scope(ep, monkeypatch):
    monkeypatch.setenv("BOOK_NAME", "B")
    monkeypatch.delenv("PHASE2_CHAPTERS", raising=False)
    ep._scope_to_manifest_chapters(
        [
            "output/content/B/chapter8full-parsed.json",
            "output/content/Other/chapter1-parsed.json",  # another book's key: ignored
            "output/content/B/chapter8full-notes-event.json",
        ]
    )
    import os

    assert os.environ["PHASE2_CHAPTERS"] == "chapter8full"


def test_a_resumed_runs_scope_is_not_overwritten(ep, monkeypatch):
    import os

    monkeypatch.setenv("BOOK_NAME", "B")
    monkeypatch.setenv("PHASE2_CHAPTERS", "chapter3")
    ep._scope_to_manifest_chapters(["output/content/B/chapter8full-parsed.json"])
    assert os.environ["PHASE2_CHAPTERS"] == "chapter3"


def test_a_phase2_batch_job_carries_the_chapter_scope(monkeypatch):
    from src.utils import job_queue as jq

    monkeypatch.setenv("PHASE2_CHAPTERS", "chapter8full")
    table = mock.Mock()
    with mock.patch.object(jq, "_get_table", return_value=table):
        jq.enqueue_job(jq.BatchJob("b-1", "phase2", "B", "n", 1))
        jq.enqueue_job(jq.BatchJob("b-2", "phase3", "B", "n", 1))
    phase2_item = table.put_item.call_args_list[0].kwargs["Item"]
    phase3_item = table.put_item.call_args_list[1].kwargs["Item"]
    assert phase2_item["chapters"] == "chapter8full"
    assert "chapters" not in phase3_item


def test_the_retrieve_task_gets_the_jobs_chapter_scope(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    from lambda_handlers import batch_poller

    ecs = mock.Mock()
    ecs.run_task.return_value = {"tasks": [{"taskArn": "arn:t"}], "failures": []}
    job = {
        "batch_id": "b-1",
        "phase": "phase2",
        "book": "B",
        "chapters": "chapter8full",
    }
    with (
        mock.patch.object(batch_poller.boto3, "client", return_value=ecs),
        mock.patch.object(batch_poller, "_get_network_config", return_value={}),
        mock.patch.object(batch_poller, "_claim_retrieve", return_value=True),
        mock.patch.object(batch_poller, "_wait_for_nat"),
        mock.patch.object(batch_poller, "_hold_retrieve", return_value=True),
    ):
        batch_poller._trigger_retrieve(job)
    env = ecs.run_task.call_args.kwargs["overrides"]["containerOverrides"][0][
        "environment"
    ]
    assert {"name": "PHASE2_CHAPTERS", "value": "chapter8full"} in env


def test_no_book_means_no_chapter_scope(ep, monkeypatch):
    """Chapter names are per book: without BOOK_NAME, never merge several books' names."""
    import os

    monkeypatch.delenv("BOOK_NAME", raising=False)
    monkeypatch.delenv("PHASE2_CHAPTERS", raising=False)
    ep._scope_to_manifest_chapters(
        [
            "output/content/A/chapter1-parsed.json",
            "output/content/B/chapter1-parsed.json",
        ]
    )
    assert "PHASE2_CHAPTERS" not in os.environ


def test_a_job_read_back_keeps_its_chapter_scope():
    from src.utils import job_queue as jq

    table = mock.Mock()
    table.get_item.return_value = {
        "Item": {
            "batch_id": "b-1",
            "phase": "phase2",
            "book": "B",
            "submitted_at": 1,
            "status": "pending",
            "chapters": "chapter8full",
        }
    }
    with mock.patch.object(jq, "_get_table", return_value=table):
        assert jq.get_job("b-1").chapters == "chapter8full"
