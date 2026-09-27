# Spec: True Multi-Job Concurrency + NAT Management

**Status:** draft (2026-09-27). Design spec — not yet implemented.
**Motivation:** a real backlog (`~/Downloads/WWIIArchives`: **628 files / ~502 GB**
— 238 PDFs, 204 zips, 184 JPGs, plus NARA data inbound) must be ingested. Today
the pipeline processes **one document at a time** (serial per-book queue), which
does not scale to this volume. This spec defines concurrent multi-document
processing and the NAT lifecycle that must accompany it, **within AWS service
limits**.

Related: `docs/current/TODO.md` "True multi-job concurrency" (High/Future) and
"NAT torn down between compute phases (persistent race)".

---

## 1. Current state (baseline)

- **Spot:** tasks launch `FARGATE_SPOT:FARGATE` weighted 4:1 (trigger Lambda +
  cluster capacity providers). Spot-termination resilience is solid: SIGTERM →
  `_final_sync` to S3 + lock clear; DynamoDB cache avoids re-paying Grok;
  EventBridge `spot-recovery` rule relaunches; incremental resume skips done work.
- **Serial across documents:** per-phase locks (`lock#{env}-wwii-phase{1,2,3}-*`)
  allow only ONE task per phase. Per-book queues (`pending#content`,
  `pending#parsed#{book}`, `pending#enrich#{book}`) hand off one book at a time
  (`_get_next_pending_book`). Within a book, Phase 2 runs `max_parallel_chapters`
  (default 3) concurrent chapter extractions.
- **Entity store:** `DynamoEntityStore` (DynamoDB-backed, per-entity keys) already
  exists for durable writes; file layer uses `flock` + threading locks.
- **Dedup:** **cross-book / global** (`find_duplicate_people/places_v2/...` compare
  across all books; places use a global coords cache). This is the hardest part
  to parallelize — it is inherently a global barrier.
- **NAT:** single shared NAT gateway, created on demand (`nat_manager` action
  create), delayed-teardown via EventBridge schedule, 2h no-tasks guardrail
  (`openserp_manager`). The per-phase teardown races (tracked bug).

---

## 2. Goals / non-goals

**Goals**
- Process **N documents concurrently** (target: a configurable pool, e.g. 4–8
  simultaneous book pipelines) to drain a large backlog in reasonable time.
- Preserve **spot-termination resilience** and **incremental resume** unchanged.
- **Correct NAT lifecycle** under concurrency: NAT up while ANY compute runs,
  down only at the global dedup human-gate and at full-drain completion.
- Stay **within AWS service limits** (soft + hard) with backpressure, not crashes.
- Keep **cost control** (spot, scale-to-zero when idle, single NAT).

**Non-goals**
- Parallelizing the **global dedup pass** itself (it stays a single barrier).
- Real-time/interactive latency — this is batch throughput.

---

## 3. Concurrency model

### 3.1 Phase 0/1/2 fan-out (parallelizable)
- Replace the single per-phase lock with a **bounded worker pool**: allow up to
  `MAX_CONCURRENT_BOOKS` (config, default TBD after limit analysis) simultaneous
  book pipelines through Phase 0→1→2.
- **Per-book locks** (`lock#book#{book}#{phase}`) instead of one global per-phase
  lock, so different books run the same phase concurrently while a single book
  can't double-run a phase.
- A **Step Functions Map** (Standard workflow) fans out over pending docs with
  `MaxConcurrency` = the derived pool size (§5); Map branches call the shared
  DynamoDB coordination state (NAT lease, rate/credit, entity store). SFN owns
  fan-out/retry/flow; DynamoDB owns coordination.
- **OCR (AWS Batch)** already fans out across chunks/jobs; extend to submit
  multiple documents' OCR jobs concurrently up to the Batch vCPU/GPU quota.

### 3.2 Dedup as a global barrier
- Dedup is cross-book, so it **cannot** run per-book concurrently. Model it as a
  **barrier**: once a wave of books finishes Phase 2, run ONE global dedup pass →
  human review gate → Phase 3.
- Requires a **quiescence check**: don't start dedup until in-flight Phase 2
  books for the wave have drained (extend the existing lock-count logic to count
  per-book locks).

