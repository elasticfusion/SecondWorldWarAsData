"""§1.0d HIGH regression: locked_json must provide CROSS-PROCESS mutual exclusion across a
read-modify-write even though save() commits a NEW inode via os.replace. The flock is held on a
STABLE sidecar lockfile (<path>.lock) that is never renamed, so the lock identity survives the
replace. (The in-process thread_lock alone would hide this — these tests use real PROCESSES.)

Also: the create path must NOT truncate a concurrently-created file before the lock is held.
"""

import json
import multiprocessing as mp
import os
import sys

import pytest

# These tests require POSIX fcntl flock (the cross-process mechanism under test).
pytestmark = pytest.mark.skipif(
    sys.platform not in ("linux", "darwin") and os.name != "posix",
    reason="locked_json cross-process lock uses fcntl flock (POSIX only)",
)


def _append_in_process(path_str, tag, barrier_name=None):
    """Worker run in a SEPARATE process: read-modify-write one mention onto the entity file.
    Uses a filesystem barrier (spin until the sibling's start-marker exists) to force the two
    processes to contend on the lock at the same time."""
    from pathlib import Path
    import time

    from src.utils.file_lock import locked_json

    # Announce readiness + spin briefly so both workers reach locked_json near-simultaneously.
    Path(f"{path_str}.start.{tag}").write_text("1")
    if barrier_name:
        deadline = time.time() + 5
        while time.time() < deadline and not Path(barrier_name).exists():
            time.sleep(0.001)

    with locked_json(Path(path_str)) as (data, save):
        mentions = data.get("event_mentions", [])
        # Small sleep INSIDE the critical section widens the race window: if the lock is not
        # truly held cross-process, the other process reads the same pre-state and one append
        # is lost.
        time.sleep(0.05)
        mentions.append({"MentionID": f"M_{tag}", "Sub_eventID": f"SE_{tag}"})
        data["event_mentions"] = mentions
        save(data)


def test_cross_process_lock_survives_atomic_replace(tmp_path):
    """Two processes each append one mention; BOTH must survive (no lost update), proving the
    sidecar flock serializes the read-modify-write across the os.replace inode swap."""
    f = tmp_path / "entity.json"
    f.write_text(json.dumps({"PlaceID": "01TEST", "name": "X", "event_mentions": []}))
    barrier = str(tmp_path / "go")

    ctx = mp.get_context("spawn")
    p1 = ctx.Process(target=_append_in_process, args=(str(f), "a", barrier))
    p2 = ctx.Process(target=_append_in_process, args=(str(f), "b", barrier))
    p1.start()
    p2.start()
    # Release both once they're up (best-effort barrier).
    import time

    time.sleep(0.2)
    (tmp_path / "go").write_text("1")
    p1.join(30)
    p2.join(30)
    assert p1.exitcode == 0 and p2.exitcode == 0

    result = json.loads(f.read_text())
    ids = sorted(m["MentionID"] for m in result["event_mentions"])
    assert ids == ["M_a", "M_b"], f"lost-update across processes: {ids}"


def _create_in_process(path_str, tag, barrier_name):
    from pathlib import Path
    import time

    from src.utils.file_lock import locked_json

    Path(f"{path_str}.start.{tag}").write_text("1")
    deadline = time.time() + 5
    while time.time() < deadline and not Path(barrier_name).exists():
        time.sleep(0.001)
    with locked_json(Path(path_str)) as (data, save):
        time.sleep(0.05)
        keys = data.get("keys", [])
        keys.append(tag)
        data["keys"] = keys
        save(data)


def test_create_path_does_not_truncate_concurrent_write(tmp_path):
    """Two processes create the SAME not-yet-existing file concurrently. The old 'w+' create
    opened+truncated BEFORE the lock, so one process could zero the other's just-written data.
    With the sidecar lock + non-truncating read, both contributions survive. Uses a NON-entity
    bookkeeping path (not an entity dir) so the central write-guard doesn't reject the synthetic
    shape — the property under test is the lock/truncation ordering, not schema validation.
    """
    f = tmp_path / "bookkeeping" / "pending.json"
    f.parent.mkdir(parents=True)
    barrier = str(tmp_path / "go")

    ctx = mp.get_context("spawn")
    p1 = ctx.Process(target=_create_in_process, args=(str(f), "a", barrier))
    p2 = ctx.Process(target=_create_in_process, args=(str(f), "b", barrier))
    p1.start()
    p2.start()
    import time

    time.sleep(0.2)
    (tmp_path / "go").write_text("1")
    p1.join(30)
    p2.join(30)
    assert p1.exitcode == 0 and p2.exitcode == 0

    result = json.loads(f.read_text())
    assert sorted(result.get("keys", [])) == [
        "a",
        "b",
    ], f"create-path truncation lost a concurrent write: {result.get('keys')}"
