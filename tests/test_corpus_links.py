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
             "content": f"See [Omaha]({OMAHA})."}  # fmt: skip
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
           "content": f"See [Omaha]({OMAHA})."}  # fmt: skip
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
