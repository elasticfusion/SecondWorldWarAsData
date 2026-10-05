"""people_groups auto-merge on canonical-key match (no veto, no nationality conflict).
Groups, unlike people, auto-merge once disambiguation is complete."""

import os

os.environ.setdefault("S3_BUCKET", "test-bucket")

from pathlib import Path

import ecs_entrypoint as E

G = Path("output/people_groups")


def _r(name, nationality=None):
    d = {"name": name}
    if nationality:
        d["nationality"] = nationality
    return d


def test_group_variants_auto_mergeable():
    assert E._group_cluster_canonically_mergeable(
        G,
        [
            _r("9th Division"),
            _r("Ninth Infantry Division"),
            _r("9th Infantry Division"),
        ],
    )


def test_nickname_and_corps_auto_mergeable():
    assert E._group_cluster_canonically_mergeable(
        G, [_r("Screaming Eagles"), _r("101st Airborne Division")]
    )
    assert E._group_cluster_canonically_mergeable(G, [_r("VII Corps"), _r("7th Corps")])


def test_arm_veto_blocks_auto_merge():
    assert not E._group_cluster_canonically_mergeable(
        G, [_r("9th Armored Division"), _r("9th Infantry Division")]
    )


def test_nationality_veto_blocks_auto_merge():
    # same number+arm+echelon but different stored nationality -> NOT auto-merged
    assert not E._group_cluster_canonically_mergeable(
        G, [_r("2nd Armored Division", "USA"), _r("2nd Armored Division", "Germany")]
    )


def test_underspecified_bare_cc_not_auto_merged():
    assert not E._group_cluster_canonically_mergeable(
        G, [_r("Combat Command B"), _r("Combat Command B")]
    )


def test_people_never_canonically_auto_merged():
    assert not E._group_cluster_canonically_mergeable(
        Path("output/people"), [_r("9th Division"), _r("9th Infantry Division")]
    )
