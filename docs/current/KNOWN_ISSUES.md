# Known Issues

**Last Updated:** 2026-04-30

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

## Reorganize output directory structure

**Date:** 2026-04-28
**Status:** Planned
**Severity:** Low

Move book output directories under `output/content/` to separate parsed/event files from entity directories:

```
output/
├── content/                    # book-specific output
│   ├── BreakoutAndPursuit/
│   ├── CrossChannelAttack/
│   └── TheLorraineCampaign/
├── people/                     # entity directories (unchanged)
├── places/
├── people_groups/
├── equipment/
└── ...
```

**Requires:**
- Update `paths.output_root` or add `paths.content_output` in config.yaml
- Update phase1_parse.py, phase2_extract.py, phase3_enrich_data.py
- Update all extraction modules referencing output_root for book dirs
- Update S3 notification prefixes in CloudFormation
- Update ecs_entrypoint.py sync logic
- Migrate existing S3 data with a script
- Update import scripts and dedup scripts

---

## Resolved Issues

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
