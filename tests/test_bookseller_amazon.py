"""Owner 2026-11-20: Amazon is a bibliography source like archive.org and Gutenberg, with one
difference - a bookseller listing is a pointer (to buy; the basis of an affiliate link), never an
online copy: no expectation of a download. It is found through OpenSERP (site:amazon.com, so
Amazon's own pages are never fetched), asked once per window with an empty answer recorded, and
kept under source_metadata.bookseller - not in resource_urls, and without changing search_status.
"""

from unittest import mock

import pytest

from src.enrichment import bibliography_resolver as br
from src.extraction import supplemental_search as ss

CFG = {"search_amazon": True, "use_openserp": True, "openserp_url": "http://s"}
BOOK = {
    "citation": {
        "title": "The Siegfried Line Campaign",
        "author": ["MacDonald, Charles"],
        "document_type": "book",
    },
    "verbatim_reference": "Charles B. MacDonald, The Siegfried Line Campaign",
}


def _resp(status=200, results=None):
    r = mock.Mock(status_code=status)
    r.json.return_value = {"results": results or []}
    return r


@pytest.fixture
def serp(monkeypatch):
    state = mock.Mock(answers=[], calls=[])

    def fake(provider, url, **kw):
        state.calls.append(kw["params"]["text"])
        out = state.answers.pop(0)
        if isinstance(out, Exception):
            raise out
        return out

    monkeypatch.setattr("src.utils.fetch_provenance.recorded_fetch", fake)
    monkeypatch.setattr("time.sleep", lambda s: None)
    return state


def test_the_query_is_a_site_search_for_the_title_and_author(serp):
    serp.answers = [_resp(200, [])]
    ss.search_amazon_candidates("The Siegfried Line Campaign", "MacDonald")
    assert serp.calls == ['"The Siegfried Line Campaign" MacDonald site:amazon.com']


def test_only_product_pages_on_amazon_are_candidates(serp):
    serp.answers = [
        _resp(
            200,
            [
                {
                    "url": "https://www.amazon.com/dp/B000ABC123",
                    "title": "Siegfried Line",
                },
                {"url": "https://amazon.com.evil.org/dp/B000ABC123", "title": "x"},
                {"url": "http://www.amazon.com/dp/B000ABC123", "title": "plain http"},
                {"url": "https://evil.org/?u=amazon.com", "title": "x"},
                {"url": "https://www.gutenberg.org/ebooks/1", "title": "x"},
                {
                    "url": "https://www.amazon.com/s?k=siegfried",
                    "title": "a search page",
                },
            ],
        )
    ]
    out = ss.search_amazon_candidates("The Siegfried Line Campaign")
    assert out == [("https://www.amazon.com/dp/B000ABC123", "Siegfried Line")]


def test_an_empty_amazon_answer_is_asked_once(serp):
    serp.answers = [_resp(200, [])]
    for _ in range(3):
        assert ss.search_amazon_candidates("A Long Enough Title") == []
    assert len(serp.calls) == 1


def test_an_answer_during_an_outage_is_not_recorded(serp):
    serp.answers = [_resp(503), _resp(200, [])]
    ss.search_amazon_candidates("A Long Enough Title")
    ss.search_amazon_candidates("A Long Enough Title")
    ss.search_amazon_candidates("A Long Enough Title")
    assert len(serp.calls) == 2  # the outage was asked again; the answer then kept


def test_a_verified_listing_is_a_bookseller_finding_not_an_online_copy(
    serp, monkeypatch
):
    serp.answers = [
        _resp(
            200,
            [
                {
                    "url": "https://www.amazon.com/dp/B000ABC123",
                    "title": "The Siegfried Line Campaign",
                }
            ],
        )
    ]
    monkeypatch.setattr(br, "_verify_openserp_match", lambda *a, **k: True)
    entry = {**BOOK, "search_status": "not_found"}
    br._find_bookseller(entry, mock.Mock(), CFG)
    assert entry["source_metadata"]["bookseller"] == [
        {
            "url": "https://www.amazon.com/dp/B000ABC123",
            "source": "amazon",
            "kind": "bookseller",
        }
    ]
    assert entry["source_metadata"]["findings"][-1]["kind"] == "bookseller"
    assert not entry.get("resource_urls")  # never an online copy
    assert entry["search_status"] == "not_found"  # and no change of status


