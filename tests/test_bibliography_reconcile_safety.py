"""The bibliography merge never stops on one bad record, and merges exact matches only.

Owner 2026-11-25 (D1/D2/D3). Before: valid JSON of the wrong shape (a string `mentions`, a numeric
`title`, a string `citation`) made the WHOLE full-corpus merge raise, so no barrier was published and
Phase 3 ran on unmerged shards; an unreadable file was skipped silently; near-identical titles were
merged automatically (in the 2026-11-17 snapshot, "Seventh Army KTB" with its annexes "... KTB
Anlagen").
"""

import json
import logging

import pytest

from src.extraction import bibliography as B

H = "bib_0123456789abcdef"
G = "bib_fedcba9876543210"


def _rec(bid, title, **extra):
    return {
        "BibliographyID": bid,
        "title": title,
        "_schema_version": "2.29",  # the snapshot's version: brought forward on the fly
        "mentions": [
            {
                "MentionID": "01M6J3NGB3DR9QD7VT6ST9Y8SH",
                "book": "Book",
                "chapter": "ch",
                "reference_number": "1",
                "reference_type": "footnote",
                "verbatim_reference": title,
            }
        ],
        **extra,
    }


def _write(d, name, data):
    (d / name).write_text(data if isinstance(data, str) else json.dumps(data))


def _left(d):
    return sorted(p.name for p in d.glob("*.json") if p.name not in ("index.json",))


@pytest.fixture
def bib(tmp_path):
    d = tmp_path / "output" / "bibliography"
    d.mkdir(parents=True)
    return d


@pytest.mark.parametrize(
    "field,value",
    [
        ("mentions", "x"),
        ("mentions", ["x"]),
        ("title", 5),
        ("title", ["a"]),
        ("citation", "x"),
    ],
)
def test_a_wrong_shape_is_held_and_the_rest_merges(bib, caplog, field, value):
    _write(
        bib,
        f"{H}__w0.json",
        _rec("01M00000000000000000000001", "Utah Beach to Cherbourg"),
    )
    _write(
        bib,
        f"{H}__w1.json",
        _rec("01M00000000000000000000002", "Utah Beach to Cherbourg"),
    )
    bad = _rec("01M00000000000000000000003", "Omaha Beachhead")
    bad[field] = value
    _write(bib, f"{G}__w0.json", bad)
    held: list = []
    with caplog.at_level(logging.WARNING):
        merged = B.reconcile_bibliography_dir(bib, held=held)
    assert merged == 2  # the good shards still became one canonical record
    assert held == [f"{G}__w0.json"]
    assert f"{H}.json" in _left(bib) and f"{G}__w0.json" in _left(
        bib
    )  # bad left unchanged
    assert json.loads((bib / f"{G}__w0.json").read_text())[field] == value
    assert "OBSERV schema.not_usable entity=bibliography" in caplog.text


def test_an_unreadable_file_is_said_and_held(bib, caplog):
    _write(
        bib,
        f"{H}__w0.json",
        _rec("01M00000000000000000000001", "Utah Beach to Cherbourg"),
    )
    _write(bib, f"{G}__w0.json", '{"BibliographyID": "01M0000')  # truncated
    held: list = []
    with caplog.at_level(logging.WARNING):
        B.reconcile_bibliography_dir(bib, held=held)
    assert held == [f"{G}__w0.json"]
    assert (
        "bib.record_unreadable" in caplog.text
        and "Restore its previous version" in caplog.text
    )


def test_a_citation_that_fails_to_merge_does_not_stop_the_others(
    bib, monkeypatch, caplog
):
    _write(
        bib,
        f"{H}__w0.json",
        _rec("01M00000000000000000000001", "Utah Beach to Cherbourg"),
    )
    _write(
        bib,
        f"{H}__w1.json",
        _rec("01M00000000000000000000002", "Utah Beach to Cherbourg"),
    )
    _write(bib, f"{G}__w0.json", _rec("01M00000000000000000000003", "Omaha Beachhead"))
    _write(bib, f"{G}__w1.json", _rec("01M00000000000000000000004", "Omaha Beachhead"))
    real = B._reconcile_bib_group

    def flaky(bib_dir, canon, items, index, removed):
        if canon == f"{G}.json":
            raise RuntimeError("boom")
        return real(bib_dir, canon, items, index, removed)

    monkeypatch.setattr(B, "_reconcile_bib_group", flaky)
    with caplog.at_level(logging.WARNING):
        merged = B.reconcile_bibliography_dir(bib)
    assert merged == 2
    assert {f"{H}.json", f"{G}__w0.json", f"{G}__w1.json"} <= set(_left(bib))
    assert "bib.group_merge_failed" in caplog.text


def test_similar_titles_are_listed_not_merged(bib):
    # the snapshot's pair (2026-11-17): the war diary and its annexes, different documents
    _write(
        bib,
        f"{H}.json",
        _rec("01M00000000000000000000001", "Seventh Army, KTB 1.I.–30.VI.44"),
    )
    _write(
        bib,
        f"{G}.json",
        _rec("01M00000000000000000000002", "Seventh Army, KTB Anlagen 1.I.–30.VI.44"),
    )
    assert B.reconcile_bibliography_dir(bib) == 0
    assert {f"{H}.json", f"{G}.json"} <= set(_left(bib))
    review = json.loads(B.merge_review_path(bib).read_text())
    assert len(review["pairs"]) == 1 and review["pairs"][0]["rule"] == "near_title"


def test_the_review_file_is_outside_the_entity_dir(bib):
    """Nothing that lists output/bibliography/ can take the review list for a record."""
    B.reconcile_bibliography_dir(bib)
    assert B.merge_review_path(bib).exists()
    assert B.merge_review_path(bib).parent != bib
    assert not list(bib.glob("*review*"))


def test_identical_normalized_titles_still_merge(bib):
    """Exact by the existing rule (case, punctuation): a record written under an older file name for
    the same citation is still combined (TODO D3 rule question (a))."""
    _write(
        bib, f"{H}.json", _rec("01M00000000000000000000001", "Utah Beach to Cherbourg")
    )
    _write(
        bib, f"{G}.json", _rec("01M00000000000000000000002", "Utah Beach to Cherbourg.")
    )
    assert B.reconcile_bibliography_dir(bib) == 1
    assert len(_left(bib)) == 1


def test_a_brought_forward_record_is_written_at_the_current_version(bib):
    from src.schemas import entity_version

    _write(
        bib,
        f"{H}__w0.json",
        _rec("01M00000000000000000000001", "Utah Beach to Cherbourg"),
    )
    _write(
        bib,
        f"{H}__w1.json",
        _rec("01M00000000000000000000002", "Utah Beach to Cherbourg"),
    )
    B.reconcile_bibliography_dir(bib)
    rec = json.loads((bib / f"{H}.json").read_text())
    assert rec["_schema_version"] == entity_version("bibliography")
    assert len(rec["mentions"]) == 1  # the same mention, not doubled
