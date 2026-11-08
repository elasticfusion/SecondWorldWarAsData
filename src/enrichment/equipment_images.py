"""Phase 3: equipment images - candidates for SPECIFIC equipment only, each confirmed by Grok vision.

Owner rules (2026-11-07, XChannel-8 E2E: 169 equipment images attached, 0 vision-checked - "trench"
illustrated by the Mariana Trench, "rifle" by a benchrest Mauser):
- Accuracy and completeness outrank cost: no image is attached unless Grok vision confirms it shows
  the named equipment (WWII era or a preserved example). A vision error is UNDECIDED (retried, then
  needs_review), never a silent rejection.
- Only SPECIFIC records get images; GENERIC mentions stay in the JSON unenriched (this step also
  removes enrichment a generic record received before the rule).
- "World War II" qualifies TEXT: candidates come only from a Wikipedia article whose text places the
  subject in WWII. Images themselves are judged by vision alone.
- Phase 2 makes no image/Commons requests; downloads are held in memory for vision (the old Phase 2
  filestore copy was ephemeral and never reached S3).

Mirrors src/extraction/image_map_vision.py (pending state, attempt cap, needs_review, one
actionable alert per cause).
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

from src.extraction.equipment_identity import (
    GENERIC,
    SPECIFIC,
    UNRESOLVED,
    deterministic_scope,
    mentions_wwii,
)
from src.utils.observe import observe_warn

logger = logging.getLogger(__name__)

PENDING, VERIFIED, REJECTED = "pending", "verified", "rejected"
MAX_VISION_ATTEMPTS = (
    3  # undecided this many times -> needs_review (no further Grok calls)
)
MAX_FETCH_FAILURES = (
    5  # image download failed this many runs -> needs_review (dead URL)
)
MAX_CANDIDATES = 20  # images considered per Wikipedia article
_COMMONS_BATCH = 5  # titles per Commons imageinfo request
_SKIP_FILES = {
    "index.json",
    "duplicate_report.json",
    "disambiguation_suggestions.jsonl",
}
# Enrichment a GENERIC record must not carry (narrative-sourced fields are kept).
_ENRICHMENT_FIELDS = (
    "images",
    "media",
    "external_data",
    "wikipedia_extract",
    "wikipedia_checked_at",
    "wikipedia_wwii_relevant",
    "description_source_url",
    "image_candidates_checked_at",
    "enrichment_status",
    "enrichment_checked_at",
)
_ENRICHMENT_SOURCED = ("timeline", "technical_evolution", "logistics")


# --- scope ---------------------------------------------------------------------------------------


def record_scope(rec: Dict[str, Any], grok_client=None) -> Optional[str]:
    """The record's scope: the Phase 2 stamp, else the deterministic rule, else the stored
    canonical resolution, else the same cached canonical resolver Phase 2 uses (records written
    before identity_scope existed). None = undecidable now (no Grok client): leave it alone -
    never strip a record whose scope is unknown."""
    from src.extraction.equipment import (
        _GENERIC_EQUIPMENT_WORDS,
        _identity_scope,
        _is_alias_name,
    )

    stamped = rec.get("identity_scope")
    if stamped in (SPECIFIC, GENERIC, UNRESOLVED):
        return stamped
    name = rec.get("common_name") or ""
    scope = deterministic_scope(
        name,
        rec.get("category"),
        _is_alias_name(name),
        _GENERIC_EQUIPMENT_WORDS,
        rec.get("subcategory"),
    )
    if scope:
        return scope
    if rec.get("canonical_name") and rec.get("identity_source") in _TABLE_SOURCES:
        return SPECIFIC
    return _identity_scope(rec, grok_client) if grok_client else None


# Canonical identities from the alias tables (not a Grok guess): specific without asking again.
_TABLE_SOURCES = ("exact", "alias", "learned_alias", "fuzzy", "vessel_disambiguation")


def _strip_enrichment(rec: Dict[str, Any]) -> bool:
    """Remove enrichment from a GENERIC record. Returns True if anything was removed."""
    removed = False
    for key in _ENRICHMENT_FIELDS:
        if key in rec:
            rec.pop(key)
            removed = True
    for key in _ENRICHMENT_SOURCED:
        val = rec.get(key)
        blocks = val if isinstance(val, list) else [val]
        if val and all(
            isinstance(b, dict)
            and b.get("source") in ("grokipedia", "wikipedia", "enrichment")
            for b in blocks
        ):
            rec.pop(key)
            removed = True
    return removed


# --- candidates -----------------------------------------------------------------------------------


def _qualified_article_title(rec: Dict[str, Any]) -> Optional[str]:
    """The Wikipedia article title to take images from - only if its text places the subject in
    WWII (Phase 2 stamps wikipedia_wwii_relevant; older records are checked on their extract).
    """
    url = (rec.get("external_data") or {}).get("wikipedia_url") or ""
    if "/wiki/" not in url:
        return None
    relevant = rec.get("wikipedia_wwii_relevant")
    if relevant is None:
        relevant = mentions_wwii(rec.get("wikipedia_extract"))
    if not relevant:
        return None
    return unquote(url.split("/wiki/", 1)[1]).replace("_", " ")


def _wiki_json(
    endpoint: str, provider: str, params: Dict[str, Any], subject: str
) -> Optional[Dict[str, Any]]:
    """One Wikimedia API call via the shared client (back-off, provenance). The JSON dict, or
    None when unreachable / not JSON (transient: the caller retries next run)."""
    from src.utils.wikimedia_client import TRANSIENT, wikimedia_get

    resp = wikimedia_get(
        provider,
        endpoint,
        params={"format": "json", **params},
        headers={"User-Agent": "SecondWorldWarAsData/1.0 (equipment images)"},
        timeout=15,
        subject=subject,
        result_summary_fn=lambda r: f"equip-images status={r.status_code}",
    )
    if resp is TRANSIENT or getattr(resp, "status_code", 0) != 200:
        return None
    try:
        data = resp.json()  # type: ignore[union-attr]
    except ValueError:
        return None
    if not isinstance(data, dict) or "error" in data:
        return (
            None  # not JSON, or MediaWiki's maxlag / rate-limit error body: transient
        )
    return data


def article_image_candidates(title: str) -> Optional[List[Dict[str, Any]]]:
    """Image candidates (url/title/license...) from a Wikipedia article and Commons; None when
    Wikipedia/Commons was unreachable (transient).
    Reuses the equipment module's icon/logo filter and Commons record builder."""
    from src.extraction.equipment import (
        _build_wikipedia_media_list,
        _filter_wikipedia_image_titles,
    )

    data = _wiki_json(
        "https://en.wikipedia.org/w/api.php",
        "wikipedia",
        {
            "action": "query",
            "titles": title,
            "prop": "images",
            "imlimit": str(MAX_CANDIDATES),
            "redirects": "1",
        },
        title,
    )
    if data is None:
        return None
    files = _filter_wikipedia_image_titles((data.get("query") or {}).get("pages") or {})
    media: List[Dict[str, Any]] = []
    for i in range(0, min(len(files), MAX_CANDIDATES), _COMMONS_BATCH):
        info = _wiki_json(
            "https://commons.wikimedia.org/w/api.php",
            "wikimedia-commons",
            {
                "action": "query",
                "titles": "|".join(files[i : i + _COMMONS_BATCH]),
                "prop": "imageinfo",
                "iiprop": "url|extmetadata|mime",
                "iiurlwidth": "1280",
            },
            title,
        )
        if info is None:
            return None
        media.extend(
            _build_wikipedia_media_list((info.get("query") or {}).get("pages") or {})
        )
    return media


