# Bibliography Resolution: End-to-End Process

**Date:** 2026-06-05 · **Updated:** 2026-11-17 (re-checked against `src/enrichment/bibliography_resolver.py`, `phase3_enrich_data.py`)

---

## Prerequisite gap — CLOSED (checked 2026-11-16)

On 2026-10-13 a clean RoerDams (green-book) run produced an EMPTY `output/bibliography/` because the
parser dropped the citation text of every note. This is fixed upstream of this resolver:

- `src/parser.py::extract_footnotes` now pairs the in-text marker `<sup>[N](...#fnN)</sup>` with the
  end-of-chapter definition `[N](...#cnN). <citation text>` and returns `(number, url, text)`.
- `src/notes.py::separate_notes` (Phase 1) separates each chapter's footnotes/endnotes from the
  narrative with provenance (note number, kind, source URL, citing paragraph) and rewrites markers to
  the canonical form; for OCR'd books `src/ingestion/chandra_markdown` gathers the notes into a
  `### Footnotes` section first.
- Phase 2's supplemental pass reads notes in windows within each sub-event and assigns
  `reference_number` / `verbatim_reference` deterministically (`src/extraction/supplemental.py`).

## Planned extension (2026-10-13) — resolve-then-DOWNLOAD (TODO HIGH #3)

Status (2026-11-17): partly done. Item 1 is done (`_apply_local_holding` runs first and an entry with
`resource_urls` is never re-searched). Item 2 is partly done (an archival entry with an
`archive_reference_number` but no URL gets `search_status = "reference_identified"`, `_apply_result`).
Item 5's distinct status exists only as `reference_identified` and `held_unprocessed`; there is no
`available_on_visit` / `queued_for_future_inclusion`. Item 7: the NARA key is wired
(`ecs_entrypoint.py` loads `<env>-wwii-pipeline/nara-api-key` into `NARA_API_KEY`, mapped to
`api.nara_api_key` in `src/utils/config.py`); there is still no NOAA mapping there (`config.yaml`
`api.noaa_api_token` is empty). Items 3, 4 and 6 (download of EPUB/PDF, copyright gating) are NOT built:
the resolver still STOPS at recording a source URL (`resource_urls`). Original intent:

1. **Check existing holdings FIRST** (strengthen the current skip-if-resolved):
   if `archive_reference_number` / a NARA record / local holdings already present →
   do NOT search again.
2. **NARA** = primary/military records. A NARA reference is NORMALLY physical-only;
   record the Record Group + College Park location even when no digital copy exists
   (that is a valid "resolved" — reference available on a visit).
3. **Archive.org** = mostly NON-NARA material (books, articles). Books may be public
   domain or not.
4. **DOWNLOAD** any available copy for further processing, with format preference
   **EPUB > PDF > text**. Gate downloads on `copyright_status`: public-domain books
   download; non-PD → note-only (do not download).
5. If NO download is available, record a DISTINCT status
   (e.g. `available_on_visit` / `queued_for_future_inclusion`) — not plain
   `not_found` — so it can be revisited.
6. Open design points: download destination (`sources/` vs `contentrepository/`),
   and whether a downloaded PD book feeds back into Phase 0/1 as new source material.
7. Wire the NARA + NOAA API keys into runtime config (secrets exist:
   `dev-wwii-pipeline/{nara,noaa}-api-key`; `_load_secrets` currently loads only
   Grok; `config.py` maps `NARA_API_KEY`→`api.nara_api_key` but has NO NOAA mapping).

---

## Pipeline Flow

### Phase 2 (Extract) — Creates Bibliography Entry

1. Text extracted from chapter endnotes/footnotes
2. Grok classifies each reference and outputs a bibliography JSON file
3. File written to `s3://dev-wwii-data-pipeline/output/bibliography/{filename}.json`
4. S3 notification → SNS (`dev-wwii-entity-created`) → trigger Lambda

### Trigger Lambda — Decides What to Do Next

