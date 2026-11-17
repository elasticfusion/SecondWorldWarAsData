"""A scanned book's OWN figures (maps, charts, photographs): extracted once from its PDF and its
OCR (src/ingestion/pdf_figures), stored beside its content, and given to the chapters that print
or cite them.

Why: Phase 0 removes every picture paragraph from an OCR'd chapter's text (src/ingestion/
chandra_markdown - the alt text is model-written, not the book's), and Phase 1 found figures only
through links in the text (a HyperWar web page's ``[Map VIII](maps/...)``). So an OCR'd PDF book
lost all of them: USA-E-XChannel's 24 fold-out maps, 6 in-text maps, 4 charts and ~90 photographs
were in the PDF and the OCR's 139 crops, and Phase 1 recorded 0 images and 0 maps (2026-11-15).

* ``ensure`` (Phase 1, once per book): the book's ``contentrepository/<book>/_figures/``
  (``figures.json`` + ``files/``), rebuilt only when the PDF or the OCR submission changed.
  Deterministic - no Grok, no OCR.
* ``attach`` (Phase 1, per chapter): the figures printed on the chapter's PDF pages become its
  ``images``; each numbered map printed or cited ("Map VIII") there becomes one of its ``maps``,
  with the citing paragraphs (the reference footprint).
* ``read_figure``: the bytes behind a figure's key, for the image (Phase 2) and map (Phase 3)
  download steps (``images.fetch_image_bytes``).

A figure's ``url`` is its file's key under ``_figures/files/``: its identity, and its provenance
(``figures.json`` holds its PDF page, printed page, the book's caption and credit, and every
candidate copy considered with why the chosen one won).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

FIGURES_DIR = "_figures"  # reserved: the trigger never treats it as an upload
REPORT_NAME = "figures.json"
_FILE_EXTS = {"png": "png", "jpg": "jpg", "jpeg": "jpg", "webp": "webp"}
# A figure's key: the system namespace only (as the trigger's _is_derived_figure: never a
# contributor's user-/contrib-/org- folder), one book segment, a plain file name - no other path
# can be read through it.
_FIGURE_KEY = re.compile(
    r"contentrepository/(?:_system/)?(?P<book>[A-Za-z0-9][A-Za-z0-9._-]*)/"
    + FIGURES_DIR
    + r"/files/(?P<file>[A-Za-z0-9][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)*)"
)
_MAX_FIGURE_BYTES = 50 * 1024 * 1024  # a fold-out plate is a few MB


def figures_prefix(book: str) -> str:
    return f"contentrepository/{book}/{FIGURES_DIR}/"


def figure_key(book: str, file: str) -> str:
    """The key of a figure file (``file`` as in figures.json: ``files/<name>``)."""
    return f"{figures_prefix(book)}{file}"


def _figure_ext(url: Any) -> Optional[str]:
    """The extension of a figure key's file; None when ``url`` is not a figure key."""
    from src.utils.content_source import is_source_segment

    m = _FIGURE_KEY.fullmatch(str(url or ""))
    if not m or is_source_segment(m["book"]):
        return None
    return _FILE_EXTS.get(Path(m["file"]).suffix.lstrip(".").lower())


def is_figure_key(url: Any) -> bool:
    return _figure_ext(url) is not None


# --- extraction (the pure core; scripts/extract_pdf_figures adds a HyperWar copy) -----------------
def sha256(path: Path) -> str:
    """The file's SHA-256, read in 1 MB blocks (a book PDF is tens of MB)."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def front_of(chunks: Sequence[Any]) -> Any:
    """The book's lists of maps / charts / illustrations, from the first chunk that has them;
    None when no chunk does (not a book with figure lists)."""
    from src.ingestion import pdf_figures as F

    for chunk in chunks:
        front = F.front_matter((chunk.folder / "input.md").read_text(encoding="utf-8"))
        if front.entries:
            return front
    return None


def known_ids(out: Path) -> Dict[Tuple[int, int], str]:
    """{(PDF page, position): figure_id} of an earlier run in ``out``: a re-run keeps its IDs."""
    data = _read_report(out / REPORT_NAME) or {}
    return {
        (f["pdf_page"], f["position"]): f["figure_id"]
        for f in data.get("figures") or []
        if isinstance(f, dict) and {"pdf_page", "position", "figure_id"} <= set(f)
    }


def _read_report(path: Path) -> Optional[Dict[str, Any]]:
    """A figures report, or None when absent or unreadable (said: a corrupt report only resets
    the figure IDs, never stops the build)."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("OBSERV figures.report_unreadable file=%s reason=%s", path, e)
        return None
    return data if isinstance(data, dict) else None


