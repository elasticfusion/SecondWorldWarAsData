"""people_groups auto-merge on canonical-key match (no veto) — groups, unlike people,
auto-merge once disambiguation is complete."""

import os

os.environ.setdefault("S3_BUCKET", "test-bucket")

from pathlib import Path

import ecs_entrypoint as E

G = Path("output/people_groups")


def test_group_variants_auto_mergeable():
    assert E._group_cluster_canonically_mergeable(
        G, ["9th Division", "Ninth Infantry Division", "9th Infantry Division"]
    )


def test_nickname_and_corps_auto_mergeable():
    assert E._group_cluster_canonically_mergeable(
        G, ["Screaming Eagles", "101st Airborne Division"]
    )
    assert E._group_cluster_canonically_mergeable(G, ["VII Corps", "7th Corps"])


def test_veto_blocks_auto_merge():
    assert not E._group_cluster_canonically_mergeable(
        G, ["9th Armored Division", "9th Infantry Division"]
    )


def test_underspecified_bare_cc_not_auto_merged():
    assert not E._group_cluster_canonically_mergeable(
        G, ["Combat Command B", "Combat Command B"]
    )


def test_people_never_canonically_auto_merged():
    # the deterministic key auto-merge is for people_groups ONLY; people stay human-gated
    assert not E._group_cluster_canonically_mergeable(
        Path("output/people"), ["9th Division", "9th Infantry Division"]
    )
