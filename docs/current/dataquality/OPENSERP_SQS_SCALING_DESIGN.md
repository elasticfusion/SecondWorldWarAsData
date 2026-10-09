# OpenSERP Enrichment at Scale — SQS + Sidecar Worker Design

**Status:** DESIGN (not yet implemented) · **Author:** pipeline team · **Date:** 2026-10-08
**Supersedes (as the scale target):** the interim "single-coordinator → N OpenSERP backends"
sketch discussed in session. That remains a valid small-scale increment; this doc is the
Spot-resilient, horizontally-scalable architecture we commit to for corpus-scale Phase-3
OpenSERP enrichment.

---

## 1. Problem

Phase-3 OpenSERP enrichment is slow and serial. A 10-entity scoped run took ~37 min because
every query drives headless-Chromium search across ~5 engines, then Grok verify + per-URL
fetch/summarize — all in **one** process against **one** OpenSERP task. Measured per-run metrics
(`output/metrics/openserp_metrics.json`) confirm OpenSERP itself is healthy (0 zero-result,
0 breaker trips, ~8 results/query); the bottleneck is **throughput**, not correctness.

The real bottleneck is **one Chromium doing sequential multi-engine search** (OpenSERP
`core/browser.go:512` documents the sequential-dispatch constraint). Scaling requires **more
browsers running concurrently**, each fed independently, with work coordination that:

- never lets two workers search the same entity ("no multiple grabs at the apple"),
- survives **Spot interruption** and worker/coordinator crashes with automatic retry,
- scales horizontally with **no single coordinator** as a bottleneck or SPOF,
- marks work "done" exactly once, durably.

SQS provides all four natively. We reject a bespoke DynamoDB work-queue (reinventing SQS) and the
single-coordinator model (coordinator is the ceiling + SPOF).

---

## 2. Architecture (SQS + sidecar)

```
            ┌───────────────────────┐
Phase-3  →  │  ENQUEUE pass          │  one cheap pass over entity files:
 producer   │  (producer, 1 task)    │  for each entity needing search (not already
            │                        │  openserp_searched within TTL) send ONE SQS
            └───────────┬────────────┘  message {entity_path, entity_type, book}.
                        ▼                 No searching here — just enqueue.
            ┌────────────────────────────────────────────┐
            │  SQS  <env>-wwii-openserp-work  (standard)  │ durable backlog;
            │  [e1][e2][e3] … [eN]                        │ in-flight = invisible
            │  RedrivePolicy → DLQ (maxReceiveCount=5)    │ (visibility timeout)
            └────────────────────────────────────────────┘
                 │ (long-poll receive, batch=1)
      ┌──────────┴───────────┬───────────────────┬─────────────────┐
      ▼                      ▼                   ▼                 ▼
 ┌──────────────┐    ┌──────────────┐    ┌──────────────┐   ┌──────────────┐
 │ Worker task 0│    │ Worker task 1│    │ Worker task 2│ … │ Worker task M│  ECS Service
 │ ┌──────────┐ │    │ ┌──────────┐ │    │ ┌──────────┐ │   │ ┌──────────┐ │  desiredCount=M
 │ │ worker   │ │    │ │ worker   │ │    │ │ worker   │ │   │ │ worker   │ │  (FARGATE_SPOT)
 │ │ container│ │    │ │ container│ │    │ │ container│ │   │ │ container│ │
 │ └────┬─────┘ │    │ └────┬─────┘ │    │ └────┬─────┘ │   │ └────┬─────┘ │
 │  localhost   │    │  localhost   │    │  localhost   │   │  localhost   │
 │   :7001      │    │   :7001      │    │   :7001      │   │   :7001      │
 │ ┌────▼─────┐ │    │ ┌────▼─────┐ │    │ ┌────▼─────┐ │   │ ┌────▼─────┐ │
 │ │ openserp │ │    │ │ openserp │ │    │ │ openserp │ │   │ │ openserp │ │  SIDECAR:
 │ │ Chromium │ │    │ │ Chromium │ │    │ │ Chromium │ │   │ │ Chromium │ │  1 browser
 │ └──────────┘ │    │ └──────────┘ │    │ └──────────┘ │   │ └──────────┘ │  per task
 └──────────────┘    └──────────────┘    └──────────────┘   └──────────────┘
      │                                                            │
      ▼  each worker, end-to-end per message:                     ▼
   1. receive (entity invisible for VisibilityTimeout)      results written to
   2. heartbeat: extend visibility while still working        the entity JSON file
   3. search localhost:7001 → verify (Grok) → fetch/summarize (filesystem ↔ S3)
   4. write entity file (atomic, schema-guarded, openserp_searched=True)
   5. DELETE message   ← the authoritative "done"
   on crash / Spot kill before step 5:
      message visibility lapses → reappears → another worker retries
      after maxReceiveCount retries → Dead-Letter Queue (+ alarm)
```

