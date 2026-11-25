"""A bibliography record whose cited work we hold says so (owner 2026-11-24): matched exactly by
(citing book, parsed chapter, note number), never guessed, provenance kept."""

import json
import logging

import jsonschema
import pytest

from src.extraction import bibliography_corpus as bc
from src.extraction import corpus_links as cl
from src.schemas.bibliography_output import BIBLIOGRAPHY_OUTPUT_SCHEMA

OMAHA_P31 = "https://www.ibiblio.org/hyperwar/USA/USA-A-Omaha/USA-A-Omaha-2.html#page31"
XCH = "https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/index.html"


def _bib(out, name, mentions):
    d = out / "bibliography"
    d.mkdir(exist_ok=True)
    rec = {"BibliographyID": "01HX7YZABCDEFGHJKMNPQRSTV" + name[-1].upper(),
           "title": "Omaha Beachhead", "mentions": mentions}  # fmt: skip
    (d / f"{name}.json").write_text(json.dumps(rec))
    return d / f"{name}.json"


def _mention(number, chapter="chapter6full", book="XChannel"):
    return {"book": book, "chapter": chapter, "reference_number": number,
            "verbatim_reference": "Taylor, Omaha Beachhead, p. 31."}  # fmt: skip


def _link(
    number="116", chapter="chapter6full", volume="USA-A-Omaha", book="USA-A-Omaha"
):
    from src.utils.deterministic_ids import book_id

    return {
        "citing": {"book": "XChannel", "parsed_chapter": chapter,
                   "reference_number": number, "url": OMAHA_P31},
        "cited": {"BookID": book_id(book), "book": book, "volume": volume, "format": "pdf",
                  "edition": "CMH Pub 100-11", "copyright_date": "1945"},
    }  # fmt: skip


def test_a_held_work_is_marked_on_its_record(tmp_path):
    f = _bib(tmp_path, "b1", [_mention("116")])
    assert bc.mark_held(tmp_path, [_link()]) == 1
    rec = json.loads(f.read_text())
    held = rec["held_in_corpus"]
    assert held["book"] == "USA-A-Omaha" and held["format"] == "pdf"
    assert held["edition"] == "CMH Pub 100-11" and held["linked_at"]
    assert held["evidence"] == [{"book": "XChannel", "chapter": "chapter6full",
                                 "reference_number": "116", "url": OMAHA_P31}]  # fmt: skip
    assert rec["title"] == "Omaha Beachhead" and rec["mentions"]  # nothing else changed
    jsonschema.validate(rec, BIBLIOGRAPHY_OUTPUT_SCHEMA)


def test_an_unchanged_mark_is_not_rewritten(tmp_path):
    f = _bib(tmp_path, "b1", [_mention("116")])
    bc.mark_held(tmp_path, [_link()])
    first = f.read_text()
    assert bc.mark_held(tmp_path, [_link()]) == 0 and f.read_text() == first


def test_a_link_in_the_narrative_marks_nothing(tmp_path):
    f = _bib(tmp_path, "b1", [_mention("116")])
    assert bc.mark_held(tmp_path, [_link(number=None)]) == 0
    assert "held_in_corpus" not in json.loads(f.read_text())


def test_another_chapters_note_with_the_same_number_is_not_matched(tmp_path):
    f = _bib(tmp_path, "b1", [_mention("116", chapter="chapter7full")])
    assert bc.mark_held(tmp_path, [_link()]) == 0
    assert "held_in_corpus" not in json.loads(f.read_text())


def test_a_link_in_the_second_work_of_a_note_marks_that_works_record():
    """Phase 2 splits "Taylor ...; Harrison ..." into records 3 and 3b; the link in the second
    part must carry 3b, so it marks Harrison's record, not Taylor's."""
    import phase3_enrich_data as P

    fn = {"number": 3, "text": "Taylor, Omaha Beachhead, p. 31; Harrison, "
          f"_[Cross-Channel Attack]({XCH.replace('index.html', 'USA-E-XChannel-5.html')})_, p. 5."}  # fmt: skip
    doc = {
        "_parsed_chapter": "chapter6full",
        "_parsed_book": "TheSiegfriedLineCampaign",
    }
    out = P._note_edges(fn, doc, "https://www.ibiblio.org/hyperwar/USA/USA-E-Siegfried/index.html",
                        "TheSiegfriedLineCampaign", {})  # fmt: skip
    (edge,) = [e for e in out["edges"] if e["kind"] == "in_corpus_doc"]
    assert edge["reference_number"] == "3b" and edge["to_volume"] == "USA-E-XChannel"


def test_a_note_in_several_records_is_not_guessed(tmp_path, caplog):
    _bib(tmp_path, "b2", [_mention("4")])
    _bib(tmp_path, "b3", [_mention("4")])
    with caplog.at_level(logging.WARNING):
        assert bc.mark_held(tmp_path, [_link(number="4")]) == 0
    assert "ambiguous=1" in caplog.text


def test_a_mark_that_no_longer_holds_is_cleared(tmp_path):
    f = _bib(tmp_path, "b1", [_mention("116")])
    bc.mark_held(tmp_path, [_link()])
    assert bc.mark_held(tmp_path, []) == 1  # the volume is no longer held
    assert "held_in_corpus" not in json.loads(f.read_text())


def test_a_partial_run_clears_nothing(tmp_path):
    f = _bib(tmp_path, "b1", [_mention("116")])
    bc.mark_held(tmp_path, [_link()])
    assert bc.mark_held(tmp_path, [], clear_stale=False) == 0
    assert json.loads(f.read_text())["held_in_corpus"]