def stored(book_dir: Path) -> Optional[Dict[str, Any]]:
    """The book's stored figures report (its synced ``_figures/figures.json``), or None."""
    return _read_report(Path(book_dir) / FIGURES_DIR / REPORT_NAME)


# other(figures, out) -> (CopyLog, its source): another copy of the book's figures (HyperWar)
OtherCopies = Callable[[List[Any], Path], Tuple[Any, Optional[str]]]


def extract(
    pdf: Path,
    chunks: Sequence[Any],
    front: Any,
    out: Path,
    texts: Sequence[Tuple[str, Path, int]] = (),
    other: Optional[OtherCopies] = None,
) -> Dict[str, Any]:
    """Extract, compare (``other``), cite (``texts``: (unit, corrected text, its first PDF
    page)); write ``out/figures.json`` + ``out/files/``. Returns the report."""
    import fitz  # pymupdf (pinned)

    from src.ingestion import pdf_figures as F
    from src.ingestion import pdf_html_crosscheck as X

    with fitz.open(str(pdf)) as doc:
        printed = X.printed_pages(doc, 1, len(doc))
        figures = F.build_figures(doc, chunks, front, printed, out, known_ids(out))
    copy_log, copies_source = other(figures, out) if other else (F.CopyLog(), None)
    refs = [
        ref
        for unit, path, first in texts
        for ref in F.references(path.read_text(encoding="utf-8"), first, unit)
    ]
    unmatched = F.attach_references(figures, refs)
    report = _report(pdf, chunks, front, figures, texts, unmatched)
    report.update(other_copies=copies_source, other_copies_log=asdict(copy_log))
    write_report(out, report)
    return report


def _report(pdf, chunks, front, figures, texts, unmatched) -> Dict[str, Any]:
    """figures.json: the inputs (hashed), the counts, what the book lists but the PDF lacks, the
    citations of numbers the lists lack, and every figure with its provenance."""
    placed = {(f.kind, f.number, f.title) for f in figures if f.match}
    return {
        "pdf": str(pdf),
        "pdf_sha256": sha256(pdf),
        "ocr_chunks": {str(c.folder): sha256(c.folder / "input.md") for c in chunks},
        "texts": {unit: {"path": str(p), "sha256": sha256(p), "first_pdf_page": first}
                  for unit, p, first in texts},  # fmt: skip
        "counts": counts(figures),
        "list_entries_without_figure": [
            asdict(e)
            for e in front.entries
            if (e.kind, e.number, e.title) not in placed
        ],
        "citations_of_unlisted_numbers": unmatched,
        "figures": [asdict(f) for f in figures],
    }


def write_report(out: Path, report: Dict[str, Any]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / REPORT_NAME).write_text(json.dumps(report, indent=2), encoding="utf-8")


def counts(figures: Iterable[Any]) -> Dict[str, Any]:
    from collections import Counter

    figures = list(figures)
    return {
        "figures": len(figures),
        "by_kind": dict(Counter(f.kind for f in figures)),
        "by_source": dict(Counter(f.source for f in figures)),
        "by_match": dict(Counter(f.match or "unlisted" for f in figures)),
        "fallbacks": sum(1 for f in figures if f.chosen_because.startswith("FALLBACK")),
        "references": sum(len(f.references) for f in figures),
    }


