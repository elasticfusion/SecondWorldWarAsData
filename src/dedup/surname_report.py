"""Corpus-frequency surname SUGGESTION report (human-gated) for people dedup.

Observes the ingested people corpus and SUGGESTS additions/corrections to the curated
``data/surname_frequency.yaml`` — it NEVER edits that file. A human reviews
``output/people/surname_frequency_suggestions.json`` and promotes entries by hand.

Skew guard (the single-subject-book / "Patton" problem): a suggestion is keyed on the
number of DISTINCT PEOPLE sharing a (nationality, surname), NOT raw mention count. A
Patton biography with 500 "Patton" mentions but one distinct person does NOT make
"Patton" look common; eleven distinct Smiths do.

Regeneration is GROWTH-TRIGGERED: the report is rebuilt only when the people corpus has
grown by at least ``min_new_people`` since the last report (see maybe_generate).

Shape note: this is people-only. Groups will follow a similar pattern keyed on
structured unit designations; equipment is a different (alias/synonym) problem and is
NOT modeled here.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

SUGGESTIONS_FILENAME = "surname_frequency_suggestions.json"
_STATE_FILENAME = ".surname_report_state.json"

# Distinct-person-count -> suggested rarity band (skew-safe thresholds).
_BAND_BY_DISTINCT = [
    (12, "very_common"),
    (6, "common"),
    (3, "uncommon"),
    (1, "rare"),
]


def _band_for(distinct_count: int) -> str:
    for threshold, band in _BAND_BY_DISTINCT:
        if distinct_count >= threshold:
            return band
    return "rare"


def _extract_last_name(name: str) -> str:
    """Last-name extraction mirroring the dedup matcher (rank/suffix tolerant)."""
    from importlib import import_module

    try:
        fdp = import_module("scripts.find_duplicate_people")  # type: ignore
        return fdp._extract_last_name(name)
    except Exception:
        parts = name.split()
        return (parts[-1] if parts else name).lower()


def _canonical_nationality(nat: Optional[str]) -> Optional[str]:
    if not nat:
        return None
    try:
        from src.enrichment.award_sources import canonical_nationality

        return canonical_nationality(nat) or nat.strip().upper()
    except Exception:
        return nat.strip().upper()


def build_suggestions(
    people: list[dict], curated: Dict[str, Dict[str, str]]
) -> Dict[str, Any]:
    """Compute distinct-person counts per (nationality, surname) and emit suggestions
    where the corpus disagrees with / is missing from the curated table.

    ``curated`` is the loaded surname table: {NATIONALITY: {surname: band}}.
    """
    # (nat, surname) -> set of distinct person keys (PersonID or normalized full name)
    distinct: Dict[tuple, set] = defaultdict(set)
    mentions: Dict[tuple, int] = defaultdict(int)
    for p in people:
        name = p.get("name") or ""
        if not name:
            continue
        bp = p.get("biographical_profile") or {}
        nat = _canonical_nationality(
            bp.get("nationality")
            or p.get("nationality")
            or bp.get("nationality_served")
        )
        if not nat:
            continue  # commonness is nationality-conditioned; skip unknown nationality
        surname = _extract_last_name(name)
        if not surname:
            continue
        key = (nat, surname)
        pid = p.get("PersonID") or name.strip().lower()
        distinct[key].add(pid)
        mentions[key] += 1

    suggestions = []
    for (nat, surname), persons in sorted(distinct.items()):
        distinct_count = len(persons)
        observed_band = _band_for(distinct_count)
        curated_band = (curated.get(nat, {}) or {}).get(surname)
        # Suggest when absent, or when the observed band differs from the curated one.
        if curated_band == observed_band:
            continue
        suggestions.append(
            {
                "nationality": nat,
                "surname": surname,
                "distinct_people": distinct_count,  # skew-safe signal
                "mentions": mentions[(nat, surname)],  # shown for context only
                "observed_band": observed_band,
                "curated_band": curated_band,  # None if not yet in the table
                "action": "add" if curated_band is None else "review_change",
            }
        )

    return {
        "_note": (
            "SUGGESTIONS ONLY — review and promote to data/surname_frequency.yaml by "
            "hand. Keyed on distinct_people (skew-safe), not mentions."
        ),
        "total_people": len(people),
        "suggestion_count": len(suggestions),
        "suggestions": suggestions,
    }


def _read_state(people_dir: Path) -> int:
    try:
        state = json.loads((people_dir / _STATE_FILENAME).read_text(encoding="utf-8"))
        return int(state.get("last_report_people_count", 0))
    except Exception:
        return 0


def _write_state(people_dir: Path, count: int) -> None:
    try:
        (people_dir / _STATE_FILENAME).write_text(
            json.dumps({"last_report_people_count": count}, indent=2),
            encoding="utf-8",
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("surname-report state write failed: %s", e)


def maybe_generate(people_dir: Path, config: Optional[dict] = None) -> Optional[Path]:
    """Growth-triggered entry point. Regenerate the suggestion report ONLY when the
    people corpus has grown by >= min_new_people since the last report. Returns the
    report path if (re)generated, else None. Suggestion-only; never writes the curated
    table. Fail-safe."""
    try:
        from src.dedup.config import load_dedup_config, _load_surname_table

        cfg = load_dedup_config(config)
        rep = (
            (config or {}).get("dedup", {}).get("people", {}).get("surname_report", {})
            if isinstance(config, dict)
            else {}
        )
        if not rep.get("enabled", True):
            return None
        min_new = int(rep.get("min_new_people", 50))

        files = [
            f
            for f in people_dir.glob("*.json")
            if f.name
            not in (
                "index.json",
                "duplicate_report.json",
                "not_duplicates.json",
                SUGGESTIONS_FILENAME,
            )
            and not f.name.startswith(".")
        ]
        current_count = len(files)
        last_count = _read_state(people_dir)
        if current_count - last_count < min_new:
            logger.info(
                "Surname report skipped: corpus grew %d (< min_new_people=%d)",
                current_count - last_count,
                min_new,
            )
            return None

        people = []
        for f in files:
            try:
                people.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                continue
        curated = _load_surname_table(
            str(cfg.surname_frequency.get("path", "data/surname_frequency.yaml"))
        )
        report = build_suggestions(people, curated)
        out = people_dir / SUGGESTIONS_FILENAME
        out.write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        _write_state(people_dir, current_count)
        logger.info(
            "Surname suggestion report: %d suggestion(s) over %d people -> %s",
            report["suggestion_count"],
            current_count,
            out,
        )
        return out
    except Exception as e:  # noqa: BLE001 - report is best-effort, never blocks dedup
        logger.warning("Surname suggestion report skipped: %s", e)
        return None
