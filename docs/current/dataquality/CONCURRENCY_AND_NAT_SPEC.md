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

## 4. NAT management under concurrency (the lifecycle rule)

**Rule:** NAT is a **shared, reference-counted** resource.
- **UP** whenever ≥1 compute task (any phase, any book) is running or queued.
- **DOWN** only at: (a) the **global dedup human-review gate** (async, indefinite
  — no compute pending), and (b) **full backlog drain** (queues empty, no locks).
- **Never** torn down between compute phases or between books while work remains.

**Mechanism:**
- Replace per-phase delayed-teardown with a **NAT reference count** in DynamoDB
  (`nat#refcount`): each task increments on launch, decrements on exit/SIGTERM;
  `nat_manager` tears down only when count reaches 0 **and** no pending queue
  entries. This fixes the current race (Phase 1 teardown stranding Phase 2 launch)
  by construction — teardown can't happen while the count > 0.
- Keep the **2h no-tasks guardrail** as a cost backstop (safe: it checks for
  running tasks / locks first).
- At the **dedup gate**, drop NAT deliberately (refcount → 0, human review is
  async) and recreate when review completes (existing dedup-complete → trigger →
  `_wait_for_networking`). This matches the confirmed intent (dedup + the OCR
  markdown-review + bibliography-disposition gates are async human pauses).

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
2. **NAT refcount** (DynamoDB) replacing per-phase delayed-teardown (fixes the
   race AND is required for safe concurrency).
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
