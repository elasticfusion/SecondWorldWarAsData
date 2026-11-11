"""Provenance validation (scripts/validate_provenance): a record's links may all resolve and still
point at the wrong place, or quote text that is not in the book. Calibrated on USA-E-XChannel
(2026-11-11): 7% of 3,587 quotes were in another sub-event or not in the book at all, and the
logistics paragraph numbers were sub-event-relative."""

import json
from pathlib import Path

import pytest

from scripts import validate_provenance as V
from src.utils import quote_match as Q

EV, S1, S2 = (
    "EV000000000000000000000001",
    "SE000000000000000000000001",
    "SE000000000000000000000002",
)
NOTES_EV, NS1 = "EV000000000000000000000NTS", "SE00000000000000000000NTS1"

PARAS = {
    1: "Force U was marshaled in the Tor Bay area and east of Plymouth.",
    2: "The _Seventh Army,_ had neither the troops nor the guns.<sup>[1](https://x/#fn1)</sup>",
    3: "Admiral Ramsay commanded the naval forces for Operation Neptune.",
    4: "At the end of 7 June there were at la Fière some 600 men of the 507th.",
}


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def root(tmp_path):
    book = tmp_path / "content" / "B"
    _write(
        book / "chapter1-parsed.json",
        {
            "book": "B",
            "paragraphs": [{"absolute_number": n, "text": t} for n, t in PARAS.items()],
            "footnotes": [
                {"number": 1, "text": "See Phantom Intercept in 21 A Gp Sitrep 4."}
            ],
        },
    )
    _write(
        book / "chapter1-event.json",
        {
            "Event": {
                "EventID": EV,
                "Sub-events": [
                    {
                        "Sub-eventID": S1,
                        "Sub-event_fulltext": {"Paragraph_1": "-", "Paragraph_2": "-"},
                    },
                    {
                        "Sub-eventID": S2,
                        "Sub-event_fulltext": {"Paragraph_3": "-", "Paragraph_4": "-"},
                    },
                ],
            }
        },
    )
    _write(
        book / "chapter1-notes-event.json",
        {
            "Event": {
                "EventID": NOTES_EV,
                "Sub-events": [
                    {
                        "Sub-eventID": NS1,
                        "Sub-event_fulltext": {
                            "paragraph_1": "Information on mounting"
                        },
                    }
                ],
            }
        },
    )
    return tmp_path


def _person(root, name, *mentions):
    _write(root / "people" / f"{name}.json", {"event_mentions": list(mentions)})


def _m(text, seid=S1, event=EV, **kw):
    return {
        "MentionID": kw.pop("mid", "M1"),
        "EventID": event,
        "Sub_eventID": seid,
        "original_text": text,
        **kw,
    }


def _run(root):
    return V.validate(root, "B")


def _reasons(f, check):
    return [(i["level"], i["reason"]) for i in f.items if i["check"] == check]


# --- the shared matcher ---------------------------------------------------------------------------


def test_markup_is_not_part_of_the_text():
    assert (
        Q.normalize(PARAS[2]) == "the seventh army had neither the troops nor the guns"
    )
    assert Q.normalize("_\\--212--_ text") == "text"
    assert Q.normalize("![](img.jpg) [Ramsay](https://x) said") == "ramsay said"


def test_mojibake_is_undone():
    assert Q.normalize("la FiÃ¨re") == Q.normalize("la Fière")


def test_an_elided_quote_is_its_parts():
    index = Q.build_index([{"absolute_number": n, "text": t} for n, t in PARAS.items()])
    assert Q.in_paragraphs("Force U was marshaled ... east of Plymouth", index) == (
        1,
        Q.EXACT,
    )
    assert Q.in_paragraphs("Force U was marshaled … the 101st stormed", index) == (
        None,
        "",
    )


def test_a_near_match_is_only_looked_for_in_the_cited_paragraphs():
    index = Q.build_index([{"absolute_number": n, "text": t} for n, t in PARAS.items()])
    reworded = "Admiral Ramsay commanded all the naval forces for Operation Neptune"
    assert Q.in_paragraphs(reworded, index, [3]) == (3, Q.NEAR)
    assert Q.in_paragraphs(reworded, index) == (None, "")  # whole chapter: exact only


