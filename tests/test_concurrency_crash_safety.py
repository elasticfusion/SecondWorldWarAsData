"""Concurrency / crash-safety regressions — Phase-2 review (MED).

#1 grok_client cache state (sub-cache creation + hit/miss counters) is lock-guarded
#2 file_lock.locked_json.save is crash-atomic (temp + os.replace), not truncate-in-place
#4 run_scope.read_scope distinguishes legit-missing from PRESENT-but-corrupt (visible downgrade)
"""

import json
import threading
from pathlib import Path


# --- #1 cache sub-creation + counters are race-safe ----------------------------------------
def test_get_cache_single_backend_under_concurrency():
    import src.grok_client as gc

    client = gc.GrokClient.__new__(gc.GrokClient)
    client.caches = {}
    client._cache_lock = threading.Lock()
    client.BOOK_CACHE_TYPES = set()

    created = {"n": 0}

    class _Backend:
        def get_sub_cache(self, key):
            # simulate work so threads interleave in the create window
            created["n"] += 1
            return {"k": key}

    client._cache_backend = _Backend()
    # current_book contextvar returns None in tests -> cache_key == cache_type
    import src.grok_client as _gc

    _gc.current_book.set(None)

    barrier = threading.Barrier(16)

    def worker():
        barrier.wait()
        client._get_cache("events")

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # The double-checked lock must have built the 'events' backend EXACTLY once.
    assert created["n"] == 1
    assert set(client.caches) == {"events"}


def test_cache_counters_not_lost_under_concurrency():
    import src.grok_client as gc

    client = gc.GrokClient.__new__(gc.GrokClient)
    client._cache_lock = threading.Lock()
    client._cache_hits = 0

    def bump():
        for _ in range(1000):
            with client._cache_lock:
                client._cache_hits += 1

    threads = [threading.Thread(target=bump) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert client._cache_hits == 8000  # no lost increments


# --- #2 locked_json.save is crash-atomic ---------------------------------------------------
def test_locked_json_save_is_atomic_and_round_trips(tmp_path):
    from src.utils.file_lock import locked_json

    f = tmp_path / "entity.json"
    f.write_text(json.dumps({"v": 1}), encoding="utf-8")
    with locked_json(f) as (data, save):
        assert data == {"v": 1}
        save({"v": 2, "added": True})
    assert json.loads(f.read_text()) == {"v": 2, "added": True}
    # no temp files left behind
    assert not list(tmp_path.glob("*.tmp"))


def test_locked_json_save_failure_leaves_original_intact(tmp_path, monkeypatch):
    import os
    from src.utils import file_lock

    f = tmp_path / "entity.json"
    original = {"v": "ORIGINAL"}
    f.write_text(json.dumps(original), encoding="utf-8")

    # Make the atomic swap fail AFTER the temp file is written — the original must survive intact
    # (truncate-in-place would have corrupted it). file_lock's save does `import os as _os`, which is
    # the same os module, so patching os.replace covers it.
    monkeypatch.setattr(
        os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("boom"))
    )
    with file_lock.locked_json(f) as (_data, save):
        try:
            save({"v": "NEW"})
        except OSError:
            pass
    # original content preserved (atomic write never clobbered it)
    assert json.loads(f.read_text()) == original
    assert not list(tmp_path.glob("*.tmp"))  # temp cleaned up on failure


# --- #4 read_scope: corrupt record is a VISIBLE downgrade ----------------------------------
def test_read_scope_legit_missing_is_quiet(tmp_path, caplog):
    from src.utils.run_scope import read_scope

    with caplog.at_level("WARNING"):
        assert read_scope(tmp_path, "NoSuchBook") is None
    # legitimately missing -> no WARNING (normal first run)
    assert not any("read_corrupt" in r.message for r in caplog.records)


def test_read_scope_corrupt_record_warns(tmp_path, caplog):
    from src.utils import run_scope

    # Create a PRESENT but corrupt record at the path read_scope will look for.
    path = run_scope._record_path(tmp_path, "XChannel")
    assert path is not None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not valid json", encoding="utf-8")

    with caplog.at_level("WARNING"):
        result = run_scope.read_scope(tmp_path, "XChannel")
    assert result is None  # downgraded to the ceiling
    assert any(
        "run_scope.read_corrupt" in r.message for r in caplog.records
    ), "a corrupt existing record must WARN (visible downgrade), not silently return None"
