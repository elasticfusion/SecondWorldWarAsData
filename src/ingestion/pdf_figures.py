"""Figures (maps, charts, photographs) of a SCANNED book PDF, taken from the PDF itself - no OCR -
each at the best resolution available, with its number and title from the book's own lists and
its provenance.

Owner rules (2026-11-08): maps need no OCR, but must be associated with the text that refers to
them and later go to Grok (Phase 3, docs/current/design/maps-target-architecture.md); the PDF is
the main source and its numbering is definitive (a printing may add or drop maps: the 1951
Cross-Channel Attack had a Rand McNally world map as Map I, the 1993 CMH PDF does not); where a
copy exists in more than one source the HIGHER resolution wins.

How a scanned green-book PDF stores its pictures (measured on Cross-Channel Attack): every page
is ONE full-page scan (or a stack of horizontal strips) - photographs and in-text maps are part
of it, not separate images; text pages are 300 dpi bilevel, fold-out map plates 150 dpi colour.
Chandra renders at ~300 dpi and saves a crop of each picture. So, per page:

* a page that is one picture stored BELOW Chandra's render resolution (a fold-out plate): the
  figure is the page's own stored image, extracted at its native pixels (PNG, lossless) - Chandra's
  crop of it would only be an upsampled copy;
* otherwise each Chandra crop is a figure: on a 300 dpi page it already carries the native pixels.

Chandra's alt text is MODEL-WRITTEN; it is kept only as ``machine_description``, never as the
book's caption. The caption is the book's own (the ALL-CAPS-titled paragraph Chandra transcribes
beside the picture); number and title come from the book's lists of maps, charts and
illustrations; the credit from its "Illustrations are from the following sources" list.
Each figure also records every candidate considered (PDF, Chandra crop, HyperWar copy) with its
pixel size, and why the chosen one won.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from src.ingestion import pdf_html_crosscheck as X

_IMAGE_PARAGRAPH = re.compile(r"^!\[(?P<alt>[^\]]*)\]\((?P<file>[^)\s]+)\)")
_IMAGE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<file>[^)\s]+)\)")
_LEADER = re.compile(r"(?:\s*\.){2,}\s*$")  # the ". . . . ." dot leader of a list entry
_ROMAN_ITEM = re.compile(r"^\s*[-*]\s*(?P<num>[IVXLC]+)\.\s+(?P<title>.+?)\s*$", re.M)
_CREDIT = re.compile(
    r"^\s*[-*]\s*(?P<source>[^,\n]+?),\s*pages?:\s*(?P<pages>[\d,\s]+)$", re.M
)
# a stored page image below this share of Chandra's render resolution is not what Chandra saw
_UPSAMPLED = 0.9
_WHOLE_PAGE = 0.9  # an image covering this share of the page area IS the page


@dataclass
class ListEntry:
    """One line of the book's list of maps / charts / illustrations."""

    kind: str  # map | chart | illustration
    number: Optional[str]  # "VII", "3", or None (illustrations are unnumbered)
    title: str
    printed_page: Optional[
        int
    ]  # None: a fold-out plate ("in accompanying map envelope")


@dataclass
class FrontMatter:
    """The book's lists of figures, and its photo credits by printed page."""

    entries: List[ListEntry] = field(default_factory=list)
    credits: Dict[int, List[str]] = field(
        default_factory=dict
    )  # printed page -> sources


@dataclass
class Candidate:
    """One copy of a figure that was considered, with its pixel size."""

    source: str  # pdf_page_image | chandra_crop | hyperwar
    width: int
    height: int
    mode: str
    location: str  # PDF page / crop file / URL

    @property
    def pixels(self) -> int:
        """Width x height."""
        return self.width * self.height


@dataclass
class Figure:  # pylint: disable=too-many-instance-attributes
    """One figure of the book, with its provenance (pylint R0902: it is a record)."""

    figure_id: str
    pdf_page: int
    printed_page: Optional[int]
    position: int  # order on the page
    kind: str  # map | chart | illustration | figure (not in the book's lists)
    number: Optional[str]
    title: Optional[str]
    caption: Optional[str]  # the book's own caption
    credit: List[str]
    machine_description: Optional[
        str
    ]  # Chandra's alt text: MODEL-WRITTEN, not the book's
    file: str
    width: int
    height: int
    mode: str
    sha256: str
    source: str
    chosen_because: str
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    parts: List[Dict[str, str]] = field(
        default_factory=list
    )  # Chandra crops inside a plate
    match: str = ""  # how the list entry was matched: title | page | none
    references: List[Dict[str, Any]] = field(default_factory=list)  # the text citing it


# --- the book's own lists ------------------------------------------------------------------------


def front_matter(markdown: str) -> FrontMatter:
    """The lists of charts, maps and illustrations, and the photo credits, from Chandra's
    markdown of the book's front matter."""
    out = FrontMatter()
    for heading, kind in (
        ("Charts", "chart"),
        ("Maps", "map"),
        ("Illustrations", "illustration"),
    ):
        section = _section(markdown, heading)
        out.entries += _table_entries(section, kind)
        if kind == "map":
            out.entries += [
                ListEntry("map", m["num"], _clean_title(m["title"]), None)
                for m in _ROMAN_ITEM.finditer(section)
            ]
        if kind == "illustration":
            out.credits = _credits(section)
    return out