# --- P2-Q1 quotes --------------------------------------------------------------------------------


def test_a_quote_in_its_sub_event_passes(root):
    _person(
        root,
        "p",
        _m("marshaled in the Tor Bay area"),
        _m("la FiÃ¨re some 600", seid=S2),
    )
    f = _run(root)
    assert f.ok and f.checked["P2-Q1"] == 2


def test_a_quote_in_another_sub_event_is_the_wrong_location(root):
    _person(root, "p", _m("Admiral Ramsay commanded the naval forces"))  # it is in S2
    f = _run(root)
    assert _reasons(f, "P2-Q1") == [("fail", "quote is in another part of the chapter")]
    assert f.items[0]["found_in"] == 3 and f.items[0]["found_in_sub_event"] == S2


def test_a_quote_not_in_the_book_fails(root):
    _person(root, "p", _m("the 101st Airborne stormed the beach at dawn"))
    assert _reasons(_run(root), "P2-Q1") == [("fail", "quote is not in the book")]


def test_a_reworded_quote_is_a_warning(root):
    _person(
        root,
        "p",
        _m(
            "Admiral Ramsay commanded all the naval forces for Operation Neptune",
            seid=S2,
        ),
    )
    f = _run(root)
    assert f.ok and _reasons(f, "P2-Q1") == [("warn", "near match only")]


def test_a_mention_of_another_book_is_not_checked(root):
    _person(root, "p", _m("not in this book", event="EV_OTHER_BOOK", seid="SE_OTHER"))
    f = _run(root)
    assert f.ok and not f.checked


def test_a_quote_without_a_sub_event_is_looked_for_in_the_whole_chapter(root):
    _person(root, "p", _m("Admiral Ramsay commanded", seid=None))
    assert _run(root).ok


def test_a_record_level_quote_is_checked_when_all_its_mentions_are_in_the_book(root):
    _write(
        root / "dates" / "d.json",
        {"original_text": "not in the book at all here", "event_mentions": [_m("")]},
    )
    _write(
        root / "dates" / "shared.json",
        {
            "original_text": "not in the book at all here",
            "event_mentions": [_m(""), _m("", event="EV_OTHER", seid="X")],
        },
    )
    f = _run(root)
    assert [i["where"] for i in f.items] == ["dates/d.json"]


# --- P2-L1 / P2-L2 links and locators -------------------------------------------------------------


def test_a_sub_event_of_another_event_fails(root):
    _person(
        root, "p", _m("Information on mounting", seid=NS1)
    )  # NS1 is the notes event's
    assert _reasons(_run(root), "P2-L1") == [
        ("fail", "sub-event belongs to another event")
    ]


def test_cited_paragraphs_must_be_in_the_chapter_and_the_sub_event(root):
    _write(
        root / "equipment" / "e.json",
        {
            "event_mentions": [
                _m("Force U", paragraph_numbers=[1]),
                _m("Force U", mid="M2", paragraph_numbers=[3]),
                _m("Force U", mid="M3", paragraph_numbers=["99"]),
            ]
        },
    )
    f = _run(root)
    assert sorted(_reasons(f, "P2-L2")) == [
        ("fail", "cited paragraph is not in the chapter"),
        ("fail", "cited paragraph is outside the cited sub-event"),
    ]


# --- P2-Q2 notes ----------------------------------------------------------------------------------


def _bib(root, **m):
    _write(
        root / "bibliography" / "b.json",
        {"mentions": [{"EventID": EV, "Sub-eventID": S1, **m}]},
    )


def test_a_note_quote_must_be_the_cited_notes_text(root):
    _bib(
        root,
        reference_number="1",
        verbatim_reference="Phantom Intercept in 21 A Gp Sitrep 4",
    )
    assert _run(root).ok
    _bib(root, reference_number="1", verbatim_reference="Some other source entirely")
    assert _reasons(_run(root), "P2-Q2") == [
        ("fail", "quote is not in the chapter's notes")
    ]


