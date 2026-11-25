"""What a map shows, kept on its Map record (owner decision 2026-11-17, built 2026-11-24).

Grok map vision (``prompts/map_vision.yaml``) answers with a ``depicted`` block: ``units[]``,
``places[]``, ``movements[]``, ``front_line_or_sector``, ``date_or_window``, ``operation``,
``description``, ``legibility_confidence``. It used to be turned into prose and written to
``contentrepository/map-<id>/`` as a new "book" for Phase 2 to read again - a second paid request
per map, and on ECS those files were never uploaded, so every answer was lost when the task ended.

Now the answer is stored on the Map record itself, with its provenance:

* ``depicted`` - the vision block as Grok returned it (lists and strings only, capped), or null when
  the image was judged not to be a relevant map;
* ``depicted_provenance`` - ``verdict`` (``depicted`` | ``not_relevant``), ``kind`` (the Grok request
  kind), ``model``, ``request_id`` (the RequestID the request was asked and checked with), ``checked_at``,
  ``image_url`` (what was looked at) and ``found_in`` (the book whose link led to it, or
  ``openserp``).

A record with ``depicted_provenance`` is never sent to vision again (``was_read``). Linking the units,
places and dates it shows to their entity records is the next step (TODO, Group 8).
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEPICTED = "depicted"
NOT_RELEVANT = "not_relevant"
# below this vision legibility confidence a map's units/places/dates are not trusted (the M7 gate)
MIN_LEGIBILITY_CONFIDENCE = 0.35
_LIST_FIELDS = ("units", "places", "movements")
_TEXT_FIELDS = ("front_line_or_sector", "date_or_window", "operation", "description")
_MAX_ITEMS = 100
_MAX_TEXT = 8000


def legibility_confidence(depicted: Any) -> Optional[float]:
    """The vision-reported legibility confidence (0.0-1.0) when present and a number, else None."""
    if not isinstance(depicted, dict):
        return None
    raw = depicted.get("legibility_confidence")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    # NaN / infinity would make the record invalid JSON; clamp to the stated 0..1 range
    return min(1.0, max(0.0, value)) if math.isfinite(value) else None


def is_legible(depicted: Any) -> bool:
    """Whether the map's contents may be trusted: no stated confidence, or one at or above the gate."""
    conf = legibility_confidence(depicted)
    return conf is None or conf >= MIN_LEGIBILITY_CONFIDENCE


def clean(depicted: Any) -> Optional[Dict[str, Any]]:
    """The ``depicted`` block reduced to its known fields (lists of short strings, capped strings,
    the confidence); None when nothing usable is left. Model output is untrusted data.
    """
    if not isinstance(depicted, dict):
        return None
    out: Dict[str, Any] = {}
    for field in _LIST_FIELDS:
        items = _strings(depicted.get(field))
        if items:
            out[field] = items
    for field in _TEXT_FIELDS:
        value = depicted.get(field)
        if isinstance(value, str) and value.strip():
            out[field] = value.strip()[:_MAX_TEXT]
    conf = legibility_confidence(depicted)
    if conf is not None:
        out["legibility_confidence"] = conf
    return out or None


def _strings(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    items = [v.strip()[:500] for v in value if isinstance(v, str) and v.strip()]
    return items[:_MAX_ITEMS]


def verdict_for(answer: Any) -> str:
    """``depicted`` when Grok called the image a relevant map and said what it shows, else
    ``not_relevant`` (no usable contents to keep)."""
    if not isinstance(answer, dict) or not answer.get("is_relevant"):
        return NOT_RELEVANT
    return DEPICTED if clean(answer.get("depicted")) else NOT_RELEVANT


def provenance(
    verdict: str,
    *,
    kind: str,
    model: Optional[str],
    request_id: Optional[str],
    image_url: str,
    found_in: str,
) -> Dict[str, Any]:
    return {
        "verdict": verdict,
        "kind": kind,
        "model": model or None,
        "request_id": request_id or None,
        "checked_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "image_url": (image_url or "")[:2000] or None,
        "found_in": found_in or None,
    }


def apply(
    record: Dict[str, Any], depicted: Any, prov: Dict[str, Any]
) -> Dict[str, Any]:
    """Put the vision answer and its provenance on a Map record (in place; returned)."""
    record[DEPICTED] = clean(depicted) if prov.get("verdict") == DEPICTED else None
    record["depicted_provenance"] = prov
    return record


def was_read(record: Any) -> bool:
    """Whether vision has already answered for this map (either verdict): never asked again."""
    return isinstance(record, dict) and isinstance(
        record.get("depicted_provenance"), dict
    )


def store(map_file: Path, depicted: Any, prov: Dict[str, Any]) -> bool:
    """Write the answer onto the Map record at ``map_file``. False when it could not be (said)."""
    from src.utils.file_lock import write_json_with_lock

    try:
        record = json.loads(map_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.error(
            "OBSERV map_depicted.read_failed map=%s err=%s status=error - the vision answer "
            "was not stored, so the map is asked about again next run. Check the Map record",
            map_file.name,
            type(e).__name__,
        )
        return False
    if not isinstance(record, dict):
        return False
    if write_json_with_lock(map_file, apply(record, depicted, prov), entity="maps"):
        return True
    logger.error(
        "OBSERV map_depicted.write_blocked map=%s status=error - the schema guard refused the "
        "Map record, so the vision answer is not stored and the map is asked about again next "
        "run. Fix the record's schema errors (see the guard's log line)",
        map_file.name,
    )
    return False


def request_id_for(
    grok_client: Any, prompt: str, image_url: str, kind: str
) -> Optional[str]:
    """The RequestID of the vision request that produced an answer (``GrokClient.vision_request_id``:
    the answer itself no longer carries it once the client has checked it), or None when the
    client cannot say. (Reading the echoed field from the answer, the old way, always gave None.)
    """
    try:
        rid = grok_client.vision_request_id(prompt, image_url, kind)
    except Exception:  # noqa: BLE001 - provenance field: unknown is recorded as null
        return None
    return rid if isinstance(rid, str) and rid else None


def model_for(grok_client: Any, kind: str) -> Optional[str]:
    """The model the client uses for ``kind``, when it says."""
    try:
        model = grok_client._get_model(kind)  # noqa: SLF001 - the client's own routing
    except Exception:  # noqa: BLE001 - unknown: recorded as null
        return None
    return model if isinstance(model, str) else None
