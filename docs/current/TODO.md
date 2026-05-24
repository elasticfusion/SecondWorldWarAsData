# Pipeline Backlog

**Last Updated:** 2026-05-24

---

## High Priority

### Security & Infrastructure

### Prompt & Data Quality

### Reliability


---

## Medium Priority

### CI/CD & DevOps

#### DynamoDB point-in-time recovery and DeletionPolicy on all tables
Only CacheTable has DeletionPolicy. Entity tables would be deleted on stack delete. Enable PITR for recovery.
*Source: DEVOPS_RECOMMENDATIONS.md*

#### API Gateway WAF and rate limiting
No WAF, no rate limiting, authorizer TTL=0 (every request invokes auth Lambda). Add WAF rate-limit rule, set authorizer TTL to 300s.
*Source: DEVOPS_RECOMMENDATIONS.md*

#### Observability: ECS failure alarms and custom metrics
No alarm on ECS task non-zero exit. No end-to-end latency metric. Add EventBridge rule for STOPPED tasks, emit custom metrics from entrypoint. Increase log retention to 30 days.
*Source: DEVOPS_RECOMMENDATIONS.md*

#### Pin dependency versions (pip-compile)
`requirements.txt` uses open ranges (`>=`). Builds not reproducible. Use `pip-compile` for locked requirements. Trim Lambda package (includes unnecessary `scripts/`).
*Source: DEVOPS_RECOMMENDATIONS.md*

#### Remove `continue-on-error: true` from CI validation
Failures never block merges. Add a final step that fails if any validation failed.
*Source: QA_GAPS.md*

#### Pin Python version to 3.12 in CI
CI uses 3.11 while development uses 3.12.
*Source: QA_GAPS.md*

#### Pin CI dependencies with exact versions
`pip install jsonschema pytest` installs latest with no pinning.
*Source: QA_GAPS.md*

#### Fix pre-commit hardcoded macOS path
Has `/Users/dchristian/...` — won't work on Linux/CI.
*Source: QA_GAPS.md*

### Prompt & Schema

#### Align logistics/supplemental enum values to actual output
Logistics prompt defines `type` and `status` values that differ from actual output. Prompt defines: supply_shortage, transportation_disruption, capacity_constraint, distribution_failure, production_delay. Output contains: supply_shortage, supply_excess, delivery_delay, transport_disruption. Supplemental `availability` values differ. Align prompts to match desired output schema.
*Source: PROMPT_REVIEW.md, DATA_SCIENCE_RECOMMENDATIONS.md*

#### Enforce equipment category enum in prompt
Output contains non-standard values like "Medium Tank" and "infantry". Add explicit category list with examples showing category vs subcategory distinction. Normalize existing non-standard values (e.g., "Medium Tank" → "armor", "infantry" → "infantry_weapons").
*Source: PROMPT_REVIEW.md, DATA_SCIENCE_RECOMMENDATIONS.md*

#### Standardize field naming across prompts
Inconsistent: `Sub-eventID`/`Sub_eventID`, `Sub-event_summary`/`Sub_event_Name`, `event_mentions`/`mentions`. Pick one and align all prompts + output.
*Source: PROMPT_REVIEW.md*

#### Fix WeatherMentionID vs WeatherID field name mismatch
Weather extraction uses `WeatherMentionID`, output uses `WeatherID`.
*Source: QA_GAPS.md*

#### Add config validation (Pydantic model or JSON Schema)
Zero validation on config.yaml — typos silently ignored, invalid values only fail deep in pipeline. Fail fast with clear error messages at load time.
*Source: QA_GAPS.md*

#### Unify schema versioning
`json_schemas.py` uses "1.0.0", output schemas use "2.3" — no clear relationship. Single version number across extraction and output.
*Source: QA_GAPS.md*

#### Add `additionalProperties: false` to extraction schemas
LLM-hallucinated fields pass undetected. Also add `minLength: 1` to required string fields.
*Source: QA_GAPS.md*

### Testing

#### Write tests for batch_parallel.py
Critical orchestration module (1007 lines) with zero tests. Mock Grok API, test error isolation and concurrency.
*Source: QA_GAPS.md, CODE_REVIEW.md*

#### Write tests for dedup_ui_handler.py
User-facing Lambda (1141 lines) with zero tests. Mock DynamoDB/S3, test merge idempotency.
*Source: QA_GAPS.md*

