"""Are the cached Grok answers stored under the right request? (Group 2, owner 2026-11-18)

Until 2026-11-11 a batch's answers could be stored under ANOTHER request's key (they were paired by
position, and the results come back out of order). Since then each answer is paired by the id the
batch returns, and a kind that echoes its identity is checked on every read. This module finds the
entries written before that which nothing checks:

* ``check_echo`` (offline, reads the cache table): for every identity kind, does the answer carry its
  identity, and does a BookID in it match the book of the key it is stored under? A kind that is
  not echoed cannot be proven here: it is counted ("no_echo"), not trusted.
* ``check_batches`` (needs the batch results from xAI): re-pairs each old batch's results with its
  requests BY ID, and compares each answer with what the cache holds under that request id.
  A different answer under the key is a swap.

Both are read-only; ``evict_bad`` removes a row proven wrong (the request is then asked again).
A ``swapped`` row can also be an answer re-asked on purpose after the batch (a cache row replaced
later), so look at the list before evicting: the cost of a wrong eviction is one repeated request.
"""

from __future__ import annotations

import calendar
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple

from src.ingestion.book_slug import is_safe_slug
from src.utils import request_identity
from src.utils.batch_api import (
    align_by_request_id,
    retrieve_results_ordered,
    strip_control_chars,
)
from src.utils.ddb_scan import scan_prefix
from src.utils.deterministic_ids import book_id

logger = logging.getLogger(__name__)

IDENTITY_KINDS = (
    request_identity.BOOK_BOUND
    | request_identity.REQUEST_BOUND
    | request_identity.DOC_BOUND
)
_BOOK_KEY = re.compile(r"^books/([^/]+)/([^/]+)$")
_BATCH_BOOK = re.compile(r"^phase[23]-(.+?)-\d+files")
# the shapes a key, a request id, a book and a batch id may have: the tool builds and DELETES keys from
# strings in metrics rows, so anything else is refused (a forged row cannot name another entity's key)
_RID = re.compile(r"^[0-9a-f]{16,64}$")
_BATCH_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


@dataclass
class Report:
    """What a check found. ``bad`` rows are the cache keys proven to hold another request's answer."""

    counts: Dict[str, int] = field(default_factory=dict)
    by_kind: Dict[str, Dict[str, int]] = field(default_factory=dict)
    bad: List[str] = field(default_factory=list)
    evicted: int = 0

    def add(self, kind: str, status: str, key: str = "") -> None:
        self.counts[status] = self.counts.get(status, 0) + 1
        per = self.by_kind.setdefault(kind, {})
        per[status] = per.get(status, 0) + 1
        if status in ("book_mismatch", "swapped"):
            self.bad.append(key)


def split_key(cache_key: str) -> Tuple[str, str, str]:
    """(kind, book, request id) of a cache row key ``<prefix>#<id>``; the prefix is ``<kind>`` or
    ``books/<book>/<kind>``. Book is "" for a global kind."""
    prefix, _, rid = cache_key.rpartition("#")
    m = _BOOK_KEY.match(prefix)
    if m:
        return m.group(2), m.group(1), rid
    return prefix, "", rid


def _json_object(response: Any) -> Optional[dict]:
    if isinstance(response, dict):
        return response
    try:
        body = json.loads(response)
    except (TypeError, ValueError):
        return None
    return body if isinstance(body, dict) else None


def classify_row(cache_key: str, response: Any) -> Tuple[str, str]:
    """(kind, status): ``ok`` (echoes its identity; a BookID matches the key's book),
    ``book_mismatch`` (the BookID is another book's: proven wrong), ``no_echo`` (an identity kind
    whose answer carries none: unprovable here), ``other_kind`` (no identity check exists).
    """
    kind, book, _ = split_key(cache_key)
    if kind not in IDENTITY_KINDS:
        return kind, "other_kind"
    body = _json_object(response)
    if body is None or "RequestID" not in body:
        return kind, "no_echo"
    got = body.get("BookID")
    if book and got is not None and str(got) != book_id(book):
        return kind, "book_mismatch"
    return kind, "ok"


