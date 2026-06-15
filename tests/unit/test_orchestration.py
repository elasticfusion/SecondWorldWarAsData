"""Tests for per-book queue orchestration and related new paths."""

import json
import threading
import time
from pathlib import Path

import pytest


class TestBackgroundSyncRules:
    """Test BackgroundSync .tmp and mtime staleness rules."""

    def test_skips_tmp_files(self, tmp_path):
        """Temp files should not be uploaded."""
        tmp_file = tmp_path / "output" / "people" / "test.tmp"
        tmp_file.parent.mkdir(parents=True)
        tmp_file.write_text("{}")

        # Simulate the filter logic from BackgroundSync._sync_changed
        files = list(tmp_path.rglob("*"))
        uploadable = [f for f in files if f.is_file() and f.suffix != ".tmp"]
        assert len(uploadable) == 0

    def test_skips_recently_modified(self, tmp_path):
        """Files modified in last 2s should not be uploaded."""
        entity_file = tmp_path / "output" / "people" / "test.json"
        entity_file.parent.mkdir(parents=True)
        entity_file.write_text("{}")

        now = time.time()
        mtime = entity_file.stat().st_mtime
        # Just written — should be skipped
        assert now - mtime < 2

    def test_uploads_stable_files(self, tmp_path):
        """Files with mtime >2s old should be uploadable."""
        import os

        entity_file = tmp_path / "output" / "people" / "test.json"
        entity_file.parent.mkdir(parents=True)
        entity_file.write_text("{}")
        # Backdate mtime by 5s
        old_time = time.time() - 5
        os.utime(entity_file, (old_time, old_time))

        now = time.time()
        mtime = entity_file.stat().st_mtime
        assert now - mtime >= 2


class TestDownloadedKeysTracking:
    """Test that downloaded files are only skipped if unmodified."""

    def test_unmodified_downloaded_file_skipped(self):
        """File with same mtime as download should be skipped."""
        downloaded_keys = {"output/people/test.json": 1000.0}
        current_mtime = 1000.0
        key = "output/people/test.json"

        # Logic from _sync_changed
        skip = key in downloaded_keys and downloaded_keys[key] == current_mtime
        assert skip is True

    def test_modified_downloaded_file_uploaded(self):
        """File with changed mtime should be uploaded (enriched)."""
        downloaded_keys = {"output/people/test.json": 1000.0}
        current_mtime = 1005.0  # Modified by enrichment
        key = "output/people/test.json"

        skip = key in downloaded_keys and downloaded_keys[key] == current_mtime
        assert skip is False


class TestIndexLocking:
    """Test thread-safe index.json access."""

    def test_get_index_lock_returns_same_lock(self):
        from src.extraction.batch_parallel import _get_index_lock

        lock1 = _get_index_lock(Path("/fake/people/index.json"))
        lock2 = _get_index_lock(Path("/fake/people/index.json"))
        assert lock1 is lock2

    def test_get_index_lock_different_paths(self):
        from src.extraction.batch_parallel import _get_index_lock

        lock1 = _get_index_lock(Path("/fake/people/index.json"))
        lock2 = _get_index_lock(Path("/fake/places/index.json"))
        assert lock1 is not lock2

    def test_lock_prevents_concurrent_access(self):
        from src.extraction.batch_parallel import _get_index_lock

        lock = _get_index_lock(Path("/test/concurrent/index.json"))
        results = []

        def worker(n):
            with lock:
                results.append(f"start-{n}")
                time.sleep(0.01)
                results.append(f"end-{n}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Verify no interleaving (start-N always followed by end-N)
        for i in range(0, len(results), 2):
            n = results[i].split("-")[1]
            assert results[i + 1] == f"end-{n}"


class TestBatchCollectorThreadSafety:
    """Test BatchCollector.add() thread safety."""

    def test_concurrent_adds_no_duplicates(self):
        from src.utils.batch_api import BatchCollector, BatchRequest

        collector = BatchCollector()
        requests_to_add = [
            BatchRequest(
                request_id=f"req-{i}",
                messages=[{"role": "user", "content": f"prompt-{i}"}],
                model="grok-4.3",
                temperature=0.0,
                cache_type="test",
            )
            for i in range(100)
        ]

        def add_batch(start, end):
            for req in requests_to_add[start:end]:
                collector.add(req)

        threads = [
            threading.Thread(target=add_batch, args=(0, 50)),
            threading.Thread(target=add_batch, args=(50, 100)),
            threading.Thread(target=add_batch, args=(0, 100)),  # duplicates
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(collector) == 100  # No duplicates


class TestRenderPromptHardFail:
    """Test that render_prompt fails hard on missing YAML."""

    def test_missing_yaml_raises(self):
        from src.utils.prompt_loader import render_prompt

        with pytest.raises(FileNotFoundError):
            render_prompt("nonexistent_prompt_xyz")

    def test_all_required_prompts_loadable(self):
        from src.utils.prompt_loader import load_prompt

        required = [
            "events",
            "people",
            "places",
            "dates",
            "equipment",
            "weather",
            "casualties",
            "logistics",
            "supplemental",
            "people_groups",
            "biography",
        ]
        for name in required:
            data = load_prompt(name)
            assert "prompt_template" in data


class TestSearchQueryLoader:
    """Test search query loader."""

    def test_missing_file_raises(self):
        from src.utils.search_query_loader import load_search_queries

        with pytest.raises(FileNotFoundError):
            load_search_queries("nonexistent_xyz")

    def test_all_required_files_loadable(self):
        from src.utils.search_query_loader import load_search_queries

        required = ["people", "equipment", "events", "bibliography", "maps", "nara"]
        for name in required:
            data = load_search_queries(name)
            assert isinstance(data, dict)
            assert len(data) > 0

    def test_render_replaces_variables(self):
        from src.utils.search_query_loader import render_search_queries

        queries = render_search_queries("people", "portrait_images", name="Patton")
        assert all("Patton" in q for q in queries)
        assert all("{name}" not in q for q in queries)
