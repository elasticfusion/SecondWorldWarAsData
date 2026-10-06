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
            - else create a new record (+ enrichment-on-identity; see below)
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
  - canonical disambiguation first (src/extraction/equipment_disambiguation.py):
    exact->alias->fuzzy->Grok (cached) resolves the raw designation to a canonical
    identity (stamped canonical_name + identity_source); enrichment then uses that name.
  - once a record resolves to a SPECIFIC designation (M4 Sherman, M2 .50 cal — has a
    technical_identifier or a non-generic common_name), enrich ONCE (stamped
    enrichment_status, never re-run): Grokipedia/Wikipedia text/specs/URLs + a canonical
    reference image; vision VERIFIES the TYPE (never the event). Fires for auto-created
    related records too (fixes the Pershing stub). Generic names are skipped.
  - a second mention of an already-enriched record is MERGED, not re-enriched; a record
    whose first enrichment FAILED is retried on the next merge (idempotent).
  - gated by src/enrichment/enrichment_gate.py: LIMIT UPDATES (skip if checked within the
    90-day window), stamp enrichment_checked_at + _last_updated on EVERY check, and DIFF
    the revised entry so no-op results don't rewrite content.
  - mention-level book images carry image_scope: representative (DEFAULT — generic/stock,
    illustrates the type) vs documentary (source explicitly asserts it depicts this event).
