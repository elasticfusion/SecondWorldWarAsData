"""Records that cite the same footnote text are combined into one, keeping provenance for both
(owner 2026-11-21). Found in production: Phase 2 writes one footnote as several records (the
deterministic pass titles it "Unknown", the Grok pass gives it a real title), 348 such groups.
"""

import json
import os
from unittest.mock import MagicMock

os.environ.setdefault("S3_BUCKET", "test-bucket")

import jsonschema

from src.extraction import bibliography as b
from src.extraction.bibliography import reconcile_bibliography_dir
from src.schemas.bibliography_output import BIBLIOGRAPHY_OUTPUT_SCHEMA

A, B, C = (
    "01HX7YZABCDEFGHJKMNPQRSTVA",
    "01HX7YZABCDEFGHJKMNPQRSTVB",
    "01HX7YZABCDEFGHJKMNPQRSTVC",
)
VERB = "MS # B-541 (Schimpf)."


def _rec(bid, title, ref, verbatim=VERB, **kw):
    return {
        "BibliographyID": bid,
        "title": title,
        "citation": {"title": title},
        "resource_urls": [],
        "mentions": [
            {
                "MentionID": f"01JQKV6Z9F00000000000000{ref:02d}",
                "book": "USA-E-XChannel",
                "chapter": "chapter9full",
                "reference_number": str(ref),
                "verbatim_reference": verbatim,
            }
        ],
        **kw,
    }


def _write(d, name, rec):
    (d / name).write_text(json.dumps(rec))


def _records(d):
    return [
        json.loads(f.read_text())
        for f in sorted(d.glob("*.json"))
        if f.name != "index.json"
    ]


def test_records_citing_the_same_footnote_are_combined(tmp_path):
    _write(tmp_path, "bib_060fea444aaa21ac.json", _rec(B, "MS # B-541 (Schimpf).", 64))
    _write(
        tmp_path,
        "bib_8292e3aba0ac24f9.json",
        _rec(C, "Foreign Military Studies Manuscript B-541", 64),
    )
    _write(tmp_path, "bib_ad49c304edea6f4d.json", _rec(A, "MS # B-541", 81))
    removed = []
    assert reconcile_bibliography_dir(tmp_path, removed=removed) == 2
    (rec,) = _records(tmp_path)
    assert rec["BibliographyID"] == A  # the lowest id survives (deterministic)
    assert rec["merged_from"] == [B, C]  # the provenance of the merge
    refs = sorted(m["reference_number"] for m in rec["mentions"])
    assert refs == ["64", "64", "81"] or refs == [
        "64",
        "81",
    ]  # every citing source kept
    assert len(removed) == 2 and all(n.startswith("bib_") for n in removed)
    jsonschema.validate(rec, BIBLIOGRAPHY_OUTPUT_SCHEMA)


def test_back_references_and_short_texts_are_never_combined(tmp_path):
    """Two "Ibid., p. 47." notes in different places point at different works."""
    for n, bid in enumerate((A, B)):
        _write(
            tmp_path,
            f"bib_{n:016x}.json",  # distinct canonical names: only the same-text pass could fold them
            _rec(bid, f"work {n}", n + 1, verbatim="Ibid., p. 47."),
        )
    _write(tmp_path, f"bib_{9:016x}.json", _rec(C, "Unknown", 9, verbatim="See n. 5."))
    folded = b._verbatim_collapse_groups(b._group_bib_files_by_hash(tmp_path))
    assert folded == 0
    reconcile_bibliography_dir(tmp_path)
    assert {r["BibliographyID"] for r in _records(tmp_path)} == {A, B, C}
    assert not any(r.get("merged_from") for r in _records(tmp_path))


def test_different_footnotes_are_not_combined(tmp_path):
    _write(tmp_path, "bib_a.json", _rec(A, "MS # B-541", 1))
    _write(
        tmp_path,
        "bib_b.json",
        _rec(B, "MS # B-810", 2, verbatim="MS # B-810, Schmidt."),
    )
    reconcile_bibliography_dir(tmp_path)
    assert {r["BibliographyID"] for r in _records(tmp_path)} == {A, B}


def test_a_combined_record_keeps_what_it_absorbed_earlier(tmp_path):
    _write(tmp_path, "bib_a.json", _rec(B, "MS # B-541", 1, merged_from=[C]))
    _write(tmp_path, "bib_b.json", _rec(A, "Unknown", 2))
    reconcile_bibliography_dir(tmp_path)
    (rec,) = _records(tmp_path)
    assert rec["BibliographyID"] == A and rec["merged_from"] == [B, C]


