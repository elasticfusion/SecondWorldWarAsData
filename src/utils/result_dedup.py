"""Centralized, schema-consistent JSON result de-duplication (RESULT_DEDUP_DESIGN.md).

ONE function (`dedup_entity`) run at the central write path collapses duplicate RESULT
entries uniformly for every entity type — so "don't repeat JSON results" behaves
identically everywhere, instead of ~20 enrichers each half-doing it.

Identity = the SOURCE URL (clean_url), NOT the claim text: the same fact from two
different URLs is TWO entries (corroboration preserved — the Churchill case); the SAME
URL re-returned collapses to one. Mentions dedup on MentionID; free-form entries on a
canonical hash.

UPSERT (not discard): on a same-URL match, refresh the entry's `checked_at` and
SAFE-MERGE new fields (fill-only / upgrade-only / list-union / NEVER downgrade good data
with a worse/empty re-search), and append a `history[]` observation (results tracked
over time). First-writer-wins, order preserved, idempotent.

Pure + fail-safe: never raises; on any error leaves data untouched.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date
from typing import Any, Dict, List

from src.utils.url_hygiene import clean_url

logger = logging.getLogger(__name__)

# Per-entity URL-bearing result-list fields (dedup identity = clean_url of entry["url"],
# or the string for a str list). Field names verified against the actual schemas.
# NOTE: mention lists (event_mentions/mentions) are DELIBERATELY excluded — they are
# ULID-keyed (deduped at the entity-store layer) and their item schemas are
# additionalProperties:false, so stamping recheck fields on them would BLOCK the write.
# Only URL-identity result arrays (which permit additive fields) are deduped here.
DEDUP_FIELDS: Dict[str, Dict[str, str]] = {
    "places": {"images": "url", "web_results": "url"},
    "people": {
        "images": "url",
        "military_awards": "url",
        "academic_references": "url",
    },
    "people_groups": {"images": "url", "web_results": "url"},
    "equipment": {"images": "url", "additional_sources": "url"},
    "bibliography": {"resource_urls": "url"},
    "source_section": {"primary_sources": "url"},
}

_MAX_HISTORY = 20  # cap per-entry observation log so it can't grow unbounded


def _today() -> str:
    return date.today().isoformat()


def _entry_url(entry: Any) -> str:
    """Clean URL identity for a result entry (dict with 'url', or a bare string)."""
    if isinstance(entry, str):
        return clean_url(entry) or entry
    if isinstance(entry, dict):
        return clean_url(entry.get("url", "")) or ""
    return ""


def _canonical_hash(entry: Any) -> str:
    try:
        return hashlib.sha256(
            json.dumps(entry, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
    except Exception:  # noqa: BLE001
        return str(entry)


def _richer(new: Any, old: Any) -> bool:
    """Is ``new`` strictly richer than ``old`` (upgrade-only)? More populated fields /
    longer string. Never downgrade: a thinner/empty new value is NOT richer."""
    if isinstance(old, dict) and isinstance(new, dict):
        return sum(1 for v in new.values() if v) > sum(1 for v in old.values() if v)
    if isinstance(old, str) and isinstance(new, str):
        return len(new.strip()) > len(old.strip())
    return bool(new) and not bool(old)


def _safe_merge(old: dict, new: dict) -> dict:
    """Fill-only / upgrade-only merge of a NEW dict entry into the stored OLD one.
    Fills empty/absent fields; replaces a non-empty field ONLY if the new value is
    strictly richer; never deletes. Returns the merged dict (new object)."""
    merged = dict(old)
    for k, v in new.items():
        if k in ("history", "checked_at"):
            continue  # recheck metadata handled separately
        cur = merged.get(k)
        if cur in (None, "", [], {}):
            if v not in (None, "", [], {}):
                merged[k] = v
        elif _richer(v, cur):
            merged[k] = v
    return merged


def _observe(entry: dict, source: str = "", http_status: Any = None) -> None:
    """Append a capped history[] observation (results tracked over time). checked_at +
    history[] are NON-CONTENT (excluded from change-detection — see #7)."""
    hist = entry.get("history")
    if not isinstance(hist, list):
        hist = []
    obs = {"checked_at": _today()}
    if source:
        obs["source"] = source
    if http_status is not None:
        obs["http_status"] = http_status
    hist.append(obs)
    entry["history"] = hist[-_MAX_HISTORY:]
    entry["checked_at"] = _today()


def _dedup_list(items: List[Any], ident: str) -> List[Any]:
    """Dedup one URL-identity result list, UPSERT-merging duplicates. Order preserved.
    Identity = clean_url(entry['url']) (dict) or clean_url(string); a url-less/free-form
    entry falls back to a canonical hash (so distinct free-form entries are not merged).
    """
    out: List[Any] = []
    index: Dict[str, int] = {}  # identity -> position in out
    for entry in items:
        key = _entry_url(entry) if ident == "url" else ""
        if not key:
            key = _canonical_hash(entry)  # free-form / url-less -> canonical hash
        if key in index:
            pos = index[key]
            existing = out[pos]
            if isinstance(existing, dict) and isinstance(entry, dict):
                merged = _safe_merge(existing, entry)
                _observe(
                    merged,
                    source=entry.get("source", ""),
                    http_status=entry.get("http_status"),
                )
                out[pos] = merged
            # str/duplicate: keep the first; nothing to merge.
        else:
            index[key] = len(out)
            if isinstance(entry, dict):
                entry = dict(entry)
                _observe(
                    entry,
                    source=entry.get("source", ""),
                    http_status=entry.get("http_status"),
                )
            out.append(entry)
    return out


def dedup_entity(data: Dict[str, Any], entity: str) -> Dict[str, Any]:
    """Dedup all declared result-list fields for ``entity`` IN PLACE (and return data).
    Pure + fail-safe: on any error, leave data untouched."""
    try:
        fields = DEDUP_FIELDS.get(entity)
        if not fields or not isinstance(data, dict):
            return data
        for field_name, ident in fields.items():
            items = data.get(field_name)
            if isinstance(items, list) and items:
                data[field_name] = _dedup_list(items, ident)
    except Exception as e:  # noqa: BLE001 - never break a write
        logger.warning("result dedup skipped for %s: %s", entity, e)
    return data
