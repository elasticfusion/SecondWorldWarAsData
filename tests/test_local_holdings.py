"""Tests for local-holdings recognition (short-circuit resolution to held docs)."""

from src.enrichment import local_holdings as lh


class TestNormalize:
    def test_ms_hash_hyphen_forms(self):
        assert lh.normalize_source_id("MS #B-405") == "B405"
        assert lh.normalize_source_id("MS # B-405") == "B405"
        assert lh.normalize_source_id("MS #B-090") == "B090"  # zero-padded

    def test_bare_forms_with_and_without_hyphen(self):
        assert lh.normalize_source_id("B-405") == "B405"
        assert lh.normalize_source_id("B405") == "B405"  # filename stem form
        assert lh.normalize_source_id("21 AGp Dirs, M-502") == "M502"

    def test_no_id_returns_none(self):
        assert lh.normalize_source_id("random narrative prose") is None
        assert lh.normalize_source_id("") is None


class TestIndexAndFind:
    def test_build_index_marks_processed_vs_unprocessed(self, tmp_path):
        proc = tmp_path / "content"
        unproc = tmp_path / "staging"
        proc.mkdir()
        unproc.mkdir()
        (proc / "B405.pdf").write_text("x")
        (unproc / "A-105.pdf").write_text("x")
        idx = lh.build_holdings_index(processed_dirs=[proc], unprocessed_dirs=[unproc])
        assert idx["B405"] == (idx["B405"][0], "processed")
        assert idx["A105"][1] == "unprocessed"

    def test_processed_overrides_unprocessed(self, tmp_path):
        proc = tmp_path / "content"
        unproc = tmp_path / "staging"
        proc.mkdir()
        unproc.mkdir()
        (unproc / "B405.pdf").write_text("x")  # raw
        (proc / "B405.pdf").write_text("x")  # ingested
        idx = lh.build_holdings_index(processed_dirs=[proc], unprocessed_dirs=[unproc])
        assert idx["B405"][1] == "processed"  # processed wins

    def test_find_local_holding_returns_kind(self, tmp_path):
        proc = tmp_path / "content"
        proc.mkdir()
        (proc / "B405.pdf").write_text("x")
        idx = lh.build_holdings_index(processed_dirs=[proc], unprocessed_dirs=[])
        entry = {"citation": {"title": "MS # B-405"}}
        path, kind = lh.find_local_holding(entry, idx)
        assert path.endswith("B405.pdf") and kind == "processed"

    def test_find_local_holding_by_verbatim(self, tmp_path):
        unproc = tmp_path / "staging"
        unproc.mkdir()
        (unproc / "B405.pdf").write_text("x")
        idx = lh.build_holdings_index(processed_dirs=[], unprocessed_dirs=[unproc])
        entry = {"verbatim_reference": "see MS #B-405, p. 3"}
        hit = lh.find_local_holding(entry, idx)
        assert hit is not None and hit[1] == "unprocessed"

    def test_no_holding_returns_none(self, tmp_path):
        proc = tmp_path / "content"
        proc.mkdir()
        (proc / "B405.pdf").write_text("x")
        idx = lh.build_holdings_index(processed_dirs=[proc], unprocessed_dirs=[])
        entry = {"citation": {"title": "MS # B-999"}}  # not held
        assert lh.find_local_holding(entry, idx) is None
