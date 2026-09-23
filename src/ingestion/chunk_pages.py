"""Map Chandra chunk markdown to physical PDF pages, for table-recovery routing.

The gap this closes
-------------------
Chandra OCR writes output **per chunk** (a page range) as one merged
``chunk-*/input/input.md`` — not one file per page. The PP-StructureV3
table-recovery worker, however, needs to know the **physical PDF page** of a
flattened table so it can render that page image. There was no bridge between
"a flattened table exists somewhere in this chunk's markdown" and "it is on
physical page N".

Chandra's ``--paginate_output`` flag supplies the missing signal: it inserts a
page separator between consecutive pages in the merged markdown. Verified against
``chandra-ocr==0.2.0`` (``chandra/scripts/cli.py``): for the k-th page result
(0-based ``page_num``), when ``page_num > 0`` it appends::

    "\\n\\n{page_num}" + "-" * 48 + "\\n\\n"

i.e. a line consisting of the *0-based index of the page that follows the
separator* immediately followed by 48 dashes. The first page has no preceding
separator. So splitting on that separator yields the chunk's pages in order, and

    physical_page(page_i) = chunk_start_page + page_i        # 1-based

where ``chunk_start_page`` is the 1-based physical page of the chunk's first
page (recoverable from the ``chunk-pNNNN-NNNN`` output-dir name).

This module is pure (no network/AWS) and reuses
:func:`src.ingestion.table_recovery.page_needs_recovery` for detection, so the
routing decision stays identical to the rest of the pipeline.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from src.ingestion.table_recovery import page_needs_recovery

# Chandra ``--paginate_output`` separator: a line of "<0-based-page-index>" +
# exactly 48 dashes. Anchored to a full line so it never matches a dashed rule
# or table border inside content.
PAGE_SEPARATOR_RE = re.compile(r"^(\d+)-{48}$", re.MULTILINE)

# The dir-name pattern produced by submit_ocr_job._chunk_dir: chunk-pNNNN[-NNNN].
_CHUNK_DIR_RE = re.compile(r"chunk-p(\d+)(?:-(\d+))?$")


def chunk_start_page_from_dir(chunk_dir: str) -> Optional[int]:
    """Recover a chunk's first physical (1-based) page from its output-dir name.

    ``chunk-p0151-0200`` -> 151. Legacy index-only dirs (``chunk-003``) carry no
    page information and return ``None`` (caller must supply the start page).
    """
    stem = chunk_dir.rstrip("/").rsplit("/", 1)[-1]
    match = _CHUNK_DIR_RE.search(stem)
    if not match:
        return None
    return int(match.group(1))


def split_pages(markdown: str, chunk_start_page: int) -> List[Tuple[int, str]]:
    """Split paginated chunk markdown into ``[(physical_page, page_markdown)]``.

    ``chunk_start_page`` is the 1-based physical page of the chunk's first page.
    Pages are numbered sequentially from there. If no page separators are found
    (e.g. a single-page chunk, or OCR run without ``--paginate_output``), the
    whole markdown is returned as one page at ``chunk_start_page`` — a safe
    degrade: the caller still gets a routable unit, just without finer page
    resolution.
    """
    segments = PAGE_SEPARATOR_RE.split(markdown)
    # re.split with one capture group yields: [seg0, cap1, seg1, cap2, seg2, ...]
    # The captured group (the separator's page index) is discarded here; ordering
    # is positional. Keep only the text segments (even indices).
    texts = segments[::2]
    pages: List[Tuple[int, str]] = []
    for offset, text in enumerate(texts):
        pages.append((chunk_start_page + offset, text))
    return pages


def flattened_pages_in_chunk(markdown: str, chunk_start_page: int) -> List[int]:
    """Return the physical page numbers in a chunk that contain a flattened table.

    Splits the (paginated) chunk markdown into physical pages and runs the
    shared flattened-table detector on each. Only pages that actually flattened
    are returned — so downstream PP-StructureV3 recovery is spent only where
    Chandra failed, never on prose/normal-table pages.
    """
    hits: List[int] = []
    for physical_page, page_md in split_pages(markdown, chunk_start_page):
        if page_needs_recovery(page_md):
            hits.append(physical_page)
    return hits
