"""Bibliography dedup: one record per DOCUMENT, each citation a mention.

Owner decision (2026-11-07): "116th Inf S-3 Jnl should be one record, with multiple citations".
XChannel-8 batch E2E: the reconcile fuzzy fold (0.85 SequenceMatcher on the verbatim) merged
"*Seventh Army*, KTB 1.I.-30.VI.44, 6 Jun 44" INTO "*Fifteenth Army*, KTB 1.I.-30.VI.44, 5 Jun 44"
(ratio 0.897) - two different war diaries - the same ends-match failure as place dedup.
"""

import json

import pytest

from src.extraction import bibliography as B


@pytest.mark.parametrize(
    "verbatim,key",
    [
        (
            "*Seventh Army, KTB 1.I.-30.VI.44,* 6 Jun 44.",
            "seventh army ktb 1 i 30 vi 44",
        ),
        (
            "*Seventh Army, KTB 1.I.-30.VI.44,* 27 Jan and 9 Jun 44.",
            "seventh army ktb 1 i 30 vi 44",
        ),
        ("116th Inf S-3 Jnl, 6 Jun 44.", "116th inf s 3 jnl"),
        ("First Army G-3 Jnl, 6-7 Jun 44.", "first army g 3 jnl"),
        ("First Army G-3 Jnl, 6 and 7 Jun 44.", "first army g 3 jnl"),
        ("First Army G-3 Jnl, 30 May-1 Jun 44.", "first army g 3 jnl"),
        ("First Army G-3 Jnl, 5, 6 Jun 44.", "first army g 3 jnl"),
        ("First Army G-3 Jnl, 6th June 1944.", "first army g 3 jnl"),
        ("First Army G-3 Jnl, June 6, 1944.", "first army g 3 jnl"),
        ("First Army G-3 Jnl, 6 Jun.", "first army g 3 jnl"),
        # words that merely START like a month are not dates
        ("V Corps Log 1 Marine.", "v corps log 1 marine"),
        ("Engr Log, Junction 2.", "engr log junction 2"),
        (
            "*Marinegruppenkommando West, KTB 1.-7.VI.44,* 6 Jun 44,",
            "marinegruppenkommando west ktb 1 7 vi 44",
        ),
        # a periodical ISSUE: its date identifies it ("journal" is not a periodic record)
        (
            "Journal of Modern History, 14 Mar 1944.",
            "journal of modern history 14 mar 1944",
        ),
        ("116th Inf S-3 Jnl.", "116th inf s 3 jnl"),
        ("See First Army G-3 Jnl.", "first army g 3 jnl"),
        ("* cf. *First Army G-3 Jnl,* 7 Jun 44.", "first army g 3 jnl"),
        (
            "*Marinegruppenkommando West, KTB 1.-7.VI.44,* 6 Jun 4.",
            "marinegruppenkommando west ktb 1 7 vi 44",
        ),
        ("Stacey, The Canadian Army, p. 81.", "stacey the canadian army"),
        # the date IDENTIFIES a non-periodic document (an interview) - kept
        (
            "Interv, Pogue with Williams, London, 31 May 47.",
            "interv pogue with williams london 31 may 47",
        ),
    ],
)
def test_document_key(verbatim, key):
    assert B._normalize_title(verbatim) == key


@pytest.mark.parametrize(
    "a,b,same",
    [
        # different issuing unit: never the same document, however similar the tails
        (
            "*Seventh Army, KTB 1.I.-30.VI.44,* 6 Jun 44.",
            "*Fifteenth Army,* *KTB 1.I.-30.VI.44,* 5 Jun 44.",
            False,
        ),
        # different manuscript / journal numbers
        ("MS # B-432 (Ziegelmann).", "MS # B-433 (Ziegelmann).", False),
        ("18th Inf S-1 Jnl, 6 Jun 44.", "18th Inf S-3 Jnl, 6 Jun 44.", False),
        ("116th Inf S-3 Jnl.", "16th Inf S-3 Jnl.", False),
        # Roman-numeral volumes; a volume vs the untagged work
        (
            "Army Air Forces in WWII, Vol III.",
            "Army Air Forces in WWII, Vol IV.",
            False,
        ),
        ("Army Air Forces in WWII.", "Army Air Forces in WWII, Vol 2.", False),
        # same document: typo in the unit name; dated entries of one diary; publication detail
        (
            "*Marinegruppenkommando West, KTB 16.-30.IV.44,* 27 Apr 44.",
            "*Maringruppenkommando West, KTB 16.-30.IV.44,* 8 Apr 44.",
            True,
        ),
        ("116th Inf S-3 Jnl, 6 Jun 44.", "116th Inf S-3 Jnl, 7 Jun 44.", True),
        (
            "The Canadian Army, 1939-1945",
            "The Canadian Army 1939-1945.",
            True,
        ),
    ],
)
def test_same_document(a, b, same):
    assert B._same_document(B._normalize_title(a), B._normalize_title(b)) is same


def _parsed(tmp_path, notes):
    pf = tmp_path / "ch-parsed.json"
    pf.write_text(
        json.dumps(
            {
                "footnotes": [
                    {
                        "number": n,
                        "text": t,
                        "url": f"https://h/p.html#fn{n}",
                        "kind": "footnote",
                    }
                    for n, t in notes
                ]
            }
        ),
        encoding="utf-8",
    )
    return pf


