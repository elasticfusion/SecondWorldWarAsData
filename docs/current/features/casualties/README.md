# Casualties Extraction

**Module:** `src/extraction/casualties.py`
**Schema:** `src/schemas/casualties_output.py` (enforced, `additionalProperties: False`)
**Status:** Enabled (`casualties.enabled: true` in `config.yaml`)

---

## Overview

Casualties extraction captures **personnel** losses — killed, wounded, missing, and prisoners
of war — from the chapter's raw parsed text (3-paragraph windows). Equipment/materiel losses belong to the Equipment entity,
not here. Each casualty is cross-referenced to the people, units, places, and date it
involves, so it is queryable by who/where/when rather than only as free text.

**Key features:**
- Window extraction: one Grok request per 3-paragraph window of the raw parsed text (`_ask_casualties` -> `windows.window_event_for`, `chunk_size=1`), never the events' summaries. Each item is attributed to the sub-event that holds its quote (`windows.attribute_found` / `settle`); an item the text does not support is dropped
- Null-tolerant: a null or wrong-shape answer field (role, side, relation, type, description, nationality, names, `impacted_*`) is defaulted or skipped and never drops the casualty (commit 96c31bb1); a casualty that still fails is logged with chapter, sub-event, type, quote and traceback (`_observe_build_failed`)
- A `cause` dimension (combat / weather_exposure / disease / accident / other) — enabling
  queries like weather-caused casualties (frostbite / trench foot / exposure)
- A `side` dimension (allied / axis / civilian / unknown) — who **suffered** the loss
- Resolution of impacted people / organizations / places to their entity IDs
- Direct `PersonID` / `PlaceID` anchors for a clean person + place + date join
- `original_text` retained for source traceability

---

## Data Structure

```json
{
  "CasualtyID": "01ULID...",
  "type": "casualties|killed|wounded|pow|missing|non_battle (free-form)",
  "cause": "combat|weather_exposure|disease|accident|other|null",
  "side": "allied|axis|civilian|unknown",
  "description": "...",
  "original_text": "exact quote from source",
  "PersonID": "01ULID...|null",
  "PlaceID": "01ULID...|null",
  "date": { "DateID": "01ULID...|null", "date_string": "9 August", "precision": "unknown" },
  "event_context": { "EventID": "01ULID...", "Sub-eventID": "01ULID...|null" },
  "source": { "EventID": "...", "Sub-eventID": "...", "book": "...", "chapter": "...", "paragraph_number": 89 },
  "count": {
    "total":    {"value": 500, "qualifier": "approximately"},
    "killed":   {"value": 100, "qualifier": "exact"},
    "wounded":  {"value": 300, "qualifier": "exact"},
    "missing":  {"value": 50,  "qualifier": "exact"},
    "captured": {"value": 50,  "qualifier": "exact"}
  },
  "impacted_organizations": [
    { "PeopleGroupID": "01ULID...|null", "name": "358th Infantry", "nationality": "USA", "role": "suffered_casualties" }
  ],
  "impacted_people": [ { "PersonID": "01ULID...|null", "name": "Capt. Smith", "casualty_type": "killed" } ],
  "impacted_places": [ { "PlaceID": "01ULID...|null", "name": "le Mans" } ],
  "impacted_equipment": [ { "EquipmentID": "01ULID...|null", "name": "M4 Sherman", "relation": "causative|medical" } ]
}
```

**Required fields:** `CasualtyID`, `type`. `count`, `date`, and `source` are loosely typed
(object/string/number/null) — the inner shapes above are illustrative, not strictly enforced.

### Field notes
- **`type`** — free-form string (`killed`/`wounded`/`pow`/`missing`/`non_battle`/
  `casualties`, …).
- **`cause`** — enum `combat | weather_exposure | disease | accident | other`; validated
  against `_VALID_CAUSES`, unknown values dropped.
- **`side`** — enum `allied | axis | civilian | unknown` (who suffered); validated against
  `VALID_SIDES`, defaults `unknown`.
- **Direct `PersonID`/`PlaceID`** — the individual anchors: prefer an explicit LLM-provided
  ID; else, for a single-person / single-place casualty, the sole resolved impacted
  person/place is hoisted (`_set_direct_anchors`) so a person+place+date query needs no
  free-text parsing.
