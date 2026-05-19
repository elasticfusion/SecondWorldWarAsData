# TODO & Known Issues

**Last Updated:** 2026-05-11

---

## Active

### OpenSERP web search from AWS
**Priority:** — | **Status:** Won't Do

Search engines block AWS datacenter IPs. Rate limiting and fallback engines don't resolve the fundamental issue. OpenSERP web search requires residential IPs or a paid SERP API. Pipeline enrichment works without it (Grok bio, NARA, Archive.org all functional).

### Batch mode for optional extractors
**Priority:** Medium | **Type:** Enhancement

Optional extractors (casualties, weather, equipment, logistics, supplemental) use real-time API calls even in `--batch` mode. Fix: add second batch collect→submit→poll cycle.

### Re-extract entities with updated prompts
**Priority:** Medium | **Type:** Enhancement

After cache clear, re-run with prompts requesting: places `original_text`/`role_in_event`, people `position_at_event`, groups `context`.

---

## Phase 2 Stabilization

### Prevent duplicate task launches
**Priority:** High

Race condition: two Lambda invocations both clear a stale lock and launch duplicate tasks/batch jobs. Fix: use DynamoDB conditional write with task ARN, or add random jitter before stale lock check.

### Auto-delete poisoned cache entries
**Priority:** Medium

When a cached Grok response fails JSON validation, delete the cache entry and retry fresh instead of failing all attempts.

### Ensure dedup always runs
**Priority:** Medium

Wrap `_run_dedup_detection` in retry with explicit error logging. If it fails, don't send completion notification.

### SQS deduplication
**Priority:** Medium

Multiple S3 uploads generate multiple SQS messages causing duplicate Lambda invocations. Fix: content-based deduplication or batching window.

### Detect cancelled/stuck batch jobs
**Priority:** Medium

If batch state is `cancelled` or progress is 0/0/0 after first poll, stop monitoring and exit.

### Batch mode should retry empty event files
**Priority:** Medium

In batch mode, empty event files (0 sub-events) from previous failed runs are never retried. The retry step only works in real-time mode. Fix: after batch results are applied, check for remaining empty event files and submit them as a follow-up batch or real-time retry.

### Proper progress counter for Phase 2
**Priority:** Low

Add per-entity-type heartbeat pings so progress is visible mid-chapter. Log "Chapter X: 6/10 entity types complete" and update heartbeat after each extractor finishes. Eliminates misleading "no progress for N minutes" warnings during long sequential extraction.

---

## Known Issues

### Spurious "short response" warnings for NOT_FOUND sentinel
**Severity:** Low

`_log_api_response` warns on any response under 200 chars that isn't valid JSON. This triggers on expected `NOT_FOUND` responses from `search_llm`. Fix: exclude known sentinel values (`NOT_FOUND`, `[]`, `{}`) from the short-response warning.

### Search engines block AWS datacenter IPs
**Severity:** High

Google/Bing/DuckDuckGo return 503 or empty from Fargate. Options: residential proxy, or accept rate-limited Bing/DuckDuckGo only.

### Military units misclassified as places
**Severity:** Medium

Phase 2 sometimes puts military units in `output/places/`. Auto-reclassify script and dedup UI reclassify button available.

### 548 stub files missing primary IDs
**Severity:** Low

107 people, 228 places, 183 groups, 29 dates have only `event_mentions` — no ID field. Should be cleaned up or regenerated.

### NARA Catalog API returning HTML
**Severity:** Low

NARA API returns HTML instead of JSON. Reported to Catalog_API@nara.gov. Grok Record Group identification works as fallback.

### Grok fast model appends commentary after JSON
**Severity:** Low

Retry logic and JSON repair handle most cases.

---

## Backlog

### Periodic re-search of not_found entities
Add config `enrichment.re_search_after_days: 90` to retry entities after threshold.

### True batch submission for OpenSERP verification
Collect candidates → submit as Grok batch → apply results.

### Configurable OpenSERP search depth
Config: `results_per_query`, `max_images_per_entity`, `max_web_results_per_entity`, `rate_limit_seconds`.

### Propagate source URL into notes-event files
**Priority:** Medium

