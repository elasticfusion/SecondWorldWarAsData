# Pipeline Backlog

**Last Updated:** 2026-06-13

---

## Critical (data loss / correctness bugs)

#### Race condition on index.json (read-modify-write without lock)
Multiple async tasks sharing `output_root` can load stale index.json copies, causing last-writer-wins and dropped index entries. Fix: use `locked_json()` around the full read-modify-write cycle.
*Source: CODE_INTEGRITY_REVIEW.md #1*

#### BackgroundSync uploads partially-written files
S3 sync thread can read files mid-write (between mkstemp and os.replace). Fix: skip `.tmp` files, add mtime staleness check (only upload if unchanged for >2s).
*Source: CODE_INTEGRITY_REVIEW.md #2*

#### DynamoDB dual-write is fire-and-forget
Failed DynamoDB writes logged at DEBUG only. Causes filesystem/DynamoDB divergence that Phase 3 materialization doesn't detect. Fix: log at WARNING, track failed writes for retry, reconcile at Phase 3 start.
*Source: CODE_INTEGRITY_REVIEW.md #3*

#### BatchCollector not thread-safe
`BatchCollector.add()` uses a plain list + set with no locking. Optional extractors run in `ThreadPoolExecutor` — concurrent calls can lose requests or produce duplicates. Fix: wrap with `threading.Lock`. This is the #1 suspect for zero-count optional entities.
*Source: Code review 2026-06-13*

---

## High Priority (pipeline fails or produces wrong results without intervention)

#### Fix DynamoCacheBackend.preload() reserved keyword bug
Uses `response` in ProjectionExpression without ExpressionAttributeNames. Will fail at runtime if DynamoDB entry has a field named `response`.
*Source: QA test_cache_backend.py 2026-06-07*

#### ULID fix generates different replacements for same invalid ID
Same invalid ULID referenced in multiple places within one response gets different replacements, breaking internal referential integrity. Fix: build replacement map and reuse same new ULID for repeated occurrences.
*Source: CODE_INTEGRITY_REVIEW.md #5*

#### Event mention dedup only checks Sub_eventID
Re-extractions with new Sub_eventIDs bypass the duplicate check, accumulating mentions. Fix: check (EventID + book + paragraph context) not just Sub_eventID.
*Source: CODE_INTEGRITY_REVIEW.md #4*

#### merge_generic doesn't update event file cross-references
After merging places/groups/equipment, event files still reference deleted entity IDs. Fix: call `update_event_refs()` in `merge_generic`.
*Source: CODE_INTEGRITY_REVIEW.md #6*

#### Teardown timing in non-batch fallback
When `run_submit_only` takes the non-batch path (all events cached), it tears down NAT immediately after auto-triggering Phase 3. The trigger Lambda recreates it, adding ~3 min latency. Fix: skip teardown when Phase 3 was just triggered.
*Source: Code review 2026-06-13*

---

## Medium Priority (efficiency, observability, developer experience)

### Pipeline Efficiency

#### Batch ALL entity types, not just events
Currently only events go to Batch API (50% savings). People, places, groups, dates, and optional entities still use live calls. Design: submit-only collects ALL requests into batch, retrieve-only re-runs with full cache. Saves ~60% of API costs.
*Source: Ardennes debugging 2026-06-13*

#### Explicit `BatchModeCollecting` handling in optional extractors
`casualties.py`, `logistics.py`, `weather_central.py`, `equipment.py` catch it via generic `except Exception` and log at ERROR. Catch explicitly first at DEBUG, let generic handler catch real errors.
*Source: Code review 2026-06-13*

#### Phase results zero-count sanity check
If ALL optional extractors produce 0 entities AND >5 events, emit WARNING in SNS notification. Catches extraction failures immediately.
*Source: Code review 2026-06-13*

#### Optional extractors lack idempotency check
On re-run, casualties/logistics extract again for all events, creating duplicates. Fix: check if output exists before API call.
*Source: CODE_INTEGRITY_REVIEW.md #15*

