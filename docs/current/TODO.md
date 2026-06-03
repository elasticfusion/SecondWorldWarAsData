# Pipeline Backlog

**Last Updated:** 2026-06-03

---

## High Priority

### Production Reliability (observed failures in E2E testing)

#### Phase 4: DynamoDB as primary entity storage
Eliminates the 15-30 min S3 download phase, prevents data loss on spot termination (writes are immediately durable), and enables incremental processing (query only unenriched entities). Current architecture loses all enrichment work on spot kill because background sync hasn't uploaded yet. With DynamoDB: zero data loss, instant restart from where it left off. Spec: `docs/current/PHASE4_SPEC.md`. Existing `import_to_dynamodb.py` provides the schema foundation.

**Strategy: Dual-write (DynamoDB + S3).** DynamoDB is source of truth for operational reads/writes (fast, durable, queryable). S3 remains as archival/bulk export (browsable JSON, versioned, cheap). Write DynamoDB first (immediate durability), periodic S3 export on phase completion for human review and backup.
*Source: end-2-end-1 spot termination data loss*

#### Investigate why pending queues persist without triggering action
`pending#content` and `pending#parsed` entries remained in DynamoDB for a week without any service consuming them. Either the trigger Lambda isn't being invoked (SQS→Lambda path broken after idle period), or the hourly scheduled check doesn't process pending queues.
*Source: end-2-end-1 observation*

#### Validate dedup exclusion lists are working (places, groups, equipment)
Entities previously marked as not-duplicates may be reappearing in the dedup review queue. Verify that DynamoDB-backed exclusions are being loaded and checked correctly in `find_duplicate_places_v2.py`, `find_duplicate_groups.py`, and `find_duplicate_equipment.py`.
*Source: end-2-end-1 dedup review*

#### Fix OpenSERP null response crash in equipment photo search
`openserp_enrichment.py` calls `.get()` on `None` when OpenSERP returns empty/malformed results for image searches. Add null guard before accessing response fields. Causes repeated warnings and wasted time in Phase 3.
*Source: end-2-end-1 Phase 3 logs*

#### Add circuit breaker for OpenSERP image search
After N consecutive failures (e.g., 5), skip remaining OpenSERP searches for the current run. Mark failed entities with `openserp_search_failed_at` timestamp. Only retry entities whose failure is older than 90 days. Prevents wasting 5s × hundreds of entities on a broken/unavailable OpenSERP service.
*Source: end-2-end-1 Phase 3 logs*

#### Add "Phase started" notifications
No email when ECS tasks launch. Add `_notify_launch()` at end of `_run_task()` in trigger_handler.py. Currently only get notifications on completion/failure — operator blind to whether pipeline is running.
*Source: end-2-end-0.md Issue 1*

---

## Medium Priority

### Data Quality

#### Fix casualties entity_context — pass full org name→GroupID mapping
`casualties.py` line 146 passes only first 10 org names with no IDs. LLM has nothing to copy. Pass all `name: GroupID` pairs. Re-run casualties extraction after fix.
*Source: end-2-end-0-ds.md*

#### Fix weather _build_places_section() passing MentionIDs instead of PlaceIDs
Weather extraction receives `PlaceMentionID` values (per-mention) instead of top-level `PlaceID` from consolidated place files. 31% of weather PlaceIDs don't resolve.
*Source: end-2-end-0-ds.md*

#### Add "COPY — do NOT generate" pattern to all cross-reference prompts
All prompts passing available entity IDs should explicitly say "COPY these IDs exactly — do NOT generate new ones". Single highest-impact prompt change across all entity types.
*Source: PROMPT_REVIEW.md*

#### Add few-shot examples to logistics prompt for severity calibration
Severity calibration text alone had no effect (still 78% high/critical). Add 3-4 examples or "Default to medium unless text explicitly says operations halted/postponed/impossible."
*Source: end-2-end-0-ds.md*

#### Align logistics enum values between prompt and output
Prompt defines different type/status values than output. Standardize types and status enums.
*Source: PROMPT_REVIEW.md*

