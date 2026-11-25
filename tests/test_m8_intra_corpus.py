"""M8: in-corpus ibiblio links -> internal corpus-graph edges (ULID-attributed). Nothing is fetched
or uploaded (owner 2026-11-24): the citation index (corpus_links) records them."""

import pathlib
import tempfile

import src.extraction.intra_corpus as IC

SRC = "https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/USA-E-XChannel-8.html"


# --- recognizer / classifier ---
def test_classify_link_kinds():
    assert (
        IC.classify_link(
            "https://www.ibiblio.org/hyperwar/USA/USA-E-Utah/USA-E-Utah-3.html", SRC
        )
        == "in_corpus_doc"
    )
    assert (
        IC.classify_link(
            "https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/USA-E-XChannel-12.html",
            SRC,
        )
        == "same_volume_anchor"
    )  # same volume
    assert IC.classify_link("#page329", SRC) == "same_volume_anchor"
    assert (
        IC.classify_link("https://en.wikipedia.org/wiki/Omaha_Beach", SRC) == "external"
    )


def test_volume_of():
    assert (
        IC._volume_of(
            "https://www.ibiblio.org/hyperwar/USA/USA-E-Utah/USA-E-Utah-3.html"
        )
        == "USA-E-Utah"
    )
    assert IC._volume_of("https://example.com/x") is None


# --- edges (nothing is fetched or uploaded: the citation index records them) ---
def _content():
    return (
        "See [Utah Beach to Cherbourg](https://www.ibiblio.org/hyperwar/USA/USA-E-Utah/USA-E-Utah-3.html#page12) "
        "and [page 329](#page329) and "
        "[same vol](https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/USA-E-XChannel-12.html) and "
        "[ext](https://en.wikipedia.org/wiki/Omaha_Beach)."
    )


def test_process_records_edges(monkeypatch):
    s = IC.process_doc_links(
        _content(),
        SRC,
        "XChannel",
        book_to_section={"xchannel": "01HXSEC000000000000000000A"},
    )
    # one cross-volume citation, two same-volume anchors (#page329 + another XChannel page)
    kinds = sorted(e["kind"] for e in s["edges"])
    assert kinds == ["in_corpus_doc", "same_volume_anchor", "same_volume_anchor"]
    assert s["anchors"] == 2 and s["cited"] == 1
    doc_edge = next(e for e in s["edges"] if e["kind"] == "in_corpus_doc")
    assert doc_edge["from_SourceSectionID"] == "01HXSEC000000000000000000A"
    assert doc_edge["to_volume"] == "USA-E-Utah" and doc_edge["to_page"] == "page12"


def test_nothing_is_fetched_or_uploaded(monkeypatch):
    monkeypatch.setattr("src.utils.url_hygiene.fetch_text_safe", lambda *a, **k: 1 / 0)
    monkeypatch.setattr(
        "src.enrichment.fetch_review._s3_client_and_bucket", lambda: 1 / 0
    )
    IC.process_doc_links(_content(), SRC, "XChannel", book_to_section={})
    assert not hasattr(IC, "_queue_as_batch") and not hasattr(IC, "_upload_batch_item")


def test_a_book_imported_from_its_index_page_is_its_own_volume():
    """Most HyperWar books' source URL is the volume's index.html: their own pages are
    navigation, not citations of another volume (they used to be re-uploaded as one-page books).
    """
    index = "https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/index.html"
    own = "https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/USA-E-XChannel-12.html"
    assert IC._volume_of(index) == "USA-E-XChannel"
    assert IC.classify_link(own, index) == "same_volume_anchor"
    assert IC._volume_of("https://www.ibiblio.org/hyperwar/USA/index.html") is None


def test_failsafe_bad_input():
    s = IC.process_doc_links(None, None, "X", book_to_section={})
    assert s["cited"] == 0 and s["edges"] == []


def test_corpus_volumes_present(tmp_path):
    import json

    sec = tmp_path / "source_section"
    sec.mkdir()
    (sec / "a.json").write_text(
        json.dumps({"SourceSectionID": "x", "source": {"book": "USA-E-XChannel"}})
    )
    (sec / "b.json").write_text(
        json.dumps(
            {
                "SourceSectionID": "y",
                "source": {
                    "source_url": "https://www.ibiblio.org/hyperwar/USA/USA-E-Utah/USA-E-Utah-1.html"
                },
            }
        )
    )
    vols = IC._corpus_volumes_present(tmp_path)
    assert "USA-E-XChannel" in vols and "USA-E-Utah" in vols
