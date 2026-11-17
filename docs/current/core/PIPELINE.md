# Pipeline Documentation

**Last Updated:** 2026-11-16

## Overview

The extraction pipeline consists of four phases (Phase 0 ingestion + three
extraction phases), with a dedup review gate between Phase 2 and Phase 3:

### Phase 0: Ingestion Normalization
Normalizes any source medium into the pipeline's inputs *before* Phase 1. Built
in `src/ingestion/` (see
[../dataquality/INGESTION_FRONT_END.md](../dataquality/INGESTION_FRONT_END.md)
and [../dataquality/STRUCTURED_DATA_ROUTING.md](../dataquality/STRUCTURED_DATA_ROUTING.md)):
- **Media-type detection** — PDF / HTML / image / moving-image; unsupported
  media is recorded and flagged, never fails the run.
- **Per-page disposition classification** — `structured` | `unstructured` |
  `image` | `map`, heuristic + config-override, scanned-aware (a fully-scanned
  PDF defers structure to its OCR+AI markdown rather than guessing from
  geometry).
- **Region conversion** — each page/region → Markdown (the universal
  intermediate) via a disposition-appropriate method, extracting image/map
  assets that the prose converter previously dropped.
- **Language detection + translation** (on by default) — a **per-page** pass detects each
  page's language (deterministically when clearly English, else via Grok) and translates
  non-English pages to English (`normalize_pages_to_english`; documents without pages per
  section, so a mixed-language book is handled),
  so every downstream extractor (events, people, …) sees English and needs no language
  guard. The verbatim original is kept as a `<name>.orig.md` sidecar and `source_language`
  is stamped (provenance preserved). Idempotent on re-runs. Full spec:
  [../dataquality/LANGUAGE_TRANSLATION.md](../dataquality/LANGUAGE_TRANSLATION.md).
  (Foreign-language **maps** are handled separately at vision time — see
  [../features/maps/MAP_FEATURES_SCHEMA.md](../features/maps/MAP_FEATURES_SCHEMA.md).)
- **Scanned tabular parsing** — for scanned reference tables (e.g. the ETO
  Order of Battle), section parsers turn the OCR+AI markdown into structured
  rows (command-staff, campaigns, command-posts, statistics, organic-units) with
  division inference and verification-flagging, persisted to `output/oob/` and
  linked to `PersonID` via a non-destructive crosswalk.

Phase 0 runs primarily in the AWS path, driven by uploads to `contentrepository/`.
The trigger Lambda (`lambda_handlers/trigger_handler.py`) routes each upload by media
type:
- **PDF / scan** → Chandra OCR on AWS Batch GPU, chunked by pages (`src/ingestion/chunk_pages.py`;
  the chunk manifest `ocr-output/<slug>/.chunks.json` is written before submit). The OCR merge
  Lambda (`lambda_handlers/ocr_merge_handler.py`) waits for every chunk, then
  `src/ingestion/ocr_promote.promote` sanitises, translates, splits into chapters, normalises the
  markdown (`chandra_markdown`) and publishes `contentrepository/<slug>/chapterN/chapterN-{meta.yaml,content.md}`.
  An upload with a validation URL is cross-checked against it by the `phase0_ocr_crosscheck.py` ECS task.
- **EPUB / DOCX / HTML** → pandoc conversion (`phase0_convert.py`).
- **Video** → `phase0_video.py`.
- **Structured reference documents** (e.g. the ETO Order of Battle) → `phase0_ingest.py`, which
  also runs locally (`python phase0_ingest.py`) or in AWS (`ecs_entrypoint.py phase0_ingest.py`,
  syncing `output/oob/` back up).
- Binaries are scanned by ClamAV (a Fargate task, `src/ingestion/av_scan.py`); rejected uploads go
  to `needs-review/`.

A book's slug (`src/ingestion/book_slug.py`) is fixed at intake and never renamed. `chandra_markdown`
removes picture paragraphs from the chapter text (their alt text is model-written) and records them
as figures; Phase 1 attaches them (below).

