"""Regression for the Phase-3-review residual (Phase-2 bibliography reconcile): _reconcile_bib_group
ignored the write_json_with_lock return and THEN unlinked all source shards — a blocked canonical
write = total silent loss. The fix must keep every shard when the canonical write is refused.
"""

import json
from pathlib import Path

import src.extraction.bibliography as B


def _shard(d: Path, name: str, bid: str) -> Path:
    f = d / name
    f.write_text(
        json.dumps({"BibliographyID": bid, "title": "V Corps Ops", "mentions": []})
    )
    return f


def test_blocked_canonical_write_keeps_all_shards(tmp_path, monkeypatch):
    s1 = _shard(tmp_path, "bib_aaa__w1.json", "01HX7YZABCDEFGHJKMNPQRSTVW")
    s2 = _shard(tmp_path, "bib_aaa__w2.json", "01HX7YZABCDEFGHJKMNPQRSTVX")
    items = [(s1, json.loads(s1.read_text())), (s2, json.loads(s2.read_text()))]

    # Force the schema guard to BLOCK the canonical write.
    monkeypatch.setattr(
        "src.utils.file_lock.write_json_with_lock", lambda *a, **k: False
    )
    n = B._reconcile_bib_group(tmp_path, "bib_aaa.json", items, {})
    assert n == 0  # nothing merged away
    assert s1.exists() and s2.exists()  # NO shard deleted -> no data loss
    assert not (tmp_path / "bib_aaa.json").exists()  # canonical not written (blocked)


def test_successful_write_merges_and_unlinks(tmp_path, monkeypatch):
    s1 = _shard(tmp_path, "bib_bbb__w1.json", "01HX7YZABCDEFGHJKMNPQRSTVW")
    s2 = _shard(tmp_path, "bib_bbb__w2.json", "01HX7YZABCDEFGHJKMNPQRSTVX")
    items = [(s1, json.loads(s1.read_text())), (s2, json.loads(s2.read_text()))]

    # Real-ish write: just persist, return True.
    def _ok(path, data, entity=None):
        Path(path).write_text(json.dumps(data))
        return True

    monkeypatch.setattr("src.utils.file_lock.write_json_with_lock", _ok)
    n = B._reconcile_bib_group(tmp_path, "bib_bbb.json", items, {})
    assert n == 2  # both non-canonical shards merged away (canonical written fresh)
    assert (tmp_path / "bib_bbb.json").exists()
