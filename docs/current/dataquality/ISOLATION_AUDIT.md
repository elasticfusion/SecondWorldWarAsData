# Isolation Audit — Document-Level Parallelism Prerequisites

**Date:** 2026-09-28
**Model (operator):** N documents in flight simultaneously; each progresses through
phases **uninterrupted** (resources spin up on demand); **no overlap or confusion
between jobs** — unique Grok batches, unique internet lookups, unique outputs.
**Method:** read each per-doc isolation boundary; classify CONFIRMED-GOOD vs GAP.
**Gate to flip `MULTI_DOC_ENABLED=true`:** every GAP closed + a controlled multi-doc
test proving isolation.

---

## Isolation boundaries — findings

### ✅ CONFIRMED-GOOD (isolation holds under concurrency)

1. **Grok batch identity** — jobs keyed `batch_job#{batch_id}` (globally-unique xAI
   id) carrying `book`+`phase`; retrieve launched with `--retrieve-only <batch_id>`
   + `BOOK_NAME`. Batch *download* is correctly targeted per job — no wrong-batch
   fetch. (Spec §3.4.)

2. **Per-book phase locks** — `lock#book#{book}#{phase}` design lets different books
   run the same phase concurrently while one book can't double-run a phase.
   (Dispatcher path; primitive built.)

3. **NAT demand leases** — `nat#lease#{taskArn}` (TTL'd); NAT up iff aggregate
   demand > 0, down only at cluster-wide zero. Correct for 1 or N jobs. (nat_lease
   + nat_manager demand check — built + tested.)

4. **Entity store** — `DynamoEntityStore` conditional/atomic writes for cross-book
   entities (Eisenhower in many books merges, not races). Idempotent mention-append.
   (Built.)

5. **Output path per-doc** — Phase 2 scopes to `output/content/{book_name}/` when
   BOOK_NAME set; content/parsed outputs are book-namespaced. Extraction inputs
   scope to `contentrepository/{book}/`.

6. **Cluster-wide Grok rate limiter + credit gate** — shared limiter prevents N
   docs × parallel chapters from tripping 429s; credit gate caps spend. (Built +
   tested.)

### ⚠️ GAPS — must fix before flipping the switch

**G1. Launch idle-check race (VERIFIED live 2026-09-28).**
`_launch_phase1_if_idle` / the serial launch path checks "any task RUNNING?" then
launches. During NAT cold-start (task PROVISIONING, not yet RUNNING) a second doc's
event sees "idle" and launches the SAME phase → two tasks, shared-state stomp.
Observed: two phase2-extract tasks (book=B460 + book=all) launched together.
**Fix:** route ALL launches through the dispatcher / per-book-lock atomic claim
(SFN Map with per-book `lock#book#{book}#{phase}`), never the serial idle-check.
The per-book lock's conditional write is the serialization point.

**G2. OpenSERP premature shutdown under concurrency (VALIDATED 2026-09-28).**
OpenSERP is a SINGLE shared ECS service (`{env}-wwii-openserp`, scale 0↔1, one IP
:7001). Sharing the stateless search endpoint is fine; the SHUTDOWN logic is not
concurrency-safe. VALIDATED by reading the shutdown paths:

- `_stop_openserp_if_running(phase)` guards teardown with `_other_phase_locked`,
  which by design treats a concurrent SAME-phase lock for another book as
  **"NOT another phase"** (its docstring: "a concurrent lock for the SAME phase
  (another book) is NOT 'another phase' and must not block same-phase teardown").
  **Failure:** Doc A and Doc B both in Phase 2 (both need OpenSERP); Doc A finishes
  → `_stop_openserp_if_running("phase2")` → `_other_phase_locked` sees only Doc B's
  same-phase lock → returns False → **OpenSERP scaled to 0 while Doc B is still
  extracting** = internet-lookup confusion/failure. Same-phase concurrency is the
  COMMON case (multiple docs extracting at once), so this fires routinely.
- Second shutdown path: `openserp_manager._teardown` (idle monitor) also scales to
  0 — must likewise consult aggregate demand.
- `search_history` writes `cache/openserp_search_history.json` (per-host file) —
  not shared-safe (minor; per-task caches don't corrupt shared state).

**Fix:** OpenSERP scale-down must be **aggregate-demand-counted** — scale to 0 only
when NO doc anywhere needs it (any book in Phase 2/3, or a per-book lock of those
phases, or a running extract/enrich task). Mirror the NAT-lease model: a finishing
doc contributes −1; teardown only at cluster-wide zero. A single doc's completion
must NEVER scale OpenSERP down while any other doc needs it. Keep the one shared
endpoint. Both shutdown paths (`_stop_openserp_if_running` + `openserp_manager`)
must use the same aggregate check.

**G3. Double bibliography download (#4) — amplified under concurrency.**
Phase 2 unconditionally downloads the entire 13,566-file bibliography (~17 min),
and the submit-only delegation re-downloads it AGAIN. Under N concurrent docs this
is N×2 full downloads → NAT/S3 thrash + NAT lease expiry mid-download.
**Fix:** download once (cache/share between parent run_phase and submit-only
subprocess), or scope to what the extraction needs. Prerequisite for concurrency.

**G4. OCR intake not idempotent (§8).**
`_submit_ocr` submits a GPU job unconditionally; duplicate `ObjectCreated` (S3/SNS
at-least-once, redrive, re-upload) → redundant SPOT GPU jobs. Under concurrency,
multiple triggers for the same book are more likely.
**Fix:** atomic `ocr#{book}` conditional-write claim (M2 `_lease_table()`), TTL'd,
released on terminal failure. Deny redundant submissions at intake (front door).

**G5. Batch retrieve orchestration is per-PHASE singleton (spec §3.4).**
`batch_poller._trigger_retrieve` skips if a retrieve for THIS PHASE is already
RUNNING and returns `True` for a batch it did NOT retrieve. Under concurrency this
serializes retrievals + masks success.
**Fix:** key retrieve orchestration per-`batch_id` (one retrieve per batch, not
per-phase); track truthful per-batch retrieve state; never return True for a
batch not actually retrieved. NEEDS VERIFICATION — may already be partly fixed
(grep showed per-batch keying in batch_poller); confirm during fix.

---

## Fix order (prerequisites, all serial-testable)

1. **G1** idle-check race → dispatcher/per-book-lock launch (highest — the direct
   "no overlap" violation).
2. **G4** OCR intake idempotency (§8) — front-door deny.
3. **G2** OpenSERP demand-counted scale-down.
4. **G3** double-bibliography download (once/scoped).
5. **G5** verify + fix per-batch retrieve orchestration.

Then: flip `MULTI_DOC_ENABLED=true` with adaptive POOL_MIN/MAX (Service-Quotas
derived cap); controlled 3–4 doc test proving unique batches, no cross-attribution,
correct NAT lifecycle, all docs complete.
