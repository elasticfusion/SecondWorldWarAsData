"""Guard: "82d Division" vs "82d Airborne Division" is a human-review candidate, not vetoed.

XChannel ch8 e2e: groups dedup found 0 groups, missing 82d/101st Division vs 82d/101st Airborne
Division — a bare division defaults to arm=infantry, which hard-vetoed vs airborne.
"""

import json

from src.dedup.unit_key import (
    defaulted_arm_review_reason,
    derive_unit_key,
    unit_keys_match,
)


def test_defaulted_vs_explicit_arm_is_review_candidate():
    for a, b in (
        ("82d Division", "82d Airborne Division"),
        ("101st Division", "101st Airborne Division"),
    ):
        assert defaulted_arm_review_reason(a, b)
        assert defaulted_arm_review_reason(b, a)


def test_real_vetoes_stay_vetoes():
    assert (
        defaulted_arm_review_reason("9th Infantry Division", "9th Armored Division")
        is None
    )
    assert defaulted_arm_review_reason("1st Division", "1st Marine Division") is None
    assert (
        defaulted_arm_review_reason("82d Division", "101st Airborne Division") is None
    )
    assert defaulted_arm_review_reason("9th Battalion", "9th Airborne Division") is None
    # strict linker unchanged
    assert not unit_keys_match(
        derive_unit_key("9th Division"), derive_unit_key("9th Armored")
    )[0]


def test_already_matching_pair_is_not_a_soft_candidate():
    assert (
        defaulted_arm_review_reason("29th Division", "29th Infantry Division") is None
    )


def _groups(tmp_path, monkeypatch, names):
    import scripts.find_duplicate_groups as fdg

    monkeypatch.setattr(
        "src.dedup.exclusions.load_reviewed_pairs", lambda *_a, **_k: set()
    )
    monkeypatch.setattr(fdg, "_load_group_exclusions", lambda d: (set(), set()))
    monkeypatch.setattr("src.dedup.incremental.get_last_dedup_run", lambda *_a: None)
    for f in tmp_path.glob("*.json"):
        f.unlink()
    for i, n in enumerate(names):
        (tmp_path / f"g{i}.json").write_text(
            json.dumps({"GroupID": f"G{i}", "name": n, "group_type": "military_unit"}),
            encoding="utf-8",
        )
    return fdg.find_duplicate_groups(tmp_path)


def test_find_duplicate_groups_surfaces_airborne_pairs(tmp_path, monkeypatch):
    names_in = [
        "82d Division",
        "82d Airborne Division",
        "9th Infantry Division",
        "9th Armored Division",
    ]
    groups = _groups(tmp_path, monkeypatch, names_in)
    names = [sorted(p["name"] for p in g["people"]) for g in groups]
    assert ["82d Airborne Division", "82d Division"] in names
    assert not any("9th Armored Division" in n for n in names)
    assert any("defaulted infantry" in r for g in groups for r in g["reasons"])


def test_soft_path_never_chains_explicit_vetoes(tmp_path, monkeypatch):
    import itertools

    for order in itertools.permutations(
        ["9th Division", "9th Infantry Division", "9th Armored Division"]
    ):
        for g in _groups(tmp_path, monkeypatch, list(order)):
            names = {p["name"] for p in g["people"]}
            assert not {"9th Infantry Division", "9th Armored Division"} <= names, order


def test_soft_candidates_are_never_auto_merged(tmp_path):
    import os

    os.environ.setdefault("S3_BUCKET", "test-bucket")
    import ecs_entrypoint as ee

    d = tmp_path / "people_groups"
    d.mkdir()
    recs = [{"name": "82d Division"}, {"name": "82d Airborne Division"}]
    assert not ee._group_cluster_canonically_mergeable(d, recs)
