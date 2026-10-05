# Data Storage Review

How this project's structured data, vector search, and object storage should be
organized — what to change, what to keep, and what to defer. Grounded in the
actual code + live AWS state (Cost Explorer actuals + current us-east-1 list
prices), not generic guidance.

*Reviewed 2026-10-02. Companion: `.kiro/steering/architecture-decisions.md`.*

---

## Recommendations (start here)

1. **RAG store → PostgreSQL + pgvector, single store.** Affirmed. **Build it after
   ingestion is complete**, not now (it's a derived layer; building early risks
   re-embedding). Resolve the embedding dimension before creating the schema.
   *DynamoDB native vector search (GA 2026-08) was weighed and not adopted —
   exact-match-only filtering can't serve our relational/range/join queries, and it
   would reverse the plan to retire the DynamoDB entity copy; see Decisions. Revisit
   if the query profile turns out mostly find-similar + exact-match.*

2. **Keep DynamoDB — but split its 3 roles.** It is not one decision:
   - *Entity materialization* (the full-table `Scan` reads) → **remove**, fold
     into Postgres with the RAG phase (it's a redundant copy of S3 data read via
     an anti-pattern).
   - *Concurrency-safe merge* (version-conditional writes) → **preserve** (move to
     Postgres row-locking when entities move; never just drop it).
   - *Coordination KV* (locks, queues, NAT leases, batch jobs) → **keep on
     DynamoDB** long-term; it's the sweet spot.

3. **S3 cold-binary archiving — DONE this session.** ~6.8 GB of terminal, cold,
   ingestion-only binaries moved to Glacier **Instant** Retrieval (so reads stay
   millisecond — no performance hit). Keep doing this via the lifecycle rules +
   `archive=cold` tagging as the corpus grows.

4. **Cost reality: storage is not the problem; growth-time compute is.** At today's
   7 GB, storage is ~$0.16/mo. Even the full 502 GB backlog is ~$2/mo on
   Glacier-IR. The real recurring cost is **LLM/Grok inference + bursty GPU OCR**,
   not storage. Optimize those (process-once discipline), not bytes.

5. **Reset the $75 budget.** It predates the corpus; S3 alone already bills ~$77
   (mostly byte-hour churn + a one-off legacy line, not steady-state). Set a
   realistic figure from Cost Explorer trend data.

6. **Deferred, not needed now:** consolidating the 45k per-entity JSON files into
   per-type NDJSON/Parquet. It's an efficiency/latency optimization (and
   Postgres-load prep), **not a correctness fix** — do it when Phase-3 reads become
   a felt bottleneck or when the Postgres load is built.

## At a glance

| Area | Decision | Status | Cost impact |
|---|---|---|---|
| RAG / vector | Postgres + pgvector single store | Affirmed, **deferred** to post-ingestion | DB is smallest line item |
| DynamoDB | Keep; split 3 roles (migrate materialization, keep coordination) | Planned (with RAG phase) | $1.79/mo — not a cost driver |
| S3 cold binaries | Glacier-IR via lifecycle + `archive=cold` tag | **Done + live** | tiny now; ~83% at scale |
| S3 JSON layout | Consolidate to NDJSON/Parquet | **Deferred** (efficiency only) | ~$0.53/mo — negligible |
| Budget | Reset the stale $75 limit | **TODO** | — |

---

## Supporting detail

### Cost model (actuals + projections)

Figures: **actual Sept MTD** from Cost Explorer + **current us-east-1 list
prices**. Forward numbers are directional — use AWS Pricing Calculator / Cost
Explorer for authoritative projections.

**Sept actual:** S3 **$77.54**, DynamoDB **$1.79**.

**S3 by usage type** — the important part:
| Usage type | $ | Reading |
|---|---|---|
| `TimedStorage-ByteHrs` (Standard) | 60.37 | byte-hours — mostly CHURN, not current 7 GB resident |
| `TimedStorage-GDA-ByteHrs` (Deep Archive) | 15.96 | **transient/legacy** — 0 GDA objects remain now |
| `EarlyDelete-GDA` | 0.66 | one-off penalty (GDA objects deleted early) |
| All requests (Tier1-4, GIR) | ~0.53 | **negligible** |

