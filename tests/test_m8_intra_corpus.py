"""M8: in-corpus ibiblio links -> internal corpus-graph edges (ULID-attributed) + queue the cited
document as another batch to be processed."""

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


# --- edges + batch queue (enqueue stubbed; verify decisions + dedup) ---
def _content():
    return (
        "See [Utah Beach to Cherbourg](https://www.ibiblio.org/hyperwar/USA/USA-E-Utah/USA-E-Utah-3.html) "
        "and [page 329](#page329) and "
        "[same vol](https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/USA-E-XChannel-12.html) and "
        "[ext](https://en.wikipedia.org/wiki/Omaha_Beach)."
    )


def test_process_records_edges_and_queues(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        IC, "_queue_as_batch", lambda url, vol: (calls.append((url, vol)) or True)
    )
    s = IC.process_doc_links(
        _content(),
        SRC,
        "XChannel",
        out=tmp_path,
        book_to_section={"xchannel": "01HXSEC000000000000000000A"},
        corpus_volumes=set(),
        queued=set(),
    )
    # one cross-volume in-corpus doc (queued), two same-volume anchors (#page329 + other XChannel page)
    kinds = sorted(e["kind"] for e in s["edges"])
    assert kinds == ["in_corpus_doc", "same_volume_anchor", "same_volume_anchor"]
    assert s["anchors"] == 2 and s["queued"] == 1
    # ULID attribution on the edge
    doc_edge = next(e for e in s["edges"] if e["kind"] == "in_corpus_doc")
    assert doc_edge["from_SourceSectionID"] == "01HXSEC000000000000000000A"
    assert doc_edge["to_volume"] == "USA-E-Utah"
    assert calls == [
        (
            "https://www.ibiblio.org/hyperwar/USA/USA-E-Utah/USA-E-Utah-3.html",
            "USA-E-Utah",
        )
    ]


def test_dedup_already_in_corpus(monkeypatch, tmp_path):
    monkeypatch.setattr(IC, "_queue_as_batch", lambda url, vol: True)
    s = IC.process_doc_links(
        _content(),
        SRC,
        "XChannel",
        out=tmp_path,
        book_to_section={},
        corpus_volumes={"USA-E-Utah"},
        queued=set(),
    )
    assert s["queued"] == 0 and s["skipped"] >= 1  # already in corpus -> not re-queued


def test_dedup_already_queued_this_run(monkeypatch, tmp_path):
    monkeypatch.setattr(IC, "_queue_as_batch", lambda url, vol: True)
    q = {"USA-E-Utah"}
    s = IC.process_doc_links(
        _content(),
        SRC,
        "XChannel",
        out=tmp_path,
        book_to_section={},
        corpus_volumes=set(),
        queued=q,
    )
    assert s["queued"] == 0  # already queued


def test_enqueue_disabled(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(
        IC, "_queue_as_batch", lambda url, vol: called.append(1) or True
    )
    s = IC.process_doc_links(
        _content(),
        SRC,
        "XChannel",
        out=tmp_path,
        book_to_section={},
        corpus_volumes=set(),
        queued=set(),
        enqueue=False,
    )
    assert not called and s["queued"] == 0  # edges still recorded, no actual ingest
    assert any(e["kind"] == "in_corpus_doc" for e in s["edges"])


def test_failsafe_bad_input(tmp_path):
    s = IC.process_doc_links(
        None,
        None,
        "X",
        out=tmp_path,
        book_to_section={},
        corpus_volumes=set(),
        queued=set(),
    )
    assert s["queued"] == 0 and s["edges"] == []


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
