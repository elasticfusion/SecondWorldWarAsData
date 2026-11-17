"""Equipment source-recheck — a thin configuration of the reusable SourceRechecker.

Recovers CRITICAL equipment fields from retained ``event_mentions[].original_text``:
  * country_of_origin — a dedup VETO (US M4 vs a captured/German designation); must be
    present for the equipment dedup country-mismatch guard to work correctly;
  * category — the broad type (armor/aircraft/…), used by same-category corroboration.
Gap-fill only; source-first; fail-safe. See src/extraction/source_recheck.py.
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.extraction.source_recheck import FieldRecheckSpec, SourceRechecker

_FIELDS = {
    "country_of_origin": "the country that DESIGNED/MANUFACTURED this equipment "
    "(NOT whoever is using it here — a Sherman is USA-origin even when used by the "
    "British; a captured M10 used by Germany is still USA-origin), ISO 3166-1 "
    "alpha-3 (e.g. USA, DEU, GBR) or null",
    "category": "one of armor, aircraft, naval, artillery, infantry_weapons, "
    "vehicles, communications, engineering, other, or null",
}


# Asked only when the RECORD lacks them. quantity / quantity_text / place_name are per mention:
# refine already reads them from the same quote onto the mention, and they are never on the
# record dict - so asking for them made every item a recheck request (2,249 in the 2026-11-14
# full-book run, against the ~2,500 items refine had just classified).
_RECORD_FIELDS = ("country_of_origin", "category")


def _needs(rec: Dict[str, Any]) -> List[str]:
    return [f for f in _RECORD_FIELDS if not rec.get(f)]


_SPEC = FieldRecheckSpec(
    fields=_FIELDS,
    needed=_needs,
    label="equipment",
    name_fields=("common_name", "name", "technical_identifier"),
)
_RECHECKER = SourceRechecker(_SPEC)


def recheck_equipment_from_source(data: Dict[str, Any], grok_client: Any) -> int:
    """Recover missing critical equipment fields (country_of_origin, category) from the
    retained source text. Gap-fill only. Returns the count filled."""
    return _RECHECKER.recheck(data, grok_client)