- **`event_context` vs `source`** — both carry EventID + hyphenated `Sub-eventID`; `source`
  adds book / chapter / paragraph_number.
- **`role`** (impacted_organizations) — normalized against `VALID_ROLES`
  (`attacking_force` / `defending_force` / `captured` / `captor` / `suffered_casualties`).

---

## Flow

```
Parsed chapter text -> 3-paragraph windows (src/extraction/windows.py)
  ↓ _has_casualty_mention gate (skip windows with no casualty language)
One Grok request per window (_ask_casualties -> _batch_extract_casualties, chunk_size=1)
  ↓ windows.attribute_found (settle the quote, assign the real sub-event; else drop)
  ↓ _validate_items
For each casualty (_build_casualty):
  ↓ resolve date   -> DateID            (_resolve_casualty_date / dates index)
  ↓ resolve people -> PersonID          (_resolve_people)
  ↓ resolve orgs   -> PeopleGroupID     (_resolve_organizations)
  ↓ resolve places -> PlaceID           (_resolve_places)
  ↓ cause + side validated against enums
  ↓ _set_direct_anchors (hoist single person/place to top-level PersonID/PlaceID)
Write output/casualties/{CasualtyID}.json
```

Resolution reuses each category's **shared library** (not bespoke exact-string matching):
- **People_groups** → GroupID via the canonical `unit_key` (`group_resolver.resolve_group_id`
  — structural number/service/arm/echelon match, so "358th Infantry" → "358th Infantry
  Regiment"; ambiguous/generic names decline to null).
- **Place** → PlaceID via the alias-aware `places._build_place_name_index` + `_match_place_id`.
- **Date** → DateID via the interval-aware `_resolve_date_link` (exact `date_start` OR
  resolved-interval overlap, carrying `time_source`).
- **Equipment** → EquipmentID via `equipment_disambiguation.resolve_designation` + the
  equipment index — the `impacted_equipment[]` link (CONTEXT: equipment *involved in* a
  personnel casualty). `relation` is an enforced dimension ∈ {`causative` (the weapon/vehicle
  that inflicted the loss), `medical` (ambulance/surgical/evacuation equipment responding),
  `other`}; NOT equipment-loss cataloging.
- **Logistics** → LogisticsID via the shared `logistics_resolver` (`impacted_logistics[]`),
  two tiers: **explicit** (the source ties the casualty to a named supply/transport failure)
  and **co_occurring** (an inferred *candidate* when the casualty shares a GroupID AND
  co-occurs in time — shared DateID or ISO-interval overlap — with a supply-type logistics
  record). The `association` field labels the tier; `co_occurring` is a review candidate,
  never asserted as causation (logistics carries no PlaceID, so place is not a join key).
- **People** → PersonID via exact name match. **By design:** there is no shared fuzzy
  people resolver (the whole codebase matches people exactly), people records carry
  essentially no aliases, and fuzzy person-matching risks wrong-person links — null is safer
  than a wrong individual.
- **Date** → DateID also infers a **missing year** from the event's single-year date
  context (`_event_year_lookup`): a bare "9 August" resolves to 1944-08-09 when the event is
  wholly in 1944; multi-year events decline (no fabrication). Lifted date resolution 608→1058
  (+74%) on the corpus.

Unresolved references keep the name with a `null` ID (never fabricated).

> **Note:** corpus counts quoted above (608→1058 dates, 414 shared GroupIDs) were measured on
> an earlier run; records written before these resolvers existed show the older, sparser
> resolution until re-extracted.

---

## Phase 3 Enrichment

**None by design.** Casualties are extracted and cross-referenced in Phase 2; they receive no
external enrichment in Phase 3. See [Phase 3 Enrichment](../../core/PHASE3_ENRICHMENT.md).

---

## Known gaps

- Many records have `null` `PersonID`/`PlaceID`/`DateID` — resolution depends on the
  referenced entities already existing in the corpus; unresolved anchors are left null.
- `count`, `date`, and `source` are loosely typed in the schema; the inner shapes are
  convention, not enforced.

## Related
- [People](../people/README.md) · [People Groups](../people_groups/README.md) ·
  [Places](../places/README.md) · [Dates](../dates/README.md)
- [Schema Reference — Casualties](../../SCHEMA_REFERENCE.md)