# --- the pipeline: a book's figures, built once ----------------------------------------------------
def ensure(
    s3: Any, bucket: str, book: str, book_dir: Path, workdir: Path
) -> Optional[Dict[str, Any]]:
    """The book's figures report (``figures.json``), building and storing it when the book is an
    OCR'd PDF whose figures are missing or older than its PDF / OCR submission. None for a book
    that is not one (no OCR chunk manifest, not a PDF, no figure lists) or when a step failed
    (said LOUDLY: its chapters then get no figures). Never raises.

    ``book_dir``: the book's local ``contentrepository/<book>/`` (Phase 1 syncs it whole, so its
    PDF and any earlier ``_figures/`` are there)."""
    try:
        return _ensure(s3, bucket, book, book_dir, workdir) or _stale(book, book_dir)
    except (
        Exception
    ) as e:  # noqa: BLE001 - the parse goes on with the stored figures, loudly
        from src.utils.observe import observe_error

        kept = stored(book_dir)
        observe_error(
            "figures",
            "build_failed",
            book=book,
            reason=f"{type(e).__name__}: {str(e)[:160]}",
            kept_stored=bool(kept),
            note="the book's figures (maps, charts, photographs) were not (re)built: its "
            "chapters keep the stored ones if any (kept_stored) - fix the cause and re-run "
            "Phase 1 for the book",
        )
        return kept


def _stale(book: str, book_dir: Path) -> Optional[Dict[str, Any]]:
    """The stored report when no current one could be made (no manifest any more, no local PDF,
    no figure lists): kept - a parse never drops figures - but said, since it may be another
    submission's."""
    kept = stored(book_dir)
    if kept is not None:
        from src.utils.observe import observe_warn

        observe_warn(
            "figures",
            "stale_figures_kept",
            book=book,
            stored_submission=str(kept.get("ocr_submission") or "")[:40],
            note="no current figures could be built (see figures.not_applicable / "
            "pdf_not_local): the stored ones are attached - re-run Phase 1 once the book's "
            "PDF and OCR are in place, or delete its _figures/ if the book has none",
        )
    return kept


def _ensure(
    s3: Any, bucket: str, book: str, book_dir: Path, workdir: Path
) -> Optional[Dict[str, Any]]:
    from src.ingestion import ocr_chunk_manifest as cm

    manifest = cm.read_manifest(s3, bucket, book)
    pdf = _local_pdf(manifest, book, book_dir)
    if manifest is None or pdf is None:
        logger.info(
            "OBSERV figures.not_applicable book=%s reason=%s",
            book,
            "no_ocr_manifest" if manifest is None else "source_not_a_local_pdf",
        )
        return None
    local = book_dir / FIGURES_DIR
    current = _current(local, manifest.submission, pdf)
    if current is not None:
        logger.info(
            "OBSERV figures.current book=%s figures=%d (PDF and OCR unchanged: not rebuilt)",
            book,
            len(current.get("figures") or []),
        )
        return current
    chunks = _download_chunks(s3, bucket, book, manifest, workdir / "ocr")
    front = front_of(chunks)
    if front is None:
        logger.info(
            "OBSERV figures.not_applicable book=%s reason=no_figure_lists", book
        )
        return None
    out = workdir / "figures"
    if stored(book_dir) is not None:  # a re-run keeps the earlier figure IDs
        out.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local / REPORT_NAME, out / REPORT_NAME)
    report = extract(pdf, chunks, front, out)
    report["ocr_submission"] = manifest.submission
    write_report(out, report)
    _store(s3, bucket, book, out, local)
    _observe_built(book, report)
    return report


def _local_pdf(manifest: Any, book: str, book_dir: Path) -> Optional[Path]:
    """The book's source PDF in its synced folder (the manifest's ``source_key``)."""
    key = str(getattr(manifest, "source_key", "") or "")
    if not key.lower().endswith(".pdf"):
        return None  # not a PDF source: its figures, if any, come from its text's links
    prefix = f"contentrepository/{book}/"
    rest = key[len(prefix) :] if key.startswith(prefix) else ""
    path = book_dir / rest
    if rest and ".." not in rest.split("/") and path.is_file():
        return path
    from src.utils.observe import observe_warn

    observe_warn(
        "figures",
        "pdf_not_local",
        book=book,
        source_key=key[:160],
        note="the book was OCR'd from this PDF but it is not in the book's synced folder: no "
        "figures are built - check the source key and the Phase 1 sync of the book prefix",
    )
    return None


