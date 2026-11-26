"""The manual merge scripts store a pointer from every merged-away id to the survivor before they
delete its file, and write the survivor first (Group 3 step E follow-up, owner 2026-11-21).
"""

import json
import sys

import pytest

from src.utils import id_aliases as ia

A, B, C = (
    "01HX7YZABCDEFGHJKMNPQRSTVA",
    "01HX7YZABCDEFGHJKMNPQRSTVB",
    "01HX7YZABCDEFGHJKMNPQRSTVC",
)


@pytest.fixture
def no_pointers(monkeypatch):
    monkeypatch.setattr(ia, "record", lambda *a: False)


def _groups_dir(tmp_path):
    d = tmp_path / "people_groups"
    d.mkdir()
    for n, gid in ((1, A), (2, B)):
        (d / f"g{n}.json").write_text(
            json.dumps(
                {"GroupID": gid, "group_name": f"3rd AD {n}", "event_mentions": []}
            )
        )
    return d


def test_consolidate_groups_points_then_deletes(tmp_path):
    from scripts import consolidate_people_groups as cg

    d = _groups_dir(tmp_path)
    assert cg._merge_canonical_groups({"3rd AD": [d / "g1.json", d / "g2.json"]}) == 1
    assert not (d / "g2.json").exists()
    assert json.loads((d / "g1.json").read_text())["group_name"] == "3rd AD"
    assert ia.resolve("people_groups", B) == A


def test_consolidate_groups_keeps_a_file_whose_pointer_failed(tmp_path, no_pointers):
    from scripts import consolidate_people_groups as cg

    d = _groups_dir(tmp_path)
    cg._merge_canonical_groups({"3rd AD": [d / "g1.json", d / "g2.json"]})
    assert (d / "g2.json").exists()
    assert (
        json.loads((d / "g1.json").read_text())["group_name"] == "3rd AD"
    )  # survivor written


def _dates_dir(tmp_path):
    d = tmp_path / "dates"
    d.mkdir()
    for n, did in ((1, A), (2, B)):
        (d / f"d{n}.json").write_text(
            json.dumps(
                {
                    "DateID": did,
                    "date_start": "1944-06-06",
                    "date_end": "1944-06-06",
                    "event_mentions": [],
                }
            )
        )
    return d


def _run_dates(monkeypatch, tmp_path, d):
    from scripts import merge_dates as md

    monkeypatch.setattr(md, "DATES_DIR", d)
    monkeypatch.setattr(md, "update_cross_references", lambda *a: 0)
    monkeypatch.setattr(md, "_rebuild_index", lambda: None)
    monkeypatch.setattr(sys, "argv", ["merge_dates.py"])
    md.main()


def test_merge_dates_points_then_deletes(tmp_path, monkeypatch):
    d = _dates_dir(tmp_path)
    _run_dates(monkeypatch, tmp_path, d)
    assert sorted(p.name for p in d.glob("*.json")) == ["d1.json"]
    assert ia.resolve("dates", B) == A


def test_merge_dates_keeps_a_file_whose_pointer_failed(
    tmp_path, monkeypatch, no_pointers
):
    d = _dates_dir(tmp_path)
    _run_dates(monkeypatch, tmp_path, d)
    assert (d / "d2.json").exists()


def _equipment(tmp_path):
    d = tmp_path / "equipment"
    d.mkdir()
    for n, eid in ((1, A), (2, B), (3, C)):
        (d / f"e{n}.json").write_text(
            json.dumps({"EquipmentID": eid, "common_name": "M4", "mentions": []})
        )
    items = [{"filename": f"e{n}.json", "name": f"M4 {n}"} for n in (1, 2, 3)]
    return d, items


def test_merge_equipment_writes_the_survivor_first_then_points_and_deletes(
    tmp_path, monkeypatch
):
    from scripts import merge_equipment as me
    import src.dedup.merge as M

    monkeypatch.setattr(M, "update_event_refs", lambda *a: 0)
    d, items = _equipment(tmp_path)
    me._do_merge(d, items, 0)
    assert sorted(p.name for p in d.glob("e*.json")) == ["e1.json"]
    assert ia.resolve("equipment", B) == A and ia.resolve("equipment", C) == A


def test_merge_equipment_keeps_files_whose_pointer_failed(
    tmp_path, monkeypatch, no_pointers
):
    from scripts import merge_equipment as me
    import src.dedup.merge as M

    monkeypatch.setattr(M, "update_event_refs", lambda *a: 0)
    d, items = _equipment(tmp_path)
    me._do_merge(d, items, 0)
    assert sorted(p.name for p in d.glob("e*.json")) == [
        "e1.json",
        "e2.json",
        "e3.json",
    ]


@pytest.mark.parametrize(
    "script", ["merge_dates.py", "merge_equipment.py", "consolidate_people_groups.py"]
)
def test_each_script_imports_src_when_run_directly(script, tmp_path):
    """python3 scripts/<name>.py puts scripts/ on sys.path, not the repo root."""
    import os
    import subprocess
    from pathlib import Path

    path = Path("scripts", script).resolve()
    code = (
        "import runpy; g = runpy.run_path(%r, run_name='x'); "
        "from src.dedup.merge import store_merge_pointer; print('ok')" % str(path)
    )
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0 and out.stdout.strip() == "ok", out.stderr[-400:]


def test_a_kept_file_keeps_its_own_id_so_a_rerun_can_point_it(tmp_path, monkeypatch):
    """The real reference repointing runs: a file whose pointer failed is not rewritten to the
    survivor's id (that would make its pointer impossible to store later)."""
    from scripts import merge_equipment as me

    monkeypatch.chdir(tmp_path)
    d, items = _equipment(tmp_path)
    real = ia.record
    monkeypatch.setattr(
        ia, "record", lambda e, o, s, src: False if o == B else real(e, o, s, src)
    )
    me._do_merge(d, items, 0)
    assert json.loads((d / "e2.json").read_text())["EquipmentID"] == B  # kept, own id
    assert not (d / "e3.json").exists() and ia.resolve("equipment", C) == A
    monkeypatch.setattr(ia, "record", real)
    me._do_merge(d, [items[0], items[1]], 0)  # the re-run
    assert not (d / "e2.json").exists() and ia.resolve("equipment", B) == A


def test_a_valid_id_is_never_deleted_without_a_valid_survivor():
    from src.dedup import merge as M

    assert M.store_merge_pointer("dates", B, "") is False
    assert M.store_merge_pointer("dates", B, "not-a-ulid") is False
