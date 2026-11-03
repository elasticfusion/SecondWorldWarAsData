"""M2: the cross-file OOB reconcile detector must be WIRED into phase0_ingest and
surface a SEVERITY_ERROR (misfiled-duplicate / vanished division) as a loud OBSERV."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import phase0_ingest as p0


def _write_stat(stats_dir: Path, name: str, source_file: str) -> None:
    (stats_dir / name).write_text(
        json.dumps(
            {
                "source_file": source_file,
                "rows": [
                    {
                        "category": "casualties",
                        "metric": "KIA",
                        "value": "123",
                        "division": "X",
                    },
                    {
                        "category": "awards",
                        "metric": "DSC",
                        "value": "4",
                        "division": "X",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def test_reconcile_wired_emits_loud_integrity_error(tmp_path, caplog):
    output_root = tmp_path / "output"
    stats = output_root / "oob" / "statistics"
    stats.mkdir(parents=True)
    # Two DIFFERENT source files with a byte-identical value-bearing statistics block
    # => the misfiled-duplicate case (one division duplicated, another vanished).
    _write_stat(stats, "a.json", "101st_airborne.md")
    _write_stat(stats, "b.json", "100th_infantry.md")

    logger = logging.getLogger("m2test")
    with caplog.at_level(logging.WARNING):
        p0._reconcile_cross_file(output_root, logger)

    # Loud canonical OBSERV with status=error (a real integrity anomaly).
    assert any(
        "OBSERV phase0_ingest.reconcile" in r.message
        and "decision=integrity_error" in r.message
        and "status=error" in r.message
        for r in caplog.records
    ), "reconcile did not emit a loud integrity_error OBSERV"
    # Report persisted for human triage.
    assert (output_root / "oob" / "reconcile" / "report.json").exists()


def test_reconcile_wired_clean_is_ok(tmp_path, caplog):
    output_root = tmp_path / "output"
    stats = output_root / "oob" / "statistics"
    stats.mkdir(parents=True)
    _write_stat(stats, "only.json", "82nd_airborne.md")

    logger = logging.getLogger("m2test")
    with caplog.at_level(logging.INFO):
        p0._reconcile_cross_file(output_root, logger)
    assert any(
        "OBSERV phase0_ingest.reconcile" in r.message and "status=ok" in r.message
        for r in caplog.records
    )
