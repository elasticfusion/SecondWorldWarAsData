# Equipment Feature

Extraction, deduplication, entity-linking, media, and enrichment for military
equipment mentioned in WWII sources. Equipment is a **single record per equipment
type** with an **`event_mentions`** array (one entry per mention in the corpus),
mirroring the people/people_groups pattern.

**Status:** ✅ Production. Extraction, origin-aware two-stage dedup, entity linking
(people/people_groups/dates/places), per-mention quantity/place/operator with source
traceability, media, enrichment, and source-recheck are implemented and pipeline-wired.
The richer analytics in the proposal (comparisons, timeline, doctrine, …) remain
aspirational.

## End-to-end flow

```
Phase 2 extract (per event file)
  1. load indices  → people, people_groups, dates, places (name→ID), equipment index
  2. Grok extract  → equipment list from the event (prompts/equipment.yaml)
       assertion gate: only extract when the SOURCE ASSERTS presence
       (narrative text or a video/audio narration); ambient/stock footage ignored
  3. per equipment item (_process_equipment_item):
       a. link using_unit → PeopleGroupID, using_person → PersonID
       b. link supporting_units → support_type (supporting arm) + PeopleGroupID + EquipmentID
       c. build mention (_build_mention):
            - record identity: common_name, technical_identifier, country_of_origin
              (= design/manufacture origin, STABLE)
            - per-mention: operating_country, captured, quantity (+quantity_text),
              place_name → single PlaceID, assertion_source, original_text (retained)
            - denormalize DateID (event:sub-event date index) + PlaceID
       d. record-level related_equipment (narrative-sourced): resolve name→EquipmentID,
          auto-create a minimal distinct record (EquipmentID+common_name) when absent
       e. merge_or_create_equipment:
            - match: technical_identifier → exact common_name → fuzzy (≥0.80, alt names)
            - merge fields (alt names, variants, related_equipment accumulate+dedup);
              append the mention
            - else create a new record (+ optional enrichment/media)
  4. generate index.json

Dedup detection (ecs_entrypoint._run_dedup_detection → find_duplicate_equipment.py)
  - normalize (caliber/mm/cm) + alias-expand (equipment_aliases.yaml)
  - score name-similarity + name-contained + same-category
  - VETO on country_of_origin only (never operator); captured flag relaxes the veto
  - merge tool: scripts/merge_equipment.py

Gap-fill (SourceRechecker, source-first)
  - when country_of_origin / category / quantity / place_name missing, recover from the
    retained original_text before any external lookup; gap-fill-only, provenance-stamped

Enrichment-on-identity (follows identity resolution)
  - once a record resolves to a SPECIFIC designation (M4 Sherman, M2 .50 cal — has a
    technical_identifier or a non-generic common_name), enrich ONCE (stamped
    enrichment_status, never re-run): Grokipedia/Wikipedia text/specs/URLs + a canonical
    reference image; vision VERIFIES the TYPE (never the event). Fires for auto-created
    related records too (fixes the Pershing stub). Generic names are skipped.
  - mention-level book images carry image_scope: representative (DEFAULT — generic/stock,
    illustrates the type) vs documentary (source explicitly asserts it depicts this event).
```

## Code

| Concern | Module |
|---|---|
| Extraction + mention building + **ingest-time merge (LIVE)** | `src/extraction/equipment.py` |
| Dedup (detection scoring) | `scripts/find_duplicate_equipment.py` |
| Merge tool | `scripts/merge_equipment.py`, `scripts/merge_equipment_dupes.py` |
| Media | `src/extraction/equipment_ext/media.py`, `scripts/backfill_equipment_media.py` |
| Enrichment | `src/extraction/equipment_ext/enrichment.py`, `src/enrichment/equipment_wikipedia.py` |
| Source-recheck gap-fill | `src/extraction/equipment_source_recheck.py` (uses the reusable `src/extraction/source_recheck.py`) |
| **Enforced output schema (source of truth)** | `src/schemas/equipment_output.py` |
| Alias table | `config/equipment_aliases.yaml` |
| Prompt | `prompts/equipment.yaml` |
| ⚠️ `src/extraction/equipment_ext/dedup.py` | **DEAD/duplicate** — a stale copy of `merge_or_create_equipment`/`_merge_equipment_fields` that nothing imports; the live merge is the `equipment.py` copy. See Gaps. |

## Known gaps

