"""Promote a book's merged OCR to the chapters Phase 1 parses (S3 I/O for src/ingestion/
ocr_publish). Used by the OCR merge Lambda (no validation URL) and by the Phase 0 cross-check
task (``phase0_ocr_crosscheck.py``, with one).

Writes, for book B:

* ``ocr-output/B/merged/raw.md``         - the merged OCR exactly as Chandra wrote it (kept:
  the bucket keeps every superseded version of ocr-output/ and contentrepository/);
* ``ocr-output/B/merged/postprocess.json`` - what sanitising, cross-checking and normalising
  did, chapter by chapter;
* ``contentrepository/B/chapterN/chapterN-meta.yaml`` then ``chapterN-content.md`` for each
  published chapter (meta first: the content's upload event starts the parse).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from src.ingestion import book_metadata
from src.ingestion import ocr_publish as P

logger = logging.getLogger(__name__)

Translate = Callable[[str], str]


def raw_key(book: str) -> str:
    return f"ocr-output/{book}/merged/raw.md"


def log_key(book: str) -> str:
    return f"ocr-output/{book}/merged/postprocess.json"


def chapter_keys(book: str, number: int) -> Tuple[str, str]:
    base = f"contentrepository/{book}/chapter{number}/chapter{number}"
    return f"{base}-meta.yaml", f"{base}-content.md"


def read_parts(
    s3: Any, bucket: str, keys: Sequence[str], first_pages: Sequence[Optional[int]]
) -> List[Tuple[Optional[int], str]]:
    return [
        (first, s3.get_object(Bucket=bucket, Key=k)["Body"].read().decode("utf-8"))
        for k, first in zip(keys, first_pages)
    ]


def keep_raw(s3: Any, bucket: str, book: str, merged: str) -> str:
    key = raw_key(book)
    s3.put_object(
        Bucket=bucket, Key=key, Body=merged.encode("utf-8"), ContentType="text/markdown"
    )
    return key


def promote(
    s3: Any,
    bucket: str,
    book: str,
    parts: Sequence[Tuple[Optional[int], str]],
    *,
    source_key: str,
    validation_url: str = "",
    translate: Optional[Translate] = None,
    fetch: Optional[Callable[[str], Optional[str]]] = None,
    still_current: Optional[Callable[[], None]] = None,
) -> List[str]:
    """Sanitise, (translate,) split, (cross-check and correct, with ``validation_url``),
    normalise and publish. Returns the content keys written, in chapter order.

    ``still_current`` (if given) is called just before anything is published and raises when
    the book has been re-submitted meanwhile, so a long run never publishes over a newer one.
    """
    clean, sanitise_log = P.sanitised(parts)
    if translate is not None:
        clean = [(first, translate(text)) for first, text in clean]
    sections = P.sections_of(clean)
    check = None
    if validation_url:
        from src.ingestion import ocr_crosscheck

        # the cross-check and the metadata request read the same page: fetch it once
        fetch = _fetch_once(fetch or ocr_crosscheck.fetch_html)
        check = ocr_crosscheck.correct_sections(sections, validation_url, fetch)
    pub = P.publication(sections, P.book_vocabulary(clean))
    _alert_unchecked(book, validation_url, check, pub)
    # the book's own title page and imprint, read once for the whole book (src/ingestion/book_metadata)
    book_meta = book_metadata.for_import(clean, _validation_page(validation_url, fetch))
    log = {
        "book": book,
        "source_key": source_key,
        "validation_url": validation_url,
        "sanitise": sanitise_log.to_dict(),
        "crosscheck": (
            check.to_dict()
            if check
            else {"status": "no validation URL: not cross-checked"}
        ),
        "publication": pub.to_dict(),
        "metadata": book_meta
        or {"status": "nothing established (see the metadata gap marker)"},
    }
    s3.put_object(
        Bucket=bucket,
        Key=log_key(book),
        Body=json.dumps(log, indent=1, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )
    if still_current is not None:
        still_current()
    keys, first_meta = _write_chapters(s3, bucket, book, pub, source_key, book_meta)
    _mark_gap(s3, bucket, book, pub, first_meta)
    _warn_stale(s3, bucket, book, pub)
    logger.info(
        "OBSERV ocr_promote.done book=%s chapters=%d held_back=%s sanitise=%s crosscheck=%s",
        book,
        len(pub.chapters),
        pub.summary()["held_back"],
        sanitise_log.summary(),
        check.summary() if check else "none (no validation URL)",
    )
    return keys


def _alert_unchecked(book: str, url: str, check: Any, pub: P.Publication) -> None:
    """The book was published, but not all of it as asked: tell a person what to look at."""
    from src.utils.observe import observe_warn

    if check is not None:
        bad = check.unchecked_chapters()
        if bad or not check.chapters:
            observe_warn(
                "ocr_promote",
                "crosscheck_incomplete",
                note=(
                    "published WITHOUT correcting these chapters against the validation URL "
                    "(unreachable page, wrong page, or an error): see ocr-output/<book>/merged/"
                    "postprocess.json; fix the URL/page and re-drive the merge"
                ),
                book=book,
                validation_url=url,
                chapters={f"{c.number}": c.status for c in bad} or "no chapters",
            )
    odd = [h for h in pub.held_back if h["kind"] in ("chapter", "appendix")]
    if odd:
        observe_warn(
            "ocr_promote",
            "unreadable_headings",
            note=(
                "chapter/appendix headings whose number could not be read were NOT published "
                "(OCR slip such as 'CHAPTER XIl'): correct the heading in the raw merge and "
                "re-drive the merge"
            ),
            book=book,
            sections=[h["label"] for h in odd],
        )


def _fetch_once(
    fetch: Callable[[str], Optional[str]],
) -> Callable[[str], Optional[str]]:
    """``fetch``, reading each URL at most once (a failure is not remembered: it raises again)."""
    pages: Dict[str, Optional[str]] = {}

    def once(url: str) -> Optional[str]:
        if url not in pages:
            pages[url] = fetch(url)
        return pages[url]

    return once


def _validation_page(url: str, fetch: Optional[Callable[[str], Optional[str]]]) -> str:
    """The validation page, a fallback source for the book's metadata ("" without one)."""
    if not url:
        return ""
    try:
        from src.ingestion import ocr_crosscheck

        return (fetch or ocr_crosscheck.fetch_html)(url) or ""
    except Exception as e:  # noqa: BLE001 - only a hint
        logger.warning(
            "OBSERV ocr_promote.validation_page_unreadable err=%s", str(e)[:200]
        )
        return ""


