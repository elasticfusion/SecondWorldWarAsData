"""AV sub-PR 3b: downloaded map binaries stage to unscanned/ (not prod) on Phase-3 sync, and the
trigger Lambda drains unscanned/ on Phase-3 completion (same ClamAV scanner)."""

import os
import tempfile
from pathlib import Path

os.environ.setdefault("S3_BUCKET", "b")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")


def test_sync_up_excludes_files_subdir_by_path(monkeypatch):
    import ecs_entrypoint as e

    up = []
    # monkeypatch: a bare assignment leaked this fake into every later test
    monkeypatch.setattr(
        e,
        "_s3_client",
        lambda: type("S", (), {"upload_file": lambda s, p, b, k: up.append(k)})(),
    )
    d = Path(tempfile.mkdtemp()) / "maps"
    (d / "files").mkdir(parents=True)
    (d / "map1.json").write_text("{}")
    (d / "files" / "map1.jpg").write_bytes(b"x")
    _n, keys = e.s3_sync_up(d, "output/maps", exclude_patterns=["files"])
    assert any("output/maps/map1.json" in k for k in keys)  # record -> prod
    assert not any("map1.jpg" in k for k in keys)  # binary excluded from prod


def test_sync_up_stages_files_to_unscanned(monkeypatch):
    import ecs_entrypoint as e

    up = []
    # monkeypatch: a bare assignment leaked this fake into every later test
    monkeypatch.setattr(
        e,
        "_s3_client",
        lambda: type("S", (), {"upload_file": lambda s, p, b, k: up.append(k)})(),
    )
    d = Path(tempfile.mkdtemp())
    (d / "files").mkdir()
    (d / "files" / "m.jpg").write_bytes(b"x")
    e.s3_sync_up(d / "files", "unscanned/output/maps/files")
    assert up == ["unscanned/output/maps/files/m.jpg"]


def test_phase2_and_phase3_completion_start_a_drain_phase1_does_not(monkeypatch):
    import lambda_handlers.trigger_handler as th

    monkeypatch.setattr(th, "_reconcile_pending", lambda reason: [])
    started = []
    monkeypatch.setattr(th, "_start_av_drain", lambda reason: started.append(reason))
    th._drive_next_phase("3")
    th._drive_next_phase("2")  # Phase 2 stages images: drained as soon as it ends
    th._drive_next_phase("1")  # Phase 1 uploads no binaries
    assert started == ["phase-3", "phase-2"]
