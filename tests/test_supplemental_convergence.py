"""Regression test: supplemental extraction must honor the processed-events
registry (skip already-done endnotes + mark after running).

Bug: `_extract_supplemental` was the only optional extractor missing the
`_is_processed` skip guard AND the `_mark_processed` call. On a `--retrieve-only`
re-run it re-fetched EVERY endnote (slow per-endnote HTTP to ibiblio) from
scratch and never recorded completion → non-convergent (the run that forced us to
temporarily disable supplemental to prove the Phase-3 lifecycle).
"""

import phase2_extract as p2


def _cfg(enabled=True):
    return {
        "supplemental_material": {"enabled": enabled, "enrich_with_searches": False}
    }


class _Ev:
    name = "chapter1c05-event.json"


def test_supplemental_skips_when_already_processed(tmp_path, monkeypatch):
    """If the event is already in the processed registry, extract_supplemental is
    NOT called again (prevents the retrieve re-fetch loop)."""
    output_root = tmp_path
    p2._mark_processed(output_root, "supplemental", _Ev.name)  # pre-mark

    called = {"n": 0}

    def fake_extract(**_kw):
        called["n"] += 1
        return None

    monkeypatch.setattr(
        "src.extraction.supplemental.extract_supplemental", fake_extract, raising=False
    )
    # Hermetic: the skip guard is `not should_reprocess(...) and _is_processed(...)`.
    # Force should_reprocess False so this tests the code guard, not config.yaml.
    monkeypatch.setattr(p2, "should_reprocess", lambda _t: False, raising=False)
    monkeypatch.setattr(
        "src.utils.config.should_reprocess", lambda _t: False, raising=False
    )
    import logging

    p2._extract_supplemental(
        _Ev(), object(), output_root, _cfg(), logging.getLogger("t")
    )
    assert called["n"] == 0, "should skip already-processed endnote"


def test_supplemental_marks_after_running(tmp_path, monkeypatch):
    """A fresh event is processed AND then marked, so the next run skips it."""
    output_root = tmp_path

    monkeypatch.setattr(
        "src.extraction.supplemental.extract_supplemental",
        lambda **_kw: {"ok": True},
        raising=False,
    )
    import logging

    p2._extract_supplemental(
        _Ev(), object(), output_root, _cfg(), logging.getLogger("t")
    )
    assert p2._is_processed(output_root, "supplemental", _Ev.name), "must mark done"


def test_supplemental_disabled_is_noop(tmp_path):
    import logging

    # enabled=False → returns immediately, no marker written
    p2._extract_supplemental(
        _Ev(), object(), tmp_path, _cfg(enabled=False), logging.getLogger("t")
    )
    assert not p2._is_processed(tmp_path, "supplemental", _Ev.name)
