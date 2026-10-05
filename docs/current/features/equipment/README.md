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

## Code

| Concern | Module |
|---|---|
| Extraction + mention building | `src/extraction/equipment.py` |
| Dedup (ingest-time merge) | `src/extraction/equipment_ext/dedup.py` |
| Dedup (detection scoring) | `scripts/find_duplicate_equipment.py` |
| Merge tool | `scripts/merge_equipment.py`, `scripts/merge_equipment_dupes.py` |
| Media | `src/extraction/equipment_ext/media.py`, `scripts/backfill_equipment_media.py` |
| Enrichment | `src/extraction/equipment_ext/enrichment.py`, `src/enrichment/equipment_wikipedia.py` |
| Source-recheck gap-fill | `src/extraction/equipment_source_recheck.py` (uses the reusable `src/extraction/source_recheck.py`) |
| **Enforced output schema (source of truth)** | `src/schemas/equipment_output.py` |
| Alias table | `config/equipment_aliases.yaml` |
| Prompt | `prompts/equipment.yaml` |

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
- **Schema version:** `src/schemas/__init__.py::SCHEMA_VERSION` (currently 2.8 — adds the
  per-mention `operating_country` + `captured` origin/operator split and declares
  `original_text` retention).