def _chapter_meta(
    s3: Any, bucket: str, book: str, c: P.Chapter, source_key: str, book_meta: dict
) -> Tuple[str, bytes, dict]:
    """(key, YAML, fields) of chapter ``c``'s meta: the book's metadata (the title-page request)
    and the chapter's title, with an operator's edit of the meta at ``key`` kept
    (book_metadata.resolve).
    """
    from src.ingestion.chapter_meta import build_meta_yaml

    meta_key, _ = chapter_keys(book, c.number)
    # a document that is one chapter (no chapter headings) is titled by its title page
    title = c.title or str(book_meta.get("book_title", ""))
    fields = book_metadata.resolve(
        book_metadata.existing(s3, bucket, meta_key), book_meta, title
    )
    title = str(fields.pop("chapter_title", "") or title)
    body = build_meta_yaml(
        book,
        source_url="",
        source_url_basis="ocr",
        source_key=source_key or f"ocr-output/{book}/",
        chapter_title=title,
        extra={
            "chapter_number": str(c.number),
            "section_label": c.label,
            "pdf_pages": f"{c.first_page}-{c.last_page}" if c.first_page else "",
            "page_basis": "pdf",
            **fields,
        },
    )
    return meta_key, body, {**fields, "chapter_title": title}


def _write_chapters(
    s3: Any,
    bucket: str,
    book: str,
    pub: P.Publication,
    source_key: str,
    book_meta: Optional[dict] = None,
) -> Tuple[List[str], dict]:
    """Every chapter's meta, then every chapter's content (each content upload starts a parse).
    Returns (content keys, the first chapter's metadata fields)."""
    meta_changed = set()
    first: dict = {}
    for c in pub.chapters:
        meta_key, body, fields = _chapter_meta(
            s3, bucket, book, c, source_key, book_meta or {}
        )
        first = first or fields
        if _put_if_changed(s3, bucket, meta_key, body):
            meta_changed.add(c.number)
    keys = []
    for c in pub.chapters:
        _, content_key = chapter_keys(book, c.number)
        text = c.markdown.encode("utf-8")
        if c.number in meta_changed:
            # a changed meta alone fires no event: write the content again so Phase 1 re-parses
            s3.put_object(Bucket=bucket, Key=content_key, Body=text)
        else:
            _put_if_changed(s3, bucket, content_key, text)
        keys.append(content_key)
    return keys, first


