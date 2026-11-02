"""Model-A drainer: drain the unscanned/ queue in bounded SEQUENTIAL chunks, single-drainer (S3
lock), keys-after-snapshot wait for next cycle. Owner contract 2026-11-02."""

import time
import pytest

import src.ingestion.av_scan as av
from src.ingestion.av_scan import drain_unscanned, ScanResult, Verdict


class _Dt:
    def __init__(self, ts):
        self.ts = ts

    def timestamp(self):
        return self.ts


class FakeS3:
    def __init__(self, n_clean=7, lock_ts=None):
        self.objs = {}
        for i in range(n_clean):
            self.objs[f"unscanned/output/maps/files/m{i}.jpg"] = b"x"
        self.objs["unscanned/output/maps/files/virus.jpg"] = b"x"
        self.objs["unscanned/.keep"] = b""
        self.lock_ts = lock_ts

    def head_bucket(self, **k):
        pass

    def put_object(self, **k):
        self.objs[k["Key"]] = k.get("Body", b"")

    def copy_object(self, **k):
        self.objs[k["Key"]] = self.objs[k["CopySource"]["Key"]]

    def delete_object(self, **k):
        self.objs.pop(k["Key"], None)

    def head_object(self, **k):
        if k["Key"].endswith(".scan-lock") and self.lock_ts is not None:
            return {"LastModified": _Dt(self.lock_ts)}
        raise RuntimeError("no key")

    def get_paginator(self, name):
        objs = self.objs

        class P:
            def paginate(self, Bucket, Prefix):
                yield {
                    "Contents": [{"Key": k} for k in list(objs) if k.startswith(Prefix)]
                }

        return P()


class Sc:
    def scan(self, b, keys):
        return [
            ScanResult(
                k,
                Verdict.INFECTED if "virus" in k else Verdict.CLEAN,
                signature="X" if "virus" in k else "",
            )
            for k in keys
        ]


@pytest.fixture(autouse=True)
def _noop_alert(monkeypatch):
    monkeypatch.setattr(av, "reject_to_review", lambda *a, **k: "m")
    monkeypatch.setattr(av, "freeze_submitter", lambda *a, **k: None)


def test_full_drain_chunked():
    s3 = FakeS3()
    out = drain_unscanned(s3, "b", Sc(), chunk_size=3)  # 8 files -> 3 chunks
    assert len(out.clean_keys) == 7 and len(out.quarantined_keys) == 1
    assert "output/maps/files/m0.jpg" in s3.objs  # promoted
    assert "quarantine/output/maps/files/virus.jpg" in s3.objs
    assert "unscanned/.keep" in s3.objs  # excluded, not dispositioned
    assert "unscanned/.scan-lock" not in s3.objs  # lock released


def test_fresh_lock_skips():
    s3 = FakeS3(lock_ts=time.time())
    out = drain_unscanned(s3, "b", Sc())
    assert out.clean_keys == [] and "output/maps/files/m0.jpg" not in s3.objs


def test_stale_lock_reclaimed():
    s3 = FakeS3(lock_ts=time.time() - 3600)  # > TTL
    out = drain_unscanned(s3, "b", Sc(), chunk_size=10)
    assert len(out.clean_keys) == 7


def test_empty_queue():
    s3 = FakeS3(n_clean=0)
    s3.objs = {"unscanned/.keep": b""}  # only the marker
    out = drain_unscanned(s3, "b", Sc())
    assert out.clean_keys == [] and out.quarantined_keys == []


def test_chunk_size_zero_clamps_not_silent():
    # a mis-wired chunk_size<1 must NOT silently skip everything (prior half-applied guard bug):
    # step clamps to 1, all files still dispositioned.
    for cs in (0, -3):
        s3 = FakeS3()
        out = drain_unscanned(s3, "b", Sc(), chunk_size=cs)
        assert len(out.clean_keys) == 7 and len(out.quarantined_keys) == 1