5. Trigger Lambda sees SNS topic is `entity-created`
6. Checks if dedup review is complete
7. If yes → launches Phase 3 ECS task (Fargate Spot)
8. If no → waits (hourly reconciliation will catch it later)

### Phase 3 ECS Task (`phase3_enrich_data.py`) — Resolves Sources

9. ECS task starts and runs these steps sequentially (each wrapped by `_run_step`, which records enriched/status/error and never aborts Phase 3):
   - People (Grokipedia/Wikipedia) enrichment no longer runs here; it moved to Phase 2
   - Enrich people groups (structured facts)
   - Enrich places (and geocode)
   - **Enrich bibliography** (`enrich_bibliography`, then `resolve_bibliography_dir`; detail below)
   - Document media steps, then OpenSERP enrichment (if `supplemental_material.use_openserp`)
   - Equipment images
   - NOAA weather (only if `api.noaa_api_token` is set)

   The `[phase3 step N/6]` log labels in `phase3_enrich_data.py` are stale; the step names in
   `OBSERV phase3.step name=...` are authoritative.

---

## Phase 3 Step 4: Bibliography Enrichment (Detailed)

Step 4 runs two sub-processes sequentially:

### Sub-process A: `enrich_bibliography()` — Metadata Enrichment

Called via `src/extraction/supplemental_advanced.py`. Iterates every file in `output/bibliography/` and applies three enrichments:

1. **ISBN Extraction** (`_enrich_isbn`)
   - Only runs if `citation.type == "book"`, `supplemental_material.extract_isbn` is true (default false in `config.yaml`) AND no ISBN already present
   - Asks Grok (cache_type `supplemental_advanced`) to identify the ISBN from the citation metadata (title, author, publisher, year)
   - Writes `citation.isbn` if found

2. **Copyright Determination** (`_enrich_copyright`)
   - Only runs if `supplemental_material.determine_copyright` is true (default false in `config.yaml`)
   - Looks up author death date via Grok (`author_death_date` prompt)
   - Applies jurisdiction-based copyright rules (typically USA — life + 70 years, or government work = public domain)
   - Writes `copyright_status` field

3. **Archive URL Verification** (`_enrich_archive_url`)
   - Only runs if `supplemental_material.verify_archive_urls` is true (default false in `config.yaml`)
   - Verifies any existing `archive_url` is still accessible
   - Marks as `verified: true/false`

### Sub-process B: `resolve_bibliography_dir()` — Source URL Resolution

Called via `src/enrichment/bibliography_resolver.py`. This is where external searches happen.

**Entry point logic:**
```
For each *.json in output/bibliography/:
    Skip if search_status == "resolved" AND resource_urls is non-empty
        (resolved by record-group identification alone is retried)
    Reuse the resolution of an earlier identical citation (_citation_key), else:
        locally-held source -> resolved / held_unprocessed
        not a citation (narrative prose) -> not_citation
        otherwise route to a resolver (below)
    Apply result: resolved | reference_identified | not_found
    not_citation records are moved to output/narrative_notes/ (verified before delete)
    Write file back (schema-guarded; a blocked write raises and counts as an error)
```

**Router (`_pick_resolver`):**

| `citation.document_type` | Resolver | Search Order |
|---|---|---|
| primary source (also "primary source document"), after action report, field order, military message, letter, memo, cable, report, journal, war diary; or `availability == "archive"` | `_resolve_archive` | deterministic RG lookup → NARA API → OpenSERP → Archive.org → Gutenberg → Grok LLM search (last, only if nothing else hit) |
| book, monograph, unit history; or `availability == "offline"` | `_resolve_book` | Archive.org / Gutenberg / OpenSERP (title, then `alt_title`), then the Grok LLM search once |
| journal article, periodical, newspaper article | `_resolve_article` | Archive.org (title, then `alt_title`) |
| no `document_type` but NARA-searchable verbatim | `_resolve_archive` | as above |
| anything else (untyped, the common green-book case) | `_resolve_generic_entry` | the same verified multi-source search as the archive path (`_search_online_sources`) |

---

