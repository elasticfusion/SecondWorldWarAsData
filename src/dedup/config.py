"""Config-driven weights/thresholds for people deduplication scoring.

All values are overridable via ``config.yaml`` under ``dedup.people``. If a key (or
the whole block) is absent or invalid, the DEFAULTS below are used — which equal the
historical hardcoded behavior, so dedup is unchanged until deliberately tuned.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Defaults == prior hardcoded behavior. auto_merge_threshold is intentionally high so
# the richer scoring only improves human-review candidates; expanding auto-merge is a
# deliberate, operator-controlled threshold change.
_DEFAULTS: Dict[str, Any] = {
    "candidate_threshold": 0.5,
    "auto_merge_threshold": 999.0,
    "weights": {
        "shared_external_url": 3.0,
        "event_overlap_high": 1.5,
        "event_overlap_medium": 0.8,
        "shared_biographical": 0.5,
        "middle_initial_subset": 0.5,
        "same_last_name": 0.4,
    },
    "proximity": {
        "tight_radius_words": 60,
        "near_radius_words": 400,
        "loose_radius_words": 4000,
        "tight_weight": 0.8,
        "near_weight": 0.4,
        "loose_weight": 0.15,
        "cross_document_weight": 0.0,
    },
    "conflicting_middle_initial_weight": -5.0,
    "frequency": {"enabled": True, "dominant_surname_boost": 0.3},
    "commonness": {"enabled": True, "common_surname_damping": 0.5},
    # Nationality-aware surname rarity prior (scales bare-surname match evidence by how
    # rare the surname is in the person's nationality). Falls back to corpus-relative
    # commonness when nationality or a table entry is absent.
    "surname_frequency": {
        "enabled": True,
        "path": "data/surname_frequency.yaml",
        # multipliers applied to the surname-match contribution by rarity band
        "rare_multiplier": 1.5,
        "uncommon_multiplier": 1.1,
        "common_multiplier": 0.6,
        "very_common_multiplier": 0.4,
    },
    # Rank difference interacts with proximity: a DIFFERENT rank-set in tight proximity
    # is a mild negative (likely two people seen together); across wide separation it is
    # near-neutral (promotion over time is plausible). Dampen, never veto.
    "rank_proximity": {
        "enabled": True,
        "tight_penalty": -0.6,  # different rank-set + tight proximity
        "near_penalty": -0.2,  # different rank-set + near proximity
        "loose_penalty": 0.0,  # different rank-set + far apart -> neutral
    },
}


@dataclass
class DedupConfig:
    candidate_threshold: float
    auto_merge_threshold: float
    weights: Dict[str, float]
    proximity: Dict[str, float]
    conflicting_middle_initial_weight: float
    frequency: Dict[str, Any]
    commonness: Dict[str, Any]
    surname_frequency: Dict[str, Any] = field(default_factory=dict)
    rank_proximity: Dict[str, Any] = field(default_factory=dict)

    def weight(self, key: str) -> float:
        return float(self.weights.get(key, _DEFAULTS["weights"].get(key, 0.0)))


def _num(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _merge(section: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
    """Shallow-merge a config sub-dict over defaults, coercing numbers; invalid or
    absent keys fall back to the default (with a debug note)."""
    out = dict(defaults)
    if not isinstance(section, dict):
        return out
    for k, dv in defaults.items():
        if k not in section:
            continue
        v = section[k]
        if isinstance(dv, bool):
            out[k] = bool(v)
        elif isinstance(dv, (int, float)):
            out[k] = _num(v, dv)
        else:
            out[k] = v
    return out


def load_dedup_config(config: Dict[str, Any] | None = None) -> DedupConfig:
    """Build a validated DedupConfig from a loaded config dict (or defaults)."""
    people: Dict[str, Any] = {}
    if isinstance(config, dict):
        people = (config.get("dedup") or {}).get("people") or {}

    return DedupConfig(
        candidate_threshold=_num(
            people.get("candidate_threshold"), _DEFAULTS["candidate_threshold"]
        ),
        auto_merge_threshold=_num(
            people.get("auto_merge_threshold"), _DEFAULTS["auto_merge_threshold"]
        ),
        weights=_merge(people.get("weights", {}), _DEFAULTS["weights"]),
        proximity=_merge(people.get("proximity", {}), _DEFAULTS["proximity"]),
        conflicting_middle_initial_weight=_num(
            people.get("conflicting_middle_initial_weight"),
            _DEFAULTS["conflicting_middle_initial_weight"],
        ),
        frequency=_merge(people.get("frequency", {}), _DEFAULTS["frequency"]),
        commonness=_merge(people.get("commonness", {}), _DEFAULTS["commonness"]),
        surname_frequency=_merge(
            people.get("surname_frequency", {}), _DEFAULTS["surname_frequency"]
        ),
        rank_proximity=_merge(
            people.get("rank_proximity", {}), _DEFAULTS["rank_proximity"]
        ),
    )


def default_dedup_config() -> DedupConfig:
    return load_dedup_config(None)


# ---- Nationality-aware surname-frequency table ----
_RARITY_MULT_KEY = {
    "rare": "rare_multiplier",
    "uncommon": "uncommon_multiplier",
    "common": "common_multiplier",
    "very_common": "very_common_multiplier",
}


@lru_cache(maxsize=4)
def _load_surname_table(path: str) -> Dict[str, Dict[str, str]]:
    """Load the curated nationality->surname->rarity table. Returns {} on any error
    (missing file / parse error) so the prior degrades gracefully to corpus commonness.
    """
    try:
        import yaml

        from pathlib import Path

        p = Path(path)
        if not p.is_file():
            return {}
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        raw = data.get("surnames", {}) or {}
        # normalize: upper nationality keys, lower surnames
        out: Dict[str, Dict[str, str]] = {}
        for nat, entries in raw.items():
            if isinstance(entries, dict):
                out[str(nat).upper()] = {
                    str(s).lower(): str(band).lower() for s, band in entries.items()
                }
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning("surname-frequency table load failed (%s): %s", path, e)
        return {}


def surname_rarity_multiplier(
    cfg: DedupConfig, surname: str, nationality: Optional[str]
) -> Optional[float]:
    """Multiplier for a bare-surname match given the person's nationality, or None when
    the prior can't apply (disabled / no nationality / surname not in the table) — the
    caller then falls back to corpus-relative commonness.

    Rare surname in that nationality -> >1 (boost); common -> <1 (damp)."""
    sf = cfg.surname_frequency
    if not sf.get("enabled", True) or not surname or not nationality:
        return None
    table = _load_surname_table(str(sf.get("path", "data/surname_frequency.yaml")))
    nat = nationality.strip().upper()
    band = table.get(nat, {}).get(surname.strip().lower())
    if not band:
        return None
    return float(sf.get(_RARITY_MULT_KEY.get(band, ""), 1.0))
