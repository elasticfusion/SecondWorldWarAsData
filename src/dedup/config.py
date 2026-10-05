"""Config-driven weights/thresholds for people deduplication scoring.

All values are overridable via ``config.yaml`` under ``dedup.people``. If a key (or
the whole block) is absent or invalid, the DEFAULTS below are used — which equal the
historical hardcoded behavior, so dedup is unchanged until deliberately tuned.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict

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
    )


def default_dedup_config() -> DedupConfig:
    return load_dedup_config(None)