> **Key correction:** 7 GB at Standard is only **$0.16/mo**. The $60 line is
> byte-hours from data resident *earlier in the month* (large objects written then
> moved/deleted) — so the lever is **churn discipline**, not storage class. And the
> 45k-object JSON sprawl costs ~$0.53/mo — negligible (its consolidation is a
> latency/load-prep win, not a cost win).

**Storage-class comparison** (list price, $/GB-mo, us-east-1):
| Class | $/GB-mo | vs Standard | Our use |
|---|---|---|---|
| S3 Standard | 0.023 | — | active markdown, hot JSON |
| Standard-IA | 0.0125 | −46% | `output/` after 30d |
| **Glacier IR** | **0.004** | **−83%** | **cold binaries (this session)** |
| Glacier Flexible | 0.0036 | −84% | not used (restore needed) |
| Deep Archive | 0.00099 | −96% | not used — would break immediate-read ingestion |
| DynamoDB storage | 0.25 | 11× Standard | entity tables + cache (~38 MB) |

**Projected scenarios** (scale anchors: NARA PDF ≈36 MB/scanned-vol; HyperWar
Green Books = HTML + a few map JPGs, **not** scans, ~150 vols; WWIIArchives backlog
≈502 GB mostly scanned):
| Scenario | Added | Storage $/mo |
|---|---|---|
| Current (7.1 GB) | — | $0.16 STD → **~$0.04 after cold→GIR** |
| +400 NARA PDFs | +14.4 GB | +$0.06 (GIR) vs +$0.33 (STD) |
| +150 HyperWar vols | +3.6 GB | **+$0.01** — it's text |
| Full 502 GB backlog | +502 GB | **$2 (GIR)** vs $12 (STD) vs $0.50 (Deep Archive) |
| Entity JSON + DynamoDB @10× | ~2 GB | $0.14 — never the cost |

**The performance-vs-cost edge (explicit):** Glacier-IR (not Deep Archive) was
chosen to KEEP performance — ingestion reads stay millisecond, no restore. Active
markdown + hot JSON stay on Standard so Phase-1/serving isn't penalized. Cost
control is applied ONLY to terminal cold binaries → no performance penalty. The
one thing to watch at scale: byte-hour churn during bulk OCR (write-then-archive),
already minimized by the lifecycle rules + tag-at-chapter-write.

### Current architecture (what the code does today)

Entities are **dual-written**:
1. **S3 `output/{type}/{slug}_{ULID}.json`** — one JSON per entity (~45,273 objects
   / 163 MB). Canonical, human-inspectable, git-diffable; what the website/loader
   read. **Source of truth.**
2. **DynamoDB `dev-wwii-api-cache`** — `entity#{type}#{id}` items with the full JSON
   in a `data` attribute + projected columns. ~32 MB / ~5,900 items.

Two **bulk-read** paths compete to be "fast":
- `ecs_modules/s3_sync._materialize_from_dynamo` → 11 sequential `list_all` Scans.
- `s3_sync_down` → `list_objects_v2` + per-object `download_file` (thousands of GETs).

### Findings (evidence-based)
1. **DynamoDB entity materialization is the weak link.** `list_all`/
   `query_unenriched` (`src/utils/entity_store.py`) use `Scan` with a
   `begins_with` filter — reads/bills the WHOLE table then discards non-matches. A
   redundant copy of S3 data read via an anti-pattern.
2. **DynamoDB `merge_entity` earns its keep.** Version-conditional writes stop two
   books clobbering the same entity's `event_mentions` — replaced flock-guarded S3
   writes that weren't cross-host safe. Real correctness S3 alone can't provide.
3. **S3 JSON is correct; the per-object LAYOUT is the perf driver.** 163 MB is
   trivial; 45k objects is not — Phase-3 pays per-object GET latency (not dollars).

### Decisions (detail)

**pgvector / RAG — affirmed, deferred.** Single Postgres store (pgvector + HNSW,
Aurora Serverless v2 scale-to-zero): entity data is tiny, queries need relational
flexibility + semantic search together, workload is bursty/idle. Build after the
corpus is schema-stable. Resolve embedding dimension first. Consider Neon/Supabase
for the prototype tier.