def _current(local: Path, submission: str, pdf: Path) -> Optional[Dict[str, Any]]:
    """The stored report when it was built from this OCR submission and this PDF, else None."""
    report = _read_report(local / REPORT_NAME)
    if report is None or report.get("ocr_submission") != submission:
        return None
    return report if report.get("pdf_sha256") == sha256(pdf) else None


def _download_chunks(
    s3: Any, bucket: str, book: str, manifest: Any, root: Path
) -> List[Any]:
    """The submission's OCR chunks (markdown, metadata and picture crops), in page order."""
    from src.ingestion import ocr_chunk_manifest as cm
    from src.ingestion import pdf_figures as F

    chunks = []
    for chunk in manifest.chunks:
        prefix = f"{cm.book_prefix(book)}{chunk + '/' if chunk else ''}input/"
        folder = root / (chunk or "whole") / "input"
        _download_prefix(s3, bucket, prefix, folder)
        first = cm.first_page(chunk)
        if first is None or not (folder / "input.md").is_file():
            raise ValueError(
                f"OCR chunk {chunk or 'whole'!r}: no page range or input.md"
            )
        chunks.append(F.Chunk(first, folder))
    return sorted(chunks, key=lambda c: c.first_page)


def _download_prefix(s3: Any, bucket: str, prefix: str, folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for page in s3.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=prefix
    ):
        for obj in page.get("Contents") or []:
            name = obj["Key"][len(prefix) :]
            if name and "/" not in name and not name.startswith("."):
                s3.download_file(bucket, obj["Key"], str(folder / name))


def _store(s3: Any, bucket: str, book: str, out: Path, local: Path) -> None:
    """``out``'s files then its report to ``contentrepository/<book>/_figures/`` (the report
    last: a report never names a file not yet stored), and a local copy for this parse.
    """
    prefix = figures_prefix(book)
    for path in sorted((out / "files").iterdir()):
        if path.is_file():
            s3.upload_file(str(path), bucket, f"{prefix}files/{path.name}")
    s3.upload_file(str(out / REPORT_NAME), bucket, f"{prefix}{REPORT_NAME}")
    try:  # this parse's copy: built aside, then swapped in
        staged = local.with_name(local.name + ".new")
        shutil.rmtree(staged, ignore_errors=True)
        shutil.copytree(out, staged, ignore=shutil.ignore_patterns(".cache"))
        shutil.rmtree(local, ignore_errors=True)
        staged.rename(local)
    except (
        OSError
    ) as e:  # stored in S3 already; this parse attaches from the report itself
        from src.utils.observe import observe_warn

        observe_warn(
            "figures",
            "local_copy_failed",
            book=book,
            reason=f"{type(e).__name__}: {str(e)[:120]}",
            note="the figures are stored in S3 but not copied into this task: they are still "
            "attached (from the report); figure files read later come from S3",
        )


def _observe_built(book: str, report: Dict[str, Any]) -> None:
    from src.utils.observe import observe_warn

    logger.info("OBSERV figures.built book=%s counts=%s", book, report["counts"])
    missing = report.get("list_entries_without_figure") or []
    if missing:
        observe_warn(
            "figures",
            "list_entries_without_figure",
            book=book,
            count=len(missing),
            entries="; ".join(
                f"{e['kind']} {e.get('number') or ''} {e['title']!r}"[:60]
                for e in missing[:8]
            ),
            note="the book lists these figures but none was found in its PDF: look at the "
            "pages (a missing plate, or a list entry the OCR garbled) - see figures.json",
        )


# --- Phase 1: a book's chapters get its figures ---------------------------------------------------
_IMAGE_KINDS = ("illustration", "chart", "figure")


