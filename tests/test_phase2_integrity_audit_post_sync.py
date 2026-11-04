"""Guard: in ECS, Phase 2's referential/provenance audit runs AFTER _final_sync, not before.

XChannel ch8 e2e: the in-main audit read S3 before the final local->S3 sync and reported
images.SourceSectionID 14/14 dangling — the source_section files existed locally and in S3
seconds later. The final corpus audited clean for that edge.
"""

import inspect
import os

import pytest

os.environ.setdefault("S3_BUCKET", "test-bucket")
import ecs_entrypoint  # noqa: E402
import phase2_extract  # noqa: E402


def test_phase2_main_defers_enforcement_when_s3():
    src = inspect.getsource(phase2_extract)
    i = src.index(
        'if not os.environ.get("S3_BUCKET"):\n        _local_integrity_enforce('
    )
    assert i > 0
    # no unconditional in-main referential enforcement left in phase2 main flow
    assert src.count('phase="Phase 2"') == 2  # both inside _local_integrity_enforce


def test_local_integrity_enforce_runs_both(monkeypatch, tmp_path):
    import src.utils.provenance_integrity as pi
    import src.utils.referential_integrity as ri

    calls = []
    monkeypatch.setattr(
        ri, "run_for_phase", lambda o, phase="": calls.append(("ri", phase))
    )
    monkeypatch.setattr(
        pi, "run_for_phase", lambda o, phase="": calls.append(("pi", phase))
    )
    phase2_extract._local_integrity_enforce(
        tmp_path, phase2_extract.logging.getLogger("t")
    )
    assert calls == [("ri", "Phase 2"), ("pi", "Phase 2")]


def test_post_process_phase2_enforces_before_dedup(monkeypatch):
    order = []
    monkeypatch.setattr(
        ecs_entrypoint, "_post_sync_integrity_enforce", lambda lbl: order.append(lbl)
    )

    class _Stop(BaseException):  # not swallowed by _post_process's except Exception
        pass

    def _dedup(env):
        order.append("dedup")
        raise _Stop

    monkeypatch.setattr(ecs_entrypoint, "_run_dedup_detection", _dedup)
    with pytest.raises(_Stop):
        ecs_entrypoint._post_process("phase2_extract.py", {})
    assert order[:2] == ["Phase 2 (post-sync)", "dedup"]


def test_post_sync_enforce_never_raises(monkeypatch):
    import src.utils.provenance_integrity as pi
    import src.utils.referential_integrity as ri

    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(ri, "run_for_phase", boom)
    monkeypatch.setattr(pi, "run_for_phase", boom)
    ecs_entrypoint._post_sync_integrity_enforce("Phase 2 (post-sync)")