#### Convert anti-pattern tests to proper pytest tests
3 test files with zero assertions (test_supplemental.py, test_supplemental_complete.py, test_equipment_deduplication.py). Convert to proper tests with assertions and mocked dependencies.
*Source: QA_GAPS.md*

#### Add S3 mocking with moto for storage layer tests
S3 operations in storage.py, s3_lazy.py, ecs_entrypoint.py have no test coverage.
*Source: QA_GAPS.md*

#### Add extraction-time validation
Before writing entity files: validate required fields present, referenced IDs exist, no duplicate files for same logical entity. Reject or flag invalid extractions.
*Source: DATA_SCIENCE_RECOMMENDATIONS.md*

#### Validate all 11 entity types in CI
Currently only validates 3 of 11 (People, Equipment, Events).
*Source: QA_GAPS.md*

### Dedup & Normalization

#### Normalize group index keys more aggressively
Strip "the/us/u.s.", remove branch names (infantry/armored/airborne), collapse ordinals. Prevents "4th division" ≠ "4th infantry division" creating separate files.
*Source: DEDUP_ANALYSIS_ALL_ENTITIES.md*

#### Propagate coordinates to index-only place entries
When a place is first created with coordinates, store them in the index so cross-book dedup can use distance matching.
*Source: DEDUP_ANALYSIS_ALL_ENTITIES.md*

#### Build equipment alias table
Map known equivalents: "sherman"→"m4 sherman", "panther"→"pzkpfw v panther", "88"→"88mm flak 36", etc.
*Source: DEDUP_ANALYSIS_ALL_ENTITIES.md*

#### Normalize caliber formats in equipment extraction
`.50-caliber` ≠ `50 caliber` ≠ `12.7mm`. Strip leading dots, normalize mm format.
*Source: DEDUP_ANALYSIS_ALL_ENTITIES.md*

#### Incremental dedup (only score new files vs corpus)
All dedup scripts load ALL files and score ALL pairs every run (O(n²)). Track `last_dedup_run` timestamp, only compare new files against existing.
*Source: DEDUP_ANALYSIS_ALL_ENTITIES.md*

#### Track "reviewed but no action" state in dedup UI
Pairs reviewed without merge/exclude decision reappear next run. Add `reviewed_at` timestamp, suppress recently-reviewed pairs.
*Source: DEDUP_ANALYSIS.md*

#### Use technical_identifier as primary equipment index key
More stable than common_name. Fall back to common_name only when no technical ID exists.
*Source: DEDUP_ANALYSIS_ALL_ENTITIES.md*

### Pipeline Efficiency

#### Phase 2 should read pending#parsed as input manifest
`_download_phase2_inputs` always falls through to S3 scan (lists all objects, checks each for event file). The trigger Lambda already writes parsed file keys to `pending#parsed` in DynamoDB — Phase 2 should read this directly instead of scanning. Reduces compute time on startup.
*Source: CODE_REVIEW agent analysis*

#### Increase API rate limit (test with 60 RPM)
Current `calls_per_minute: 30` is conservative. Grok typically allows 60-120 RPM. Rate limiter already handles 429 backoff. Directly doubles extraction throughput if API allows it.
*Source: PERFORMANCE_ENHANCEMENTS.md*

#### Grok model tiering (use lighter models for simple tasks)
`grok-4.3` used for ALL tasks including trivial classification (ISBN extraction, URL validation, license check, NARA matching). Add configurable `model_map` in config.yaml to route tasks by cache_type to appropriate models. Models evolve — all model names must be in config, never hardcoded. 15-25% Grok cost savings.
*Source: COST_OPTIMIZATION.md*

#### Move SecretsManager VPC endpoint to dynamic set
Always-on interface endpoint costs $14.60/month for ~5 calls per pipeline run. Add to `INTERFACE_ENDPOINTS` in nat_manager.py so it's created/destroyed with NAT.
*Source: COST_OPTIMIZATION.md*

#### Reduce prompt fulltext for dates/places extraction
Phase 2 sends full chapter text for ALL entity types. For dates and places, sub-event summaries alone likely suffice. Test with `include_fulltext=False` — could cut 25% of Phase 2 input tokens (10-15% cost savings).
*Source: COST_OPTIMIZATION.md*

