"""AV sub-PR 3b: downloaded map binaries stage to unscanned/ (not prod) on Phase-3 sync, and the
trigger Lambda drains unscanned/ on Phase-3 completion (same ClamAV scanner)."""

import os
import tempfile
from pathlib import Path

os.environ.setdefault("S3_BUCKET", "b")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")


def test_sync_up_excludes_files_subdir_by_path():
    import ecs_entrypoint as e

    up = []
    e._s3_client = lambda: type(
        "S", (), {"upload_file": lambda s, p, b, k: up.append(k)}
    )()
    d = Path(tempfile.mkdtemp()) / "maps"
    (d / "files").mkdir(parents=True)
    (d / "map1.json").write_text("{}")
    (d / "files" / "map1.jpg").write_bytes(b"x")
    _n, keys = e.s3_sync_up(d, "output/maps", exclude_patterns=["files"])
    assert any("output/maps/map1.json" in k for k in keys)  # record -> prod
    assert not any("map1.jpg" in k for k in keys)  # binary excluded from prod


def test_sync_up_stages_files_to_unscanned():
    import ecs_entrypoint as e

    up = []
    e._s3_client = lambda: type(
        "S", (), {"upload_file": lambda s, p, b, k: up.append(k)}
    )()
    d = Path(tempfile.mkdtemp())
    (d / "files").mkdir()
    (d / "files" / "m.jpg").write_bytes(b"x")
    e.s3_sync_up(d / "files", "unscanned/output/maps/files")
    assert up == ["unscanned/output/maps/files/m.jpg"]


def test_phase3_complete_drains_phase2_does_not(monkeypatch):
    import lambda_handlers.trigger_handler as th
    import src.ingestion.av_scan as av

    monkeypatch.setattr(th, "AV_SCAN_ENABLED", True)
    monkeypatch.setattr(th, "_reconcile_pending", lambda reason: [])
    monkeypatch.setattr(th, "_make_av_scanner", lambda: object())
    calls = {"n": 0}

    class _Out:
        clean_keys = []
        quarantined_keys = []

    monkeypatch.setattr(
        av,
        "drain_unscanned",
        lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1) or _Out()),
    )
    th._drive_next_phase("3")
    assert calls["n"] == 1
    th._drive_next_phase("2")
    assert calls["n"] == 1  # phase2 doesn't drain