### Phase 1: Parsing
Converts markdown source files into structured JSON with absolute paragraph numbering. On AWS, Phase 1 also (`ecs_entrypoint._prepare_phase1`, `_clear_manifest`):
- Clears its own Phase 1 lock (not the Phase 2/3 locks)
- Resets dedup review status (`complete: false`)
- Deletes the S3 manifest `manifests/pending.json` when it finishes (`_clear_manifest`). The DynamoDB `manifest#phase2` row is only cleared by `ecs_entrypoint._clear_all_locks`, which nothing calls in the current code, so the row keeps the previous Phase 2 keys until Phase 2 overwrites it (only Phase 2 reads it: `_read_manifest`, as a fallback)

### Phase 2: Extraction
Extracts entities and events using Grok AI (`grok-4.3`, `config.yaml` `api.grok`). Prompts are loaded from YAML templates in `prompts/` (overridable from S3). In AWS mode, Phase 2 uses **incremental processing** — only downloads and processes parsed files that don't have a corresponding event file in S3. When `batch.phase2: true`, the task auto-delegates to submit-only mode and works in **batch rounds** (`src/utils/batch_rounds.py`): each round collects every request that can be asked now, submits them as one xAI batch, waits in-task up to `batch.in_task_wait_minutes` (5), and if the batch is not yet answered hands it to the batch poller Lambda (`dev-wwii-batch-poller`, checks every 5 minutes, configurable), which launches a `--retrieve-only` task that resumes the book. Rounds repeat until a pass queues nothing (a stage that depends on an earlier answer, e.g. an event's entities or an equipment refine, can only be asked in a later round). `batch.max_rounds` (8) caps them: past the cap the run stops and alerts (`batch.rounds_over_cap`). After Phase 2 completes, if the dedup gate finds no duplicates (and fewer than `DEDUP_ZERO_SUSPICION_THRESHOLD` entities, default 500) and provenance validation does not hold it, Phase 3 is auto-triggered without waiting for human review.

Entity discovery (people, groups/units, places, dates, equipment, weather, logistics, casualties) runs over fixed 3-paragraph **windows** of the raw parsed text (`src/extraction/windows.py`), never over the events' summaries; each answer item is attributed to the real sub-event that contains its quote (`windows.settle`), and an item whose quote the source does not support is dropped (`quote_unsupported`). At the end of Phase 2, provenance validation (`scripts/validate_provenance.py`, `provenance_validation.gate` in `config.yaml`) writes `output/reports/provenance/<book>-phase2.json`.

**Phase 2 owns ALL Grokipedia/Wikipedia enrichment** (the enrichment split is *by source*):
each extractor enriches its entity natively as it writes it — people (biography + portrait +
award citations), people_groups (Wikipedia text + images), places (Grokipedia/Wikipedia
descriptive text + image), equipment (Wikipedia extract + images). Phase 2 also emits the
**`source_section`** entity — a coarse, source-neutral anchor (one per book chapter / article /
report) that carries a short section summary, a derived canonical **operation/campaign label**
(null-over-fake), and Grokipedia + Wikipedia reference articles + media for that operation
(`[phase2 step 4b/5]`). See [PHASE3_ENRICHMENT.md](PHASE3_ENRICHMENT.md) for the by-source
split and [features/source_section/README.md](../features/source_section/README.md).

### Dedup Review Gate
After Phase 2, duplicate detection runs automatically:
1. Military units in `output/places/` are auto-reclassified to `output/people_groups/`
2. Stale index entries are cleaned up
3. Exclusion pairs are migrated from local JSON to DynamoDB (one-time)
4. All four dedup detectors run as `python -m src.dedup.<module>` (people, places_v2, groups, equipment), reading exclusions from DynamoDB (AWS) or local JSON (local mode). A detector that fails emits `OBSERV dedup.detector_failed status=error` (Slack + email), so a missing duplicate report is never silent

The gate (`ecs_entrypoint._dedup_gate_decision` / `_phase3_gate_decision`) blocks when duplicate groups are pending, when detection failed or a report is missing, or when a large corpus (>= `DEDUP_ZERO_SUSPICION_THRESHOLD`, default 500 entities) shows zero groups. Provenance validation also holds Phase 3 when `provenance_validation.gate` is `block` (in `warn` it only alerts). In AWS mode, a web UI allows merging, skipping, and reclassifying entities before Phase 3 proceeds. UI actions (merge, reclassify, assign) append changed file keys to the DynamoDB manifest so Phase 3 downloads them. "Not Duplicates" decisions are stored in DynamoDB and persist across pipeline runs.