#### Buffer entity file writes per chapter
Each entity mention triggers immediate file read+write. Buffer updates in memory during chapter extraction, flush once at end. Reduces I/O from O(mentions) to O(unique_entities).
*Source: PERFORMANCE_ENHANCEMENTS.md*

#### Split large prompts into smaller batches for parallel processing
Batch extraction concatenates ALL sub-events into one prompt (50-100K tokens for large chapters). Split into batches of 5-10 sub-events, process in parallel. Reduces wall-clock time ~4x for large chapters and reduces truncation risk.
*Source: PERFORMANCE_ENHANCEMENTS.md*

#### Conditional S3 downloads (skip unchanged files)
`s3_sync_down` downloads every file regardless of local state. Use `head_object` to check size/ETag before downloading. Skips 80-90% of downloads on Phase 3 re-runs.
*Source: PERFORMANCE_ENHANCEMENTS.md*

#### Optional entity extraction: parallelize across event files
Weather, equipment, logistics, casualties, supplemental extracted sequentially per event file. Could run in parallel (same max_parallel semaphore) for significant speedup.
*Source: PIPELINE_REVIEW.md*

#### Phase 3 retry: exclude enrichment_status "not_found" from unenriched count
phase3_retry.py counts entities with `enrichment_status: "not_found"` as unenriched, causing unnecessary retries that always find nothing.
*Source: PIPELINE_REVIEW.md*

#### Fix _get_book_entity_files scoping (downloads everything)
Function attempts to scope downloads to a specific book but fallback downloads entire entity corpus. Add `book`/`source_book` field to entity files during Phase 2, filter index by that field.
*Source: PIPELINE_REVIEW.md, DATA_FLOW_ANALYSIS.md*

#### Add "still waiting" notification for long-running batches
Batch poller sends completion/failure notifications but not "batch pending >X hours" alerts. Operator blind to stalled batches.
*Source: PIPELINE_REVIEW.md*

#### Phase 1 notification: include incremental vs full context
Notification lists filenames but doesn't indicate "Incremental: 3 new files" vs "Full re-parse: 47 files".
*Source: PIPELINE_REVIEW.md*

### Infrastructure Fixes

#### SQS MessageRetentionPeriod too short (1hr)
Messages lost during outages. Increase to 4-14 days.
*Source: CODE_REVIEW.md*

#### Fix CloudWatch Alarms referencing wrong namespace
Alarms reference Lambda namespace for ECS tasks — non-functional monitoring.
*Source: CODE_REVIEW.md*

#### Fix EntityCreatedTopic S3 notification (never configured)
S3 notification for entity creation never configured in custom resource. Phase 3 entity-created flow broken.
*Source: CODE_REVIEW.md*

#### Fix manifest read-modify-write race in trigger Lambda
Lost S3 keys when concurrent Lambda invocations modify manifest.
*Source: CODE_REVIEW.md*

#### Fix non-paginated DynamoDB cache clear
`DynamoCacheBackend.clear()` only processes first page. Add pagination loop.
*Source: QA_GAPS.md*

#### Add disk space checks before writes
Unbounded cache growth in ecs_entrypoint.py and cache_backend.py. No pre-write verification.
*Source: QA_GAPS.md*

#### Add backup before dedup merge operations
Dedup merges delete secondary files with no undo. Write backup before destructive operations.
*Source: QA_GAPS.md*

#### Replace silent exception swallowing
Multiple locations use `except Exception: pass` (e.g., `_clear_manifest`, `_clear_all_locks`). At minimum log at WARNING level with context.
*Source: QA_GAPS.md*

---

## Low Priority

### UI & UX

#### Dedup UI: rename person (edit name field + rename file)
Allow editing the `name` field directly in the dedup UI and renaming the file to match. Current "Fix name" only works when filename/content mismatch — doesn't support correcting a wrong name (e.g., "mclain" → "Raymond S. McLain").

#### Refine place deduplication scoring
Only flag places as duplicates when: exact name match (allowing for non-English character variants like accents/umlauts), or one name is contained within another (e.g., "Belleau" / "Bois de Belleau"). Current fuzzy matching produces too many false positives for places with similar but distinct names.

#### Auto-merge equipment with case-insensitive exact name match
When two equipment entries have identical names differing only in case (e.g., "M4 Sherman" / "M4 sherman"), auto-merge them without human review.

