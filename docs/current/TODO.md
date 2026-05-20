# Pipeline Backlog

**Last Updated:** 2026-05-19

---

## High Priority

### Prevent duplicate task launches
Race condition: two Lambda invocations both clear a stale lock and launch duplicate tasks/batch jobs. Fix: use DynamoDB conditional write with task ARN, or add random jitter before stale lock check.

### Phase 2 completion re-triggers itself
Final sync or background sync re-uploads `-parsed.json` files to S3, firing the S3 notification that triggers another Phase 2 run. The `_downloaded_keys` skip set or exclude patterns aren't preventing this. Fix: ensure parsed/event files are never re-uploaded, or add a guard in the trigger Lambda to ignore files that already have corresponding event files.

### Optimize ECS S3 download strategy
ECS container downloads entire entity directories on startup, getting slower with each new book. Fix: lazy-load entity files on demand via S3-backed file accessor that downloads on first read and caches locally. Scope Phase 3 downloads to only the book being processed.

### Grok verification of supplemental URLs and references
After finding a URL via search, fetch first ~2000 chars and ask Grok to confirm it matches the cited title/author/document. Reject mismatches. Applies to: `search_llm`, `search_archive_org`, `search_gutenberg_openserp`, `search_openserp`.

---

## Medium Priority

### Batch mode for optional extractors
Optional extractors (casualties, weather, equipment, logistics, supplemental) use real-time API calls even in `--batch` mode. Fix: add second batch collect→submit→poll cycle.

### Re-extract entities with updated prompts
After cache clear, re-run with prompts requesting: places `original_text`/`role_in_event`, people `position_at_event`, groups `context`.

### Auto-delete poisoned cache entries
When a cached Grok response fails JSON validation, delete the cache entry and retry fresh instead of failing all attempts against the same bad data.

### Ensure dedup always runs
Wrap `_run_dedup_detection` in retry with explicit error logging. If it fails, don't send completion notification.

### SQS deduplication
Multiple S3 uploads generate multiple SQS messages causing duplicate Lambda invocations. Fix: content-based deduplication or batching window.

### Batch mode should retry empty event files
In batch mode, empty event files (0 sub-events) from previous failed runs are never retried. Fix: after batch results are applied, check for remaining empty event files and submit them as a follow-up batch or real-time retry.

### Propagate source URL into notes-event files
`*-notes-event.json` factual items don't include the internet URL where the endnote text was fetched from. Fix: add `source_url` to `source_reference` stanza in `_write_notes_event`.

### Unmatched combinable people files
Use Grok to verify ambiguous title-to-person matches with event date context (e.g., "Is 'Supreme Commander' in June 1944 Normandy the same as 'Dwight D. Eisenhower'?").

---

## Low Priority

### Proper progress counter for Phase 2
Add per-entity-type heartbeat pings so progress is visible mid-chapter. Eliminates misleading "no progress for N minutes" warnings.

### Meaningful batch job names
Include book, scope, and request count: `phase2-TheLorraineCampaign-ch7-12reqs`.

### Periodic re-search of not_found entities
Add config `enrichment.re_search_after_days: 90` to retry entities after threshold.

### Move find_duplicates scoring to src/dedup/scoring.py

### True batch submission for OpenSERP verification
Collect candidates → submit as Grok batch → apply results.

### Detect cancelled/stuck batch jobs
If batch state is `cancelled` or stuck at 0/0/0, mark as failed and stop polling. Low cost now (just Lambda invocations) but clutters the job queue.

---

## Future / Research

### Grok function calling for Phase 3 enrichment
Use Grok 4.3 function calling to let the model orchestrate search/verification tools (NARA, Archive.org, Wikipedia) directly during enrichment, replacing multi-step Python orchestration. Blocked on: function calling support in batch API (currently real-time only).

### Phase 4: Document Acquisition & Processing
Download digitized sources, OCR, feed back through pipeline. Spec: `docs/current/PHASE4_SPEC.md`.

### UK National Archives (Discovery API)
British military records integration.

---

## Known Issues

| Issue | Severity | Notes |
|-------|----------|-------|
| Search engines block AWS datacenter IPs | High | Residential proxy or paid SERP API needed. Pipeline works without OpenSERP. |
| Military units misclassified as places | Medium | Auto-reclassify script and dedup UI button available. |
| 548 stub files missing primary IDs | Low | 107 people, 228 places, 183 groups, 29 dates. Clean up or regenerate. |
| NARA Catalog API returning HTML | Low | Reported to NARA. Grok Record Group ID works as fallback. |
| Grok fast model appends commentary after JSON | Low | Retry logic and JSON repair handle most cases. |
| Spurious "short response" warnings for NOT_FOUND | Low | Exclude sentinel values from warning. |

---

## Won't Do (Archived)

- ~~OpenSERP web search from AWS~~ — Search engines block AWS datacenter IPs. Residential IPs or paid SERP API required.
- ~~Windows PowerShell scripts~~ — Not needed.

---

## Completed

### 2026-05-19
- ✅ Batch infrastructure optimization (submit-only/retrieve-only ECS modes, Lambda batch poller, DynamoDB job queue, infra teardown/spin-up)

### 2026-05-11
- ✅ BatchModeCollecting fix
- ✅ Final sync fix (Phase 3 uploads all files)
- ✅ OpenSERP endpoint fix
- ✅ NAT teardown via SNS
- ✅ LOC removed from search chain
- ✅ Schema versioning (all 11 entity types)
- ✅ NOAA weather enrichment module
- ✅ IAM least privilege audit
- ✅ ALB fully removed
- ✅ Bibliography verbatim fix
- ✅ OpenSERP scales to 0 on completion

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