```

## Code

| Concern | Module |
|---|---|
| Extraction, mention building, **ingest-time merge, enrichment, media/vision (ALL LIVE)** | `src/extraction/equipment.py` |
| Dedup — how it works (spec) | [EQUIPMENT_DEDUPLICATION.md](EQUIPMENT_DEDUPLICATION.md) |
| Dedup (detection scoring) | `scripts/find_duplicate_equipment.py` |
| Merge tool | `scripts/merge_equipment.py`, `scripts/merge_equipment_dupes.py` |
| Media backfill | `scripts/backfill_equipment_media.py` |
| Wikipedia enrichment | `src/enrichment/equipment_wikipedia.py` |
| Enrichment staleness gate | `src/enrichment/enrichment_gate.py` |
| Source-recheck gap-fill | `src/extraction/equipment_source_recheck.py` (uses the reusable `src/extraction/source_recheck.py`) |
| Canonical designation disambiguator | `src/extraction/equipment_disambiguation.py` (exact→alias→fuzzy→Grok, cached) |
| **Enforced output schema (source of truth)** | `src/schemas/equipment_output.py` |
| Alias table | `config/equipment_aliases.yaml` |
| Prompt | `prompts/equipment.yaml` |

> Note: a former `src/extraction/equipment_ext/` sub-package held dead duplicate copies of
> the merge/enrichment/media code (no importers). It was **deleted** — `equipment.py` is
> the single live implementation.

## Known gaps

Open/incomplete items only. (Implemented capabilities — fuzzy PlaceID, alias/category
identity gate, enrich-on-identity, cross-entity staleness/diff, disambiguation + learned
aliases, supporting-unit alias/fuzzy linking, merge reference-redirect — are described in
[EQUIPMENT_FLOW.md](EQUIPMENT_FLOW.md), not here.)

- **No live end-to-end run.** Validated by hermetic tests only; no equipment records exist
  in `output/` and the full `_process_equipment_item` path has not run against a real
  chapter. Origin/operator, quantity/place, assertion gate, related_equipment,
  crew_accounts, and enrichment-on-identity are unit-tested but unconfirmed on real Grok
  output. **This is the top gap** — several silent bugs were already caught only by partial
  live runs.
- **Vision verification + OpenSERP image path untested live.** Wikipedia image/license +
  Grok text/specs enrichment are live-validated; **vision TYPE verification** and the
  **OpenSERP image search** are not (the `search_media` binary is absent in this
  environment). `image_scope` default is unit-tested only.
- **Curated alias table is thin.** `config/equipment_aliases.yaml` lacks Sd.Kfz. numbers,
  British Sherman-marks/A-numbers, and `Pz.Kpfw.`⇄`Panzer`/`Ausf.` normalization. The
  runtime Grok resolver + learned-alias store cover the long tail, but the deterministic
  (no-Grok) path depends on human promotion of
  `output/equipment/disambiguation_suggestions.jsonl` into the curated YAML. A bad learned
  resolution persists until a curated override is added (curated wins). See
  [EQUIPMENT_DESIGNATION_SYSTEMS.md](EQUIPMENT_DESIGNATION_SYSTEMS.md).
- **PlaceID resolution is not geocoded.** Fuzzy name resolution only (exact → containment →
  `SequenceMatcher ≥ 0.88`); no coordinate-based resolution, and sub-0.88 near-misses leave
  `place_name` set with `PlaceID` null.
- **Proposal items deferred.** Comparative analysis, tactical doctrine, and geographic
  performance (`MILITARY_EQUIPMENT.md`) are intentionally unbuilt (lower value / messier
  provenance). `related_equipment`, `crew_accounts`, `timeline`, `technical_evolution`,
  `logistics` are done.

## Docs

| Doc | Purpose |
|---|---|
| [EQUIPMENT_FLOW.md](EQUIPMENT_FLOW.md) | **Comprehensive end-to-end flow** — every stage, decision point, provenance rule, and module/function reference. Start here. |
| [EQUIPMENT_FINAL_STRUCTURE.md](EQUIPMENT_FINAL_STRUCTURE.md) | The record/mention structure actually produced. Canonical example lives here. |
| [EQUIPMENT_DEDUPLICATION.md](EQUIPMENT_DEDUPLICATION.md) | Dedup behavior, alias/fuzzy matching, and the origin-vs-operator country model. |
| [EQUIPMENT_DESIGNATION_SYSTEMS.md](EQUIPMENT_DESIGNATION_SYSTEMS.md) | US vs. German naming (M-number vs. Pz.Kpfw./Ausf./Sd.Kfz.) + implications for dedup/alias/identity. |
| [EQUIPMENT_ENTITY_LINKING.md](EQUIPMENT_ENTITY_LINKING.md) | Linking mentions to people/people_groups/dates by real IDs. |
| [EQUIPMENT_MEDIA_INTEGRATION.md](EQUIPMENT_MEDIA_INTEGRATION.md) | Media sourcing (OpenSERP + wiki) + vision verification + storage. |
| [EQUIPMENT_ERROR_HANDLING.md](EQUIPMENT_ERROR_HANDLING.md) | Error-handling patterns compliance review. |
| [MILITARY_EQUIPMENT.md](MILITARY_EQUIPMENT.md) | **Aspirational proposal** — the original rich schema vision (comparisons, timeline, crew accounts, doctrine, geographic performance). NOT the enforced schema; see FINAL_STRUCTURE + `equipment_output.py` for what is actually produced. |

## Single sources of truth

- **Enforced schema:** `src/schemas/equipment_output.py` (`additionalProperties: false`).
- **Canonical structure example:** `EQUIPMENT_FINAL_STRUCTURE.md` (other docs link here
  rather than repeating the JSON).
- **Schema version:** `src/schemas/__init__.py::SCHEMA_VERSION` (currently **2.14**).
  History: 2.14 `crew_accounts` + `timeline`/`technical_evolution`/`logistics` (source-
  tracked); 2.13 structured `specifications`/`external_data`/`images`/in-file `variants`;
  2.12 `canonical_name` + `identity_source` (designation disambiguator); 2.11
  `enrichment_checked_at` (staleness gate); 2.10 `image_scope` + enrichment-on-identity;
  2.9 record-level `related_equipment`; 2.8 per-mention `quantity`/`place`/
  `operating_country`/`captured` + declared `original_text` retention.