### Data Integrity

#### Truncated JSON repair hides data loss
Closing braces on truncated responses produces valid but incomplete JSON. Downstream doesn't know data is missing. Fix: flag truncated responses, re-request with shorter input.
*Source: CODE_INTEGRITY_REVIEW.md #9*

#### String replacement in update_event_refs can corrupt data
Blind `text.replace(old_id, new_id)` could match IDs in URLs or text fields. Fix: parse JSON and only replace in known ID fields.
*Source: CODE_INTEGRITY_REVIEW.md #11*

#### Phase 1 chapter splitting has no overlap
Events spanning the 50-paragraph boundary generate duplicates or lose context. Fix: add 2-3 paragraph overlap between chunks.
*Source: CODE_INTEGRITY_REVIEW.md #7*

### Infrastructure

#### Fix EntityCreatedTopic S3 notification (never configured)
Phase 3 entity-created flow broken.
*Source: CODE_REVIEW.md*

#### Placeholder regex hardening in `_validate_prompt`
Change `r"\{([a-z_]+)\}"` to match only known template variables. Prevents future false positives on JSON with lowercase keys.
*Source: Code review 2026-06-13*

### Notifications

#### Add "Enrichment started" notification from Phase 3 ECS task
Distinguishes "task launched" (before download) from "enrichment in progress" (real work starting).
*Source: end-2-end-1 observation*

#### Add "still waiting" notification for long-running batches
Operator blind to stalled batches (>4h with no status change).
*Source: PIPELINE_REVIEW.md*

### CI/CD & DevOps

#### Register pytest markers (slow, requires_api)
Add to `pyproject.toml` — generates warnings on every test run.
*Source: QA review 2026-06-13*

#### Fix find_related_groups.py mypy errors
2 trivial fixes. Shows up in every lint run.
*Source: QA lint runs 2026-06-04+*

#### Add secrets scanning to CI
detect-secrets or gitleaks.
*Source: QA_GAPS.md*

---

## Low Priority (code quality, minor improvements)

### Code Quality

#### Finish ecs_entrypoint.py → ecs_modules/ extraction
1858 lines. `_download_inputs` and `_notify_complete` simplified (this session). More can be extracted.
*Source: QA radon 2026-06-13*

#### Consolidate find_duplicate_*.py scripts
All 4 share load→normalize→score→filter→report pattern. Strategy pattern or shared base would cut ~40% code.
*Source: QA review 2026-06-13*

#### Refactor phase3_enrich_data.py:main (D(23) complexity)
Extract per-entity-type dispatch into registry dict.
*Source: QA radon 2026-06-13*

#### Log when `_dedup_has_no_pending()` returns True due to missing reports
Distinguishes "no duplicates found" from "dedup didn't run".
*Source: Code review 2026-06-13*

#### Fix `.gitignore` trailing newline + run `black` on 6 scripts
*Source: Code review 2026-06-13*

### Performance

#### Reduce image memory usage in equipment.py
Image + base64 = ~4x memory. 80MB for a 20MB image.
*Source: CODE_REVIEW.md*

#### DynamoEntityStore.query_unenriched does full table scan
FilterExpression with `begins_with` on partition key = full scan. Add GSI on `entity_type` + `enrichment_status`.
*Source: CODE_INTEGRITY_REVIEW.md #14*

### Testing

#### Fix test_batch_poller.py fixture pattern
7 unused `dynamodb_table` variables — use `@pytest.fixture(autouse=True)`.
*Source: QA vulture 2026-06-12*

#### Add mutation testing
`mutmut` to find weak assertions.
*Source: QA review 2026-06-13*

### DevOps

#### CloudFormation drift detection
*Source: DEVOPS_RECOMMENDATIONS.md*

