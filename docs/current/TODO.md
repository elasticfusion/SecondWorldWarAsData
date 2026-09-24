# Pipeline Backlog

**Last Updated:** 2026-09-24

---

## Critical (actively losing data or breaking pipeline)

_None — all critical items resolved._

---

## Recently Completed (2026-09-23) — OCR reliability + table recovery

_All deployed and validated end-to-end on the St. Vith / Boyer book (252-page
scanned PDF)._

#### ~~Chandra OCR killed by 1h wall-clock timeout on healthy dense chunks~~ ✅ Fixed + deployed
Progress-watchdog (`scripts/ocr_watchdog.py`) is now the primary failure
detector — fails on no-page-progress for `OCR_NO_PROGRESS_SECS` (900s), not
elapsed time. `AttemptDurationSeconds` raised 3600→14400 as a loose backstop.
Wired via `chandra_entrypoint.sh` + `Dockerfile.chandra`; 7 tests. Chandra image
rebuilt+pushed, CFN job def at rev 13 with the new env/timeout.
*Source: St. Vith end-to-end test 2026-09-23*

#### ~~submit_ocr_job.py chunk-000 collision overwrites prior OCR output~~ ✅ Fixed
Page-range/manifest re-runs all wrote to `chunk-000/` (submission-local index),
silently clobbering earlier chunks. Output dir now derived from the page range
(`chunk-p0001-0050`, zero-padded/sortable) in all submit paths + publish.
*Source: St. Vith end-to-end test 2026-09-23*

#### ~~Auto-route flattened 2-D tables to PP-StructureV3 after OCR~~ ✅ Fixed + validated
`src/ingestion/chunk_pages.py` maps `--paginate_output` chunk markdown to
physical PDF pages and flags pages with a flattened task-org table;
`submit_ocr_job.py:auto_route_table_recovery()` fires after OCR (best-effort,
`--no-recover-tables` opt-out), submitting one Paddle job per flattened page.
Validated: OCR → detect pp.154/155/156 → recovery submitted with no manual
steps; p155 produced a reconstructed `<table>`.
*Source: St. Vith end-to-end test 2026-09-23*

#### ~~PP-StructureV3 recovery recovered 0 tables (libgomp1 missing)~~ ✅ Fixed + deployed
Paddle worker crashed at import (`libgomp.so.1: cannot open shared object
file`) → CPU fallback → also crashed. `Dockerfile.paddle` now installs
`libgomp1`. Image rebuilt+pushed; re-run confirmed `device=gpu` and a recovered
table.
*Source: St. Vith end-to-end test 2026-09-23*

#### ~~NAT torn down while async recovery jobs still pending~~ ✅ Fixed
`auto_route_table_recovery` submits Paddle jobs asynchronously; `--wait` used to
tear NAT down as soon as the Chandra job finished, stranding recovery jobs
(which need NAT for first-run model download). `_wait_and_publish` now waits for
the recovery jobs to drain before releasing networking (Option B —
"networking stays up while work pending").
*Source: St. Vith end-to-end test 2026-09-23*

#### ~~Chandra image full rebuild fails on torch CUDA-dep hash drift~~ ✅ Fixed
`docker build --no-cache -f Dockerfile.chandra` failed at the `torch==2.5.1+cu121`
install: pip resolved torch 2.5.1's declared CUDA deps (`nvidia-cudnn-cu12`,
`nvidia-cusparse-cu12`, …) whose upstream hashes on the pytorch CDN no longer
matched torch's recorded metadata (a different nvidia-* pkg failed each run).
Fix: install torch/torchvision `--no-deps` (skip that resolution), then install
the exact CUDA runtime deps torch needs (incl. `nvidia-cudnn-cu12==9.1.0.70`)
from PyPI, which has no stale hash-file enforcement; a build-time `import torch`
asserts the result loads. Verified in isolation. The `Dockerfile.chandra.overlay`
is no longer needed for a full rebuild (kept as a fast code-only-change helper).
*Source: St. Vith end-to-end test 2026-09-23*

---

## High Priority (produces wrong results or wastes significant resources)

