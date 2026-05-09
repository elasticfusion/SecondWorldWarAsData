# Known Issues

**Last Updated:** 2026-05-04

## Open Issues

### Military units misclassified as places

**Date:** 2026-04-23
**Status:** Partial fix available
**Severity:** Medium

Phase 2 extraction sometimes classifies military units (e.g., "17th SS Panzer Grenadier Division", "1st SS Panzer Division", "12th Army Group headquarters") as places instead of people_groups. These end up in `output/places/` instead of `output/people_groups/`.

**Impact:**
- Inflates place count with non-geographic entities
- Missing from people_groups dedup detection
- Won't be enriched correctly in Phase 3

**Mitigations in place:**
- Extraction prompts now explicitly instruct Grok to exclude military units from people and places
- Prompt templates in `prompts/people.yaml` and `prompts/places.yaml` include exclusion rules
- The Dedup Review UI has a **Reclassify** feature (↗ button) to move misclassified entities between categories with automatic schema transformation

**Remaining work:**
- Add post-extraction validation step that auto-detects military unit patterns and reclassifies
- Add blocklist of military unit patterns (division, corps, army, regiment, battalion, brigade)

**Examples from output/places/:**
- `12th_army_group_headquarters.json`
- `17th_ss_panzer_grenadier_division.json`
- `1st_ss_panzer_division.json`
- `2nd_panzer_division.json`

## Grok fast model appends commentary after JSON

**Date:** 2026-04-26
**Status:** Open
**Severity:** Low

The `grok-4-1-fast-non-reasoning` model sometimes appends explanatory text after valid JSON output, causing "Extra data" parse errors. The existing JSON repair logic in `GrokClient` handles most cases, but `batch_parallel` extraction may not use it consistently.

**Impact:**
- Some entity extractions fail on first attempt but succeed on retry
- Places and dates extraction most affected

**Mitigation:**
- Retry logic (3 attempts) handles most cases
- JSON repair in `GrokClient.extract_json()` strips trailing text

### OpenSERP image search for people and equipment

**Date:** 2026-04-28
**Status:** Planned
**Severity:** Low (enhancement)

Phase 3 enrichment should use OpenSERP to find images of people (portraits) and equipment (photos) during enrichment. Currently Phase 3 only searches Grokipedia and Wikipedia for text data. OpenSERP is already available for Phase 2 external maps but not wired into Phase 3.

**Requires:**
- Add OpenSERP image search to `enrich_biographies.py` and equipment enrichment
- Image validation (vision verification, similar to external maps)
- Store image URLs/metadata in entity JSON (`images` field)
- Config option to enable/disable (`enrichment.image_search: true`)
- OpenSERP service must be running during Phase 3

### OpenSERP academic and media search for biographies

**Date:** 2026-04-28
**Status:** Planned
**Severity:** Low (enhancement)

Use OpenSERP during Phase 3 biography enrichment to find academic papers, oral histories, video interviews, and university archive materials related to people. Results would be LLM-verified for relevance and stored as enrichment links.

**Search targets:**
- University digital archives and repositories
- Oral history collections (e.g., Library of Congress Veterans History Project)
- Academic papers and dissertations
- Documentary footage and video interviews
- Museum collections and exhibits

**Requires:**
- Add OpenSERP search to `enrich_biographies.py` with targeted queries (e.g., `"{name}" oral history WWII`, `"{name}" university archive`)
- LLM verification of result relevance (similar to external maps vision verification)
- Store as `academic_references` or `media_references` in person JSON
- Domain allowlist for trusted academic sources
- Config option to enable/disable (`enrichment.academic_search: true`)
- OpenSERP service must be running during Phase 3

### OpenSERP event-related content search

**Date:** 2026-04-28
**Status:** Planned
**Severity:** Low (enhancement)

Use OpenSERP to find primary source material related to specific events — veteran interviews, oral histories, documentary footage, academic papers, and archival content. Searches should include non-English material (French, German, Dutch, Polish, etc.) to capture perspectives from all sides and affected populations.

**Search targets:**
- Veteran interviews and oral histories (written and video)
- Documentary footage and newsreels
- Academic papers and battle analyses
- National archives (US, UK, French, German, etc.)
- Museum exhibits and memorial sites
- Non-English language sources (search in native language + English)

**Examples:**
- `"Normandy landings" veteran interview`
- `"bataille des Ardennes" témoignage` (Battle of the Bulge testimony, French)
- `"Ardennenoffensive" Zeitzeuge` (Ardennes offensive eyewitness, German)
- `"Operation Market Garden" oral history`