#### Evaluate residential proxy for OpenSERP
Search engines block AWS IPs. **Preferred: Tailscale exit node** on home machine. Free, no third-party trust, ~20-50ms latency. Alternative: paid SERP API ($50-100/mo).
*Source: end-2-end-1 Phase 3 observation*

---

## Future / Research

#### True multi-job concurrency
Multiple books in parallel (separate ECS tasks per book). Requires per-book locking, shared DynamoDB entity store, dedup coordination.

#### Grok function calling for Phase 3 enrichment
Blocked on: function calling support in batch API.

#### Step Functions pipeline orchestration
Replace SNS→SQS→Lambda→ECS with Step Functions.

#### New entity types: Economic Data, Policy/Legislation
Prompts and schemas drafted in `dataquality/new_entity_types.md`.

#### UK National Archives (Discovery API)

---

## Completed (2026-06-09 through 2026-06-13)

#### ~~Phase transition reliability: explicit Phase 1→2 trigger~~ ✅ Fixed
`_post_process(phase1)` now explicitly invokes trigger Lambda with `phase: 2`. No longer relies on S3 notification chain.

#### ~~Batch poller lost track of jobs after marking "complete"~~ ✅ Fixed
New state machine: `pending → ready → retrieved`. Poller retries `ready` jobs if retrieve fails.

#### ~~Phase 3 NameError on output_root~~ ✅ Fixed
Changed `output_root` → `args.output_dir` in phase3 results writing.

#### ~~Stale lock TTL too long (24h)~~ ✅ Fixed
Reduced to 2h. DynamoDB TTL auto-expires abandoned locks.

#### ~~Auto-trigger Phase 3 when dedup finds zero duplicates~~ ✅ Done
`_dedup_has_no_pending()` checks reports; if all zero, triggers Phase 3 directly.

#### ~~Reconciliation interval too slow (1h)~~ ✅ Fixed
Changed from 1h → 15 min. Batch poller from 15 min → 5 min.

#### ~~Timing values hardcoded~~ ✅ Fixed
CloudFormation parameters: `BatchPollerIntervalMinutes`, `ReconciliationIntervalMinutes`, `NatWaitSeconds`, `TeardownDelayMinutes`.

#### ~~Duplicate retrieve tasks launched by poller~~ ✅ Fixed
`_trigger_retrieve` checks for running tasks before launching.

#### ~~Event extraction: flat response not wrapped~~ ✅ Fixed
AI returns `{EventID, Sub-events}` → code wraps to `{Event: {EventID, Sub-events}}`.

#### ~~Event extraction: missing Chapter field~~ ✅ Fixed
Injects `Chapter` from parsed_data before validation.

#### ~~BatchModeCollecting miscounted as failures~~ ✅ Fixed
`batch_parallel.py` catches `BatchModeCollecting` separately.

#### ~~No non-batch fallback when all events cached~~ ✅ Fixed
If submit-only produces 0 batch requests, falls through to non-batch re-run.

#### ~~Input validation before LLM calls~~ ✅ Done
`_validate_prompt`: empty check, unfilled placeholder detection, size limit, empty data section detection.

#### ~~Empty text guards in extractors~~ ✅ Done
People, places, dates, weather return early with debug log when fulltext is empty.

#### ~~Prompt YAML schema bugs~~ ✅ Fixed
`equipment.yaml` malformed YAML, `casualties.yaml`/`weather.yaml` used `{{` instead of `{`.

#### ~~Entity context wastes tokens when empty~~ ✅ Fixed
Casualties `entity_context` omits empty sections entirely.

#### ~~Sub-event fulltext fallback~~ ✅ Done
`_reconstruct_fulltext` uses summary as fallback when paragraph lookup yields empty fulltext.

#### ~~Case-insensitive book name fallback~~ ✅ Done

#### ~~Prompt schema alignment test~~ ✅ Done

#### ~~Empty content guard tests~~ ✅ Done

#### ~~Validate prompt tests~~ ✅ Done

