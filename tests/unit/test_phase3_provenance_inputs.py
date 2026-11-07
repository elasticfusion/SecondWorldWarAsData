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


def test_sync_down_works_when_the_target_dir_is_a_symlink_into_elsewhere(
    tmp_path, monkeypatch
):
    """REGRESSION (XChannel-8 rerun 2026-11-07): in the container /app/contentrepository is a symlink
    into the work dir. A resolve()-based escape check flagged every Phase 1 key as unsafe, downloaded
    0 files and the phase exited 1. Symlinked layouts must download normally."""
    real = tmp_path / "pipeline" / "contentrepository"
    real.mkdir(parents=True)
    app = tmp_path / "app"
    app.mkdir()
    (app / "contentrepository").symlink_to(real, target_is_directory=True)
    fake = _FakeS3(
        [
            "contentrepository/B/B.html",
            "contentrepository/B/chapter1/chapter1-content.md",
        ]
    )
    monkeypatch.setattr(EE, "_s3_client", lambda: fake)
    assert EE.s3_sync_down("contentrepository/B/", app) == 2
    assert (real / "B" / "B.html").exists()  # landed through the symlink


def test_safe_local_path_rules(tmp_path):
    f = EE._safe_local_path
    assert f(tmp_path, "a/b.json") == tmp_path / "a/b.json"
    assert (
        f(tmp_path, "a/../b.json") is None
    )  # ANY '..' segment: never legitimate in a key
    assert f(tmp_path, "contentrepository/B/../../app.py") is None  # leaves the prefix
    assert f(tmp_path, "../x.json") is None
    assert f(tmp_path, "a/../../x.json") is None
    assert f(tmp_path, "/etc/passwd") is None
    assert f(tmp_path, "a/") is None and f(tmp_path, "") is None


def test_download_keys_skips_markers_and_escapes(tmp_path, monkeypatch):
    """The incremental Phase 1 path (_download_keys) had no guard at all."""
    fake = _FakeS3(
        [
            "content/B/chapter1/",  # folder marker equal to the listed prefix
            "content/B/chapter1/c.md",
            "content/B/chapter1/../../../evil.py",
        ]
    )
    alerts = []
    monkeypatch.setattr(EE, "_s3_client", lambda: fake)
    monkeypatch.setattr(EE, "observe_warn", lambda *a, **k: alerts.append(a[1]) or "")
    n = EE._download_keys(["content/B/chapter1/c.md"], tmp_path)
    assert n == 1 and fake.downloaded == ["content/B/chapter1/c.md"]
    assert not (tmp_path.parent / "evil.py").exists()
    assert alerts == ["unsafe_key_skipped"]