def _as_candidate(
    item: Dict[str, Any], source: str, page_url: Optional[str] = None
) -> Dict[str, Any]:
    return {
        "source_page_url": page_url,
        "candidate_added_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "url": item.get("url"),
        "source": item.get("source") or source,
        "license": item.get("license"),
        "caption": item.get("title") or item.get("caption") or item.get("description"),
        "image_scope": "representative",
        "vision_verified": False,
        "vision_check": PENDING,
        "vision_attempts": 0,
        "candidate_source": source,
    }


def _queue_existing(rec: Dict[str, Any]) -> None:
    """Images attached before this step (Phase 2 legacy, OpenSERP) that vision never checked are
    queued for vision instead of being trusted."""
    for img in rec.get("images") or []:
        if isinstance(img, dict) and not img.get("vision_check"):
            _queue_one(img)
    rec.pop(
        "media", None
    )  # legacy loose copy of images[]; images[] is the record of truth


def _queue_one(img: Dict[str, Any]) -> None:
    """Stamp a never-checked image. Verified only with a RECORDED check: the old Phase 2 set
    vision_verified from the config flag, not per image."""
    checked = img.get("vision_verified") is True and bool(img.get("vision_checked_at"))
    img["vision_check"] = VERIFIED if checked else PENDING
    img.setdefault("vision_attempts", 0)
    img.setdefault("candidate_source", img.get("source") or "unknown")
    if not img.get("caption") and img.get("title"):
        img["caption"] = img["title"]  # OpenSERP candidates carry "title"
    if not checked:
        img["vision_verified"] = False


