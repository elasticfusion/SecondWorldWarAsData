"""Grok vision looks at the scans of a NARA record before a person sees it (owner 2026-11-22).

A NARA catalog record is matched to a citation by its description (``nara_verify``: title, series,
scope note). File-unit titles are terse ("XIV Corps: 1-009"), so a neighbouring file can pass. When
the record has scans, Grok vision looks at its first page (and a middle page of a longer file) and
says whether the page belongs to the cited document:

* ``not_match`` (every page checked says so): the find is not queued, the same rule as any find
  Grok calls irrelevant;
* ``match`` / ``unsure``: queued, with the verdict, the reason (words read off the page) and the
  pages checked, so the reviewer approves against the actual document;
* ``unchecked``: no page could be checked (no image scans, e.g. a PDF; a page too large or
  unreadable): queued, said.

Pages are fetched SSRF-safely and size-capped; the answer is cached and batch-aware like every Grok
check (``cache_type`` ``nara_scan_vision``, request-bound: the answer echoes its RequestID).
"""

from __future__ import annotations

import base64
import logging
from typing import Any, Dict, List, Optional

from src.grok_client import BatchModeCollecting
from src.utils.url_hygiene import scrub

logger = logging.getLogger(__name__)

MATCH, NOT_MATCH, UNSURE, UNCHECKED = "match", "not_match", "unsure", "unchecked"
CACHE_TYPE = "nara_scan_vision"
_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff")
_PAGE_MAX_BYTES = 25 * 1024 * 1024  # a NARA TIFF page; shrunk before it is sent


def _is_image(scan: Dict[str, Any]) -> bool:
    kind = str(scan.get("type") or "").lower()
    name = str(scan.get("filename") or scan.get("url") or "").lower()
    return kind.startswith("image") or name.endswith(_IMAGE_EXT)


def pages_to_check(scans: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The first image page, and a middle one when the file has more than two."""
    images = [s for s in scans if _is_image(s)]
    if not images:
        return []
    picks = [0] + ([len(images) // 2] if len(images) > 2 else [])
    return [dict(images[i], page=i + 1) for i in picks]


def _fetch(url: str) -> Optional[bytes]:
    from src.utils.url_hygiene import safe_get

    resp = safe_get(
        url,
        timeout=60,
        headers={"User-Agent": "SecondWorldWarAsData/1.0 (NARA scan check)"},
        max_bytes=_PAGE_MAX_BYTES,
    )
    if resp is None or getattr(resp, "status_code", 0) != 200:
        return None
    body = resp.content or b""
    return (
        body if body and len(body) < _PAGE_MAX_BYTES else None
    )  # truncated: not judged


def _prompt(page: Dict[str, Any], citation: str, record: Dict[str, Any]) -> str:
    from src.utils.prompt_loader import render_prompt

    return render_prompt(
        "nara_scan_vision",
        page_label=f"page {page['page']}",
        citation=scrub(citation)[:500],
        nara_title=scrub(record.get("nara_title") or "")[:200],
        series=scrub((record.get("series") or {}).get("title") or "")[:200],
    )


def _image(page: Dict[str, Any]) -> "tuple[Optional[bytes], str]":
    """The page ready for vision, or (None, why it cannot be looked at)."""
    from src.extraction.equipment import _prepare_image_for_vision

    body = _fetch(page["url"])
    if body is None:
        return None, "the page could not be fetched"
    prepared, problem = _prepare_image_for_vision(body)
    return prepared, scrub(problem)[:200]


def _verdict(answer: Any) -> "tuple[str, str]":
    verdict = answer.get("verdict") if isinstance(answer, dict) else None
    if verdict not in (MATCH, NOT_MATCH, UNSURE):
        return UNSURE, "no usable answer from Grok"
    return verdict, scrub(answer.get("reason") or "")[:300]


def _ask(
    page: Dict[str, Any], citation: str, record: Dict[str, Any], grok: Any
) -> Dict[str, Any]:
    """One page's verdict. Raises BatchModeCollecting when the request is queued for a batch."""
    result: Dict[str, Any] = {"url": page["url"], "page": page["page"]}
    prepared, problem = _image(page)
    if prepared is None:
        return {**result, "verdict": UNCHECKED, "reason": problem}
    try:
        answer = grok.extract_json_with_image_base64(
            prompt=_prompt(page, citation, record),
            image_base64=base64.b64encode(prepared).decode(),
            cache_type=CACHE_TYPE,
            temperature=0.0,
        )
    except BatchModeCollecting:
        raise
    except Exception as e:  # noqa: BLE001 - undecided, not a rejection
        logger.warning(
            "OBSERV nara_vision.ask_failed err=%s status=warn", type(e).__name__
        )
        answer = None
    verdict, reason = _verdict(answer)
    return {**result, "verdict": verdict, "reason": reason}


def _combine(pages: List[Dict[str, Any]]) -> str:
    """``not_match`` only when every page was looked at and none belongs to the document (a page
    that could not be looked at never counts as evidence against it)."""
    verdicts = [p["verdict"] for p in pages]
    decided = [v for v in verdicts if v != UNCHECKED]
    if not decided:
        return UNCHECKED
    if all(v == NOT_MATCH for v in verdicts):
        return NOT_MATCH
    if MATCH in decided and NOT_MATCH not in decided:
        return MATCH
    return UNSURE


def check_scans(
    scans: List[Dict[str, Any]], citation: str, record: Dict[str, Any], grok: Any
) -> Dict[str, Any]:
    """Grok vision's verdict on a NARA record's scans against the citation:
    ``{"verdict", "reason", "pages": [...]}``. Never raises except BatchModeCollecting.
    """
    pages = pages_to_check(scans)
    if not pages:
        return {
            "verdict": UNCHECKED,
            "reason": "no image scans to look at",
            "pages": [],
        }
    if grok is None:
        return {"verdict": UNCHECKED, "reason": "no Grok client", "pages": []}
    checked, queued = [], None
    for p in pages:  # every page's request is queued in the same batch round
        try:
            checked.append(_ask(p, citation, record, grok))
        except BatchModeCollecting as e:
            queued = queued or e
    if queued is not None:
        raise queued
    verdict = _combine(checked)
    reason = "; ".join(
        f"page {p['page']}: {p['verdict']} - {p['reason']}" for p in checked
    )
    logger.info(
        "OBSERV nara_vision.checked verdict=%s pages=%d %s",
        verdict,
        len(checked),
        ",".join(p["verdict"] for p in checked),
    )
    return {"verdict": verdict, "reason": reason[:600], "pages": checked}