### Code Quality

#### Use locked_json for all entity file read-modify-write
Replace open→load→modify→write pattern with `locked_json` context manager in dates.py, places.py, people_groups.py, batch_parallel.py. Prevents lost event mentions under parallel execution.

#### Extract shared patterns (retry, index, event_mention)
Deduplicate retry loop, index load/save, and `_add_event_mention` patterns across dates.py, places.py, people_groups.py, batch_parallel.py.

#### Cache config.py load_config() result
`load_config()` reads YAML from disk on every call. Add module-level caching.

#### Fix _build_date_id_lookup rebuilt 4x per chapter
Built once per entity type in batch_parallel.py. Build once and pass to all extractors.

#### Fix prompt_loader str.format() breaking on JSON with braces
`prompt_loader` uses `str.format()` — breaks on JSON templates containing `{}`.
*Source: CODE_REVIEW.md*

#### Fix json_validator._fix_invalid_ulids mutating input
Validate shouldn't modify. Surprising side effects when validation changes data.
*Source: CODE_REVIEW.md*

### Performance

#### Parallel S3 downloads in ecs_entrypoint
`s3_sync_down` downloads sequentially. Use ThreadPoolExecutor for parallel downloads.

#### Reduce image memory usage in equipment.py
Image processing loads full image + base64 encoding (~4x memory). 80MB for a 20MB image.
*Source: CODE_REVIEW.md*

#### Fix _lookup_by_place_id O(n) iteration in weather_central.py
Iterates ALL place files per lookup. O(n²) for weather extraction. Build a lookup dict.
*Source: CODE_REVIEW.md*

#### Implement circuit breaker for Grok API
After N consecutive failures, fast-fail for a cooldown period instead of repeated slow retries.
*Source: QA_GAPS.md*

#### Add unbounded cache size limit with LRU eviction
`DiskCacheBackend` has no size limits. Add max-size with LRU eviction.
*Source: QA_GAPS.md*

#### Fix ThreadPoolExecutor leak in grok_client
`_post_with_deadline` creates a new ThreadPoolExecutor per API call. Use a shared pool or asyncio timeout instead.

### Testing

#### Add golden file tests for extraction
Store expected extraction output, compare against actual. Catches prompt/schema regressions.
*Source: CODE_REVIEW.md*

#### Add integration tests for Phase 3 enrichment pipeline
Entire enrichment phase untested. Mock external APIs, test merge logic.
*Source: QA_GAPS.md*

#### Add Lambda handler tests (remaining 7 handlers)
Only batch_poller has tests. Use moto for DynamoDB/S3/Lambda mocking.
*Source: QA_GAPS.md*

#### Local end-to-end simulation test
Run full pipeline locally with a single small chapter and mocked Grok API (canned JSON responses). Validates all code paths: parse → submit-only → job queue → retrieve-only → dedup → Phase 3 submit → retrieve.

### DevOps

#### AWS cost quick wins (trivial config changes)
Parameterize Container Insights level (`enabled`/`enhanced`/`disabled` via CloudFormation parameter, default `enabled` — saves $5-10/mo). Add S3 noncurrent version expiration (30 days). Set Lambda log retention to 30 days. Reduce Lambda memory (dedup-ui 512→256, metrics/openserp-manager 256→128). Single DynamoDB read for cache hits (replace `__contains__` + `__getitem__` with single `get()`).
*Source: COST_OPTIMIZATION.md*

#### Add staging environment gate
Add GitHub Environment with required reviewers before production deploy. Low priority — single developer, no external consumers.
*Source: DEVOPS_RECOMMENDATIONS.md*

#### Scripts cleanup and categorization
`scripts/__pycache__/` committed, 60+ scripts with no categorization, duplicates (find_duplicate_places.py and v2). Consider deprecating deploy_all.sh in favor of `gh workflow run`.
*Source: DEVOPS_RECOMMENDATIONS.md*

#### CloudFormation drift detection
No scheduled drift detection. Add periodic check to catch manual changes that diverge from template.
*Source: DEVOPS_RECOMMENDATIONS.md*

#### Dedup UI refactor (extract HTML to S3/CloudFront)
dedup_ui_handler.py is 47KB with inline HTML. Extract static assets to S3, Lambda serves API only. Reduces cold start.
*Source: DEVOPS_RECOMMENDATIONS.md*

