# Postgres + pgvector Schema Design

Target datastore for the website and RAG layer. Grounded in the actual
`output/` structure (event-centric, ID-linked, ~40k entity records). Aurora
Serverless v2, PostgreSQL, pgvector with HNSW, min-capacity 0 ACU (scale to
zero) — per the cost/architecture decisions.

## The model in one sentence

Every extracted fact links, via `EventID`/`Sub_eventID` and per-mention IDs,
to a central **events → sub-events** spine sourced from
`output/content/<Book>/chapter*-event.json`; the sub-event narrative text is the
RAG corpus, and the typed entities (people, places, casualties, …) are the
structured/faceted-search corpus.

## Layers

1. **Sources** — the bibliographic backbone (`output/bibliography/`), the
   provenance every fact can cite.
2. **Events / Sub-events** — the hub (`output/content/`). Sub-events carry the
   narrative (`Sub-event_summary`, `Sub-event_fulltext`) and endnote refs.
3. **Entities** — people, people_groups, places, dates, casualties, equipment,
   logistics, weather, images, maps. Each has a stable `<Entity>ID`.
4. **Mentions** — the junction: an entity is *mentioned in* a sub-event, with a
   `MentionID`. This is the many-to-many fabric already present as
   `event_mentions[]` in each entity file.
5. **OOB** — the Order-of-Battle structured dataset (`output/oob/*`): a parallel
   authoritative roster (command staff, campaigns, attachments, higher units,
   organic units, statistics), crosswalked to `people`.
6. **Search** — `content_chunks` (text + `vector` embedding) for semantic RAG,
   derived from sub-event narratives; structured columns for faceted queries.

## Core tables (DDL sketch)

```sql
CREATE EXTENSION IF NOT EXISTS vector;

-- 1. Sources (bibliography) — citation backbone
CREATE TABLE sources (
    bibliography_id   TEXT PRIMARY KEY,          -- ULID from BibliographyID
    title             TEXT,
    author            TEXT[],                    -- citation.author[]
    document_type     TEXT,                      -- Manuscript, KTB, AAR, ...
    archive_reference TEXT,                      -- NARA RG etc.
    archive_address   TEXT,
    license           TEXT,
    copyright_status  TEXT,
    raw               JSONB                      -- full original record
);

-- 2. Events + sub-events (the hub) — from output/content
CREATE TABLE events (
    event_id    TEXT PRIMARY KEY,                -- ULID from EventID
    event_name  TEXT,
    book        TEXT,
    chapter     TEXT,
    series      TEXT,
    author      TEXT
);

CREATE TABLE sub_events (
    sub_event_id  TEXT PRIMARY KEY,              -- ULID from Sub-eventID
    event_id      TEXT REFERENCES events(event_id),
    summary       TEXT,                          -- Sub-event_summary
    fulltext      TEXT,                          -- flattened Sub-event_fulltext
    endnote_refs  INT[],
    footnote_refs INT[]
);

-- 3. Entities (one table per type; people shown, others analogous)
CREATE TABLE people (
    person_id     TEXT PRIMARY KEY,              -- PersonID
    name          TEXT NOT NULL,
    rank          TEXT,
    nationality   TEXT,
    raw           JSONB
);
-- places carry geocoding + provenance produced this session:
CREATE TABLE places (
    place_id        TEXT PRIMARY KEY,
    place_name      TEXT,
    latitude        DOUBLE PRECISION,
    longitude       DOUBLE PRECISION,
    country         TEXT,
    geocode_source  TEXT,                        -- osm | grok | grok_hill | situational
    geo_confidence  REAL,
    geo_review      TEXT,                         -- flag for approximate/suspect
    geom            GEOGRAPHY(POINT, 4326),       -- for map/proximity queries (PostGIS)
    raw             JSONB
);
-- people_groups, dates, casualties, equipment, logistics, weather, images,
-- maps: same shape — typed columns for the fields queried + JSONB raw.

-- 4. Mentions — the entity <-> sub-event junction (from event_mentions[])
CREATE TABLE mentions (
    mention_id    TEXT PRIMARY KEY,              -- MentionID
    sub_event_id  TEXT REFERENCES sub_events(sub_event_id),
    entity_type   TEXT NOT NULL,                 -- 'person','place',...
    entity_id     TEXT NOT NULL,                 -- FK resolved per type
    source_id     TEXT REFERENCES sources(bibliography_id),
    original_text TEXT,
    verbatim_ref  TEXT
);
CREATE INDEX ON mentions (entity_type, entity_id);
CREATE INDEX ON mentions (sub_event_id);

-- 5. OOB roster (one table per section; command_staff shown)
CREATE TABLE oob_command_staff (
    id             BIGSERIAL PRIMARY KEY,
    division       TEXT,
    division_source TEXT,                        -- title|inferred_*|unknown
    position       TEXT,
    effective_date TEXT,
    rank           TEXT,
    name           TEXT,
    person_id      TEXT REFERENCES people(person_id),  -- from crosswalk
    needs_review   BOOLEAN,
    source_file    TEXT
);
-- oob_campaigns, oob_attachments, oob_higher_units, oob_organic_units,
-- oob_statistics, oob_command_posts: analogous.

-- 6. Semantic search
CREATE TABLE content_chunks (
    chunk_id      BIGSERIAL PRIMARY KEY,
    sub_event_id  TEXT REFERENCES sub_events(sub_event_id),
    text          TEXT NOT NULL,                 -- chunked narrative
    embedding     VECTOR(1024),                  -- dim matches chosen model
    token_count   INT
);
CREATE INDEX ON content_chunks USING hnsw (embedding vector_cosine_ops);
```

## Query paths the site/RAG needs

- **Semantic ("what was trench warfare like?")** — embed query → HNSW search on
  `content_chunks` → join `sub_events` → cite via `mentions`→`sources`.
- **Structured / faceted ("casualties in Lorraine, Nov 1944")** — filter typed
  columns (`places`, `dates`, `casualties`) joined through `mentions`.
- **Genealogy ("surname X near place Y")** — `people`/`oob_command_staff`
  filtered by name/place/date; crosswalk unifies narrative + roster identity.
- **Map ("show engagements near Aachen")** — PostGIS `geom` proximity on
  `places` (uses the geocoding produced this session; approximate ones carry
  `geo_review`).

## Notes / decisions to confirm

- **Embedding model + dimension** — `VECTOR(1024)` is a placeholder; set to the
  chosen model (e.g. a 1024-dim or 1536-dim embedder). Grok has no embeddings
  endpoint, so this needs a separate embedder (Bedrock Titan/Cohere, or OSS).
- **PostGIS** — needed for map proximity; enable alongside pgvector.
- **JSONB `raw`** keeps the full original record so the DB never loses fidelity
  the typed columns don't capture.
- **Provenance columns** (`geocode_source`, `division_source`, `needs_review`,
  `geo_review`) surface the confidence/verification work from ingestion so the
  site can flag approximate data — essential for a citable reference.
- **Loader** — a phase that reads `output/` and populates these tables; the
  event/sub-event pass first (hub), then entities, then mentions, then chunks +
  embeddings.
```
