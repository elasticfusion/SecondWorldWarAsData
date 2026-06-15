"""Tests for openserp_enrichment.py and search_query_loader.py."""

# pylint: disable=missing-function-docstring

from unittest.mock import Mock, patch

import yaml

from src.enrichment.openserp_enrichment import (
    _classify_source,
    _openserp_reachable,
    _verify_result,
)
from src.utils.search_query_loader import load_search_queries, render_search_queries


class TestClassifySource:
    def test_oral_history(self):
        assert (
            _classify_source("http://x.com", "Oral History of Gen X") == "oral_history"
        )

    def test_video(self):
        assert _classify_source("https://youtube.com/watch?v=x", "Title") == "video"

    def test_academic(self):
        assert _classify_source("https://mit.edu/paper.pdf", "Title") == "academic"

    def test_archive(self):
        assert _classify_source("https://museum.org/item", "Title") == "archive"

    def test_military_award(self):
        assert (
            _classify_source("https://valor.militarytimes.com/x", "Title")
            == "military_award"
        )

    def test_other(self):
        assert _classify_source("https://random.com", "Random") == "other"


class TestOpenSerpReachable:
    @patch("src.enrichment.openserp_enrichment.get_session")
    def test_reachable(self, mock_session):
        mock_session.return_value.get.return_value = Mock(status_code=200)
        assert _openserp_reachable("http://localhost:7001") is True

    @patch("src.enrichment.openserp_enrichment.get_session")
    def test_unreachable(self, mock_session):
        mock_session.return_value.get.side_effect = Exception("refused")
        assert _openserp_reachable("http://localhost:7001") is False


class TestVerifyResult:
    def test_no_grok_client_returns_true(self):
        assert _verify_result("any title", "any context", None) is True

    @patch("src.utils.search_cache.get_cached", return_value="YES")
    def test_cached_yes(self, _):
        assert _verify_result("title", "context", Mock()) is True

    @patch("src.utils.search_cache.get_cached", return_value="NO")
    def test_cached_no(self, _):
        assert _verify_result("title", "context", Mock()) is False


class TestSearchQueryLoader:
    def test_load_and_render(self, tmp_path, monkeypatch):
        import src.utils.search_query_loader as sql

        monkeypatch.setattr(sql, "SEARCH_QUERIES_DIR", tmp_path)
        sql.load_search_queries.cache_clear()

        data = {
            "portrait_images": ['"{name}" WWII portrait', '"{name}" photo military']
        }
        (tmp_path / "people.yaml").write_text(yaml.dump(data), encoding="utf-8")

        result = render_search_queries("people", "portrait_images", name="Patton")
        assert len(result) == 2
        assert '"Patton" WWII portrait' in result
        assert '"Patton" photo military' in result

    def test_missing_file_raises(self, tmp_path, monkeypatch):
        import src.utils.search_query_loader as sql

        monkeypatch.setattr(sql, "SEARCH_QUERIES_DIR", tmp_path)
        sql.load_search_queries.cache_clear()

        try:
            load_search_queries("nonexistent")
            assert False, "Should have raised"
        except FileNotFoundError:
            pass

    def test_missing_category_returns_empty(self, tmp_path, monkeypatch):
        import src.utils.search_query_loader as sql

        monkeypatch.setattr(sql, "SEARCH_QUERIES_DIR", tmp_path)
        sql.load_search_queries.cache_clear()

        (tmp_path / "test.yaml").write_text(
            yaml.dump({"other": ["q"]}), encoding="utf-8"
        )
        result = render_search_queries("test", "nonexistent")
        assert result == []
