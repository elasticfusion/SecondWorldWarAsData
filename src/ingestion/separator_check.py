"""Post-merge OCR integrity check: page-separator count vs. PDF page count.

Why
---
Chandra OCR writes paginated chunk markdown with a page separator between
consecutive pages (``src.ingestion.chunk_pages.PAGE_SEPARATOR_RE``). The merged
book markdown feeds the OOB parsers / extraction, which rely on per-physical-page
mapping. If a page produced **no separator** (e.g. a blank/near-blank scan Chandra
emitted nothing for), the page count derived from the markdown drifts from the
real PDF page count — silently shifting every downstream per-page mapping.

This module counts the pages the merged markdown actually represents (via the same
separator logic the rest of the pipeline uses) and compares it to the PDF page
count, listing the physical pages that have NO content segment so an operator can
eyeball them (usually genuine blank pages, occasionally a real OCR miss).

Pure + import-safe: the core (:func:`check_separators`) takes already-fetched
chunk markdown; the AWS/S3 wiring lives in the CLI (``scripts/ocr_separator_check``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from src.ingestion.chunk_pages import chunk_start_page_from_dir, split_pages


@dataclass
class SeparatorReport:
    pdf_pages: int
    represented_pages: int  # distinct physical pages the markdown represents
    empty_pages: List[int] = field(default_factory=list)  # pages with blank content
    legacy_chunks: List[str] = field(default_factory=list)  # dirs w/o page range
    per_chunk: Dict[str, int] = field(default_factory=dict)  # chunk_dir -> page count

    @property
    def ok(self) -> bool:
        """True when the markdown represents exactly the PDF's pages and none are
        empty. A mismatch or any empty page is worth an operator look before the
        markdown feeds parsers."""
        return (
            self.represented_pages == self.pdf_pages
            and not self.empty_pages
            and not self.legacy_chunks
        )

    def summary(self) -> str:
        status = "OK" if self.ok else "MISMATCH"
        lines = [
            f"[{status}] represented {self.represented_pages} page(s) "
            f"vs {self.pdf_pages} PDF page(s)"
        ]
        if self.empty_pages:
            lines.append(
                f"  empty/no-content pages ({len(self.empty_pages)}): "
                + ", ".join(str(p) for p in self.empty_pages)
            )
        if self.legacy_chunks:
            lines.append(
                "  legacy chunk dirs (no page range — cannot map): "
                + ", ".join(self.legacy_chunks)
            )
        return "\n".join(lines)


def check_separators(
    chunks: List[Tuple[str, str]],
    pdf_pages: int,
    *,
    min_content_chars: int = 1,
) -> SeparatorReport:
    """Compare the pages represented by paginated chunk markdown to ``pdf_pages``.

    ``chunks`` is ``[(chunk_dir, markdown)]`` — the ``chunk-pNNNN-NNNN`` dir name
    (used to recover the chunk's 1-based start page) and that chunk's merged
    markdown. A chunk dir without a parseable page range is reported in
    ``legacy_chunks`` (it cannot be mapped and is excluded from the count — exactly
    how the merge path treats it).

    A page whose content (stripped) is shorter than ``min_content_chars`` is
    flagged as empty — the signal for a page that produced no real separator/text.
    """
    report = SeparatorReport(pdf_pages=pdf_pages, represented_pages=0)
    seen_pages = set()
    for chunk_dir, markdown in chunks:
        start = chunk_start_page_from_dir(chunk_dir)
        if start is None:
            report.legacy_chunks.append(chunk_dir)
            continue
        pages = split_pages(markdown, start)
        report.per_chunk[chunk_dir] = len(pages)
        for phys_page, text in pages:
            seen_pages.add(phys_page)
            if len(text.strip()) < min_content_chars:
                report.empty_pages.append(phys_page)
    report.represented_pages = len(seen_pages)
    report.empty_pages.sort()
    report.legacy_chunks.sort()
    return report