### Phase 3: Enrichment
Enriches entities with **every external source EXCEPT Grokipedia/Wikipedia** (those moved to
Phase 2). Fixed step order: people_groups (structured unit/org history), places (hierarchy/
names + **geocoding cascade**: Nominatim→hill→Grok), bibliography (ISBN/copyright + NARA
**and non-NARA** source resolution — Archive.org, HathiTrust, university libraries, Google
Books, LOC), and — when enabled — **OpenSERP** for people, equipment, people_groups, places
(multi-language, native place names), and source_section (primary sources keyed on the
operation label), plus weather (NOAA observed). OpenSERP results are Grok-verified
(**fail-closed**), and verified textual pages are fetched + summarized with a 90-day
positive/negative URL cache. Dates, casualties, maps, map_features, and events have no Phase-3
step by design (events are anchored for enrichment via source_section). Each source is isolated
(`_run_step`): a failure is logged + surfaced via `.phase_results.json` → email/Slack, never
aborting the rest. Full detail, config gating, batch re-run, and the reliability model:
**[PHASE3_ENRICHMENT.md](PHASE3_ENRICHMENT.md)**. In AWS mode, Phase 3 scopes its download by the per-book `book_manifest#` rows (`ecs_entrypoint._get_book_entity_files`, `src/utils/book_manifest.py`), else lists the entity prefixes in S3 (`_download_phase3_from_s3`); it does not read `manifest#phase2`.

### Caching
- **Local mode:** diskcache (SQLite) in `cache/api/`
- **AWS mode:** DynamoDB table (`dev-wwii-api-cache`). Cached responses persist across ECS task runs, so re-processing only calls Grok for new/changed chapters.

## Phase 1: Parse

```bash
python3 phase1_parse.py
```

**Input:** `contentrepository/{Book}/chapter*/chapter*-content.md` (+ `-meta.yaml`; `sourcedocument/` and `_figures/` are skipped by `src/discovery.py`)  
**Output:** `output/content/{Book}/chapter*-parsed.json` (plus `chapter*-footnotes.json` / `chapter*-endnotes.json`)

**Features:**
- Absolute paragraph numbering across entire book
- Inline entity extraction (images, maps, footnotes, page markers; per-paragraph `page_number`)
- **Book figures:** for an OCR'd PDF book, Phase 1 builds the book's own figures once (`src/ingestion/book_figures.ensure`, from the PDF + OCR crops) into `contentrepository/<book>/_figures/figures.json`, rebuilt only when the PDF or OCR submission changes. Each picture is assigned to one chapter by PDF page, each numbered map to every chapter that prints or cites it (`book_figures.attach_book`). Web (HyperWar) sources take images/maps from links in the text (`parser.extract_images` / `extract_maps`)
- Metadata from YAML files
- **Translation markers:** the Phase 0 translation pass leaves inline markers in the markdown; Phase 1 lifts them out of the text and records `translated_from` (source language) on each `Paragraph` or `Footnote` that holds translated text (`parser._mark_translated_paragraphs`). Phase 0 translates non-English text by default (`ingestion.translate` in `config.yaml`, env `PHASE0_TRANSLATE=0` turns it off)
- Preserves source structure
- **Source hash skip:** Computes content hash — unchanged files are not re-written or re-uploaded to S3
- **Lock scoping:** Only clears its own Phase 1 lock (not Phase 2/3 locks)
- **Full-sync scoped by `BOOK_NAME`** env var (AWS mode — avoids downloading all books)
- **Atomic file writes:** Uses temp file + `os.replace()` via `write_json_with_lock()` to prevent corruption
- **Auto-splitting:** Chapters >400K chars automatically split into ~50 paragraph chunks (e.g., `chapter20a`, `chapter20b`)

**Large Chapter Handling:**
When a chapter exceeds 400K characters (leaves 100K headroom for API responses):
- Splits into chunks of 50 paragraphs, each sharing 3 paragraphs with the previous one (`phase1_parse._save_split_chapter`)
- Names: zero-padded numeric suffix, `chapter20c00-parsed.json`, `chapter20c01-parsed.json`, etc.
- Each chunk includes the full metadata and the images/maps that belong to its paragraphs (`_keep_chunk_figures`)
- Original oversized file is not created
- Prevents Phase 2 token limit issues

## Phase 2: Extract

