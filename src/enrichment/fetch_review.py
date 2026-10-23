"""Human-review + promotion bridge for TEXTUAL fetch-provenance finds.

Principle (owner): a positive textual find (e.g. a Wayback snapshot) is enqueued for human
review; **on affirm** it flows through the SAME path as a deliberately-uploaded book — it becomes
a ``SourceMetadata`` record handed to the ingestion front-end (Phase 0/1), rather than being
written directly as facts by an enrichment side-channel.

This closes the gap identified in docs/current/FETCH_PROVENANCE_DESIGN.md: ``source_retrieval``
already quarantines internet content for review, but nothing promoted an AFFIRMED item into
ingestion. ``affirm_fetch_find`` is that bridge (minimal function/CLI first; a UI can call it
later). Reuses: fetch_provenance (the review_status), source_retrieval quarantine, SourceMetadata.

All operations are fail-safe and never process content automatically — promotion only stages a
source for the normal pipeline; extraction remains the pipeline's job.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _load_events() -> List[Dict[str, Any]]:
    """Load local textual fetch-provenance events pending review (local JSONL mode).

    AWS mode (DynamoDB) scanning is intentionally out of scope for the minimal CLI; the local
    JSONL is the reference surface. Returns [] on any error (fail-safe)."""
    import json

    from src.utils import fetch_provenance as fp

    out: List[Dict[str, Any]] = []
    try:
        d: Path = fp._LOCAL_DIR  # noqa: SLF001 - reuse the configured local dir
        if not d.exists():
            return out
        for jf in d.glob("*.jsonl"):
            for line in jf.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if rec.get("kind") == fp.TEXTUAL:
                    out.append(rec)
    except Exception as e:  # noqa: BLE001
        logger.debug("fetch_review load skipped: %s", e)
    return out


def list_pending() -> List[Dict[str, Any]]:
    """Textual finds awaiting human review (review_status == pending)."""
    from src.utils import fetch_provenance as fp

    return [e for e in _load_events() if e.get("review_status") == fp.PENDING]


def set_review_status(fetch_event_id: str, status: str) -> bool:
    """Persist a reviewer's decision (affirmed/rejected) onto a stored fetch-provenance event.
    DynamoDB in AWS mode (overwrite the item), else rewrite the local JSONL line. Fail-safe.
    """
    import json

    from src.utils import fetch_provenance as fp

    try:
        table = fp._table()  # noqa: SLF001 - reuse the configured store
        if table is not None:
            key = f"{fp._KEY_PREFIX}{fetch_event_id}"  # noqa: SLF001
            item = table.get_item(Key={"cache_key": key}).get("Item")
            if not item:
                return False
            rec = json.loads(item.get("response", "{}"))
            rec["review_status"] = status
            table.put_item(Item={"cache_key": key, "response": json.dumps(rec)})
            return True
        # Local JSONL: rewrite the matching line in place.
        d: Path = fp._LOCAL_DIR  # noqa: SLF001
        if not d.exists():
            return False
        updated = False
        for jf in d.glob("*.jsonl"):
            lines = jf.read_text(encoding="utf-8").splitlines()
            out = []
            changed = False
            for line in lines:
                if not line.strip():
                    continue
                rec = json.loads(line)
                if rec.get("fetch_event_id") == fetch_event_id:
                    rec["review_status"] = status
                    changed = True
                out.append(json.dumps(rec))
            if changed:
                jf.write_text("\n".join(out) + "\n", encoding="utf-8")
                updated = True
        return updated
    except Exception as e:  # noqa: BLE001 - a review action must never crash
        logger.debug("set_review_status skipped for %s: %s", fetch_event_id, e)
        return False


def reject_fetch_find(fetch_event: Dict[str, Any]) -> bool:
    """Mark a textual find REJECTED (reviewer declined it as a source). Returns True if persisted."""
    from src.utils import fetch_provenance as fp

    ok = set_review_status(fetch_event.get("fetch_event_id", ""), fp.REJECTED)
    if ok:
        logger.info(
            "OBSERV fetch_find.rejected provider=%s fetch_event_id=%s",
            fetch_event.get("provider"),
            fetch_event.get("fetch_event_id"),
        )
    return ok


def affirm_fetch_find(
    fetch_event: Dict[str, Any], quarantine_path: Path, *, media_type: str = "html"
) -> Optional["SourceMetadata"]:  # type: ignore[name-defined] # noqa: F821
    """Promote an AFFIRMED textual find into a SourceMetadata for ingestion — the SAME front door
    as an uploaded book. Returns the SourceMetadata (staged, not yet extracted) or None on failure.

    ``fetch_event`` is the stored provenance record; ``quarantine_path`` is the reviewed, affirmed
    content already downloaded by source_retrieval's quarantine. Fail-safe.
    """
    try:
        from src.ingestion.source_metadata import SourceMetadata

        endpoint = fetch_event.get("endpoint", "")
        summary = fetch_event.get("result_summary") or ""
        # Capture the archive snapshot URL as the acquisition provenance when present.
        acq_url = endpoint
        if "->" in summary:
            acq_url = summary.split("->", 1)[1].strip() or endpoint

        src = SourceMetadata(
            source_id=f"fetchfind-{fetch_event.get('fetch_event_id', '')}",
            original_path=Path(quarantine_path),
            media_type=media_type,  # type: ignore[arg-type]
            acquisition_method="web_archive_affirmed",
            acquisition_url=acq_url,
            capture_date=fetch_event.get("queried_at"),
            notes=(
                "Promoted from an affirmed textual fetch-provenance find "
                f"(provider={fetch_event.get('provider')}, "
                f"fetch_event_id={fetch_event.get('fetch_event_id')}). "
                "Enters ingestion like an uploaded book."
            ),
        )
        logger.info(
            "OBSERV fetch_find.affirmed provider=%s fetch_event_id=%s -> source_id=%s staged for ingestion",
            fetch_event.get("provider"),
            fetch_event.get("fetch_event_id"),
            src.source_id,
        )
        from src.utils import fetch_provenance as _fp

        set_review_status(fetch_event.get("fetch_event_id", ""), _fp.AFFIRMED)
        return src
    except Exception as e:  # noqa: BLE001 - promotion must never crash a review action
        logger.warning(
            "fetch_find affirm failed for %s: %s",
            fetch_event.get("fetch_event_id"),
            e,
        )
        return None
