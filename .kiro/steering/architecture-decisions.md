# Architecture Decisions

Recorded design decisions for the website + search/API platform. Cost control
is a primary constraint throughout.

## Storage: PostgreSQL + pgvector (single store)

- Use **one** database for both structured/relational data and vector search,
  rather than splitting across a vector DB + DynamoDB. Avoids paying for and
  operating two stores, and website queries need flexible filtering/sorting
  that DynamoDB makes awkward.
- **pgvector with HNSW** indexing for semantic search. HNSW requires pgvector
  ≥ 0.5.0; recent Aurora PostgreSQL engine versions bundle pgvector 0.7/0.8
  (parallel HNSW builds). Pick a recent engine version.
- Semantic hits resolve back to full structured records + citations via the
  `EventID` foreign key.

## Hosting: Aurora Serverless v2 with scale-to-zero

- Aurora Serverless v2, PostgreSQL, **minimum capacity 0 ACU** (auto-pause).
  Workload is mostly idle (static serving + periodic ingestion), so the DB
  pauses when idle and bills storage only; compute only during ingestion
  bursts and live search.
- Trade-off: ~15s cold-start resume after idle. Fine for batch ingestion;
  when live search launches and must feel instant, consider a small non-zero
  floor or a cache in front.
- Cheaper alternatives to weigh at small scale: a small RDS `db.t4g` instance
  (predictable, always-on) or Neon/Supabase (true scale-to-zero, generous
  free tiers) for prototyping.

## Site: static-first + incremental regeneration

- Content is largely read-only → **pre-generate pages at build/ingest time**
  and serve as static content (S3 + CloudFront). Near-zero per-visit cost;
  LLM runs once per item at ingest, never per visitor.
- Continuous new data → **incremental** publishing: process only new items
  (OCR if needed → chunk → embed → LLM generate), regenerate only the
  affected pages (item page + its index/listing pages), invalidate only those
  CDN URLs. Cost scales with data volume, not traffic.
- Discipline: never re-embed / re-generate unchanged content. Track processed
  state (status flag / `_last_updated`) so each item is handled once.

## Cost priorities (biggest levers first)

1. LLM inference — dominate cost. Pre-generate + cache; call LLM once per item,
   not per visit. Small/cheap model for routine generation, larger only for
   hard cases.
2. Static serving behind CDN — traffic is effectively free.
3. Embeddings — generate once; re-embed only changed content.
4. Database — smallest line item at this scale; scale-to-zero when idle.

## Phased plan: search + monetization

- **Phase 1 (now):** static site, no live search. Keep search logic factorable
  into a standalone module (future Lambda) and populate embeddings/HNSW during
  ingestion.
- **Phase 2:** turn on live search (semantic RAG **and** structured/entity
  queries) behind **API Gateway with usage plans** — free quota + throttling.
  Metering here is a **cost-protection** measure first (caps per-caller LLM/DB
  spend), monetization second.
- **Phase 3:** monetize — metered API tier for professionals (charge above a
  volume threshold; API Gateway usage plans enforce the free tier/quota, a
  payment integration like Stripe or AWS Marketplace metering handles billing);
  feature/subscription tier for genealogists; free tier for amateurs.