**Sidecar rationale (worker + OpenSERP in the SAME task):** the unit that can be Spot-reclaimed
is exactly the unit holding the message lease. If the task dies, both die together and the
message reappears cleanly — no orphaned browser, no half-leased work. Worker↔browser addressing
is trivial (`localhost:7001`, same as local dev). This is the clean, atomic failure story.

---

## 3. Components

### 3.1 Work queue + DLQ (new, `cloudformation/compute.yaml` or a new `enrichment.yaml`)

Mirror the existing `events.yaml` SQS+DLQ+QueuePolicy pattern.

```yaml
OpenSerpWorkQueue:
  Type: AWS::SQS::Queue
  Properties:
    QueueName: !Sub ${EnvironmentName}-wwii-openserp-work
    VisibilityTimeout: 900          # 15 min — see §5 sizing; extended via heartbeat
    MessageRetentionPeriod: 1209600 # 14 days
    ReceiveMessageWaitTimeSeconds: 20   # long-poll (cost/latency)
    RedrivePolicy:
      deadLetterTargetArn: !GetAtt OpenSerpWorkDLQ.Arn
      maxReceiveCount: 5

OpenSerpWorkDLQ:
  Type: AWS::SQS::Queue
  Properties:
    QueueName: !Sub ${EnvironmentName}-wwii-openserp-work-dlq
    MessageRetentionPeriod: 1209600
```

### 3.2 Message schema (standard queue; order-independent, idempotent)

```json
{
  "entity_path": "output/people/bruce c clarke.json",
  "entity_type": "people",
  "book": "TheArdennesBattleOfTheBulge",
  "enqueued_at": "2026-10-08T20:00:00Z",
  "schema": 1
}
```

Minimal by design — the message is a **pointer to the entity file** (the result/done store),
not a copy of entity data. Keeps messages < 256 KB trivially and avoids state duplication.

### 3.3 Enqueue pass (producer) — `phase3_enqueue_openserp.py` (new, thin)

A short pass (can run in the existing Phase-3 task or a tiny dedicated task):

```
for entity_type in [people, equipment, people_groups, places, source_section]:
    for f in glob(output/<type>/*.json):
        data = read(f)
        if openserp_searched(data) within TTL:   # idempotent gate (existing)
            continue
        sqs.send_message(OpenSerpWorkQueue, {entity_path:f, entity_type, book})
```

Uses `SendMessageBatch` (10/call) for throughput. Cheap, serial, no browser work.

### 3.4 Sidecar worker task def (new ECS service `<env>-wwii-openserp-worker`)

Two containers in one task:
- `openserp` — identical to today's OpenSERP container (`serve --host 0.0.0.0 --port 7001`),
  with a container healthcheck; `essential: true`.
- `worker` — the pipeline image, entrypoint = the new worker loop (§3.5); depends on the
  `openserp` container being `HEALTHY` (`DependsOn: condition: HEALTHY`).

Task sizing: reuse OpenSERP's `512 CPU / 1024 MB` for the browser + a modest allocation for the
worker (Chromium is the heavy one). `FARGATE_SPOT` weighted (as the current OpenSERP service).

### 3.5 Worker loop — `openserp_worker.py` (new; reuses existing enrichment code)

```
reset per-process state
while running and (not drain_requested):
    msg = sqs.receive_message(wait=20s, max=1, VisibilityTimeout=900)
    if not msg: 
        if idle_too_long: break        # lets the service scale to 0 (§7)
        continue
    start_heartbeat(msg)               # background: ChangeMessageVisibility every ~VT/3
    try:
        data = read(entity_path)
        if openserp_searched(data) within TTL:    # backstop: already done → skip
            sqs.delete_message(msg); continue
        enrich_ONE_entity(data, "http://localhost:7001", grok_client)  # reuse driver core
        write_json_with_lock(entity_path, data, entity=entity_type)    # done marker in file
        sqs.delete_message(msg)        # authoritative "done" — ONLY after a successful write
    except SpotInterruption:           # SIGTERM handler
        stop_heartbeat(); break        # do NOT delete → message reappears
    except TransientError:
        stop_heartbeat()               # do NOT delete → retry after visibility lapse
    except PoisonError:
        stop_heartbeat(); sqs.delete_message? NO → let maxReceiveCount route to DLQ
    finally:
        stop_heartbeat(msg)
```

