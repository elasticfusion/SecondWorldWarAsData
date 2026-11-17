# Universal Fetch-Provenance (design — for confirmation before coding)

**Principle (owner):** *every* external fetch is a traceable side-channel event — "I queried
source X at time T with params P and got result R (status S, outcome O)." If an API misbehaves
you can walk backwards from the stored record. A positive **textual** find is additionally
enqueued for **human review**, and **on affirm** flows through the SAME fetch-provenance path into
ingestion/extraction like an uploaded book. One rule, no exceptions.

## What already exists (reuse, do not reinvent)

- `src/utils/http_pool.get_session()` — the pooled `requests.Session` that nominatim, wayback, and
  most enrichment fetches already funnel through. **This is the single choke point** the primitive
  wraps.
- `src/utils/url_registry` — DynamoDB `UrlRecord` (status/relevant/http_status/first_wayback_ts) +
  per-domain politeness gate. URL-scoped state machine. The fetch-provenance record **references**
  a URL registry entry; it does not replace it.
- `src/ingestion/source_metadata.SourceMetadata` — the "recorded original document" model; one per
  ingested source. An **affirmed textual find becomes a SourceMetadata** (the book-ingestion door).
- `src/enrichment/source_retrieval.retrieve_source` — already downloads internet content to a
  **quarantine for human review** (SSRF-guarded). This IS the review queue for Task #4; today it
  dead-ends — the missing piece is the quarantine→affirm→ingestion promotion bridge.

## The fetch-provenance record (the primitive's output)

One record per external fetch. Fail-safe: recording never blocks or fails the fetch.

| field | type | notes |
|---|---|---|
| `fetch_event_id` | ULID | stable id for this fetch event |
| `provider` | str | logical source: `"nominatim"`, `"wayback"`, `"noaa"`, `"award:<power>"`, … |
| `endpoint` | str | the URL/endpoint actually called |
| `request_params` | dict | query params / body summary (secrets redacted) |
| `http_method` | str | GET/POST |
| `queried_at` | str | UTC ISO-8601 |
| `http_status` | int\|null | terminal HTTP code (null on transport error) |
| `outcome` | enum | `hit` \| `miss` \| `error` \| `blocked` (SSRF/politeness) |
| `result_summary` | str\|null | compact description of R (e.g. `"lat=50.1,lon=6.4 conf=0.9"`, `"snapshot 1996…"`) — NOT the full payload |
| `latency_ms` | int\|null | |
| `error` | str\|null | exception/class on `error` |
| `url_key` | str\|null | sha256 link to the `url_registry` entry when URL-based |
| `kind` | enum | `structured` (geocode/weather — provenance only) \| `textual` (wayback/award/bio — review-eligible) |
| `review_status` | enum\|null | textual only: `pending` \| `affirmed` \| `rejected` (null for structured) |

### Storage
- **AWS:** same DynamoDB cache table as `url_registry`, key `fetchevent#{fetch_event_id}`
  (reuses the existing `_table()` accessor + TTL conventions). No new infra.
- **Local/test:** JSONL at `output/_fetch_events/<provider>.jsonl` (append-only; mirrors the
  local-first pattern used elsewhere). `_table()` returning None already signals local mode.
- **NOT a schema-governed entity** (no entity_registry/ULID-PK record, no `_schema_version`): it is
  operational provenance/telemetry, not a corpus entity. Avoids versioning/fingerprint churn.

## The primitive (choke point)

`src/utils/fetch_provenance.py`:

```python
def recorded_fetch(provider, endpoint, *, kind, method="GET", params=None,
                   session=None, **kwargs) -> requests.Response | None:
    """Wrap http_pool.get_session().request through one choke point, recording a
    fetch-provenance event (queried_at, status, outcome, latency, result summary).
    Fail-safe: a recording error is swallowed; the response is always returned."""
```

Plus a lower-level `record_fetch_event(**fields)` for callers that already hold a response
(wayback's courtesy wrapper) so we don't double-fetch.

## Migrations (reference implementations)

- **Task #3 — structured (nominatim):** route its `session.get` through `recorded_fetch(kind="structured")`.
  Provenance only. Regression test: a geocode emits a traceable record with provider/endpoint/
  params/queried_at/status/outcome — and a bad/empty result is still traceable.
- **Task #4 — textual (wayback positive hit):** route through `recorded_fetch(kind="textual")`;
  on a positive snapshot, enqueue via `retrieve_source` quarantine with `review_status="pending"`.
  **On affirm** (reviewer action) → promote: quarantined content → `SourceMetadata` → Phase-1/2
  extraction (entities with provenance/dedup/merge) — the SAME path as an uploaded book. Build the
  promotion bridge (the current dead-end).

## Open questions for confirmation
1. Storage: reuse the `dev-wwii-api-cache` DynamoDB table (key `fetchevent#…`) + local JSONL — OK?
2. `result_summary` as a compact string (not the full payload) — OK? (full payload already lives in
   the existing api response cache / quarantine; duplicating it here would bloat the telemetry.)
3. Affirm action surface: extend the existing dedup/review UI, or a minimal CLI first? (I propose a
   CLI/affirm-function first; wire a UI later.)
