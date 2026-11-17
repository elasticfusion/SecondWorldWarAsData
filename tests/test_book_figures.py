"""A scanned book's own figures reach its chapters (src/ingestion/book_figures): built once from
its PDF and OCR, stored under contentrepository/<book>/_figures/, attached by PDF page (images)
and by page or citation (maps), and read back - never over HTTP - by the image and map download
steps. Before (USA-E-XChannel, 2026-11-15): 0 images and 0 maps from a book with 30 maps, 4
charts and 89 photographs."""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from src.ingestion import book_figures as B
from src.models import Map

BOOK = "USA-E-XChannel"
KEY = f"contentrepository/{BOOK}/_figures/files/p0551-0.png"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def test_only_a_system_figure_file_is_a_figure_key():
    assert B.is_figure_key(KEY)
    assert B.is_figure_key(
        f"contentrepository/_system/{BOOK}/_figures/files/a_1_img.webp"
    )
    for bad in (
        f"contentrepository/{BOOK}/_figures/files/../../secret.png",
        f"contentrepository/{BOOK}/_figures/files/sub/x.png",
        f"contentrepository/user-1/{BOOK}/_figures/files/x.png",  # contributor namespace
        "contentrepository/user-1/_figures/files/x.png",  # ... as the "book" segment
        "contentrepository/org-9/_figures/files/x.png",
        KEY + "\n",
        f"contentrepository/{BOOK}/_figures/figures.json",
        f"contentrepository/{BOOK}/_figures/files/x.pdf",
        f"contentrepository/{BOOK}/_figures/files/.hidden.png",
        "https://example.org/x.png",
        None,
    ):
        assert not B.is_figure_key(bad), bad