def test_a_bibliography_mention_without_a_locator_is_a_warning(root):
    _bib(root, reference_number="", verbatim_reference="")
    f = _run(root)
    assert f.ok and _reasons(f, "P2-Q2") == [
        ("warn", "no note locator (no reference_number or quote)")
    ]


def test_a_notes_event_is_checked_against_its_note_texts(root):
    _person(root, "p", _m("Information on mounting", seid=NS1, event=NOTES_EV))
    assert _run(root).ok


# --- the book, the report, the gate mode --------------------------------------------------------


def test_a_book_without_readable_chapters_fails(tmp_path):
    f = V.validate(tmp_path, "Missing")
    assert not f.ok and f.items[0]["check"] == "P2-SRC"


def test_the_report_is_written_and_the_result_observed(root):
    from unittest import mock

    _person(root, "p", _m("the 101st Airborne stormed the beach at dawn"))
    with mock.patch("src.utils.observe.observe") as obs:
        f, path = V.validate_and_record(root, "B")
    report = json.loads(path.read_text())
    assert path == root / "reports" / "provenance" / "B-phase2.json"
    assert report["result"] == "FAIL" and report["failed"] == {"P2-Q1": 1}
    assert obs.call_args.kwargs["status"] == "error"
    assert obs.call_args.kwargs["report"] == "output/reports/provenance/B-phase2.json"


@pytest.mark.parametrize(
    "env, expected", [("block", "block"), ("WARN", "warn"), ("nonsense", "warn")]
)
def test_the_gate_mode(monkeypatch, env, expected):
    monkeypatch.setenv("PROVENANCE_GATE", env)
    assert V.gate_mode() == expected


def test_the_gate_mode_defaults_to_config(monkeypatch):
    monkeypatch.delenv("PROVENANCE_GATE", raising=False)
    assert V.gate_mode() == "warn"  # config.yaml provenance_validation.gate


# --- review findings (2026-11-11) ---------------------------------------------------------------


def _index():
    return Q.build_index([{"absolute_number": n, "text": t} for n, t in PARAS.items()])


def test_a_match_is_whole_words():
    assert (
        Q.find_quote(
            "ton", Q.build_index([{"absolute_number": 1, "text": "Washington"}])
        )[1]
        == ""
    )
    assert Q.find_quote("plymouth", _index()) == (1, Q.EXACT)


def test_a_near_match_needs_four_words_and_no_rival():
    index = Q.build_index(
        [
            {"absolute_number": 1, "text": "the German army attacked the town at dawn"},
            {"absolute_number": 2, "text": "the German army attacked the city at dawn"},
        ]
    )
    assert Q.find_quote("german armies", index) == (
        None,
        "",
    )  # too few words for a near match
    # both paragraphs match about equally: ambiguous, not a guess
    assert Q.find_quote("the german army attacked the village at dawn", index) == (
        None,
        "",
    )


def test_a_quote_may_run_across_two_paragraphs():
    assert Q.in_paragraphs("east of Plymouth. The Seventh Army", _index(), [1, 2]) == (
        1,
        Q.EXACT,
    )


def test_a_short_quote_not_in_place_only_warns(root):
    _person(
        root, "p", _m("Admiral Ramsay")
    )  # in S2, not S1: but two words prove little
    f = _run(root)
    assert f.ok and _reasons(f, "P2-Q1") == [
        ("warn", "quote is in another part of the chapter")
    ]


def test_a_cited_paragraph_outside_the_chapter_is_one_finding_not_two(root):
    _write(
        root / "equipment" / "e.json",
        {
            "event_mentions": [
                _m("marshaled in the Tor Bay area", paragraph_numbers=[99])
            ]
        },
    )
    f = _run(root)
    assert _reasons(f, "P2-L2") == [("fail", "cited paragraph is not in the chapter")]
    assert not f.failed[
        "P2-Q1"
    ]  # the quote is checked in the sub-event instead, and passes


