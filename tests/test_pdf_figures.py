"""Figures of a scanned book PDF from the PDF itself (src/ingestion/pdf_figures), on a generated
book shaped like the CMH Cross-Channel Attack PDF: a 150 dpi full-page scan with a photograph
(Chandra renders at 300 dpi, so its crop is a 2x upsample), a fold-out map plate stored as one
image, a blank leaf, and the front matter's lists of maps / illustrations with photo credits.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from collections import Counter
from pathlib import Path

import pytest

from src.ingestion import pdf_figures as F

fitz = pytest.importorskip("fitz")
Image = pytest.importorskip("PIL.Image")

SEP = "\n\n{n}" + "-" * 48 + "\n\n"

FRONT = """## Maps

<table><tbody><tr><td>1.</td><td>Situation in Europe, 6 June 1944 . . . . .</td><td>268</td></tr>
</tbody></table>

Maps I-II are in accompanying map envelope

- I. Overlord Area.
- II. The Airborne Assault, 6 June 1944.

## Illustrations

<table><tbody><tr><td>President Roosevelt and Prime Minister Churchill . . . . .</td>
<td style="text-align: right;">7</td></tr></tbody></table>

Illustrations are from the following sources:

- U.S. Army Photos, pages: 7, 14
- Captured German Photos, pages: 55

