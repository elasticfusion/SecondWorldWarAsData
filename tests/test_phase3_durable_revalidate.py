"""Post-sync Phase-3 validation must audit the POST-REPAIR tree.

Regression: the S3-side referential repair down-bound 31 stale Sub_eventIDs (0 dangling on S3),
but validate_phase3 audited the STALE local entity tree (downloaded as Phase-3 input BEFORE the
repair) and false-FAILed with 26 dangling. Fix: re-download the repaired entity dirs with OVERWRITE
before validating so the local tree matches authoritative post-repair S3.
"""

import os

os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import ecs_entrypoint as ee


def test_durable_fix_overwrites_repaired_entity_dirs_before_validate(monkeypatch):
    calls = {
        "skip_existing": [],
        "overwrite": [],
        "validated": False,
        "repaired": False,
    }

    # Repair (S3-side) — just record it ran.
    monkeypatch.setattr(
        "src.utils.referential_integrity.run_for_phase",
        lambda root, phase="": calls.__setitem__("repaired", True),
    )
    monkeypatch.setattr(ee, "_s3_client", lambda: object())
    monkeypatch.setattr(
        ee,
        "_download_s3_prefix_skip_existing",
        lambda s3, prefix: calls["skip_existing"].append(prefix) or (0, 0),
    )
    monkeypatch.setattr(
        ee,
        "_download_s3_prefix",
        lambda s3, prefix: calls["overwrite"].append(prefix) or 0,
    )
    monkeypatch.setattr(
        "scripts.validate_phase3.run",
        lambda root, strict_coverage: calls.__setitem__("validated", True) or 0,
    )

    ee._phase3_durable_referential_fix()

    assert calls["repaired"] and calls["validated"]
    # content pulled skip-existing (events unchanged by repair)
    assert "output/content/" in calls["skip_existing"]
    # the repaired entity dirs re-downloaded with OVERWRITE so validate sees post-repair state
    for ent in ("places", "people_groups", "dates", "people", "equipment"):
        assert f"output/{ent}/" in calls["overwrite"], f"{ent} not overwrite-refreshed"


def test_durable_fix_failsafe_never_raises(monkeypatch):
    # repair raises -> must be swallowed; validate still attempted; no exception out.
    def _boom(*a, **k):
        raise RuntimeError("repair blew up")

    monkeypatch.setattr("src.utils.referential_integrity.run_for_phase", _boom)
    monkeypatch.setattr(ee, "_s3_client", lambda: object())
    monkeypatch.setattr(ee, "_download_s3_prefix_skip_existing", lambda s3, p: (0, 0))
    monkeypatch.setattr(ee, "_download_s3_prefix", lambda s3, p: 0)
    monkeypatch.setattr("scripts.validate_phase3.run", lambda root, strict_coverage: 0)
    ee._phase3_durable_referential_fix()  # must not raise
