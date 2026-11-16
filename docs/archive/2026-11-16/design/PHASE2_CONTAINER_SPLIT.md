# Phase-2 Container Split — Design

**Status:** Proposal (not scheduled). **Raised:** 2026-10-19 (operator question during the XChannel
clean E2E). **Author:** pipeline agent. **Audience:** whoever picks up the Phase-2 perf/arch work.

> Do not start this until the current clean-run data-quality baseline is established. This changes the
> execution topology and would reset that goal. See "Sequencing" at the end.

---

## 1. Why — the problem, with live evidence

Phase 2 is **one long-lived ECS task** (`phase2_extract.py`, task def `dev-wwii-phase2-extract`)
that runs the whole sequence serially:

```
core extract (events/people/places/dates/groups)
  → batch COLLECT (every LLM call raises BatchModeCollecting, request queued)
  → submit_batch()  → BLOCK on poll "Waiting for batch ... minutes to hours"
  → retrieve (re-run with cached batch results; persists entities)
  → optional-entity 2nd batch cycle (weather/equipment/logistics/casualties) — collect/submit/retrieve again
  → source-section enrichment
  → maps / images
  → dedup auto-merge + review-gate decision
```

### The core inefficiency (observed, not theoretical)
On the XChannel clean run (task `eaeb9a26`, 2026-10-19), the container submitted batch
`batch_742cf6e3` and then **blocked in-process** polling `0/80 complete` for the entire batch wait,
holding Fargate the whole time. `batch_job#` in DynamoDB stayed `0` — i.e. the batch was **never
enqueued for the `batch-poller` Lambda**.

Root cause: `ecs_entrypoint.run_submit_only()` *intends* to decouple submit from retrieve — it
monkey-patches `src.utils.batch_api.poll_batch`/`retrieve_results` to no-ops so submit returns
immediately and the job is enqueued for the poller. **But `phase2_extract.py` runs as a
`subprocess.run(...)` child**, which does not inherit the parent's monkey-patch. The child therefore
calls the real, blocking `poll_batch` (`phase2_extract.py:977 submit_batch → poll`) and does the
retrieve in the same process. The submit-only/poller path is effectively **dead code for Phase 2**.

### Consequences
1. **Fargate billed for the whole multi-hour batch wait** (the dominant waste).
2. **No crash recovery during the wait** — if the task dies (Spot reclaim, OOM, deploy), the run is
   lost; nothing enqueued for the poller to resume.
3. **One resource profile for very different work** — core-extract is LLM/IO-bound, maps/images is
   download-bound, dedup is CPU/memory-bound. One Fargate sizing fits none well.
4. **Serial tails** — maps/images, source-section enrichment, and the optional 2nd cycle are
   embarrassingly parallel across sections/entities but run one-after-another.

---

## 2. Design principle

**Split at the batch-wait boundary, not by entity type.** The batch wait is the thing that should
release compute; entity-type parallelism is a smaller, later win. The natural tool is an **AWS Step
Functions state machine** that externalizes the wait and fans out the parallel tails.

The good news: **most of the async machinery already exists** and is currently bypassed —
- `lambda_handlers/batch_poller.py` already scans `batch_job#`, checks Grok batch status, and on
  completion launches a retrieve task (`_trigger_retrieve`) and can launch a submit task
  (`_handle_submit`, already supports `phase=phase2` + a `--submit-only` command override).
- The `#452` reconcile **drain barrier** (`manifests/reconcile_complete/{book|_all}.json`) and the
  dedup **gate** already define the hand-off contract between extraction and downstream.

So this is less "build new orchestration" and more "make the existing submit-only→poller→retrieve
path actually engage, and formalize the stages."

---

## 3. Proposed topology

Three ECS task shapes (same image, different entrypoint command) coordinated by Step Functions +
the existing batch-poller:

