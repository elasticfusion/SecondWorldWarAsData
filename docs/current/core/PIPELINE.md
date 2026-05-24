# Pipeline Documentation

**Last Updated:** 2026-05-23

## Overview

The extraction pipeline consists of three main phases, with a dedup review gate between Phase 2 and Phase 3:

### Phase 1: Parsing
Converts markdown source files into structured JSON with absolute paragraph numbering. On AWS, Phase 1 also:
- Clears all DynamoDB pipeline locks
- Resets dedup review status (`complete: false`)
- Clears any stale DynamoDB manifest from previous runs

### Phase 2: Extraction
Extracts entities and events using Grok AI. Prompts are loaded from YAML templates in `prompts/` (overridable from S3). In AWS mode, Phase 2 uses **incremental processing** — only downloads and processes parsed files that don't have a corresponding event file in S3. When `batch.phase2: true`, the task auto-delegates to submit-only mode: submits the batch, enqueues the job, tears down infrastructure, and exits. The batch poller Lambda (`dev-wwii-batch-poller`) checks every 15 minutes and launches a retrieve task on completion.

### Dedup Review Gate
After Phase 2, duplicate detection runs automatically:
1. Military units in `output/places/` are auto-reclassified to `output/people_groups/`
2. Stale index entries are cleaned up
3. Exclusion pairs are migrated from local JSON to DynamoDB (one-time)
4. All four dedup scripts run, reading exclusions from DynamoDB (AWS) or local JSON (local mode)

In AWS mode, a web UI allows merging, skipping, and reclassifying entities before Phase 3 proceeds. UI actions (merge, reclassify, assign) append changed file keys to the DynamoDB manifest so Phase 3 downloads them. "Not Duplicates" decisions are stored in DynamoDB and persist across pipeline runs.

### Phase 3: Enrichment
Enriches people, groups, places, and bibliography with external data. In AWS mode, Phase 3 reads the DynamoDB manifest (`manifest#phase2`) to download only files changed by Phase 2 and dedup review, falling back to a full entity directory download if no manifest exists.

### Caching
- **Local mode:** diskcache (SQLite) in `cache/api/`
- **AWS mode:** DynamoDB table (`dev-wwii-api-cache`). Cached responses persist across ECS task runs, so re-processing only calls Grok for new/changed chapters.

## Phase 1: Parse

```bash
python3 phase1_parse.py
```

**Input:** `contentrepository/{Book}/chapter*/chapter*-content.md`  
**Output:** `output/content/{Book}/chapter*-parsed.json`

**Features:**
- Absolute paragraph numbering across entire book
- Inline entity extraction (images, maps, footnotes, page markers)
- Metadata from YAML files
- Preserves source structure
- **Source hash skip:** Computes content hash — unchanged files are not re-written or re-uploaded to S3
- **Lock scoping:** Only clears its own Phase 1 lock (not Phase 2/3 locks)
- **Full-sync scoped by `BOOK_NAME`** env var (AWS mode — avoids downloading all books)
- **Atomic file writes:** Uses temp file + `os.replace()` via `write_json_with_lock()` to prevent corruption
- **Auto-splitting:** Chapters >400K chars automatically split into ~50 paragraph chunks (e.g., `chapter20a`, `chapter20b`)

**Large Chapter Handling:**
When a chapter exceeds 400K characters (leaves 100K headroom for API responses):
- Splits into chunks of ~50 paragraphs each
- Names: `chapter20a-parsed.json`, `chapter20b-parsed.json`, etc.
- Each chunk includes full metadata and all images/maps
- Original oversized file is not created
- Prevents Phase 2 token limit issues

## Phase 2: Extract

```bash
python3 phase2_extract.py

# Or use Batch API for 50% cost reduction (async, may take hours)
python3 phase2_extract.py --batch
```

**AWS batch mode:** When `batch.phase2: true` in `config.yaml`, the ECS entrypoint auto-delegates to submit-only mode. The task submits the batch job, enqueues it in DynamoDB (`batch_job#{batch_id}`), tears down infrastructure, and exits. The batch poller Lambda handles retrieval. See [Batch Processing](../features/batch_processing/README.md).