#### ~~Remove stray @retry decorator on _validate_prompt~~ ✅ Fixed

#### ~~OpenSERP torn down before Phase 3~~ ✅ Fixed
ECS tasks cancel stale teardowns; idle monitor checks for running tasks.

#### ~~Simplify _download_inputs and _notify_complete~~ ✅ Done
Extracted into focused helpers: C(20)→C(5) dispatcher, C(18)→C(8) main.

---

## Completed (2026-06-03 through 2026-06-07)

#### ~~Phase 4: DynamoDB as primary entity storage~~ ✅ B/C/D implemented
Phase A (core + dual-write), Phase B (DynamoDB materializer), Phase C (dedup reads), Phase D (S3 export-only).

#### ~~S3 sync does not delete merged/removed entity files~~ ✅ Fixed

#### ~~People dedup: first name alone insufficient~~ ✅ Fixed

#### ~~Normalize abbreviated ranks~~ ✅ Fixed

#### ~~Incremental dedup~~ ✅ Implemented

#### ~~Add `additionalProperties: false` to schemas~~ ✅ Done

#### ~~Delayed networking teardown kills retrieve tasks~~ ✅ Fixed

#### ~~Pending content queue not cleared~~ ✅ Fixed

#### ~~NAT availability check times out~~ ✅ Fixed

#### ~~Phase 3 completion notification~~ ✅ Fixed

#### ~~Phase started notifications~~ ✅ Fixed

#### ~~DynamoDB cache preload~~ ✅ Done

#### ~~_complete_metadata() bypasses batch mode~~ ✅ Fixed

#### ~~Phase 2 reads pending#parsed~~ ✅ Fixed

#### ~~Optional entity extraction parallelized~~ ✅ Done

#### ~~Reduce prompt fulltext for dates~~ ✅ Done

#### ~~Split large prompts into batches~~ ✅ Done

#### ~~Phase 3 retry: exclude not_found~~ ✅ Fixed

#### ~~Fix manifest read-modify-write race~~ ✅ Fixed

#### ~~Fix non-paginated DynamoDB cache clear~~ ✅ Fixed

#### ~~Move SecretsManager VPC endpoint to dynamic set~~ ✅ Done

#### ~~Replace silent exception swallowing~~ ✅ Done

#### ~~Batch submitted notification~~ ✅ Done

#### ~~Phase 1 incremental vs full context~~ ✅ Done

#### ~~Unify schema versioning~~ ✅ Done

#### ~~Fix scheduler variable scope~~ ✅ Fixed

#### ~~Dedup guard: check recently completed batches~~ ✅ Done

#### ~~Remove hardcoded region~~ ✅ Done

#### ~~Fix prompt_loader str.format() breaking on JSON braces~~ ✅ Fixed

#### ~~Fix json_validator._fix_invalid_ulids mutating input~~ ✅ Fixed

#### ~~Refactor ecs_entrypoint.py~~ ✅ Extracted (ecs_modules/)

#### ~~Refactor equipment.py~~ ✅ Extracted (equipment_ext/)

#### ~~Include entity counts in notification~~ ✅ Done

#### ~~Expand Lambda handler tests~~ ✅ Done

#### ~~Local end-to-end simulation test~~ ✅ Done

#### ~~Dedup UI: batch-commit with undo~~ ✅ Done

#### ~~AWS cost quick wins~~ ✅ Done

#### ~~Scripts cleanup~~ ✅ Done

#### ~~Fix EIP leak on NAT delete~~ ✅ Fixed

#### ~~Disk space checks~~ ✅ Done

#### ~~Backup before dedup merge~~ ✅ Done

#### ~~Operations runbook~~ ✅ Done

#### ~~Re-extract with updated prompts~~ ✅ Done

#### ~~Resolve weather places~~ ✅ Done

#### ~~Periodic re-search of not_found~~ ✅ Already implemented
