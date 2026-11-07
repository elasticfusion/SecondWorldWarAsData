"""Phase-3 must download the book's content/ tree (events + source_section) so the event_provenance
stamp can resolve SourceSectionID — else stamp resolved=0 reason=no_source_sections and data can't be
traced to its source paragraph (provenance is the #1 project goal). XChannel-8 2026-10-30.
"""

import os
from unittest import mock

os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
import ecs_entrypoint as EE


def test_provenance_inputs_downloads_content_prefix():
    calls = []
    with mock.patch.object(EE, "s3_sync_down", lambda p, _w: calls.append(p) or 3):
        EE._download_phase3_provenance_inputs(object(), "USA-E-XChannel-8")
    assert calls == ["output/content/USA-E-XChannel-8/"]


def test_provenance_inputs_rejects_traversal_book():
    calls = []
    with mock.patch.object(EE, "s3_sync_down", lambda p, _w: calls.append(p) or 0):
        EE._download_phase3_provenance_inputs(object(), "../evil")
        EE._download_phase3_provenance_inputs(object(), "a/../b")
    assert calls == []  # malicious -> no download


def test_unscoped_phase3_downloads_the_whole_content_tree():
    """XChannel-8 2026-11-07: an unscoped (whole-corpus) Phase 3 never fetched output/content/, so
    the in-document map step found no parsed docs and silently produced 0 maps."""
    calls = []
    with mock.patch.object(EE, "s3_sync_down", lambda p, _w: calls.append(p) or 5):
        EE._download_phase3_provenance_inputs(object(), "")
    assert calls == ["output/content/"]


def test_full_download_path_fetches_content_when_unscoped(monkeypatch):
    """Behavioural: an unscoped Phase 3 download syncs output/content/ (not just entity dirs)."""
    synced = []
    monkeypatch.delenv("BOOK_NAME", raising=False)
    monkeypatch.setattr(EE, "_wait_for_reconcile_barrier", lambda: "seen")
    monkeypatch.setattr(EE, "_s3_client", lambda: object())
    monkeypatch.setattr(EE, "_download_s3_prefix_skip_existing", lambda s3, p: (0, 0))
    monkeypatch.setattr(
        EE, "_observe_bibliography_download_coverage", lambda *a, **k: None
    )
    monkeypatch.setattr(EE, "s3_sync_down", lambda p, _w: synced.append(p) or 4)
    EE._download_phase3_from_s3()
    assert synced == ["output/content/"]


class _FakeS3:
    def __init__(self, keys):
        self.keys, self.downloaded = keys, []

    def get_paginator(self, _name):
        keys = self.keys

        class _P:
            def paginate(self, **_k):
                return [{"Contents": [{"Key": k} for k in keys]}]

        return _P()

    def download_file(self, _bucket, key, dest):
        from pathlib import Path

        Path(dest).write_text("x")
        self.downloaded.append(key)


def test_sync_down_skips_folder_markers_and_path_escapes(tmp_path, monkeypatch):
    fake = _FakeS3(
        [
            "output/content/",  # console "folder" marker: no file content
            "output/content/B/ok-parsed.json",
            "output/content/../../../escape.json",  # resolves outside local_dir
        ]
    )
    alerts = []
    monkeypatch.setattr(EE, "_s3_client", lambda: fake)
    monkeypatch.setattr(EE, "observe_warn", lambda *a, **k: alerts.append(a[1]) or "")
    work = tmp_path / "work"
    work.mkdir()
    assert EE.s3_sync_down("output/content/", work) == 1
    assert fake.downloaded == ["output/content/B/ok-parsed.json"]
    assert (work / "output/content/B/ok-parsed.json").exists()
    assert not (tmp_path / "escape.json").exists()
    assert alerts == [
        "unsafe_key_skipped"
    ]  # the escape alerts; the folder marker does not