**Refactor needed:** today's `enrich_*_with_openserp(dir, …)` iterate a *directory*. We extract the
per-entity body into `enrich_one_<type>(data, url, grok)` so both the batch driver (local/simple)
and the worker (one message = one entity) share identical logic. No behavior change to the search/
verify/fetch/metrics code itself — EXCEPT the verify step adopts prompt-level batching
(`_verify_results_batch`, §10): one Grok call per entity-query returning a per-result YES/NO array,
instead of one call per result. Fail-closed + per-result caching preserved.

---

## 4. Correctness — the three questions, answered by SQS

| Concern | Mechanism | Backstop |
|---|---|---|
| **Where is E?** | SQS message = *work*; entity JSON file = *result + done state* (filesystem ↔ S3) | — |
| **No double-search** | **Visibility timeout** hides an in-flight message from all other workers (cross-host, native) | `openserp_searched` TTL gate skips a re-delivered-but-done entity; 90-day URL verdict cache makes any partial replay a cache hit |
| **Mark done** | **Delete the message** — but only *after* the entity write succeeds | `openserp_searched=True` + `_at` persisted in the file (idempotent) |
| **Crash / Spot mid-entity** | message never deleted → visibility lapses → auto-redelivered | already-done URLs replay free from cache; per-entity redo bound |
| **Poison entity** | `maxReceiveCount=5` → **DLQ** + alarm; run is never blocked by one bad entity | manual inspection of DLQ |

The durability boundary is **per-entity** (one message). "Done" is a two-part invariant kept
consistent by ordering: *write file, then delete message.* A crash between them simply re-delivers;
the gate turns the retry into a near-noop.

---

## 5. Visibility-timeout + heartbeat sizing

OpenSERP browser search is slow and variable; one entity (several queries × 5 engines + verify +
fetch) can take minutes. Rules:

- **VisibilityTimeout = 900 s (15 min)** initial lease — generous headroom over a typical entity.
- **Heartbeat:** a background thread calls `ChangeMessageVisibility` every ~`VT/3` (5 min) while
  the entity is still processing, extending the lease so a genuinely slow entity is never
  prematurely re-delivered. Stops on completion/failure.
- **Hard cap:** if an entity exceeds e.g. 45 min wall-clock, stop heartbeating and let it
  redeliver/DLQ — treat as poison (prevents an infinite-heartbeat stuck worker).
- Too-short VT without heartbeat → double-processing (wasteful but *safe*: URL cache + gate make
  the duplicate cheap and the final write idempotent). Heartbeat makes it rare.

---

## 6. Failure taxonomy (unchanged tiers, now per-worker)

- **Node/transport** (OpenSERP sidecar unhealthy): the worker's own healthcheck + `openserp`
  container `essential:true` → ECS replaces the task; its in-flight message redelivers. No global
  breaker needed — each task is its own failure domain by construction (the big win over the
  single global breaker).
- **Query** (zero-results, OpenSERP 4xx): counted in metrics; not a failure; delete+continue.
- **Content** (verify-NO, WAF→Wayback-miss, dead URL): per-result, node-agnostic; the entity still
  completes and is marked done (absence of results is a valid outcome).
- **Spot interruption:** SIGTERM handler stops heartbeat and exits WITHOUT delete → redelivery.

Metrics (existing `output/metrics/openserp_metrics.json`) aggregate across workers via the shared
cache/metrics in S3; add queue-level observability in §8.

---

## 7. Scaling & lifecycle

- **Scale out:** set worker service `desiredCount = M` (config `openserp.worker_pool_size`,
  default 1 → identical to today). Workers are stateless; add/remove freely.
- **Autoscaling (phase 2):** target-tracking on `ApproximateNumberOfMessagesVisible` /
  backlog-per-worker, or step-scaling on queue depth. Start **manual** (set M at Phase-3 start),
  add autoscaling once measured.