def _section(markdown: str, heading: str) -> str:
    """The text under ``## heading`` up to the next ``#`` heading (page breaks included)."""
    m = re.search(rf"^#+\s*{re.escape(heading)}\s*$", markdown, re.M | re.I)
    if not m:
        return ""
    end = re.search(r"^#+\s", markdown[m.end() :], re.M)
    return markdown[m.end() : m.end() + end.start()] if end else markdown[m.end() :]


def _table_entries(section: str, kind: str) -> List[ListEntry]:
    from bs4 import BeautifulSoup  # pinned in requirements.txt (with html5lib)

    entries = []
    for row in BeautifulSoup(section, "html5lib").find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in row.find_all("td")]
        entry = _row_entry(cells, kind)
        if entry:
            entries.append(entry)
    return entries


def _row_entry(cells: List[str], kind: str) -> Optional[ListEntry]:
    """``[No.] Title . . . Page`` -> an entry (header rows and blanks give None)."""
    cells = [c for c in cells if c]
    if len(cells) < 2 or not cells[-1].isdigit():
        return None
    number = cells[0].rstrip(".") if len(cells) >= 3 else None
    title = cells[1] if len(cells) >= 3 else cells[0]
    return ListEntry(kind, number, _clean_title(title), int(cells[-1]))


def _clean_title(title: str) -> str:
    return _LEADER.sub("", title).strip().rstrip(".")


def _credits(section: str) -> Dict[int, List[str]]:
    out: Dict[int, List[str]] = {}
    for m in _CREDIT.finditer(section):
        for page in re.findall(r"\d+", m["pages"]):
            out.setdefault(int(page), []).append(m["source"].strip())
    return out


# --- what Chandra saw ----------------------------------------------------------------------------


@dataclass
class ChandraImage:
    """A picture Chandra saw on a page (its crop file and its MODEL-WRITTEN alt text)."""

    pdf_page: int
    position: int
    file: str
    alt: str
    caption: Optional[str]
    page_text: str  # the page's transcribed text (for matching a plate's title)


def chandra_images(markdown: str, first_page: int) -> List[ChandraImage]:
    """Every picture paragraph (``![alt](file)``) of Chandra's paginated markdown, with the
    book's caption printed with it (the next paragraph, when it reads as a caption)."""
    paragraphs = list(X._chandra_paragraphs(markdown, first_page))
    page_text: Dict[int, List[str]] = {}
    for page, _start, text in paragraphs:
        if not _IMAGE_PARAGRAPH.match(text):
            page_text.setdefault(page, []).append(text)
    out: List[ChandraImage] = []
    for i, (page, _start, text) in enumerate(paragraphs):
        if not _IMAGE_PARAGRAPH.match(text):
            continue
        caption = _caption_after(paragraphs, i, page)
        for m in _IMAGE.finditer(
            text
        ):  # Chandra puts a plate's map, legend, scale in one
            position = sum(1 for f in out if f.pdf_page == page)
            out.append(
                ChandraImage(page, position, m["file"], m["alt"], caption,
                             "\n".join(page_text.get(page, [])))
            )  # fmt: skip
    return out


def _caption_after(paragraphs, i: int, page: int) -> Optional[str]:
    if i + 1 >= len(paragraphs) or paragraphs[i + 1][0] != page:
        return None
    text = paragraphs[i + 1][2]
    if _IMAGE_PARAGRAPH.match(text) or not X.is_chandra_caption(text):
        return None
    return " ".join(X._MD_MARKUP.sub(" ", text).split())


# --- the PDF's own stored images -----------------------------------------------------------------


@dataclass
class PageRaster:
    """How a PDF page stores its picture: resolution, and whether one image IS the page."""

    native_dpi: float
    whole_page_image: Optional[int]  # xref of the one image that IS the page, if so


def page_raster(doc: Any, pdf_page: int) -> PageRaster:
    """The page's stored-image resolution, and the image that IS the page (if one covers it)."""
    page = doc[pdf_page - 1]
    images = page.get_images(full=True)
    width_in = page.rect.width / 72.0
    native = max((img[2] for img in images), default=0) / width_in if width_in else 0.0
    whole = None
    if len(images) == 1:
        rects = page.get_image_rects(images[0][0])
        area = page.rect.width * page.rect.height
        if rects and area and rects[0].width * rects[0].height >= _WHOLE_PAGE * area:
            whole = images[0][0]
    return PageRaster(native, whole)


def _render_dpi(page_box: Sequence[float], doc: Any, pdf_page: int) -> float:
    width_in = doc[pdf_page - 1].rect.width / 72.0
    return (page_box[2] - page_box[0]) / width_in if width_in else 0.0


# --- building the figures ------------------------------------------------------------------------


@dataclass
class Chunk:
    """One Chandra OCR chunk: its first PDF page and its ``input/`` folder (input.md,
    input_metadata.json and the crop files)."""

    first_page: int
    folder: Path