**Requires:**
- Add event enrichment step to Phase 3 or as a new Phase 3b
- Search by event name + aliases in multiple languages
- LLM verification of relevance and content classification (interview, paper, video, archive)
- Store as `external_references` in event JSON with language tag
- Language-aware search queries generated from event metadata
- Config option to enable/disable (`enrichment.event_content_search: true`)
- OpenSERP service must be running

---

## Resolved Issues

### Infinite retry loop for short Grok responses

**Date:** 2026-05-04
**Status:** Resolved
**Severity:** High

`extract_json` retried indefinitely when Grok returned short non-JSON responses (14 chars, typically "I don't know"). The `_retried` flag wasn't checked in the `JSONDecodeError` handler, causing hundreds of wasted API calls per unfindable person.

**Fix:** Added `_retried` check to the `JSONDecodeError` path. Max 2 attempts in `extract_json`, plus 3 attempts in the enrichment caller = 6 total max per entity.

### Enrichment re-searches entities every run

**Date:** 2026-05-04
**Status:** Resolved
**Severity:** Medium

People, groups, and places without external data were re-searched on every Phase 3 run, wasting API calls on entities Grok can't find.

**Fix:** Added `enrichment_status` field (`enriched`/`not_found`) to people, groups, and places. Bibliography uses `search_status`. Entities marked `not_found` are skipped on future runs.

### Dedup exclusions lost between AWS runs

**Date:** 2026-05-02
**Status:** Resolved
**Severity:** High

"Not Duplicates" decisions were stored in S3 JSON files that weren't consistently downloaded by ECS containers. Exclusions were lost or stale across runs.

**Fix:** Moved exclusions to DynamoDB (`exclusion#{entity_type}#{file1}#{file2}`). Shared `src/dedup/exclusions.py` module used by both ECS and Lambda. One-time migration from local JSON on first run.

### Dedup high-frequency gate suppressing valid matches

**Date:** 2026-05-02
**Status:** Resolved
**Severity:** High

People with >15 event mentions (Eisenhower, Bradley, Patton) were excluded from dedup matching unless they had bio/proximity evidence — which isn't available pre-enrichment.

**Fix:** Removed the high-frequency gate entirely. All people scored identically regardless of mention count.

### NAT Gateway race condition creating duplicates

**Date:** 2026-05-03
**Status:** Resolved
**Severity:** Medium

Multiple concurrent trigger Lambda invocations each created a NAT Gateway, resulting in 2-4 NATs and orphaned EIPs.

**Fix:** Added DynamoDB lock (`lock#nat-manager`) to `nat_manager.py`. Second instance waits for NAT to appear instead of creating another.

### Idle monitor deleting ALB managed by CloudFormation

**Date:** 2026-05-03
**Status:** Resolved
**Severity:** High

The idle monitor deleted the ALB to save costs, but CloudFormation expected it to exist. Subsequent deploys failed with "load balancer not found."

**Fix:** Removed ALB deletion from idle monitor. ALB stays managed by CloudFormation (~$16/month idle). NAT is still torn down dynamically.

### Phase 3 only downloading manifest files instead of full entity dirs

**Date:** 2026-05-03
**Status:** Resolved
**Severity:** High

Phase 3 used the DynamoDB manifest for incremental download, but the manifest only contained files from the current Phase 2 run. Groups, places, and bibliography directories were empty.

**Fix:** Phase 3 now downloads all entity directories (using skip-existing optimization). Manifest-only download removed for Phase 3.

### Output directory reorganization

**Date:** 2026-05-01
**Status:** Resolved
**Severity:** Low

Moved book output directories under `output/content/` to separate parsed/event files from entity directories. Backwards compatible — `get_content_root()` auto-detects old layout. Migration script: `python3 scripts/migrate_output_content.py`.

### Casualties schema included equipment and weather

**Date:** 2026-05-01
**Status:** Resolved
**Severity:** Medium

Casualties spec, schema, prompt, and extraction code included `impacted_equipment` and `weather_conditions` fields. Casualties are about people — equipment losses belong in the Equipment entity.

**Fix:** Removed `impacted_equipment`, `weather_conditions`, and related resolver functions. Added `side` field (allied/axis/civilian/unknown). Updated prompt to explicitly say "people only."

### Endnote cross-reference resolution incomplete

**Date:** 2026-05-01
**Status:** Resolved
**Severity:** Medium

`fetch_endnotes.py` only resolved "cited in n. X, above" cross-references (4 matches across all books). Patterns "cited n. X" (47 matches) and "see n. X" (13 matches) were not resolved.

**Fix:** Expanded regex to handle all three patterns. 64 cross-references now resolved vs 4 previously.

### Cross-book dedup detection missing in AWS mode

**Date:** 2026-05-01
**Status:** Resolved
**Severity:** High