## Next
"""


def test_the_books_lists_and_credits():
    front = F.front_matter(FRONT)
    assert [(e.kind, e.number, e.title, e.printed_page) for e in front.entries] == [
        ("map", "1", "Situation in Europe, 6 June 1944", 268),
        ("map", "I", "Overlord Area", None),
        ("map", "II", "The Airborne Assault, 6 June 1944", None),
        ("illustration", None, "President Roosevelt and Prime Minister Churchill", 7),
    ]
    assert front.credits == {7: ["U.S. Army Photos"], 14: ["U.S. Army Photos"],
                             55: ["Captured German Photos"]}  # fmt: skip


def test_every_image_of_a_paragraph_and_the_books_caption():
    md = (
        "# THE AIRBORNE ASSAULT\n\n![Scale bar](a.webp)![A model-written map description](b.webp)"
        "\n\n**PARACHUTE TROOPS** *marching onto airfield.*"
    )
    images = F.chandra_images(md, 9)
    assert [(i.pdf_page, i.position, i.file) for i in images] == [
        (9, 0, "a.webp"),
        (9, 1, "b.webp"),
    ]
    assert (
        images[1].alt == "A model-written map description"
    )  # kept apart: never the caption
    assert images[0].caption == "PARACHUTE TROOPS marching onto airfield."
    assert "THE AIRBORNE ASSAULT" in images[0].page_text


def test_printed_pages_are_inferred_only_where_neighbours_agree():
    printed = {20: 1, 21: 2, 23: 4, 30: 11, 31: 0, 32: 13, 33: 14, 40: 30}
    consistent = F.consistent_printed(printed)
    assert 31 not in consistent  # a map's "0" label read as a running head
    inferred = F.infer_printed(printed, 45)
    assert inferred[22] == 3  # between 21 (offset 19) and 23 (offset 19)
    assert inferred[31] == 12  # the dropped "0" replaced by the sequence's number
    assert 35 not in inferred  # 33 (offset 19) and 40 (offset 10) disagree


def _pattern(width, height, seed):
    """A deterministic grey picture with structure (so it can be located)."""
    im = Image.new("L", (width, height))
    px = im.load()
    for y in range(height):
        for x in range(width):
            px[x, y] = (x * 7 + y * 13 + seed * 31 + (x // 9) * (y // 7) * 5) % 256
    return im


def test_a_crop_is_found_in_the_stored_page_image():
    native = Image.new("L", (300, 400), 230)
    photo = _pattern(120, 90, 3)
    native.paste(photo, (60, 150))
    crop = photo.resize((240, 180), Image.LANCZOS)  # Chandra's 2x render
    box, score = F.locate(native, crop, 0.5)
    assert box == (60, 150, 180, 240) and score < 10


# --- a generated scanned book --------------------------------------------------------------------

PAGE_W, PAGE_H = 4.0, 5.0  # inches


def _page_image(doc, image, dpi_box=None):
    page = doc.new_page(width=PAGE_W * 72, height=PAGE_H * 72)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    page.insert_image(page.rect, stream=buffer.getvalue())
    return page


@pytest.fixture
def book(tmp_path):
    """PDF pages 1-3 (printed 7, -, -): a 150 dpi page with a photo, a fold-out plate, a blank
    leaf; and the Chandra chunk of them (300 dpi crops) plus a front-matter chunk."""
    native = Image.new("L", (600, 750), 235)  # 150 dpi page
    photo = _pattern(300, 220, 1)
    native.paste(photo, (150, 120))
    plate = _pattern(600, 750, 2).convert("RGB")  # the plate is the whole stored page
    blank = Image.new("L", (600, 750), 255)
    doc = fitz.open()
    for im in (native, plate, blank):
        _page_image(doc, im)
    pdf = tmp_path / "book.pdf"
    doc.save(str(pdf))

    chunk = tmp_path / "ocr" / "chunk-p0001-0003" / "input"
    chunk.mkdir(parents=True)
    photo.resize((600, 440), Image.LANCZOS).save(chunk / "photo.webp", lossless=True)
    plate.resize((1200, 1500), Image.LANCZOS).save(chunk / "plate.webp", lossless=True)
    _pattern(80, 40, 5).save(chunk / "scale.webp", lossless=True)
    blank.resize((1200, 1500)).save(chunk / "blank.webp", lossless=True)
    md = (
        "![Roosevelt and Churchill seated](photo.webp)\n\n"
        "PRESIDENT ROOSEVELT AND PRIME MINISTER CHURCHILL *at Casablanca.*"
        + SEP.format(n=1)
        + "# THE AIRBORNE ASSAULT\n\n6 June 1944\n\n![A scale bar](scale.webp)"
        "![A model-written description](plate.webp)"
        + SEP.format(n=2)
        + "![Blank white page](blank.webp)"
    )
    (chunk / "input.md").write_text(md, encoding="utf-8")
    meta = {
        "pages": [{"page_num": i, "page_box": [0, 0, 1200, 1500]} for i in range(3)]
    }
    (chunk / "input_metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    front = tmp_path / "ocr" / "chunk-p0000-0000" / "input"  # sorts first
    front.mkdir(parents=True)
    (front / "input.md").write_text(FRONT, encoding="utf-8")
    (front / "input_metadata.json").write_text(
        json.dumps({"pages": []}), encoding="utf-8"
    )
    return pdf, tmp_path / "ocr", chunk


def _build(pdf, chunk, out, known=None):
    with fitz.open(str(pdf)) as doc:
        return F.build_figures(
            doc, [F.Chunk(1, chunk)], F.front_matter(FRONT), {1: 7}, out, known
        )


def test_figures_come_from_the_pdf_at_native_resolution(book, tmp_path):
    pdf, _ocr, chunk = book
    figures = _build(pdf, chunk, tmp_path / "out")
    by_page = {f.pdf_page: f for f in figures}
    assert sorted(by_page) == [1, 2]  # the blank leaf is no figure
    photo, plate = by_page[1], by_page[2]
    # the photo is cut from the 150 dpi stored page where Chandra's crop matches - not the
    # upsampled 600x440 crop, not the whole page with its text
    assert photo.source == "pdf_page_image" and (photo.width, photo.height) == (
        300,
        220,
    )
    assert (
        photo.kind == "illustration"
        and photo.match == "page"
        and photo.printed_page == 7
    )
    assert photo.title == "President Roosevelt and Prime Minister Churchill"
    assert photo.caption.startswith("PRESIDENT ROOSEVELT AND PRIME MINISTER CHURCHILL")
    assert photo.machine_description == "Roosevelt and Churchill seated"
    assert photo.credit == ["U.S. Army Photos"]
    sizes = {c["source"]: (c["width"], c["height"]) for c in photo.candidates}
    assert sizes == {"pdf_page_image": (300, 220), "chandra_crop": (600, 440)}
    # the plate: unnumbered page, its printed title in the list -> the whole stored image
    assert plate.source == "pdf_page_image" and (plate.width, plate.height) == (
        600,
        750,
    )
    assert (plate.kind, plate.number, plate.title, plate.match) == (
        "map", "II", "The Airborne Assault, 6 June 1944", "title",
    )  # fmt: skip
    assert [p["file"] for p in plate.parts] == ["scale.webp", "plate.webp"]
    assert (tmp_path / "out" / plate.file).is_file()


def test_a_re_run_keeps_the_figure_ids(book, tmp_path):
    pdf, _ocr, chunk = book
    first = _build(pdf, chunk, tmp_path / "out")
    known = {(f.pdf_page, f.position): f.figure_id for f in first}
    again = _build(pdf, chunk, tmp_path / "out", known)
    assert [f.figure_id for f in again] == [f.figure_id for f in first]


def _figure(**kw):
    base = dict(
        figure_id="X", pdf_page=1, printed_page=7, position=0, kind="illustration",
        number=None, title="Assault Training", caption=None, credit=[],
        machine_description=None, file="files/a.png", width=800, height=450, mode="L",
        sha256="", source="pdf_page_image", chosen_because="native",
    )  # fmt: skip
    base.update(kw)
    return F.Figure(**base)


def _jpeg(width, height):
    buffer = io.BytesIO()
    Image.new("L", (width, height), 128).save(buffer, format="JPEG")
    return buffer.getvalue()


def test_the_higher_resolution_copy_wins_one_to_one(tmp_path):
    """Same picture (a sideways print counts: same shape rotated), more pixels: taken."""
    (tmp_path / "files").mkdir()
    f = _figure(title="Quineville", width=1012, height=831)
    copies = [F.OtherCopy("illustration", "51", "Quineville", 394, "https://h/51.jpg")]
    log = F.add_other_copies([f], copies, lambda url: _jpeg(1000, 1200), tmp_path)
    assert f.source == "hyperwar" and (f.width, f.height) == (1000, 1200)
    assert "more pixels" in f.chosen_because and f.candidates[0]["title_score"] == 1.0
    assert log.matched[0]["url"] == "https://h/51.jpg" and log.shape_differences == []


def test_a_copy_of_another_shape_is_logged_never_taken(tmp_path):
    """The real case: HyperWar links its "Carentan and Hill 30 Area" photo under "Carentan
    Causeway" - more pixels, another picture. The PDF's is kept, the mismatch logged."""
    (tmp_path / "files").mkdir()
    f = _figure(title="Carentan Causeway", width=814, height=1014)
    copies = [
        F.OtherCopy("illustration", "46", "Carentan Causeway", 358, "https://h/46.jpg")
    ]
    log = F.add_other_copies([f], copies, lambda url: _jpeg(1200, 791), tmp_path)
    assert f.source == "pdf_page_image" and "another shape" in f.chosen_because
    assert log.shape_differences[0]["copy"] == [1200, 791]


