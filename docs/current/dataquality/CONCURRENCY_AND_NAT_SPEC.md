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
- A **dispatcher** (trigger Lambda or a small Step Functions map) pulls from the
  pending queue and launches up to the pool size, respecting limits (§5).
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

So `pool_size` is **derived**: `min` across (vCPU-quota ÷ per-task-vCPU × margin,
Grok-rate headroom, Batch GPU capacity). If the derived cap is lower than the
backlog demands, **warn + surface the specific quota to raise** rather than
silently throttle.

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

### 6.1 Grok Batch failure + size-limit handling (verified current state)

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

---

## 7. Phased implementation

1. **Per-book locks** replacing the single per-phase lock (unblocks concurrency;
   smallest change).
2. **Job-aware NAT** (DynamoDB per-task leases + demand computed from live
   leases/running tasks/pending queues) replacing per-phase delayed-teardown —
   fixes the race by construction AND is required for safe concurrency.
3. **Dispatcher with bounded pool + limit-aware backpressure** (config pool size;
   global Grok rate limiter).
4. **Shared-entity-store hardening** — route cross-book entity writes through
   DynamoEntityStore with conditional/idempotent updates.
5. **Global dedup barrier** — quiescence detection across per-book locks.
6. **Load test** on a subset of WWIIArchives (e.g. 20 docs) before full drain.

---

## 8. Open decisions

- Pool size / per-task Grok rate — set after querying live quotas
  (`aws service-quotas get-service-quota`) and the Grok plan's rate limit.
- Dispatcher: extend the trigger Lambda, or adopt **Step Functions** (Map state
  with `MaxConcurrency` gives limit-aware fan-out for free) — the existing
  "Step Functions pipeline orchestration" TODO aligns here.
- Dedup barrier policy for stragglers (timeout vs wait).
- Whether zips/rar in the archive are auto-expanded before dispatch (238 PDFs are
  direct; 204 zips need an unpack stage).
