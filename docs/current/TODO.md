# Pipeline Backlog

**Last Updated:** 2026-06-07

---

## High Priority

### Data Quality (affects extraction/enrichment outcomes)

#### OpenSERP torn down before Phase 3 uses it
~~Phase 3 connects to `localhost:7001`~~ Fixed: ECS tasks now cancel stale delayed teardowns at startup. Remaining risk: if idle monitor fires during Phase 3 (shouldn't happen — it checks for running tasks, but verify).
*Source: E2E testing 2026-06-05*

### Production Reliability (pipeline completes without intervention)

### Architecture (enables future data improvements)



---

## Medium Priority

### Pipeline Efficiency

### Infrastructure Fixes

#### Fix EntityCreatedTopic S3 notification (never configured)
Phase 3 entity-created flow broken.
*Source: CODE_REVIEW.md*

### Notifications

#### Add "Enrichment started" notification from Phase 3 ECS task
Distinguishes "task launched" (before download) from "enrichment in progress" (real work starting).
*Source: end-2-end-1 observation*

#### Add "still waiting" notification for long-running batches
Operator blind to stalled batches.
*Source: PIPELINE_REVIEW.md*

### CI/CD & DevOps

## Low Priority

### Code Quality

### Performance

#### Reduce image memory usage in equipment.py
Image + base64 = ~4x memory. 80MB for a 20MB image.
*Source: CODE_REVIEW.md*

### Testing

### UI & UX

### DevOps

#### CloudFormation drift detection
*Source: DEVOPS_RECOMMENDATIONS.md*

#### Add secrets scanning to CI
detect-secrets or gitleaks.
*Source: QA_GAPS.md*

#### Evaluate residential proxy for OpenSERP searches
Search engines block AWS datacenter IPs. **Preferred approach: Tailscale exit node** on a home machine (RPi/NAS). ECS OpenSERP container joins Tailnet, routes outbound traffic through residential IP. Tailscale ACLs restrict ECS to internet-only (no LAN access — protects local secrets). Free, no third-party proxy trust, ~20-50ms added latency. Alternatives: paid SERP API (SerpAPI, ScaleSerp ~$50-100/mo) or restrict search to local-only runs.
*Source: end-2-end-1 Phase 3 observation*

### Data



---

## Completed (2026-06-03 through 2026-06-07)

#### ~~Phase 4: DynamoDB as primary entity storage~~ ✅ B/C/D implemented
Phase A (core + dual-write), Phase B (DynamoDB materializer), Phase C (dedup reads), Phase D (S3 export-only). Spec: `docs/current/PHASE4_DYNAMODB_STORAGE.md`.

#### ~~S3 sync does not delete merged/removed entity files~~ ✅ Fixed
After auto-merge deletes duplicate files locally, the final S3 sync now tracks deletions and removes them from S3. Merged files no longer persist across runs.


#### ~~People dedup: first name alone is insufficient for duplicate detection~~ ✅ Fixed
Tightened: single-name matches now require shared context or distinctive name (>5 chars). Last-name-only matches require >4 chars. Short common names no longer produce false positives.


#### ~~Normalize abbreviated ranks to full form in people entities~~ ✅ Fixed
Already implemented in `_normalize_rank`. Expanded mapping to handle period-less variants (Col, Lt Gen, etc.) and German ranks.


#### ~~Incremental dedup (only score new files vs corpus)~~ ✅ Implemented
All 4 dedup scripts now use `src/dedup/incremental.py` — tracks `dedup_run#{type}` timestamp in DynamoDB, only scores pairs where at least one file is newer than last run. First run = full mode. Delete DynamoDB keys to force full re-scan.


#### ~~Add `additionalProperties: false` to extraction schemas~~ ✅ Done
All 15 schemas now reject unexpected fields. Internal metadata (`_schema_version`, `_last_updated`) allowed via `patternProperties: {"^_": {}}`.


#### ~~Delayed networking teardown kills active retrieve tasks~~ ✅ Fixed
Three fixes: (1) ECS tasks cancel stale scheduled teardowns at startup, (2) batch_poller uses RequestResponse for NAT creation (errors visible), (3) nat_manager SNS filter fixed to trigger on any pipeline completion (was dead code filtering for "Phase 3" on Phase2CompleteTopic).


#### ~~Pending content queue not cleared after Phase 1 completes~~ ✅ Fixed
`_post_process` now deletes `pending#content` from DynamoDB after Phase 1 completes.


#### ~~NAT availability check always times out (3 min wasted per launch)~~ ✅ Fixed
`_wait_for_networking` was filtering by `tag:Project` but NAT is tagged `tag:Name`. Fixed to use `f"{ENV_NAME}-nat"` matching nat_manager.


#### ~~Phase 3 has no completion notification~~ ✅ Fixed
Added `_notify_complete` call after Phase 3 submit-only completes, with "Pipeline run complete" message.


#### ~~Add "Phase started" notifications~~ ✅ Fixed
`_notify_launch()` added to `_run_task()` — sends email with phase name, book, and trigger source on every ECS task launch.


#### ~~Preload DynamoDB cache into memory at Phase 2 start~~ ✅ Done
`DynamoCacheBackend.preload()` scans all entries into a local dict on `GrokClient` init. Eliminates 1600+ individual gets — one paginated scan instead.


#### ~~_complete_metadata() bypasses batch mode~~ ✅ Fixed
Now passes `batch_mode=args.batch` to `GrokClient` — metadata completions are batched at 50% discount.


#### ~~Phase 2 should read pending#parsed as input manifest~~ ✅ Fixed
`_read_manifest()` now reads `pending#parsed` (written by trigger Lambda), clears it after reading, falls back to `manifest#phase2`.


#### ~~Optional entity extraction: parallelize across event files~~ ✅ Done
`ThreadPoolExecutor(max_workers=config.max_event_files)` processes event files in parallel.


#### ~~Reduce prompt fulltext for dates extraction~~ ✅ Done
Dates uses summary when ≥50 chars. Places kept fulltext (needs geographic context).


#### ~~Split large prompts into smaller batches~~ ✅ Done
Logistics, casualties, weather chunked to 10 sub-events per API call.


#### ~~Phase 3 retry: exclude not_found from unenriched count~~ ✅ Fixed


#### ~~Fix manifest read-modify-write race in trigger Lambda~~ ✅ Fixed
Replaced get+put with atomic `list_append` via `update_item`.


#### ~~Fix non-paginated DynamoDB cache clear~~ ✅ Fixed


#### ~~Move SecretsManager VPC endpoint to dynamic set~~ ✅ Done
Moved to `INTERFACE_ENDPOINTS` in nat_manager.py. Saves $14.60/month.


#### ~~Replace silent exception swallowing~~ ✅ Done
22 silent `pass` blocks replaced with `logger.warning()` across 13 files.


#### ~~Add "Batch submitted" notification~~ ✅ Done
SNS notification with phase, book, batch_id, request count.


#### ~~Phase 1 notification: include incremental vs full context~~ ✅ Done


#### ~~Unify schema versioning~~ ✅ Done
Single `SCHEMA_VERSION = "2.3"` in `src/schemas/__init__.py`, imported everywhere.

---


#### ~~Fix scheduler variable scope~~ ✅ Fixed


#### ~~Dedup guard: check recently completed batches~~ ✅ Done


#### ~~Remove hardcoded region~~ ✅ Done
`get_aws_region()` in `src/utils/config.py` as single source of truth.


#### ~~Fix prompt_loader str.format() breaking on JSON braces~~ ✅ Fixed


#### ~~Fix json_validator._fix_invalid_ulids mutating input~~ ✅ Fixed
Now deep-copies before modifying.


#### ~~Refactor ecs_entrypoint.py~~ ✅ Extracted
`ecs_modules/s3_sync.py` (591 lines), `ecs_modules/aws_networking.py` (324 lines). Original remains functional.


#### ~~Refactor equipment.py~~ ✅ Extracted
`src/extraction/equipment_ext/` — media.py, dedup.py, enrichment.py. Original remains functional.


#### ~~Include entity counts in phase_complete notification~~ ✅ Done
Phase scripts write `.phase_results.json`, entrypoint reads it for SNS notification.


#### ~~Expand Lambda handler tests~~ ✅ Done
4 invocation tests with mocked boto3.


#### ~~Local end-to-end simulation test~~ ✅ Done
`tests/integration/test_local_e2e.py` — Phase 2 extraction with canned responses.


#### ~~Review local implementation end-to-end~~ ✅ Verified
All imports, cache, entity store, validators confirmed working in local mode.


#### ~~Dedup UI: batch-commit merges with undo support~~ ✅ Done
Client-side queue + Commit All button + `dedup/history/` snapshots + undo endpoint.


#### ~~Dedup UI: rename person~~ ✅ Already implemented
`POST /dedup/api/rename` + "✎ Fix name" button in UI.


#### ~~AWS cost quick wins~~ ✅ Done
Insights→enabled, S3 version expiry 30d, Lambda 256→128MB, `__contains__` caches value.


#### ~~Scripts cleanup and categorization~~ ✅ Done
8 scripts archived, README rewritten with 9 categories.


#### ~~Dedup UI refactor~~ — Skipped
Not justified for single-user internal tool. Revisit if UI grows past 100KB.


#### ~~Fix EIP leak on NAT delete~~ ✅ Fixed
NAT deletion now captures and releases EIP after gateway is deleted.


#### ~~Add disk space checks before writes~~ ✅ Done
50MB threshold in `write_json_with_lock`, local mode only.


#### ~~Add backup before dedup merge operations~~ ✅ Done
`_backup_before_delete()` copies to `dedup/backups/` before every merge unlink.


#### ~~Create operations runbook~~ ✅ Done
`docs/current/RUNBOOK.md` — re-runs, locks, debugging, emergency procedures.


#### ~~Re-extract entities with updated prompts~~ ✅ Done
`processing.reprocess_types: ["people"]` bypasses processed registry for listed types.


#### ~~Create place entities for unresolved weather locations~~ ✅ Done
67 places created, 164 weather files linked. Script: `scripts/resolve_weather_places.py`.


#### ~~Source-anchored names~~ ✅ Already implemented
Weather places created with `identified_as` field.


#### ~~Periodic re-search of not_found entities~~ ✅ Already implemented
`re_search_after_days: 90` in config, checked by all enrichment functions.

---


## Future / Research

### True multi-job concurrency
Run multiple books in parallel (separate ECS tasks per book). Currently one task at a time with SQS queuing. Requires: per-book locking, shared DynamoDB entity store (Phase 4B), and dedup coordination across concurrent jobs. Prerequisite: DynamoDB as primary storage.

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

## Completed (Earlier)

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
- ✅ Phase 4A: DynamoDB entity store core (DynamoEntityStore class + dual-write hook + config + tests)

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
