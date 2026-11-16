"""Phase-2 resume must rehydrate existing entity RECORD dirs from S3 so the yield gate's LOCAL
count matches S3 — else a resume (data left in place) false-fail_stops with people=0 while S3 has 58
(XChannel-8 2026-10-29). Regression for the entity-rehydration fix."""

import os
from unittest import mock

import pytest

os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import ecs_entrypoint as EE


def test_rehydrate_syncs_the_scored_and_the_local_deduplicating_dirs():
    """weather, logistics and casualties de-duplicate against the records on local disk: a fresh
    container without them wrote every statement again (2026-11-16: 13 duplicate weather records
    across batch rounds, 102 duplicate casualties from a re-run)."""
    calls = []
    counts = {"output/people/": 58, "output/places/": 96, "output/casualties/": 85}

    def fake_sync(prefix, _workdir):
        calls.append(prefix)
        return counts.get(prefix, 0)

    with (
        mock.patch.dict(os.environ, {"BOOK_NAME": ""}),
        mock.patch.object(EE, "s3_sync_down", fake_sync),
    ):
        total = EE._rehydrate_scored_entities()

    assert calls == [
        f"output/{d}/"
        for d in ("people", "people_groups", "dates", "places", "equipment",
                  "weather", "logistics", "casualties")
    ]  # fmt: skip
    assert total == 58 + 96 + 85


def test_rehydrate_is_fail_safe_on_empty():
    with mock.patch.object(EE, "s3_sync_down", lambda *a: 0):
        assert EE._rehydrate_scored_entities() == 0


def test_malicious_book_name_not_synced_for_content():
    """A traversal/garbage BOOK_NAME must NOT produce a content/ sync (regex-gated)."""
    calls = []
    with (
        mock.patch.dict(os.environ, {"BOOK_NAME": "../evil"}),
        mock.patch.object(EE, "s3_sync_down", lambda p, _w: calls.append(p) or 0),
    ):
        EE._rehydrate_scored_entities()
    # the entity dirs sync, but NO content/ key for the malicious book
    assert not any(c.startswith("output/content/") for c in calls)


def test_every_rehydrated_dir_is_one_the_final_sync_uploads():
    assert set(EE.REHYDRATED_ENTITY_DIRS) <= set(EE._PHASE23_ENTITY_DIRS)


def test_the_final_upload_skips_only_downloads_the_task_left_unchanged(
    tmp_path, monkeypatch
):
    """A rehydrated weather/index.json the task then updated must be uploaded (skipping every
    downloaded key lost it - and the next round wrote duplicates again)."""
    same = tmp_path / "output" / "people" / "a.json"
    changed = tmp_path / "output" / "weather" / "index.json"
    gone = tmp_path / "output" / "casualties" / "c.json"
    for f in (same, changed):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{}")
    monkeypatch.setattr(
        EE,
        "_downloaded_keys",
        {
            "output/people/a.json": same.stat().st_mtime,
            "output/weather/index.json": changed.stat().st_mtime - 5,  # modified since
            "output/casualties/c.json": 1.0,  # removed locally
        },
    )
    assert not gone.exists()  # a removed download is not skipped either
    assert EE._unchanged_downloads(tmp_path) == {"output/people/a.json"}


def test_stopping_the_background_sync_flushes_the_last_changes(monkeypatch):
    sync = EE.BackgroundSync(120)
    flushed = []
    monkeypatch.setattr(sync, "_upload_changed", lambda: flushed.append(True))
    monkeypatch.setattr(sync, "_thread", mock.Mock())
    monkeypatch.setattr(sync, "_sync", lambda: pytest.fail("the watchdog ran at stop"))
    sync.stop()
    assert flushed == [True]  # the upload only: never the idle watchdog
