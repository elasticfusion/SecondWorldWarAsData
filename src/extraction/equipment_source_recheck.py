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
    "quantity": "the EXACT number of this equipment the text states, as an integer, "
    "or null if the text gives only a vague count or none",
    "quantity_text": "the VERBATIM count phrase from the text (e.g. '10', 'several', "
    "'a handful') or null if no count is stated — never invent a number",
    "place_name": "where this equipment was per the text (e.g. 'the crossroads in "
    "Cherbourg') or null",
}


def _needs(rec: Dict[str, Any]) -> List[str]:
    fields = (
        "country_of_origin",
        "category",
        "quantity",
        "quantity_text",
        "place_name",
    )
    return [f for f in fields if not rec.get(f)]


_SPEC = FieldRecheckSpec(
    fields=_FIELDS,
    needed=_needs,
    label="equipment",
    name_fields=("common_name", "name", "technical_identifier"),
    coerce={"quantity": int},
)
_RECHECKER = SourceRechecker(_SPEC)


def recheck_equipment_from_source(data: Dict[str, Any], grok_client: Any) -> int:
    """Recover missing critical equipment fields (country_of_origin, category) from the
    retained source text. Gap-fill only. Returns the count filled."""
    return _RECHECKER.recheck(data, grok_client)