#### Add count qualifier pattern to casualties
Output uses `{"value": 500, "qualifier": "approximately"}` but prompt schema shows raw integers. Align to output format.
*Source: PROMPT_REVIEW.md*

#### Clean up 105 legacy people files missing name/PersonID
Script to populate from filename + generate missing PersonIDs.
*Source: end-2-end-0-ds.md*

### Dedup & Normalization

#### Auto-merge exact duplicates without human review
When two entity files have identical normalized names (case-insensitive, after ASCII-folding and punctuation stripping), merge automatically. Only flag fuzzy/partial matches for human review.
*Source: end-2-end-1 observation*

#### Reduce false-positive place matches on common prefixes/suffixes
Exclude common geographic prefixes/suffixes ("Bois de...", "...River", "Fort...", "Hill...") from fuzzy matching, or require coordinate proximity as a second signal.
*Source: end-2-end-1 dedup review*

#### Equipment dedup: reject matches when numeric prefix differs
"105 mm howitzer" and "155 mm howitzer" are not duplicates. Require exact numeric match.
*Source: end-2-end-1 dedup review*

#### Equipment dedup: require country of origin match
Different nations' equipment with similar names are distinct entities.
*Source: end-2-end-1 dedup review*

#### Normalize group index keys more aggressively
Strip "the/us/u.s.", remove branch names, collapse ordinals. Prevents "4th division" ≠ "4th infantry division" creating separate files.
*Source: DEDUP_ANALYSIS_ALL_ENTITIES.md*

#### Incremental dedup (only score new files vs corpus)
All dedup scripts load ALL files and score ALL pairs every run (O(n²)). Track `last_dedup_run` timestamp, only compare new files against existing.
*Source: DEDUP_ANALYSIS_ALL_ENTITIES.md*

#### Propagate coordinates to index-only place entries
Store coordinates in index so cross-book dedup can use distance matching.
*Source: DEDUP_ANALYSIS_ALL_ENTITIES.md*

#### Build equipment alias table
Map known equivalents: "sherman"→"m4 sherman", "panther"→"pzkpfw v panther", etc.
*Source: DEDUP_ANALYSIS_ALL_ENTITIES.md*

#### Normalize caliber formats in equipment extraction
`.50-caliber` ≠ `50 caliber` ≠ `12.7mm`. Strip leading dots, normalize mm format.
*Source: DEDUP_ANALYSIS_ALL_ENTITIES.md*

#### Track "reviewed but no action" state in dedup UI
Pairs reviewed without decision reappear next run. Add `reviewed_at` timestamp, suppress recently-reviewed pairs.
*Source: DEDUP_ANALYSIS.md*

#### Use technical_identifier as primary equipment index key
More stable than common_name. Fall back to common_name only when no technical ID exists.
*Source: DEDUP_ANALYSIS_ALL_ENTITIES.md*

### Pipeline Efficiency

#### Phase 3: separate search pass from Grok analysis pass for batching
Restructure into: Pass 1 (search, collect raw data) → Pass 2 (batch all Grok prompts) → Pass 3 (retrieve, write files). Enables 50% cost savings and eliminates per-entity round-trips.
*Source: end-2-end-1 observation*

#### Parallel S3 downloads in ecs_entrypoint
`s3_sync_down` downloads sequentially (~15 min for 7,600 files). Use ThreadPoolExecutor with 20 workers. Cuts to ~2 min.
*Source: PERFORMANCE_ENHANCEMENTS.md*

#### Phase 2 should read pending#parsed as input manifest
Phase 2 always falls through to S3 scan. Read the DynamoDB queue directly instead.
*Source: CODE_REVIEW agent analysis*

#### Increase API rate limit (test with 60 RPM)
Current 30 RPM is conservative. Rate limiter handles 429 backoff. Doubles throughput.
*Source: PERFORMANCE_ENHANCEMENTS.md*