def check_echo(table: Any, report: Optional[Report] = None) -> Report:
    """Classify every cached identity-kind answer (one table scan): ``book_mismatch`` rows hold an
    answer whose BookID is another book's."""
    report = report or Report()
    prefixes = ["books/"] + [f"{k}#" for k in sorted(IDENTITY_KINDS)]
    for item in scan_prefix(
        table, prefixes, projection="cache_key, #r", names={"#r": "response"}
    ):
        kind, status = classify_row(item["cache_key"], item.get("response"))
        report.add(kind, status, item["cache_key"])
    return report


_BACKSLASH = re.compile(r'\\(?!["\\/bfnrtu]|u[0-9a-fA-F]{4})')


def _parsed(text: str) -> Any:
    """The JSON object in an answer as the readers see it: as stored, the JSON inside a markdown
    fence or prose (``json_payload``), each also with the invalid escapes repaired as
    ``GrokClient._sanitize_content`` repairs them. None if it is not a JSON object."""
    from src.grok_client import json_payload

    cleaned = strip_control_chars(text)
    for base in (cleaned, json_payload(cleaned)):
        for candidate in (base, _BACKSLASH.sub(r"\\\\", base)):
            try:
                body = json.loads(candidate)
            except (TypeError, ValueError):
                continue
            if isinstance(body, dict):
                return body
    return None


def _normal(value: Any) -> Any:
    """An answer in a form that compares equal however it was stored: a dict (or a JSON object in
    a string, a fence, or with repaired escapes) without its identity fields, else stripped text.
    """
    body = (
        value
        if isinstance(value, dict)
        else _parsed(str(value)) if value is not None else None
    )
    if body is not None:
        return {k: v for k, v in body.items() if k not in ("BookID", "RequestID")}
    return str(value).strip() if value is not None else None


def book_of_batch(batch_name: str) -> str:
    m = _BATCH_BOOK.match(batch_name or "")
    return m.group(1) if m else ""


def check_batches(
    table: Any,
    api_key: str,
    before: float,
    fetch: Callable[[str, str], list] = retrieve_results_ordered,
    report: Optional[Report] = None,
    limit: int = 0,
) -> Report:
    """Re-pair every batch written before ``before`` (epoch seconds) by request id and compare each
    answer with the cache. ``limit`` caps the batches (0 = all). A batch xAI no longer holds is
    counted ``batch_unavailable``."""
    report = report or Report()
    done = 0
    for item in scan_prefix(
        table,
        "metrics#",
        projection="cache_key, #r, created_at",
        names={"#r": "response"},
    ):
        if float(item.get("created_at", 0) or 0) >= before:
            continue
        if limit and done >= limit:
            break
        done += 1
        _check_one_batch(table, api_key, item, fetch, report)
    return report


def _submitted_pairs(metrics: dict) -> List[Tuple[str, str]]:
    """The batch's (request id, kind) pairs, in submission order, from its metrics."""
    return [
        (p[0], p[1] if len(p) > 1 else "default")
        for p in metrics.get("submitted_request_ids") or []
        if isinstance(p, (list, tuple)) and p
    ]


def _fetch_or_none(fetch: Callable, api_key: str, batch_id: str) -> Optional[list]:
    """The batch's results, or None when xAI no longer holds them (a finding, not a crash)."""
    try:
        return fetch(api_key, batch_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "OBSERV validate.batch_unavailable batch=%s err=%s",
            batch_id[:40].encode("unicode_escape").decode(),
            type(e).__name__,
        )
        return None


