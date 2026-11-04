"""Guard: in ECS, Phase 2's referential audit runs AFTER _final_sync (on success AND failure).

XChannel ch8 e2e: the in-main audit read S3 before the final local->S3 sync and reported
images.SourceSectionID 14/14 dangling — the source_section files existed seconds later and the
final corpus audited clean for that edge. Provenance (local-tree) stays in-main.
"""

import logging
import os

import pytest

os.environ.setdefault("S3_BUCKET", "test-bucket")
import ecs_entrypoint  # noqa: E402
import phase2_extract  # noqa: E402


@pytest.fixture
def _spies(monkeypatch):
    import src.utils.provenance_integrity as pi

    calls = []
    monkeypatch.setattr(
        phase2_extract,
        "_local_referential_enforce",
        lambda o, lg: calls.append("ref"),
    )
    monkeypatch.setattr(pi, "run_for_phase", lambda o, phase="": calls.append("prov"))
    return calls


def _write_results(tmp_path):
    out = tmp_path / "output"
    (out / "content").mkdir(parents=True)
    try:
        phase2_extract._write_phase_results(out, processed=1, failed=0)
    except SystemExit:
        pass  # downstream yield gate on an empty tmp corpus — not under test


def test_s3_run_defers_referential_but_keeps_provenance(monkeypatch, tmp_path, _spies):
    monkeypatch.setenv("S3_BUCKET", "b")
    _write_results(tmp_path)
    assert "ref" not in _spies and "prov" in _spies


def test_local_run_does_both(monkeypatch, tmp_path, _spies):
    monkeypatch.delenv("S3_BUCKET", raising=False)
    _write_results(tmp_path)
    assert "ref" in _spies and "prov" in _spies


class _Stop(BaseException):  # not swallowed by _post_process's except Exception
    pass


def test_post_process_phase2_audits_before_dedup(monkeypatch):
    order = []
    monkeypatch.setattr(
        ecs_entrypoint, "_post_sync_referential_enforce", lambda lbl: order.append(lbl)
    )

    def _dedup(env):
        order.append("dedup")
        raise _Stop

    monkeypatch.setattr(ecs_entrypoint, "_run_dedup_detection", _dedup)
    with pytest.raises(_Stop):
        ecs_entrypoint._post_process("phase2_extract.py", {})
    assert order[:2] == ["Phase 2 (post-sync)", "dedup"]


def test_failed_phase2_is_still_audited(monkeypatch):
    seen = []
    monkeypatch.setattr(
        ecs_entrypoint, "_post_sync_referential_enforce", lambda lbl: seen.append(lbl)
    )
    ecs_entrypoint._audit_failed_phase2("phase2_extract.py", 3)
    ecs_entrypoint._audit_failed_phase2("phase3_enrich_data.py", 1)
    assert seen == ["Phase 2 (post-sync, failed rc=3)"]


@pytest.mark.parametrize(
    "report,want",
    [
        ({"status": "ok", "total_dangling": 0}, "ok"),
        ({"status": "unknown", "skipped": "timeout"}, "warn"),
        (None, "warn"),
    ],
)
def test_post_sync_audit_surfaces_unmeasured(monkeypatch, report, want):
    import src.utils.referential_integrity as ri

    obs = []
    monkeypatch.setattr(ri, "run_for_phase", lambda o, phase="": report)
    monkeypatch.setattr(ecs_entrypoint, "observe", lambda *a, **k: obs.append((a, k)))
    ecs_entrypoint._post_sync_referential_enforce("Phase 2 (post-sync)")
    assert obs[0][0] == ("integrity", "post_sync_audit") and obs[0][1]["status"] == want


def test_post_sync_audit_never_raises(monkeypatch):
    import src.utils.referential_integrity as ri

    def boom(*a, **k):
        raise RuntimeError("x")

    obs = []
    monkeypatch.setattr(ri, "run_for_phase", boom)
    monkeypatch.setattr(ecs_entrypoint, "observe", lambda *a, **k: obs.append(k))
    ecs_entrypoint._post_sync_referential_enforce("P")
    assert obs[0]["status"] == "warn" and obs[0]["error"] == "RuntimeError"
