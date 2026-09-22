-- Schema for the WWII-as-data warehouse (see docs/SCHEMA_DESIGN.md).
-- Written to be portable across SQLite (tests) and PostgreSQL (production);
-- Postgres-only features (pgvector, PostGIS, VECTOR/GEOGRAPHY columns, HNSW)
-- live in schema_pg_extras.sql so the core loads/tests run without them.

CREATE TABLE IF NOT EXISTS sources (
    bibliography_id   TEXT PRIMARY KEY,
    title             TEXT,
    author            TEXT,
    document_type     TEXT,
    availability      TEXT,
    archive_reference TEXT,
    license           TEXT,
    raw               TEXT
);

CREATE TABLE IF NOT EXISTS events (
    event_id    TEXT PRIMARY KEY,
    event_name  TEXT,
    book        TEXT,
    chapter     TEXT
);

CREATE TABLE IF NOT EXISTS sub_events (
    sub_event_id  TEXT PRIMARY KEY,
    event_id      TEXT,
    summary       TEXT,
    fulltext      TEXT,
    endnote_refs  TEXT,
    footnote_refs TEXT
);

CREATE TABLE IF NOT EXISTS people (
    person_id   TEXT PRIMARY KEY,
    name        TEXT,
    rank        TEXT,
    nationality TEXT,
    raw         TEXT
);

CREATE TABLE IF NOT EXISTS people_groups (
    group_id    TEXT PRIMARY KEY,
    group_name  TEXT,
    group_type  TEXT,
    nationality TEXT,
    raw         TEXT
);

CREATE TABLE IF NOT EXISTS places (
    place_id          TEXT PRIMARY KEY,
    place_name        TEXT,
    latitude          REAL,
    longitude         REAL,
    country           TEXT,
    geocode_source    TEXT,
    enrichment_status TEXT,
    geo_confidence    REAL,
    geo_review        TEXT,
    raw               TEXT
);

CREATE TABLE IF NOT EXISTS equipment (
    equipment_id     TEXT PRIMARY KEY,
    common_name      TEXT,
    category         TEXT,
    country_of_origin TEXT,
    raw              TEXT
);

CREATE TABLE IF NOT EXISTS dates (
    date_id             TEXT PRIMARY KEY,
    date_start          TEXT,
    date_end            TEXT,
    date_precision      TEXT,
    normalized_datetime TEXT,
    original_text       TEXT,
    raw                 TEXT
);

CREATE TABLE IF NOT EXISTS casualties (
    casualty_id  TEXT PRIMARY KEY,
    type         TEXT,
    description  TEXT,
    count        TEXT,
    date_string  TEXT,
    iso_date     TEXT,
    raw          TEXT
);

CREATE TABLE IF NOT EXISTS logistics (
    logistics_id   TEXT PRIMARY KEY,
    logistics_type TEXT,
    category       TEXT,
    description    TEXT,
    severity       TEXT,
    status         TEXT,
    raw            TEXT
);

CREATE TABLE IF NOT EXISTS weather (
    weather_id  TEXT PRIMARY KEY,
    date_id     TEXT,
    place_name  TEXT,
    place_id    TEXT,
    source_type TEXT,
    raw         TEXT
);

CREATE TABLE IF NOT EXISTS mentions (
    mention_id    TEXT,
    sub_event_id  TEXT,
    entity_type   TEXT,
    entity_id     TEXT,
    original_text TEXT,
    verbatim_ref  TEXT,
    -- Composite key: mention_id alone is not unique (it repeats across
    -- entity/sub-event pairings) and is sometimes null, so idempotent
    -- INSERT OR REPLACE / ON CONFLICT keys on the full identifying tuple.
    PRIMARY KEY (mention_id, entity_type, entity_id, sub_event_id)
);

CREATE INDEX IF NOT EXISTS idx_mentions_entity ON mentions (entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_mentions_sub_event ON mentions (sub_event_id);
CREATE INDEX IF NOT EXISTS idx_sub_events_event ON sub_events (event_id);
CREATE INDEX IF NOT EXISTS idx_places_country ON places (country);
