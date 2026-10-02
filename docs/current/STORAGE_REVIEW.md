# Data Storage Review

Decision record from the 2026-10-02 storage review: how structured data, vector
search, and object storage should be organized, what was changed, and what is
deliberately deferred. Grounded in the actual code + live AWS state (not generic
guidance). Companion: `.kiro/steering/architecture-decisions.md` (the recorded
pgvector/Aurora direction this affirms).

## Cost (the equation behind the decisions)

Figures are **actual Sept MTD** from Cost Explorer + **current us-east-1 list
prices** for comparison. Treat forward numbers as directional — use the AWS
Pricing Calculator / Cost Explorer for authoritative projections.

### Where the money actually is (Sept actual)
| Service | Sept actual | Note |
|---|---|---|
| **S3** | **$77.54** | breakdown below — it is ~78% plain Standard storage |
| DynamoDB | **$1.79** | confirms "smallest line item" — storage+requests for ~38 MB |

**S3 by usage type (the important part):**
| Usage type | $ | Reading |
|---|---|---|
| `TimedStorage-ByteHrs` (Standard) | **60.37** | the real lever — plain Standard storage |
| `TimedStorage-GDA-ByteHrs` (Deep Archive) | 15.96 | **transient/legacy** — 0 GDA objects remain now; something was archived then deleted |
| `EarlyDelete-GDA` | 0.66 | GDA 180-day-min early-deletion penalty (confirms the GDA objects were deleted early — one-off) |
| All requests (Tier1-4, GIR) | **~0.53** | **negligible** |
| Glacier-IR / SIA storage | ~0.00 | our just-moved cold binaries (not yet accrued) |

### Two corrections this forced to the review
1. **The 45k-object `output/` sprawl costs ~$0.53/mo in requests — negligible.**
   The earlier "object-count cost" worry was overstated. The consolidation's value
   is Phase-3 **latency** + Postgres-load-prep, essentially **not dollars** —
   reinforcing its "deferred, efficiency-only" status.
2. **~$60 of the $77 is plain Standard storage**, so the cold-binary → Glacier-IR
   moves are the correct and only material S3 lever. The $16 Deep-Archive line is
   **not recurring** (zero GDA objects remain; it + the early-delete penalty are a
   one-off from a prior archive that was deleted).

### Storage-class cost comparison (list price, /GB-month, us-east-1)
| Class | $/GB-mo | vs Standard | Our use |
|---|---|---|---|
| S3 Standard | 0.023 | — | active markdown, hot JSON |
| Standard-IA | 0.0125 | −46% | `output/` after 30d (existing rule) |
| **Glacier IR** | **0.004** | **−83%** | **source/ + NARA PDFs + tagged media (this session)** |
| Glacier Flexible | 0.0036 | −84% | not used (restore needed) |
| Deep Archive | 0.00099 | −96% | NOT used — would break the immediate-read ingestion path |
| DynamoDB (on-demand storage) | 0.25 | 11× Standard | entity tables (~21 MB) + cache (~17 MB) |

### Projected effect of this session's S3 work
~**6.8 GB** of cold large binaries (source/ 0.53 GB + NARA 3.29 GB + media ~2.9 GB)
move Standard→Glacier IR. At list price that slice drops from ~$0.023 to ~$0.004
/GB-mo — roughly a **$0.13/mo saving on those bytes**… which exposes the real
story: **at 6.8 GB the absolute storage dollars are small**; the Standard line is
$60 because of the TOTAL footprint over time + byte-hours, not because any single
prefix is huge. The Glacier-IR moves are correct and proportionate, but the
**biggest future cost lever is not storage class — it is corpus GROWTH** (the
502 GB WWIIArchives backlog). At 500 GB, Standard = ~$11.50/mo vs Glacier-IR
~$2/mo — THAT is where the ~83% class saving becomes real money, so the lifecycle
rules + the archive-tag discipline matter most as the corpus scales.

### DynamoDB cost perspective
DynamoDB storage is **$0.25/GB-mo — 11× S3 Standard** — but on ~38 MB it is
$1.79/mo total. Cost is NOT the reason to migrate the entity tables (fit +
Scan-efficiency is); and the coordination KV is far too small to matter. Do not
make the DynamoDB decision on cost grounds.

### Budget implication
The stale **$75 budget** (TODO item) is unrealistic given corpus growth: S3 alone
is $77. Reset it to reflect the real steady-state (storage + bursty GPU/Fargate +
Grok billed separately), using Cost Explorer trend data — not a guess.

---

## TL;DR

- **pgvector single-store (Postgres/Aurora) for RAG** — the recorded decision is
  sound; **affirmed**. Build it *after* ingestion is schema-stable (per the phased
  plan), not now.
- **DynamoDB** — keep it, but it plays **3 roles**; split them: remove the entity
  *materialization* copy (→ Postgres later), **preserve** the concurrency-safe
  *merge*, **keep** the tiny *coordination* KV on DynamoDB long-term.
- **S3 cost** — **done this session**: ~6.8 GB of cold large binaries moved to
  Glacier IR (source/, NARA PDFs, tagged media).