### 3.3 Shared entity store (the correctness crux)
- Concurrent books writing People/Places/Groups **will collide** on the same
  entities (e.g. "Eisenhower" mentioned in many books). Requirements:
  - Route all cross-book entity writes through **`DynamoEntityStore`** with
    **conditional/atomic updates** (already conditional-write capable) — NOT
    per-file JSON writes, which race across tasks even with flock (flock is
    per-host; Fargate tasks are separate hosts).
  - **Idempotent mention-append** keyed on (EntityID + book + sub_event) so a
    retried/relaunched task doesn't double-append (the dedup event-mention dedup
    already does this per-book; must hold cross-task).
  - Materialize to `output/` files only at safe points (post-dedup), or make the
    file layer a read-through cache of Dynamo.

### 3.4 Batch retrieval must be per-batch, not per-phase (verified finding)

The Grok Batch flow (submit → poller → retrieve task) must retrieve the **correct
batch for each job** under concurrency. Current state (verified 2026-09-27):

- **Batch identity is correctly bound** ✅ — jobs are keyed `batch_job#{batch_id}`
  (globally-unique xAI id) carrying `book` + `phase`; the retrieve task is launched
  with the specific `--retrieve-only <batch_id>` arg **and** `BOOK_NAME`. So the
  batch *download* is correctly targeted — no wrong-batch fetch.
- **BUT the retrieve orchestration is a per-PHASE singleton** ⚠️
  (`batch_poller._trigger_retrieve`): "if a retrieve task for this phase is already
  RUNNING, skip and return True." Under concurrency this **serializes** retrievals
  and, worse, **returns success (`True`) for a batch it did NOT retrieve** — it
  relies on the `ready` status + next poll cycle to eventually pick it up. Safe
  under serial (no corruption/loss — just delayed), but **not parallel-safe** and
  the success-masking return is a latent bug.

**Required changes:**
- Retrieve orchestration keyed **per-batch** (one retrieve per `batch_id`), not a
  single-flight-per-phase guard. Multiple batches for different books retrieve
  concurrently (subject to the compute pool cap, §5).
- **Rigorously book-scoped result application** — verify `BOOK_NAME` threads
  through every write in `--retrieve-only` processing so two concurrent retrieves
  cannot cross-attribute results. Combined with §3.3 (entity writes via
  conditional DynamoDB updates), this makes parallel retrieval correct.
- Don't return `True` for a batch that wasn't actually retrieved — track per-batch
  retrieve state so the poller's accounting is truthful.

---

## 4. NAT management under concurrency — MULTI-JOB AWARE (primary invariant)

Under parallel processing, NAT teardown **cannot** be driven by phase transitions
or by human-gate events — that framing strands jobs (Job A finishing must not tear
down NAT that Jobs B/C still need). NAT management must be driven by **aggregate
network demand across ALL jobs**, cluster-wide.

**Primary invariant (job-aware):**
> NAT is UP if and only if **any job anywhere** currently needs outbound network,
> OR any queued work will need it imminently. Teardown happens only when
> **cluster-wide network demand is zero** — regardless of which phase any
> individual job is in, and regardless of *why* it reached zero.

Human review gates are **NOT** a special teardown trigger — they are simply **one
way a job stops needing the network** (a job parked at the dedup / OCR-review /
bibliography-disposition gate contributes 0 to demand). Teardown is decided by the
**aggregate**, never by any single job's state or transition.

**Mechanism — cluster-wide reference count (source of truth):**
- A **NAT demand counter** in DynamoDB (`nat#demand`), incremented/decremented
  **atomically** (conditional update) by every network-needing unit of work:
  - **+1** when a task launches / acquires a per-book phase lock that needs egress.
  - **−1** on task exit, on SIGTERM (add to the emergency handler alongside
    `_final_sync`/lock-clear), AND when a job **parks at a human gate** (it
    releases its NAT hold while waiting — the gate is just a −1, not a special
    teardown).
  - Queued-but-not-started work that will need egress counts via a separate
    **pending-demand** check so NAT isn't torn down microseconds before the next
    job launches (closes the current Phase1→Phase2 launch race by construction).
- `nat_manager` tears NAT down **only** when `nat#demand == 0` **AND** no pending
  queue entries imply imminent demand. It **never** infers teardown from a phase
  completing.
- **Idempotent / crash-safe counting:** a spot-killed task must not leak its +1.
  Use a **per-task lease with TTL** (task writes `nat#lease#{taskArn}` with a
  heartbeat/TTL; demand = count of live leases) rather than a raw integer that a
  crash could leave incremented. `nat_manager` computes demand from live leases +
  running-task list + pending queues — self-healing if a decrement is missed.