```bash
python3 phase2_extract.py

# Or use Batch API for 50% cost reduction (async, may take hours)
python3 phase2_extract.py --batch

# Scope to one book; abort if the chapter count differs
python3 phase2_extract.py --book Cross_Channel_Attack --expected-count 20
```

**AWS batch mode:** When `batch.phase2: true` in `config.yaml`, the ECS entrypoint auto-delegates to submit-only mode. Each round the task submits the batch, enqueues it in DynamoDB (`batch_job#{batch_id}`) and, if not answered within `batch.in_task_wait_minutes`, exits (exit status 75, "handed off"); the networking teardown is left to the `nat_teardown` Lambda. The batch poller Lambda handles retrieval and launches the next round. See [Batch Processing](../features/batch_processing/README.md).

**Pipeline Stages:**
0. **Metadata Completion** - Auto-fills missing chapter titles/numbers (`_complete_metadata`)
1. **Parallel Core Extraction** - `process_chapters_parallel` processes chapters concurrently (`concurrency` in `config.yaml`):
   - Event extraction, one request per chapter (Event with Sub-events; orphan paragraphs are claimed by the nearest earlier sub-event)
   - Dates, Places, People Groups, People discovered over 3-paragraph windows of the raw text (`src/extraction/windows.py`), attributed to the real sub-event by quote
2. **Retry Missing Events** - Retries any chapters that failed event extraction (`_retry_missing_events`)
3. **Deterministic bibliography pass** - a citable bibliography record per parsed footnote/endnote, no LLM (`extract_bibliography_deterministic`), when `supplemental_material.enabled`
4. **Optional Entity Extraction** - per event file (`_extract_optional_entities`):
   - Weather, Equipment (broad discovery + refine, over windows), Logistics, Casualties (personnel only: killed, wounded, missing, POW), Supplemental material (notes; also windowed within each sub-event, assigns `reference_number` / `verbatim_reference` deterministically) - each if enabled in `config.yaml`
5. **Maps / Images** - external maps (`_extract_external_maps`, OpenSERP if enabled), images (`src/extraction/images.py`)
6. **Source-section enrichment** (`[phase2 step 4b/5]`, live, not batched)
7. **Analysis** - Duplicate people report + related groups report; then exact-duplicate auto-merge, the dedup gate and provenance validation (in `ecs_entrypoint._post_process`)

In batch mode the steps run once per round; the work that needs an earlier answer is asked in the next round.

**Auto-split on truncation:** If a Grok API response is truncated, the chapter is automatically extracted in parts (`events._split_on_truncation`) and the parts are merged.

**Heartbeat monitor:** Both Phase 2 and Phase 3 log progress per chapter. A warning is logged if no progress is made for 5 minutes, showing the last item processed.

**Smart retry:** Retry logic skips footnotes-only sections that consistently fail extraction (no actionable content).

**JSON quality statistics:** Extraction quality metrics (valid JSON rate, schema compliance) are tracked per batch and stored in DynamoDB for monitoring.

**Output Files:** (chapter files under `output/content/{Book}/`; entity directories are siblings under `output/`)
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

All Grok API responses are cached (local mode `cache/api/`, AWS mode the DynamoDB cache table), one sub-cache per request type (`cache_type`), e.g. `events`, `dates`, `places`, `people`, `people_groups`, `weather`, `logistics`, `casualties`, `equipment_broad`, `equipment_refine`, `supplemental`, `source_section`. Cache keys are book-scoped (`GrokClient.cache_key_for`).

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

**Phase 3:** Downloads the entity files of the book (`BOOK_NAME`) listed in the book manifest (`book_manifest#{book}#{entity_type}`, `ecs_entrypoint._get_book_entity_files`), plus the shared `bibliography` and `supplemental` directories in full; with no `BOOK_NAME` it downloads every entity directory (`_download_phase3_from_s3`). It first waits for Phase 2's bibliography reconcile barrier.

**Background sync mtime tracking:** S3 uploads track file modification times locally. Only files with changed mtime are uploaded, avoiding redundant S3 PutObject calls during final sync.

### DynamoDB Manifest

Phase 2's final sync writes all uploaded entity file keys to `manifest#phase2` in DynamoDB (`_write_manifest`). The dedup UI appends keys for any files it modifies (merge, reclassify, assign actions). `_read_manifest` also reads it as a legacy fallback for the per-book `pending#parsed#{book}` queue.

Phase 1 deletes only the S3 file `manifests/pending.json` when it finishes (`_clear_manifest`); it does not touch `manifest#phase2`.