```
                         ┌──────────────────────────────────────────────┐
                         │  Step Functions: Phase2StateMachine(book, N)   │
                         └──────────────────────────────────────────────┘
                                             │
   Stage A — COLLECT+SUBMIT (short ECS task) │
   • core-extract in batch-collect mode      ▼
   • write batch_requests.jsonl, submit     [A] ──► enqueue batch_job#{id} ──► TASK EXITS
   • scope guard (source-vs-actual, §5)              (Fargate released)
                                             │
                                             │   (EventBridge rule / 1-min poll)
                                             ▼
   batch-poller Lambda (exists)      ┌───────────────┐
   • polls Grok batch status         │  wait (async) │  ← NO Fargate held
   • on complete → launches Stage B  └───────────────┘
                                             │
   Stage B — RETRIEVE+PERSIST (ECS task)     ▼
   • re-run with cached batch results       [B] ──► persist events/people/places/dates/groups
   • optional-entity 2nd cycle = recurse to [A] for the optional batch
   • emit OBSERV *.extract + per-entity counts
                                             │
                                             ▼
   Stage C — ENRICH + ANALYZE (ECS task, fan-out capable)
   • source-section enrichment  ┐
   • maps / images              ├─ parallelizable (SFN Map, bounded MaxConcurrency)
   • (Phase-3 is separate)      ┘
   • dedup auto-merge → reconcile barrier publish → dedup gate decision
   • emit OBSERV phase2.summary (end-of-run per-type counts)  ──► STOP at review gate
```

### Stage responsibilities & resource profiles
| Stage | Work | Lifetime | Fargate sizing | Exit condition |
|---|---|---|---|---|
| **A: collect+submit** | core extraction in collect mode; build + submit batch; enqueue `batch_job#` | seconds–minutes | CPU-light, mem-moderate | after `submit_batch` returns the id |
| *(wait)* | batch-poller Lambda polls Grok | mins–hours | **none** | batch `complete` |
| **B: retrieve+persist** | re-run with cached results; persist entities; optional 2nd cycle (loops A for the optional batch) | minutes | mem-heavier (holds corpus) | all entity types persisted |
| **C: enrich+analyze** | source-section enrich, maps/images (fan-out), dedup, barrier, gate | minutes | network-bound; parallel map | reconcile barrier published + gate decided |

---

## 4. Invariants the split MUST preserve

These are load-bearing today inside the single process; across stages they must be re-established
explicitly (this is the main source of risk):

1. **Run-scope guard** (`run_scope_guard.enforce_scope`, ceiling 50 / declared count). Must run in
   **Stage A** before any submit, and the declared/derived expected count must be carried to Stage B
   (so a partial retrieve is detectable). See §5 — ideally replace with source-vs-actual.
2. **`#452` reconcile drain barrier** — `manifests/reconcile_complete/{book|_all}.json` with the
   freshness guard. Must be **cleared at Stage C start** and **published at Stage C end**, exactly as
   `ecs_entrypoint._wait_for_reconcile_barrier` expects, so Phase 3 still waits on a fresh barrier.
3. **Dedup gate** — the block/allow decision + `batch_job#`/pending bookkeeping. Stays in Stage C;
   must still "STOP at review gate" (no Phase-3 auto-trigger).
4. **Idempotent retrieve skip** (`_is_processed`) — Stage B re-run must still skip already-persisted
   sections (cost + latency), as the in-process re-run does today.
5. **Observability** — the P1/P2/P3 OBSERV lines (`*.extract`, `enrich.persisted`,
   `phase2.summary` + `phase2.summary.zero`) must still fire, and `phase2.summary` must emit from the
   **final** stage (C), where the corpus is complete — not from A (which would count the empty
   collect pass, the bug the current end-of-run summary explicitly avoids).
6. **NAT lease discipline** — each stage acquires/releases its own NAT lease; the wait holds none.
7. **Batch poller idempotency** — `_trigger_retrieve` may fire more than once (at-least-once); Stage
   B must be safe to launch twice for the same `batch_job#` (dedupe on job status `retrieved`).