- **Dead duplicate dedup module.** `src/extraction/equipment_ext/dedup.py` duplicates
  `merge_or_create_equipment` + `_merge_equipment_fields` but has **no importers**; the
  live path uses the `equipment.py` copies. The two can drift (e.g. the `related_equipment`
  merge was added only to the live copy). **Action:** delete `equipment_ext/dedup.py` or
  make it the single source and import it. (Not done yet — mid-feature.)
- **PlaceID resolution is exact-name only.** A mention's `place_name` resolves to a
  PlaceID only on an exact (lowercased) match in the places index; near-misses leave
  `place_name` set but `PlaceID` null. No fuzzy place resolution yet.
- **`related_equipment` auto-create now enriches on identity.** Auto-created related
  records (e.g. "M26 Pershing" from a successor link) are enriched on creation when the
  name is a specific identity — Grokipedia/Wikipedia text/specs/URLs + a canonical
  reference image — so they are no longer bare `EquipmentID`+`common_name` stubs. A name
  too generic to be a specific identity is still created minimal (nothing to look up).
- **Supporting-unit equipment linking is name-exact.** `equipment_name` → `EquipmentID`
  uses the same exact-index lookup; no alias/fuzzy resolution.
- **Enrichment-on-identity: image/license fetch LIVE-validated; Grok text + vision still
  stubbed.** The Wikipedia canonical-image + license fetch was run against the live
  Wikipedia/Commons API (M4 Sherman, M26 Pershing → correct article, image, and license
  e.g. "CC BY-SA 2.0"). A live-only bug was found and fixed: Wikipedia appends
  `?utm_source=…` tracking params to image URLs, which corrupted the Commons `File:` title
  and silently broke license lookup (now stripped; regression-tested). Still untested
  live: the **Grok** text/specs enrichment and **vision TYPE verification** (no Grok API
  key configured in this environment) and the OpenSERP image path (`search_media` binary
  absent). The `image_scope` default (representative vs documentary) is unit-tested.
- **Validated by hermetic tests only.** No live end-to-end equipment run has been executed
  against a real chapter (no equipment records currently in `output/`); the origin/operator,
  quantity/place, assertion-gate, related_equipment, and enrichment-on-identity behaviors
  are unit-tested but not yet confirmed against real Grok output.
- **Proposal backlog unbuilt.** `crew_accounts`, comparisons, timeline, doctrine,
  geographic performance, logistics (see MILITARY_EQUIPMENT.md) remain aspirational.
  (`related_equipment` is now built — no longer on this list.)

## Docs

| Doc | Purpose |
|---|---|
| [EQUIPMENT_FINAL_STRUCTURE.md](EQUIPMENT_FINAL_STRUCTURE.md) | The record/mention structure actually produced. Canonical example lives here. |
| [EQUIPMENT_DEDUPLICATION.md](EQUIPMENT_DEDUPLICATION.md) | Dedup behavior, alias/fuzzy matching, and the origin-vs-operator country model. |
| [EQUIPMENT_ENTITY_LINKING.md](EQUIPMENT_ENTITY_LINKING.md) | Linking mentions to people/people_groups/dates by real IDs. |
| [EQUIPMENT_MEDIA_INTEGRATION.md](EQUIPMENT_MEDIA_INTEGRATION.md) | Media sourcing (OpenSERP + wiki) + vision verification + storage. |
| [EQUIPMENT_ERROR_HANDLING.md](EQUIPMENT_ERROR_HANDLING.md) | Error-handling patterns compliance review. |
| [MILITARY_EQUIPMENT.md](MILITARY_EQUIPMENT.md) | **Aspirational proposal** — the original rich schema vision (comparisons, timeline, crew accounts, doctrine, geographic performance). NOT the enforced schema; see FINAL_STRUCTURE + `equipment_output.py` for what is actually produced. |

## Single sources of truth

- **Enforced schema:** `src/schemas/equipment_output.py` (`additionalProperties: false`).
- **Canonical structure example:** `EQUIPMENT_FINAL_STRUCTURE.md` (other docs link here
  rather than repeating the JSON).
- **Schema version:** `src/schemas/__init__.py::SCHEMA_VERSION` (currently 2.10 — adds
  `image_scope` on images + enrichment-on-identity (`enrichment_status`); builds on 2.9's
  record-level `related_equipment` and 2.8's per-mention `quantity`/`place`/
  `operating_country`/`captured` + declared `original_text` retention).
