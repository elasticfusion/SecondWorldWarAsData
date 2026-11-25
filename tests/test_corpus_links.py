"""The citation index of HyperWar volumes (owner 2026-11-24): what each book is, what it cites, and
the list of cited volumes to ingest; matched by volume key, never by slug."""

import pytest

from src.extraction import corpus_links as cl

UTAH = "https://www.ibiblio.org/hyperwar/USA/USA-A-Utah/USA-A-Utah-3.html#page12"
OMAHA = "https://www.ibiblio.org/hyperwar/USA/USA-A-Omaha/USA-A-Omaha-1.html"
XCH_INDEX = "https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/index.html"


def _edge(url, vol, kind="in_corpus_doc", page=None):
    return {
        "from_SourceSectionID": "01HXSEC000000000000000000A",
        "to_url": url,
        "to_volume": vol,
        "to_page": page,
        "kind": kind,
    }


def _read(path):
    return cl._store().read_json(path)


def test_a_book_records_what_it_is_and_what_it_cites():
    out = cl.record_book(
        "XChannel",
        XCH_INDEX,
        [
            {"chapter": "8", "edges": [_edge(UTAH, "USA-A-Utah", page="page12"),
                                        _edge(UTAH, "USA-A-Utah", page="page12")]},  # fmt: skip
            {"chapter": "9", "edges": [_edge(OMAHA, "USA-A-Omaha")]},
        ],
    )
    assert out == {"volumes_cited": 2, "citations": 2}  # the repeat in chapter 8 is one
    assert _read("volumes/XChannel.json")["volume"] == "USA-E-XChannel"
    utah = _read("citations/USA-A-Utah/XChannel.json")
    assert (
        utah["volume_index_url"]
        == "https://www.ibiblio.org/hyperwar/USA/USA-A-Utah/index.html"
    )
    (c,) = utah["citations"]
    assert c == {
        "from_SourceSectionID": "01HXSEC000000000000000000A",
        "chapter": "8",
        "chapter_title": None,
        "to_url": UTAH,
        "to_page": "page12",
        "reference_number": None,
        "parsed_chapter": None,
        "parsed_book": None,
    }


def test_links_to_its_own_volume_and_anchors_are_not_citations():
    own = "https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/USA-E-XChannel-12.html"
    out = cl.record_book(
        "CrossChannelAttack",
        XCH_INDEX,
        [{"chapter": "1", "edges": [_edge(own, "USA-E-XChannel"),
                                     _edge("#page3", None, kind="same_volume_anchor")]}],  # fmt: skip
    )
    assert out == {"volumes_cited": 0, "citations": 0}


def test_the_ingest_list_names_pending_volumes_most_cited_first_and_held_ones_by_key():
    cl.record_book("XChannel", XCH_INDEX, [{"chapter": "8", "edges": [
        _edge(UTAH, "USA-A-Utah"), _edge(OMAHA, "USA-A-Omaha"),
        _edge(OMAHA + "#page2", "USA-A-Omaha")]}])  # fmt: skip
    # a book whose slug is not its volume key, holding a cited volume
    cl.record_book(
        "UtahBeachToCherbourg",
        "https://www.ibiblio.org/hyperwar/USA/USA-A-Utah/index.html",
        [],
    )
    listing = cl.build_ingest_list()
    vols = {v["volume"]: v for v in listing["volumes"]}
    assert listing["pending"] == 1 and listing["held"] == 1
    assert listing["volumes"][0]["volume"] == "USA-A-Omaha"  # pending first
    assert vols["USA-A-Omaha"]["links"] == 2 and vols["USA-A-Omaha"]["cited_by"] == [
        "XChannel"
    ]
    assert vols["USA-A-Utah"]["status"] == "held"
    assert vols["USA-A-Utah"]["held_as"] == ["UtahBeachToCherbourg"]
    assert _read(cl.INGEST_LIST)["pending"] == 1