#### Buffer entity file writes per chapter
Each entity mention triggers immediate file read+write. Buffer in memory, flush once at end. Reduces I/O from O(mentions) to O(unique_entities).
*Source: PERFORMANCE_ENHANCEMENTS.md*

#### Conditional S3 downloads (skip unchanged files)
Use `head_object` to check size/ETag before downloading. Skips 80-90% of downloads on Phase 3 re-runs.
*Source: PERFORMANCE_ENHANCEMENTS.md*

#### Fix _get_book_entity_files scoping (downloads everything)
Add `book`/`source_book` field to entity files during Phase 2, filter index by that field.
*Source: PIPELINE_REVIEW.md, DATA_FLOW_ANALYSIS.md*

#### Optional entity extraction: parallelize across event files
Weather, equipment, logistics, casualties, supplemental extracted sequentially. Could run in parallel.
*Source: PIPELINE_REVIEW.md*

#### Grok model tiering (use lighter models for simple tasks)
Add configurable `model_map` in config.yaml. 15-25% cost savings.
*Source: COST_OPTIMIZATION.md*

#### Reduce prompt fulltext for dates/places extraction
Sub-event summaries likely suffice. 10-15% cost savings.
*Source: COST_OPTIMIZATION.md*

#### Split large prompts into smaller batches for parallel processing
Reduces wall-clock time ~4x for large chapters and reduces truncation risk.
*Source: PERFORMANCE_ENHANCEMENTS.md*

#### Phase 3 retry: exclude enrichment_status "not_found" from unenriched count
Causes unnecessary retries that always find nothing.
*Source: PIPELINE_REVIEW.md*

### Infrastructure Fixes

#### Fix watchdog notification failure preventing self-termination
If `_notify_failure` raises, SIGTERM is never sent. Wrap in try/except before `os.kill`.
*Source: POST_FIX_REVIEW.md*

#### Fix `_stamp_file` non-atomic writes
Uses `write_text()` directly. Use `write_json_with_lock` or temp+replace.
*Source: POST_FIX_REVIEW.md*

#### SQS MessageRetentionPeriod too short (1hr)
Messages lost during outages. Increase to 4-14 days.
*Source: CODE_REVIEW.md*

#### Fix CloudWatch Alarms referencing wrong namespace
Alarms reference Lambda namespace for ECS tasks — non-functional monitoring.
*Source: CODE_REVIEW.md*

#### Fix EntityCreatedTopic S3 notification (never configured)
Phase 3 entity-created flow broken.
*Source: CODE_REVIEW.md*

#### Fix manifest read-modify-write race in trigger Lambda
Lost S3 keys when concurrent Lambda invocations modify manifest.
*Source: CODE_REVIEW.md*

#### Fix non-paginated DynamoDB cache clear
`DynamoCacheBackend.clear()` only processes first page.
*Source: QA_GAPS.md*

#### Move SecretsManager VPC endpoint to dynamic set
Always-on costs $14.60/month for ~5 calls per run.
*Source: COST_OPTIMIZATION.md*

#### Replace silent exception swallowing
Multiple `except Exception: pass` locations. Log at WARNING with context.
*Source: QA_GAPS.md*

### Notifications

#### Add "Enrichment started" notification from Phase 3 ECS task
Distinguishes "task launched" (before download) from "enrichment in progress" (real work starting).
*Source: end-2-end-1 observation*

#### Add "Batch submitted" notification from submit-only task
No confirmation that submission worked or how many requests.
*Source: end-2-end-0.md Issue 2b*

#### Add "still waiting" notification for long-running batches
Operator blind to stalled batches.
*Source: PIPELINE_REVIEW.md*

#### Phase 1 notification: include incremental vs full context
"Incremental: 3 new files" vs "Full re-parse: 47 files".
*Source: PIPELINE_REVIEW.md*

### Testing

#### Write tests for batch_parallel.py
Critical orchestration module (1007 lines) with zero tests.
*Source: QA_GAPS.md, CODE_REVIEW.md*

#### Write tests for dedup_ui_handler.py
User-facing Lambda (1141 lines) with zero tests.
*Source: QA_GAPS.md*