def _add_article_candidates(rec: Dict[str, Any], counts: Dict[str, int]) -> None:
    """Add the qualified Wikipedia article's images once (stamped), deduped by URL."""
    if rec.get("image_candidates_checked_at"):
        return
    title = _qualified_article_title(rec)
    if not title:
        return
    found = article_image_candidates(title)
    if found is None:
        counts["candidates_transient"] += 1
        return  # unknown: retried next run (not stamped)
    rec["image_candidates_checked_at"] = _dt.date.today().isoformat()
    page_url = (rec.get("external_data") or {}).get("wikipedia_url")
    images = rec.setdefault("images", [])
    known = {img.get("url") for img in images if isinstance(img, dict)}
    for item in found:
        if item.get("url") and item["url"] not in known:
            images.append(_as_candidate(item, "wikipedia", page_url))
            known.add(item["url"])
            counts["candidates_added"] += 1


# --- vision ---------------------------------------------------------------------------------------


_SAFE_FETCH_MAX_BYTES = 10 * 1024 * 1024  # url_hygiene.safe_get's body cap


def _fetch_bytes(url: str) -> Optional[bytes]:
    """SSRF-safe image download (the URL comes from Wikipedia/OpenSERP), held in memory."""
    from src.utils.url_hygiene import safe_get

    resp = safe_get(
        url,
        timeout=30,
        headers={"User-Agent": "SecondWorldWarAsData/1.0 (equipment image check)"},
    )
    if resp is None or getattr(resp, "status_code", 0) != 200:
        return None
    body = resp.content or None
    if body and len(body) >= _SAFE_FETCH_MAX_BYTES:
        return None  # safe_get truncated it: a partial image must not be judged (or rejected)
    return body


def _vision_one(
    img: Dict[str, Any], rec: Dict[str, Any], grok_client, seen_hashes: set
) -> str:
    """Decide one pending image: 'verified' | 'rejected' | 'duplicate' | 'undecided' |
    'needs_human'."""
    from src.extraction.equipment import _compute_image_hash

    attempts = int(img.get("vision_attempts") or 0)
    if attempts >= MAX_VISION_ATTEMPTS:
        img["needs_review"] = True
        return "needs_human"
    body = _fetch_bytes(img.get("url") or "")
    if not body:
        return _fetch_failed(img)
    digest = _compute_image_hash(body)
    if digest and digest in seen_hashes:
        img.update(vision_check=REJECTED, vision_reason="duplicate of a verified image")
        return "duplicate"
    verdict, reason = _ask_vision(body, img, rec, grok_client)
    if verdict is None:
        img["vision_attempts"] = attempts + 1
        img["vision_reason"] = reason
        return "undecided"
    return _record_verdict(img, verdict, reason, digest, seen_hashes)


