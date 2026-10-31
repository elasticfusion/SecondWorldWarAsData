"""BATCH_PHASE2 env overrides the --batch CLI flag (the task-def hardcodes --batch; this is the
only way to force synchronous extraction without editing the command)."""

import logging
from phase2_extract import _resolve_batch_mode

log = logging.getLogger("t")


def test_env_false_forces_sync(monkeypatch):
    for v in ("false", "0", "no", "off", "FALSE", "Off"):
        monkeypatch.setenv("BATCH_PHASE2", v)
        assert _resolve_batch_mode(True, log) is False  # overrides --batch


def test_env_true_forces_batch(monkeypatch):
    for v in ("true", "1", "yes", "on"):
        monkeypatch.setenv("BATCH_PHASE2", v)
        assert _resolve_batch_mode(False, log) is True


def test_unset_leaves_cli(monkeypatch):
    monkeypatch.delenv("BATCH_PHASE2", raising=False)
    assert _resolve_batch_mode(True, log) is True
    assert _resolve_batch_mode(False, log) is False


def test_garbage_leaves_cli(monkeypatch):
    monkeypatch.setenv("BATCH_PHASE2", "maybe")
    assert _resolve_batch_mode(True, log) is True
