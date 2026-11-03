"""Step 7: find-or-create must be conflict-safe WITHOUT a queue.

Cross-phase feedback is the shared entity JSON: Phase 2 (and a re-run after Phase 3
mints a new place/org) "examines the JSON and creates it or adds to it". SQS is
unnecessary because the write conflict is managed: the find-vs-create decision is
serialised on the shared index.json under the existing cross-process locked_json
lock, with the authoritative index re-read INSIDE the lock and the new entry
persisted under the same lock before release.

Race closed: two overlapping writers (distinct empty in-memory snapshots) both try
to create the same new name; the second must FIND the first's locked entry rather
than mint a duplicate. Asserted in-process AND cross-process. The cross-process test
uses subprocess (true separate interpreters) rather than multiprocessing, so it
leaves zero in-process pool/semaphore state to leak into the rest of the suite.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch


def _real_entries(index_path: Path) -> dict:
    idx = json.loads(index_path.read_text())
    return {k: v for k, v in idx.items() if not k.startswith("_")}


# ---- in-process (fast, deterministic) -------------------------------------


def test_places_locked_find_or_create_converges_one_file(tmp_path: Path) -> None:
    places_dir = tmp_path / "places"
    places_dir.mkdir()
    name = "Sainte-Mere-Eglise"
    with (
        patch("src.extraction.enrich_places.enrich_place_from_grokipedia"),
        patch("src.extraction.enrich_places.enrich_place_from_wikipedia"),
    ):
        from src.extraction.places import _find_or_create_place

        pa = _find_or_create_place(name, {"current_name": name}, places_dir, {})
        pb = _find_or_create_place(name, {"current_name": name}, places_dir, {})
    place_files = [p for p in places_dir.glob("*.json") if p.name != "index.json"]
    assert len(place_files) == 1, f"duplicate files: {[p.name for p in place_files]}"
    assert pa.name == pb.name
    assert len(_real_entries(places_dir / "index.json")) == 1


def test_places_find_existing_fresh_snapshot(tmp_path: Path) -> None:
    places_dir = tmp_path / "places"
    places_dir.mkdir()
    name = "Carentan"
    with (
        patch("src.extraction.enrich_places.enrich_place_from_grokipedia"),
        patch("src.extraction.enrich_places.enrich_place_from_wikipedia"),
    ):
        from src.extraction.places import _find_or_create_place

        first = _find_or_create_place(name, {"current_name": name}, places_dir, {})
        second = _find_or_create_place(name, {"current_name": name}, places_dir, {})
    assert first.name == second.name
    assert len([p for p in places_dir.glob("*.json") if p.name != "index.json"]) == 1


def test_groups_find_or_create_converges_one_file(tmp_path: Path) -> None:
    from src.extraction.people_groups import _save_group

    groups_dir = tmp_path / "people_groups"
    groups_dir.mkdir()
    index_file = groups_dir / "index.json"
    name = "506th Parachute Infantry Regiment"
    _save_group(
        groups_dir,
        index_file,
        {"group_name": name, "event_mentions": []},
        "B",
        "A",
        "S",
    )
    _save_group(
        groups_dir,
        index_file,
        {"group_name": name, "event_mentions": []},
        "B",
        "A",
        "S",
    )
    files = [p for p in groups_dir.glob("*.json") if p.name != "index.json"]
    assert len(files) == 1, f"duplicate group files: {[p.name for p in files]}"


# ---- cross-process (real flock, via subprocess) ---------------------------

_REPO = Path(__file__).resolve().parents[1]

_PLACE_WORKER = textwrap.dedent("""
    import os, sys
    from pathlib import Path
    from unittest.mock import patch
    places_dir, barrier = Path(sys.argv[1]), Path(sys.argv[2])
    with patch("src.extraction.enrich_places.enrich_place_from_grokipedia"), \
         patch("src.extraction.enrich_places.enrich_place_from_wikipedia"):
        from src.extraction.places import _find_or_create_place
        (barrier / f"r_{os.getpid()}").write_text("1")
        while len(list(barrier.glob("r_*"))) < 4:
            pass
        _find_or_create_place("Isigny", {"current_name": "Isigny"}, places_dir, {})
    os._exit(0)
    """)

_GROUP_WORKER = textwrap.dedent("""
    import os, sys
    from pathlib import Path
    gdir, idx, barrier = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    from src.extraction.people_groups import _save_group
    (barrier / f"g_{os.getpid()}").write_text("1")
    while len(list(barrier.glob("g_*"))) < 4:
        pass
    _save_group(gdir, idx, {"group_name": "2nd Ranger Battalion", "event_mentions": []}, "B", "A", "S")
    os._exit(0)
    """)


def _run_workers(script: str, args: list, n: int = 4) -> None:
    procs = [
        subprocess.Popen([sys.executable, "-c", script, *args], cwd=str(_REPO))
        for _ in range(n)
    ]
    for p in procs:
        assert p.wait(timeout=90) == 0, "worker exited non-zero"


def test_places_find_or_create_cross_process(tmp_path: Path) -> None:
    places_dir = tmp_path / "places"
    places_dir.mkdir()
    barrier = tmp_path / "b"
    barrier.mkdir()
    _run_workers(_PLACE_WORKER, [str(places_dir), str(barrier)])
    place_files = [p for p in places_dir.glob("*.json") if p.name != "index.json"]
    assert (
        len(place_files) == 1
    ), f"cross-process duplicate: {[p.name for p in place_files]}"
    assert len(_real_entries(places_dir / "index.json")) == 1


def test_groups_find_or_create_cross_process(tmp_path: Path) -> None:
    groups_dir = tmp_path / "people_groups"
    groups_dir.mkdir()
    index_file = groups_dir / "index.json"
    barrier = tmp_path / "bg"
    barrier.mkdir()
    _run_workers(_GROUP_WORKER, [str(groups_dir), str(index_file), str(barrier)])
    files = [p for p in groups_dir.glob("*.json") if p.name != "index.json"]
    assert len(files) == 1, f"cross-process duplicate groups: {[p.name for p in files]}"
