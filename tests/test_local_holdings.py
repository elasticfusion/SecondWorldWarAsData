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
    def test_build_index_from_source_dir(self, tmp_path):
        (tmp_path / "B405.pdf").write_text("x")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "A-105.pdf").write_text("x")
        (tmp_path / "notes.txt").write_text("x")  # no id -> not indexed
        idx = lh.build_holdings_index([tmp_path])
        assert idx["B405"].endswith("B405.pdf")
        assert idx["A105"].endswith("A-105.pdf")
        assert "notes" not in idx

    def test_find_local_holding_by_title(self, tmp_path):
        (tmp_path / "B405.pdf").write_text("x")
        idx = lh.build_holdings_index([tmp_path])
        entry = {"citation": {"title": "MS # B-405"}}
        assert lh.find_local_holding(entry, idx).endswith("B405.pdf")

    def test_find_local_holding_by_verbatim(self, tmp_path):
        (tmp_path / "B405.pdf").write_text("x")
        idx = lh.build_holdings_index([tmp_path])
        entry = {"verbatim_reference": "see MS #B-405, p. 3"}
        assert lh.find_local_holding(entry, idx) is not None

    def test_no_holding_returns_none(self, tmp_path):
        (tmp_path / "B405.pdf").write_text("x")
        idx = lh.build_holdings_index([tmp_path])
        entry = {"citation": {"title": "MS # B-999"}}  # not held
        assert lh.find_local_holding(entry, idx) is None