#### Add extraction-time validation
Validate required fields present, referenced IDs exist before writing entity files.
*Source: DATA_SCIENCE_RECOMMENDATIONS.md*

#### Validate all 11 entity types in CI
Currently only validates 3 of 11.
*Source: QA_GAPS.md*

#### Convert anti-pattern tests to proper pytest tests
3 test files with zero assertions.
*Source: QA_GAPS.md*

#### Add S3 mocking with moto for storage layer tests
*Source: QA_GAPS.md*

### CI/CD & DevOps

#### Unify schema versioning
`json_schemas.py` uses "1.0.0", output schemas use "2.3".
*Source: QA_GAPS.md*

#### Add `additionalProperties: false` to extraction schemas
LLM-hallucinated fields pass undetected.
*Source: QA_GAPS.md*

---

## Low Priority

### Code Quality

#### Fix scheduler variable scope in _schedule_delayed_teardown
Move `boto3.client("scheduler")` before the try block.
*Source: code review*

#### Dedup guard: also check recently completed batches
Add time-bounded check for status "complete" within last hour.
*Source: code review*

#### Extract shared patterns (retry, index, event_mention)
Deduplicate across dates.py, places.py, people_groups.py, batch_parallel.py.

#### Cache config.py load_config() result
Reads YAML from disk on every call. Add module-level caching.

#### Fix _build_date_id_lookup rebuilt 4x per chapter
Build once and pass to all extractors.

#### Fix prompt_loader str.format() breaking on JSON with braces
*Source: CODE_REVIEW.md*

#### Fix json_validator._fix_invalid_ulids mutating input
Validate shouldn't modify.
*Source: CODE_REVIEW.md*

#### Refactor ecs_entrypoint.py (1,499 lines)
Split into focused modules: `aws_networking.py`, `s3_sync.py`, `phase_runner.py`.

#### Refactor equipment.py (1,959 lines)
Split into extraction, enrichment, media handling, and dedup sub-modules.

#### Include entity counts in phase_complete structured log
Write results JSON from phase scripts, read in `_post_process`.

### Performance

#### Reduce image memory usage in equipment.py
Image + base64 = ~4x memory. 80MB for a 20MB image.
*Source: CODE_REVIEW.md*

#### Fix _lookup_by_place_id O(n) iteration in weather_central.py
Build a lookup dict instead.
*Source: CODE_REVIEW.md*

#### Implement circuit breaker for Grok API
Fast-fail after N consecutive failures.
*Source: QA_GAPS.md*

#### Add unbounded cache size limit with LRU eviction
*Source: QA_GAPS.md*

#### Fix ThreadPoolExecutor leak in grok_client
Creates new pool per API call. Use shared pool.

### Testing

#### Add golden file tests for extraction
Catches prompt/schema regressions.
*Source: CODE_REVIEW.md*

#### Add integration tests for Phase 3 enrichment pipeline
Mock external APIs, test merge logic.
*Source: QA_GAPS.md*

#### Add Lambda handler tests (remaining 7 handlers)
Use moto for DynamoDB/S3/Lambda mocking.
*Source: QA_GAPS.md*

#### Local end-to-end simulation test
Full pipeline with mocked Grok API (canned JSON responses).

### UI & UX

#### Dedup UI: rename person (edit name field + rename file)

#### Auto-merge equipment with case-insensitive exact name match

### DevOps

#### AWS cost quick wins
Container Insights level, S3 version expiration, Lambda memory reduction, single DynamoDB read for cache hits.
*Source: COST_OPTIMIZATION.md*

#### Scripts cleanup and categorization
60+ scripts, no categorization, duplicates.
*Source: DEVOPS_RECOMMENDATIONS.md*

#### CloudFormation drift detection
*Source: DEVOPS_RECOMMENDATIONS.md*

#### Dedup UI refactor (extract HTML to S3/CloudFront)
47KB Lambda with inline HTML.
*Source: DEVOPS_RECOMMENDATIONS.md*