def test_finds_queued_under_an_absorbed_id_belong_to_the_combined_record():
    from src.enrichment import bibliography_finds as bf

    index = {B: [{"endpoint": "x", "review_status": "affirmed"}]}
    assert bf.finds_for(index, {"BibliographyID": A, "merged_from": [B]}) == index[B]
    assert bf.finds_for(index, {"BibliographyID": A}) is None
    assert bf.finds_for(None, {"BibliographyID": A}) is None


def test_the_merged_away_records_are_deleted_from_s3_too(tmp_path, monkeypatch):
    import ecs_entrypoint as ep

    s3 = MagicMock()
    (tmp_path / "bib_keep.json").write_text("{}")
    ep._delete_merged_bibliography_records(
        s3,
        tmp_path,
        ["bib_gone.json", "bib_keep.json", "../evil.json", "bib_gone.json"],
    )
    keys = [c.kwargs["Key"] for c in s3.delete_object.call_args_list]
    assert keys == [
        "output/bibliography/bib_gone.json"
    ]  # once; never a kept or unsafe name


def test_a_failed_s3_delete_is_an_error_that_says_what_to_do(tmp_path, caplog):
    import ecs_entrypoint as ep

    s3 = MagicMock()
    s3.delete_object.side_effect = RuntimeError("AccessDenied")
    ep._delete_merged_bibliography_records(s3, tmp_path, ["bib_gone.json"])
    assert "OBSERV bib.merged_delete_failed" in caplog.text and "by hand" in caplog.text


def test_invalid_ids_are_not_listed_as_absorbed():
    merged = b._merge_bib_records([_rec(A, "x", 1), _rec("01LEGACY", "x", 2)])
    assert "merged_from" not in merged


def test_notes_that_point_elsewhere_or_are_only_a_date_are_never_combined():
    for text in (
        "*Ibid.*, 25 Jun 44.",
        "See below, ch. III.",
        "29 April 1944",
        "Hodges, op. cit., 112.",
    ):
        assert b._shared_citation_key({"verbatim_reference": text}) == "", text
    for text in ("2d ANFA Mtg.", "4th Cav Gp AAR.", "CCS 58th Mtg, 16 Jan 43."):
        assert b._shared_citation_key({"verbatim_reference": text}), text


def test_a_chain_of_shared_texts_folds_in_one_pass():
    """g3 shares one text with g1 and another with g2: all three end in g1 at once."""
    t1, t2 = "MS # B-541 (Schimpf).", "MS # B-810, Schmidt, 1946."
    groups = {
        "g1": [(None, _rec(A, "x", 1, verbatim=t1))],
        "g2": [(None, _rec(B, "y", 2, verbatim=t2))],
        "g3": [
            (None, _rec(C, "z", 3, verbatim=t1)),
            (None, _rec(C, "z", 4, verbatim=t2)),
        ],
    }
    assert b._verbatim_collapse_groups(groups) == 2
    assert list(groups) == ["g1"] and len(groups["g1"]) == 4


def test_the_furthest_resolution_and_every_copy_survive_the_merge():
    lo = _rec(A, "MS # B-541", 1, search_status="not_found")
    hi = _rec(
        B,
        "MS # B-541",
        2,
        search_status="resolved",
        search_source="archive_org",
        availability="online",
        download_path="source/archive_org/x.pdf",
        resource_urls=["https://archive.org/details/b541"],
        source_metadata={
            "findings": [
                {"url": "https://archive.org/details/b541", "source": "archive_org"}
            ]
        },
    )
    merged = b._merge_bib_records([lo, hi])
    assert merged["BibliographyID"] == A and merged["merged_from"] == [B]
    assert merged["search_status"] == "resolved" and merged["download_path"]
    assert merged["resource_urls"] == ["https://archive.org/details/b541"]
    assert merged["source_metadata"]["findings"][0]["source"] == "archive_org"


def test_author_and_title_alone_never_combine_records():
    rec = {"citation": {"author": "Hodges", "title": "Unknown"}, "title": "Unknown"}
    assert b._shared_citation_key(rec) == ""


def test_an_unexpected_name_is_kept_and_said(tmp_path, caplog):
    import ecs_entrypoint as ep

    s3 = MagicMock()
    ep._delete_merged_bibliography_records(s3, tmp_path, ["notbib.json"])
    s3.delete_object.assert_not_called()
    assert "OBSERV bib.merged_delete_skipped" in caplog.text
