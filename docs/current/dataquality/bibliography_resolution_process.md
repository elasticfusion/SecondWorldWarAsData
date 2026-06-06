# Bibliography Resolution: End-to-End Process

**Date:** 2026-06-05

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

9. ECS task starts, runs 6 steps sequentially:
   - Step 1: Enrich people (Grokipedia/Wikipedia)
   - Step 2: Enrich people groups
   - Step 3: Enrich places
   - **Step 4: Enrich bibliography** ← resolution happens here (detail below)
   - Step 5: OpenSERP enrichment (if enabled)
   - Step 6: NOAA weather

---

## Phase 3 Step 4: Bibliography Enrichment (Detailed)

Step 4 runs two sub-processes sequentially:

### Sub-process A: `enrich_bibliography()` — Metadata Enrichment

Called via `src/extraction/supplemental_advanced.py`. Iterates every file in `output/bibliography/` and applies three enrichments:

1. **ISBN Extraction** (`_enrich_isbn`)
   - Only runs if `citation.document_type == "book"` AND no ISBN already present
   - Asks Grok to identify the ISBN from the citation metadata (title, author, publisher, year)
   - Writes `citation.isbn` if found

2. **Copyright Determination** (`_enrich_copyright`)
   - Looks up author death date via Grok
   - Applies jurisdiction-based copyright rules (typically USA — life + 70 years, or government work = public domain)
   - Writes `copyright_status` field

3. **Archive URL Verification** (`_enrich_archive_url`)
   - Only runs if `config.verify_archive_urls == True`
   - Verifies any existing `archive_url` is still accessible
   - Marks as `verified: true/false`

### Sub-process B: `resolve_bibliography_dir()` — Source URL Resolution

Called via `src/enrichment/bibliography_resolver.py`. This is where external searches happen.

**Entry point logic:**
```
For each *.json in output/bibliography/:
    Skip if search_status == "resolved" or resource_urls is non-empty
    Route to resolver based on document_type
    Apply result (URL + status) or mark not_found
    Write file back
```

**Router (`_pick_resolver`):**

| `citation.document_type` | Resolver | Search Order |
|---|---|---|
| primary source, after action report, field order, military message, letter, memo, cable, report, journal, war diary | `_resolve_archive` | Grok RG identification → NARA API → OpenSERP → Archive.org |
| book, monograph, unit history | `_resolve_book` | Archive.org → Gutenberg → OpenSERP |
| journal article, periodical, newspaper article | `_resolve_article` | Archive.org |
| anything else | `_resolve_generic` | Archive.org |

---

#### Archive Resolution Path (military records) — `_resolve_archive`

This is the most complex path. It handles primary source documents (AARs, field orders, journals, etc.):

**Step 1: Extract search text**
- Uses `verbatim_reference` from the entry's `mentions[0]` (the original endnote text)
- Falls back to `citation.title` if no verbatim

**Step 2: Identify NARA Record Group** (`_identify_record_group`)
- If entry doesn't already have a Record Group (RG) reference:
  - Sends verbatim text to Grok with a prompt listing common WWII Record Groups (RG 331 SHAEF, RG 407 unit records, RG 338 commands, etc.)
  - Grok returns something like `"RG 407, Entry 427"` or `"UNKNOWN"`
  - If valid, writes to `archive_reference_number` and sets `archive_physical_address` to NARA College Park

**Step 3: Search for online copy** (`_search_online_sources`)

Searches in order, stops at first verified hit:

1. **NARA Catalog API** (if API key available AND entry has NARA indicators):
   - Checks `_is_nara_searchable()` — scans verbatim/title for keywords like "RG", "after action", "SHAEF", "journal", unit abbreviations, etc.
   - Searches `GET /api/v2/records/search?q={query}&limit=5`
   - For each result: **verifies via `_verify_nara_match()`** — asks Grok if unit AND document type match
   - Accepts first verified hit, caches URL
   - If all 5 rejected → caches NOT_FOUND for this query

2. **OpenSERP** (if enabled):
   - Searches Google/Bing/DuckDuckGo for `"{archive_reference} digitized document"`
   - **Verifies via `_verify_openserp_match()`** — asks Grok if result is relevant to specific event/unit
   - Equipment exception: photographs allowed but must be accurate model/variant

3. **Archive.org**:
   - Searches Archive.org metadata API by title
   - Verifies via `_verify_match()` — asks Grok if title matches

**Step 4: Apply result**
- If URL found: `resource_urls.append(url)`, `search_status = "resolved"`, `search_source = "{source}"`
- If only RG identified (no online copy): `search_status = "resolved"` (physical location known)
- If nothing: `search_status = "not_found"`

---

#### Book Resolution Path — `_resolve_book`

Simpler flow:
1. Search Archive.org by title + author → verify with Grok
2. Search Gutenberg via OpenSERP (if enabled) → verify with Grok
3. Search OpenSERP general (if enabled) → verify with Grok
4. Return first verified hit or None

---

#### Article Resolution Path — `_resolve_article`

1. Search Archive.org by title + author → verify with Grok
2. Return first verified hit or None

---

#### Generic Resolution Path — `_resolve_generic`

1. Search Archive.org by title + author
2. Return first hit or None

---

### After Both Sub-processes Complete

12. All modified bibliography files are written to disk (ECS local filesystem)
13. ECS task syncs results back to S3 at task completion

### After Phase 3 Completes

13. ECS task syncs enriched files back to S3
14. Task exits, lock released in DynamoDB
15. NAT gateway teardown scheduled (delayed to save costs)

---

## Verification Logic (2026-06-05 Update)

Previously, NARA and OpenSERP results were accepted without verification. Now:

| Source | Verification | Fail Behavior |
|---|---|---|
| NARA | `_verify_nara_match` — checks unit + document type match | Rejects; tries next result |
| OpenSERP | `_verify_openserp_match` — checks event relevance; equipment must be accurate photo | Rejects; tries next result |
| Archive.org | `_verify_match` — title + content check via Grok | Rejects |

All verifications fail closed (reject on error rather than accept).

---

## Caching

- **DynamoDB table:** `dev-wwii-api-cache`
- **Key format:** `search#{source}#{hash}` (e.g., `search#nara#87bb5aece6dd63f0`)
- **Positive cache:** URL stored, returned on subsequent calls without re-searching
- **Negative cache:** `NOT_FOUND` stored, prevents re-searching the same query
- **Cache must be flushed** when verification logic changes, otherwise old unverified results persist

---

## Rate Limits

| Resource | Limit | Notes |
|---|---|---|
| NARA API (search + record detail) | 10,000/month per key | Email `Catalog_API@nara.gov` for higher limit |
| NARA file downloads (TIF/JPG) | No documented limit | Plain HTTPS, no API key needed |
| Grok API | 30 calls/minute | Used for verification prompts |
| Archive.org | No documented limit | Be courteous |

---

## Triggering Re-Resolution

To re-run bibliography resolution (e.g., after resetting entries or changing verification logic):

```bash
# 1. Sync reset bibliography files to S3
aws s3 sync output/bibliography/ s3://dev-wwii-data-pipeline/output/bibliography/ --region us-east-1

# 2. Flush DynamoDB search cache
python3 /tmp/flush_search_cache.py

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