def test_an_unverified_listing_is_not_kept(serp, monkeypatch):
    serp.answers = [
        _resp(
            200,
            [
                {
                    "url": "https://www.amazon.com/dp/B000ABC123",
                    "title": "The Siegfried Line Campaign",
                }
            ],
        )
    ]
    monkeypatch.setattr(br, "_verify_openserp_match", lambda *a, **k: False)
    entry = dict(BOOK)
    br._find_bookseller(entry, mock.Mock(), CFG)
    assert "bookseller" not in (entry.get("source_metadata") or {})


@pytest.mark.parametrize(
    "doc_type,nara,cfg,want",
    [("book", False, CFG, True),
     ("monograph", True, CFG, True),
     ("", False, CFG, True),            # an untyped published work
     ("", True, CFG, False),            # an archival record
     ("journal article", False, CFG, False),
     ("book", False, {**CFG, "search_amazon": False}, False),
     ("book", False, {**CFG, "use_openserp": False}, False)],
)  # fmt: skip
def test_which_entries_get_a_listing_search(doc_type, nara, cfg, want):
    assert br._wants_bookseller(dict(BOOK), doc_type, nara, cfg) is want


def test_a_found_listing_is_not_searched_for_again():
    entry = {
        **BOOK,
        "source_metadata": {
            "bookseller": [{"url": "https://www.amazon.com/dp/B000ABC123"}]
        },
    }
    assert br._wants_bookseller(entry, "book", False, CFG) is False


def test_a_book_that_already_has_a_copy_still_gets_a_listing(serp, monkeypatch):
    serp.answers = [
        _resp(
            200,
            [
                {
                    "url": "https://www.amazon.com/dp/B000ABC123",
                    "title": "The Siegfried Line Campaign",
                }
            ],
        )
    ]
    monkeypatch.setattr(br, "_verify_openserp_match", lambda *a, **k: True)
    entry = {
        **BOOK,
        "resource_urls": ["https://archive.org/details/x"],
        "search_status": "resolved",
    }
    out = br.resolve_bibliography_entry(entry, mock.Mock(), CFG)
    assert out["resource_urls"] == [
        "https://archive.org/details/x"
    ]  # the copy is untouched
    assert out["source_metadata"]["bookseller"][0]["source"] == "amazon"


def test_a_resolved_book_without_a_listing_is_not_skipped_by_the_resolver():
    """The resolver used to skip every resolved record; a book still wants its listing."""
    data = {
        **BOOK,
        "resource_urls": ["https://archive.org/details/x"],
        "search_status": "resolved",
    }
    assert br._needs_bookseller(data, CFG) is True
    done = {
        **data,
        "source_metadata": {
            "bookseller": [{"url": "https://www.amazon.com/dp/B000ABC123"}]
        },
    }
    assert br._needs_bookseller(done, CFG) is False


HOSTILE = [
    "https://evil.org\\@www.amazon.com/dp/B000ABC123",  # a browser reads \ as /: host evil.org
    "https://evil.org%5c@amazon.com/dp/B000ABC123",
    "https://evil.org@amazon.com/dp/B000ABC123",
    "https://user:pw@www.amazon.com/dp/B000ABC123",
    "https://www.amazon.com:8443/dp/B000ABC123",
    "https://www.amazon.com./dp/B000ABC123",
    "https://www.amazon.com.evil.org/dp/B000ABC123",
    "https://smile.amazon.com/dp/B000ABC123",
    "https://\uff41mazon.com/dp/B000ABC123",
    "https://www.amazon.com/dp/B000ABC123\n",
    "https://www.amazon.com/dp/B000ABC12",  # not an ASIN
    "https://www.amazon.com/review/R1",
    "javascript:alert(1)",
    "https://www.amazon.com/dp/B000ABC123/" + "a" * 400,
]


@pytest.mark.parametrize("url", HOSTILE)
def test_a_hostile_or_odd_url_is_never_a_listing(url):
    assert ss.amazon_listing_url(url) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://www.amazon.com/dp/B000ABC123",
        "https://AMAZON.com/dp/B000ABC123?tag=evil-20&ref=x#frag",
        "https://www.amazon.com/Siegfried-Line-Campaign/dp/B000ABC123/ref=sr_1_1?keywords=x",
        "https://www.amazon.com/gp/product/B000ABC123",
    ],
)
def test_a_product_page_becomes_the_canonical_listing_url(url):
    """The query string (a third party's affiliate tag), fragment and path words are dropped:
    only /dp/<ASIN> is kept, so a later affiliate link is built from the ASIN alone."""
    assert ss.amazon_listing_url(url) == "https://www.amazon.com/dp/B000ABC123"


