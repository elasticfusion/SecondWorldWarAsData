# RAG Vector-Store Trade Study: Postgres+pgvector vs. Amazon S3 Vectors

**Status:** decided 2026-09-26 — **keep Postgres + pgvector (single store)**.
S3 Vectors evaluated and rejected for this workload's query model.
**Context:** revisits the store choice in `docs/SCHEMA_DESIGN.md` after the
S3 Vectors + EKS pattern (AWS blog, "Building self-managed RAG applications with
Amazon EKS and Amazon S3 Vectors"). RAG remains a **deferred / Phase-2** concern
(see `.kiro/steering/project-overview.md`); this records the decision so it is
not re-litigated later.

## Decision

Retain the `SCHEMA_DESIGN.md` design: **one PostgreSQL (Aurora Serverless v2)
store holding relational entities + pgvector/HNSW embeddings + PostGIS geo**.
Do **not** adopt S3 Vectors as the vector store.

## What prompted the review

- **S3 Vectors** (preview): an S3 bucket type that stores + indexes embeddings
  natively and does similarity search via API (`PutVectors`/`QueryVectors`) with
  scalar **metadata filtering** — no vector database to run. Attractive on
  **cost-at-idle** (pure pay-per-use storage/query, no always-on DB floor) and
  **zero vector-store ops**, both of which align with the project's cost-first
  constraint.
- **ECS vs. EKS:** settled — the blog's EKS is incidental. S3 Vectors is
  compute-agnostic (it's a storage/query API); any caller works. If S3 Vectors
  were adopted, **ECS Fargate (or Lambda) would host the app, not EKS.** EKS is
  not required.
- **Preview status:** accepted as a non-blocker by the owner (RAG is deferred;
  S3 Vectors may be GA by build time).

## The deciding factor: the query model is (B) blended

The intended search UX is **(B): a single query that blends semantic meaning
with structured facets and geo, co-ranked into one result set** — not (A)
separate semantic-search and faceted/map features.

Canonical target query:

> passages *describing armored counterattacks* [semantic] in *March 1945*
> [date] *within 50 km of the Rhine* [geo] where *casualties > 500*
> [structured] — **ranked together.**

## Why S3 Vectors serves blended queries poorly

1. **Similarity index, not a query engine — no joins.** `QueryVectors` returns
   nearest neighbors + optional filtering on metadata *stored on each vector*.
   It cannot **join**. But the data is deliberately event-centric and
   normalized: a chunk → sub-events → places/dates/casualties via the `mentions`
   junction. A blended query must traverse those relationships; S3 Vectors
   can't, pgvector (same SQL DB) does natively.

2. **Filtering requires denormalizing every facet onto every vector — and it
   goes stale.** S3 Vectors filters only on metadata copied into each vector.
   Flattening date/place/casualties onto each chunk fails because (a) chunks map
   to sub-events with *one-to-many* places/dates/casualty rows (no clean flatten)
   and (b) those values are **derived and still changing** (geocoding backfill,
   casualty `PeopleGroupID` fixes, dedup merges) — every change would force
   rewriting vector metadata, duplicating the source of truth. pgvector reads
   the live typed columns at query time: no duplication, no staleness.

3. **Geo-radius is a hard blocker.** "Within 50 km of the Rhine" is a PostGIS
   `ST_DWithin` spatial computation. S3 Vectors metadata filtering is scalar
   predicates only — no spatial distance. The map query path (`places.geom`
   proximity) has **no S3 Vectors equivalent**; a bounding-box approximation is
   crude and cannot do true radius or polygon proximity.

4. **Cannot co-rank the signals.** (B) wants results ranked by a *combination*
   of semantic relevance and structured constraints. With S3 Vectors the vector
   score and the facet filters live in different systems — no single pass can
   `ORDER BY` an expression mixing `embedding <=> q` with typed columns.
   pgvector can.

5. **The workaround reintroduces two stores and keeps Postgres anyway.** The
   only way to approximate (B) — pre-filter the structured store for candidate
   IDs, pass them to `QueryVectors` — means **still operating a full
   Postgres+PostGIS store** for the facets/geo, *plus* S3 Vectors. That is
   exactly the two-store cost/ops the single-store decision rejected, and it
   keeps the Postgres you were trying to avoid. Net simplification is negative.

## Cost caveat (recorded honestly)

pgvector on Aurora Serverless v2 is **not as cheap-at-idle** as S3 Vectors —
that was S3 Vectors' genuine appeal. We accept Aurora's idle-cost floor as the
price of blended-query capability, mitigated by:
- **scale-to-0 ACU** auto-pause when idle (storage-only billing),
- **static-first + CDN** serving so most traffic never hits the DB,
- **API Gateway usage plans** (Phase 2) capping live-search spend.

## Where S3 Vectors could still fit (future, optional)

If a cheaper embedding-search tier is ever wanted for the **pure-semantic path
only** (query #1, self-contained), S3 Vectors could host *just* those vectors
without moving the structured store — an additive option, not a replacement.
Not pursued now.

## Follow-ups

- `SCHEMA_DESIGN.md` "decisions to confirm" — embedding model + dimension still
  open (Grok has no embeddings endpoint; needs Bedrock Titan/Cohere or OSS).
- Revisit if S3 Vectors adds joins/spatial filtering at GA (unlikely to change
  the join/geo reasoning).
