"""PR1 (B1): _write_phase_results must not crash with NameError when an end-of-phase
enforcement step raises — its except handlers referenced an undefined `logger`."""

from __future__ import annotations

import logging

import phase2_extract as p2


def test_write_phase_results_enforcement_error_is_logged_not_nameerror(
    tmp_path, monkeypatch, caplog
):
    # In-main enforcement is the LOCAL-run path (under S3_BUCKET it is deferred post-sync).
    monkeypatch.delenv("S3_BUCKET", raising=False)
    output_root = tmp_path / "output"
    output_root.mkdir()
    (output_root / "content").mkdir()

    import src.utils.referential_integrity as ri

    def _raise(*a, **k):
        raise RuntimeError("boom")

    # Referential-integrity enforcement raises -> exercises the except handler that previously
    # referenced an undefined `logger` (NameError). On origin/main this NameError fires BEFORE the
    # warning is logged; on the fix the warning IS logged.
    monkeypatch.setattr(ri, "run_for_phase", _raise)

    with caplog.at_level(logging.WARNING):
        try:
            p2._write_phase_results(output_root, processed=1, failed=0)
        except SystemExit:
            pass  # a downstream yield-gate halt on the empty tmp dir is not what we're testing

    # The enforcement except handler ran and logged (it did NOT crash with NameError before this).
    assert any(
        "referential-integrity enforcement skipped: boom" in r.message
        for r in caplog.records
    ), "except handler crashed with NameError (undefined logger) before logging"