**Alternative weighed: DynamoDB native vector search** (GA 2026-08; store
embeddings on an item, `SearchVectors` API, single-digit-ms, up to 4096 dims).
Considered and **not adopted** for this workload — reasons, including cost:
- **Query fit (decisive).** DynamoDB inline filters are **exact-match only** — no
  range (`BETWEEN`/`BEGINS_WITH`) and each search is scoped to one partition-key
  value. Our audiences need **range + join** queries (date ranges, place/unit
  joins, citation resolution via `EventID` FK, genealogical entity-centric
  lookups). pgvector-in-Postgres serves semantic + relational + range in one SQL
  query; DynamoDB cannot express the relational side.
- **Trajectory fit.** Its win ("data already in DynamoDB → no second store") does
  NOT apply: our entities live in **S3 JSON** (source of truth) + a DynamoDB
  *derived copy* this review decided to RETIRE. Adopting DynamoDB-vector would mean
  un-retiring and expanding that store — reversing the plan.
- **Cost.** DynamoDB-vector removes the "cheaper/simpler single store" angle for
  us: index bills on **bytes written + bytes stored + bytes processed per search**
  (`VectorSearchRequestBytes`), on-demand only, and DynamoDB storage is **11× S3
  Standard/GB**. At our tiny data size the absolute $ is immaterial either way, so
  cost is **not** a differentiator — but it is NOT the cheaper option at rest, and
  per-search byte-processing cost grows with dimensions. Aurora Serverless v2
  scale-to-zero bills storage-only when idle (our mostly-idle profile), which is
  the better cost fit for bursty ingestion + occasional search.
- **Where it WOULD win** (revisit triggers): if the query profile turns out to be
  mostly "find-similar + exact-match filter" (not relational/range), OR if entities
  end up primarily living in DynamoDB. Neither holds under the current plan.
Net: pgvector retained primarily on **query fit** (exact-match-only filtering can't
serve the relational/range needs), with trajectory + cost reinforcing — not
flipping — the call.

**DynamoDB — keep, split by role:**
| Role | Decision | Rationale |
|---|---|---|
| Entity materialization (Scans) | Remove → Postgres (RAG phase) | redundant copy + Scan anti-pattern |
| Merge safety (conditional writes) | Move → Postgres row-lock/`ON CONFLICT` | real concurrency correctness |
| Coordination (locks/queues/leases/jobs) | Keep on DynamoDB | tiny KV + atomic writes + TTL |

**S3 object storage — done this session.** Large binaries are read ONLY during
ingestion, so cold classes are safe; Glacier IR (not deeper) keeps immediate reads
working. Live: `source/` PDFs → GIR; `contentrepository/NARA/` (92 PDFs, 3.29 GB)
→ GIR (prefix rule); consumed media/large images → GIR via `archive=cold` tag (set
by `phase0_video`/`phase0_convert` once the chapter is written; existing mkv+mp4
~2.9 GB backfilled). ~6.8 GB total.

**S3 JSON consolidation — deferred (efficiency, not correctness).** Consolidating
`output/{type}/*.json` (45k objects) → per-type NDJSON/Parquet would cut Phase-3
read latency, remove the need for `_materialize_from_dynamo`, and pre-stage the
Postgres load. But the per-entity JSON is functionally correct today. Do it when
Phase-3 reads are a felt bottleneck OR when the Postgres load is built.

### Non-goals (explicitly not now)
- Don't rip out DynamoDB (breaks concurrency-safe merge).
- Don't build Postgres/pgvector before ingestion is complete (re-embed/re-schema risk).
- Don't go deeper than Glacier IR for ingestion-read binaries (restore would break
  the immediate-read path).

---

## Related documents
- [Code Architecture](core/CODE_ARCHITECTURE.md) — where the storage backends (`src/utils/storage.py`, `backends.py`) sit in the module map.
- [AWS Deployment](AWS_DEPLOYMENT.md) — S3 / DynamoDB / lifecycle infrastructure that this review covers.
- [Security Posture](SECURITY_POSTURE.md) — S3 encryption / TLS / lifecycle controls referenced here.