- **S3 JSON layout** (45k per-entity objects) — consolidation into per-type
  NDJSON/Parquet is a **performance/cost optimization, NOT a correctness fix**;
  **deferred** until Phase-3 bulk-read time is a felt bottleneck OR the Postgres
  load is built, whichever first.

---

## Current architecture (what the code does today)

Entities are **dual-written**:

1. **S3 `output/{type}/{slug}_{ULID}.json`** — one JSON file per entity (~45,273
   objects / 163 MB). Canonical, human-inspectable, git-diffable; what the
   website/loader read. **Source of truth.**
2. **DynamoDB `dev-wwii-api-cache`** — `entity#{type}#{id}` items with the full
   JSON in a `data` attribute + projected columns (`name`, `enrichment_status`,
   `book`). ~32 MB / ~5,900 items.

Two **bulk-read** paths compete to be "fast":
- `ecs_modules/s3_sync._materialize_from_dynamo` → **11 sequential `list_all`
  Scans** (one per entity type), each a filtered full-table `Scan`.
- `s3_sync_down` → `list_objects_v2` + **per-object `download_file`** (thousands of
  serial GETs).

## Findings (evidence-based)

1. **DynamoDB entity *materialization* is the weak link.** `list_all` /
   `query_unenriched` (`src/utils/entity_store.py`) use `Scan` with a
   `begins_with(cache_key, "entity#type#")` filter — a filtered Scan reads/bills
   the WHOLE table then discards non-matches, the same "crawl everything" cost it
   was meant to beat. It is a redundant copy of data that already lives
   authoritatively in S3, read via an anti-pattern.
2. **DynamoDB `merge_entity` genuinely earns its keep.** It uses version-
   conditional writes so two books extracting the same entity ("Eisenhower") don't
   clobber each other's `event_mentions`. This replaced flock-guarded S3 writes
   that were per-host, NOT cross-host safe. Real concurrency correctness S3 alone
   cannot provide — must be preserved if entities move.
3. **S3 JSON is correct; the per-object LAYOUT is the cost/perf driver.** 163 MB is
   trivial; **45k objects** is not — Phase-3 bulk reads pay per-object GET cost +
   serial latency. Same anti-pattern as the Scan, in S3 form.

## Decisions

### pgvector / RAG — AFFIRMED (deferred execution)
Single Postgres store (pgvector + HNSW on Aurora Serverless v2, scale-to-zero) is
the right call: entity data is tiny, queries need relational flexibility +
semantic search together, and the bursty/idle workload fits scale-to-zero. Build
it only after the corpus is schema-stable (RAG is a derived layer). Resolve the
embedding dimension BEFORE creating the schema (`content_chunks VECTOR(?)`) —
changing it later means re-embedding everything. Consider Neon/Supabase for the
prototype tier.

### DynamoDB — KEEP, split by role
| Role | Decision | Rationale |
|---|---|---|
| Entity materialization (list_all Scans) | **Remove** → Postgres (with RAG phase) | Redundant copy + Scan anti-pattern; Postgres is the eventual query store |
| Entity merge safety (version-conditional writes) | **Move** → Postgres row-lock / `ON CONFLICT` (with RAG phase) | Real concurrency correctness — preserve, don't drop |
| Coordination (locks, `pending#*`, NAT leases, `batch_job#`) | **Keep on DynamoDB** long-term | Tiny KV, atomic conditional writes + TTL — DynamoDB's sweet spot |
Do NOT remove DynamoDB wholesale now — that would reintroduce the cross-host merge race.

### S3 object storage — DONE this session
Large binaries are read ONLY during ingestion (operator-confirmed), so cold
classes are safe; Glacier **Instant** Retrieval (not deeper) chosen so an
auto-triggered re-OCR's immediate read still works (no restore). Live:
- `source/` PDFs → Glacier IR.
- `contentrepository/NARA/` (92 PDFs, 3.29 GB) → Glacier IR (prefix rule).
- Consumed media/large images → Glacier IR via `archive=cold` **tag** (set by
  `phase0_video`/`phase0_convert` once the chapter is written;
  `src/ingestion/archive_tag`); existing mkv+mp4 (~2.9 GB) backfilled.
~6.8 GB total moved to Glacier IR (~83% cheaper on that slice).

### S3 JSON consolidation — DEFERRED (optimization, not necessary)
Consolidating `output/{type}/*.json` (45k objects) into one NDJSON/Parquet per
entity type would: cut Phase-3 bulk-read GET cost + latency, remove the need for
`_materialize_from_dynamo`, and pre-stage the Postgres/pgvector load (Parquet =
columnar + natural load format). **But it is purely efficiency** — the per-entity
JSON is functionally correct and the website/loader read it fine today. **Not a
correctness fix.** Do it when EITHER:
- Phase-3 bulk-read time becomes a felt operational bottleneck (the
  "S3 is slow on Phase 3" symptom), OR
- the Postgres load is built (consolidate as the load-prep step),
whichever comes first. Until then, leave the per-entity JSON as-is.

## Non-goals / explicitly not now
- Don't rip out DynamoDB (breaks concurrency-safe merge).
- Don't build Postgres/pgvector before ingestion is complete (re-embed/re-schema risk).
- Don't go deeper than Glacier IR for ingestion-read binaries (would break the
  immediate-read path with a restore requirement).