### Per-Book Queues

Each book gets its own DynamoDB queue entry. When a phase is locked (another book is running), work is queued per-book rather than dropped.

**Queue keys:**

| Key Pattern | Written By | Consumed By | Purpose |
|---|---|---|---|
| `pending#content` | Trigger Lambda | Phase 1 (`_clear_processed_content_keys`) and the trigger's `_reconcile_phase1` | Raw content files awaiting Phase 1 |
| `pending#parsed#{book}` | Trigger Lambda (`_queue_parsed`) | Phase 2 `_read_manifest` (on task start) | Parsed files for a specific book awaiting Phase 2 |
| `pending#enrich#{book}` | Trigger Lambda | Phase 3 `_get_next_pending_enrich` / `_consume_pending_enrich` (on completion) | Book awaiting Phase 3 enrichment |

**Multi-book flow:**

```
Book A uploaded → Phase 2 starts (Book A)
Book B uploaded → Phase 2 locked → queued as pending#parsed#BookB

Book A Phase 2 completes → checks pending#parsed#* → finds BookB
  → triggers Phase 2 for BookB (networking stays up)
  
Book B Phase 2 completes → no more pending books
  → auto-triggers Phase 3 for BookB (if no duplicates)
  → tears down networking (nothing pending)

--- If Phase 3 overlaps: ---

Book A Phase 3 running, Book B Phase 3 triggered:
  → trigger sees Phase 3 lock held → queues pending#enrich#BookB

Book A Phase 3 completes → releases lock → checks pending#enrich#*
  → finds BookB → triggers Phase 3 for BookB (networking stays up)

Book B Phase 3 completes → no pending enrich or parsed queues
  → tears down networking + OpenSERP
```

**Networking optimization:** Networking (NAT Gateway, VPC endpoints) stays up as long as either queue has entries. Teardown only happens when both `pending#parsed#*` and `pending#enrich#*` are empty after a phase completes.

**Queue consumption:**
- `pending#parsed#{book}` is consumed (deleted) when the Phase 2 task starts and reads its manifest via `_read_manifest()`
- `pending#enrich#{book}` is consumed (deleted) by `_get_next_pending_enrich()` when Phase 3 completes and picks up the next book

**Ordering:** Books are processed in arrival order (first match from DynamoDB scan). No priority mechanism.

**Legacy compatibility:** The old `pending#parsed` (no book suffix) is still read as a fallback if no per-book queue exists.

### Pending Content Queue

When new content is uploaded while the pipeline is already running:

1. Trigger Lambda detects Phase 1 is locked
2. Appends the new file keys to `pending#content` in DynamoDB (`_queue_pending`, atomic `list_append`)
3. Sends email notification: "Content queued — pipeline busy"
4. When Phase 2 completes with no other book queued, the entrypoint invokes the trigger Lambda with `source: phase-complete` (`_invoke_trigger_phase_complete`); the trigger drains parked content (`_reconcile_phase1`). The 15-minute reconcile schedule is the backstop
5. Phase 1 removes only the keys of the book it processed from the queue (`_clear_processed_content_keys`)

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

**AWS batch mode:** `batch.phase3` is `false` in `config.yaml` (live calls, no submit/retrieve bounce). When it is `true` (or `BATCH_PHASE3` is set), the ECS entrypoint auto-delegates to submit-only mode (same flow as Phase 2). S3 downloads are scoped by `BOOK_NAME` env var to avoid downloading all books.

**What it does** (step order in `phase3_enrich_data.main`; full map in [PHASE3_MAP.md](PHASE3_MAP.md)). People biographies, group Wikipedia text and equipment Wikipedia text/images are NOT Phase 3 steps; Phase 2 writes them.
- **people_groups** - structured org history (unit type, nationality, commanders, operations) via Grok (`enrich_groups_data`)
- **places** - hierarchy and names (`enrich_all_places`), `link_parent_place_ids`, then the geocoding cascade Nominatim -> hill -> Grok
- **bibliography** - ISBN, copyright and archive URLs (`enrich_bibliography`), then source resolution (`resolve_bibliography_dir`): Grok identifies the NARA Record Group (RG 407, etc.), then OpenSERP, Archive.org, Gutenberg and other non-NARA sources. A Grok URL content check confirms a found URL holds the expected content. External search results are cached (positive 30 days, negative 7 days)
- **Document media** - in-document map links and deferred map vision (`_run_document_media_steps`), independent of OpenSERP
- **OpenSERP group** (when `supplemental_material.use_openserp` is true; on ECS the entrypoint forces it on) - see below
- **equipment_images** - Grok-vision confirmation of candidate images for specific equipment
- **NOAA weather** - observed station data from the NOAA CDO API, only when `api.noaa_api_token` is set
- **Schema versioning** - stamps `_schema_version` and `_last_updated` on all files before enrichment. Skips if already current. Uses `try/finally` to re-enable the trigger Lambda even if the task crashes during stamping