def test_a_notes_event_mention_citing_a_real_paragraph_is_not_outside_its_sub_event(
    root,
):
    _person(
        root,
        "p",
        _m("Information on mounting", seid=NS1, event=NOTES_EV, paragraph_numbers=[1]),
    )
    assert not _run(root).failed["P2-L2"]


def test_a_short_note_is_not_proof(root):
    (root / "content" / "B" / "chapter1-endnotes.json").write_text(
        json.dumps({"endnotes": [{"number": 2, "text": "Ibid."}]})
    )
    _bib(
        root, reference_number="2", verbatim_reference="Ibid. A different source, p. 4"
    )
    assert _reasons(_run(root), "P2-Q2") == [
        ("fail", "quote is not in the chapter's notes")
    ]


def test_footnotes_and_endnotes_may_share_a_number(root):
    (root / "content" / "B" / "chapter1-endnotes.json").write_text(
        json.dumps(
            {"endnotes": [{"number": 1, "text": "An endnote one, another source."}]}
        )
    )
    _bib(
        root,
        reference_number="1",
        verbatim_reference="Phantom Intercept in 21 A Gp Sitrep 4",
    )
    assert _run(root).ok  # the footnote 1 still counts


def test_a_quote_of_a_sub_event_without_paragraphs_is_checked_in_the_chapter(root):
    ev_file = root / "content" / "B" / "chapter1-event.json"
    ev = json.loads(ev_file.read_text())
    ev["Event"]["Sub-events"].append(
        {"Sub-eventID": "SE_DEGRADED", "Sub-event_fulltext": {"summary": "x"}}
    )
    ev_file.write_text(json.dumps(ev))
    _person(
        root,
        "p",
        _m("marshaled in the Tor Bay area", seid="SE_DEGRADED"),
        _m("the 101st Airborne stormed the beach", mid="M2", seid="SE_DEGRADED"),
    )
    assert _reasons(_run(root), "P2-Q1") == [("fail", "quote is not in the book")]


def test_a_record_level_summary_only_warns(root):
    _write(
        root / "dates" / "d.json",
        {
            "original_text": "a summary written by the extractor",
            "event_mentions": [_m("")],
        },
    )
    f = _run(root)
    assert f.ok and _reasons(f, "P2-Q1") == [("warn", "quote is not in the book")]


# --- Phase 3 (after every merge) -----------------------------------------------------------------


def _bib_rec(root, name="b", **rec):
    base = {
        "BibliographyID": "B1",
        "title": "Outline OVERLORD, Part II",
        "license": "public_domain",
        "copyright_status": {"status": "public_domain"},
        "mentions": [
            {
                "EventID": EV,
                "Sub-eventID": S1,
                "reference_number": "1",
                "verbatim_reference": "Phantom Intercept in 21 A Gp Sitrep 4",
            }
        ],
    }
    base.update(rec)
    _write(root / "bibliography" / f"{name}.json", base)


def _p3(root, check):
    return [
        (i["level"], i["reason"])
        for i in V.validate(root, "B", "3").items
        if i["check"] == check
    ]


def test_phase3_reruns_the_phase2_checks(root):
    _person(root, "p", _m("the 101st Airborne stormed the beach at dawn"))
    assert _p3(root, "P2-Q1") == [("fail", "quote is not in the book")]


@pytest.mark.parametrize("title", ["ibid.", "26", "", "Op. cit.", "Unknown"])
def test_a_stub_bibliography_entry_fails(root, title):
    _bib_rec(root, title=title)
    assert _p3(root, "P3-B1") == [("fail", "stub entry, not a source")]


def test_a_not_citation_entry_warns(root):
    _bib_rec(root, search_status="not_citation")
    assert _p3(root, "P3-B1") == [("warn", "classed not_citation (awaits disposition)")]