def build_figures(
    doc: Any,
    chunks: Sequence[Chunk],
    front: FrontMatter,
    printed: Dict[int, int],
    out_dir: Path,
    known_ids: Optional[Dict[Tuple[int, int], str]] = None,
) -> List[Figure]:
    """All figures of the book, written to ``out_dir/files``. ``printed``: {PDF page: printed
    page} from the running heads (``infer_printed`` fills the pages that print none).
    ``known_ids``: {(PDF page, position): figure_id} of an earlier run, so a re-run keeps its
    IDs."""
    files = out_dir / "files"
    files.mkdir(parents=True, exist_ok=True)
    printed = infer_printed(printed, len(doc))
    plate_entries = [e for e in front.entries if e.printed_page is None]
    figures: List[Figure] = []
    plate_texts: Dict[int, str] = {}
    for chunk in chunks:
        for page, images, box in _chunk_pages(chunk):
            plate_texts[page] = images[0].page_text
            is_plate = page not in printed and (
                _plate_title(plate_texts[page], plate_entries) is not None
            )  # fold-outs are unnumbered ('in accompanying map envelope')
            figures += _page_figures(
                doc, page, images, box, chunk.folder, files, is_plate
            )
    for f in figures:
        f.printed_page = printed.get(f.pdf_page)
    figures += _printed_only(doc, front.entries, figures, printed, files)
    _identify(figures, front, known_ids or {})
    assign_entries(figures, front.entries, plate_texts)
    return figures


def _identify(figures: Sequence[Figure], front: FrontMatter, known: Dict) -> None:
    """IDs (kept from an earlier run) and the book's photo credit of each figure's page."""
    import ulid

    for f in figures:
        f.figure_id = known.get((f.pdf_page, f.position)) or str(ulid.new())
        f.credit = front.credits.get(f.printed_page, []) if f.printed_page else []


def _chunk_pages(chunk: Chunk):
    """(PDF page, its Chandra images, Chandra's page box) of every page with a picture."""
    import json

    meta = json.loads(
        (chunk.folder / "input_metadata.json").read_text(encoding="utf-8")
    )
    boxes = {chunk.first_page + p["page_num"]: p["page_box"] for p in meta["pages"]}
    markdown = (chunk.folder / "input.md").read_text(encoding="utf-8")
    by_page: Dict[int, List[ChandraImage]] = {}
    for img in chandra_images(markdown, chunk.first_page):
        by_page.setdefault(img.pdf_page, []).append(img)
    for page, images in by_page.items():
        pictures = [img for img in images if not _blank(chunk.folder / img.file)]
        if pictures:
            yield page, pictures, boxes.get(page)


_BLANK_STDDEV = 3.0  # grey-level spread below which a crop is empty paper


def _blank(path: Path) -> bool:
    """A crop of empty paper ("Blank white page"): not a figure."""
    from PIL import ImageStat

    return ImageStat.Stat(_open(path)).stddev[0] < _BLANK_STDDEV


def consistent_printed(printed: Dict[int, int]) -> Dict[int, int]:
    """Running-head numbers that fit the book's sequence: one whose PDF-to-printed offset agrees
    with neither its numbered neighbour before nor after is not a page number (a map's "0"
    scale label at the top of a plate)."""
    pages = sorted(printed)
    out = {}
    for k, page in enumerate(pages):
        offset = page - printed[page]
        near = [pages[j] for j in (k - 1, k + 1) if 0 <= j < len(pages)]
        if not near or any(n - printed[n] == offset for n in near):
            out[page] = printed[page]
    return out


def infer_printed(printed: Dict[int, int], pages: int) -> Dict[int, int]:
    """Printed page numbers for the pages whose running head prints none (a chapter opening, a
    full-page photo): taken from the nearest numbered pages before AND after, only where both
    agree on the PDF-to-printed offset."""
    printed = consistent_printed(printed)
    known = sorted(printed)
    out = dict(printed)
    for page in range(1, pages + 1):
        if page in out:
            continue
        before = max((k for k in known if k < page), default=None)
        after = min((k for k in known if k > page), default=None)
        if before is None or after is None:
            continue
        if before - printed[before] == after - printed[after]:
            out[page] = page - (before - printed[before])
    return out


def _plate_title(
    page_text: str, plate_entries: Sequence[ListEntry]
) -> Optional[ListEntry]:
    """The fold-out plate whose title this page prints, if any (best word overlap >= 0.6)."""
    text = _norm(page_text)
    scored = [(_title_score(e.title, text), i) for i, e in enumerate(plate_entries)]
    best = max(scored, default=(0.0, -1))
    return plate_entries[best[1]] if best[0] >= 0.6 else None


def _printed_only(doc, entries, figures, printed, files: Path) -> List[Figure]:
    """A listed chart or map Chandra saw no picture in (it transcribed the chart as text): the
    printed page itself, rendered at its stored resolution, is the figure - one per entry, so
    two charts on one page each have theirs."""
    pdf_of = {v: k for k, v in printed.items()}
    out = []
    for entry, position in _text_only(entries, {f.printed_page for f in figures}):
        page = pdf_of.get(entry.printed_page)  # type: ignore[arg-type]
        if page is None:
            continue
        out.append(_page_render(doc, page, files, position))
        out[-1].printed_page = entry.printed_page
    return out