#### Archive Resolution Path (military records) — `_resolve_archive`

This is the most complex path. It handles primary source documents (AARs, field orders, journals, etc.):

**Step 1: Extract search text**
- Uses the entry's `verbatim_reference`, else `mentions[0].verbatim_reference` (the original endnote text)
- Falls back to `citation.title` if no verbatim

**Step 2: Identify NARA Record Group** (`_identify_record_group` -> `_identify_nara_record` -> `_lookup_record_group`)
- If the entry has no Record Group (RG) in `archive_reference_number`: a deterministic alias lookup (`_RG_LOOKUP`, from NARA's Record Group Explorer) returns a bare label such as `RG 407`. No Grok call is made (the `grok_client` argument is unused).
- On a hit it writes `archive_reference_number` and sets `archive_physical_address` to NARA College Park; a miss returns None.

**Step 3: Search for online copy** (`_search_online_sources`)

Searches in order and AGGREGATES every verified hit into `resource_urls` and `source_metadata.findings` (the first is the primary `search_source`). The Grok LLM search runs only when no other source found a copy:

1. **NARA Catalog API** (if API key available AND entry has NARA indicators):
   - Checks `_is_nara_searchable()` — scans verbatim/title for keywords like "RG", "after action", "SHAEF", "journal", unit abbreviations, etc.
   - Requires `api.nara_api_key`; scoped by the deterministic record group (`_lookup_record_group`); retries with the Grok-expanded `alt_title`
   - Searches the NARA catalog API (`_search_nara`, `_execute_nara_query`; paced to ~10/min)
   - Candidates are verified (`_verify_nara_hits` / `_verify_nara_match`) — asks Grok if unit AND document type match
   - Accepts the first verified hit and caches the URL; no verified hit → caches NOT_FOUND for this query

2. **OpenSERP** (if enabled):
   - Only when `use_openserp` is set in the resolve config; uses repository-targeted query templates (`_search_openserp_archive`), with the archive reference number or the search title
   - **Verifies via `_verify_openserp_match()`** — asks Grok if result is relevant to specific event/unit
   - An unreachable OpenSERP marks the entry `_backend_down`; a resulting `not_found` is counted as `backend_down` in the run stats and is not queued for human review
   - Equipment exception: photographs allowed but must be accurate model/variant

3. **Archive.org**:
   - Gated on `search_archive_org` (default true); `search_archive_org_candidates` in `src/extraction/supplemental_search.py`
   - Verifies each candidate via `_collect_verified_candidates` — asks Grok if it matches

4. **Gutenberg** (gated on `search_gutenberg`, default true; Gutendex API) and, last, the **Grok LLM search** (`llm_search`, default true; the URL is fetched and content-verified by `_verify_url_content` before it is stored; skipped in batch-collect mode)

**Step 4: Apply result**
- If URL found: `resource_urls.append(url)`, `search_status = "resolved"`, `search_source = "{source}"`, `availability = "online"`, license re-derived
- If only RG identified (no online copy): `search_status = "reference_identified"` (physical location known; a later run retries the online sources)
- If nothing: `search_status = "not_found"`

---

#### Book Resolution Path — `_resolve_book`

Simpler flow (`_resolve_book_search`, title then `alt_title`):
1. Search Archive.org by title + author → verify with Grok
2. Search Gutenberg via OpenSERP (if enabled) → verify with Grok
3. Search OpenSERP general (if enabled) → verify with Grok
4. If nothing: the content-verified Grok LLM search, once
5. Return first verified hit or None

---

#### Article Resolution Path — `_resolve_article`

1. Search Archive.org by title + author (then `alt_title`) → verify with Grok
2. Return first verified hit or None

---

#### Generic Resolution Path — `_resolve_generic_entry`

Takes the same verified multi-source search as the archive path (`_search_online_sources`: NARA, OpenSERP, Archive.org, Gutenberg, then the Grok LLM search). It no longer takes Archive.org's first hit unverified.

---

### After Both Sub-processes Complete

10. All modified bibliography files are written to disk (ECS local filesystem) via `write_json_with_lock`
11. Unresolved real citations are queued for human disposition (`_queue_entry`); the run emits `OBSERV bib.resolve in=... resolved=... ref_identified=... not_found=... not_citation=... deduped=... queued=...`

### After Phase 3 Completes

12. ECS task syncs enriched files back to S3
13. Task exits, lock released in DynamoDB
14. NAT gateway teardown scheduled (delayed to save costs; not re-verified)

---

## Verification Logic (2026-06-05 Update)

Previously, NARA and OpenSERP results were accepted without verification. Now:

| Source | Verification | Fail Behavior |
|---|---|---|
| NARA | `_verify_nara_match` — checks unit + document type match | Rejects; tries next result |
| OpenSERP | `_verify_openserp_match` — checks event relevance; equipment must be accurate photo | Rejects; tries next result |
| Archive.org | `_verify_match` — title + content check via Grok | Rejects |

The Grok LLM search is verified the same way (`_verify_url_content` fetches the page and asks Grok). All verifications fail closed (reject on error rather than accept).

---

## Caching

- **DynamoDB table:** `dev-wwii-api-cache`
- **Key format:** `search#{source}#{hash}` (e.g., `search#nara#87bb5aece6dd63f0`)
- Implemented in `src/utils/search_cache.py` (local file backend when AWS is off)
- **Positive cache:** URL stored (TTL 30 days, `POSITIVE_TTL_DAYS`), returned on subsequent calls without re-searching
- **Negative cache:** `NOT_FOUND` stored (TTL 7 days, `NEGATIVE_TTL_DAYS`), prevents re-searching the same query
- **Cache must be flushed** when verification logic changes, otherwise old unverified results persist

---

## Rate Limits

| Resource | Limit | Notes |
|---|---|---|
| NARA API (search + record detail) | 10,000/month per key | Email `Catalog_API@nara.gov` for higher limit. Code throttles to one search per 6 s (`_nara_rate_limit`) |
| NARA file downloads (TIF/JPG) | No documented limit | Plain HTTPS, no API key needed |
| Grok API | 30 calls/minute (not re-verified) | Used for verification prompts |
| Archive.org | No documented limit | Be courteous |

---

## Triggering Re-Resolution

To re-run bibliography resolution (e.g., after resetting entries or changing verification logic):

```bash
# 1. Sync reset bibliography files to S3
aws s3 sync output/bibliography/ s3://dev-wwii-data-pipeline/output/bibliography/ --region us-east-1

# 2. Flush DynamoDB search cache
python3 scripts/flush_search_cache.py

# 3. Build and push updated Docker image
docker build -t dev-wwii-pipeline .
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <account-id>.dkr.ecr.us-east-1.amazonaws.com
docker tag dev-wwii-pipeline:latest <account-id>.dkr.ecr.us-east-1.amazonaws.com/dev-wwii-pipeline:latest
docker push <account-id>.dkr.ecr.us-east-1.amazonaws.com/dev-wwii-pipeline:latest

# 4. Trigger Phase 3
aws lambda invoke \
  --function-name dev-wwii-trigger \
  --payload '{"source": "manual", "phase": "3"}' \
  --region us-east-1 \
  /dev/stdout
```

---

## 2026-06-05 Reset Summary

| Source | Entries Reset | Reason |
|---|---|---|
| catalog.archives.gov (NARA) | 2,468 | Accepted without unit/document verification |
| archive.org | 1,391 | 48% hallucinated URLs (random hashes), rest unverified |
| history.army.mil | 59 | 404 errors |
| eisenhowerlibrary.gov | 41 | 404 errors |
| loc.gov | 35 | 404 or wrong content |
| media.defense.gov | 32 | 404 errors |
| gallica.bnf.fr | 16 | 404 errors |
| fdrlibrary.org | 9 | 404 errors |
| marshallfoundation.org | 4 | Redirects to search page |
| All remaining (ibiblio + misc) | 216 | Assumed bad |
| **Total** | **4,271** | |