def test_untrusted_title_text_cannot_add_search_operators(serp):
    serp.answers = [_resp(200, [])]
    ss.search_amazon_candidates(
        'Book" OR site:evil.org -site:amazon.com (x)', 'A" inurl:b'
    )
    text = serp.calls[0]
    # the words stay (harmless text); no operator survives: one site: term, none excluded
    assert "site:evil" not in text and "OR" not in text and "(" not in text
    assert text.endswith("site:amazon.com") and text.count("site:") == 1
    assert not any(w.startswith("-") for w in text.split())


def test_a_very_long_title_is_capped(serp):
    serp.answers = [_resp(200, [])]
    ss.search_amazon_candidates("word " * 500, None)
    assert len(serp.calls[0]) < 300


def test_the_search_is_stamped_and_not_repeated_within_the_window(serp, monkeypatch):
    monkeypatch.setattr(br, "_verify_openserp_match", lambda *a, **k: False)
    serp.answers = [
        _resp(
            200,
            [
                {
                    "url": "https://www.amazon.com/dp/B000ABC123",
                    "title": "The Siegfried Line Campaign",
                }
            ],
        )
    ]
    entry = dict(BOOK)
    br._find_bookseller(entry, mock.Mock(), CFG)
    assert "bookseller" not in entry["source_metadata"]
    assert entry["source_metadata"]["bookseller_searched_at"] > 0
    assert br._wants_bookseller(entry, "book", False, CFG) is False  # no repeat verify


def test_a_search_during_an_outage_is_not_stamped(serp, monkeypatch):
    serp.answers = [_resp(503)]
    entry = dict(BOOK)
    br._find_bookseller(entry, mock.Mock(), CFG)
    assert "bookseller_searched_at" not in (entry.get("source_metadata") or {})
    assert br._wants_bookseller(entry, "book", False, CFG) is True


def test_an_old_stamp_expires():
    entry = {**BOOK, "source_metadata": {"bookseller_searched_at": 1}}
    assert br._wants_bookseller(entry, "book", False, CFG) is True


def test_a_null_source_metadata_does_not_crash(serp, monkeypatch):
    monkeypatch.setattr(br, "_verify_openserp_match", lambda *a, **k: True)
    serp.answers = [
        _resp(
            200,
            [
                {
                    "url": "https://www.amazon.com/dp/B000ABC123",
                    "title": "The Siegfried Line Campaign",
                }
            ],
        )
    ]
    entry = {**BOOK, "source_metadata": None}
    br._find_bookseller(entry, mock.Mock(), CFG)
    assert entry["source_metadata"]["bookseller"][0]["source"] == "amazon"


def test_a_failure_in_the_listing_step_keeps_the_resolution(monkeypatch, caplog):
    def boom(*a, **k):
        raise RuntimeError("serp")

    monkeypatch.setattr(br, "_find_bookseller", boom)
    entry = {**BOOK, "resource_urls": ["https://archive.org/details/x"]}
    out = br.resolve_bibliography_entry(entry, mock.Mock(), CFG)
    assert out["search_status"] == "resolved" and "bookseller_failed" in caplog.text


def test_no_grok_client_means_no_unverified_listing(serp):
    serp.answers = [
        _resp(200, [{"url": "https://www.amazon.com/dp/B000ABC123", "title": "x"}])
    ]
    entry = dict(BOOK)
    br._maybe_bookseller(entry, "book", False, None, CFG)
    assert serp.calls == [] and "bookseller" not in (entry.get("source_metadata") or {})


def test_an_untyped_citation_needs_a_real_title():
    prose = {
        "verbatim_reference": "Interview with Gen X, 12 Mar 1946",
        "citation": {"title": "Unknown"},
    }
    assert br._wants_bookseller(prose, "", False, CFG) is False
    titled = {**prose, "citation": {"title": "Cross-Channel Attack"}}
    assert br._wants_bookseller(titled, "", False, CFG) is True


def test_another_records_resolution_never_replaces_a_records_own_copy(
    serp, monkeypatch
):
    """Dedup used to be skipped for records that already had a copy; they now pass through
    _resolve_or_reuse, which must not overwrite them with a not_found sibling's status.
    """
    own = {
        **BOOK,
        "resource_urls": ["https://archive.org/details/x"],
        "search_status": "resolved",
    }
    sibling = {**BOOK, "search_status": "not_found", "source_metadata": {"x": 1}}
    key = br._citation_key(own)
    calls = []
    monkeypatch.setattr(br, "resolve_bibliography_entry", lambda *a: calls.append(1))
    by_key = {key: sibling}
    reused = br._resolve_or_reuse(own, mock.Mock(), CFG, by_key)
    assert own["search_status"] == "resolved" and calls == [1]
    assert (
        reused is None and by_key[key] is own
    )  # a fresh resolution, now the one reused


