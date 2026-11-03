"""PR6: incremental dedup must not count aux files as 'new' (silent zero-comparison bug);
recorded_fetch must reject an unsafe endpoint host (SSRF defense-in-depth)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from src.dedup.incremental import get_new_files, should_compare


def test_aux_files_not_counted_as_new_records(tmp_path: Path):
    """Aux files (review_queue/coords/report) rewritten after `since` must NOT enter new_files —
    otherwise new_files is non-empty but holds 0 real records and should_compare silently returns
    False for every real pair (zero comparisons). A real record IS counted."""
    d = tmp_path / "people"
    d.mkdir()
    since = time.time() - 100
    # aux files 'modified now' (after since) — must be ignored
    for aux in [
        "review_queue.json",
        "coords.json",
        "duplicate_report.json",
        "surname_frequency_suggestions.json",
        "index.json",
        "not_related.json",
    ]:
        (d / aux).write_text("[]", encoding="utf-8")
    # one real record, also 'new'
    (d / "John_Smith_01HTEST.json").write_text(
        json.dumps({"PersonID": "01HTEST"}), "utf-8"
    )

    new = get_new_files(d, since)
    assert new == {"John_Smith_01HTEST.json"}, f"aux files leaked into new_files: {new}"
    # And with NO real record changed, new_files is empty -> full-mode compare (not silent-zero).
    # (Simulate: only aux changed -> empty set -> should_compare True.)
    assert should_compare("a.json", "b.json", set()) is True


def test_recorded_fetch_rejects_unsafe_host(monkeypatch):
    """recorded_fetch must refuse an endpoint resolving to a private/metadata host (SSRF DiD)."""
    from src.utils import fetch_provenance as fp

    # Stub the provenance persist so the test doesn't touch DynamoDB/disk.
    monkeypatch.setattr(fp, "record_fetch_event", lambda *a, **k: "evt")
    with pytest.raises(ValueError, match="unsafe endpoint host"):
        fp.recorded_fetch(
            "test", "http://169.254.169.254/latest/meta-data/", kind="test"
        )