def _fetch_failed(img: Dict[str, Any]) -> str:
    """A download failure is not a vision verdict: counted on its own budget; a URL that fails
    MAX_FETCH_FAILURES runs is a dead link for a human."""
    failures = int(img.get("fetch_failures") or 0) + 1
    img["fetch_failures"] = failures
    if failures >= MAX_FETCH_FAILURES:
        img["needs_review"] = True
        return "needs_human"
    return "fetch_failed"


def _ask_vision(
    body: bytes, img: Dict[str, Any], rec: Dict[str, Any], grok_client
) -> tuple:
    """Grok vision verdict for one image of this record: (True/False/None, reason)."""
    from src.extraction.equipment import _verify_media_with_vision

    name = rec.get("canonical_name") or rec.get("common_name") or ""
    return _verify_media_with_vision(
        body, name, rec.get("category") or "", img.get("caption") or "", grok_client
    )


def _record_verdict(
    img: Dict[str, Any],
    verdict: bool,
    reason: str,
    digest: Optional[str],
    seen_hashes: set,
) -> str:
    """Stamp a decided vision verdict (provenance: reason + UTC time) on the image; a verified
    image's hash joins ``seen_hashes`` for duplicate rejection. Returns the outcome."""
    img.update(
        vision_check=VERIFIED if verdict else REJECTED,
        vision_verified=verdict,
        vision_reason=reason,
        vision_checked_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
    )
    img.pop("needs_review", None)
    if verdict and digest:
        img["phash"] = digest
        seen_hashes.add(digest)
    return VERIFIED if verdict else REJECTED


def _verify_pending(
    rec: Dict[str, Any], grok_client, wanted: int, counts: Dict[str, int]
) -> None:
    """Vision-check pending images until ``wanted`` are verified (the rest stay pending for a
    later run)."""
    images = [i for i in rec.get("images") or [] if isinstance(i, dict)]
    seen, verified = _verified_state(images)
    for img in images:
        if verified >= wanted:
            break
        if img.get("vision_check") != PENDING or img.get("needs_review"):
            continue  # decided, or already waiting for a human
        outcome = _vision_one(img, rec, grok_client, seen)
        counts[outcome] += 1
        verified += outcome == VERIFIED


def _verified_state(images: List[Dict[str, Any]]) -> tuple:
    """(hashes of already-verified images - duplicate rejection spans runs, their count)."""
    done = [i for i in images if i.get("vision_check") == VERIFIED]
    return {i["phash"] for i in done if i.get("phash")}, len(done)


# --- driver ---------------------------------------------------------------------------------------


def _process(
    rec: Dict[str, Any], grok_client, wanted: int, counts: Dict[str, int]
) -> bool:
    """Apply the rules to one record. Returns True when the record changed."""
    before = json.dumps(rec, sort_keys=True, default=str)
    scope = record_scope(rec, grok_client)
    if scope is None:
        return False  # undecidable without Grok: untouched (never strip on an unknown scope)
    rec["identity_scope"] = scope
    if scope == GENERIC:
        if _strip_enrichment(rec):
            counts["generic_cleaned"] += 1
    elif scope == SPECIFIC:
        _queue_existing(rec)
        _add_article_candidates(rec, counts)
        _verify_pending(rec, grok_client, wanted, counts)
    return json.dumps(rec, sort_keys=True, default=str) != before


def _new_counts() -> Dict[str, int]:
    keys = (
        "records",
        "generic_cleaned",
        "candidates_added",
        "candidates_transient",
        VERIFIED,
        REJECTED,
        "duplicate",
        "undecided",
        "fetch_failed",
        "scope_deferred",
        "needs_human",
        "unreadable",
        "write_failed",
    )
    return {k: 0 for k in keys}


