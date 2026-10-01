# Pipeline Backlog

**Last Updated:** 2026-09-26

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

#### NAT torn down between compute phases (persistent race) — should only drop at dedup gate + final completion
NAT teardown keeps firing **between compute phases**, stranding the next phase.
Observed live 2026-09-27: Phase 1 finished → PARSED_TOPIC cascade launched
Phase 2 (NAT create) but Phase 1's own teardown deleted NAT **~2s after** Phase
2 launched → Phase 2 ran with no networking, failed Grok/S3, was relaunched by
self-heal (~8 min lost). Prior "fixes" (lock-check before teardown) don't cover
this: **Phase 2's lock isn't held yet** when Phase 1 tears down in the S3-notif
cascade, so the guard passes.

**Intended lifecycle (confirmed with owner):**
- Phases 1 → 2 (and any compute→compute): NAT stays **UP continuously** — never
  torn down between compute phases.
- **Dedup gate: NAT SHOULD tear down** (async, indefinite human review) — this
  teardown is correct/wanted. Phase 3 re-creates NAT when review completes.
- **End of Phase 3 / job complete: final teardown.**

Rule to implement: tear down **only** at (a) the dedup gate and (b) final
completion — never between compute phases. Fix likely: gate `_teardown_networking`
/ submit-only teardown on "no downstream compute work queued" (check pending
content/parsed queues, not just current locks), or make the teardown decision
explicit per phase-transition type rather than per-phase-completion.
*Source: St. Vith AWS end-to-end run 2026-09-27*