def test_a_changed_defining_copy_updates_the_mark(tmp_path):
    f = _bib(tmp_path, "b1", [_mention("116")])
    bc.mark_held(tmp_path, [_link(book="Omaha-HTML")])
    assert bc.mark_held(tmp_path, [_link(book="USA-A-Omaha")]) == 1
    assert json.loads(f.read_text())["held_in_corpus"]["book"] == "USA-A-Omaha"


def test_records_are_matched_on_the_book_folder_phase2_used(tmp_path):
    f = _bib(tmp_path, "b1", [_mention("116", book="XChannelFolder")])
    link = _link()
    link["citing"]["parsed_book"] = "XChannelFolder"  # the meta's book name may differ
    assert bc.mark_held(tmp_path, [link]) == 1
    assert (
        json.loads(f.read_text())["held_in_corpus"]["evidence"][0]["book"]
        == "XChannelFolder"
    )


def test_only_a_web_link_is_kept_as_evidence(tmp_path):
    f = _bib(tmp_path, "b1", [_mention("116")])
    link = _link()
    link["citing"]["url"] = "javascript:alert(1)"
    bc.mark_held(tmp_path, [link])
    rec = json.loads(f.read_text())
    assert rec["held_in_corpus"]["evidence"][0]["url"] is None
    jsonschema.validate(rec, BIBLIOGRAPHY_OUTPUT_SCHEMA)


def test_a_record_cited_from_two_books_keeps_both_as_evidence(tmp_path):
    f = _bib(
        tmp_path, "b1", [_mention("116"), _mention("9", book="CrossChannelAttack")]
    )
    other = _link(number="9")
    other["citing"]["book"] = "CrossChannelAttack"
    bc.mark_held(tmp_path, [_link(), other])
    ev = json.loads(f.read_text())["held_in_corpus"]["evidence"]
    assert {e["book"] for e in ev} == {"XChannel", "CrossChannelAttack"}


def test_a_failing_write_does_not_stop_the_others(tmp_path, monkeypatch, caplog):
    a = _bib(tmp_path, "b1", [_mention("116")])
    b = _bib(tmp_path, "b2", [_mention("117")])
    calls = []

    def flaky(f, d, entity=None):
        calls.append(f)
        if f == a:
            raise OSError("disk full")
        from src.utils.file_lock import write_json_with_lock as real  # noqa: F401

        f.write_text(json.dumps(d))
        return True

    monkeypatch.setattr("src.utils.file_lock.write_json_with_lock", flaky)
    with caplog.at_level(logging.WARNING):
        assert bc.mark_held(tmp_path, [_link("116"), _link("117")]) == 1
    assert len(calls) == 2 and "held_in_corpus" in json.loads(b.read_text())
    assert "write_error" in caplog.text


def test_a_record_linking_two_volumes_is_not_guessed(tmp_path, caplog):
    f = _bib(tmp_path, "b1", [_mention("116"), _mention("117")])
    links = [_link("116"), _link("117", volume="USA-A-Utah", book="USA-A-Utah")]
    with caplog.at_level(logging.WARNING):
        assert bc.mark_held(tmp_path, links) == 0
    assert "held_in_corpus" not in json.loads(f.read_text())
    assert "conflicting=1" in caplog.text


def test_a_blocked_write_is_said(tmp_path, monkeypatch, caplog):
    _bib(tmp_path, "b1", [_mention("116")])
    monkeypatch.setattr(
        "src.utils.file_lock.write_json_with_lock", lambda *a, **k: False
    )
    with caplog.at_level(logging.WARNING):
        assert bc.mark_held(tmp_path, [_link()]) == 0
    assert "blocked=1" in caplog.text


def test_phase3_marks_the_record_end_to_end(monkeypatch, tmp_path):
    """A footnote linking a held volume -> citation -> link -> the bibliography record."""
    import phase3_enrich_data as P

    f = _bib(tmp_path, "b1", [_mention("116", chapter="chapter6full")])
    cl.record_book("USA-A-Omaha", "", [{"chapter": 2, "first_page": 20, "last_page": 40}],
                   {"source_key": "contentrepository/books/USA-A-Omaha.pdf",
                    "edition": "CMH Pub 100-11"})  # fmt: skip
    doc = {"book": "XChannel", "_source_url": XCH, "chapter_number": 6,
           "_parsed_chapter": "chapter6full", "paragraphs": [],
           "footnotes": [{"number": 116, "url": XCH + "#fn116",
                          "text": f"Taylor, _[Omaha Beachhead]({OMAHA_P31})_, p. 31."}]}  # fmt: skip
    monkeypatch.setattr(P, "_load_parsed_docs", lambda out, *a: [doc])
    monkeypatch.setattr(P, "_book_section_index", lambda out: {})
    P._process_intra_corpus_links(tmp_path)
    held = json.loads(f.read_text())["held_in_corpus"]
    assert held["book"] == "USA-A-Omaha" and held["format"] == "pdf"
    assert held["evidence"][0]["reference_number"] == "116"


def test_the_parsed_chapter_key_matches_phase2s_mention_chapter(tmp_path):
    """Phase 2 names a mention's chapter by the parsed file (pf.name minus -parsed.json)."""
    import phase3_enrich_data as P

    d = tmp_path / "content" / "XChannel"
    d.mkdir(parents=True)
    (d / "chapter6full-parsed.json").write_text('{"book": "XChannel"}')
    (doc,) = P._load_parsed_docs(tmp_path)
    assert doc["_parsed_chapter"] == "chapter6full"