#### Add secrets scanning to CI
detect-secrets or gitleaks.
*Source: QA_GAPS.md*

#### Fix EIP leak on NAT delete
$3.60/month per leaked EIP.

#### Evaluate residential proxy for OpenSERP searches
Search engines block AWS datacenter IPs. Route OpenSERP traffic through residential proxy service to enable image/web searches from ECS. Alternatives: paid SERP API (SerpAPI, ScaleSerp ~$50-100/mo) or restrict search to local-only runs.
*Source: end-2-end-1 Phase 3 observation*

#### Add disk space checks before writes
*Source: QA_GAPS.md*

#### Add backup before dedup merge operations
*Source: QA_GAPS.md*

#### Create operations runbook (RUNBOOK.md)
How to re-run failed phases, clear locks, handle dedup, debug batch failures.
*Source: project review*

### Data

#### Re-extract entities with updated prompts
Add `processing.reprocess_types` config option.

#### Create place entities for 248 unresolved weather locations

#### Source-anchored names (`identified_as` field)
Phase 3 enrichment field for canonical name identification.

#### Periodic re-search of not_found entities
`enrichment.re_search_after_days: 90`

---

## Future / Research

### Grok function calling for Phase 3 enrichment
Blocked on: function calling support in batch API.

### Phase 4: Document Acquisition & Processing
Spec: `docs/current/PHASE4_SPEC.md`.

### Migrate dedup to DynamoDB-backed reads
Enables incremental dedup, eliminates O(n²) scan.
*Source: end-2-end-1 observation*

### Concurrent Phase 2 and Phase 3 job submission
Multiple books/entity types in parallel. Requires DynamoDB as primary storage.
*Source: end-2-end-1 observation*

### UK National Archives (Discovery API)

### Step Functions pipeline orchestration
Replace SNS→SQS→Lambda→ECS with Step Functions.
*Source: DATA_FLOW_ANALYSIS.md*

### Queue-based distributed processing
*Source: FUTURE_ENHANCEMENTS.md*

### Append-only + merge distributed processing
*Source: FUTURE_ENHANCEMENTS.md*

### Data quality metrics dashboard
*Source: DATA_SCIENCE_RECOMMENDATIONS.md*

### Incremental enrichment strategy (prioritize by mention count)
*Source: DATA_SCIENCE_RECOMMENDATIONS.md*

### Weather dedup at extraction time
*Source: DATA_SCIENCE_RECOMMENDATIONS.md*

### Unify schema naming conventions
*Source: DATA_SCIENCE_RECOMMENDATIONS.md*

### Chapter partitioning for parallel extraction
*Source: FUTURE_ENHANCEMENTS.md*

### Separate batch jobs for new vs revised content
*Source: end-2-end-0.md Feature Request*

### Local-mode automated phase chaining
*Source: PIPELINE_REVIEW.md*

---

## Known Issues

| Issue | Severity | Notes |
|-------|----------|-------|
| Search engines block AWS datacenter IPs | High | Pipeline works without OpenSERP. |
| Military units misclassified as places | Medium | Auto-reclassify script available. |
| 548 stub files missing primary IDs | Low | Clean up or regenerate. |
| NARA Catalog API returning HTML | Low | Grok Record Group ID as fallback. |
| Grok fast model appends commentary after JSON | Low | Retry + JSON repair handle it. |
| Spurious "short response" warnings for NOT_FOUND | Low | Exclude sentinel values. |

---

## Won't Do (Archived)

- ~~OpenSERP web search from AWS~~ — Residential IPs required.
- ~~Windows PowerShell scripts~~ — Not needed.

---

## Completed

### 2026-06-03
- ✅ Fix missing `import os` in phase3_enrich_data.py (crashed after enrichment completed)
- ✅ Fix event mention race condition in dates.py, places.py, weather_central.py (locked_json)
- ✅ Fix locked_json threading bug (fcntl.flock is per-FD, not per-file — added per-file threading.Lock)
- ✅ Add concurrent event mention tests (test_event_mention_race.py)

