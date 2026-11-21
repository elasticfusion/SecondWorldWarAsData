"""Which batch answered a cached Grok answer (Group 2, task 1.7: provenance guaranteed).

A Phase 3 answer is cached under its request id (the cache key), and every batch's metrics file
(``output/metrics/batch_<id>_<ts>.json``, also in S3 and, as ``metrics#<id>``, DynamoDB) lists the
request ids it submitted. So the batch of any cached answer is found deterministically: the
round (``-r<n>-`` in the batch name), when it was submitted, and which model(s) answered it. The
records an answer was used for do not carry the batch id; this is the way back.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.utils.batch_rounds import round_of

logger = logging.getLogger(__name__)


_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def _clean(value: Any, limit: int = 200) -> str:
    """Text from a metrics file for the terminal: control characters (ANSI escapes, newlines)
    removed, length capped."""
    return _CTRL.sub(" ", str(value))[:limit]


def batches_for(request_id: str, metrics_dir: Path) -> List[Dict[str, Any]]:
    """Every batch whose metrics list ``request_id`` (oldest first). A request asked again in
    a later round is in several. An unreadable file is logged and skipped."""
    found: List[Dict[str, Any]] = []
    for f in sorted(Path(metrics_dir).glob("batch_*.json")):
        try:
            entry = _entry_from(f, request_id)
        except (OSError, ValueError, TypeError, AttributeError, OverflowError) as e:
            logger.warning(
                "OBSERV provenance.metrics_unreadable file=%s err=%s",
                _clean(f.name),
                type(e).__name__,
            )
            continue
        if entry:
            found.append(entry)
    return sorted(found, key=lambda b: (b["submitted_at"], b["file"]))


def _entry_from(f: Path, request_id: str) -> Optional[Dict[str, Any]]:
    m = json.loads(f.read_text(encoding="utf-8"))
    cache_type = _cache_type_of(m.get("submitted_request_ids") or [], request_id)
    if cache_type is None:
        return None
    models = m.get("served_models") or {}
    return {
        "batch_id": _clean(m.get("batch_id", "")),
        "batch_name": _clean(m.get("batch_name", "")),
        "round": round_of(str(m.get("batch_name", ""))),
        "cache_type": _clean(cache_type),
        "submitted_at": int(m.get("submitted_at", 0) or 0),
        "served_models": {_clean(k): int(v) for k, v in models.items()},
        "file": f.name,
    }


def _cache_type_of(pairs: Iterable[Any], request_id: str) -> Optional[str]:
    for pair in pairs:
        if isinstance(pair, (list, tuple)) and pair and pair[0] == request_id:
            return str(pair[1]) if len(pair) > 1 else "default"
        if pair == request_id:
            return "default"
    return None