#### Deploy + wire the OCR markdown-review UI
The markdown-review Lambda + UI is **built and tested but not deployed**
(commit d1c9869): `lambda_handlers/mdreview_ui_handler.py` (two-pane page image
+ editable snippet, saves to `ocr-output/{book}/reviewed/pN.md`), merge-time
substitution + consume-once in `submit_ocr_job.merge_outputs`, and CFN
(`MdReviewUIFunction` + `/mdreview` routes on the existing DedupApi). Remaining:
1. **Deploy** — rebuild the Lambda code bundle + main-stack CFN update
   (`deploy_all.sh` main path), then smoke-test the `/mdreview` route (basic
   auth via the shared dedup authorizer).
   **Deploy-path health (investigated 2026-09-27 — must clear before deploying):**
   the parent stack `wwii-pipeline-dev` is in **UPDATE_ROLLBACK_COMPLETE** (last
   attempt 2026-07-01: ComputeStack "Validation failed with 8 errors"). History
   of recurring ComputeStack update failures. Known causes seen in events:
   (a) `PipelineDashboard` name collision with EventsStack — **appears fixed**
   (compute.yaml now uses `-wwii-pipeline-logs` vs events' `-wwii-pipeline`),
   verify no other duplicate logical/physical names; (b) TaskDef
   "Container.image should not be null or empty" — deploy MUST pass
   `--pipeline-image`/`--openserp-image` (use the `deploy_all.sh` path, not a
   bare `deploy_aws.py`); (c) enumerate the July "8 validation errors" via
   `describe-stack-events` on ComputeStack before retrying. Do a **change-set /
   dry-run first**, and NOT during a live Phase 2/3 run (ComputeStack holds the
   phase task defs + the API).
2. **Direct UI↔pipeline wiring (deferred by design)** — today the UI and merge
   are decoupled via the `reviewed/` prefix. Decide whether a reviewer save
   should trigger anything (e.g. re-merge/re-publish) or stay pull-based at the
   next OCR/merge run.
3. Confirm reviewers can discover the URL + credentials (same pattern as dedup).
4. **NAT management — treat as an async human-review gate (like dedup).** The
   OCR markdown review is an **indefinite human pause**, exactly like the dedup
   gate. So NAT must follow the same lifecycle: **tear down NAT while waiting on
   the reviewer** (no compute is running; don't pay for idle NAT), and **bring
   it back up when review completes** and downstream work (re-merge/re-publish,
   or continued OCR) resumes. Mirror the dedup gate's teardown/resume mechanism
   (the delayed-teardown at the gate + recreate-on-resume) rather than holding
   NAT up. This ties into the "NAT torn down between compute phases" fix: the
   rule is NAT UP across compute, DOWN at async human gates (dedup **and** OCR
   markdown review), final teardown at completion.
*Source: human-review compromise for layout-miss task-org tables, 2026-09-24;
NAT-as-async-gate requirement added 2026-09-27*

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

#### ~~ULID fix generates different replacements for same invalid ID~~ ✅ Already fixed + now regression-tested
Same invalid ULID referenced in multiple places within one response gets different replacements, breaking internal referential integrity. Fix: build replacement map and reuse same new ULID for repeated occurrences.
**Resolution (2026-09-27):** verified already fixed — `src/utils/json_validator.py`
`_fix_invalid_ulids` builds a `replacement_map` keyed by the invalid value and
`_fix_ulids_recursive` reuses it, so repeated occurrences collapse to one ULID
(the dedup path `src/dedup/merge.py:_replace_id_in_obj` is likewise deterministic).
Behavioral check confirmed same-invalid→one replacement across nested
fields/lists; different-invalid→own id; input not mutated. Added regression test
`tests/test_ulid_replacement_consistency.py` (2 tests) to lock it in.
*Source: CODE_INTEGRITY_REVIEW.md #5*

#### SHAEF OB map corpus gaps → source acquisition (likely a NARA visit)
The `SHAEF OB Maps` daily situation-map set has coverage gaps that limit
date-based queries (e.g. "unit X on date Y → show the map"). These are
**acquisition** issues, not code — deferred; likely require obtaining the
missing sheets from NARA.
- **December 1944 entirely missing** — no `44-12` folder. The Ardennes/Bulge
  month is absent, so any query in Dec 1944 (e.g. "3rd Armored on 15 Dec 1944")
  is unanswerable from this set. Coverage is Oct–Nov 1944 + Jan–Apr 1945.
- **Missing 15 Feb 1945** — the `45-02` folder has 29 files (a single-day gap).
- **`311144 SHAEF OB.jpg` mislabeled** — filename encodes 31 November (does not
  exist); needs disambiguation (likely 30 Nov or 1 Dec) when the corpus is
  revisited.
- Interim behavior: the ingestion should return "no map for that date" rather
  than a wrong neighboring map. See
  `docs/current/dataquality/MAP_IMAGE_AV_INGESTION.md`.
*Source: B405 / SHAEF map assessment 2026-09-26*

#### Wire Phase 0 as an auto-triggered phase + type-routing (close the ingest→extract seam)
Adding a **raw** document today does **not** run source-to-finish. The narrative
markdown path auto-chains (S3 `content/` upload → Phase 1 → 2 → dedup gate → 3),
but for a raw document there are manual seams:
1. **Phase 0 / OCR is operator-run** — `submit_ocr_job.py` is invoked by hand on
   AWS Batch; a raw scanned-PDF upload does **not** auto-trigger OCR.
2. **Phase 0 → Phase 1 handoff is not chained** — OCR/converted markdown must be
   placed into the `content/`-trigger path manually; the "upload triggers Phase
   1" doc assumes markdown already exists.
3. **No document-type routing at ingest** — narrative → Phase 1/2/3 extraction;
   structured/reference (e.g. ETO OOB) → the deterministic `src/ingestion/
   oob_markdown/` parser track (script-invoked, not auto-triggered);
   maps/images/film → the vision branch (design only, unbuilt, see
   `MAP_IMAGE_AV_INGESTION.md`).
Work: auto-trigger OCR on raw-PDF upload, chain the Phase 0→1 output handoff, and
route by disposition (narrative vs. OOB vs. map/media) to the correct downstream
track. Note: the dedup review gate and OCR markdown-review UI are **intentional**
human gates, not seams to remove. Related: the tracked "reprocess after Phase 0
routing lands" item under Future/Research.
*Source: end-to-end wiring review 2026-09-26*

---

## Medium Priority (efficiency, observability, developer experience)

### Pipeline Efficiency

#### Pipeline notifications don't render in Slack (only email + alarms do)
Phase-complete / phase-FAILURE notifications (`_notify_complete` /
`_notify_failure` → `dev-wwii-phase2-complete`) publish **free-form SNS text**,
which AWS Chatbot / Amazon Q **silently drops** ("Event received is not
supported") — only CloudWatch **alarms** and supported structured events render
in Slack. Confirmed twice: the St. Vith Phase 2 **failure** emailed but never
reached `#wwii-pipeline-alerts`. Fix options: (a) route pipeline failures/
completions through a **CloudWatch alarm** (metric filter on the failure log or a
custom metric) so Slack renders them; or (b) a small **formatter Lambda**
subscribed to the topic that reposts as a Chatbot custom-notification schema; or
(c) SNS→Lambda→Slack webhook. Email delivery works today, so this is
observability, not correctness.
*Source: St. Vith Phase 2 failure 2026-09-27 (emailed, not Slacked)*

#### ECS task role missing `SNS:ListSubscriptionsByTopic` (notification preflight always warns)
The new `_preflight_notification_subscriptions` (non-blocking) hits
`AuthorizationError` — `dev-wwii-ecs-task-role` lacks `SNS:ListSubscriptionsByTopic`
on the notification topic, so the preflight can't actually verify subscriptions
and always logs its warning (then correctly continues). Add the read permission
to the task role (`cloudformation/iam.yaml`) so the preflight does its job.
Non-blocking; the run proceeds regardless.
*Source: St. Vith verification run 2026-09-27 log*

#### Misleading "0 processed, N failed" log in batch mode
In batch mode, the synchronous core-extraction step does **not** process inline —
it *collects* requests for the xAI Batch API. But it still logs
`Core extraction complete: 0 processed, 140 failed` / `Phase 2 complete: 0
processed, 140 failed` (70 events + 70 dates requests deferred to the batch),
which reads like a total failure when the batch actually submitted fine
(`num_requests: 70, num_success: 70`). Reword the batch-mode path to report
"N requests collected for batch" instead of "0 processed / N failed" so operators
(and the zero-count warning) don't false-alarm. Observability, not correctness.
*Source: St. Vith verification run 2026-09-27 — batch_bb0c1ba0 succeeded 70/70 while log said "140 failed"*

#### Confirm batch-poller sees ECS-submitted batch jobs (poller visibility)
The `dev-wwii-batch-poller` Lambda logged **"No pending batch jobs"** on every
5-min poll while `batch_bb0c1ba0` was submitted and completed (70/70) by the
running phase2 task. Likely fine for this run (the task handles submit→retrieve
in-process for the interactive path), but the **detached/async path** the
concurrency build relies on assumes the Lambda poller picks up ECS-submitted
batches. **Confirm the batch-job record key/table the task writes matches what
the poller scans** (`metrics#batch_...` was present; the poller may key on a
different `batch_job#`/pending marker). Must be resolved *before* the Step
Functions Map dispatcher depends on poller-driven retrieval.
*Source: St. Vith verification run 2026-09-27 — poller "No pending" vs live batch*

#### Delete stale legacy `chunk-NNN` OCR dirs superseded by re-OCR
Re-OCR runs (off-by-one fix) write new page-range chunk dirs
(`chunk-p0001-0050`, …) but leave the **old pre-fix `chunk-000`..`chunk-NNN`**
dirs in place. The merge correctly **skips** them ("legacy chunk dir without
page range — cannot map to physical pages"), so output is not polluted, but the
stale dirs are confusing clutter and could trip tooling that globs `chunk-*`.
Delete the superseded legacy dirs from
`s3://dev-wwii-data-pipeline/ocr-output/{source}/` after a re-OCR is verified.
Affected so far: `stvith_boyer_full` (chunk-000..005), and `ETO_Order_of_Battle`
(chunk-000..019) once it is re-OCR'd. Mildly destructive (S3 rm) — verify the
new page-range chunks + merged output first.
*Source: stvith_boyer_full re-OCR 2026-09-26*

#### Verify page-separator count vs. page count after OCR merge
The `stvith_boyer_full` re-OCR merged with **246 page separators for 252 pages**
(6 short). Likely blank/near-blank scan pages for which Chandra emits no
separator (B405 was a clean 13/14), but this should be **confirmed** before the
markdown feeds the OOB parsers / extraction — a missing separator shifts
per-page mapping. Add a lightweight post-merge check that reports
`separators vs. pages` and lists the pages with no separator for a quick
blank-page eyeball.
*Source: stvith_boyer_full re-OCR 2026-09-26*

#### Batch ALL entity types, not just events
Currently only events go to Batch API (50% savings). People, places, groups, dates, and optional entities still use live calls. Design: submit-only collects ALL requests into batch, retrieve-only re-runs with full cache. Saves ~60% of API costs.
*Source: Ardennes debugging 2026-06-13*

#### Bibliography resolver processes duplicate citations redundantly
#### ~~Bibliography resolver processes duplicate citations redundantly~~ ✅ Fixed 2026-09-27
Same citation referenced by multiple sub-events is processed N times (NARA identify + search). Cache prevents duplicate API calls but generates log noise (4x identical log lines). Fix: deduplicate by citation text before resolution loop.
**Done:** `resolve_bibliography_dir` now dedups by citation key (verbatim, else
author|title); identical citations resolve once and reuse the result (`deduped`
stat). Tested in `tests/test_bibliography_resolver_guard.py`.
*Source: Phase 3 log observation 2026-06-16*

#### ~~Narrative content misclassified as document_reference reaches NARA resolver~~ ✅ Fixed 2026-09-27 (guard)
Footnotes with factual narrative (e.g., "In October 1941, the Germans had discussed...") are being sent to NARA identification. Two fixes needed: (1) improve supplemental.yaml classification prompt to better distinguish narrative from citations, (2) add guard in bibliography_resolver to skip text that doesn't match citation patterns (no author/title/date structure).
**Done (guard, #2):** `_looks_like_citation` gates the resolvers — prose with no
bibliographic structure is marked `not_citation` and never sent to NARA
(preserves legitimate NARA lookups: any RG/archive-ref/structured entry still
resolves). **Still open (#1):** tighten `supplemental.yaml` classification prompt
to reduce misclassification upstream (defense in depth).
*Source: Phase 3 log observation 2026-06-16*

#### Bibliography human-disposition UI (review_queue.json)
`resolve_bibliography_dir` now writes `review_queue.json` — real citations that
could not be grabbed online (per the "grab what's legitimately online, human-
disposition the rest" intent). There is **no UI** to work that queue yet. Build a
disposition UI analogous to the dedup UI + OCR markdown-review UI: show the
structured citation + verbatim, let a human mark disposition (request-from-
archive, manual link, mark-not-a-source, etc.). **NAT: treat as an async human-
review gate like dedup + mdreview** (tear down while waiting, resume on
completion). Deploy alongside the other UIs (same ComputeStack deploy-path
caveats).
**Retrieval mechanism now exists (2026-09-27):** `src/enrichment/source_retrieval.py`
downloads legitimately-online resolved items into a **quarantine** area
(`bibliography/retrieved/pending_review`, deliberately outside content/ so Phase 1
can't auto-ingest), gated on the existing `config/domain_blacklist.yaml` (NOT a
strict allow-list — download unless blacklisted; human review decides import).
Marks entries `retrieved_pending_review` and **never auto-processes** them.
Legitimacy basis: the pipeline downloads to AI-summarize (Grok) + attribute, not
republish. The UI needs to: drive retrieval for queued/resolved items, show the
downloaded content, and let a human accept (→ move into processable corpus) or
reject. Mechanism is staged/tested but intentionally NOT auto-wired to a phase.
*Source: bibliography-resolution intent discussion 2026-09-27*

#### Archive.org metadata + legitimacy enrichment (free, no new key)
The resolver already calls the Archive.org Metadata API (`/metadata/{id}`) but
only extracts PDF url + page count. From the **same call** we can also capture
**publisher, ISBN, date, creator, rights/license** (actionable publication data
for the professional-historian audience even when not downloadable) and check
`access-restricted-item`/rights so only **legitimately** public items are
presented as "grabbed online" — lending-only/restricted ones route to the
human-disposition queue. Also consider OpenLibrary (book metadata) and Crossref
(journal DOIs) as free authoritative metadata sources. Additive to the resolver;
ties into the existing "Amazon metadata enrichment" future item.
*Source: bibliography-resolution intent discussion 2026-09-27*

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

#### Explore removing DynamoDB as a general data store (esp. Phase 3 bulk transfer)
Investigate whether DynamoDB is still earning its place, and scope removing it as
a *general* data store. Code review (2026-09-30) found it plays three distinct
roles — they must be evaluated separately, not lumped together:

1. **Bulk Phase 3 materialization (weak — prime candidate for removal).**
   `s3_sync._materialize_from_dynamo` calls `DynamoEntityStore.list_all` once per
   entity type = **11 sequential full-table `Scan`s** (`FilterExpression`
   `begins_with(cache_key, "entity#<type>#")`), deserializing every entity's
   `data` blob. A filtered `Scan` reads/bills the whole table then discards
   non-matches — the same "crawl everything" anti-pattern as the S3
   `list_objects_v2` + per-object `download_file` loop it's meant to beat, so it
   is unlikely to be a real bulk-transfer win. **This is the "S3 is slow on Phase
   3" path the removal question is really about.**
2. **Small coordination (KV-shaped, legitimately good on DynamoDB).** Manifests
   (`manifest#phase2`, `pending#parsed`) and the Phase 3 lock/status
   (`_update_lock_status`). Tiny; keep unless the whole store goes.
3. **Concurrency-safe entity merge (genuinely earns its keep).**
   `merge_entity` / `store_bibliography` use version-conditional writes so two
   books extracting the same entity (e.g. "Eisenhower") don't clobber each
   other's `event_mentions`. Comments note this replaced flock-guarded S3 JSON
   writes that were per-host, NOT cross-host safe. Removing this without a
   replacement reintroduces that race — see "True multi-job concurrency" and
   `CONCURRENCY_AND_NAT_SPEC.md`.

**Fix options for the Phase 3 transfer bottleneck (cheapest first), to benchmark
before deciding:**
- (a) **Parallelize the S3 transfer** — current `s3_sync` loop is serial
  `download_file`; use concurrent downloads / `aws s3 sync`. Lowest-risk quick
  win, zero architecture change; may erase the pain that motivated
  `_materialize_from_dynamo`.
- (b) **Consolidate into few large objects** — write one Parquet/NDJSON blob per
  entity type so Phase 3 pulls ~11 objects instead of thousands (attacks the
  actual cause: object count; Parquet adds columnar projection).
- (c) **If DynamoDB materialization stays, stop using `Scan`** — add a GSI on
  `entity_type` so `list_all` becomes a `Query`. (Overlaps the existing Low-Prio
  item "DynamoEntityStore.query_unenriched does full table scan".) Weaker than
  (a)/(b).

**Decision inputs to gather (not yet done):** measured Phase 3 timings +
corpus/object counts, DynamoDB-scan vs. parallel-S3 vs. Parquet crossover, and a
plan for preserving role #3's concurrency safety (Postgres row-level
locking/`ON CONFLICT`, or keep a minimal Dynamo lock table) if the general store
is removed. Ties into the Aurora/pgvector direction ("Postgres dialect adapter
for src/loader") — the eventual query store is Postgres, so DynamoDB here is
really ingestion-time coordination, not a query store.
**Status:** exploration only — nothing to implement yet (per owner 2026-09-30).
*Source: DynamoDB value review 2026-09-30*

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
**Spec written 2026-09-27:** `docs/current/dataquality/CONCURRENCY_AND_NAT_SPEC.md` — per-book locks, reference-counted NAT, limit-aware dispatcher pool (Grok rate + Fargate vCPU as binding limits), global dedup barrier, shared-entity-store hardening. Motivated by the WWIIArchives backlog (628 files / ~502 GB).

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