def test_a_volume_held_in_this_tasks_corpus_counts_as_held():
    cl.record_book(
        "XChannel",
        XCH_INDEX,
        [{"chapter": "8", "edges": [_edge(OMAHA, "USA-A-Omaha")]}],
    )
    listing = cl.build_ingest_list(also_held={"USA-A-Omaha"})
    assert listing["pending"] == 0 and listing["volumes"][0]["status"] == "held"


def test_a_volume_no_longer_cited_is_dropped_on_the_next_run():
    cl.record_book(
        "XChannel",
        XCH_INDEX,
        [{"chapter": "8", "edges": [_edge(OMAHA, "USA-A-Omaha")]}],
    )
    cl.record_book(
        "XChannel", XCH_INDEX, [{"chapter": "8", "edges": [_edge(UTAH, "USA-A-Utah")]}]
    )
    assert [v["volume"] for v in cl.build_ingest_list()["volumes"]] == ["USA-A-Utah"]


def test_unsafe_names_never_become_paths():
    cl.record_book(
        "../evil/book",
        XCH_INDEX,
        [{"chapter": "1", "edges": [_edge(OMAHA, "../../x")]}],
    )
    paths = cl._list(cl._store(), "citations") + cl._list(cl._store(), "volumes")
    assert all(".." not in p for p in paths) and len(paths) == 2


def test_a_failed_write_is_said(monkeypatch, caplog):
    import logging

    class _Broken:
        def write_json(self, *a):
            raise OSError("denied")

        def list_files(self, *a):
            return []

    monkeypatch.setattr(cl, "_store", lambda: _Broken())
    with caplog.at_level(logging.ERROR):
        cl.record_book("XChannel", XCH_INDEX, [])
    assert "OBSERV corpus_links.write_failed" in caplog.text


def test_phase3_indexes_without_uploading(monkeypatch, tmp_path):
    import phase3_enrich_data as P

    monkeypatch.setattr(
        P,
        "_load_parsed_docs",
        lambda out, *a: [
            {"book": "XChannel", "_source_url": XCH_INDEX, "chapter": "8",
             "paragraphs": [{"text": f"See [Omaha]({OMAHA})."}]}  # fmt: skip
        ],
    )
    monkeypatch.setattr(P, "_doc_link_content", lambda d: d["content"])
    monkeypatch.setattr(P, "_book_section_index", lambda out: {})
    assert P._process_intra_corpus_links(tmp_path) == 1
    assert cl.build_ingest_list()["volumes"][0]["volume"] == "USA-A-Omaha"


def test_two_chapters_citing_one_url_are_two_citations():
    """Citations are keyed on Phase 1's chapter id: chapters sharing a title (or having none)
    must not collapse into one."""
    out = cl.record_book(
        "XChannel",
        XCH_INDEX,
        [
            {"chapter": "chapter8a", "chapter_title": "D Day", "edges": [_edge(OMAHA, "USA-A-Omaha")]},
            {"chapter": "chapter8b", "chapter_title": "D Day", "edges": [_edge(OMAHA, "USA-A-Omaha")]},
        ],  # fmt: skip
    )
    assert out["citations"] == 2
    cites = _read("citations/USA-A-Omaha/XChannel.json")["citations"]
    assert {c["chapter"] for c in cites} == {"chapter8a", "chapter8b"}


def test_phase3_keys_citations_on_the_chapter_number(monkeypatch, tmp_path):
    import phase3_enrich_data as P

    doc = {"book": "XChannel", "_source_url": XCH_INDEX, "chapter_title": "D Day",
           "paragraphs": [{"text": f"See [Omaha]({OMAHA})."}]}  # fmt: skip
    monkeypatch.setattr(
        P,
        "_load_parsed_docs",
        lambda out, *a: [
            dict(doc, chapter_number="chapter8a"),
            dict(doc, chapter_number="chapter8b"),
        ],
    )
    monkeypatch.setattr(P, "_doc_link_content", lambda d: d["content"])
    monkeypatch.setattr(P, "_book_section_index", lambda out: {})
    P._process_intra_corpus_links(tmp_path)
    cites = _read("citations/USA-A-Omaha/XChannel.json")["citations"]
    assert sorted(c["chapter"] for c in cites) == ["chapter8a", "chapter8b"]