def test_disagreements_with_the_other_copy_are_logged(tmp_path):
    (tmp_path / "files").mkdir()
    plate = _figure(kind="map", number="VII", title="The Airborne Assault, 6 June 1944",
                    width=1718, height=2052)  # fmt: skip
    garbled = _figure(
        title="Fort du Rouie", width=800, height=600
    )  # an OCR-garbled title
    copies = [
        F.OtherCopy(
            "map", "VIII", "The Airborne Assault, 6 June 1944", None, "https://h/m8.jpg"
        ),
        F.OtherCopy("illustration", "60", "Fort du Roule", 435, "https://h/60.jpg"),
        F.OtherCopy("map", "I", "Cosmopolitan World Map", None, "https://h/m1.jpg"),
    ]

    def fetch(url):
        if url.endswith("m1.jpg"):
            raise OSError("no such map")
        return _jpeg(800, 955)

    log = F.add_other_copies([plate, garbled], copies, fetch, tmp_path)
    assert log.number_differences == [
        {
            "kind": "map",
            "title": "The Airborne Assault, 6 June 1944",
            "pdf": "VII",
            "copy": "VIII",
        }
    ]
    assert [
        m["copy_title"] for m in log.near_misses
    ] == []  # 0.92: matched, not a near miss
    assert {c["url"] for c in log.unused_copies} == {"https://h/m1.jpg"}
    assert plate.source == "pdf_page_image"  # the PDF's has more pixels
    assert garbled.candidates and garbled.candidates[0]["title_score"] < 1.0


def test_one_copy_is_never_taken_for_two_figures(tmp_path):
    (tmp_path / "files").mkdir()
    a = _figure(title="Omaha Beach During the Build-up", width=400, height=300)
    b = _figure(title="Omaha Beach During the Build up", position=1, width=400, height=300,
                number="x")  # fmt: skip
    copies = [F.OtherCopy("illustration", "57", "Omaha Beach During the Build-up", 425,
                          "https://h/57.jpg")]  # fmt: skip
    F.add_other_copies([a, b], copies, lambda url: _jpeg(800, 600), tmp_path)
    assert [a.source, b.source] == ["hyperwar", "pdf_page_image"]
    assert "not taken twice" in b.chosen_because