def _mark_gap(s3: Any, bucket: str, book: str, pub: P.Publication, first: dict) -> None:
    """The needs-review metadata marker, for the fields the title-page request (and any operator
    edit) still left empty; an earlier marker is cleared when nothing is missing."""
    if not pub.chapters:
        return
    gaps = book_metadata.missing(first)
    from src.ingestion import review_reject

    if not gaps:
        review_reject.clear_metadata_gap(s3, bucket, book)
        return
    meta_key, _ = chapter_keys(book, pub.chapters[0].number)
    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
    review_reject.mark_metadata_gap(
        s3, bucket, f"ocr-output/{book}/", book, region, fields=gaps, meta_key=meta_key
    )


def _put_if_changed(s3: Any, bucket: str, key: str, body: bytes) -> bool:
    """Write ``body`` unless the object already holds exactly it: every write fires the content
    upload event that starts a Phase 1 (and, after it, the LLM spend), so a re-drive of the merge
    must not republish chapters it did not change. (S3's ETag of a small, single-part object is
    its MD5.) Returns whether it wrote."""
    try:
        etag = str(s3.head_object(Bucket=bucket, Key=key).get("ETag", "")).strip('"')
        if etag == hashlib.md5(body, usedforsecurity=False).hexdigest():
            logger.info("OBSERV ocr_promote.unchanged key=%s (not rewritten)", key)
            return False
    except Exception:  # noqa: BLE001 - absent or unreadable: write it
        pass
    s3.put_object(Bucket=bucket, Key=key, Body=body)
    return True


def _warn_stale(s3: Any, bucket: str, book: str, pub: P.Publication) -> None:
    """A chapter of an earlier promotion that this one did not write would be parsed again with
    the new ones: say so (never delete content)."""
    try:
        wanted = {chapter_keys(book, c.number)[1] for c in pub.chapters}
        stale = []
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(
            Bucket=bucket, Prefix=f"contentrepository/{book}/chapter"
        ):
            for obj in page.get("Contents", []):
                k = obj["Key"]
                if k.endswith("-content.md") and k not in wanted:
                    stale.append(k)
    except Exception as e:  # noqa: BLE001 - a warning must never block the promotion
        logger.warning("OBSERV ocr_promote.stale_check_failed book=%s err=%s", book, e)
        return
    if stale:
        from src.utils.observe import observe_warn

        observe_warn(
            "ocr_promote",
            "stale_chapters",
            note=(
                "chapters from an earlier promotion of this book were not rewritten and will be "
                "parsed with the new ones: remove them (e2e_reset --wipe-title) and re-run Phase 1"
            ),
            book=book,
            stale=stale[:20],
        )