---

## 5. Companion change: replace the magic-50 guard (recommended, not required)

The split is the right moment to adopt the **source-vs-actual reconciliation** guard (operator
proposal, 2026-10-19), because Stage A already needs an authoritative expected count:

- At Phase-1/ingest, record an **independent** section count (e.g. `run_scope#{book}` in DynamoDB or
  a book-manifest field = count of ingested `*-content.md` / produced `*-parsed.json`).
- Stage A compares its scoped **actual** work-set to that recorded **expected** and **fails loud on
  any drift — high (unintended fan-out) OR low (silent drop / partial sync)**.
- Stage B re-checks persisted count vs expected (catches a partial retrieve).
- Magic-50 becomes a **fallback** only when no upstream count was recorded (ad-hoc runs).

This also removes the current **Phase 1→2 chaining bug** (the manual trigger threads
`EXPECTED_COUNT`, but the phase-complete chain does not → any >50-section book aborts at the
handoff), because the expectation is derived from the source, not hand-carried.

---

## 6. Migration path (incremental, low-risk first)

1. **Fix submit-only across the subprocess boundary (smallest win, do first even without the full
   split).** Pass the intent into the child via env (e.g. `BATCH_SUBMIT_ONLY=1`), and have
   `phase2_extract.py` itself return after `submit_batch` (skip the in-process poll/re-run) when set.
   The parent then enqueues `batch_job#` and exits. This alone recovers the Fargate-during-wait cost
   and activates the existing batch-poller retrieve path — **no Step Functions required.**
2. **Formalize Stage A / Stage B** as distinct entrypoint commands (`--collect-submit` /
   `--retrieve`) — mostly renaming the existing submit-only + retrieve flows and making the
   poller→retrieve launch authoritative.
3. **Introduce the Step Functions state machine** to own A → (poll) → B → C with retries, a Map for
   the Stage-C fan-out, and the barrier/gate as explicit states.
4. **Adopt source-vs-actual guard** (§5) in Stage A/B.
5. **Fan out Stage C** (maps/images/source-section) via SFN Map with bounded concurrency.

Step 1 is independently valuable and reversible; later steps are each gated on the clean baseline
holding.

---

## 7. Costs / tradeoffs of doing this

- **More orchestration surface.** Three task shapes + a state machine + the poller = more places a
  barrier/handoff can be missed. The invariants in §4 are the risk; test them explicitly.
- **More task starts** (image pull/startup per stage). Mitigated by the batch wait dwarfing startup,
  and by Spot for the short stages.
- **Harder local reproduction.** The single-process flow is runnable locally today; a SFN topology is
  not. Keep a `--monolith` path (today's behavior) for local/dev + as a fallback.
- **Not a concurrency win by itself.** The big win is releasing Fargate during the wait (step 1). The
  fan-out (step 5) is a secondary, smaller gain.

## 8. Explicit non-goals
- Not changing Phase 1 or Phase 3 topology.
- Not changing the entity schema or the dedup algorithm.
- Not removing the review gate (still STOP there).
- Not a concurrency cap change (scope guard is orthogonal).

---

## 9. Sequencing / recommendation

1. **Now:** finish the current clean XChannel E2E → get the data-quality baseline. Do not re-architect
   mid-flight.
2. **Next, cheap + high value:** migration **step 1** (fix submit-only across the subprocess boundary)
   — recovers the Fargate-during-wait waste and activates the poller path with minimal risk.
3. **Then, if the perf case holds:** steps 2–5 (formal stages → Step Functions → source-vs-actual
   guard → Stage-C fan-out), each verified against §4 invariants.

Related backlog: A00 OpenSERP redesign and A0/A0+ (parallelize optional-entity + place-image
enrichment) are the Stage-C fan-out concretely; this doc is their umbrella.