def _text_only(entries: Sequence[ListEntry], have: set):
    """(entry, position on its page) of each listed chart / map on a page with no figure."""
    seen: Dict[int, int] = {}
    for entry in entries:
        page = entry.printed_page
        if entry.kind not in ("chart", "map") or page is None or page in have:
            continue
        seen[page] = seen.get(page, -1) + 1
        yield entry, seen[page]


def _page_render(doc, page: int, files: Path, position: int = 0) -> Figure:
    raster = page_raster(doc, page)
    dpi = max(72, round(raster.native_dpi)) if raster.native_dpi else 300
    pix = doc[page - 1].get_pixmap(dpi=dpi, colorspace="gray")
    data = pix.tobytes("png")
    name = f"p{page:04d}-page.png"
    (files / name).write_bytes(data)
    cand = Candidate("pdf_page_render", pix.width, pix.height, "L", f"pdf page {page}")
    return Figure(
        figure_id="", pdf_page=page, printed_page=None, position=position, kind="figure",
        number=None, title=None, caption=None, credit=[], machine_description=None,
        file=f"files/{name}", width=pix.width, height=pix.height, mode="L",
        sha256=hashlib.sha256(data).hexdigest(), source="pdf_page_render",
        chosen_because=f"the book lists a figure here that Chandra transcribed as text, not a "
        f"picture: the printed page, rendered at its stored {dpi} dpi",
        candidates=[asdict(cand)],
    )  # fmt: skip


def _page_figures(
    doc, page: int, images: List[ChandraImage], box, folder: Path, files: Path,
    is_plate: bool = False,
):  # fmt: skip
    raster = page_raster(doc, page)
    render = _render_dpi(box, doc, page) if box else 0.0
    if raster.whole_page_image and render and raster.native_dpi < _UPSAMPLED * render:
        native = _native_image(doc, raster.whole_page_image)
        scale = native.width / ((doc[page - 1].rect.width / 72.0) * render)
        located = [
            (img, locate(native, _open(folder / img.file), scale)) for img in images
        ]
        if is_plate:
            return [_plate_figure(page, located, native, raster, folder, files)]
        return [
            _located_figure(page, il, native, raster, folder, files) for il in located
        ]
    return [_crop(img, folder, files, raster.native_dpi, render) for img in images]


def _native_image(doc: Any, xref: int) -> Any:
    """The page's stored image as a Pillow image at its own pixels (grey or RGB)."""
    import fitz  # pymupdf (pinned)
    from PIL import Image

    pix = fitz.Pixmap(doc, xref)
    if pix.alpha:
        pix = fitz.Pixmap(pix, 0)
    if pix.n not in (1, 3):  # CMYK, indexed -> RGB
        pix = fitz.Pixmap(fitz.csRGB, pix)
    mode = "L" if pix.n == 1 else "RGB"
    return Image.frombytes(mode, (pix.width, pix.height), pix.samples)


def _plate_figure(page, located, native, raster, folder: Path, files: Path) -> Figure:
    """A fold-out plate: the region holding all its pieces (map, legend, scale bars) - the whole
    stored page when they cover most of it."""
    boxes = [box for _img, (box, score) in located if score <= _MATCH_LIMIT]
    union = (
        (min(b[0] for b in boxes), min(b[1] for b in boxes),
         max(b[2] for b in boxes), max(b[3] for b in boxes))
        if boxes else (0, 0, native.width, native.height)
    )  # fmt: skip
    if _area(union) >= _PLATE_SHARE * native.width * native.height:
        union = (0, 0, native.width, native.height)
    main = max(located, key=lambda il: _area(il[1][0]))[0]
    why = (f"a fold-out plate (its title is in the book's list): the stored page image at "
           f"{raster.native_dpi:.0f} dpi; Chandra's crops of it are upsampled renders")  # fmt: skip
    images = [img for img, _ in located]
    return _native_figure(
        page, 0, main, native, union, why, folder, files, parts=images
    )


def _located_figure(
    page, img_located, native, raster, folder: Path, files: Path
) -> Figure:
    img, (box, score) = img_located
    if score > _MATCH_LIMIT:
        return _crop(img, folder, files, raster.native_dpi, 0.0, score)
    why = (
        f"cut from the {raster.native_dpi:.0f} dpi stored page image where Chandra's crop "
        f"matches (mean difference {score:.1f}/255); the crop itself is upsampled"
    )
    return _native_figure(page, img.position, img, native, box, why, folder, files)


def _native_figure(
    page, position, img, native, box, why, folder, files, parts=()
) -> Figure:
    import io

    region = native.crop(box)
    buffer = io.BytesIO()
    region.save(buffer, format="PNG")  # lossless: the stored pixels as they are
    data = buffer.getvalue()
    name = f"p{page:04d}-{position}.png"
    (files / name).write_bytes(data)
    cand = Candidate("pdf_page_image", region.width, region.height, region.mode,
                     f"pdf page {page} box {list(box)}")  # fmt: skip
    return Figure(
        figure_id="", pdf_page=page, printed_page=None, position=position, kind="figure",
        number=None, title=None, caption=img.caption, credit=[],
        machine_description=img.alt or None, file=f"files/{name}", width=region.width,
        height=region.height, mode=region.mode, sha256=hashlib.sha256(data).hexdigest(),
        source="pdf_page_image", chosen_because=why,
        candidates=[asdict(cand), asdict(_crop_candidate(img, folder))],
        parts=[{"file": p.file, "machine_description": p.alt} for p in parts],
    )  # fmt: skip


