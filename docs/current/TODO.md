# Pipeline Backlog

**Last Updated:** 2026-06-15

---

## Critical (actively losing data or breaking pipeline)

_None — all critical items resolved._

---

## High Priority (produces wrong results or wastes significant resources)

#### ULID fix generates different replacements for same invalid ID
Same invalid ULID referenced in multiple places within one response gets different replacements, breaking internal referential integrity. Fix: build replacement map and reuse same new ULID for repeated occurrences.
*Source: CODE_INTEGRITY_REVIEW.md #5*

---

## Medium Priority (efficiency, observability, developer experience)

### Pipeline Efficiency

#### Batch ALL entity types, not just events
Currently only events go to Batch API (50% savings). People, places, groups, dates, and optional entities still use live calls. Design: submit-only collects ALL requests into batch, retrieve-only re-runs with full cache. Saves ~60% of API costs.
*Source: Ardennes debugging 2026-06-13*

### Prompts & LLM Integration

#### ~~Prompt versioning (cache invalidation)~~ ✅ Fixed
Cache key hash now includes system_prompt. Any YAML change (prompt or system prompt) auto-invalidates.

---

## Low Priority (code quality, minor improvements)

#### Finish ecs_entrypoint.py → ecs_modules/ extraction
2473 lines. Proposed splits: `notifications.py` (~110 lines), `dedup.py` (~280 lines), `locks.py` (~150 lines). Would reduce entrypoint to ~1900 lines.
*Source: QA radon 2026-06-13*

#### Consolidate find_duplicate_*.py scripts
Strategy pattern or shared base would cut ~40% code.
*Source: QA review 2026-06-13*

#### Refactor phase3_enrich_data.py:main (D(23) complexity)
*Source: QA radon 2026-06-13*

#### Fix `.gitignore` trailing newline + run `black` on 6 scripts
*Source: Code review 2026-06-13*

#### Reduce image memory usage in equipment.py
*Source: CODE_REVIEW.md*

#### DynamoEntityStore.query_unenriched does full table scan
Add GSI on `entity_type` + `enrichment_status`.
*Source: CODE_INTEGRITY_REVIEW.md #14*

#### Per-book queue ordering not guaranteed
DynamoDB scan order is undefined. Low impact (sequential processing, just affects which book goes first).
*Source: Review 2026-06-14*

#### Hard-coded step lists in Phase 3 notifications
`_build_phase_section` and `_update_lock_status` duplicate the enrichment step list. Could drift.
*Source: Review 2026-06-14*

#### CloudFormation drift detection
*Source: DEVOPS_RECOMMENDATIONS.md*

#### Evaluate residential proxy for OpenSERP
Tailscale exit node preferred. Free, no third-party trust.
*Source: end-2-end-1 Phase 3 observation*

---

## Regression Protection

#### Smoke test CI job — full pipeline on chapter99
Run Phase 1→2→3 end-to-end with real file I/O on test chapter. Catches multi-phase coordination bugs that unit tests miss (threading, file locks, phase transitions).
*Source: QA regression review 2026-06-15*

#### Thread-safety stress test (50 threads, @pytest.mark.slow)
Current `test_event_mention_race.py` uses 10 threads. Add variant with 50 threads for CI only. Race conditions often only manifest under load.
*Source: QA regression review 2026-06-15*

#### Expand prompt-schema contract tests to all 27 YAMLs
Assert every YAML has valid `prompt_template`, `schema` (parseable JSON), `system_prompt`. Verify all `{placeholders}` in templates have matching function arguments. Currently only events + 5 types covered.
*Source: QA regression review 2026-06-15*

#### Phase coordination integration test
Mock 2 concurrent phases and verify: Phase 3 can't tear down NAT while Phase 2 lock held, per-book queue processes correctly, lock status updates at each step.
*Source: QA regression review 2026-06-15*

#### Snapshot/golden tests for merge output
If `_merge_person`, `merge_generic`, or `update_event_refs` change behavior, output could silently drift. Add golden file assertions: input A + B → expected merged C.
*Source: QA regression review 2026-06-15*

#### Enforce mypy --strict on new files only
Prevent `find_related_groups.py`-style issues from accumulating. Don't enforce on legacy but require new code to pass strict mode.
*Source: QA regression review 2026-06-15*

---

## Future / Research

#### True multi-job concurrency
Multiple books in parallel. Requires per-book locking, shared DynamoDB entity store, dedup coordination.

#### Grok function calling for Phase 3 enrichment
Blocked on: function calling support in batch API.

#### Step Functions pipeline orchestration
Replace SNS→SQS→Lambda→ECS with Step Functions.

#### New entity types: Economic Data, Policy/Legislation
Prompts and schemas drafted in `dataquality/new_entity_types.md`.

#### UK National Archives (Discovery API)

#### Model routing expansion (grok-3-mini for simple extractors)
Dates, places, weather, casualties, logistics could use cheaper model. Needs A/B testing with quality metrics before rolling out.
*Source: Prompt deep dive 2026-06-14*

#### Prompt version in cache key
Enables safe prompt iteration without manual cache invalidation.
*Source: Prompt deep dive 2026-06-14*

#### Cost quantification for model_map expansion
Token counts and per-run cost estimates needed for ROI analysis.
*Source: Kiro evaluation 2026-06-14*

#### Retry escalation (cheap model → expensive model on validation failure)
Undefined policy for when grok-3-mini fails validation. Retry same or escalate?
*Source: Kiro evaluation 2026-06-14*

---

## Completed (2026-06-15)