#### Add secrets scanning to CI
detect-secrets or gitleaks. No secrets detection currently.
*Source: QA_GAPS.md*

#### Fix EIP leak on NAT delete
nat_manager.py allocates EIPs but never releases them on NAT deletion. $3.60/month per leaked EIP.

### Data

#### Re-extract entities with updated prompts
Add `processing.reprocess_types: [places, people, peoplegroups]` config option that clears cache for only the specified entity types and re-runs extraction.

#### Create place entities for 248 unresolved weather locations
Weather PlaceID reconciliation resolved 83% (1,292 files). Remaining 248 have place names (e.g., "Hill 310 area", "Canrobert line") with no matching place entity. Need new place entities created or manual mapping. Leaves events and other types untouched.

#### Source-anchored names (`identified_as` field)
Add `identified_as` as an optional Phase 3 enrichment field. Phase 2 keeps extracting names as-is from source text. Phase 3 asks Grok "who is this person/unit based on event context?" and stores the canonical name. Dedup uses `identified_as` as an additional matching signal without changing the index key.

#### Periodic re-search of not_found entities
Add config `enrichment.re_search_after_days: 90` to retry entities after threshold.

---

## Future / Research

### Grok function calling for Phase 3 enrichment
Use Grok 4.3 function calling to let the model orchestrate search/verification tools (NARA, Archive.org, Wikipedia) directly during enrichment, replacing multi-step Python orchestration. Blocked on: function calling support in batch API (currently real-time only).

### Phase 4: Document Acquisition & Processing
Download digitized sources, OCR, feed back through pipeline. Spec: `docs/current/PHASE4_SPEC.md`.

### UK National Archives (Discovery API)
British military records integration.

### Step Functions pipeline orchestration
Replace SNS → SQS → Lambda → ECS orchestration with AWS Step Functions. Visual execution graph, built-in retry/catch/timeout, execution history, no custom lock management. Significant refactor but eliminates lock/manifest complexity.
*Source: DATA_FLOW_ANALYSIS.md*

### Queue-based distributed processing (Option D)
Coordinator → SQS Queue → Worker pool → Post-processor merge. Highly scalable, auto-scaling, fault tolerant. Major architecture change.
*Source: FUTURE_ENHANCEMENTS.md*

### Append-only + merge distributed processing (Option E)
Each server writes to own directory, merge script combines results. No locking needed, true parallelism. Medium effort.
*Source: FUTURE_ENHANCEMENTS.md*

### Data quality metrics dashboard
Automated quality report after each extraction batch: cross-reference resolution rate, null field rates, duplicate counts, entity count trends. Store in `output/metrics/quality_report.json`.
*Source: DATA_SCIENCE_RECOMMENDATIONS.md*

### Incremental enrichment strategy (prioritize by mention count)
For 783 people with "not_found": prioritize by mention count, try alternative search strategies (rank + unit + date range), search non-English names in original language.
*Source: DATA_SCIENCE_RECOMMENDATIONS.md*

### Weather dedup at extraction time
Check if file for (date, place_name) already exists before extraction. Append new event_mention to existing file instead of creating duplicates at source.
*Source: DATA_SCIENCE_RECOMMENDATIONS.md*

### Unify schema naming conventions
Pick one convention and migrate: Sub_eventID everywhere, event_mentions everywhere, MentionID everywhere. Write migration script for all existing files.
*Source: DATA_SCIENCE_RECOMMENDATIONS.md*

### Chapter partitioning for parallel extraction
`python phase2_extract.py --chapters 1-50` on multiple servers. No code changes needed for basic version, true parallelism, no locking required.
*Source: FUTURE_ENHANCEMENTS.md*

### Local-mode automated phase chaining
Simple `run_pipeline.py` that sequences Phase 1 → Phase 2 → Phase 3 for unattended local runs (cron).
*Source: PIPELINE_REVIEW.md*

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
- ✅ SQS deduplication confirmed already working (60s batching window)
- ✅ Batch mode for optional extractors confirmed already working
- ✅ run_phase complexity reduced (D→C)
- ✅ _stamp_schema_versions complexity reduced (C17→C13)
- ✅ Monitor logs script (color-coded, polling-based)

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