def test_a_combined_copy_of_several_pictures_is_never_taken(tmp_path):
    """HyperWar's one image of an "above / below" pair has more pixels than either photo but
    fewer per photo: recorded, not taken."""
    (tmp_path / "files").mkdir()
    pair = [_figure(position=0), _figure(position=1, match="shared")]
    copies = [
        F.OtherCopy("illustration", "17", "Assault Training", 163, "https://h/17.jpg")
    ]
    F.add_other_copies(pair, copies, lambda url: _jpeg(640, 813), tmp_path)
    assert all(f.source == "pdf_page_image" for f in pair)
    assert all("one image of all 2 pictures" in f.chosen_because for f in pair)
    assert all(f.candidates[-1]["source"] == "hyperwar" for f in pair)


def test_other_copies_are_matched_by_title_not_number():
    html = (
        "<table><tr><td>I.</td><td><a href=maps/X-I.jpg>Cosmopolitan World Map</a></td><td></td></tr>"
        "<tr><td>VIII.</td><td><a href=maps/X-VIII.jpg>The Airborne Assault, 6 June 1944.</a></td>"
        "<td></td></tr><tr><td>30.</td><td><a href=img/X-30.jpg>Parachute Troops</a></td>"
        "<td><a href=X-8.html#page279>279</a></td></tr></table>"
    )
    copies = F.hyperwar_copies(
        html, "https://www.ibiblio.org/hyperwar/USA/X/index.html"
    )
    assert [(c.kind, c.number, c.title, c.printed_page) for c in copies] == [
        ("map", "I", "Cosmopolitan World Map", None),
        ("map", "VIII", "The Airborne Assault, 6 June 1944", None),
        ("illustration", "30", "Parachute Troops", 279),
    ]
    plate = _figure(kind="map", number="VII", title="The Airborne Assault, 6 June 1944")
    score, best = F._best_copy(plate, copies)
    assert score == 1.0
    assert best is not None and best.url.endswith(
        "maps/X-VIII.jpg"
    )  # 1951 VIII = 1993 VII


def test_references_carry_the_citing_sentence_and_land_on_the_pdfs_numbers():
    md = "The battalion moved south. See Maps VII and VIII. Chart 2 shows it.\n\nAs before (Map 3-4)."
    refs = F.references(md, 300, "ch8")
    assert [(r.kind, r.number) for r in refs] == [
        ("map", "VII"), ("map", "VIII"), ("chart", "2"), ("map", "3"), ("map", "4"),
    ]  # fmt: skip
    assert refs[0].verbatim == "See Maps VII and VIII."
    figures = [
        _figure(kind="map", number="VII", title="A"),
        _figure(kind="map", number="VII", title="A", match="shared"),
        _figure(kind="chart", number="2", title="B"),
    ]
    unmatched = F.attach_references(figures, refs)
    assert len(figures[0].references) == 1 and figures[1].references == []
    assert unmatched == {"map VIII": 1, "map 3": 1, "map 4": 1}


# --- the CLI -----------------------------------------------------------------------------------


def _cli():
    path = Path(__file__).resolve().parent.parent / "scripts" / "extract_pdf_figures.py"
    spec = importlib.util.spec_from_file_location("extract_pdf_figures", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_cli_writes_figures_json_with_provenance(book, tmp_path, capsys):
    pdf, ocr, _chunk = book
    text = tmp_path / "ch1.md"
    text.write_text("The landings (Map II) went well.", encoding="utf-8")
    out = tmp_path / "figures"
    code = _cli().main(["--pdf", str(pdf), "--chunks", str(ocr), "--out", str(out),
                        "--text", f"ch1={text}:1"])  # fmt: skip
    report = json.loads((out / "figures.json").read_text(encoding="utf-8"))
    assert report["pdf_sha256"] and len(report["ocr_chunks"]) == 2
    assert report["counts"]["figures"] == 2 and report["counts"]["references"] == 1
    # map 1 (printed 268) and plate I are listed but not in this small book, and its pages print
    # no running heads (so printed page 7 is unknown): all reported, exit 1
    missing = Counter(e["number"] for e in report["list_entries_without_figure"])
    assert missing == Counter({"1": 1, "I": 1, None: 1}) and code == 1
    assert "no figure for map 1" in capsys.readouterr().err


def test_cli_rejects_a_folder_without_chunks(tmp_path, capsys):
    pdf = tmp_path / "x.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(str(pdf))
    assert (
        _cli().main(
            ["--pdf", str(pdf), "--chunks", str(tmp_path), "--out", str(tmp_path / "o")]
        )
        == 2
    )
    assert "no chunk-pFFFF-LLLL" in capsys.readouterr().err