**Guardrails (defense in depth, unchanged in spirit):**
- Keep the **2h stale-NAT guardrail** (`openserp_manager`) as a backstop, but it
  must also consult the demand counter/leases (tear down only if genuinely no
  demand and no running tasks — it already checks running tasks/locks).
- Reconciliation: a scheduled check recomputes demand from ground truth (running
  ECS tasks + pending queues + live leases) and corrects a drifted counter, so a
  missed increment/decrement can't leave NAT permanently up or wrongly down.

**Why this supersedes the per-phase/human-gate framing:**
- The previously tracked "NAT torn down between compute phases" race and the
  "tear down at the dedup gate" behavior both become **emergent consequences** of
  the single job-aware invariant: between-phase teardown can't happen (demand > 0
  while any job runs); dedup-gate teardown happens naturally (all jobs parked →
  demand 0) and comes back up when a job resumes and takes a lease.
- Correct for **1 job or N jobs** identically — no special-casing.

---

## 5. AWS service limits (soft + hard) — must design within

Concurrency fan-out hits real quotas. The dispatcher MUST apply **backpressure**
(cap concurrency, queue the rest) rather than launch blindly and fail.

### 5.0 Limits are RETRIEVABLE — discover, cap, and warn (do not hardcode)

AWS exposes compute limits via the **Service Quotas API**
(`aws service-quotas get-service-quota` / `list-service-quotas`). The dispatcher
MUST:
1. **Query the live quota** at startup (and periodically), not hardcode a pool
   size.
2. **Cap concurrent compute below the established limit** — computed max tasks =
   `floor(quota_vCPU / per_task_vCPU)`, then apply a safety margin (e.g. 80%).
   Concurrent compute must **not exceed** the quota.
3. **Warn as usage approaches the ceiling** (e.g. ≥80% of the quota) — emit an
   alert (SNS → Slack/email) so the operator can **request a quota increase**
   before it becomes the bottleneck. A warning, not a failure.

Verified live (account 340339225515, us-east-1) — all adjustable:

| Quota | Code | Value | Use |
|-------|------|-------|-----|
| Fargate On-Demand vCPU resource count | `L-3032A538` | 4000 | max concurrent on-demand task vCPUs |
| Fargate Spot vCPU resource count | `L-36FBB829` | 4000 | max concurrent spot task vCPUs (primary) |
| Fargate Spot Sustained Launch Rate | `L-FAA52651` | 20/s | launch pacing |
| NAT gateways per AZ | (vpc) | 5 | keep ONE shared NAT (§4) |
| EIP per NAT gateway | (vpc) | 2 | NAT egress IPs |

So the quota gives a **derived cap** = `min` across (vCPU-quota ÷ per-task-vCPU
× margin, Grok-rate headroom, Batch GPU capacity). **Pool size is a soft-coded
config option** (`MAX_CONCURRENT_BOOKS` / per-resource variants, with a sensible
default) that the operator sets; the effective pool is
**`min(configured_pool, derived_cap)`** — so config controls concurrency but can
**never exceed the live quota ceiling** (a config typo can't blow past AWS
limits). If the configured value is clamped down by the cap, **log it**; if the
cap itself is below backlog demand, **warn + name the quota to raise** (§5.1).

### 5.1 Limit table

| Resource | Limit type | Concern at scale | Mitigation |
|----------|-----------|------------------|------------|
| **Fargate vCPU** (`L-3032A538`/`L-36FBB829`, 4000) | Soft (raisable) | Pool × per-task vCPU may exceed quota | **Query via Service Quotas API; cap pool below it; warn at ≥80% → request increase** |
| **Fargate launch rate** (`L-FAA52651`, 20/s) | Soft | Bursty dispatch throttled | Pace launches; dispatcher respects the rate |
| **Fargate Spot capacity** | Practical | Spot unavailable → PROVISIONING | 4:1 spot:on-demand fallback; monitor |
| **AWS Batch (Chandra GPU)** vCPU/GPU | Soft | Concurrent OCR jobs exceed GPU capacity | Batch queue absorbs; cap concurrent submissions; GPU/spot-GPU availability is the real ceiling |
| **Grok API rate** | Hard (vendor) | N tasks × parallel chapters → 429s | **Cluster-wide** limiter (below); honor 429 backoff |
| **NAT Gateway** | Soft (per-AZ) | Single NAT is fine; don't spawn per-book NATs | Keep ONE shared NAT (§4) |
| **Elastic IPs** | Soft (5/region default) | NAT churn leaked EIPs before (fixed) | Reuse; the create path already releases orphaned EIPs |
| **DynamoDB throughput** | Soft (on-demand scales) | Concurrent entity writes spike WCU | On-demand billing mode; exponential backoff on throttling; conditional writes |
| **S3 request rate** | Effectively high | Many parallel syncs | Prefix sharding by book (already per-book prefixes); standard retry |
| **CloudWatch Logs / API throttling** | Soft | Many tasks logging | Standard; unlikely to bind first |