#### ~~OCR page-range off-by-one dropped the first page of every range~~ ✅ Fixed (code); re-OCR needed
`submit_ocr_job.py` emitted **1-based** `--page-range` values, but Chandra's
`--page-range` is **0-based**, matched against 0-based PDF page indices
(`chandra.input.parse_range_str("1-2")` → `[1,2]`). So every OCR job **dropped
the first page of its range and read one page past the end**. Auto-chunk runs
(e.g. St. Vith `1-50`, `51-100`, …) were each shifted by one page — physical
page 1 of every chunk missing, chunk boundaries off by one; a single-page/small
range dropped its first page entirely (found via the M1019 2-page test: only
page 2 OCR'd). Fix: `_to_chandra_range` converts physical N → index N-1 at the
Chandra arg only (chunk-dir naming, logs, manifests, merge mapping stay 1-based
physical). Verified against Chandra's own parser; 6 tests.
**Re-OCR consequence (open):** all OCR output produced before this fix is
off-by-one and should be **re-OCR'd** to be correct — notably the St. Vith /
Boyer corpus and any ETO OOB Chandra markdown generated via this path. Scope +
cost TBD; the fix is deployed-pending (ships in the Chandra path with the next
`submit_ocr_job` run — no image rebuild needed, it's a client-side arg change).
*Source: M1019 language-path investigation 2026-09-24*

#### Deploy + wire the OCR markdown-review UI
The markdown-review Lambda + UI is **built and tested but not deployed**
(commit d1c9869): `lambda_handlers/mdreview_ui_handler.py` (two-pane page image
+ editable snippet, saves to `ocr-output/{book}/reviewed/pN.md`), merge-time
substitution + consume-once in `submit_ocr_job.merge_outputs`, and CFN
(`MdReviewUIFunction` + `/mdreview` routes on the existing DedupApi). Remaining:
1. **Deploy** — rebuild the Lambda code bundle + main-stack CFN update
   (`deploy_all.sh` main path), then smoke-test the `/mdreview` route (basic
   auth via the shared dedup authorizer).
2. **Direct UI↔pipeline wiring (deferred by design)** — today the UI and merge
   are decoupled via the `reviewed/` prefix. Decide whether a reviewer save
   should trigger anything (e.g. re-merge/re-publish) or stay pull-based at the
   next OCR/merge run.
3. Confirm reviewers can discover the URL + credentials (same pattern as dedup).
*Source: human-review compromise for layout-miss task-org tables, 2026-09-24*

#### Layout-miss task-org tables (e.g. p156) → human review (DECIDED 2026-09-24)
**Decision:** sparse/borderless task-org pages that PP-StructureV3's layout
detector classifies as `text` (no `table`-class box — verified on p156 via
`layout_boxes`: text@0.49/0.43 + paragraph_title@0.32, zero table boxes) are
**resolved by human review, not further recovery-engine work**. Ruled out:
`layout_threshold` (nothing to admit) and orientation tuning. This matches the
pipeline's existing "flag, don't fabricate" compromise. The page's structure is
preserved in the recovery JSON `flattened_hints` (group→units, `needs_review`);
`flattened_hints` carry content+grouping, NOT a reconstructed 2-D grid — a
reviewer supplies the column crosstab. See CHANDRA_OCR_DESIGN.md "Recovery
limits & the human-review compromise". Dense tables (p155, OOB corpus generally)
still auto-recover to `<table>`.

**Open follow-up (path being decided by owner):** wire flagged pages'
`flattened_hints` into the existing review surface (with page image + parsed
groups) so a human actually sees and completes them — the gap between "flagged"
and "reviewed". Not yet designed. Escape hatches if a specific page is
high-value: (A) image preprocess to make the grid table-like; (B) crop to the
columnar region and run the table-structure model directly (bypass layout
detection). Also: residual recovered-cell OCR noise (`1703dd -arch South`).
*Source: St. Vith end-to-end test 2026-09-23/24*

#### ULID fix generates different replacements for same invalid ID
Same invalid ULID referenced in multiple places within one response gets different replacements, breaking internal referential integrity. Fix: build replacement map and reuse same new ULID for repeated occurrences.
*Source: CODE_INTEGRITY_REVIEW.md #5*

---

## Medium Priority (efficiency, observability, developer experience)

### Pipeline Efficiency

#### Batch ALL entity types, not just events
Currently only events go to Batch API (50% savings). People, places, groups, dates, and optional entities still use live calls. Design: submit-only collects ALL requests into batch, retrieve-only re-runs with full cache. Saves ~60% of API costs.
*Source: Ardennes debugging 2026-06-13*

#### Bibliography resolver processes duplicate citations redundantly
Same citation referenced by multiple sub-events is processed N times (NARA identify + search). Cache prevents duplicate API calls but generates log noise (4x identical log lines). Fix: deduplicate by citation text before resolution loop.
*Source: Phase 3 log observation 2026-06-16*

#### Narrative content misclassified as document_reference reaches NARA resolver
Footnotes with factual narrative (e.g., "In October 1941, the Germans had discussed...") are being sent to NARA identification. Two fixes needed: (1) improve supplemental.yaml classification prompt to better distinguish narrative from citations, (2) add guard in bibliography_resolver to skip text that doesn't match citation patterns (no author/title/date structure).
*Source: Phase 3 log observation 2026-06-16*

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

#### OCR structural fidelity: raise render DPI + evaluate augmenting Chandra
Chandra runs at the 192 render-DPI baseline (`chandra/settings.py:IMAGE_DPI`,
not exposed on the CLI); its own benchmark recommends 300. Measured: 192→300 =
+83% usable detail (3.41→6.24 MP the model actually sees); the
`scale_to_fit(max_size=(3072,2048))` ceiling caps gains above 300. **Do:** (1)
empirically diff 192-vs-300 re-OCR of the hard St. Vith pages (p155 2-D
task-org table, p103 casualty table) via `tmp/dpi_probe.py`; if 300 helps, make
it the corpus-wide default (override the library setting — no CLI flag exists).
(2) For the persistent structural blind spots that DPI won't fix (2-D task-org
flattening, block-quote markup loss), evaluate augmenting Chandra with a
dedicated table engine — **PaddleOCR PP-StructureV3** (Apache-2.0, table
structure + multi-column reading order) is the first candidate; Docling as a
cross-check; ensemble-disagreement → `needs_review`. Full analysis in
`docs/current/dataquality/CHANDRA_OCR_DESIGN.md` (Render DPI / Augmenting
Chandra sections).
*Source: OCR fidelity investigation 2026-09-22*

#### Postgres dialect adapter for src/loader (Aurora target)
The `output/` → relational loader (`src/loader/`) currently runs **SQLite only**,
though its docstring claims "works with any PEP-249 connection ... psycopg
against Aurora." Three SQLite-specific spots block Postgres: `create_schema`
uses `conn.executescript` (not in psycopg), `_insert` uses `INSERT OR REPLACE`
(Postgres needs `ON CONFLICT (pk) DO UPDATE`), and `?` placeholders (psycopg
uses `%s`). `transform.py` is already DB-agnostic, so this is a contained seam,
not a rewrite. Also missing: `schema_pg_extras.sql` (pgvector/PostGIS/HNSW),
referenced in a comment but not on disk. Defer the full adapter until the Aurora
target is provisioned (untestable before then) and the embedding dimension is
settled (`content_chunks VECTOR(?)` depends on the chosen embedder). **Done
(2026-09-22):** `load.py` docstring corrected to "SQLite today; Postgres via a
planned dialect adapter," and a `_require_sqlite` guard now rejects non-sqlite
connections with a clear `NotImplementedError` (tested). Remaining: the adapter
itself + `schema_pg_extras.sql`.
*Source: loader schema-gap review 2026-09-22*

#### Reprocess already-imported data after Phase 0 routing lands
Once the format-agnostic ingestion + media classification work is built (see
`docs/current/dataquality/STRUCTURED_DATA_ROUTING.md`), build a specialized
audit-and-reimport script that:

> **Status (2026-09-18):** the Phase 0 ingestion front-end + OOB scanned-table
> parsers are now built (`src/ingestion/`, see
> `docs/current/dataquality/INGESTION_FRONT_END.md`). This reprocess/backfill
> script remains pending and should run once Phase 0 is wired as a runnable
> phase and the entity-convergence bridge lands.

1. **Audits existing output** — scans already-extracted content and entities for
   sources affected by the pre-Phase-0 gaps: embedded image-maps that were lost
   between `images.py` and `maps.py`, images misclassified by keyword-only
   `_classify_content_type`, images dropped by `alt_text` dedup, and any
   sources that never parsed (raw PDFs sitting in `contentrepository/`).
2. **Reports** what would change (per source: images recovered, maps
   reclassified, entities newly created) before touching anything.
3. **Re-attempts import** through the finished Phase 0 pipeline, feeding results
   into dedup so recovered media/entities merge with existing ones rather than
   duplicating.
Applies across ALL sources (not ibiblio-specific) — the underlying fixes are
made once in the shared parser/media path, and this script backfills everything
imported before the fix.
*Source: Structured-data routing analysis 2026-06-30*

#### Amazon metadata enrichment for confirmed books
For bibliography entries confirmed as published books, search Amazon.com for metadata: book cover image, ISBN, edition info, page count, publisher details. Supplements Archive.org/Gutenberg data with commercial metadata.
*Source: Search debug session 2026-06-17*

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