**Pipeline Stages:**
1. **Metadata Completion** - Auto-fills missing chapter titles/numbers
2. **Parallel Core Extraction** - Processes all chapters concurrently (max 3):
   - Event extraction (if event file doesn't exist)
3. **Core Entity Extraction** - Per chapter (parallel):
   - Dates, Places, People Groups, People (batched API calls, parallel per chapter)
4. **Retry Missing Events** - Retries any chapters that failed event extraction (per-chapter cache clear)
5. **Optional Entity Extraction** - Sequential per event file (batched per chapter — 1 API call per extractor):
   - Weather (if enabled)
   - Equipment (if enabled)
   - Logistics (if enabled)
   - Casualties (if enabled) — personnel only (killed, wounded, missing, POW); no equipment losses
   - Supplemental material (if enabled) — fetches actual endnote text from ibiblio HTML pages, resolves cross-references ("cited in n. 5", "see n. 4"), passes real citation text to Grok
   - Images (if enabled, via `src/extraction/images.py`)
6. **Maps Extraction** - Source maps + external maps via OpenSERP (if enabled)
7. **Analysis** - Duplicate people report + related groups report

**Auto-split on truncation:** If a Grok API response is truncated (>100K chars), the chapter is automatically split at section boundaries and each half is extracted separately, then merged.

**Heartbeat monitor:** Both Phase 2 and Phase 3 log progress per chapter. A warning is logged if no progress is made for 5 minutes, showing the last item processed.

**Smart retry:** Retry logic skips footnotes-only sections that consistently fail extraction (no actionable content).

**JSON quality statistics:** Extraction quality metrics (valid JSON rate, schema compliance) are tracked per batch and stored in DynamoDB for monitoring.

**Output Files:**
- `chapter*-event.json` - Events and sub-events
- `chapter*-notes-event.json` - Factual content from endnotes/footnotes (supplemental split)
- `dates/{YYYYMM}_{ID}.json` - Central dates repository
- `places/{Name}_{ID}.json` - Central places repository
- `weather/{YYYYMM}_{ID}.json` - Central weather repository (optional)
- `equipment/{Name}_{ID}.json` - Military equipment (optional)
- `maps/{Name}_{ID}.json` - Maps from source material (optional)
- `external_maps/{Name}_{ID}.json` - Third-party maps (optional)
- `people/{Name}_{ID}.json` - Individual person files
- `people_groups/{Group}_{ID}.json` - Organization files
- `bibliography/{title_slug}_{ID}.json` - Deduplicated document references (supplemental split)
- `bibliography/review_queue.json` - Ambiguous endnotes for human review

## Caching

All Grok API responses are cached in `cache/api/`:
- `events/` - Event extractions
- `dates/` - Date extractions
- `places/` - Place extractions
- `people/` - People extractions
- `people_groups/` - Group extractions
- `weather/` - Weather extractions (optional)
- `equipment/` - Equipment extractions (optional)

**Cache benefits:**
- Avoids redundant API calls
- Faster re-runs
- Cost savings

**Cache stats:** At the end of Phase 2 and Phase 3, a summary is logged:
```
Cache stats: 45 hits, 23 misses, 66.2% hit rate
```

**Clear cache (specific entry):**
```bash
# Use the command from error log output — clears only the affected entry
python3 -c "from diskcache import Cache; c=Cache('cache/api/{type}'); c.pop('{key}', None)"
```

**Clear cache (specific chapter across all types):**
```bash
python3 -c "from diskcache import Cache; from pathlib import Path; \
[Cache(str(d)).pop(k, None) for d in Path('cache/api').iterdir() if d.is_dir() \
for k in list(Cache(str(d))) if 'chapter8c' in str(Cache(str(d)).get(k, ''))]"
```

**Clear cache (entire type — use sparingly):**
```bash
rm -rf cache/api/{type}/*
```

## Skip Logic

Phase 2 intelligently skips already-processed files:
- **Events/Dates/Places**: Skip if output file exists
- **People/Groups**: Skip if files newer than event file

**Force reprocessing:**
```bash
rm output/content/{Book}/chapter*-{type}.json
```

## Incremental Processing (AWS)

In AWS mode, each phase downloads only what it needs:

**Phase 1:** Downloads content files listed in the S3 manifest (from trigger Lambda). Falls back to full `content/` sync scoped by `BOOK_NAME` env var if no manifest.

**Phase 2:** S3 scan scoped by `BOOK_NAME` — compares `-parsed.json` files against `-event.json` files in S3. Only downloads parsed files that don't have a corresponding event file. Also downloads entity directories for cross-referencing.

**Phase 3:** Reads the DynamoDB manifest (`manifest#phase2`) which contains S3 keys of files uploaded by Phase 2 and modified by the dedup UI. Downloads only those files. Falls back to full entity directory download if no manifest exists. Uses `book_manifest#{book}#{entity_type}` for scoped entity downloads.

**Background sync mtime tracking:** S3 uploads track file modification times locally. Only files with changed mtime are uploaded, avoiding redundant S3 PutObject calls during final sync.

### DynamoDB Manifest

Phase 2's final sync writes all uploaded entity file keys to `manifest#phase2` in DynamoDB. The dedup UI appends keys for any files it modifies (merge, reclassify, assign actions). Phase 3 reads this manifest to download only changed files.

Phase 1 clears the manifest at the start of each new pipeline run.

### Pending Content Queue

When new content is uploaded while the pipeline is already running:

1. Trigger Lambda detects Phase 1 is locked
2. Saves the new file keys to `pending#content` in DynamoDB
3. Sends email notification: "Content queued — pipeline busy"
4. When Phase 2 completes, it checks `pending#content`
5. If pending files exist, clears the queue and re-triggers Phase 1 via SNS

Similarly, parsed files are queued as `pending#parsed` when Phase 2 is busy. The trigger Lambda only launches a phase if the pipeline is idle (no active lock for that phase family).

**Publish-before-delete ordering:** When re-triggering pending content, the new SNS message is published *before* the DynamoDB pending entry is deleted. This ensures no content is lost if the Lambda crashes between the two operations.

No content is lost and no concurrent processing occurs.

### Feedback Loop Prevention

Phase 2 and Phase 3 final syncs only upload entity subdirectories (people, places, groups, etc.), never book directories containing `-parsed.json` or `-event.json` files. S3 notifications for parsed/event files are scoped to the `output/content/` prefix, so entity uploads to `output/people/` etc. cannot re-trigger the pipeline. Dedup report sync only uploads `duplicate_report.json` files, not the entire output directory.

### Cross-Book Dedup Detection (AWS)

After Phase 2 extraction, the ECS container downloads the full entity inventory from S3 (all books) plus all event files (`output/content/`) before running dedup scripts. This ensures duplicate detection works across books — e.g., "Eisenhower" in Lorraine is matched against "Dwight D. Eisenhower" from Cross-Channel Attack. The event files provide text proximity signals needed for high-frequency entities (>15 event mentions).

## Phase 3: Enrich

```bash
python3 phase3_enrich_data.py

# Or use Batch API for 50% cost reduction (async, may take hours)
python3 phase3_enrich_data.py --batch
```

**AWS batch mode:** When `batch.phase3: true` in `config.yaml`, the ECS entrypoint auto-delegates to submit-only mode (same flow as Phase 2). S3 downloads are scoped by `BOOK_NAME` env var to avoid downloading all books.

**What it does:**
- Enriches people with biographical data from Wikipedia/Grokipedia
- Enriches groups with organizational history and command structure
- Enriches places with additional geographic and historical context
- Enriches bibliography with full citation data and source verification
- **Grok URL content verification** — verifies that URLs found for bibliography entries actually contain the expected content before accepting them
- **Resolves bibliography sources** — routes by document type:
  - Military records → Grok identifies NARA Record Group (RG 407, etc.) → OpenSERP for digitized copies → Archive.org
  - Books → Archive.org, Gutenberg
  - All external search results cached (positive 30 days, negative 7 days) to prevent redundant API calls
- **NOAA weather enrichment** — fetches observed historical weather data from NOAA CDO API for weather entities with coordinates and dates. Supplements Open-Meteo reanalysis data with actual station measurements.
- **Schema versioning** — stamps `_schema_version` and `_last_updated` on all files before enrichment. Skips if already current. Uses `try/finally` to re-enable the trigger Lambda even if the task crashes during stamping (crash safety).
- Searches for birth/death dates, service history, awards
- Follows references for additional context
- Caches all external lookups

**Enrichment Status Tracking:**

Each entity file gets an `enrichment_status` field after processing:
- `enriched` — external data found and added
- `not_found` — searched all sources, nothing found (skipped on future runs)
- No field — never searched yet

All entities also record `last_enrichment_search` (YYYY-MM-DD) for periodic re-search of not_found entities. Entities marked `not_found` are re-searched after `enrichment.re_search_after_days` (default: 90 days). Bibliography entries use `search_status` with the same values.

**OpenSERP Enrichment** (when `use_openserp: true`):
- People: portrait images, academic papers, oral histories, video interviews
- Equipment: photos, technical drawings
- Events: primary sources, veteran interviews (multi-language search)
- All results verified by Grok before acceptance
- Tracked via `openserp_searched` flag to prevent duplicate searches

**Book Entity Manifest (AWS):**

Phase 3 uses a book entity manifest (`book_manifest#{book}#{entity_type}` in DynamoDB) to scope S3 downloads. Instead of downloading all entities across all books, it downloads only entities relevant to the current book. The manifest is populated during Phase 2 extraction.

**source_url propagation:** When enrichment discovers a source URL for a bibliography entry, it is propagated into the corresponding `notes-event` files that reference that bibliography entry.

**Options:**
```bash
python3 phase3_enrich_data.py --max-items 50        # Limit items per type
python3 phase3_enrich_data.py --people-only          # Only enrich people
python3 phase3_enrich_data.py --no-references        # Skip reference following (faster)
```

**Output:** Updates existing `people/{Name}_{ID}.json` files in-place with enrichment data.

## Retry Wrappers

Both Phase 2 and Phase 3 have retry wrappers that handle transient errors automatically:

```bash
# Recommended: use retry wrappers instead of calling phases directly
python3 phase2_retry.py    # Retries until all event files exist (default: 3 attempts)
python3 phase3_retry.py    # Retries until all people are enriched (default: 3 attempts)
```

**Features:**
- Counts remaining work after each run
- Stops early if everything is processed
- Corrupted cache entries auto-cleared between retries
- **Poisoned cache auto-detection:** Detects cache entries that cause repeated failures and automatically evicts them before retry
- Configurable: `--max-attempts N`

See [Retry Wrappers](../pipeline/RETRY_WRAPPERS.md) for details.

## AWS Networking Lifecycle

NAT Gateway and VPC endpoints are created/deleted dynamically to minimize costs:

1. **Trigger Lambda** invokes `nat_manager(create)` before launching any ECS task
2. NAT + VPC endpoints (ECR API, ECR DKR, CloudWatch Logs) created in private subnets
3. Pipeline tasks run with internet access via NAT
4. **Submit-only task completion** → tears down NAT + scales OpenSERP to 0 immediately after batch submission
5. **Batch poller Lambda** → on batch completion, invokes `nat_manager(create)` before launching retrieve task
6. **Retrieve task completion** → tears down NAT + OpenSERP after results are processed

Infrastructure lifecycle is deterministic — no idle timeout needed. NAT only exists during active pipeline execution.

## Stale Lock Detection

If a task is killed mid-run, its DynamoDB lock persists. The trigger Lambda auto-detects stale locks:

1. Lock exists for a phase → check if an ECS task is actually running for that family
2. If no task running → lock is stale → clear it and proceed
3. If task IS running → legitimately locked → skip

An hourly EventBridge rule invokes the trigger Lambda to check for stale locks even when no new content is uploaded. This also reconciles the dedup gate — if review is marked complete but Phase 3 never launched (e.g., due to a transient failure), the hourly check detects this and re-triggers Phase 3.

## ECS Entrypoint Modes

The `ecs_entrypoint.py` supports three execution modes:

| Mode | Flag | Description |
|------|------|-------------|
| **Default** | (none) | Runs `run_phase` — full phase execution (parse, extract, or enrich) |
| **Submit-only** | `--submit-only` | Submits batch to Grok API, enqueues job in DynamoDB, tears down infra, exits |
| **Retrieve-only** | `--retrieve-only` | Downloads batch results, populates cache, re-runs phase with `SKIP_RETRY` |

**Auto-delegation:** When `batch.phase2: true` or `batch.phase3: true` in `config.yaml`, the default mode automatically delegates to submit-only. The trigger Lambda doesn't need to know about batch mode.

**Submit-only flow:**
1. Syncs content from S3
2. Runs the phase — cached results return instantly, uncached requests collected
3. Submits collected requests as one xAI batch job
4. Writes `batch_job#{batch_id}` to DynamoDB (status: `pending`)
5. Invokes `nat_manager(delete)` to tear down NAT Gateway
6. Scales OpenSERP service to 0
7. Exits with success

**Retrieve-only flow:**
1. Downloads batch results from Grok API
2. Writes results into DynamoDB cache
3. Re-runs the phase with `SKIP_RETRY=true` — all requests hit cache
4. Uploads results to S3
5. Updates job status to `retrieved`
6. Tears down networking
7. Triggers next pipeline stage (dedup/Phase 3)