- **Scale to zero:** worker loop exits after an idle period with an empty queue; a small
  controller (or the existing idle-reaper design in TODO.md) sets `desiredCount=0` when
  `ApproximateNumberOfMessages* == 0` for N consecutive checks. Mirrors the OpenSERP
  cost-discipline we already practice (NAT/OpenSERP up deliberately, torn down after).
- **Cost:** M× Spot task cost only while draining; FARGATE_SPOT + scale-to-zero bound it.

---

## 8. Observability & alerting

Reuse the project's alerting convention (publish free-form text to the
`<env>-wwii-phase2-complete` SNS topic → email + Slack formatter; alarms render in Slack directly):

- **CloudWatch alarms:** `ApproximateAgeOfOldestMessage` (stuck backlog),
  `ApproximateNumberOfMessagesVisible` (depth trend), **DLQ `ApproximateNumberOfMessagesVisible > 0`**
  (poison entities — actionable).
- **Per-run summary:** extend the existing OpenSERP metrics line with queue stats (enqueued,
  completed, redelivered, DLQ'd) so a run is reviewable at a glance (consistent with the metrics
  work already shipped).

---

## 9. Rollout plan (incremental, reversible)

1. **Refactor** `enrich_*_with_openserp` → extract `enrich_one_<type>(data, url, grok)` (pure
   refactor; batch driver still works; full gate + existing tests green). Ship alone.
2. **Add infra** (SQS work queue + DLQ + worker service/taskdef) with `worker_pool_size` default
   **0/1** and a kill-switch — deployed but dormant (mirrors how `dispatcher.yaml` shipped
   "deployed but NOT invoked"). No behavior change until turned on.
3. **Add producer** (`phase3_enqueue_openserp.py`) + **worker loop** (`openserp_worker.py`) with
   unit tests (receive→skip-if-done, write-then-delete ordering, heartbeat extension,
   SIGTERM-no-delete, poison→DLQ). Local tests mock boto3/SQS (no live AWS in CI).
4. **Live smoke** in AWS at `worker_pool_size=1` (== today's throughput, proves the loop end-to-end
   + idempotency), then raise to 3–4 and **measure** against the ~37-min baseline. Tear down.
5. **Autoscaling + scale-to-zero controller** once the manual pool is proven.

Each step is independently shippable and reversible; the kill-switch keeps `main` safe.

---

## 10. Decisions & open questions

### Decided
- **SQS vs. the DynamoDB/Step-Functions dispatcher → independent SQS.** OpenSERP work is
  intra-document, fine-grained, uniform, and Spot-centric; SQS visibility-timeout + redrive + DLQ
  are the exact primitives wanted. It rides its own queue, NOT the Step Functions dispatcher.
- **Grok rate across M workers → acceptable, because the total number of verify calls is
  independent of worker count (M only changes pace), and the account-wide cluster Grok limiter
  (`grok_client.py:47`, budget DIVIDED across the pool) already keeps the aggregate within the
  account rate.** Workers must obtain their slice from that cross-task limiter before raising M.
- **Batch the Grok verify calls → YES, via prompt-level batching (Option A) in the worker.**
  Today `_verify_result` makes ONE real-time Grok call PER result (~209 calls in the 10-entity
  run). Instead, verify ALL candidate results for an entity-query in a SINGLE call that returns a
  per-result YES/NO array. Cuts call count ~N×, lowering cost + wall-time + rate pressure, with no
  change to the synchronous worker loop. Requirements:
    * **Fail-closed preserved:** any missing/malformed array index → treat as NO (reject).
    * **Cache preserved:** pre-filter already-cached results out of the batch (so repeat runs stay
      free) and write per-result `openserp_verify` cache entries from the batched response, keyed
      exactly as today (`context|title|url`) — so the 90-day cache win is retained.
    * Implement as a new `_verify_results_batch(context, [results], grok)` → `List[bool]`, with
      `_verify_result` kept as the single-item fallback.
  NOT the async **xAI Batch API** (`batch_mode`) here: it is asynchronous (submit→poll→retrieve),
  which breaks the per-message receive→verify→write→delete loop. The Batch API (50% discount)
  remains the right tool for a separate OFFLINE bulk-verify mode if ever wanted, not the SQS worker.

### Open
- **Enqueue placement:** standalone tiny task vs. a first step inside the Phase-3 task. Lean:
  a step inside Phase-3 (one fewer moving part), guarded by the kill-switch.