def test_a_book_with_an_unreadable_document_keeps_its_last_index(
    monkeypatch, tmp_path, caplog
):
    import logging

    import phase3_enrich_data as P
    from src.extraction import intra_corpus as IC

    cl.record_book(
        "XChannel",
        XCH_INDEX,
        [{"chapter": "c1", "edges": [_edge(OMAHA, "USA-A-Omaha")]}],
    )
    monkeypatch.setattr(P, "_load_parsed_docs", lambda out, *a: [{"book": "XChannel"}])
    monkeypatch.setattr(P, "_doc_link_content", lambda d: "")
    monkeypatch.setattr(P, "_book_section_index", lambda out: {})
    monkeypatch.setattr(
        IC,
        "process_doc_links",
        lambda *a, **k: {"edges": [], "anchors": 0, "cited": 0, "error": True},
    )
    with caplog.at_level(logging.WARNING):
        assert P._process_intra_corpus_links(tmp_path) == 0
    assert "OBSERV corpus_links.book_skipped" in caplog.text
    assert _read("citations/USA-A-Omaha/XChannel.json")["citations"]  # not wiped


@pytest.mark.parametrize(
    "url",
    ["javascript:x//http://www.ibiblio.org/hyperwar/USA/USA-A-Omaha/USA-A-Omaha-1.html",
     "http://internal.host/hyperwar/USA/USA-A-Omaha/USA-A-Omaha-1.html"],  # fmt: skip
)
def test_only_an_ibiblio_web_url_gives_a_volume_index_url(url):
    assert cl.volume_index_url(url, "USA-A-Omaha") is None
    assert cl.volume_index_url(OMAHA, "USA-A-Omaha") == (
        "https://www.ibiblio.org/hyperwar/USA/USA-A-Omaha/index.html"
    )


