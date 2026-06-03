# Pipeline Backlog

**Last Updated:** 2026-06-03

---

## High Priority

### Production Reliability (observed failures in E2E testing)

#### Phase 4: DynamoDB as primary entity storage
Eliminates the 15-30 min S3 download phase, prevents data loss on spot termination (writes are immediately durable), and enables incremental processing (query only unenriched entities). Current architecture loses all enrichment work on spot kill because background sync hasn't uploaded yet. With DynamoDB: zero data loss, instant restart from where it left off. Spec: `docs/current/PHASE4_SPEC.md`. Existing `import_to_dynamodb.py` provides the schema foundation.

**Strategy: Dual-write (DynamoDB + S3).** DynamoDB is source of truth for operational reads/writes (fast, durable, queryable). S3 remains as archival/bulk export (browsable JSON, versioned, cheap). Write DynamoDB first (immediate durability), periodic S3 export on phase completion for human review and backup.
*Source: end-2-end-1 spot termination data loss*

#### Add "Phase started" notifications
No email when ECS tasks launch. Add `_notify_launch()` at end of `_run_task()` in trigger_handler.py. Currently only get notifications on completion/failure — operator blind to whether pipeline is running.
*Source: end-2-end-0.md Issue 1*

---

## Medium Priority

### Dedup & Normalization

#### Incremental dedup (only score new files vs corpus)
All dedup scripts load ALL files and score ALL pairs every run (O(n²)). Track `last_dedup_run` timestamp per entity type in DynamoDB, only compare new files against existing. Add `dedup.mode: incremental|full` to config.yaml — `full` forces all-pairs comparison (useful after prompt changes or bulk re-extraction). Store file creation timestamps via S3 LastModified or entity metadata. Modify all 4 scripts to accept "newer than X" filter.
*Source: DEDUP_ANALYSIS_ALL_ENTITIES.md, end-2-end-1*

### Pipeline Efficiency

#### Phase 2 should read pending#parsed as input manifest
Phase 2 always falls through to S3 scan. Read the DynamoDB queue directly instead.
*Source: CODE_REVIEW agent analysis*

#### Optional entity extraction: parallelize across event files
Weather, equipment, logistics, casualties, supplemental extracted sequentially. Could run in parallel.
*Source: PIPELINE_REVIEW.md*

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

### Testing

#### Expand Lambda handler tests with invocation tests
Current tests only verify importability. Add at least one `handler(event, context)` call per handler with mocked boto3 clients. Requires refactoring handlers to avoid module-level boto3 initialization.
*Source: QA review*

#### Local end-to-end simulation test
Full pipeline with mocked Grok API (canned JSON responses).

#### Review local implementation end-to-end
Verify local mode (non-AWS) still works correctly after all AWS-focused changes. Test: Phase 1 parse → Phase 2 extract → Phase 3 enrich using filesystem storage, local cache, and real-time Grok API. Confirm config.yaml with `aws.enabled: false` produces correct output without S3/DynamoDB dependencies.

### UI & UX

#### Dedup UI: rename person (edit name field + rename file)

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

#### Add disk space checks before writes (local mode only)
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
- ✅ Fix pending queue reconciliation (hourly check now triggers Phase 1/2 if queues have items and no tasks running)
- ✅ Fix OpenSERP null response crash (null guard on resp.json() in _search_openserp)
- ✅ Fix dedup exclusion lists not checking name-based exclusions (places, groups, equipment now check both filename + name pairs)
- ✅ Refactor find_duplicate_equipment.py complexity (C21 → below C)
- ✅ Verify casualties entity_context already fixed (passes name:ID pairs, limit 50)
- ✅ Verify "COPY — do NOT generate" already in all prompts that inject IDs (casualties, weather)
- ✅ Align logistics enum values (added capacity_constraint + production_delay to _VALID_TYPES and hardcoded prompts)
- ✅ Fix weather _build_places_section (now uses places index with real PlaceIDs instead of nonexistent PlaceMentionIDs)
- ✅ Add few-shot examples to logistics severity calibration (4 examples + "DEFAULT TO MEDIUM" instruction)
- ✅ Add count qualifier pattern to casualties prompt (value+qualifier schema, enum in rules)
- ✅ Clean up 102 legacy people files (populated name from filename + generated PersonID)
- ✅ Auto-merge exact duplicates (identical normalized names merged automatically, fuzzy matches still go to human review)
- ✅ Equipment dedup: reject matches when numeric prefix differs
- ✅ Reduce false-positive place matches on common geographic prefixes/suffixes
- ✅ Equipment dedup: require country of origin match (exception for captured equipment)
- ✅ Normalize group dedup keys (strip branch names + nationality guard)
- ✅ Propagate coordinates to index-only place entries (coords.json + per-place DynamoDB items)
- ✅ Build equipment alias table (config/equipment_aliases.yaml, ~60 mappings, resolved in dedup scoring)
- ✅ Track "reviewed but no action" state in dedup UI (90-day TTL in DynamoDB, suppresses in all dedup scripts)
- ✅ Normalize caliber formats in equipment dedup (.50-caliber = 50 cal, 155-mm = 155mm, 7.5-cm = 7.5cm)
- ✅ Use technical_identifier as primary equipment index key (falls back to common_name)
- ✅ Add OpenSERP circuit breaker (5 consecutive failures → skip remaining, 90-day retry via timestamp)
- ✅ Phase 3 batch architecture verified (already implemented: submit-only collects searches + Grok prompts → batch API → retrieve-only hits all caches)
- ✅ Grok model tiering (model_map in config.yaml routes cache_type to per-task model, works in both real-time and batch)
- ✅ Write tests for batch_parallel.py (14 tests: entity creation, helpers, process logic)
- ✅ Convert test_equipment_deduplication.py to proper pytest (4 tests with assertions)
- ✅ Write tests for dedup_ui_handler.py (11 tests: path validation, actions, helpers, report modification)
- ✅ Add extraction-time validation (required fields checked on every entity write, warns on missing)
- ✅ Validate all 11 entity types in CI (fixed 3 schema name mismatches: places→place, dates→date, maps→map)
- ✅ Add S3 mocking with moto for storage layer tests (10 tests: read/write/list/delete/prefix/errors)
- ✅ Add golden file tests for extraction (13 tests: parsed structure, event structure, entity schemas, index format)
- ✅ Add integration tests for Phase 3 enrichment (5 tests: enrichment flow, skip-enriched, not_found, max_people)
- ✅ Add Lambda handler tests (7 tests: dedup_gate logic + importability for all 8 handlers)
- ✅ Fix mypy errors in phase2/phase3 Lambda handlers (type mismatch + 4 wrong function names)
- ✅ Verify watchdog notification + _stamp_file already fixed (try/except + atomic write already in place)
- ✅ SQS MessageRetentionPeriod increased (3600 → 1209600 = 14 days)
- ✅ Fix CloudWatch Alarms (pointed to ECS task names instead of Lambda functions)

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
