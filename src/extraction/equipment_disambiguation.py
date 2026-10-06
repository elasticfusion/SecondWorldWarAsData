"""Runtime equipment designation disambiguator — CANONICAL LOOKUP ONLY.

Resolves a raw equipment designation (as it appears in a source) to a canonical identity:

    resolve_designation("Sherman V") -> {
        "canonical_name": "M4A2 Sherman",
        "nationality_of_origin": "USA",
        "equivalents": ["M4A2", "Sherman III"],
        "identity_source": "grok_disambiguation",   # or "alias" / "fuzzy" / "exact"
        "confidence": 0.9,
    }

Scope is deliberately narrow — **naming/identity only, no specs**. Specs, images and dedup
are handled downstream by the existing enrichment-on-identity call, which is fed the
canonical name this resolver returns. This exists because the US (M-number), German
(Pz.Kpfw./Ausf./Sd.Kfz.) and British (A-number / service name / Sherman-mark / census)
systems give one vehicle many valid names; see EQUIPMENT_DESIGNATION_SYSTEMS.md.

Fallback ordering (cheap→expensive): exact → curated alias table → fuzzy → **Grok**.
Grok is the long-tail fallback: ONE call per distinct unresolved designation, HARD-CACHED
(never re-run), flag-gated (`EQUIPMENT_DISAMBIGUATION`), and fail-open to the raw name.
Grok resolutions are written to a suggestions file for human promotion into the curated
alias YAML (never auto-edited).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_LOCK = threading.Lock()

_SUGGESTIONS_PATH = Path("output/equipment/disambiguation_suggestions.jsonl")
# Learned-alias store: AUTO-persisted resolutions, SEPARATE from the human-curated
# config/equipment_aliases.yaml (which is never auto-edited). First resolution per
# designation wins -> deterministic + stable across runs, and no repeat Grok call.
_LEARNED_PATH = Path("config/equipment_aliases_learned.yaml")
_FUZZY_THRESHOLD = 0.90  # conservative — avoid cross-type false matches

_LEARNED_CACHE: Optional[Dict[str, Dict[str, Any]]] = None
_LEARNED_LOCK = threading.Lock()


def _load_learned() -> Dict[str, Dict[str, Any]]:
    """Load the learned-alias store (lowercased designation -> resolution dict)."""
    global _LEARNED_CACHE
    if _LEARNED_CACHE is None:
        import yaml

        try:
            data = yaml.safe_load(_LEARNED_PATH.read_text(encoding="utf-8")) or {}
            _LEARNED_CACHE = {
                k.lower(): v for k, v in (data.get("learned") or {}).items()
            }
        except Exception:  # noqa: BLE001 - absent/malformed -> empty
            _LEARNED_CACHE = {}
    return _LEARNED_CACHE


def _persist_learned(key: str, result: Dict[str, Any]) -> None:
    """Persist a resolution to the learned store, FIRST-WINS (never overwrite an existing
    learned entry -> stable canonical identity across runs). Separate from curated YAML.
    """
    import yaml

    with _LEARNED_LOCK:
        learned = _load_learned()
        if key in learned:
            return  # first resolution wins — stability
        entry = {
            "canonical_name": result["canonical_name"],
            "nationality_of_origin": result.get("nationality_of_origin"),
            "equivalents": result.get("equivalents", []),
        }
        learned[key] = entry
        try:
            _LEARNED_PATH.parent.mkdir(parents=True, exist_ok=True)
            _LEARNED_PATH.write_text(
                yaml.safe_dump({"learned": learned}, sort_keys=True), encoding="utf-8"
            )
        except Exception as e:  # noqa: BLE001 - best-effort
            logger.debug("Could not persist learned alias: %s", e)


def _aliases() -> Dict[str, str]:
    from src.extraction.equipment import _equipment_aliases

    return _equipment_aliases()


def _result(
    canonical: str,
    nationality: Optional[str],
    equivalents: List[str],
    source: str,
    conf: float,
) -> Dict[str, Any]:
    return {
        "canonical_name": canonical,
        "nationality_of_origin": nationality,
        "equivalents": equivalents,
        "identity_source": source,
        "confidence": conf,
    }


def resolve_designation(
    name: str, grok_client: Optional[Any] = None
) -> Optional[Dict[str, Any]]:
    """Resolve a raw designation to a canonical identity via exact → alias → fuzzy → Grok.
    Returns a result dict (fail-open: on total failure returns the raw name) or None for
    empty input. Canonical lookup only — no specs."""
    if not name or not name.strip():
        return None
    key = name.strip().lower()

    with _CACHE_LOCK:
        if key in _CACHE:
            return _CACHE[key]

    result = (
        _try_alias(key)
        or _try_learned(key)
        or _try_fuzzy(key)
        or _try_grok(name, key, grok_client)
        or _result(name, None, [], "raw", 0.0)  # fail-open
    )

    with _CACHE_LOCK:
        _CACHE[key] = result
    return result


def _try_alias(key: str) -> Optional[Dict[str, Any]]:
    aliases = _aliases()
    # exact canonical already (a value in the table) -> treat as exact
    if key in set(aliases.values()):
        return _result(key, None, [], "exact", 1.0)
    canonical = aliases.get(key)
    if canonical:
        return _result(canonical, None, [key], "alias", 0.95)
    return None


def _try_learned(key: str) -> Optional[Dict[str, Any]]:
    """Served from the auto-persisted learned store (deterministic, no Grok call)."""
    entry = _load_learned().get(key)
    if entry and entry.get("canonical_name"):
        return _result(
            entry["canonical_name"],
            entry.get("nationality_of_origin"),
            entry.get("equivalents", []),
            "learned_alias",
            0.95,
        )
    return None


def _try_fuzzy(key: str) -> Optional[Dict[str, Any]]:
    aliases = _aliases()
    best, best_ratio = None, 0.0
    for nick, canonical in aliases.items():
        for cand in (nick, canonical):
            ratio = SequenceMatcher(None, key, cand).ratio()
            if ratio > best_ratio:
                best_ratio, best = ratio, canonical
    if best and best_ratio >= _FUZZY_THRESHOLD:
        return _result(best, None, [key], "fuzzy", round(best_ratio, 2))
    return None


def _try_grok(
    name: str, key: str, grok_client: Optional[Any]
) -> Optional[Dict[str, Any]]:
    if grok_client is None:
        return None
    if os.getenv("EQUIPMENT_DISAMBIGUATION", "true").lower() != "true":
        return None
    try:
        from src.utils.prompt_loader import render_prompt

        prompt = render_prompt("equipment_disambiguation", designation=name)
        res = grok_client.extract_json(
            prompt,
            temperature=0.0,
            use_cache=True,
            cache_type="equipment_disambiguation",
        )
        if not isinstance(res, dict) or not res.get("canonical_name"):
            return None
        result = _result(
            str(res["canonical_name"]),
            res.get("nationality_of_origin"),
            [str(e) for e in (res.get("equivalents") or [])],
            "grok_disambiguation",
            float(res.get("confidence", 0.8)),
        )
        _write_suggestion(name, result)
        _persist_learned(key, result)  # auto-update learned coverage (first-wins)
        return result
    except Exception as e:  # noqa: BLE001 - fail-open to the next fallback
        logger.warning("Grok disambiguation failed for %s: %s", name, e)
        return None


def _write_suggestion(raw: str, result: Dict[str, Any]) -> None:
    """Append a Grok resolution to a suggestions file for human promotion into the curated
    alias YAML. Never edits the curated table directly (suggestion-only)."""
    try:
        _SUGGESTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "raw": raw,
            "canonical_name": result["canonical_name"],
            "nationality_of_origin": result.get("nationality_of_origin"),
            "equivalents": result.get("equivalents", []),
            "confidence": result.get("confidence"),
            "suggested_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(_SUGGESTIONS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:  # noqa: BLE001 - suggestions are best-effort
        logger.debug("Could not write disambiguation suggestion: %s", e)