_ALERTS = {
    "undecided": (
        "equipment_image_vision_incomplete",
        "equipment images still unchecked (image fetch failed or Grok vision gave no verdict); "
        "check XAI_API_KEY / vision errors and image fetch (SSRF/404/429) in this task's log, "
        "then re-run Phase 3",
    ),
    "fetch_failed": (
        "equipment_image_fetch_failed",
        "equipment image downloads failed (429/5xx/timeout/SSRF block or >10MB); they are "
        "retried on the next Phase 3 run - if this repeats, check wikimedia.transient lines",
    ),
    "needs_human": (
        "equipment_image_needs_human",
        f"vision could not decide after {MAX_VISION_ATTEMPTS} attempts, or the image URL failed "
        f"{MAX_FETCH_FAILURES} runs; these images are marked needs_review - open each and set "
        "vision_check to verified or rejected",
    ),
    "candidates_transient": (
        "equipment_image_candidates_deferred",
        "Wikipedia/Commons was unreachable for some records' image candidates; they are retried "
        "on the next Phase 3 run (check wikimedia.transient lines if this repeats)",
    ),
    "unreadable": (
        "equipment_image_record_unreadable",
        "equipment records could not be read (see equipment.image_record_unreadable lines); "
        "re-run Phase 2 for the book to rewrite them",
    ),
    "write_failed": (
        "equipment_image_write_failed",
        "image verdicts could not be saved (guarded write refused; see the task log); they are "
        "re-checked on the next run",
    ),
}


def _report(counts: Dict[str, int]) -> None:
    bad = any(counts[k] for k in _ALERTS)
    logger.info(
        "OBSERV equipment.image_vision records=%d generic_cleaned=%d candidates=%d verified=%d "
        "rejected=%d duplicate=%d undecided=%d fetch_failed=%d needs_human=%d deferred=%d "
        "scope_deferred=%d status=%s",
        counts["records"],
        counts["generic_cleaned"],
        counts["candidates_added"],
        counts[VERIFIED],
        counts[REJECTED],
        counts["duplicate"],
        counts["undecided"],
        counts["fetch_failed"],
        counts["needs_human"],
        counts["candidates_transient"],
        counts["scope_deferred"],
        "warn" if bad else "ok",
    )
    for key, (event, note) in _ALERTS.items():
        if counts[key]:
            observe_warn("equipment", event, note=note, count=counts[key])


def resolve_equipment_images(
    equipment_dir: Path, grok_client, images_per_record: int = 3
) -> Dict[str, int]:
    """Run the equipment image rules over every equipment record. Never raises: a per-record
    failure is counted and alerted; a Grok call queued for the xAI batch (collect pass) leaves the
    record untouched until the batch re-run."""
    counts = _new_counts()
    files = (
        sorted(Path(equipment_dir).glob("*.json"))
        if Path(equipment_dir).is_dir()
        else []
    )
    for f in files:
        if f.name not in _SKIP_FILES:
            _process_file(f, grok_client, images_per_record, counts)
    _report(counts)
    return counts


def _load_record(f: Path, counts: Dict[str, int]) -> Optional[Dict[str, Any]]:
    """The equipment record in ``f``, or None (unreadable -> counted + logged; non-record skipped)."""
    try:
        rec = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning(
            "OBSERV equipment.image_record_unreadable file=%s err=%s status=warn",
            f.name,
            type(e).__name__,
        )
        counts["unreadable"] += 1
        return None
    return rec if isinstance(rec, dict) and "EquipmentID" in rec else None


def _process_file(
    f: Path, grok_client, images_per_record: int, counts: Dict[str, int]
) -> None:
    from src.grok_client import BatchModeCollecting
    from src.utils.file_lock import write_json_with_lock

    rec = _load_record(f, counts)
    if rec is None:
        return
    counts["records"] += 1
    try:
        changed = _process(rec, grok_client, images_per_record, counts)
    except BatchModeCollecting:
        counts["scope_deferred"] += 1  # queued; decided on the batch re-run
        return
    except Exception as e:  # noqa: BLE001 - one record must not abort the step
        logger.warning(
            "OBSERV equipment.image_vision_error file=%s err=%s status=warn",
            f.name,
            type(e).__name__,
        )
        counts["undecided"] += 1
        return
    if changed and not write_json_with_lock(f, rec, entity="equipment"):
        counts["write_failed"] += 1