### 2026-06-02
- ✅ Implement structured JSON logging for CloudWatch (JSONFormatter + ECS detection)
- ✅ Add batch submission/result detail logging (type breakdown + failed result extra_fields)
- ✅ Add pipeline phase transition and token usage logging
- ✅ Create CloudWatch dashboard with Insights queries (6 widgets)
- ✅ Fix _wait_for_networking() — replaced CloudFormation polling with NAT gateway check
- ✅ Fix batch poller timeout — async nat_manager invoke + _wait_for_nat
- ✅ Fix ecs:ListTasks IAM permission (Resource: '*' with region condition)
- ✅ Consolidate review docs into TODO.md and archive originals

### 2026-05-25
- ✅ Fix batch retrieve flow (download metrics before populating cache in run_retrieve_only)
- ✅ Pass BOOK_NAME to ECS tasks from trigger Lambda (container override + extract from pending keys)
- ✅ Add batch submission dedup guard (skip if identical job already pending)
- ✅ Add delayed networking teardown after Phase 2 (30-min EventBridge Scheduler, cancelled on next task launch)

### 2026-05-24
- ✅ Remove hardcoded secrets from deploy script (dynamic account ID + Secrets Manager lookup)
- ✅ Scope IAM permissions (Resource: '*' → scoped ARNs + region conditions)
- ✅ Container security hardening (non-root user, pinned digest, HEALTHCHECK, pinned requirements)
- ✅ Add SIGTERM handler (emergency S3 sync + lock removal on spot termination)
- ✅ Include event files in background sync (only -parsed.json excluded now)
- ✅ Add EventBridge spot termination rule (immediate lock clearing)
- ✅ Add per-task timeouts (5 min/chapter, 3 min/entity type via asyncio.wait_for)
- ✅ Fix index/entity write ordering (write confirmed before index update, returns None on failure)
- ✅ Make dedup merges idempotent (composite key dedup on event_mentions)
- ✅ Add ConnectionError to Grok retry filter
- ✅ ECS task timeout and watchdog (stopTimeout + 4hr idle self-terminate)
- ✅ Add COPY ID instructions to weather/casualties prompts
- ✅ Add severity calibration to logistics prompt
- ✅ Require name + PersonID in people prompt
- ✅ Clean up 193 people files (name + PersonID populated from filename)
- ✅ Resolve broken PlaceIDs in weather (83% resolved via fuzzy match, 248 remain)
- ✅ Implement weather deduplication (1590 → 591 files, 999 duplicates merged)
- ✅ Fix _auto_split_and_extract partial results (completeness validation + _partial flag)
- ✅ Fix _handle_wikipedia_error (guard against None response)
- ✅ Pipeline status script (scripts/pipeline_status.sh)
- ✅ Extract trigger Lambda from inline CloudFormation (trigger_handler.py)
- ✅ Link casualties to PeopleGroupIDs (entity_context passes name:ID pairs)
- ✅ Use locked_json for event_mention append (thread-safe read-modify-write)
- ✅ Sanitize dedup UI filename (path traversal blocked)
- ✅ Fix watchdog false positives (activity-based, notify wrapped in try/except)
- ✅ Fix _stamp_file atomic writes (temp+replace pattern)
- ✅ Lambda timeout awareness in dedup UI (bail early on approaching timeout)
- ✅ DynamoDB point-in-time recovery + DeletionPolicy on all 11 tables
- ✅ API Gateway authorizer TTL set to 300s (was 0)
- ✅ ECS task failure EventBridge rule (non-zero exit → lock check)
- ✅ Log retention increased to 30 days
- ✅ Pin CI dependencies (requirements-ci.txt)
- ✅ Fix pre-commit hardcoded macOS path
- ✅ Align logistics/supplemental enum values in prompts
- ✅ Enforce equipment category enum (prompt + fix 5 existing files)
- ✅ Fix WeatherMentionID → WeatherID field name mismatch in prompt
- ✅ Add config validation (required sections, type checks, range checks)
- ✅ Pin requirements.txt versions (exact versions)
- ✅ Trim Lambda package (only needed scripts)
- ✅ Remove continue-on-error from CI
- ✅ Pin Python 3.12 in CI

