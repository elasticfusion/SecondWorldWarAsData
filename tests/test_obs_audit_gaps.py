"""Guard: observability gaps found by the post-#743 audit are closed.

Each degraded/skipped path below must emit a canonical, greppable OBSERV line.
"""

import asyncio
import json
import logging
import os

import pytest

os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
import ecs_entrypoint  # noqa: E402
import phase2_extract  # noqa: E402
from src.extraction import batch_parallel  # noqa: E402


def test_local_referential_audit_failure_is_observed(monkeypatch, tmp_path):
    import src.utils.referential_integrity as ri

    def boom(*a, **k):
        raise RuntimeError("x")

    seen = []
    monkeypatch.setattr(ri, "run_for_phase", boom)
    monkeypatch.setattr(
        phase2_extract, "observe_warn", lambda *a, **k: seen.append((a, k))
    )
    phase2_extract._local_referential_enforce(tmp_path, logging.getLogger("t"))
    assert seen[0][0] == ("integrity", "local_audit")
    assert seen[0][1]["error"] == "RuntimeError"


def test_failed_phase_final_sync_error_is_loud_and_does_not_raise(monkeypatch):
    def boom(_p):
        raise OSError("s3 down")

    seen = []
    monkeypatch.setattr(ecs_entrypoint, "_final_sync", boom)
    monkeypatch.setattr(
        ecs_entrypoint, "observe_error", lambda *a, **k: seen.append((a, k))
    )
    ecs_entrypoint._final_sync_failed_phase("phase2_extract.py")  # must not raise
    assert seen[0][0] == ("phase", "final_sync_failed")
    assert seen[0][1]["error"] == "OSError"


def test_chapter_failure_emits_canonical_observ(monkeypatch):
    seen = []
    monkeypatch.setattr(
        batch_parallel, "observe_error", lambda *a, **k: seen.append((a, k))
    )
    results = {"processed": 0, "failed": 0}
    batch_parallel._process_batch_results(
        [("Book", "ch-parsed.json", None)], [asyncio.TimeoutError()], results
    )
    assert seen[0][0] == ("phase2", "chapter_failed")
    assert seen[0][1]["chapter"] == "ch-parsed.json"
    assert seen[0][1]["book"] == "Book"
    assert "TimeoutError" in seen[0][1]["reason"]


def test_incompatible_group_candidate_is_counted(caplog):
    import src.dedup.find_duplicate_groups as fdg
    from src.dedup.unit_key import derive_unit_key

    cluster = [{"name": "9th Division"}, {"name": "9th Infantry Division"}]
    caplog.set_level(logging.INFO)
    ok, why = fdg._canonical_match(
        derive_unit_key("9th Division"),
        derive_unit_key("9th Armored Division"),
        "9th Division",
        "9th Armored Division",
        cluster,
    )
    assert not ok and "incompatible" in why
    assert any(
        "OBSERV dedup.group_candidate_incompatible" in r.getMessage()
        for r in caplog.records
    )


def test_metadata_sidecar_observ_line_is_emitted(caplog):
    from unittest.mock import MagicMock, patch

    import lambda_handlers.trigger_handler as th

    caplog.set_level(logging.INFO)
    with (
        patch.object(th, "BUCKET", "buck"),
        patch.object(th.boto3, "client", return_value=MagicMock()),
        patch("src.ingestion.review_reject.reject_to_review"),
    ):
        th._content_keys(["contentrepository/B/ch1/ch1-meta.yaml"])
    assert any(
        "OBSERV trigger.metadata_sidecar status=ok" in r.getMessage()
        for r in caplog.records
    )


def test_provenance_non_object_record_is_loud(tmp_path, monkeypatch):
    import src.utils.observe as obs
    from src.utils import provenance_integrity as pi

    seen = []
    monkeypatch.setattr(obs, "observe_warn", lambda *a, **k: seen.append(k))
    d = tmp_path / "people"
    d.mkdir()
    (d / "x.json").write_text(json.dumps([1, 2]), encoding="utf-8")
    assert pi._iter_entity_records(tmp_path, "people/*.json") == []
    assert seen and seen[0]["error"] == "not_a_json_object"


@pytest.mark.parametrize("lat,lon", [(float("nan"), float("nan")), (float("inf"), 1.0)])
def test_non_finite_coords_are_missing(lat, lon):
    from src.utils.semantic_flags import flags_for

    rec = {"coordinates": {"latitude": lat, "longitude": lon}}
    assert flags_for("places", rec) == ["geo_missing_coords"]


def test_retrieve_only_failure_uses_safe_sync_and_still_audits(monkeypatch):
    """Call-site guard: a failed retrieve whose final sync raises must still audit, mark the
    job failed and exit with the phase's rc (reverting to a bare _final_sync would break this).
    """
    import inspect

    src = inspect.getsource(ecs_entrypoint.run_retrieve_only)
    i_safe = src.index("_final_sync_failed_phase(phase_script)")
    i_audit = src.index("_audit_failed_phase2(phase_script, result.returncode)")
    i_status = src.index('update_job_status(batch_id, "failed")')
    assert i_safe < i_audit < i_status
    run_src = inspect.getsource(ecs_entrypoint.run_phase)
    assert run_src.index("_final_sync_failed_phase(phase_script)") < run_src.index(
        "_audit_failed_phase2(phase_script, result.returncode)"
    )


def test_final_sync_success_is_not_observed(monkeypatch):
    seen = []
    monkeypatch.setattr(ecs_entrypoint, "_final_sync", lambda _p: None)
    monkeypatch.setattr(ecs_entrypoint, "observe_error", lambda *a, **k: seen.append(a))
    ecs_entrypoint._final_sync_failed_phase("phase2_extract.py")
    assert seen == []