def test_rights_fields(root):
    plain = [
        {
            "EventID": EV,
            "Sub-eventID": S1,
            "reference_number": "1",
            "verbatim_reference": "Harrison, Cross-Channel Attack, p. 4",
        }
    ]
    _bib_rec(root, license="", copyright_status=None, mentions=plain)
    assert sorted(_p3(root, "P3-R1")) == [
        ("fail", "no license"),
        ("warn", "no copyright_status"),
    ]
    _bib_rec(root, license="unknown", mentions=plain)
    assert _p3(root, "P3-R1") == [("warn", "license unknown")]


def test_a_nara_source_needs_an_archive_reference(root):
    nara_warn = [("warn", "NARA source without an archive_reference_number")]
    _bib_rec(root, search_source="nara_catalog", archive_reference_number="")
    assert _p3(root, "P3-R1") == nara_warn
    _bib_rec(root, resource_urls=[{"url": "https://catalog.archives.gov/id/1"}])
    assert _p3(root, "P3-R1") == nara_warn
    _bib_rec(root, search_source="nara_grok", archive_reference_number="RG 407")
    assert _p3(root, "P3-R1") == []
    _bib_rec(root)  # a citation-shaped entry Phase 3 did not find at NARA: no warning
    assert _p3(root, "P3-R1") == []


def test_a_bibliography_entry_of_another_book_is_not_checked(root):
    _bib_rec(root, license="", mentions=[{"EventID": "OTHER", "Sub-eventID": "X"}])
    assert _p3(root, "P3-R1") == []


def test_an_enriched_record_must_name_its_source(root):
    _write(
        root / "people" / "p.json",
        {"enrichment_status": "enriched", "event_mentions": [_m("Force U")]},
    )
    assert _p3(root, "P3-A1") == [
        ("fail", "enriched but names no source (un-attributable)")
    ]
    _write(
        root / "people" / "p.json",
        {
            "enrichment_status": "enriched",
            "event_mentions": [_m("Force U")],
            "biographical_profile": {
                "biography_sources": [
                    {"source": "Grokipedia", "page": "https://grokipedia.com/page/X"}
                ]
            },
        },
    )
    assert _p3(root, "P3-A1") == []


def test_every_cited_note_needs_a_bibliography_mention(root):
    ev_file = root / "content" / "B" / "chapter1-event.json"
    ev = json.loads(ev_file.read_text())
    ev["Event"]["Sub-events"][0]["Footnote_References"] = [1, 2]
    ev_file.write_text(json.dumps(ev))
    _bib_rec(root)  # holds note 1 of S1 only
    f = V.validate(root, "B", "3")
    found = [i for i in f.items if i["check"] == "P3-C1"]
    assert len(found) == 1 and found[0]["missing"] == 1 and found[0]["cited"] == 2
    assert found[0]["where"] == "content/B/chapter1"


def test_phase2_does_not_run_the_phase3_checks(root):
    _bib_rec(root, title="ibid")
    assert not [
        i for i in V.validate(root, "B", "2").items if i["check"].startswith("P3")
    ]


def test_a_stub_note_needs_no_bibliography_entry(root):
    """The extractor skips stub notes ("Ibid.") by design: they are not unresolved citations."""
    book = root / "content" / "B"
    (book / "chapter1-endnotes.json").write_text(
        json.dumps({"endnotes": [{"number": 2, "text": "Ibid."}]})
    )
    ev = json.loads((book / "chapter1-event.json").read_text())
    ev["Event"]["Sub-events"][0]["Footnote_References"] = [1, 2]
    (book / "chapter1-event.json").write_text(json.dumps(ev))
    _bib_rec(root)  # note 1 only
    assert not [i for i in V.validate(root, "B", "3").items if i["check"] == "P3-C1"]


@pytest.mark.parametrize("title", ["1944", "FM 100-5", "Outline OVERLORD"])
def test_a_short_real_title_is_not_a_stub(root, title):
    _bib_rec(root, title=title)
    assert _p3(root, "P3-B1") == []


def test_another_countrys_national_archives_is_not_nara(root):
    _bib_rec(
        root,
        resource_urls=[{"url": "https://discovery.nationalarchives.gov.uk/details/C1"}],
    )
    assert _p3(root, "P3-R1") == []
