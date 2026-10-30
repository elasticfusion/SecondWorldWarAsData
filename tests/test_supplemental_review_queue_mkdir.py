"""Supplemental review-queue write must create its parent dir — on a fresh run where bibliography
extraction produced 0 records, output/bibliography/ doesn't exist and the write used to crash with
FileNotFoundError, discarding all extracted materials (XChannel-8 overnight 2026-10-30).
"""

import json
from pathlib import Path

from src.extraction.supplemental import _append_to_review_queue


def test_review_queue_write_creates_missing_parent_dir(tmp_path):
    bib_dir = tmp_path / "output" / "bibliography"  # does NOT exist
    assert not bib_dir.exists()
    items = [{"title": "Ambiguous Source A"}, {"title": "Ambiguous Source B"}]
    _append_to_review_queue(bib_dir, items)  # must NOT raise FileNotFoundError
    qf = bib_dir / "review_queue.json"
    assert qf.exists()
    assert json.loads(qf.read_text()) == items


def test_review_queue_noop_on_empty_items(tmp_path):
    bib_dir = tmp_path / "bib"
    _append_to_review_queue(bib_dir, [])  # empty -> no write, no dir needed
    assert not (bib_dir / "review_queue.json").exists()
