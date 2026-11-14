"""ecs_entrypoint dedup runner: detectors run as ``python -m src.dedup.<module>`` and every failure
is a loud ``OBSERV dedup.<event> status=error`` (SRC_SCRIPTS_SEPARATION.md PR 1).

Before PR 1 the runner listed ``scripts/<file>.py`` paths and silently skipped any that did not
exist, so a rename/move dropped a whole entity type's duplicate report with no signal. These tests
pin: (1) every listed detector is a real importable module with a ``main()``; (2) a non-zero exit
is an observe_error; (3) the reclassify / index-cleanup wrappers fail loudly, not as a warning.
"""

import importlib
import logging
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# ecs_entrypoint reads S3_BUCKET at import time — set a dummy before it is imported anywhere.
os.environ.setdefault("S3_BUCKET", "test-bucket")

import ecs_entrypoint as ep  # noqa: E402


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """No Slack escalation, no S3, no coords/auto-merge/incremental side effects."""
    monkeypatch.delenv("NOTIFICATION_TOPIC_ARN", raising=False)
    monkeypatch.setattr(ep, "_generate_places_coords", lambda: None)
    monkeypatch.setattr(ep, "_auto_merge_exact_duplicates", lambda: None)
    monkeypatch.setattr(ep, "_s3_client", MagicMock)
    monkeypatch.setattr(
        "src.dedup.incremental.set_last_dedup_run", lambda *_a, **_k: None
    )


def _observ(caplog, scope, event):
    needle = f"OBSERV {scope}.{event} status=error"
    return [r.message for r in caplog.records if needle in r.message]


def _run(monkeypatch, returncode):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(ep.subprocess, "run", fake_run)
    ep._execute_dedup_scripts()
    return calls


def test_every_detector_is_a_real_module_with_main(monkeypatch, caplog):
    calls = _run(monkeypatch, 0)
    assert len(calls) == 4
    for cmd, kwargs in calls:
        assert cmd[:2] == [sys.executable, "-m"]
        module = cmd[2]
        assert module.startswith("src.dedup."), module
        assert callable(importlib.import_module(module).main), module
        assert kwargs["cwd"] == "/app"
    assert not _observ(caplog, "dedup", "detector_failed")


def test_failed_detector_is_loud(monkeypatch, caplog):
    caplog.set_level(logging.ERROR)
    _run(monkeypatch, 1)
    lines = _observ(caplog, "dedup", "detector_failed")
    assert len(lines) == 4
    assert any("src.dedup.find_duplicate_people" in line for line in lines)


def test_reclassify_failure_is_loud(monkeypatch, caplog):
    caplog.set_level(logging.ERROR)

    def boom(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr("src.dedup.reclassify_military_units.reclassify", boom)
    ep._reclassify_military_units()
    assert _observ(caplog, "dedup", "reclassify_failed")


def test_index_cleanup_failure_is_loud(monkeypatch, caplog, tmp_path):
    caplog.set_level(logging.ERROR)
    (tmp_path / "output" / "people").mkdir(parents=True)
    monkeypatch.setattr(ep, "WORKDIR", tmp_path)

    def boom(*_a, **_k):
        raise RuntimeError("bad index")

    monkeypatch.setattr("src.dedup.cleanup_indexes.cleanup_index", boom)
    ep._cleanup_entity_indexes()
    assert _observ(caplog, "dedup", "index_cleanup_failed")


def test_detectors_resolve_repo_root_and_alias_table():
    """The moved detectors resolve output/ and config/ from the repo root, not src/dedup/.

    Pre-move they used ``script_dir.parent if script_dir.name == "scripts"``, which after the move
    would point at src/dedup/ (no output/ there -> exit 1) and the alias table at src/config/.
    """
    from pathlib import Path

    import src.dedup.find_duplicate_equipment as eq
    import src.dedup.find_duplicate_groups as gr
    import src.dedup.find_duplicate_people as pe
    import src.dedup.find_duplicate_places_v2 as pl

    repo_root = Path(__file__).resolve().parents[1]
    for mod in (pe, pl, gr, eq):
        assert mod.PROJECT_ROOT == repo_root, mod.__name__
    assert (repo_root / "config" / "equipment_aliases.yaml").exists()
    assert eq._EQUIPMENT_ALIASES, "alias table resolved but empty"
