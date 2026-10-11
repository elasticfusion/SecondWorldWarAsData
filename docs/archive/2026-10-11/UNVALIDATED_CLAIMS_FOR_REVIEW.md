# Unvalidated documentation claims — for human review

> **DISPOSITIONED + ARCHIVED 2026-10-11.** Reviewed. One item with real engineering utility — expose
> the `external_maps` license/download config — was promoted to `TODO.md` (LOW #13). One flagged
> item was downgraded after verification: `GROUP_DEDUP_RULES_REVIEW.md`'s "byte-identical vs
> canonical-key auto-merge" contradiction is moot — the live code uses canonical unit-key resolution
> (`group_resolver.resolve_group_id`); the cited `_auto_merge_exact_duplicates` symbol does not exist
> (doc fix only, no code gap). The `search-history` URL-dedup claim was VALIDATED (fields
> `external_source_url`/`source_url` exist in `openserp_maps.py`/`search_history.py`). The remainder
> below are documentation-hygiene fixes (stale numbers, dead links, naming), tracked here for a
> future doc-cleanup pass — not product backlog.

---

Produced by the 2026-10-11 comprehensive documentation audit. Each entry is a claim in a **retained
(CURRENT)** doc that could NOT be tied to a real code file during the audit, and that is NOT an
objective, auto-correctable fact (those were fixed in place). These need a human to decide: fix the
doc, fix the code, or confirm it's aspirational/out-of-date.

Separately: 11 wholly-stale docs were archived to `docs/archive/2026-10-11/` (see that dir's
README), and objectively-correctable stale facts (prompt/search-query counts 27→31 / 6→8, schema
version header → per-entity, "11 entity types" → 14, `merge_duplicate_people.py` → `scripts/archive/`,
and the nonexistent `import_to_mongodb.py` line removed from LOCAL_DEPLOYMENT.md — the real import is
root-relative `import_to_dynamodb.py`; there is no MongoDB path) were corrected in place.

## Dangling doc cross-references (link targets that don't exist)
These are referenced as if they were live docs but no such file exists. Decide: write the doc,
repoint to the real doc, or remove the reference.
- `features/external-maps/README.md` → `EXTERNAL_MAPS_AUTOMATED_SEARCH.md`, `EXTERNAL_MAPS_AI_GUIDE.md`, and a root `external_maps.yaml.example` (none exist).
- `features/external-maps/whitelist-config.md` → `COMBINED_MAP_SEARCH.md`, `GROK_SEARCH_MAPS.md` (none exist).
- `core/PROMPT_MANAGEMENT.md` → `TODO_short_response_rejection.md`, `TODO_fetch_endnote_content.md` cited as live but are archived under `docs/archive/2026-03-19/`.

## Claims about code behavior not found in source
- `features/external-maps/image-processing.md`: references `logs/grok_search.log`; no such log name in source (pipeline logs to `logs/pipeline.log` per `config.yaml`). Doc artifact — confirm or drop.
- `features/external-maps/domain-blacklist.md` / `combined-search.md`: the Go filter's "automatically appends an audit-trail comment" behavior could not be tied to any inspected source; `tools/search_maps.go` was not line-verified. Confirm against `tools/search_maps.go`.
- `features/external-maps/search-history.md`: the URL-dedup "checks `external_source_url` and `source_url` in `output/external_maps/*.json`" claim was not pinned to a specific symbol. Confirm the field names in the openserp/combined map flow.
- `core/ISO_COUNTRY_CODES.md`: attributes `PeopleGroup.country_of_origin` to `people_groups.py`, but the field is defined in `src/schemas.py` / `src/schemas/groups_output.py` (field exists; file attribution wrong). Fix attribution.

## Internally inconsistent / stale config examples (verify intended value)
- `features/batch_processing/README.md`: shows `concurrency: max_parallel_chapters: 3`, but the real config keys are `max_event_files` / `max_extraction_group` (`config.yaml`); `max_parallel_chapters` is not a real key. The doc is internally inconsistent (uses the right key elsewhere). Pick the real key.
- `features/external-maps/README.md`: config keys `require_license`, `allowed_licenses`, `download_images`, `storage_path` are read via `.get()` defaults but are NOT present in the actual `config.yaml` `external_maps` block (only `enabled: false`). Confirm whether these are supported-but-undocumented-in-config or doc-only.
- `features/equipment/README.md`: states `SCHEMA_VERSION = 2.15`; equipment is actually 2.25 (global per-entity map). Verify + correct to the live per-entity version.
- `core/API_REFERENCE.md` / logistics: documents `extract_logistics` while the live symbol is `extract_logistics_from_event`. Confirm the public name.
- `features/people_groups/GROUP_DEDUP_RULES_REVIEW.md`: §2 "byte-identical" auto-merge contradicts a later "[FIXED] canonical-key auto-merge"; the live symbol is `_auto_merge_exact_duplicates`. Reconcile the doc's two statements.
- `features/supplemental/SUPPLEMENTAL_COMPLETE.md`: references the obsolete `output/supplemental/.materials[]` testing layout + an `output_dir` kwarg (live is `output_root`). Verify + update.

## Dated snapshot numbers (not code claims — confirm cadence, not correctness)
These are legitimately point-in-time and the docs flag them as drifting; listed so a human knows
they are NOT code-verifiable and may be stale:
- `DATA_QUALITY_STATUS.md`: entity counts / enrichment rates (dated snapshot).
- `STORAGE_REVIEW.md`: cost/byte figures ("S3 $77.54", "45,273 objects").
- `INDEX.md`: "SCHEMA_REFERENCE.md (v2.23)" label drift (target now per-entity).

## Readability polish (recommended, lower-impact — not yet applied)
From the 2026-10-11 consolidation+readability review. The corpus was found UNDER-INDEXED, not
under-consolidated — so the fix is discoverability, not merging (equipment/ and people/ clusters are
right-sized; do NOT merge them into mega-docs). Applied already: external-maps/README broken links
repointed; DATA_QUALITY_SAFEGUARDS §9 design-spec hub table; INDEX safeguards-hub + ingestion
"which to read when" orientation. Still open:
- `features/README.md`: stale date (2026-06-13) + a duplicated "Key Features" block under Logistics; refresh + dedupe.
- `features/maps/README.md`: add a one-line "source-maps vs map-interior features" note cross-linking `MAP_FEATURES_SCHEMA.md` (readers conflate the two).
- `features/external-maps/README.md`: drop the "Version 2.0.0 / AI-Ready" version theatre; add a one-line what/when-to-read orientation.
- Changelog-style doc titles (e.g. `features/supplemental/SUPPLEMENTAL_VALIDATION.md` "# Validation Added / ## Changes Made") → convert to topic titles with a one-line what/when-to-read header, matching the good pattern in casualties/weather/source_section READMEs.
- `INDEX.md` Features section links only a couple of the 13 entity READMEs → either list all 13 or make the "see features/README.md" pointer prominent.