#### ~~BatchCollector thread safety~~ ✅ — `threading.Lock` added to `.add()`
#### ~~DynamoDB dual-write reconciliation~~ ✅ — wired into `_materialize_from_dynamo`
#### ~~Supplemental_Materials key normalization (all paths)~~ ✅ — 5 entry points normalized
#### ~~Bibliography resource_urls array~~ ✅ — prompt + sanitizer + code aligned
#### ~~Event mention dedup~~ ✅ — checks (EventID + book + Sub_event_Name)
#### ~~merge_generic cross-ref updates~~ ✅ — `update_event_refs` + targeted JSON replacement
#### ~~Optional extractor idempotency~~ ✅ — `.processed_events.json` markers
#### ~~Phase results zero-count warning~~ ✅ — logs + SNS notification
#### ~~BatchModeCollecting explicit catch~~ ✅ — DEBUG in all 4 optional extractors
#### ~~Truncation recovery~~ ✅ — `GrokTruncationError` + `extract_with_chunk_halving`
#### ~~update_event_refs corruption~~ ✅ — `_replace_id_in_obj` (recursive, field-targeted)
#### ~~Phase 1 chunk overlap~~ ✅ — 3-paragraph overlap between splits
#### ~~System prompt centralization~~ ✅ — all use `get_system_prompt(name)` from YAML
#### ~~All prompts externalized~~ ✅ — 27 YAML files, hard fail on missing
#### ~~EntityCreatedTopic~~ ✅ — removed (redundant, dead handler cleared)
#### ~~Enrichment started notification~~ ✅ — SNS after downloads, before API calls
#### ~~Stalled batch notification~~ ✅ — after 4h, hourly, with progress n/n
#### ~~Secrets scanning in deploy~~ ✅ — gitleaks/detect-secrets, blocking
#### ~~Scripts portability~~ ✅ — ENV_NAME/AWS_DEFAULT_REGION
#### ~~Orchestration tests~~ ✅ — 14 tests (locks, sync, threads, loaders)
#### ~~Prompt schema alignment expanded~~ ✅ — 29 tests, output schema validation
#### ~~BackgroundSync skips enriched files~~ ✅ — `_downloaded_keys` tracks mtime, re-uploads modified
#### ~~Search queries externalized~~ ✅ — `search_queries/*.yaml` + loader + deploy validation

---

## Completed (2026-06-13 through 2026-06-14)

#### ~~additionalProperties: false drops casualties/equipment~~ ✅ Fixed
Added CasualtyID, date_string, impacted_* to CASUALTY_ITEM_SCHEMA. Added 8 fields to PEOPLE_GROUP_ITEM_SCHEMA.

#### ~~Supplemental_Materials key mismatch (primary path)~~ ✅ Fixed
Prompt updated to singular, `sanitize_supplemental_data()` normalizes plural→singular.

#### ~~index.json race condition~~ ✅ Fixed
Per-directory `threading.Lock` wraps read-modify-write. `_build_date_id_lookup` outside lock.

#### ~~BackgroundSync uploads partially-written files~~ ✅ Fixed
Skips `.tmp` files + requires mtime stable >2s before upload.

#### ~~DynamoDB dual-write silent failures~~ ✅ Partial
Upgraded to WARNING, added `_track_failed_write` tracking. Reconciliation not yet wired.

#### ~~Phase 3 no-op on review-all-data~~ ✅ Fixed
`force_reprocess` now skips DynamoDB materialization, downloads full S3 corpus.

#### ~~Phase 3 tears down NAT while Phase 2 running~~ ✅ Fixed
`_teardown_networking` and `_stop_openserp_if_running` check for other phase locks before tearing down.

#### ~~NAT guardrail kills networking during long Phase 3~~ ✅ Fixed
openserp_manager checks `_any_lock_held()` before force teardown.

#### ~~EIP leak from overnight bounce loop~~ ✅ Fixed
`_create_nat` releases orphaned EIPs before allocating. Fresh EIP each time (reputation isolation).

#### ~~Reconciliation launches Phase 3 with no book (network thrash)~~ ✅ Fixed
Only relaunches if `pending#enrich#*` queue has entries. Uses book name from queue.

#### ~~Per-book queues (Phase 2 + Phase 3)~~ ✅ Implemented
`pending#parsed#{book}`, `pending#enrich#{book}`. Networking stays up between books.

#### ~~Prompts externalized to YAML~~ ✅ Done
people_groups, casualties, logistics, weather, biography — no inline fallback, fail on missing YAML.

#### ~~Deploy-time prompt validation~~ ✅ Done
11 YAML files validated (existence, template, schema JSON) — blocks deploy on failure.

#### ~~Phase 3 lock status + book name~~ ✅ Done
Lock item includes `book` and `status` fields. Phase 3 updates status at each step.

#### ~~Download progress logging~~ ✅ Done
Every 500 files with elapsed time. Total count shown upfront.

#### ~~Cache hit periodic logging~~ ✅ Done
Every 100 hits at INFO level.

#### ~~Trivy blocking + pip-audit fallback~~ ✅ Done
Deploy fails on HIGH/CRITICAL vulnerabilities. Podman support added.

#### ~~Entity validation skip list~~ ✅ Fixed
`index.json`, `duplicate_report.json`, `not_duplicates.json`, `not_people.json`, `not_related.json`, dotfiles excluded from entity schema validation.

#### ~~phase3_enrich_data.py missing `import json`~~ ✅ Fixed

---

## Completed (2026-06-09 through 2026-06-13)

_(See git log for details — 23 items including Phase transitions, batch states, empty guards, prompt fixes)_

---

## Completed (2026-06-03 through 2026-06-07)

_(See git log for details — 30+ items including DynamoDB entity store, incremental dedup, cost optimizations)_