# --- the schema (bibliography 2.30) -----------------------------------------------------------


def _record(meta):
    return {
        "BibliographyID": "01HX7YZABCDEFGHJKMNPQRSTVW",
        "title": "The Siegfried Line Campaign",
        "citation": {},
        "source_metadata": meta,
        "_schema_version": "2.30",
    }


def _valid(rec):
    import jsonschema

    from src.schemas.bibliography_output import BIBLIOGRAPHY_OUTPUT_SCHEMA

    try:
        jsonschema.Draft7Validator(BIBLIOGRAPHY_OUTPUT_SCHEMA).validate(rec)
        return True
    except jsonschema.ValidationError:
        return False


def test_a_bookseller_record_the_resolver_writes_is_schema_valid(
    serp, monkeypatch, tmp_path
):
    """The resolver's real output goes through the real write guard and persists."""
    from src.utils.file_lock import write_json_with_lock

    monkeypatch.setattr(br, "_verify_openserp_match", lambda *a, **k: True)
    serp.answers = [
        _resp(
            200,
            [
                {
                    "url": "https://www.amazon.com/dp/B000ABC123?tag=x",
                    "title": "The Siegfried Line Campaign",
                }
            ],
        )
    ]
    entry = _record(None)
    entry["citation"] = BOOK["citation"]
    entry["mentions"] = [{"verbatim_reference": BOOK["verbatim_reference"]}]
    br._find_bookseller(entry, mock.Mock(), CFG)
    d = tmp_path / "bibliography"
    d.mkdir()
    assert write_json_with_lock(d / "bib_1.json", entry, entity="bibliography") is True
    meta = entry["source_metadata"]
    assert meta["bookseller"][0]["url"] == "https://www.amazon.com/dp/B000ABC123"
    assert isinstance(meta["bookseller_searched_at"], int)


@pytest.mark.parametrize(
    "bad",
    [
        {"bookseller": [{"url": "https://evil.org/dp/B000ABC123", "source": "amazon", "kind": "bookseller"}]},
        {"bookseller": [{"url": "https://www.amazon.com/dp/B000ABC123?tag=evil-20", "source": "amazon", "kind": "bookseller"}]},
        {"bookseller": [{"url": "https://www.amazon.com/dp/B000ABC123", "source": "amazon", "kind": "copy"}]},
        {"bookseller": [{"url": "https://www.amazon.com/dp/B000ABC123", "source": "amazon", "kind": "bookseller", "x": 1}]},
        {"bookseller_searched_at": "yesterday"},
        {"findings": [{"url": "u", "source": "s", "kind": "download"}]},
        # a trailing newline / space / CR (Python's $ would let the newline through)
        {"bookseller": [{"url": "https://www.amazon.com/dp/B000ABC123\n", "source": "amazon", "kind": "bookseller"}]},
        {"bookseller": [{"url": "https://www.amazon.com/dp/B000ABC123 ", "source": "amazon", "kind": "bookseller"}]},
        {"bookseller": [{"url": "https://www.amazon.com/dp/B000ABC123\r", "source": "amazon", "kind": "bookseller"}]},
        # a bookseller FINDING is held to the same rule
        {"findings": [{"url": "https://evil.org/x", "source": "amazon", "kind": "bookseller"}]},
        {"findings": [{"url": "javascript:alert(1)", "source": "amazon", "kind": "bookseller"}]},
        {"findings": [{"url": "https://www.amazon.com/dp/B000ABC123", "source": "ebay", "kind": "bookseller"}]},
        {"findings": [{"source": "amazon", "kind": "bookseller"}]},
    ],
)  # fmt: skip
def test_a_malformed_bookseller_never_reaches_the_record(bad):
    assert not _valid(_record(bad))


@pytest.mark.parametrize(
    "ok",
    [
        None,
        {},
        {
            "findings": [
                {"url": "https://archive.org/details/x", "source": "archive_org"}
            ]
        },
        {
            "canonical_title": "x",
            "anything": [1, 2],
        },  # the open provenance keys stay open
        {"bookseller": None, "bookseller_searched_at": None},
        {
            "findings": [
                {
                    "url": "https://www.amazon.com/dp/B000ABC123",
                    "source": "amazon",
                    "kind": "bookseller",
                }
            ]
        },
        {"findings": [{"url": None, "source": None, "kind": None}]},
    ],
)
def test_existing_provenance_shapes_stay_valid(ok):
    assert _valid(_record(ok))