def _records(bib_dir):
    return [
        json.loads(f.read_text())
        for f in bib_dir.glob("*.json")
        if f.name not in ("index.json", "duplicate_report.json")
    ]


def test_dated_journal_citations_become_one_record_with_mentions(tmp_path):
    pf = _parsed(
        tmp_path,
        [
            (84, "116th Inf S-3 Jnl, 6 Jun 44."),
            (85, "116th Inf S-3 Jnl, 7 Jun 44."),
            (86, "See 116th Inf S-3 Jnl."),
            (12, "*Fifteenth Army,* *KTB 1.I.-30.VI.44,* 5 Jun 44."),
            (99, "*Seventh Army, KTB 1.I.-30.VI.44,* 6 Jun 44."),
        ],
    )
    out = tmp_path / "out"
    B.extract_bibliography_deterministic(pf, out, "Book", "ch")
    B.reconcile_bibliography_dir(out / "bibliography")
    by_refs = {
        tuple(sorted(m["reference_number"] for m in r["mentions"])): r
        for r in _records(out / "bibliography")
    }
    assert set(by_refs) == {("84", "85", "86"), ("12",), ("99",)}
    jnl = by_refs[("84", "85", "86")]
    # each citation keeps its own date on its mention (verbatim provenance)
    assert sorted(m["verbatim_reference"] for m in jnl["mentions"]) == [
        "116th Inf S-3 Jnl, 6 Jun 44.",
        "116th Inf S-3 Jnl, 7 Jun 44.",
        "See 116th Inf S-3 Jnl.",
    ]


def test_fuzzy_fold_does_not_chain_through_a_shared_neighbour():
    # b and c are each "same" as a only by a chain; every member must match the candidate.
    groups = {
        "k1": [(None, {"title": "Stacey, The Canadian Army"})],
        "k2": [(None, {"title": "Stacey, The Canadian Army, Vol 2"})],
        "k3": [(None, {"title": "Stacey, The Canadian Army, Vol 3"})],
    }
    B._fuzzy_collapse_groups(groups)
    assert sorted(groups) == ["k1", "k2", "k3"]


def test_record_stored_under_the_old_dated_key_folds_with_new_writes(tmp_path):
    # Migration: a canonical file written BEFORE the document key (its hash/title carry the
    # entry date) and a new write of another entry of the same journal reconcile to ONE record.
    import hashlib

    bib = tmp_path / "out" / "bibliography"
    bib.mkdir(parents=True)
    old_title = "116th Inf S-3 Jnl, 6 Jun 44."
    old_hash = hashlib.sha256("116th inf s 3 jnl 6 jun 44".encode()).hexdigest()[:16]
    (bib / f"bib_{old_hash}.json").write_text(
        json.dumps(
            {
                "BibliographyID": "01M6J3NGH7SB27Q1Q9QAKG9H7N",
                "title": old_title,
                "citation": {"title": "Unknown"},
                "mentions": [
                    {
                        "MentionID": "01M6J3NGB3DR9QD7VT6ST9Y8SH",
                        "book": "Book",
                        "chapter": "ch",
                        "reference_number": "84",
                        "reference_type": "footnote",
                        "verbatim_reference": old_title,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    pf = _parsed(tmp_path, [(85, "116th Inf S-3 Jnl, 7 Jun 44.")])
    B.extract_bibliography_deterministic(pf, tmp_path / "out", "Book", "ch")
    B.reconcile_bibliography_dir(bib)
    recs = _records(bib)
    assert len(recs) == 1
    assert sorted(m["reference_number"] for m in recs[0]["mentions"]) == ["84", "85"]


def test_dated_diary_entry_resolves_to_the_earlier_diary_citation():
    works = [{"title": "Irwin Diary", "short": "irwin diary", "author": ""}]
    assert (
        B._resolve_short_title_repeat("Irwin Diary, 6 Jun 44.", works) == "Irwin Diary"
    )


@pytest.mark.parametrize(
    "key,ids",
    [
        ("army air forces vol iii", {"vol iii"}),
        ("book of the dead", set()),  # "of" is not a volume number
        (
            "the book civil war",
            set(),
        ),  # "civil" is made of Roman letters, not a numeral
        ("annex 4 ms b 432", {"4", "b", "432", "annex 4"} - {"b"}),
    ],
)
def test_identifiers(key, ids):
    assert B._identifiers(key) == ids


def test_reconcile_reports_unrecognised_periodic_dates(tmp_path, caplog):
    import logging

    pf = _parsed(tmp_path, [(1, "116th Inf S-3 Jnl, Jun the 6th.")])
    out = tmp_path / "out"
    B.extract_bibliography_deterministic(pf, out, "Book", "ch")
    with caplog.at_level(logging.INFO):
        B.reconcile_bibliography_dir(out / "bibliography")
    (line,) = [
        r.getMessage() for r in caplog.records if "bib.reconcile" in r.getMessage()
    ]
    assert "periodic_date_residue=1" in line