def test_a_figure_is_read_from_the_synced_content_else_from_s3(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_SYNC_ROOT", str(tmp_path))
    (tmp_path / KEY).parent.mkdir(parents=True)
    (tmp_path / KEY).write_bytes(PNG)
    assert B.read_figure(KEY) == (PNG, "png")
    (tmp_path / KEY).unlink()
    monkeypatch.setenv("S3_BUCKET", "bkt")
    client = mock.Mock()
    client.get_object.return_value = {"Body": io.BytesIO(PNG)}
    with mock.patch("boto3.client", return_value=client):
        assert B.read_figure(KEY) == (PNG, "png")
    client.get_object.assert_called_once_with(Bucket="bkt", Key=KEY)
    assert B.read_figure("contentrepository/x/other.png") == (None, None)


def test_image_and_map_downloads_read_a_figure_never_over_http(monkeypatch):
    from src.extraction import images

    monkeypatch.setattr(B, "read_figure", lambda key: (PNG, "png"))
    with mock.patch("src.utils.url_hygiene.clean_url") as http:
        assert images.fetch_image_bytes(KEY) == (PNG, "png")
    http.assert_not_called()
    monkeypatch.setattr(B, "read_figure", lambda key: (b"<html>not an image", "png"))
    assert images.fetch_image_bytes_with_reason(KEY) == (
        None,
        None,
        "figure_unreadable",
    )


# --- attaching the book's figures to its chapters -------------------------------------------------
def _doc(*paragraphs, maps=(), title="Chapter"):
    """A parsed chapter: (absolute number, PDF page, text) paragraphs."""
    return SimpleNamespace(
        chapter_title=title,
        paragraphs=[
            SimpleNamespace(absolute_number=n, page_number=page, text=text)
            for n, page, text in paragraphs
        ],
        images=[],
        maps=list(maps),
    )


def _fig(kind, page, number=None, title="", file="files/f.png", w=100, h=100, **kw):
    return {"kind": kind, "pdf_page": page, "number": number, "title": title,
            "file": file, "width": w, "height": h, "figure_id": f"F{page}{number}",
            "caption": kw.get("caption")}  # fmt: skip


REPORT = {
    "figures": [
        _fig(
            "illustration",
            289,
            title="Rundstedt",
            caption="FIELD MARSHAL VON RUNDSTEDT",
        ),
        _fig("chart", 400, number="2", title="Chain of Command", file="files/c2.png"),
        _fig(
            "map", 290, number="3", title="4th Infantry Division", file="files/m3.png"
        ),
        _fig(
            "map",
            546,
            number="VIII",
            title="101st Airborne",
            file="files/m8.png",
            w=900,
        ),
        _fig(
            "map", 546, number="VIII", title="101st Airborne", file="files/leg.png", w=9
        ),
        _fig("map", 547, number="IX", title="82d Airborne", file="files/m9.png"),
        _fig("figure", 2, title="", file="files/logo.png"),  # front matter
    ]
}


def test_a_chapter_gets_the_pictures_on_its_pages_and_the_maps_it_prints_or_cites():
    doc = _doc(
        (1, 288, "The assault began."),
        (2, 289, "The drop scattered. (*Map VIII*)"),
        (3, 291, "Later, Map VIII again."),
    )
    counts = B.attach_book([doc], REPORT, BOOK)
    # the chart (page 400) and the logo (page 2) are outside this text's pages 288-291
    assert counts == {"images": 1, "maps": 2, "unplaced": 2}
    (rundstedt,) = doc.images
    assert rundstedt.url == f"contentrepository/{BOOK}/_figures/files/f.png"
    assert (rundstedt.alt_text, rundstedt.caption) == (
        "Rundstedt",
        "FIELD MARSHAL VON RUNDSTEDT",
    )
    assert rundstedt.paragraph_number == 2  # the last paragraph on or before its page
    by_id = {m.map_id: m for m in doc.maps}
    assert set(by_id) == {"Map 3", "Map VIII"}  # Map IX: neither printed nor cited here
    m8 = by_id["Map VIII"]
    assert m8.url.endswith("files/m8.png")  # the map, not its 9-pixel legend
    assert [r["paragraph_number"] for r in m8.references] == [2, 3]
    assert (by_id["Map 3"].references, by_id["Map 3"].paragraph_number) == ([], 2)


def test_each_picture_has_one_owner_across_gaps_and_shared_pages():
    """A picture-only page has no paragraph (Phase 0 drops picture paragraphs): one between two
    chapters goes to the earlier; one on a page two sections share goes to one of them.
    """
    a = _doc((1, 280, "A."), (2, 286, "A ends."))
    b = _doc((3, 289, "B starts on the photo's page."), (4, 300, "B."))
    c = _doc((5, 300, "C shares page 300."), (6, 310, "C."))
    report = {"figures": [_fig("illustration", 288, file="files/gap.png"),
                          _fig("illustration", 300, file="files/shared.png")]}  # fmt: skip
    B.attach_book([a, b, c], report, BOOK)
    assert [i.url.rsplit("/", 1)[1] for i in a.images] == ["gap.png"]
    assert [i.url.rsplit("/", 1)[1] for i in b.images + c.images] == ["shared.png"]


def test_a_map_cited_in_two_chapters_is_one_phase3_map_with_both_footprints(tmp_path):
    from src.extraction.map_link_source import persist_map_entities

    one = _doc((1, 300, "See Map VIII."))
    two = _doc((2, 400, "Map VIII again."))
    B.attach_book([one, two], REPORT, BOOK)
    docs = [
        {"book": BOOK, "maps": [{"url": m.url, "description": m.description,
                                 "map_id": m.map_id, "references": m.references}
                                for m in d.maps]}  # fmt: skip
        for d in (one, two)
    ]
    counts = persist_map_entities(docs, tmp_path / "maps", download=False)
    assert (counts["created"], counts["existing"]) == (1, 1)
    (record,) = [
        json.loads(f.read_text())
        for f in (tmp_path / "maps").glob("*.json")
        if f.name != "index.json"
    ]
    assert sorted(r["paragraph_number"] for r in record["references"]) == [1, 2]


def test_a_map_the_text_links_already_is_not_added_twice():
    linked = Map(
        url="https://ibiblio.org/m8.jpg", description="Map VIII", map_id="Map VIII"
    )
    doc = _doc((1, 289, "See Map VIII."), maps=[linked])
    B.attach_book([doc], REPORT, BOOK)
    assert [m.map_id for m in doc.maps].count("Map VIII") == 1


def test_no_report_or_no_page_numbers_attach_nothing():
    zero = {"images": 0, "maps": 0, "unplaced": 0}
    assert B.attach_book([_doc((1, 289, "x"))], None, BOOK) == zero
    assert B.attach_book([_doc((1, None, "x"))], REPORT, BOOK) == zero


def test_maps_cited_and_none_captured_is_loud():
    from src.utils import observe

    coverage = B.Coverage(BOOK)
    coverage.add(_doc((1, 1, "The drop (*Map VIII*) and Maps XI and XII.")))
    with mock.patch.object(observe, "observe_error") as loud:
        coverage.observe()
    assert loud.call_args.args[:2] == ("figures", "maps_cited_none_captured")
    assert (
        loud.call_args.kwargs["maps_cited"] == 2
    )  # "Maps XI and XII" counts its first
    fine = B.Coverage(BOOK)
    doc = _doc((1, 289, "See Map VIII."))
    B.attach_book([doc], REPORT, BOOK)
    fine.add(doc)
    with mock.patch.object(observe, "observe_error") as loud:
        fine.observe()
    loud.assert_not_called()


# --- ensure: built once, rebuilt only when the PDF or the OCR changed ---------------------------
class _S3:
    """Serves one manifest and one chunk; records uploads (in order)."""

    def __init__(self, ocr: Path, manifest: dict):
        self.ocr, self.manifest, self.uploads = ocr, manifest, []

    def get_object(self, Bucket, Key):
        assert Key == f"ocr-output/{BOOK}/.chunks.json"
        return {"Body": io.BytesIO(json.dumps(self.manifest).encode())}

    def get_paginator(self, _name):
        ocr = self.ocr

        class P:
            def paginate(self, Bucket, Prefix):
                rel = Prefix[len(f"ocr-output/{BOOK}/") :]
                folder = ocr / rel
                yield {"Contents": [{"Key": Prefix + f.name} for f in folder.iterdir()]}

        return P()

    def download_file(self, _bucket, key, dest):
        Path(dest).write_bytes(
            (self.ocr / key[len(f"ocr-output/{BOOK}/") :]).read_bytes()
        )

    def upload_file(self, src, _bucket, key):
        self.uploads.append(key)


@pytest.fixture
def scanned(tmp_path):
    book_dir = tmp_path / "contentrepository" / BOOK
    book_dir.mkdir(parents=True)
    (book_dir / f"{BOOK}.pdf").write_bytes(b"%PDF-1.4 a book")
    chunk = tmp_path / "ocr" / "chunk-p0001-0050" / "input"
    chunk.mkdir(parents=True)
    (chunk / "input.md").write_text(
        "## Maps\n\n- I. Overlord Area.\n", encoding="utf-8"
    )
    manifest = {"submission": "S1", "chunks": ["chunk-p0001-0050"],
                "source_key": f"contentrepository/{BOOK}/{BOOK}.pdf", "validation_url": ""}  # fmt: skip
    return book_dir, _S3(tmp_path / "ocr", manifest)


def _fake_extract(pdf, chunks, front, out, *_a, **_k):
    (out / "files").mkdir(parents=True, exist_ok=True)
    (out / "files" / "p0539-0.png").write_bytes(PNG)
    report = {"pdf_sha256": B.sha256(pdf), "counts": {"figures": 1},
              "list_entries_without_figure": [], "figures": [_fig("map", 539, "I")]}  # fmt: skip
    B.write_report(out, report)
    assert [c.first_page for c in chunks] == [1] and front.entries
    return report


def test_figures_are_built_once_stored_report_last_and_reused(scanned, tmp_path):
    book_dir, s3 = scanned
    with mock.patch.object(B, "extract", side_effect=_fake_extract) as extract:
        report = B.ensure(s3, "bkt", BOOK, book_dir, tmp_path / "w1")
        assert report["ocr_submission"] == "S1" and extract.call_count == 1
        assert s3.uploads == [
            f"contentrepository/{BOOK}/_figures/files/p0539-0.png",
            f"contentrepository/{BOOK}/_figures/figures.json",  # last: names stored files only
        ]
        assert (book_dir / "_figures" / "figures.json").is_file()  # this parse's copy
        again = B.ensure(s3, "bkt", BOOK, book_dir, tmp_path / "w2")
        assert again["ocr_submission"] == "S1" and extract.call_count == 1  # reused
        s3.manifest["submission"] = "S2"  # a re-OCR: rebuilt, with the earlier IDs
        B.ensure(s3, "bkt", BOOK, book_dir, tmp_path / "w3")
        assert extract.call_count == 2


def test_a_book_that_is_not_an_ocrd_pdf_has_no_figures(scanned, tmp_path):
    book_dir, s3 = scanned
    s3.manifest["source_key"] = f"contentrepository/{BOOK}/{BOOK}.epub"
    with mock.patch.object(B, "extract") as extract:
        assert B.ensure(s3, "bkt", BOOK, book_dir, tmp_path / "w") is None
    extract.assert_not_called()


def test_a_failed_build_is_loud_and_the_parse_goes_on(scanned, tmp_path):
    from src.utils import observe

    book_dir, s3 = scanned
    with mock.patch.object(B, "extract", side_effect=RuntimeError("pdf broken")), \
            mock.patch.object(observe, "observe_error") as loud:  # fmt: skip
        assert B.ensure(s3, "bkt", BOOK, book_dir, tmp_path / "w") is None
    assert loud.call_args.args[:2] == ("figures", "build_failed")
    assert "pdf broken" in loud.call_args.kwargs["reason"]


# --- Phase 1 wiring ------------------------------------------------------------------------------
def test_phase1_builds_figures_only_inside_the_task(monkeypatch, tmp_path):
    import phase1_parse

    monkeypatch.setenv("S3_BUCKET", "bkt")
    monkeypatch.delenv("ECS_CONTAINER_METADATA_URI_V4", raising=False)
    monkeypatch.delenv("ECS_TASK_ID", raising=False)
    with mock.patch.object(B, "ensure") as ensure:
        assert phase1_parse._book_figures(BOOK, tmp_path) is None  # a local run: no S3
        (tmp_path / "_figures").mkdir()
        (tmp_path / "_figures" / "figures.json").write_text('{"figures": []}')
        assert phase1_parse._book_figures(BOOK, tmp_path) == {
            "figures": []
        }  # the stored
        ensure.assert_not_called()
        monkeypatch.setenv("ECS_TASK_ID", "t1")
        with mock.patch("boto3.client"):
            phase1_parse._book_figures(BOOK, tmp_path)
        assert ensure.call_args.args[1:4] == ("bkt", BOOK, tmp_path)


def test_phase1_gives_each_chapter_its_figures_before_saving_it(monkeypatch, tmp_path):
    import logging

    import phase1_parse

    doc = _doc((1, 289, "The drop (*Map VIII*)."))
    notes = _doc((2, 289, "Map VIII is cited in a note."), title="Footnotes")
    doc.book = notes.book = BOOK
    saved = []
    monkeypatch.setattr(phase1_parse, "parse_chapter", lambda *_a, **_k: [doc, notes])
    monkeypatch.setattr(
        phase1_parse,
        "_process_document",
        lambda d, *_a: saved.append(([m.map_id for m in d.maps], len(d.images))),
    )
    phase1_parse._parse_one_book(
        BOOK, [SimpleNamespace(chapter_number=8)], tmp_path, 0, 1, False,
        logging.getLogger("t"), figures=REPORT,
    )  # fmt: skip
    assert saved == [(["Map VIII"], 1), ([], 0)]  # a notes document gets no figures


def test_a_failed_rebuild_keeps_the_stored_figures(scanned, tmp_path):
    from src.utils import observe

    book_dir, s3 = scanned
    with mock.patch.object(B, "extract", side_effect=_fake_extract):
        built = B.ensure(s3, "bkt", BOOK, book_dir, tmp_path / "w1")
    s3.manifest["submission"] = "S2"
    with mock.patch.object(B, "extract", side_effect=OSError("disk")), \
            mock.patch.object(observe, "observe_error") as loud:  # fmt: skip
        kept = B.ensure(s3, "bkt", BOOK, book_dir, tmp_path / "w2")
    assert kept["figures"] == built["figures"]  # never a parse that drops them
    assert loud.call_args.kwargs["kept_stored"] is True


def test_a_corrupt_stored_report_only_resets_the_ids(scanned, tmp_path):
    book_dir, s3 = scanned
    (book_dir / "_figures").mkdir()
    (book_dir / "_figures" / "figures.json").write_text("{not json")
    assert B.known_ids(book_dir / "_figures") == {}
    with mock.patch.object(B, "extract", side_effect=_fake_extract) as extract:
        assert (
            B.ensure(s3, "bkt", BOOK, book_dir, tmp_path / "w")["ocr_submission"]
            == "S1"
        )
    assert extract.call_count == 1


def test_a_changed_pdf_is_rebuilt(scanned, tmp_path):
    book_dir, s3 = scanned
    with mock.patch.object(B, "extract", side_effect=_fake_extract) as extract:
        B.ensure(s3, "bkt", BOOK, book_dir, tmp_path / "w1")
        (book_dir / f"{BOOK}.pdf").write_bytes(b"%PDF-1.4 a corrected scan")
        B.ensure(s3, "bkt", BOOK, book_dir, tmp_path / "w2")
    assert extract.call_count == 2


def test_a_pdf_source_not_in_the_synced_folder_is_said(scanned, tmp_path):
    from src.utils import observe

    book_dir, s3 = scanned
    s3.manifest["source_key"] = f"contentrepository/{BOOK}/../other/{BOOK}.pdf"
    with mock.patch.object(observe, "observe_warn") as warn:
        assert B.ensure(s3, "bkt", BOOK, book_dir, tmp_path / "w") is None
    assert warn.call_args.args[:2] == ("figures", "pdf_not_local")


def test_stale_stored_figures_are_kept_and_said(scanned, tmp_path):
    from src.utils import observe

    book_dir, s3 = scanned
    with mock.patch.object(B, "extract", side_effect=_fake_extract):
        B.ensure(s3, "bkt", BOOK, book_dir, tmp_path / "w1")
    s3.manifest["source_key"] = (
        f"contentrepository/{BOOK}/{BOOK}.epub"  # no longer a PDF
    )
    with mock.patch.object(observe, "observe_warn") as warn:
        kept = B.ensure(s3, "bkt", BOOK, book_dir, tmp_path / "w2")
    assert kept["ocr_submission"] == "S1"
    assert warn.call_args.args[:2] == ("figures", "stale_figures_kept")


def test_a_document_without_page_numbers_still_gets_the_maps_it_cites():
    paged = _doc((1, 289, "Text."))
    unpaged = _doc((2, None, "See Map IX."))
    B.attach_book([paged, unpaged], REPORT, BOOK)
    assert [m.map_id for m in unpaged.maps] == ["Map IX"] and unpaged.images == []


def test_a_map_printed_on_a_gap_page_goes_to_the_earlier_document():
    a = _doc((1, 280, "A."), (2, 286, "A ends."))
    b = _doc((3, 295, "B."))
    B.attach_book(
        [a, b], {"figures": [_fig("map", 290, number="3", file="files/m3.png")]}, BOOK
    )
    assert ([m.map_id for m in a.maps], b.maps) == (["Map 3"], [])
    assert a.maps[0].paragraph_number == 2  # no citation: anchored where it is printed


def test_a_split_chapters_chunks_carry_only_their_own_figures():
    import copy

    import phase1_parse

    data = {
        "images": [{"url": "a", "paragraph_number": 1}, {"url": "b", "paragraph_number": 60},
                   {"url": "unanchored", "paragraph_number": 0}],
        "maps": [
            {"map_id": "Map VIII", "paragraph_number": 2,
             "references": [{"paragraph_number": 2}, {"paragraph_number": 61}]},
            {"map_id": "Map IX", "paragraph_number": 70, "references": []},
            {"map_id": "Map X", "paragraph_number": 0, "references": []},
        ],
    }  # fmt: skip
    first, second = copy.deepcopy(data), copy.deepcopy(data)
    phase1_parse._keep_chunk_figures(first, set(range(1, 51)), True)
    phase1_parse._keep_chunk_figures(second, set(range(48, 98)), False)
    assert [i["url"] for i in first["images"]] == ["a", "unanchored"]
    assert [i["url"] for i in second["images"]] == [
        "b"
    ]  # an unanchored one: chunk 0 only
    assert [(m["map_id"], m["references"]) for m in first["maps"]] == [
        ("Map VIII", [{"paragraph_number": 2}]),
        ("Map X", []),
    ]
    assert [(m["map_id"], m["references"]) for m in second["maps"]] == [
        ("Map VIII", [{"paragraph_number": 61}]),
        ("Map IX", []),
    ]


def test_phase1_counts_a_failed_parse_and_a_failed_save_once_per_chapter(
    monkeypatch, tmp_path
):
    import logging

    import phase1_parse

    good, bad = SimpleNamespace(chapter_number=1), SimpleNamespace(chapter_number=2)
    broken = SimpleNamespace(chapter_number=3)
    docs = {1: [_doc((1, 10, "a")), _doc((2, 11, "b"))], 2: [_doc((3, 12, "c"))]}

    def parse(group, **_k):
        if group is broken:
            raise ValueError("malformed meta")
        return docs[group.chapter_number]

    def save(doc, *_a):
        if doc in docs[2]:
            raise OSError("disk")

    for d in docs[1] + docs[2]:
        d.book = BOOK
    monkeypatch.setattr(phase1_parse, "parse_chapter", parse)
    monkeypatch.setattr(phase1_parse, "_process_document", save)
    processed, errors = phase1_parse._parse_one_book(
        BOOK, [good, bad, broken], tmp_path, 0, 3, False, logging.getLogger("t")
    )
    assert (processed, errors) == (3, 2)