def attach_book(
    docs: Sequence[Any], report: Optional[Dict[str, Any]], book: str
) -> Dict[str, int]:
    """Give a book's parsed chapters (``docs``: parser MarkdownDocuments in book order, notes
    documents left out) their figures IN PLACE:

    * each picture (photo, chart, unlisted figure) to exactly ONE document - the last that
      starts on or before its PDF page (``Paragraph.page_number``): a picture-only page has no
      paragraph of its own (Phase 0 drops picture paragraphs), so a page between two chapters,
      or two sections, goes to the earlier one, and a page two sections share to one of them;
    * each numbered map to every document that prints it (as above) or cites it ("Map VIII"),
      with that document's citing paragraphs - Phase 3 keeps ONE map per figure and gathers the
      footprints (map_common.find_or_create_map_by_url, keyed by its url).

    A page before the first text page or after the last (front matter, the map envelope's
    fold-outs) is printed in no document: a fold-out map reaches the documents citing it. A map
    a document links already (a HyperWar link) is not added twice. Returns counts: images,
    maps, and pictures outside the text's pages (``unplaced``)."""
    figures = _figures_of(report)
    pages = {i: p for i, d in enumerate(docs) if (p := _pdf_pages(d))}
    if not figures or not pages:
        return {"images": 0, "maps": 0, "unplaced": 0}
    text = (
        sorted((min(p), i) for i, p in pages.items()),
        max(max(p) for p in pages.values()),
    )
    counts = _attach_images(docs, figures, book, text)
    maps = _numbered_maps(figures)
    for i, doc in enumerate(docs):
        added = _chapter_maps(maps, doc, book, text, i)
        doc.maps.extend(added)
        counts["maps"] += len(added)
    return counts


def _attach_images(docs, figures, book: str, text) -> Dict[str, int]:
    """Each picture to the ONE document whose pages it falls in (``_owner``)."""
    starts, last = text
    counts = {"images": 0, "maps": 0, "unplaced": 0}
    for f in figures:
        if f.get("kind") not in _IMAGE_KINDS:
            continue
        owner = _owner(starts, int(f.get("pdf_page") or 0), last)
        if owner is None:
            counts["unplaced"] += 1
        else:
            docs[owner].images.append(_image(f, docs[owner], book))
            counts["images"] += 1
    return counts