**Enrichment Status Tracking:**

Each entity file gets an `enrichment_status` field after processing:
- `enriched` — external data found and added
- `not_found` — searched all sources, nothing found (skipped on future runs)
- No field — never searched yet

All entities also record `last_enrichment_search` (YYYY-MM-DD) for periodic re-search of not_found entities. Entities marked `not_found` are re-searched after `enrichment.re_search_after_days` (180 in `config.yaml`). Bibliography entries use `search_status` with the same values.

**OpenSERP Enrichment** (when `use_openserp: true`):
- People: portrait images, academic papers, oral histories, video interviews
- Equipment: photos, technical drawings (specific records only)
- People groups and places: images and web sources (multi-language, native place names)
- source_section: primary sources and witness accounts keyed on the operation label
- All results verified by Grok before acceptance
- Tracked via the `openserp_searched` flag (a 90-day gate); at every ECS Phase 3 start it is cleared on people and equipment with no hits, so those are searched again ([PHASE3_MAP.md](PHASE3_MAP.md) §3.9)

**Book Entity Manifest (AWS):**

Phase 3 uses a book entity manifest (`book_manifest#{book}#{entity_type}` in DynamoDB) to scope S3 downloads. Instead of downloading all entities across all books, it downloads only entities relevant to the current book. The manifest is populated during Phase 2 extraction.

**source_url propagation:** When enrichment discovers a source URL for a bibliography entry, it is propagated into the corresponding `notes-event` files that reference that bibliography entry.

**Options:**
```bash
python3 phase3_enrich_data.py --max-items 50        # Limit items per type
python3 phase3_enrich_data.py --people-only          # Skips every step (people enrichment moved to Phase 2)
python3 phase3_enrich_data.py --no-references        # Skip reference following (faster)
```

**Output:** Updates existing entity files (`people_groups/`, `places/`, `bibliography/`, `people/` via OpenSERP, ...) in place with enrichment data.

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

## AWS Networking Lifecycle

NAT Gateway and VPC endpoints are created/deleted dynamically to minimize costs:

1. **Trigger Lambda** invokes `nat_manager(create)` before launching any ECS task
2. NAT + VPC endpoints are created in private subnets; each task also validates NAT readiness (`_ensure_nat_if_needed`) and registers a NAT lease (`nat#lease#`)
3. Pipeline tasks run with internet access via NAT
4. **Batch poller Lambda** → on batch completion, invokes `nat_manager(create)` before launching the retrieve task
5. **Phases never tear down networking.** The `nat_teardown` Lambda (`lambda_handlers/nat_teardown.py`) is the sole teardown decider: it runs on a 4-hour schedule and deletes NAT + endpoints (via `nat_manager(delete)`) only when no lease, running pipeline task, in-flight OCR Batch job or pending/ready `batch_job#` row remains. While a container is still working it re-checks every 10 minutes.

## Stale Lock Detection

If a task is killed mid-run, its DynamoDB lock persists. The trigger Lambda auto-detects stale locks:

1. Lock exists for a phase → check if an ECS task is actually running for that family
2. If no task running → lock is stale → clear it and proceed
3. If task IS running → legitimately locked → skip

A configurable EventBridge rule (default: every 15 minutes) invokes the trigger Lambda to check for stale locks even when no new content is uploaded. This also reconciles the dedup gate — if review is marked complete but Phase 3 never launched (e.g., due to a transient failure), the check detects this and re-triggers Phase 3.

## ECS Entrypoint Modes

The `ecs_entrypoint.py` supports three execution modes:

| Mode | Flag | Description |
|------|------|-------------|
| **Default** | (none) | Runs `run_phase` — full phase execution (parse, extract, or enrich) |
| **Submit-only** | `--submit-only` | Submits batch to Grok API, enqueues job in DynamoDB, exits (teardown is left to `nat_teardown`) |
| **Retrieve-only** | `--retrieve-only` | Downloads batch results, populates cache, re-runs phase with `SKIP_RETRY` |

**Auto-delegation:** When `batch.phase2: true` or `batch.phase3: true` in `config.yaml`, the default mode automatically delegates to submit-only. The trigger Lambda doesn't need to know about batch mode.

**Submit-only flow:**
1. Syncs content from S3
2. Runs the phase — cached results return instantly, uncached requests collected
3. Submits collected requests as one xAI batch job
4. Writes `batch_job#{batch_id}` to DynamoDB (status: `pending`)
5. Stops OpenSERP if the task started it
6. Exits with success (status 75 when a Phase 2 round was handed to the poller)

**Retrieve-only flow:**
1. Downloads batch results from Grok API
2. Writes results into DynamoDB cache
3. Re-runs the phase with `SKIP_RETRY=true` — all requests hit cache
4. Uploads results to S3
5. Updates job status to `retrieved`
6. Triggers the next pipeline stage (dedup gate / Phase 3); networking teardown is left to `nat_teardown`

## Recent Features (2026-06)

### Cache Preloading

On startup, `DynamoCacheBackend` performs a single DynamoDB scan to load all cache entries into memory. This eliminates per-request `GetItem` calls during extraction (~1600+ for a typical book). Sub-caches share the parent's preloaded data.

### DynamoDB Materialization

Removed. The DynamoDB entity store no longer exists: S3 (`output/<entity>/*.json`) is the sole entity store, and `ecs_entrypoint._materialize_from_dynamo()` is a deprecated no-op that always returns False. DynamoDB holds only cache, queue and coordination rows.

### Incremental Dedup

Dedup scripts track the last run time in DynamoDB (`dedup_run#{entity_type}`). On subsequent runs, only pairs where at least one file is new (mtime > last run) are compared. Reduces O(n²) comparisons dramatically for large entity sets where only a few files changed.

### Auto-Trigger Phase 3

After Phase 2 dedup detection completes, `_dedup_gate_decision` returns `auto_proceed` only when all reports are present with zero duplicate groups and the corpus is under `DEDUP_ZERO_SUSPICION_THRESHOLD` (500) entities; Phase 3 is then triggered via the trigger Lambda without waiting for human review (unless provenance validation holds it in `block` mode). Otherwise the gate blocks and notifies.

### Prompt Validation

`GrokClient._validate_prompt()` runs before every API call. Checks:
- Empty/whitespace-only prompts → `ValueError`
- Unfilled template placeholders (`{book}`, `{author}`) → `ValueError`
- Empty data sections (e.g., "Text:\n\n") → `ValueError`
- Oversized prompts (>500k chars) → `ValueError`

All extractors also guard against empty input text by returning `""` from prompt creation, which the caller skips.

### Merge Undo/Snapshots

The dedup UI saves a pre-merge snapshot (all involved entity files) to `dedup/history/{timestamp}/` in S3 before executing a merge. The merge response includes a `snapshot_id`. The `POST /dedup/api/undo` endpoint restores files from the snapshot.

### S3 Deletion Tracking

When dedup merge deletes a local entity file, `track_deletion()` records it in `_deleted_keys`. During `_final_sync`, these keys are deleted from S3. Prevents orphan files from lingering after merges.

### OpenSERP Circuit Breaker

`src/enrichment/openserp_enrichment.py` tracks consecutive failures. After 5 consecutive empty responses or errors, the circuit breaker opens and all remaining searches are skipped. Resets on any successful response.

### Configurable Intervals (CloudFormation)

Timing constants are now CloudFormation parameters:
- `BatchPollerIntervalMinutes` (default 5)
- `ReconciliationIntervalMinutes` (default 15)
- `NatWaitSeconds` (default 270)
- `TeardownDelayMinutes` is still a parameter, but teardown is now owned by the 4-hour `nat_teardown` schedule (see AWS Networking Lifecycle)

### Batch Poller State Machine

Jobs flow through: `pending` → `ready` → `retrieved` (or `failed`). The `ready` state means Grok confirmed completion but the retrieve task hasn't launched yet. If retrieve fails, the job stays `ready` and is retried on the next poll without re-checking Grok.
