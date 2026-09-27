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

| Resource | Limit type | Concern at scale | Mitigation |
|----------|-----------|------------------|------------|
| **Fargate tasks / vCPU** per region | Soft (raisable) | Pool of N book pipelines × tasks may exceed the account Fargate vCPU quota | Cap pool to fit quota; request increase; check `service-quotas` before launch |
| **Fargate Spot capacity** | Practical | Spot may be unavailable → tasks stuck PROVISIONING | 4:1 spot:on-demand fallback already set; monitor; consider capacity-optimized |
| **AWS Batch (Chandra GPU)** vCPU / GPU | Soft | Many concurrent OCR jobs exceed GPU queue capacity | Batch job queue naturally queues; cap concurrent submissions; the GPU instance count / spot GPU availability is the real ceiling |
| **Grok API rate limits** | Hard (vendor) | N concurrent books × parallel chapters → burst of Grok calls → 429s | **Global** token-bucket/rate-limiter across tasks (shared counter in Dynamo or a fixed per-task cap × pool size ≤ vendor limit); batch API already smooths; honor 429 backoff |
| **NAT Gateway** | Soft (per-AZ) | Single NAT is fine; don't spawn per-book NATs | Keep ONE shared NAT (§4) |
| **Elastic IPs** | Soft (5/region default) | NAT churn leaked EIPs before (fixed) | Reuse; the create path already releases orphaned EIPs |
| **DynamoDB throughput** | Soft (on-demand scales) | Concurrent entity writes spike WCU | On-demand billing mode; exponential backoff on throttling; conditional writes |
| **S3 request rate** | Effectively high | Many parallel syncs | Prefix sharding by book (already per-book prefixes); standard retry |
| **CloudWatch Logs / API throttling** | Soft | Many tasks logging | Standard; unlikely to bind first |

**Principle:** the dispatcher reads the **binding limit** (likely Grok rate limit
and Fargate vCPU quota first) and sizes the pool to the *minimum* headroom across
limits. Prefer **queue + backpressure** over launch-and-fail. Make the pool size
and per-task Grok rate a **config** so it can be tuned to the current quotas.

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