def _figures_of(report: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [f for f in (report or {}).get("figures") or [] if isinstance(f, dict)]


def _pdf_pages(doc: Any) -> List[int]:
    return [p.page_number for p in doc.paragraphs if isinstance(p.page_number, int)]


def _owner(starts: List[Tuple[int, int]], page: int, last: int) -> Optional[int]:
    """The document (index) that starts last on or before ``page``; None outside the text's
    pages (before the first document, after the ``last`` text page)."""
    before = [i for first, i in starts if first <= page]
    return before[-1] if before and page <= last else None


def _chapter_maps(maps, doc: Any, book: str, text, index: int) -> List[Any]:
    """The numbered maps document ``index`` prints or cites, but does not link already
    (``text``: the documents' (first page, index) starts and the last text page)."""
    starts, last = text
    have = {m.map_id for m in doc.maps}
    found = (_map(f, doc, book, _owner(starts, int(f.get("pdf_page") or 0), last) == index)
             for f in maps)  # fmt: skip
    return [m for m in found if m is not None and m.map_id not in have]


def _anchor(doc: Any, pdf_page: int) -> int:
    """The paragraph a figure follows: the last one on or before its page (else the first)."""
    before = [
        p.absolute_number
        for p in doc.paragraphs
        if isinstance(p.page_number, int) and p.page_number <= pdf_page
    ]
    return max(before) if before else doc.paragraphs[0].absolute_number


def _image(f: Dict[str, Any], doc: Any, book: str) -> Any:
    from src.models import Image

    return Image(
        type="embedded",
        resource_id=f.get("figure_id"),
        url=figure_key(book, f["file"]),
        alt_text=str(
            f.get("title") or ""
        ),  # the book's list title (never the model's text)
        caption=f.get("caption"),
        paragraph_number=_anchor(doc, int(f["pdf_page"])),
    )


def _numbered_maps(figures: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One figure per numbered map: the largest (a map's legend or scale bar may be a second
    figure of the same entry)."""
    best: Dict[str, Dict[str, Any]] = {}
    for f in figures:
        if f.get("kind") != "map" or not f.get("number"):
            continue
        area = int(f.get("width") or 0) * int(f.get("height") or 0)
        old = best.get(f["number"])
        if old is None or area > int(old.get("width") or 0) * int(
            old.get("height") or 0
        ):
            best[f["number"]] = f
    return list(best.values())


def _map(f: Dict[str, Any], doc: Any, book: str, printed: bool) -> Any:
    """The document's map entry for ``f``, or None when it neither prints (``printed``) nor
    cites it."""
    from src.models import Map
    from src.parser import map_reference_footprint

    map_id = f"Map {f['number']}"
    refs = map_reference_footprint(map_id, doc.paragraphs)
    if not refs and not printed:
        return None
    first = refs[0]["paragraph_number"] if refs else _anchor(doc, int(f["pdf_page"]))
    return Map(
        url=figure_key(book, f["file"]),
        description=str(f.get("title") or map_id),
        map_id=map_id,
        paragraph_number=first,
        references=refs,
    )


_MAP_CITATION = re.compile(r"\bMaps?\s+(?P<num>[IVXLC]+|\d+)\b")


class Coverage:
    """A book's figure coverage over its parse: the maps its text cites against the maps and
    images its chapters got. ``observe`` says it once per book - LOUDLY when the text cites maps
    and no chapter got one (the 2026-11-15 USA-E-XChannel gap: 30 maps cited, 0 captured).
    """

    def __init__(self, book: str) -> None:
        self.book = book
        self.cited: set = set()
        self.maps = self.images = self.front_matter = 0

    def add(self, doc: Any) -> None:
        for p in doc.paragraphs:
            self.cited.update(m["num"] for m in _MAP_CITATION.finditer(p.text or ""))
        self.images += len(doc.images)
        self.maps += len(doc.maps)

    def observe(self, unplaced: int = 0) -> None:
        logger.info(
            "OBSERV figures.coverage book=%s maps_cited=%d maps=%d images=%d "
            "pictures_outside_the_text=%d",
            self.book,
            len(self.cited),
            self.maps,
            self.images,
            unplaced,
        )
        if self.cited and not self.maps:
            from src.utils.observe import observe_error

            observe_error(
                "figures",
                "maps_cited_none_captured",
                book=self.book,
                maps_cited=len(self.cited),
                examples=",".join(sorted(self.cited)[:8]),
                note="the text cites these maps but no chapter has one: an OCR'd PDF whose "
                "figures were not built (see figures.build_failed / pdf_not_local), or a web "
                "source without map links - check contentrepository/<book>/_figures/",
            )


# --- reading a figure back (image / map download steps) --------------------------------------------
def read_figure(key: str) -> Tuple[Optional[bytes], Optional[str]]:
    """(bytes, extension) of the figure file ``key``, or (None, None): from the local synced
    content when present (Phase 1), else from S3 (``S3_BUCKET``). Only a figure key is read.
    """
    ext = _figure_ext(key)
    if ext is None:
        return None, None
    body = _read_local(key) or _read_s3(key)
    if body is None or len(body) > _MAX_FIGURE_BYTES:
        logger.warning(
            "OBSERV figures.read_failed key=%s reason=%s",
            key[:160],
            "missing" if body is None else "too_large",
        )
        return None, None
    return body, ext


def _read_local(key: str) -> Optional[bytes]:
    root = Path(os.environ.get("CONTENT_SYNC_ROOT", "/app"))
    path = root / key
    return path.read_bytes() if path.is_file() else None


def _read_s3(key: str) -> Optional[bytes]:
    bucket = os.environ.get("S3_BUCKET", "")
    if not bucket:
        return None
    import boto3

    try:
        resp = boto3.client("s3").get_object(Bucket=bucket, Key=key)
        return resp["Body"].read(_MAX_FIGURE_BYTES + 1)
    except Exception as e:  # noqa: BLE001 - a missing figure is reported by the caller
        logger.debug("figure read failed %s: %s", key, e)
        return None
