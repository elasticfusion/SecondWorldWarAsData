"""Authoritative per-book run-scope count (source-vs-actual reconciliation).

The run-scope guard (``run_scope_guard.enforce_scope``) protects against a scoped run silently
becoming a whole-corpus run. The old design used only a magic-50 ceiling + a hand-declared
``expected_count`` threaded through the trigger → which (a) is an arbitrary threshold, (b) is blind
to UNDER-production (a silent drop below the real count), and (c) died at the Phase 1→2 handoff when
the declaration wasn't carried across.

This module records an INDEPENDENT authoritative count at Phase 1 — the number of parse artifacts
Phase 1 actually PRODUCED for a book — and lets later phases read it as the expected work-set size.
"Independent" is the whole point: the expected count is written by the UPSTREAM phase, NOT re-globbed
from the same directory the consuming phase is about to process (that would validate the set against
itself and catch nothing).

Reconciliation rule ("don't exceed"): the consuming phase must never process MORE than the recorded
count (fan-out = always a defect → fail loud). Processing FEWER is allowed — a full run equals the
recorded count; an incremental resume legitimately processes only the pending subset. See
``run_scope_guard.enforce_scope``.

Storage: ``output/manifests/run_scope/<book>.json`` (S3-native; lands under ``output/`` so the
existing Phase background/final sync persists it — aligns with the DynamoDB-for-JSON deprecation).
Reads are fail-safe: a missing/unreadable record returns ``None``, so the guard falls back to the
magic-50 ceiling (no recorded count → the old behaviour, never a hard failure from a missing file).
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Book names become a path segment; keep them to a safe token (defense-in-depth — the book
# name originates from the content tree / trigger, but this builds a filesystem path).
_SAFE_BOOK_RE = re.compile(r"[A-Za-z0-9._-]+")


def _safe_book(book: str) -> Optional[str]:
    b = (book or "").strip()
    if not b or ".." in b or "/" in b or "\\" in b or not _SAFE_BOOK_RE.fullmatch(b):
        return None
    return b


def _record_path(output_root: Path, book: str) -> Optional[Path]:
    safe = _safe_book(book)
    if safe is None:
        return None
    return Path(output_root) / "manifests" / "run_scope" / f"{safe}.json"


def record_scope(
    output_root: Path, book: str, section_count: int, *, phase: str = "phase1"
) -> bool:
    """Record the authoritative section count for ``book`` (called by the producing phase).

    Returns True on write, False if skipped (unsafe book / write error). Fail-safe: never raises —
    a recording failure must not crash the phase (the consumer falls back to the ceiling).
    """
    try:
        path = _record_path(output_root, book)
        if path is None:
            logger.warning(
                "OBSERV run_scope.record status=skip reason=unsafe_book book=%r", book
            )
            return False
        if not isinstance(section_count, int) or section_count < 0:
            logger.warning(
                "OBSERV run_scope.record status=skip reason=bad_count book=%s count=%r",
                book,
                section_count,
            )
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "book": book,
            "section_count": section_count,
            "phase": phase,
            "recorded_at": int(time.time()),
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(path)  # atomic
        logger.info(
            "OBSERV run_scope.record book=%s section_count=%d phase=%s",
            book,
            section_count,
            phase,
        )
        return True
    except Exception as e:  # noqa: BLE001 - recording must never crash the phase
        logger.warning(
            "OBSERV run_scope.record status=error book=%s reason=%s",
            book,
            type(e).__name__,
        )
        return False


def read_scope(output_root: Path, book: str) -> Optional[int]:
    """Read the recorded authoritative section count for ``book``, or None if not recorded /
    unreadable. Fail-safe: None means 'no independent expectation' → caller uses the ceiling.
    """
    try:
        path = _record_path(output_root, book)
        if path is None or not path.is_file():
            return None  # legitimately not recorded (e.g. first run) — caller uses the ceiling
        # The file EXISTS: a parse/validation failure here is a CORRUPT record, which silently
        # downgrades the scope guard to the weaker magic-50 fallback. Surface it loudly (vs the
        # benign missing-file case above).
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            n = data.get("section_count")
            if isinstance(n, int) and n >= 0:
                return n
            logger.warning(
                "OBSERV run_scope.read_corrupt book=%s path=%s section_count=%r — scope guard "
                "DOWNGRADED to the magic-50 fallback",
                book,
                path,
                n,
            )
            return None
        except Exception as e:  # noqa: BLE001 - corrupt existing record
            logger.warning(
                "OBSERV run_scope.read_corrupt book=%s path=%s err=%s — scope guard DOWNGRADED "
                "to the magic-50 fallback",
                book,
                path,
                e,
            )
            return None
    except (
        Exception
    ) as e:  # noqa: BLE001 - a bad record must not break the consuming phase
        logger.debug("run_scope.read failed for %s: %s", book, e)
        return None