`*-notes-event.json` factual items track back to the original event (`source_EventID`, `source_Sub-eventID`) but don't include the internet URL where the endnote text was fetched from (e.g., ibiblio). The URL exists in `*-endnotes.json` and is used during fetching but isn't carried through to the output. Fix: add `source_url` to `source_reference` stanza in `_write_notes_event`.

### Grok verification of retrieved/generated supplemental URLs and references
**Priority:** Medium

**Phase 2:** The extraction prompt asks Grok to populate `resource_urls`, `archive_reference_number`, and `archive_physical_address` from its own knowledge — these are unverified and may be hallucinated. Fix: treat Grok-generated URLs/archive refs as candidates, validate in Phase 3.

**Phase 3:** After finding a URL via search (Archive.org, Gutenberg, OpenSERP, or LLM), Grok should fetch the page content and verify it actually matches the citation before storing it. Currently:
- `search_llm` — Grok generates URLs from memory (hallucination risk, no validation)
- `search_archive_org` — matches by title string only, no content verification
- `search_gutenberg_openserp` — returns first matching URL, no content check
- `search_openserp` — returns first result, no relevance check
- URL validation only checks HTTP 200, not content relevance

Fix: after finding a URL, fetch first ~2000 chars of page content and ask Grok to confirm it matches the cited title/author/document. Reject mismatches.

### Unmatched combinable people files
Dedup now detects title/alias matches with nationality guard. Remaining: use Grok to verify ambiguous title-to-person matches, providing event date context (e.g., "Is 'Supreme Commander' in June 1944 Normandy the same as 'Dwight D. Eisenhower'?"). Titles change holders over time.

### Phase 4: Document Acquisition & Processing
Download digitized sources, OCR, feed back through pipeline. Spec: `docs/current/PHASE4_SPEC.md`.

### Auto-delete poisoned cache entries
**Priority:** Medium

When a cached Grok response fails JSON validation, delete the cache entry and retry fresh instead of failing all attempts against the same bad data.

### Meaningful batch job names
**Priority:** Low

Batch names should include book, scope, and request count:
- Single chapter: `phase2-TheLorraineCampaign-ch7-12reqs`
- Multiple chapters: `phase2-TheLorraineCampaign-6chapters-156reqs`
- Whole book: `phase2-TheLorraineCampaign-45files-892reqs`
- Phase 3: `phase3-enrich-333reqs`

### Detect cancelled/stuck batch jobs during polling
**Priority:** Medium

When polling batch status, if state is `cancelled` or progress is 0/0/0 (no pending) after first poll, stop monitoring and exit. Prevents tasks from waiting 24h on dead batches. Also fix the duplicate task race condition — two Lambda invocations can both clear a stale lock and launch duplicate tasks.

### UK National Archives (Discovery API)
British military records integration.

### ~~Windows PowerShell scripts~~
**Status:** Won't Do

### Move find_duplicates scoring to src/dedup/scoring.py

---

## Completed

### 2026-05-11
- ✅ BatchModeCollecting fix
- ✅ Final sync fix (Phase 3 uploads all files)
- ✅ OpenSERP endpoint fix (`POST /search` → `GET /mega/search`)
- ✅ OpenSERP `serve` command in task definition
- ✅ NAT teardown via SNS (immediate on completion)
- ✅ LOC removed from search chain
- ✅ Schema versioning (all 11 entity types)
- ✅ NOAA weather enrichment module
- ✅ IAM least privilege audit
- ✅ ALB fully removed
- ✅ Port 443 SG rule for ECR pulls
- ✅ Bibliography verbatim fix
- ✅ OpenSERP scales to 0 on completion
- ✅ `openserp_searched` race condition fix

### 2026-05-06
- ✅ NARA Record Group identification via Grok
- ✅ External search cache (positive 30d / negative 7d)
- ✅ `enrichment_status` tracking
- ✅ Idle monitor rewritten

### 2026-05-01 – 2026-05-03
- ✅ Dedup exclusions → DynamoDB
- ✅ Casualties spec rewritten
- ✅ Output directory reorganized
- ✅ NAT Gateway dynamic lifecycle

### Earlier
- ✅ xAI Batch API (50% cost reduction)
- ✅ Event extraction token optimization (~72% reduction)
- ✅ Cross-reference consistency fixes
- ✅ Rate limiter, retry logic, ULID fixing