def _open(path: Path) -> Any:
    from PIL import Image

    with Image.open(path) as im:
        return im.convert("L")


def _area(box: Tuple[int, int, int, int]) -> int:
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


# --- finding a Chandra crop in the stored page image ---------------------------------------------

_PLATE_SHARE = (
    0.5  # a main picture covering this share of the page makes the page a plate
)
_MATCH_LIMIT = (
    20.0  # mean absolute grey difference (0-255) above which a match is not trusted
)
_SEARCH = ((8, None), (2, 6), (1, 3))  # (downscale factor, search radius in its pixels)


def locate(
    native: Any, crop: Any, scale: float
) -> Tuple[Tuple[int, int, int, int], float]:
    """(box in ``native`` pixels, mean absolute difference) of ``crop`` - Chandra's render of a
    region, ``scale`` native pixels per render pixel - found coarse to fine: an exhaustive search
    on 1/8-size images, then refined at 1/2 and full size. Pillow only."""
    from PIL import Image

    grey = native.convert("L")
    tw, th = max(1, round(crop.width * scale)), max(1, round(crop.height * scale))
    if (
        tw > grey.width + 2 or th > grey.height + 2
    ):  # rounding may overshoot a pixel or two
        return (0, 0, grey.width, grey.height), 255.0
    tw, th = min(tw, grey.width), min(th, grey.height)
    template = crop.convert("L").resize((tw, th), Image.Resampling.LANCZOS)
    pos: Tuple[int, int] = (0, 0)  # unused by the first, exhaustive level (radius None)
    score = 255.0
    for factor, radius in _SEARCH:
        page = grey.resize(
            (max(1, grey.width // factor), max(1, grey.height // factor)),
            Image.Resampling.BOX,
        )
        small = template.resize(
            (max(1, tw // factor), max(1, th // factor)), Image.Resampling.BOX
        )
        score, x, y = _best_offset(page, small, pos, factor, radius)
        pos = (x * factor, y * factor)
    return (pos[0], pos[1], pos[0] + tw, pos[1] + th), score


def _best_offset(page, small, pos, factor: int, radius: Optional[int]):
    """(score, x, y) of the best placement of ``small`` in ``page`` - everywhere, or within
    ``radius`` of the previous level's ``pos``."""
    from PIL import ImageChops, ImageStat

    max_x, max_y = page.width - small.width, page.height - small.height
    if radius is None:
        xs, ys = range(0, max_x + 1), range(0, max_y + 1)
    else:
        cx, cy = pos[0] // factor, pos[1] // factor
        xs = range(max(0, cx - radius), min(max_x, cx + radius) + 1)
        ys = range(max(0, cy - radius), min(max_y, cy + radius) + 1)
    best = (255.0, 0, 0)
    for y in ys:
        for x in xs:
            region = page.crop((x, y, x + small.width, y + small.height))
            d = ImageStat.Stat(ImageChops.difference(region, small)).mean[0]
            if d < best[0]:
                best = (d, x, y)
    return best


def _crop(
    img: ChandraImage,
    folder: Path,
    files: Path,
    native_dpi: float,
    render: float,
    unmatched_score: Optional[float] = None,
) -> Figure:
    """Chandra's own crop. On a page stored at Chandra's render resolution it carries the native
    pixels; as a FALLBACK (its place in a lower-resolution page image was not found with
    confidence) it is the best copy available, and says so."""
    data = (folder / img.file).read_bytes()
    cand = _crop_candidate(img, folder)
    (files / img.file).write_bytes(data)
    if unmatched_score is None:
        why = (f"part of a {native_dpi:.0f} dpi page scan; Chandra rendered it at {render:.0f} "
               "dpi, so its crop carries the native pixels")  # fmt: skip
    else:
        why = (
            f"FALLBACK: not found in the {native_dpi:.0f} dpi stored page image (best mean "
            f"difference {unmatched_score:.1f}/255); Chandra's crop is upsampled - look at it"
        )
    return Figure(
        figure_id="", pdf_page=img.pdf_page, printed_page=None, position=img.position,
        kind="figure", number=None, title=None, caption=img.caption, credit=[],
        machine_description=img.alt or None, file=f"files/{img.file}", width=cand.width,
        height=cand.height, mode=cand.mode, sha256=hashlib.sha256(data).hexdigest(),
        source="chandra_crop", chosen_because=why, candidates=[asdict(cand)],
    )  # fmt: skip


def _crop_candidate(img: ChandraImage, folder: Path) -> Candidate:
    from PIL import Image  # pillow (pinned)

    with Image.open(folder / img.file) as im:
        return Candidate("chandra_crop", im.width, im.height, im.mode, img.file)


# --- the book's numbers and titles ---------------------------------------------------------------


def assign_entries(
    figures: List[Figure],
    entries: Sequence[ListEntry],
    plate_texts: Optional[Dict[int, str]] = None,
) -> None:
    """Give each figure its list entry: a fold-out plate (no page in the list) by its title as
    printed on the plate (``plate_texts``: {PDF page: the page's transcribed text}); anything
    else by its printed page (largest picture on the page first), then by its own caption
    nearby; a further picture of a listed entry inherits it (``_share_entries``)."""
    plates = [f for f in figures if f.parts]
    _by_title(plates, [e for e in entries if e.printed_page is None], plate_texts or {})
    paged = [e for e in entries if e.printed_page is not None]
    _by_page(figures, paged)
    _unplaced_by_caption(figures, paged)
    _share_entries(figures)


def _unplaced_by_caption(figures: Sequence[Figure], paged: Sequence[ListEntry]) -> None:
    placed = {(f.kind, f.number, f.title) for f in figures if f.match}
    for entry in paged:
        if (entry.kind, entry.number, entry.title) not in placed:
            _by_caption(entry, figures)


def _by_page(figures: Sequence[Figure], paged: Sequence[ListEntry]) -> None:
    on_page: Dict[Optional[int], List[Figure]] = {}
    for f in figures:
        if not f.parts:
            on_page.setdefault(f.printed_page, []).append(f)
    for entry in paged:
        free = [f for f in on_page.get(entry.printed_page, []) if not f.match]
        if free:
            _give(max(free, key=lambda f: f.width * f.height), entry, "page")


def _share_entries(figures: Sequence[Figure]) -> None:
    """A figure the lists do not name is usually another photo of a listed one: the second of
    an "above / below" pair (same page), or the far half of a two-page spread (the facing page).
    It inherits that entry, marked ``shared`` - the main figure stays the one references go to.
    """
    listed = [
        f for f in figures if f.match and f.printed_page is not None and not f.parts
    ]
    for f in figures:
        if not (f.match or f.parts or f.printed_page is None):
            _inherit(f, listed)


def _inherit(f: Figure, listed: Sequence[Figure]) -> None:
    """``f`` takes the entry of a listed figure on its page (one with its caption first), else
    on the facing page."""
    for distance in (0, 1):
        near = [g for g in listed if _page_gap(g, f) == distance]
        pick = [g for g in near if f.caption and g.caption == f.caption] or near
        if pick:
            g = pick[0]
            f.kind, f.number, f.title, f.match = g.kind, g.number, g.title, "shared"
            return


def _page_gap(a: Figure, b: Figure) -> int:
    return abs((a.printed_page or 0) - (b.printed_page or 0))


# a facing photo page can carry the list's page number or its neighbour's
_NEAR_PAGES = 2


def _by_caption(entry: ListEntry, figures: Sequence[Figure]) -> None:
    """An entry its printed page did not place: the unplaced figure within two pages whose own
    caption carries the title ("GENERAL MONTGOMERY, ground commander ..." for "General
    Montgomery" listed on 118, printed on an unnumbered facing page)."""
    scored = [
        (_title_score(entry.title, _norm(f.caption or "")), f)
        for f in _unplaced_near(entry, figures)
    ]
    best = max(scored, default=(0.0, None), key=lambda s: s[0])
    if best[1] is not None and best[0] >= 0.8:
        _give(best[1], entry, "caption")


def _unplaced_near(entry: ListEntry, figures: Sequence[Figure]) -> List[Figure]:
    page = entry.printed_page or 0
    return [
        f for f in figures
        if not f.match and f.caption and f.printed_page is not None
        and abs(f.printed_page - page) <= _NEAR_PAGES
    ]  # fmt: skip


def _by_title(
    plates: List[Figure], entries: List[ListEntry], plate_texts: Dict[int, str]
) -> None:
    """Greedy best-first title matching of plates to the list's plate entries."""
    texts = [
        _norm((p.caption or "") + " " + plate_texts.get(p.pdf_page, "")) for p in plates
    ]
    scored = sorted(
        ((_title_score(e.title, texts[k]), k, i) for k in range(len(plates))
         for i, e in enumerate(entries)),
        reverse=True,
    )  # fmt: skip
    used_p, used_e = set(), set()
    for score, k, i in scored:
        if score < 0.6 or k in used_p or i in used_e:
            continue
        _give(plates[k], entries[i], "title")
        used_p.add(k)
        used_e.add(i)


def _title_score(title: str, text: str) -> float:
    """Share of the title's words present in the plate's text (titles are printed on plates)."""
    words = _norm(title).split()
    if not words:
        return 0.0
    present = sum(1 for w in words if f" {w} " in f" {text} ")
    return present / len(words)


def _norm(text: str) -> str:
    return " ".join(w.lower() for w in X.words(X._unaccented(text)))


def _give(f: Figure, entry: ListEntry, how: str) -> None:
    f.kind, f.number, f.title, f.match = entry.kind, entry.number, entry.title, how


# --- other copies (HyperWar) ---------------------------------------------------------------------


@dataclass
class OtherCopy:
    """A figure as another source (HyperWar) lists it: its own numbering, title and URL."""

    kind: str
    number: Optional[str]
    title: str
    printed_page: Optional[int]
    url: str


def hyperwar_copies(index_html: str, base_url: str) -> List[OtherCopy]:
    """Every map / chart / illustration linked from a HyperWar book index
    (``<a href=maps/...jpg>Title</a>`` with its number and printed page in the same row).
    """
    from urllib.parse import urljoin

    from bs4 import BeautifulSoup

    kinds = {"maps": "map", "charts": "chart", "img": "illustration"}
    out = []
    for row in BeautifulSoup(index_html, "html5lib").find_all("tr"):
        link = row.find(
            "a", href=re.compile(r"^(maps|charts|img)/.+\.(jpe?g|png|gif)$", re.I)
        )
        if not link:
            continue
        cells = [c.get_text(" ", strip=True) for c in row.find_all("td")]
        page = next((int(c) for c in reversed(cells) if c.isdigit()), None)
        number = cells[0].rstrip(".") if cells and cells[0].rstrip(".") else None
        href = str(link.get("href") or "")
        kind = kinds[href.split("/", 1)[0].lower()]
        out.append(OtherCopy(kind, number, link.get_text(" ", strip=True).rstrip("."), page,
                             urljoin(base_url, href)))  # fmt: skip
    return out


@dataclass
class CopyLog:
    """Every match and mismatch between the PDF's figures and another source's copies - the
    record of where the two disagree (an OCR-garbled title, a printing's renumbering).
    """

    matched: List[Dict[str, Any]] = field(default_factory=list)
    near_misses: List[Dict[str, Any]] = field(default_factory=list)
    number_differences: List[Dict[str, Any]] = field(default_factory=list)
    shape_differences: List[Dict[str, Any]] = field(
        default_factory=list
    )  # likely mislinked
    unused_copies: List[Dict[str, Any]] = field(default_factory=list)
    failed: List[Dict[str, Any]] = field(default_factory=list)


_COPY_MATCH = 0.85  # title similarity for the same figure
_NEAR_MISS = 0.6  # ... below which a best candidate is not worth reporting
_SHAPE_TOLERANCE = 0.15  # long/short side ratio may differ this much (rotation allowed)


def add_other_copies(
    figures: List[Figure],
    copies: Sequence[OtherCopy],
    fetch: Callable[[str], bytes],
    out_dir: Path,
) -> CopyLog:
    """Match each list entry's figures to the other source's copy BY TITLE (numbers differ
    between printings), measure it, and take it instead when it has more pixels - only where the
    entry is ONE picture in the PDF, the copy has the same shape (a rotation allowed), and no
    other entry took that copy. Where the PDF prints several pictures (an "above / below" pair,
    four portraits) the other source has one combined image of them all: compared whole against
    one photo it looks larger while each photo in it is smaller, so it is recorded, never taken.
    Returns the log of every match and mismatch."""
    log = CopyLog()
    used: Dict[str, str] = {}
    for group in _entry_groups(figures):
        score, copy = _best_copy(group[0], copies)
        if copy is None:
            _near_miss(log, group[0], score, copies)
            continue
        _compare(group, copy, score, fetch, out_dir, log, used)
    log.unused_copies = [asdict(c) for c in copies if c.url not in used]
    return log


def _entry_groups(figures: Sequence[Figure]) -> List[List[Figure]]:
    groups: Dict[Tuple[str, Optional[str], Optional[str]], List[Figure]] = {}
    for f in figures:
        if f.title:
            groups.setdefault((f.kind, f.number, f.title), []).append(f)
    return list(groups.values())


def _compare(group, copy, score, fetch, out_dir: Path, log: CopyLog, used) -> None:
    main = group[0]
    try:
        data = fetch(copy.url)
        cand = _measure(data, copy.url)
    except (
        Exception
    ) as exc:  # noqa: BLE001 - a bad download is logged and skipped, not fatal
        log.failed.append(
            {"url": copy.url, "title": main.title, "error": repr(exc)[:200]}
        )
        return
    record = {
        **asdict(cand),
        "title_score": round(score, 3),
        "number_in_copy": copy.number,
    }
    for f in group:
        f.candidates.append(record)
    log.matched.append({"figure_id": main.figure_id, "title": main.title, "url": copy.url,
                        "title_score": round(score, 3)})  # fmt: skip
    if copy.number and main.number and copy.number != main.number:
        log.number_differences.append(
            {
                "kind": main.kind,
                "title": main.title,
                "pdf": main.number,
                "copy": copy.number,
            }
        )
    if len(group) == 1 and not _same_shape(main, cand):
        log.shape_differences.append({
            "title": main.title, "pdf_page": main.pdf_page, "url": copy.url,
            "pdf": [main.width, main.height], "copy": [cand.width, cand.height],
        })  # fmt: skip
    _decide(group, data, cand, copy, out_dir, used)


def _decide(group, data: bytes, cand: Candidate, copy, out_dir: Path, used) -> None:
    main = group[0]
    if len(group) > 1:
        note = (f"; the other copy ({cand.width}x{cand.height}) is one image of all "
                f"{len(group)} pictures of this entry - less per picture, not taken")  # fmt: skip
    elif copy.url in used:
        note = f"; the other copy is already {used[copy.url]}'s - not taken twice"
    elif not _same_shape(main, cand):
        note = (
            f"; the other copy ({cand.width}x{cand.height}) has another shape than "
            f"{main.width}x{main.height} - a different crop or a mismatch, not taken"
        )
    elif cand.pixels > main.width * main.height:
        used[copy.url] = main.figure_id
        _replace(main, data, cand, out_dir)
        return
    else:
        used[copy.url] = main.figure_id
        return
    used.setdefault(copy.url, main.figure_id)
    for f in group:
        f.chosen_because += note


def _same_shape(f: Figure, cand: Candidate) -> bool:
    """Long/short side ratios within the tolerance - a picture printed sideways counts."""

    def ratio(w: int, h: int) -> float:
        return max(w, h) / max(1, min(w, h))

    a, b = ratio(f.width, f.height), ratio(cand.width, cand.height)
    return abs(a - b) <= _SHAPE_TOLERANCE * max(a, b)


def _near_miss(
    log: CopyLog, f: Figure, score: float, copies: Sequence[OtherCopy]
) -> None:
    if score < _NEAR_MISS:
        return
    best = max((c for c in copies if c.kind == f.kind), key=lambda c: _similarity(f, c))
    log.near_misses.append({"title": f.title, "copy_title": best.title,
                            "title_score": round(score, 3), "url": best.url})  # fmt: skip


def _similarity(f: Figure, c: OtherCopy) -> float:
    return difflib.SequenceMatcher(None, _norm(f.title or ""), _norm(c.title)).ratio()


def _best_copy(
    f: Figure, copies: Sequence[OtherCopy]
) -> Tuple[float, Optional[OtherCopy]]:
    """(best title similarity, the copy if it clears ``_COPY_MATCH``)."""
    scored = [(_similarity(f, c), c) for c in copies if c.kind == f.kind]
    best = max(scored, default=(0.0, None), key=lambda s: s[0])
    return best[0], (best[1] if best[0] >= _COPY_MATCH else None)


def _measure(data: bytes, url: str) -> Candidate:
    import io

    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        return Candidate("hyperwar", im.width, im.height, im.mode, url)


def _replace(f: Figure, data: bytes, cand: Candidate, out_dir: Path) -> None:
    name = (
        f"p{f.pdf_page:04d}-{f.position}-hyperwar{Path(cand.location).suffix or '.jpg'}"
    )
    (out_dir / "files" / name).write_bytes(data)
    f.chosen_because = f"the other copy has more pixels ({cand.width}x{cand.height} vs {f.width}x{f.height})"
    f.file, f.width, f.height, f.mode = (
        f"files/{name}",
        cand.width,
        cand.height,
        cand.mode,
    )
    f.sha256, f.source = hashlib.sha256(data).hexdigest(), cand.source


# --- the text that cites them --------------------------------------------------------------------

_REFERENCE = re.compile(
    r"\b(?P<word>Maps?|Charts?)\s+(?P<nums>(?:[IVXL]+|\d+)(?:\s*(?:,|and|-|–)\s*(?:[IVXL]+|\d+))*)\b"
)


@dataclass
class Reference:
    """A place in the text that cites a map or chart by number."""

    kind: str
    number: str
    unit: str  # the text unit (chapter / appendix) it was found in
    pdf_page: int
    verbatim: str  # the sentence that cites it


def references(markdown: str, first_page: int, unit: str) -> List[Reference]:
    """Every "Map VII" / "See Map 3" / "Maps XI and XII" / "Chart 2" in a text unit, with its
    PDF page and the citing sentence (provenance for the reference footprint)."""
    out = []
    for page, _start, text in X._chandra_paragraphs(markdown, first_page):
        if _IMAGE_PARAGRAPH.match(text):
            continue
        for m in _REFERENCE.finditer(text):
            kind = "chart" if m["word"].lower().startswith("chart") else "map"
            for number in _numbers(m["nums"]):
                out.append(
                    Reference(kind, number, unit, page, _sentence(text, m.start()))
                )
    return out


def _numbers(spec: str) -> Iterable[str]:
    for part in re.split(r"\s*(?:,|and)\s*", spec):
        bounds = re.split(r"\s*[-–]\s*", part)
        if len(bounds) == 2 and all(b.isdigit() for b in bounds):
            yield from (str(n) for n in range(int(bounds[0]), int(bounds[1]) + 1))
        else:
            yield from (b for b in bounds if b)


def _sentence(text: str, at: int) -> str:
    start = max(text.rfind(". ", 0, at) + 2, 0)
    end = text.find(". ", at)
    return " ".join(text[start : end + 1 if end >= 0 else len(text)].split())


def attach_references(
    figures: List[Figure], refs: Sequence[Reference]
) -> Dict[str, int]:
    """Each reference onto the figure with that kind and number (the PDF's numbering).
    Returns {"map XXV": n} for numbers the book's lists do not have - a citation of a map this
    printing does not carry, or an OCR misreading of the number: to be looked at."""
    by_key = {
        (f.kind, f.number): f for f in figures if f.number and f.match != "shared"
    }
    unmatched: Dict[str, int] = {}
    for r in refs:
        f = by_key.get((r.kind, r.number))
        if f is None:
            key = f"{r.kind} {r.number}"
            unmatched[key] = unmatched.get(key, 0) + 1
        else:
            f.references.append(asdict(r))
    return unmatched
