"""LLM date significance summary — a DERIVED, synthesized convenience field.

For a date that can accumulate 1–1000+ event_mentions, write a 1–2 sentence "what this date
is about / why it matters" summary so a reader (or a RAG result) gets the gist without
reading every mention.

Honesty rules (consistent with the project's source-authority principle):
  - **Source-grounded only.** The summary is synthesized STRICTLY from the date's own
    ``event_mentions`` (Event/Sub-event names + ``original_text``) — never outside/world
    knowledge. The LLM is told to summarize only what those mentions say.
  - **Clearly marked derived.** Stamped ``summary_source="synthesized"`` +
    ``summary_generated_at`` + ``summary_mention_count`` (how many mentions it covers).
    The authoritative facts remain the individual mentions (each with its own
    ``original_text``/book); the summary is a convenience layer on top.
  - **Never fabricate.** Fail-open: on any error, leave the summary unset.
  - A cheap ``mention_count`` is always stamped (importance signal), independent of the LLM.

Runs as a SEPARATE pass (``summarize_dates``) so it can refresh as mentions grow, gated by
staleness: regenerate only when the mention set materially grew since the last summary.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: regenerate a summary only when the mention set grew by at least this many since the last
#: summary (avoids re-calling Grok for trivial growth). Config could override later.
_MIN_MENTION_GROWTH = 3


def _mention_count(date_data: Dict[str, Any]) -> int:
    return len(date_data.get("event_mentions") or [])


def _mentions_hash(date_data: Dict[str, Any]) -> str:
    """Stable content hash of the date's mentions (Sub_eventID + time_start + original_text,
    order-independent). Changes when mentions are added, removed, OR edited in content —
    so re-summary isn't gated on COUNT alone (a corrected/replaced mention at the same
    count still triggers)."""
    import hashlib

    items = sorted(
        f"{m.get('Sub_eventID','')}|{m.get('time_start') or ''}|{m.get('original_text') or ''}"
        for m in (date_data.get("event_mentions") or [])
    )
    return hashlib.sha256("\n".join(items).encode("utf-8")).hexdigest()[:16]


def needs_summary(date_data: Dict[str, Any]) -> bool:
    """True if this date should be (re)summarized. Has mentions, AND either: no summary
    yet; the mention CONTENT changed since the last summary (hash differs — catches
    corrected/replaced text at the same count); or the set grew by >= the growth threshold
    (secondary trigger)."""
    count = _mention_count(date_data)
    if count == 0:
        return False
    if not date_data.get("summary"):
        return True
    # content-aware: any change to the mention set's content re-summarizes
    if date_data.get("summary_mentions_hash") != _mentions_hash(date_data):
        return True
    since = date_data.get("summary_mention_count") or 0
    return (count - since) >= _MIN_MENTION_GROWTH


def _render_mentions(date_data: Dict[str, Any], limit: int = 60) -> str:
    """Compact, source-grounded view of the date's mentions for the prompt.

    For dates with more mentions than `limit`, select a REPRESENTATIVE slice (one per
    distinct Event/Sub-event first, so the summary isn't dominated by one repeated event)
    and PREFIX a note that this is a partial view of the true total — so the model knows it
    is summarizing a sample, not arbitrarily the first N by file order."""
    mentions = date_data.get("event_mentions") or []
    total = len(mentions)

    if total > limit:
        # representative: first occurrence of each distinct (Event, Sub-event), then fill
        seen: set = set()
        primary: List[Dict[str, Any]] = []
        rest: List[Dict[str, Any]] = []
        for m in mentions:
            k = (m.get("Event_Name"), m.get("Sub_event_Name"))
            (primary if k not in seen else rest).append(m)
            seen.add(k)
        chosen = (primary + rest)[:limit]
    else:
        chosen = mentions

    lines: List[str] = []
    for m in chosen:
        ev = m.get("Event_Name") or ""
        se = m.get("Sub_event_Name") or ""
        ot = (m.get("original_text") or "").strip()
        label = " / ".join([p for p in (ev, se) if p])
        lines.append(f"- {label}: {ot}" if label else f"- {ot}")

    body = "\n".join(lines)
    if total > limit:
        return (
            f"(PARTIAL VIEW: showing {len(chosen)} representative of {total} total "
            f"mentions on this date; summarize the overall significance, noting the scale)\n"
            + body
        )
    return body


def generate_date_summary(date_data: Dict[str, Any], grok_client: Any) -> bool:
    """Generate + stamp a source-grounded significance summary for one date record.
    Returns True if the record was modified. Fail-open. Always stamps mention_count."""
    count = _mention_count(date_data)
    prev_count = date_data.get("mention_count")
    date_data["mention_count"] = count  # cheap importance signal, always present

    if not needs_summary(date_data):
        return count != prev_count  # True only if the stamped count actually changed

    try:
        from src.utils.prompt_loader import render_prompt

        prompt = render_prompt(
            "date_summary",
            date_label=date_data.get("date_start", ""),
            mentions=_render_mentions(date_data),
        )
        res = grok_client.extract_json(
            prompt, temperature=0.1, use_cache=True, cache_type="date_summary"
        )
        summary = (res or {}).get("summary") if isinstance(res, dict) else None
        if not summary:
            return True  # mention_count stamped; no summary produced
        date_data["summary"] = str(summary).strip()
        date_data["summary_source"] = "synthesized"
        date_data["summary_generated_at"] = datetime.now(timezone.utc).isoformat()
        date_data["summary_mention_count"] = count
        date_data["summary_mentions_hash"] = _mentions_hash(date_data)
        return True
    except Exception as e:  # noqa: BLE001 - derived convenience; never block
        logger.warning("Date summary skipped for %s: %s", date_data.get("DateID"), e)
        return True  # mention_count was still stamped


def summarize_dates(
    dates_dir: "Any",
    grok_client: Any,
    max_dates: Optional[int] = None,
    max_workers: int = 6,
) -> int:
    """Separate pass: (re)summarize date files whose mention set is new/grown. Idempotent;
    staleness-gated. Parallelized with a thread pool (matching the people/groups/places
    enrichment pattern); also batch-mode compatible — if the GrokClient is in batch mode,
    each extract_json call is collected for the xAI Batch API (50% discount). Returns the
    number of files updated."""
    import json
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from pathlib import Path

    from src.utils.file_lock import write_json_with_lock

    dates_dir = Path(dates_dir)
    files = [f for f in sorted(dates_dir.glob("*.json")) if f.name != "index.json"]
    if max_dates:
        files = files[:max_dates]

    def _summarize_one(f: Path) -> bool:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        before = (
            data.get("summary"),
            data.get("summary_mention_count"),
            data.get("mention_count"),
        )
        generate_date_summary(data, grok_client)
        after = (
            data.get("summary"),
            data.get("summary_mention_count"),
            data.get("mention_count"),
        )
        if after != before:
            write_json_with_lock(f, data)
            return True
        return False

    updated = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_summarize_one, f): f for f in files}
        for future in as_completed(futures):
            try:
                if future.result():
                    updated += 1
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "Date summary failed for %s: %s", futures[future].name, e
                )

    logger.info("Date summary pass updated %d file(s)", updated)
    return updated