def test_a_book_whose_source_is_not_a_volume_is_said(caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        cl.record_book("Upload", "https://example.org/x.pdf", [])
    assert "OBSERV corpus_links.book_volume_unknown" in caplog.text


def test_the_number_of_volumes_per_book_is_capped(monkeypatch, caplog):
    import logging

    monkeypatch.setattr(cl, "_MAX_VOLUMES_PER_BOOK", 2)
    edges = [
        _edge(f"https://www.ibiblio.org/hyperwar/USA/V-{n}/V-{n}-1.html", f"V-{n}")
        for n in range(4)
    ]
    with caplog.at_level(logging.WARNING):
        out = cl.record_book("XChannel", XCH_INDEX, [{"chapter": "c", "edges": edges}])
    assert out["volumes_cited"] == 2 and "too_many_volumes" in caplog.text


# --- citations connected to the defining copy (PDF first; edition + chapter; page as cited) ----

SIEGFRIED = "https://www.ibiblio.org/hyperwar/USA/USA-E-Siegfried/index.html"
CITE_329 = (
    "https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/USA-E-XChannel-10.html#page329"
)


def _hold_xchannel():
    """The PDF book and two identical HTML transcriptions of USA-E-XChannel."""
    pdf_chapters = [
        {"chapter": 9, "chapter_title": "The Assault", "first_page": 300, "last_page": 328},
        {"chapter": 10, "chapter_title": "The V Corps Lodgment", "first_page": 329, "last_page": 350},
    ]  # fmt: skip
    cl.record_book("USA-E-XChannel", "", pdf_chapters,
                   {"edition": "CMH Pub 7-4-1", "copyright_date": "1951",
                    "source_key": "contentrepository/books/USA-E-XChannel.pdf"})  # fmt: skip
    for slug in ("XChannel", "CrossChannelAttack"):
        cl.record_book(slug, XCH_INDEX, [
            {"chapter": 10, "chapter_title": "THE V CORPS LODGMENT", "first_page": 327, "last_page": 349}],  # fmt: skip
            {"copyright_date": "1951", "source_format": "html"})


def _cite_from_siegfried(url=CITE_329, page="page329"):
    cl.record_book("TheSiegfriedLineCampaign", SIEGFRIED,
                   [{"chapter": 4, "edges": [_edge(url, "USA-E-XChannel", page=page)]}])  # fmt: skip


def test_the_pdf_defines_the_volume_and_the_transcriptions_support_it():
    _hold_xchannel()
    _cite_from_siegfried()
    listing = cl.build_ingest_list()
    (xch,) = [v for v in listing["volumes"] if v["volume"] == "USA-E-XChannel"]
    assert xch["defining_copy"] == "USA-E-XChannel"
    assert xch["held_as"] == ["CrossChannelAttack", "USA-E-XChannel", "XChannel"]
    (link,) = _read("links/TheSiegfriedLineCampaign.json")["links"]
    from src.utils.deterministic_ids import book_id

    assert link["cited"]["book"] == "USA-E-XChannel"
    assert link["cited"]["BookID"] == book_id("USA-E-XChannel")
    assert (
        link["cited"]["format"] == "pdf" and link["cited"]["edition"] == "CMH Pub 7-4-1"
    )
    assert link["cited"]["chapter_number"] == 10 and link["match"] == "chapter"
    assert link["page_as_cited"] == 329  # kept as the citing book printed it
    assert link["supporting_copies"] == ["CrossChannelAttack", "XChannel"]
    assert link["citing"]["book"] == "TheSiegfriedLineCampaign"


def test_with_only_html_held_the_chapter_is_found_and_the_edition_is_unknown():
    for slug in ("XChannel", "CrossChannelAttack"):
        cl.record_book(slug, XCH_INDEX, [
            {"chapter": 10, "chapter_title": "THE V CORPS LODGMENT", "first_page": 327, "last_page": 349}],  # fmt: skip
            {"source_format": "html"})
    _cite_from_siegfried()
    cl.build_ingest_list()
    (link,) = _read("links/TheSiegfriedLineCampaign.json")["links"]
    assert link["cited"]["book"] == "CrossChannelAttack"  # stable tiebreak: first slug
    assert link["cited"]["format"] == "html" and link["cited"]["edition"] is None
    assert link["cited"]["chapter_number"] == 10 and link["match"] == "chapter"


def test_a_citation_without_a_page_links_to_the_volume_only():
    _hold_xchannel()
    _cite_from_siegfried(url=CITE_329.split("#")[0], page=None)
    cl.build_ingest_list()
    (link,) = _read("links/TheSiegfriedLineCampaign.json")["links"]
    assert link["match"] == "volume" and link["cited"]["chapter_number"] is None
    assert link["page_as_cited"] is None


def test_a_page_outside_every_chapter_is_not_guessed():
    _hold_xchannel()
    _cite_from_siegfried(page="page999")
    cl.build_ingest_list()
    (link,) = _read("links/TheSiegfriedLineCampaign.json")["links"]
    assert link["match"] == "volume" and link["page_as_cited"] == 999


def test_links_to_a_volume_no_longer_held_are_removed():
    _hold_xchannel()
    _cite_from_siegfried()
    cl.build_ingest_list()
    _cite_from_siegfried(url=OMAHA, page=None)  # now cites only a pending volume
    cl.record_book("TheSiegfriedLineCampaign", SIEGFRIED,
                   [{"chapter": 4, "edges": [_edge(OMAHA, "USA-A-Omaha")]}])  # fmt: skip
    cl.build_ingest_list()
    assert cl._list(cl._store(), "links") == []


@pytest.mark.parametrize(
    "key,declared,want",
    [("contentrepository/books/X.pdf", None, "pdf"), (None, "html", "html"),
     ("contentrepository/x/a.epub", None, "epub"), (None, None, "unknown"),
     ("ocr-output/USA-E.X/", None, "unknown"),  # a prefix, not a file: no extension
     ("contentrepository/x/a.pdf", "html", "pdf")],  # the object wins over a declaration  # fmt: skip
)
def test_the_source_format(key, declared, want):
    assert cl.source_format(key, declared) == want


def test_a_pdf_book_named_like_its_volume_holds_it():
    assert cl.own_volume("USA-E-XChannel", "") == ("USA-E-XChannel", "slug")
    assert cl.own_volume("USA-E-XChannel-8", "") == ("USA-E-XChannel", "slug")
    assert cl.own_volume("Upload", "") == (None, "unknown")
    assert cl.own_volume("XChannel", XCH_INDEX) == ("USA-E-XChannel", "source_url")


def test_phase3_carries_the_edition_and_page_ranges(monkeypatch, tmp_path):
    import phase3_enrich_data as P

    doc = {"book": "USA-E-XChannel", "_source_url": "", "chapter_number": 10,
           "chapter_title": "The V Corps Lodgment", "edition": "CMH Pub 7-4-1",
           "source_key": "contentrepository/books/USA-E-XChannel.pdf", "content": "",
           "paragraphs": [{"page_number": 329}, {"page_number": 340, "page_end": 341}]}  # fmt: skip
    monkeypatch.setattr(P, "_load_parsed_docs", lambda out, *a: [doc])
    monkeypatch.setattr(P, "_doc_link_content", lambda d: d["content"])
    monkeypatch.setattr(P, "_book_section_index", lambda out: {})
    P._process_intra_corpus_links(tmp_path)
    rec = _read("volumes/USA-E-XChannel.json")
    assert rec["format"] == "pdf" and rec["edition"] == "CMH Pub 7-4-1"
    assert rec["chapters"] == [{"chapter_number": 10, "chapter_title": "The V Corps Lodgment",
                                "first_page": 329, "last_page": 341}]  # fmt: skip


def test_the_parsed_document_carries_the_books_edition(tmp_path):
    from src.parser import parse_metadata

    meta = tmp_path / "chapter1-meta.yaml"
    meta.write_text(
        "book: USA-E-XChannel\ncopyright_date: '1951'\nedition: CMH Pub 7-4-1\n"
        "source_key: contentrepository/books/USA-E-XChannel.pdf\n"
    )
    m = parse_metadata(meta)
    assert (m.edition, m.copyright_date) == ("CMH Pub 7-4-1", "1951")
    assert m.source_key.endswith(".pdf")


def test_a_page_shared_by_two_chapters_is_not_guessed():
    cl.record_book("USA-E-XChannel", "", [
        {"chapter": 9, "chapter_title": "The Assault", "first_page": 300, "last_page": 329},
        {"chapter": 10, "chapter_title": "The V Corps Lodgment", "first_page": 329, "last_page": 350},
    ], {"source_key": "contentrepository/books/USA-E-XChannel.pdf"})  # fmt: skip
    _cite_from_siegfried()
    cl.build_ingest_list()
    (link,) = _read("links/TheSiegfriedLineCampaign.json")["links"]
    assert link["match"] == "volume" and link["cited"]["chapter_number"] is None
    assert link["candidate_chapters"] == [9, 10] and link["page_as_cited"] == 329


def test_a_chapter_match_says_whose_pagination_it_used():
    _hold_xchannel()
    _cite_from_siegfried()
    cl.build_ingest_list()
    (link,) = _read("links/TheSiegfriedLineCampaign.json")["links"]
    assert link["match_basis"] == "page_in_defining_copy"
    assert link["page_reference"] == "page329" and link["copy_recorded_at"]
    assert link["cited"]["volume_basis"] == "slug"


@pytest.mark.parametrize(
    "book,url",
    [("USA-Something", ""), ("US-Army", ""), ("WWII-Diary", ""),
     ("USA-E-XChannel", "https://example.org/a.pdf")],  # fmt: skip
)
def test_a_name_alone_does_not_claim_a_volume(book, url):
    assert cl.own_volume(book, url) == (None, "unknown")


def test_a_copy_of_unknown_format_never_defines_a_volume():
    old = {"book": "A-old", "volume": "V"}  # a record written before formats were kept
    html = {"book": "B-html", "volume": "V", "format": "html"}
    assert cl.defining_copy([old, html]) == "B-html"
    assert cl.defining_copy([old, html, {"book": "C", "format": "pdf"}]) == "C"


def test_an_ibiblio_source_url_alone_does_not_make_a_book_html():
    """A re-parsed PDF of a HyperWar volume has an ibiblio source URL and no marker."""
    cl.record_book("USA-E-XChannel", XCH_INDEX, [], {"source_key": None})
    assert _read("volumes/USA-E-XChannel.json")["format"] == "unknown"


def test_phase3_marks_unmarked_parsed_documents_as_unknown_format(
    monkeypatch, tmp_path
):
    import phase3_enrich_data as P

    old_doc = {
        "book": "XChannel",
        "_source_url": XCH_INDEX,
        "chapter_number": 1,
        "content": "",
    }
    monkeypatch.setattr(P, "_load_parsed_docs", lambda out, *a: [old_doc])
    monkeypatch.setattr(P, "_doc_link_content", lambda d: d["content"])
    monkeypatch.setattr(P, "_book_section_index", lambda out: {})
    P._process_intra_corpus_links(tmp_path)
    assert _read("volumes/XChannel.json")["format"] == "unknown"


def test_an_unreadable_citation_record_deletes_no_link_file(monkeypatch):
    _hold_xchannel()
    _cite_from_siegfried()
    cl.build_ingest_list()
    store = cl._store()
    real = store.read_json

    def flaky(path):
        if path.startswith("citations/"):
            raise OSError("throttled")
        return real(path)

    monkeypatch.setattr(store, "read_json", flaky)
    monkeypatch.setattr(cl, "_store", lambda: store)  # the store the step uses
    listing = cl.build_ingest_list()
    assert listing["volumes"] == []  # the failure was real: no citation was read
    assert cl._list(store, "links") == ["links/TheSiegfriedLineCampaign.json"]


def test_the_parsed_document_carries_the_declared_format(tmp_path):
    from src.parser import parse_metadata

    meta = tmp_path / "chapter1-meta.yaml"
    meta.write_text("book: XChannel\nsource_format: html\n")
    assert parse_metadata(meta).source_format == "html"


def test_the_hyperwar_import_declares_html():
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "ihh", "scripts/import_hyperwar_html.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, ".")
    spec.loader.exec_module(mod)
    meta = {"series": "S", "book": "B", "author": "A", "license": "PD",
            "copyright_date": "1951", "source_url": XCH_INDEX}  # fmt: skip
    assert "source_format: html" in mod.create_chapter_meta(meta, 1, "Forward")


def test_two_sections_with_the_same_note_number_and_url_are_two_citations():
    out = cl.record_book(
        "BreakoutAndPursuit",
        XCH_INDEX,
        [
            {"chapter": 8, "edges": [dict(_edge(OMAHA, "USA-A-Omaha"), reference_number="4",
                                          parsed_chapter="chapter8a")]},
            {"chapter": 8, "edges": [dict(_edge(OMAHA, "USA-A-Omaha"), reference_number="4",
                                          parsed_chapter="chapter8b")]},
        ],  # fmt: skip
    )
    assert out["citations"] == 2