- **Reserved vs Spot mix** for workers (progress guarantee vs cost): start pure Spot, add a small
  on-demand `Base` if interruptions stall completion.

---

## 10A. Rationale — why async Batch-API verify (Option B) is NOT used in the worker

The verify step must stay a **synchronous, prompt-batched real-time call (Option A)** inside the
SQS worker. The async **xAI Batch API** (`batch_mode` / `BatchCollector`) — the 50%-discount path
Phase 2 uses — is rejected *for the worker loop* because it inverts the control flow from
synchronous to asynchronous and shatters the self-contained message. Precisely:

1. **No answer to branch on.** In `batch_mode`, `grok_client.chat_completion` does not return a
   YES/NO — it *collects* the request (`BatchCollector.add`) and signals `BatchQueuedError`
   (`grok_client.py:103`). The worker's central line `answer = chat_completion(verify_prompt)`
   cannot exist; `search → verify → apply → write → delete` assumes the verdict is available *now*.

2. **Answer arrives minutes–hours later, in another process.** `poll_batch` polls at
   `interval=30s, max_hours=24` (`batch_api.py:276`); completion is detected by a separate
   EventBridge-scheduled Lambda (`batch_poller.py`, every 15–30 min) which triggers a separate ECS
   retrieve task. The verdict for a message received at T lands at **T + tens of minutes, in a
   different compute context.**

3. **One entity splits into a two-phase distributed saga.** Phase 1 (worker): search → collect
   verify requests → submit → cannot `write`/`delete` (no verdicts). Phase 2 (retrieve, later,
   elsewhere): map results → apply → write → delete the SQS message. A single logical unit now
   spans two executions separated by up to hours.

4. **The SQS lease fights the batch latency.** The message must stay invisible from search-time
   until the batch returns (potentially hours). Either the worker heartbeats
   `ChangeMessageVisibility` for the whole turnaround — so it **cannot exit or scale to zero** while
   batches are outstanding, defeating the Spot/scale-to-zero economics — or it releases the message
   and must re-correlate later.

5. **Re-correlation needs an external state store.** Mapping `batch_request_id →
   {sqs_receipt_handle, entity_path}` so the retriever knows which message to delete and which
   entity to write is exactly the DynamoDB coordination table we deliberately avoided — reintroduced
   solely to bridge the async gap. Receipt handles also expire with the lease (point 4).

6. **Partial-failure handling multiplies.** The entity's full candidate set must be held until the
   batch returns, then reassembled, with new fallback paths for partial batch failure
   (`num_pending`/`num_success`), per-request errors, and the 24 h `max_hours` cap — none of which
   the sync path needs.

**Contrast:** Option A (prompt-batching) only reduces the *number* of synchronous calls (verify all
of an entity-query's results in one call) while keeping each entity's work self-contained in one
message — no saga, no correlation store, no hours-long lease. Phase 2 tolerates the Batch API
because it is *already* an offline two-phase submit→poll→retrieve flow with no real-time consumer;
the SQS worker is the opposite by design.

**Where the 50% discount still applies:** a separate **offline bulk-verify mode** for full-corpus
backfill, reusing Phase 2's existing submit/poll/retrieve machinery — NOT bolted onto the worker.
Cost context: SQS is effectively free (standard queue $0.40 / **million** requests, 1 M/mo free, no
per-byte storage charge; a corpus of 10⁵–10⁶ entities at ~6 SQS actions each is $0–$2), whereas the
Grok token cost dominates by 3–5 orders of magnitude — so the Batch discount is worth capturing for
*bulk* passes, while SQS cost never factors into the architecture choice.

## 11. Why not the alternatives (recap)

- **Single coordinator → N dumb browsers:** coordinator does all verify/summarize → becomes the
  ceiling + SPOF; Spot-fragile. Fine as a quick interim, wrong as the scale target.
- **DynamoDB-as-work-queue:** reimplements visibility-timeout/redrive/DLQ that SQS gives natively.
  (DynamoDB is still the right place for *locks/leases/credit/Grok-rate* — different concern.)
- **Multiple browsers in one container:** fights OpenSERP's single-Chromium design
  (`browser.go:512` sequential dispatch) and would require forking upstream Go internals.
- **Step Functions Map (the existing `dispatcher.yaml`):** great for coarse per-document fan-out
  with rich coordination; heavier than needed for a long uniform OpenSERP backlog where SQS
  visibility + redrive are the exact primitives wanted.
```