Dedup scripts in AWS mode only found duplicates within the current run, not across books. Two causes: (1) event files from previous books weren't downloaded, so text proximity signals couldn't fire; (2) the high-frequency gate suppressed name-based matches for entities with >15 event mentions (e.g., Eisenhower, Bradley) unless text proximity or bio data was present.

**Fix:** ECS dedup detection now downloads `output/content/` (all event files) for text proximity. High-frequency gate now allows substring matches, unicode variants, and name similarity ≥0.85 through without requiring bio/proximity evidence.

### Lambda cannot stop rogue ECS tasks

**Date:** 2026-05-01
**Status:** Resolved
**Severity:** Medium

When dedup-complete triggered Phase 3, the Lambda tried to stop any running Phase 2 task but failed with `AccessDeniedException: ecs:StopTask not allowed`.

**Fix:** Added `ecs:StopTask` to the Lambda IAM role in `cloudformation/iam.yaml`.

### S3 notification prefix too broad (feedback loop)

**Date:** 2026-05-01
**Status:** Resolved
**Severity:** High

S3 notifications for `-parsed.json` and `-event.json` used prefix `output/`, which matched entity file uploads and re-triggered the pipeline. This was a recurrence of the April 28 feedback loop fix — the CloudFormation template hadn't been updated.

**Fix:** Changed S3 notification prefix to `output/content/` in `cloudformation/events.yaml`. Entity uploads to `output/people/` etc. no longer trigger pipeline re-runs.

### Feedback loop: Phase 2/3 re-triggering pipeline

**Date:** 2026-04-28
**Status:** Resolved
**Severity:** High

Phase 2's dedup report sync uploaded the entire `output/` directory back to S3, including `-parsed.json` and `-event.json` files. This triggered S3 notifications that re-launched Phase 2 in an infinite loop.

**Fix:** Dedup report sync now only uploads `duplicate_report.json` files. Phase 2/3 final sync only uploads entity subdirectories, excluding book directories. Background sync excludes `-parsed.json` and `-event.json` patterns.

### Dedup exclusion persistence across sessions

**Date:** 2026-04-28
**Status:** Resolved
**Severity:** Medium

"Not Duplicates" decisions in the dedup UI were not persisted across pipeline runs because the places and groups dedup scripts did not read exclusion files.

**Fix:** All four dedup scripts (`find_duplicate_people.py`, `find_duplicate_places_v2.py`, `find_duplicate_groups.py`, `find_duplicate_equipment.py`) now read their respective exclusion files (`not_duplicates.json` / `not_related.json`) and filter out excluded pairs.

### Phase 3 launched without dedup review

**Date:** 2026-04-28
**Status:** Resolved
**Severity:** High

Phase 3 launched automatically after Phase 2 because `dedup/review_status.json` retained `complete: true` from a previous run.

**Fix:** Phase 1 now resets `dedup/review_status.json` to `complete: false` at the start of every pipeline run, ensuring Phase 3 always requires explicit approval.

### Batch API polling exits before all requests complete

**Date:** 2026-04-30
**Status:** Resolved
**Severity:** High

`poll_batch()` checked `pending == 0` to determine completion, but the xAI API can report 0 pending while requests are still in-progress (neither pending nor complete). This caused Phase 2 and Phase 3 to finish before all batch requests completed, losing extracted entities.

**Fix:** Changed completion check to `success + error >= total`. Added retry logic for transient HTTP errors (5 attempts with 60s backoff) and a 24-hour max poll timeout matching the xAI Batch API SLA.

### Equipment dedup script crashes on exclusion file format

**Date:** 2026-04-30
**Status:** Resolved
**Severity:** Medium

`find_duplicate_equipment.py` expected `file1`/`file2` keys in `not_duplicates.json` but the dedup UI writes `person1`/`person2` for all entity types.

**Fix:** Updated `_load_exclusions` to accept both key formats.

### S3 download crashes on deleted files

**Date:** 2026-04-30
**Status:** Resolved
**Severity:** Medium

`_download_s3_file` crashed with a 404 when a file was listed by S3 pagination but deleted before download (e.g., by a concurrent dedup merge).

**Fix:** Added `ClientError` handling for 404/NoSuchKey — logs at debug level and skips the file.

### Phase 3 single-threaded enrichment

**Date:** 2026-04-30
**Status:** Resolved
**Severity:** Medium

Phase 3 enrichment processed entities sequentially — one person/group/place at a time.

**Fix:** Added `ThreadPoolExecutor` with configurable `max_enrichment_workers` (default 6) in `config.yaml`. Grok API calls are still rate-limited at 30/min by the existing thread-safe rate limiter; extra threads keep search requests (Grokipedia/Wikipedia) running in parallel.