**Principle:** the dispatcher reads the **binding limit** (likely Grok rate limit
and Fargate vCPU quota first) and sizes the pool to the *minimum* headroom across
limits. Prefer **queue + backpressure** over launch-and-fail. Make the pool size
and per-task Grok rate a **config** so it can be tuned to the current quotas.

### 5.2 Current rate limiter is per-process — must become cluster-wide

Today `src/grok_client.py:_RateLimiter` is a **per-process** token bucket
(`calls_per_minute`, default 30) + 429 backoff. It is correct for serial
(one-task) operation but **breaks under concurrency**: N parallel task processes
each get their OWN 30/min bucket → **N×30/min** hitting Grok's *account-wide* hard
limit. Required change: a **cluster-wide** limiter — either a shared token bucket
in DynamoDB (atomic decrement of a refilling budget) OR a static per-task budget
= `vendor_limit ÷ pool_size` enforced locally. The vendor limit itself is a config
input (no API to query it), so the dispatcher divides it across the pool. Same
class of fix as the NAT counter: coordination must move from per-process to
cluster-wide shared state once we go parallel.

---

## 6. Failure / resilience under concurrency

- Spot termination of one book's task must not affect others — per-book locks +
  refcounted NAT make tasks independent. SIGTERM flush + relaunch per task
  (existing) still applies; the relaunched task re-acquires only its book lock.
- **Poison document** (one doc always fails) must not block the pool — cap
  retries per book, then route to a **failed/needs-review queue** and free the
  slot.
- Dedup barrier must handle a book that never finishes (timeout → proceed with
  the books that did, or hold — decide).

### 6.1 Batch resilience is failure-CAUSE driven (primary design)

Batch handling/resubmission is the **primary resilience abstraction**; a Grok
model transition is **one failure cause among many**, not a special mechanism.
The batch layer must **classify the cause of each failure** (batch-level and
per-request) and route it to a per-cause recovery policy — rather than bolt on
model-specific logic. Any new failure cause (including future ones we haven't
seen) slots into the taxonomy without new special-casing.

**Current gap:** the code classifies by **response SHAPE**
(`valid/truncated/empty/error/missing`) — *what the response looked like* — not
by **cause / retryability**. Transient-vs-permanent is guessed ad hoc in
`grok_client` ("likely transient" vs "consider splitting"). No unified taxonomy
drives resubmission.

**Required — a failure-cause taxonomy with per-cause policy:**

