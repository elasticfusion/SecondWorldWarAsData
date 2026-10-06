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

- **PlaceID resolution is now fuzzy.** A mention's `place_name` resolves to a PlaceID via
  the alias-aware places index (`places._build_place_name_index`, keyed on `current_name`
  + every alias) using `_resolve_place_id`: exact → whole-word containment ("the crossroads
  in Cherbourg" → Cherbourg, longest key wins) → conservative `SequenceMatcher` ratio ≥ 0.88
  (tolerates typos like "Cherbourge"). Below threshold → no match (never guesses). Not yet
  geocoded (no coordinate-based resolution).
- **Enrichment identity gate is alias- and category-aware.** `_is_specific_identity`
  treats a nickname that resolves via `config/equipment_aliases.yaml` (Sherman→M4 Sherman,
  88→88mm Flak 36, Tiger→German Pzkpfw VI) as a specific identity and enriches using the
  **canonical** name; bare category/subcategory classification phrases ("medium tank" vs
  "M4", "field gun", "fighter-bomber") are treated as NON-specific and skipped. Aliases map
  nicknames→specific types only — generics are never aliased to a specific identity.
- **`related_equipment` auto-create now enriches on identity.** Auto-created related
  records (e.g. "M26 Pershing" from a successor link) are enriched on creation when the
  name is a specific identity — Grokipedia/Wikipedia text/specs/URLs + a canonical
  reference image — so they are no longer bare `EquipmentID`+`common_name` stubs. A name
  too generic to be a specific identity is still created minimal (nothing to look up).
- **Enrichment staleness/diff is consistent across entities (people/groups/places/equipment).**
  People, people_groups, and places all gate re-enrichment with `last_enrichment_search` +
  `_should_re_search` (90-day `re_search_after_days` window); equipment uses the reusable
  `enrichment_gate` (`enrichment_checked_at` + staleness window). Diff-the-revised-entry
  (skip no-op rewrites): equipment ✓, places ✓ (gap-fill merge), groups ✓ (now diffs
  `enrichment_data` before rewriting). Minor remaining: the staleness check is implemented
  per-module (`_should_re_search` duplicated in people/groups/places) rather than via the
  shared `enrichment_gate` — a consolidation opportunity, not a correctness gap.
- **Multi-national designation disambiguation — runtime Grok resolver built; curated-table
  coverage still thin.** The same type has many valid names across US/German/British
  systems (`M4`=`Sherman V`; `Panzer IV`=`Pz.Kpfw. IV Ausf. H`=`Sd.Kfz. 161/2`;
  `Firefly`=`Sherman IC`). `src/extraction/equipment_disambiguation.py` now resolves a raw
  designation to a canonical identity at runtime (exact→alias→fuzzy→**Grok**, cached,
  canonical-lookup only, provenance `identity_source`), so the long tail is handled without
  exhaustive hand cross-walks. **Remaining:** the curated `equipment_aliases.yaml` still
  lacks Sd.Kfz. numbers / British marks / `Pz.Kpfw.`⇄`Panzer` normalization — Grok
  resolutions are written to `output/equipment/disambiguation_suggestions.jsonl` for human
  promotion into the YAML (to cut Grok cost over time). British **census numbers** must
  never be used as a type identity. See [EQUIPMENT_DESIGNATION_SYSTEMS.md](EQUIPMENT_DESIGNATION_SYSTEMS.md).
- **Supporting-unit equipment linking is name-exact.** `equipment_name` → `EquipmentID`
  uses the same exact-index lookup; no alias/fuzzy resolution.
- **Enrichment-on-identity: Wikipedia + Grok text/specs LIVE-validated; vision + OpenSERP
  images still untested.** Run against the live APIs with the real Grok key (loaded from
  `.env` via `load_dotenv`). Confirmed working: Wikipedia canonical image + license
  (M4 Sherman, M26 Pershing) and Grok text/specs enrichment (returns weight/speed/armament/
  crew/variants); `_enrich_on_identity` on an M26 Pershing stub stamps `enrichment_status`
  and fills specs end-to-end. **Three live-only bugs found and fixed:** (1) Wikipedia image
  URLs carry `?utm_source=…` params that broke Commons license lookup; (2) the (now-deleted)
  `equipment_ext/enrichment.py` had a runtime `NameError` (TYPE_CHECKING-only `GrokClient`
  used in a signature); (3)
  `_enrich_equipment_data` did a bare `json.loads` on a ```` ```json ````-fenced response,
  silently returning `{}` for EVERY record — now uses `extract_json`. Still untested live:
  **vision TYPE verification** + the **OpenSERP image path** (`search_media` binary absent
  in this environment). `image_scope` default is unit-tested.
- **Validated by hermetic tests only.** No live end-to-end equipment run has been executed
  against a real chapter (no equipment records currently in `output/`); the origin/operator,
  quantity/place, assertion-gate, related_equipment, and enrichment-on-identity behaviors
  are unit-tested but not yet confirmed against real Grok output.
- **Proposal backlog — crew_accounts + reference facts built; 3 items deferred.**
  `related_equipment`, **`crew_accounts`** (narrative-sourced, person-linked, mandatory
  `original_text`+`book`), and the Group A reference facts **`timeline`**,
  **`technical_evolution`**, **`logistics`** (enrichment-sourced, each stamped with
  `source`+`source_url`) are built. Still deferred (lower value / messier provenance):
  comparative analysis, tactical doctrine, geographic performance.

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
- **Schema version:** `src/schemas/__init__.py::SCHEMA_VERSION` (currently 2.14 — adds canonical_name + identity_source (designation disambiguator); 2.11 added
  `enrichment_checked_at` (enrichment staleness gate); 2.10 added `image_scope` + enrichment-on-identity; builds on 2.9's
  record-level `related_equipment` and 2.8's per-mention `quantity`/`place`/
  `operating_country`/`captured` + declared `original_text` retention).
