"""Tests for bibliography resolver: citation guard, dedup, human-disposition queue.

Design intent (owner): grab what is legitimately online, and queue every real
citation we cannot grab for HUMAN disposition; skip narrative prose entirely.
"""

import json

from src.enrichment import bibliography_resolver as br


class TestLooksLikeCitation:
    def test_narrative_prose_is_not_citation(self):
        entry = {
            "verbatim_reference": "In October 1941, the Germans had discussed the plan."
        }
        assert br._looks_like_citation(entry) is False

    def test_short_prose_is_not_citation(self):
        assert (
            br._looks_like_citation({"verbatim_reference": "They advanced."}) is False
        )

    def test_structured_citation_is_citation(self):
        entry = {"citation": {"author": "Blumenson", "title": "Breakout and Pursuit"}}
        assert br._looks_like_citation(entry) is True

    def test_archive_ref_is_citation(self):
        assert br._looks_like_citation({"archive_reference_number": "RG 407, Box 3"})

    def test_archive_verbatim_is_citation(self):
        entry = {"verbatim_reference": "AAR, 7th Armd Div, RG 407, Box 12, pp. 3-5"}
        assert br._looks_like_citation(entry) is True

    def test_empty_is_not_citation(self):
        assert br._looks_like_citation({}) is False

    def test_placeholder_title_is_not_citation(self):
        # Stub entries with "Unknown"/empty placeholder fields are not real
        # citations and must not be sent to the resolver.
        for title in ("Unknown", "", "None", "N/A"):
            entry = {"citation": {"title": title, "author": []}}
            assert br._looks_like_citation(entry) is False, title

    def test_real_title_is_citation(self):
        entry = {"citation": {"title": "Operation MARKET-GARDEN, 17-26 Sep 44"}}
        assert br._looks_like_citation(entry) is True

    def test_has_real_value_helper(self):
        assert br._has_real_value("Unknown") is False
        assert br._has_real_value(None) is False
        assert br._has_real_value([]) is False
        assert br._has_real_value(["Unknown", ""]) is False
        assert br._has_real_value("MS #B-090") is True
        assert br._has_real_value(["", "Blumenson"]) is True


class TestResolveDir:
    def _run(self, tmp_path, files, monkeypatch):
        for name, obj in files.items():
            (tmp_path / name).write_text(json.dumps(obj))

        def fake_entry(entry, _grok=None, _config=None):
            if not br._looks_like_citation(entry):
                entry["search_status"] = "not_citation"
            else:
                entry["search_status"] = "not_found"
            return entry

        monkeypatch.setattr(br, "resolve_bibliography_entry", fake_entry)
        return br.resolve_bibliography_dir(tmp_path, None, {})

    def test_dedup_identical_citations(self, tmp_path, monkeypatch):
        files = {
            "a.json": {"citation": {"author": "X", "title": "Y"}},
            "b.json": {"citation": {"author": "X", "title": "Y"}},
        }
        stats = self._run(tmp_path, files, monkeypatch)
        assert stats["deduped"] == 1  # second identical citation reused

    def test_narrative_marked_not_citation_not_queued(self, tmp_path, monkeypatch):
        files = {"c.json": {"verbatim_reference": "In October 1941 they met."}}
        stats = self._run(tmp_path, files, monkeypatch)
        assert stats["not_citation"] == 1
        assert stats["queued"] == 0
        assert not (tmp_path / "review_queue.json").exists()

    def test_unresolvable_citation_queued_for_human(self, tmp_path, monkeypatch):
        files = {"e.json": {"verbatim_reference": "AAR, RG 407, Box 12, pp. 3-5"}}
        stats = self._run(tmp_path, files, monkeypatch)
        assert stats["queued"] == 1
        queue = json.loads((tmp_path / "review_queue.json").read_text())
        assert len(queue) == 1
        assert "human disposition" in queue[0]["reason"]

    def test_local_holding_short_circuits_online_search(self, tmp_path):
        # A cited source we already hold resolves to the local copy WITHOUT any
        # online resolver call (fake resolver would raise if reached).
        def boom(*a, **k):
            raise AssertionError("online resolver must NOT be called for a holding")

        import src.enrichment.bibliography_resolver as brm

        entry = {"citation": {"title": "MS # B-405"}}
        holdings = {"B405": "unprocesseddocs/B405.pdf"}
        original = brm._pick_resolver
        brm._pick_resolver = lambda *a, **k: boom
        try:
            brm.resolve_bibliography_entry(entry, None, {"holdings_index": holdings})
        finally:
            brm._pick_resolver = original
        assert entry["search_status"] == "resolved"
        assert entry["search_source"] == "local_holding"
        assert entry["local_source_path"].endswith("B405.pdf")