### 2026-05-21 – 2026-05-23
- ✅ Batch infrastructure optimization (submit-only/retrieve-only ECS modes, Lambda batch poller, DynamoDB job queue)
- ✅ Infra teardown after batch submission (NAT + OpenSERP scaled to 0)
- ✅ Prevent duplicate task launches (atomic conditional write in poller)
- ✅ Phase 2 re-trigger loop fixed (trigger Lambda checks for existing event files)
- ✅ Optimize ECS S3 downloads (scoped by BOOK_NAME, book manifest)
- ✅ Grok URL content verification for bibliography enrichment
- ✅ Auto-delete poisoned cache entries (pre-parse validation)
- ✅ Ensure dedup always runs (retry + notification guaranteed)
- ✅ Smart retry for empty event files (skips footnotes, retries substantial)
- ✅ Propagate source_url into notes-event files
- ✅ Grok verification for ambiguous single-name dedup matches
- ✅ Per-chapter heartbeat progress counter
- ✅ Meaningful batch job names (book + files + request count)
- ✅ Dedup exclusions split bug fix (exclusions now load correctly from DynamoDB)
- ✅ Name-based exclusions (survives file recreation/re-extraction)
- ✅ Stronger index normalization (ASCII-fold, strip punctuation)
- ✅ Fixed processed events registry (basename instead of absolute path)
- ✅ Preflight credit check before pipeline runs
- ✅ Phase 1 completion email includes parsed file list
- ✅ Phase 1 source hash skip (unchanged content not re-uploaded)
- ✅ Phase 1 only clears own lock (not Phase 2/3)
- ✅ Phase 1 full-sync scoped by BOOK_NAME
- ✅ Publish-before-delete in pending content re-trigger
- ✅ JSON quality statistics tracking
- ✅ OpenSERP crash loop fix (scale to 0 before NAT teardown)
- ✅ Batch poller 24h timeout for stuck batches
- ✅ Batch poller reset-to-pending on failed retrieve
- ✅ Failure notifications (SNS email on phase failure)
- ✅ Dedup gate reconciliation (hourly check triggers Phase 3 if missed)
- ✅ Background sync mtime tracking (no redundant S3 uploads)
- ✅ Atomic file writes (temp file + os.replace)
- ✅ Connection pool fix (session no longer destroyed per API call)
- ✅ Schema migration crash safety (try/finally re-enables trigger Lambda)
- ✅ SQS VisibilityTimeout fix (120→300, prevents redelivery)
- ✅ S3 DeletionPolicy + PublicAccessBlock
- ✅ Security scanning in deploy (hadolint, cfn-lint, cfn-nag, trivy)
- ✅ Monitor logs script (color-coded, JSON-parsed, polling-based)

### Earlier (2026-05-01 – 2026-05-19)
- ✅ Batch infrastructure optimization (submit-only/retrieve-only modes)
- ✅ BatchModeCollecting fix
- ✅ Final sync fix (Phase 3 uploads all files)
- ✅ OpenSERP endpoint fix + scales to 0 on completion
- ✅ NAT teardown via SNS + dynamic lifecycle
- ✅ LOC removed from search chain
- ✅ Schema versioning (all 11 entity types)
- ✅ NOAA weather enrichment module
- ✅ IAM least privilege audit + ALB removed
- ✅ Bibliography verbatim fix
- ✅ NARA Record Group identification via Grok
- ✅ External search cache (positive 30d / negative 7d)
- ✅ `enrichment_status` tracking
- ✅ Idle monitor rewritten
- ✅ Dedup exclusions → DynamoDB
- ✅ Casualties spec rewritten
- ✅ Output directory reorganized
- ✅ xAI Batch API (50% cost reduction)
- ✅ Event extraction token optimization (~72% reduction)
- ✅ Cross-reference consistency fixes
- ✅ Rate limiter, retry logic, ULID fixing