| Cause (examples) | Class | Recovery policy |
|------------------|-------|-----------------|
| 5xx / connection / timeout | transient | retry (backoff), same batch |
| 429 rate limit | transient (paced) | honor Retry-After; cluster-wide budget (§5.2) |
| Model retirement / redirect / not-found | config/transitional | **do NOT retry blindly**; apply fallback model (§6.2), then resubmit affected requests |
| **Credit/quota exhausted (402/403)** | funding | **pause submissions, HOLD unsubmitted work (don't drop), alert; resume on top-up** (§9.0) — never counted as processed |
| Batch exceeds per-batch limit | structural | **split + resubmit** as sub-batches (§ below) |
| Request too large (tokens) | structural | split the request / chunk; not a plain retry |
| `content_filter` / policy refusal | permanent-ish | do not loop; flag → needs-review |
| Malformed / poison content | permanent | cap retries → failed/needs-review queue |
| Partial batch (some ok) | mixed | recover the failed subset only (per-request) |
| Whole batch failed / 24h expiry | batch-level | resubmit-on-failure (bounded) → needs-review |

**Design rules:**
- **Classify first, then act** — map the batch/request error (HTTP status, xAI
  error code, `finish_reason`, served-model mismatch) to a cause, then apply that
  cause's policy. Model-transition (§6.2) is just the "config/transitional" row.
- **Transient → retry; structural → transform (split/fallback) then resubmit;
  permanent → stop + route to needs-review.** Never retry a permanent cause.
- **Bounded** per cause (retry caps) so no cause loops forever; exhaustion →
  failed/needs-review queue (reuse the poison-doc pattern).
- Keep the existing **20% systemic circuit breaker**, but drive it off the cause
  mix (e.g. a spike of "model not-found" → treat as systemic → fallback, not
  per-request real-time retry).

### 6.2 Grok Batch failure + size-limit handling (verified current state)

**Handled today (post-submission):**
- **Whole-batch failure** (`num_error >= total`) → poller marks `failed` + SNS
  notify with book name (not silently dropped).
- **Partial failure** (some requests error) → batch treated `complete`; individual
  errored requests classified + **retried at the result level** (`batch_api`
  statuses: valid/truncated/empty/error/missing/retry_ok/retry_fail).
- **24h timeout** — `poll_batch(max_hours=24)` + poller marks jobs failed after
  24h (matches Grok's batch completion window; an unfinished batch expires).
- **Transient poll errors** — tolerated up to 5 consecutive (60s backoff).

**GAP — batch that EXCEEDS Grok's per-batch limit at submission:**
- `submit_batch` only retries on **429**. A **size/limit rejection** (too many
  requests or too many enqueued tokens per batch — see the "verify Grok batch
  limits" open item) would hit `raise_for_status()` and **fail hard** — there is
  **no pre-emptive chunking** and **no split-on-rejection**. A large document or
  the archive fan-out can produce an oversized batch that errors out.
- **Required:** (1) verify Grok's per-batch limits (requests + enqueued tokens);
  (2) **chunk** the JSONL to stay under them before submit; (3) on a size-class
  rejection, **split and resubmit** rather than raise; (4) track the sub-batches
  as a group so retrieval/accounting stays correct (ties to §3.4 per-batch
  retrieval). Under concurrency, chunking also interacts with the cluster-wide
  rate/limit budget (§5.2).

### 6.3 Grok model-transition resilience (verified gap)

Grok itself fails around **model introductions/deprecations** (observed in
practice). This is the **"config/transitional" row of the §6.1 taxonomy** — a
failure *cause* the batch layer classifies and routes, not a parallel mechanism.
The specifics below define that row's detection + policy. Current state:
- **Soft redirect handled (alert only):** if xAI serves a different model than
  requested (`served_model != self.model`), `grok_client` logs "MODEL DEPRECATED"
  and sends an SNS alert — but **keeps running on whatever was served** (could be
  an unvetted model producing different output for a whole run of items).
- **Transient 5xx/connection:** retried 5× with exponential backoff (absorbs
  rollout blips).
- **GAP — hard model retirement:** a fully-retired model returns model-not-found
  (400/404). That is an `HTTPError` → **retried 5× uselessly** (retrying won't
  revive a dead model) → then **hard-fails the job**. There is **no fallback
  model** and no "model retired → switch to configured alternate" path.
  `self.model` is a single value; `_model_map` is per-task-type, not a fallback
  chain.
- **GAP — batch-mode coverage:** deprecation detection lives in the real-time
  response path; whether the **batch API** surfaces a model change / triggers the
  alert is unverified. Under concurrency a transition fails ALL in-flight jobs at
  once.

**Required:** classify model-not-found distinctly and **do NOT retry it**; add a
**configured fallback model chain** (primary → fallback) applied on
retirement/redirect; escalate the alert to **block-or-fallback** rather than
silently proceed on an unvetted served model; verify + handle model changes in
batch mode.

### 6.4 Batch resubmission (verified current + gaps)

**Exists today:**
- **Partial (per-request), automatic:** `_retry_failed_batch` retries errored/
  truncated requests via the **real-time API**, with a **20% failure-rate circuit
  breaker** (skips retry above 20% as "likely systemic").
- **Whole-batch resubmit, MANUAL:** RUNBOOK — reset the `batch_job#` status / mark
  failed + re-run the phase; **cache-aware** so only genuinely-missing requests
  re-batch (succeeded ones hit the DynamoDB cache).
- **Phase-level retry loop:** `phase{2,3}_retry.py` re-run up to `--max-attempts`,
  driven by counting missing `-event.json` (incremental, skip-existing).

**GAPS:**
- **Per-request retry falls to real-time, not re-batch** — if real-time is ALSO
  down (outage/model transition), retry fails; no "re-submit failed requests as a
  new batch."
- **Whole-batch resubmit is manual** — a `failed` batch waits for a human; no
  auto-resubmit-on-failure. Bad at archive scale / under concurrency.
- **20%-breaker + model transition:** a model retirement causing >20% failure
  skips retry (good) but leaves **only the manual path** to recover once config is
  fixed.
- **No split-resubmit** for oversized batches (ties to §6.1).

**Required:** automatic whole-batch **resubmit-on-failure** (bounded retries, then
route to a failed/needs-review queue — reuse the poison-doc pattern §6); optional
**re-batch** (vs real-time) retry for the failed subset; **split-resubmit** for
size rejections; all keyed per-batch (§3.4) and cache-aware so resubmits stay
cheap.

---

## 7. Input pre-stage: archive expansion + heterogeneous media routing

The backlog is **not** uniform "books": 238 PDFs, **204 zips + 2 rars**, 184 JPGs.
A pre-stage must run before the concurrency dispatcher:
- **Archive expansion** — unpack zips/rars to individual source files. A zip may
  contain **already-held** docs (dedupe against the holdings index, §local_holdings)
  and mixed media. Expansion is itself parallelizable + resumable (track expanded
  state so a re-run doesn't re-unpack 502 GB).
- **Media-type routing** — each expanded file is classified (existing
  `media_detection`/`disposition_classifier`) and routed to the correct track,
  which the dispatcher must treat as **heterogeneous work items**, not all "books":
  - Scanned text PDF → OCR (Chandra Batch) → narrative extraction.
  - Native/text PDF, docx, epub → text converter → narrative extraction.
  - Structured/reference (OOB) → `oob_markdown` parser track (NOT narrative).
  - Images/maps (184 JPGs) → vision path (`MAP_IMAGE_AV_INGESTION.md`), NOT the
    LLM narrative pipeline.
  - Video → keyframes + transcription.
- The dispatcher pool therefore schedules **mixed work types** with different
  resource profiles (GPU-OCR vs Grok-extraction vs vision vs CPU-parse) — pool
  accounting (§5) must be **per-resource** (Batch GPU vs Fargate vCPU vs Grok
  rate), not a single number.

## 8. Completion tracking + ordering

- **Per-document lifecycle state** (DynamoDB): `held_unprocessed → expanding →
  routed → ocr → parsed → extracted → deduped → enriched → done` (or
  `failed/needs-review`). The dispatcher dispatches only docs not already `done`
  or in-flight — idempotent across restarts; no re-dispatch of completed work.
- **Ordering / prioritization — DECIDED (2026-09-27): FIFO.** Documents are
  dispatched in arrival/enumeration order (no priority tiers). Dedup-barrier
  waves (§3.2) are formed from consecutive FIFO runs of completed docs. Keeps the
  dispatcher simple and predictable; revisit only if a high-value subset ever
  needs to jump the queue.
## 9. Cost awareness — soft pre-spend alert (not a hard block)

Submitting jobs is **expected to cost money** on Grok — that is the point. So cost
handling is an **alert, not a circuit breaker**: notify before/as a soft,
configurable threshold is crossed, then **keep running** unless the operator
intervenes.

### 9.0 CRITICAL — credit-aware submission gating (job integrity, not just cost)

**An overspend on Grok does not just cost more — the work does NOT get
processed.** If credit is exhausted during/after submission, those requests
fail silently (returned as generic errors, or dropped), so managing submission
against available credit is a **correctness prerequisite**, not a courtesy.

- **Current state (verified):** `_preflight_credit_check` only asks "are there
  ANY credits?" (a minimal test request → abort on 402/403). It does **NOT**
  check whether there is **enough** credit for the batch about to be submitted.
  So a batch can pass preflight, then **run out of credit mid-batch → remaining
  requests unprocessed.** Under concurrency this is worse: N books submitting at
  once can jointly exhaust the balance with no coordination.
- **Required — gate submissions on available balance:**
  1. **Know the balance** — query available Grok credit (if the API exposes it)
     or track a configured operator-set credit budget decremented by accrued
     spend.
  2. **Estimate the batch's cost** (token estimate × price) and **do not submit a
     batch whose estimated cost exceeds remaining credit** — hold it in the
     pending queue and alert, rather than submit work that will silently fail.
  3. **Cluster-wide credit accounting** under concurrency — a shared (DynamoDB)
     running-spend/remaining-credit counter so N concurrent submitters don't
     collectively overspend; each submitter atomically "reserves" its estimated
     cost before submitting.
  4. **On credit exhaustion (402/403 mid-run):** treat as a **first-class
     failure cause** in the §6.1 taxonomy — pause submissions, hold unsubmitted
     work in the queue (do NOT drop it), alert loudly, and **resume when credit
     is topped up** (the held/needs-review work resubmits, cache-aware).
  5. Distinguish "out of credit" from other failures so it isn't retried as if
     transient and isn't silently counted as processed.

This gate is about **not losing work**; the soft alert below is about
**visibility**. Both apply.

- **Soft spend-threshold alert** — a configurable value (`GROK_SPEND_ALERT_USD`,
  **default ~$10**) that fires an SNS→Slack/email alert **as projected/accrued
  Grok spend approaches or crosses it**, *before* a large overspend — a heads-up,
  not a stop. Jobs continue.
- **Re-arm at multiples** — alert again at each further increment (e.g. $10, $20,
  $30…) so a long drain keeps the operator informed rather than one alert then
  silence.
- **Track accrued + projected spend** — Grok tokens × price (Fargate/Batch GPU
  secondary); surface running total in the observability dashboard (§10).
- **Dry-run estimate (optional, informational)** — before a full-archive drain,
  estimate total cost from a sample (per-doc token cost × 628) so the operator
  knows the ballpark up front. Informational, not a gate.
- **Hard controls remain available but OFF by default** — a hard ceiling that
  throttles/halts the pool is a *separate, opt-in* safety (e.g. runaway
  protection), not the normal path. The $75/mo AWS budget alarm stays as the
  backstop detector.
- **Spot-first** already minimizes compute cost; GPU-OCR (priciest) pool kept
  small.

### 9.1 Spend calculation — per job, per kind, per model

Both the credit gate (§9.0) and the alert (§9) need a **cost estimate + actual**.
What the pipeline already captures (verified) vs. what's needed:

**Available today:**
- **Per-request token usage** — real-time path logs `prompt_tokens` /
  `completion_tokens` / `total_tokens` from Grok's `usage`.
- **Per-request job KIND** — batch `request_details[].cache_type`
  (events/people/casualties/…), so spend rolls up **per kind** for free.
- **Per-book / per-batch** grouping — batch metrics are keyed by book + batch_id.
- `content_length` per request (a proxy where token usage isn't persisted).

**Per-KIND → per-MODEL routing (this is why cost isn't one flat rate):**
- `GrokClient._get_model(cache_type)` returns `model_map.get(cache_type,
  default_model)`. So **different job kinds can use different models** (the
  intended cost lever: cheap model for simple extractors, best model for hard
  ones — tracked TODO "model routing expansion").
- **Today** `model_map: {}` → everything uses `grok-4.6` (one price). But the
  cost model MUST resolve **per request** via the same `model_map`/`cache_type`
  routing, so spend stays correct automatically when cheaper models are enabled.

**Missing piece — a configurable PRICE TABLE keyed by model:**
- Add `api.grok.pricing: { <model>: {input_per_mtok, output_per_mtok}, ... }` +
  a **batch discount** factor (Grok batch ≈ 50% off). No price data exists in
  config today — this is the one addition required.

**Cost formula (per request, then grouped):**
```
model      = model_map.get(cache_type, default_model)
price      = pricing[model]
cost_req   = (prompt_tokens/1e6 * price.input + completion_tokens/1e6 * price.output)
           * (batch_discount if batch else 1.0)
```
Group by `book` (per-job spend) and/or `cache_type` (per-kind spend) — both tags
already present.

**Estimate (pre-submit, for the credit gate) vs actual (post-hoc):**
- **Estimate:** token-count each request's prompt (tiktoken-style / chars÷4) +
  an assumed output-token budget × `price[model_for(cache_type)]` × batch
  discount → the batch's projected cost. Gate submission on `estimate ≤ remaining
  credit` (§9.0).
- **Actual:** from real `usage` after completion (batch results must surface
  usage, not just `content_length` — a gap to close). Reconcile estimate vs
  actual; feed the running total to the dashboard (§10) and the alert (§9).

**Also capture batch token usage** — batch `request_details` currently store
`content_length` but not per-request `usage`; pull token usage from the batch
results so batch (the dominant, discounted path) has accurate actuals, not just a
proxy.

## 10. Observability at scale

Draining 628 docs across N parallel jobs over hours/days needs visibility:
- **Progress dashboard** — counts per lifecycle state (§8), throughput
  (docs/hr), ETA, per-resource utilization vs quota (§5), failure/needs-review
  counts by cause (§6.1 taxonomy).
- **Stuck-job detection** — a doc in a state past a timeout → alert (extends the
  existing stale-lock check).
- **Cost tracking** — running spend vs the guardrail (§9), to Slack (§alerting).
- Reuse the existing SNS→Slack path; the failure-cause taxonomy makes alerts
  actionable ("12 docs failed: model-not-found → fallback needed").

## 11. Dedup-barrier scalability

Dedup is global + roughly O(entities²) (cross-book compare + coords cache). As the
corpus grows past thousands of docs it becomes the bottleneck and may exceed
Lambda/task time limits.
- **Incremental dedup** — only compare NEW entities against the existing resolved
  set (blocking/indexing by normalized name + geo cell), not all-pairs each wave.
- **Bounded wave size** — cap docs per dedup wave so the barrier stays within
  task time limits; multiple waves over the drain.
- Keep it a barrier (correctness), but make its **cost sublinear** in corpus size.

---

## 12. Phased implementation

1. **Input pre-stage** — archive expansion (zip/rar) + media routing + per-doc
   lifecycle state (§7, §8); dedupe expanded files against holdings.
2. **Per-book/per-doc locks** replacing the single per-phase lock (unblocks
   concurrency; smallest core change).
3. **Job-aware NAT** (DynamoDB per-task leases + demand from live leases/running
   tasks/pending queues) replacing per-phase delayed-teardown — fixes the race by
   construction AND required for safe concurrency.
4. **Step Functions Map dispatcher** (Standard; `MaxConcurrency`=pool) with per-resource
   caps (Fargate vCPU / Batch GPU / Grok rate), cluster-wide Grok limiter (§5.2),
   **credit-aware submission gating (§9.0 — don't submit beyond available credit;
   overspend = unprocessed work)**, soft spend-threshold alert (§9, default ~$10).
5. **Shared-entity-store hardening** — cross-book entity writes via
   DynamoEntityStore conditional/idempotent updates (§3.3).
6. **Global dedup barrier** — quiescence across per-doc locks; incremental +
   bounded-wave dedup (§11).
7. **Batch resilience** (§6.1–6.4) — failure-CAUSE taxonomy + per-cause policy
   FIRST, then causes plug in (per-batch retrieval, chunk/split-resubmit,
   model-transition fallback, auto resubmit-on-failure). Verify Grok per-batch
   limits + batch-mode model-change surfacing first.
8. **Observability** (§10) — progress/cost/failure dashboard + stuck-job alerts.
9. **Load test** on a subset of WWIIArchives (e.g. 20 docs, mixed media) before
   full drain; use it to set pool sizes + validate the cost estimate.

---

## 13. Open decisions

- ~~Pool size~~ **DECIDED (2026-09-27): soft-coded config option**
  (`MAX_CONCURRENT_BOOKS` + per-resource caps, sensible default), **clamped** to
  the live quota-derived cap: effective = `min(configured, derived_cap)` (§5.0).
  Operator-tunable, never exceeds quota. (Per-task Grok rate still set from the
  Grok plan limit ÷ pool — external fact to confirm.)
- ~~Dispatcher: trigger Lambda vs Step Functions~~ **DECIDED (2026-09-27):
  Step Functions (Map state).** Rationale: the hard part — cluster-wide NAT
  leases (§4), Grok rate limiter + credit reservation (§5.2/§9.0), shared
  entity-store writes (§3.3), the global dedup barrier (§3.2) — is
  **shared-state coordination in DynamoDB that must be built regardless of
  orchestrator**. Since that cost is orchestrator-agnostic, take Step Functions'
  free wins on the easy part: Map `MaxConcurrency` (limit-aware fan-out, §5),
  built-in per-item retry/catch (§6), and execution-history observability (§10) —
  code we'd otherwise hand-build in the trigger Lambda.
  **Constraints this imposes (must design for):**
  1. **Standard** workflow (not Express — the 5-min cap can't hold multi-hour
     docs); accept **per-state-transition billing** on long-lived executions.
  2. Human gates (dedup / OCR review / bibliography disposition) modeled with
     **`waitForTaskToken`** callbacks — the execution parks (possibly days) until
     the reviewer completes; NAT demand drops to 0 meanwhile (§4).
  3. **SFN orchestrates; DynamoDB coordinates** — Map branches still call the
     shared NAT-lease / rate / credit / entity-store state. Step Functions does
     NOT own coordination; it owns fan-out, retry, and flow.
- Dedup barrier policy for stragglers (timeout vs wait).
- ~~Whether zips/rar are auto-expanded~~ **DECIDED (2026-09-27):** archives MUST
  be unarchived before submission — the §7 pre-stage unpacks zips/rars to
  individual source files (deduped against holdings, media-routed) before the
  dispatcher sees them. No archive is ever submitted directly.
