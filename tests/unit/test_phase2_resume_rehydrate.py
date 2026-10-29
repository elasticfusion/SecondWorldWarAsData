"""Phase-2 resume must rehydrate existing entity RECORD dirs from S3 so the yield gate's LOCAL
count matches S3 — else a resume (data left in place) false-fail_stops with people=0 while S3 has 58
(XChannel-8 2026-10-29). Regression for the entity-rehydration fix."""

import os
from unittest import mock

os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import ecs_entrypoint as EE


def test_rehydrate_scored_entities_syncs_all_five_dirs():
    calls = []

    def fake_sync(prefix, _workdir):
        calls.append(prefix)
        return {
            "output/people/": 58,
            "output/people_groups/": 31,
            "output/dates/": 18,
            "output/places/": 96,
            "output/equipment/": 0,
        }.get(prefix, 0)

    with mock.patch.object(EE, "s3_sync_down", fake_sync):
        total = EE._rehydrate_scored_entities()

    assert calls == [
        "output/people/",
        "output/people_groups/",
        "output/dates/",
        "output/places/",
        "output/equipment/",
    ]
    assert total == 58 + 31 + 18 + 96 + 0  # 203 records rehydrated


def test_rehydrate_is_fail_safe_on_empty():
    with mock.patch.object(EE, "s3_sync_down", lambda *a: 0):
        assert EE._rehydrate_scored_entities() == 0
