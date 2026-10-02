"""Preserve fetched award-citation source pages as primary-source records.

A scraped citation page is source material, no different from an original document:
it must be RETAINED so the citation can always be traced back to exactly what the
source served at retrieval time. This module saves the raw page bytes to the
configured storage backend (local filesystem or S3) and returns the stored path,
which goes into the award's provenance.

Layout:  award_sources/<source_id>/<sha256-16>.<ext>
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)

PRESERVE_PREFIX = "award_sources"


def _page_path(source_id: str, url: str, content_type: str = "text/html") -> str:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    ext = "html"
    if "json" in content_type:
        ext = "json"
    elif "pdf" in content_type:
        ext = "pdf"
    safe_source = "".join(c if c.isalnum() or c in "-_" else "_" for c in source_id)
    return f"{PRESERVE_PREFIX}/{safe_source}/{digest}.{ext}"


def preserve_page(
    storage,
    source_id: str,
    url: str,
    body: bytes,
    content_type: str = "text/html",
) -> Optional[str]:
    """Save a fetched source page to ``storage`` as a retained record.

    Idempotent: if the page already exists it is not rewritten. Returns the stored
    path (for provenance), or None on failure. Fail-safe: never raises — preservation
    must not break enrichment, but a failure is logged (no silent loss).
    """
    if storage is None or not body:
        return None
    path = _page_path(source_id, url, content_type)
    try:
        if hasattr(storage, "exists") and storage.exists(path):
            return path
        storage.write_bytes(path, body)
        logger.info("Preserved award source page: %s (%d bytes)", path, len(body))
        return path
    except Exception as e:  # noqa: BLE001 - preservation is best-effort, never fatal
        logger.warning("Failed to preserve award page %s: %s", url, e)
        return None


def today_iso() -> str:
    return date.today().isoformat()


ERRORS_PREFIX = "award_sourcing_errors"


def persist_sourcing_errors(
    storage,
    person_id: str,
    person_name: str,
    award_name: str,
    attempts: list,
) -> Optional[str]:
    """Persist failed sourcing attempts to storage (S3/local) as a durable record for
    later RETRY and/or UI intervention. Only writes when at least one attempt errored.

    One JSON file per (person, award, day):
        award_sourcing_errors/<person_id>/<award-slug>-<date>.json
    containing the person/award identity + the error attempts (source, error-with-URL,
    attempted_date). Idempotent per day. Fail-safe: never raises.
    """
    if storage is None or not attempts:
        return None
    errored = [a for a in attempts if a.get("outcome") == "error"]
    if not errored:
        return None
    slug = "".join(
        c if c.isalnum() or c in "-_" else "_" for c in (award_name or "award")
    )[:40]
    pid = person_id or "unknown"
    path = f"{ERRORS_PREFIX}/{pid}/{slug}-{today_iso()}.json"
    record = {
        "person_id": person_id,
        "person_name": person_name,
        "award": award_name,
        "errors": errored,
        "logged_date": today_iso(),
        "status": "needs_retry",  # a retry job / UI can flip this
    }
    try:
        import json

        storage.write_bytes(
            path, json.dumps(record, indent=2, ensure_ascii=False).encode("utf-8")
        )
        logger.info("Persisted %d award-sourcing error(s) -> %s", len(errored), path)
        return path
    except Exception as e:  # noqa: BLE001 - durable log is best-effort, never fatal
        logger.warning("Failed to persist award-sourcing errors: %s", e)
        return None