def _check_one_batch(table, api_key, item, fetch, report: Report) -> None:
    metrics = _json_object(item.get("response")) or {}
    batch_id = str(metrics.get("batch_id") or item["cache_key"].split("#", 1)[1])
    submitted = _submitted_pairs(metrics)
    if not submitted:
        report.add("(batch)", "batch_no_ids")
        return
    if not _BATCH_ID.match(batch_id):
        report.add("(batch)", "batch_unavailable")
        return
    ordered = _fetch_or_none(fetch, api_key, batch_id)
    if ordered is None:
        report.add("(batch)", "batch_unavailable")
        return
    book = book_of_batch(str(metrics.get("batch_name", "")))
    aligned = align_by_request_id(submitted, ordered, batch_id=batch_id, strict=True)
    for (rid, kind), br in zip(submitted, aligned):
        if br.error or not br.content:
            report.add(kind, "no_answer")
            continue
        status, key = _compare(table, rid, kind, book, br.content)
        report.add(kind, status, key)


def _candidates(rid: str, kind: str, book: str) -> List[str]:
    """The cache keys a batch answer may be stored under: nothing for a kind that is not an identity
    kind, a request id that is not a hash, or a book that is not a slug."""
    if kind not in IDENTITY_KINDS or not _RID.match(str(rid)):
        return []
    keys = [f"{kind}#{rid}"]
    if book and is_safe_slug(book):
        keys.insert(0, f"books/{book}/{kind}#{rid}")
    return keys


def safe_key(key: str) -> bool:
    """Whether a key has the shape of a cached identity-kind answer (the only rows this tool may
    delete): ``[books/<slug>/]<identity kind>#<hash>``."""
    kind, book, rid = split_key(key)
    return (
        kind in IDENTITY_KINDS
        and bool(_RID.match(rid))
        and (not book or is_safe_slug(book))
        and key == (f"books/{book}/{kind}#{rid}" if book else f"{kind}#{rid}")
    )


def _stored(table: Any, rid: str, kind: str, book: str) -> Tuple[str, Any]:
    for key in _candidates(rid, kind, book):
        item = table.get_item(Key={"cache_key": key}).get("Item")
        if item and "response" in item:
            return key, item["response"]
    return "", None


def _compare(
    table: Any, rid: str, kind: str, book: str, content: str
) -> Tuple[str, str]:
    """(status, the row's key): ``not_cached`` (nothing under this request), ``same`` (the cache
    holds this request's own answer) or ``swapped`` (it holds a different one)."""
    key, value = _stored(table, rid, kind, book)
    if not key:
        return "not_cached", ""
    expected = _normal(strip_control_chars(content))
    return ("same" if _normal(value) == expected else "swapped"), key


def evict_bad(table: Any, report: Report) -> int:
    """Delete every row the report proved wrong (once); the request is then asked again."""
    report.evicted += _evict(table, report.bad)
    return report.evicted


def _evict(table: Any, keys: Iterable[str]) -> int:
    """Delete the proven-wrong rows (the request is then asked again). Returns how many."""
    n = 0
    for key in sorted(set(keys)):
        if not key or not safe_key(key):
            continue  # never delete a row that is not a cached identity-kind answer
        table.delete_item(Key={"cache_key": key})
        logger.info("OBSERV validate.evicted key=%r", key[:80])
        n += 1
    return n


def format_report(report: Report) -> str:
    counts = ", ".join(f"{k}={v}" for k, v in sorted(report.counts.items())) or "none"
    lines = [f"Cached answers checked: {counts}"]
    for kind, per in sorted(report.by_kind.items()):
        lines.append(
            f"  {kind}: " + ", ".join(f"{k}={v}" for k, v in sorted(per.items()))
        )
    if report.bad:
        lines.append(
            f"PROVEN WRONG: {len(set(report.bad))} row(s) hold another request's answer"
        )
    unreadable = report.counts.get("batch_unavailable", 0)
    if unreadable:
        lines.append(
            f"INCOMPLETE: {unreadable} batch(es) could not be read from xAI (expired, or the key "
            "was refused): their answers were NOT checked"
        )
    if report.evicted:
        lines.append(f"Evicted: {report.evicted}")
    return "\n".join(lines)


def epoch(date: str) -> float:
    """Epoch seconds of a YYYY-MM-DD date (UTC)."""
    return float(calendar.timegm(time.strptime(date, "%Y-%m-%d")))
