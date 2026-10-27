"""_retry_missing_events must NO-OP in batch (submit-only) mode.

During batch collection every chapter legitimately lacks an event file until submit_batch + the
re-run; retrying there only re-collides request_ids (OBSERV batch.duplicate_request_id_dropped) and
emits a false '✗ Retry failed' ERROR. The live (non-batch) path must still retry.
"""

import json
import types
from pathlib import Path

import phase2_extract


def _make_parsed(tmp_path: Path) -> Path:
    """A parsed file whose -event.json is MISSING (so _retry_missing_events would act)."""
    pf = tmp_path / "chapter1a-parsed.json"
    pf.write_text(json.dumps({"paragraphs": ["p"] * 50}), encoding="utf-8")
    return pf


def test_retry_skipped_in_batch_mode(tmp_path, monkeypatch):
    pf = _make_parsed(tmp_path)
    calls = {"extract": 0}

    def _spy_extract(*a, **k):
        calls["extract"] += 1
        return None

    # If the guard fails, _retry_missing_events would import+call extract_events.
    monkeypatch.setattr(
        "src.extraction.events.extract_events", _spy_extract, raising=False
    )
    monkeypatch.setattr(phase2_extract, "extract_events", _spy_extract, raising=False)

    grok = types.SimpleNamespace(batch_mode=True)
    retried, failed = phase2_extract._retry_missing_events([pf], grok, _Logger())

    assert (retried, failed) == (0, 0)
    assert calls["extract"] == 0  # NO re-extraction attempted in batch mode


def test_retry_runs_in_live_mode(tmp_path, monkeypatch):
    pf = _make_parsed(tmp_path)
    seen = {"cleared": 0, "extracted": 0}

    # Stub the live retry internals so the test is hermetic but proves the path RUNS.
    monkeypatch.setattr(
        "src.extraction.events.create_event_prompt",
        lambda data: "PROMPT",
        raising=False,
    )

    def _extract(parsed_file, grok_client, output_dir):
        seen["extracted"] += 1
        out = Path(output_dir) / parsed_file.name.replace("-parsed.json", "-event.json")
        out.write_text(json.dumps({"Sub-events": [{"x": 1}]}), encoding="utf-8")
        return out

    # extract_events is a module-level binding in phase2_extract.
    monkeypatch.setattr(phase2_extract, "extract_events", _extract, raising=False)

    grok = types.SimpleNamespace(
        batch_mode=False,
        clear_cache_entry=lambda prompt, cache_type: seen.__setitem__(
            "cleared", seen["cleared"] + 1
        ),
    )
    retried, failed = phase2_extract._retry_missing_events([pf], grok, _Logger())

    assert seen["extracted"] == 1 and retried == 1 and failed == 0


class _Logger:
    def warning(self, *a, **k):
        pass

    def info(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass

    def debug(self, *a, **k):
        pass
