# Configuration Guide

**File:** `config.yaml` (shipped defaults: `config.yaml.example`; `config.yaml` is gitignored local state)  
**Last Updated:** 2026-11-17 (checked against code at main 0dac86fa)

Complete reference for all configuration options in the WWII data extraction pipeline.

---

## Paths

Directory structure for the project.

```yaml
paths:
  content_root: "contentrepository"  # Source documents
  output_root: "output"              # Extracted data (entity directories)
  content_output: "output/content"   # Book-specific output (parsed/event files)
  cache_root: "cache"                # API and download caches
  review_root: "review"              # Manual review files
  
  # Cache subdirectories
  api_cache: "cache/api"             # Grok API response cache
  image_cache: "cache/images"        # Downloaded images
  maps_images: "output/maps_images"  # Map images from source material
  
  # Central data files
  people_data: "output/people.json"
  peoplegroup_data: "output/peoplegroups.json"
  supplemental_data: "output/supplemental.json"
```

**Notes:**
- All paths are relative to project root
- Directories are created automatically if they don't exist
- `content_output` separates book-specific files (parsed/event) from entity directories. Backwards compatible: if `output/content/` doesn't exist, falls back to `output/` (old layout). Run `python3 scripts/archive/migrate_output_content.py` to migrate.

---

## API Configuration

Settings for external API services.

```yaml
api:
  grok:
    base_url: "https://api.x.ai/v1/chat/completions"
    model: "grok-4.3"
    model_map: {}                 # per-task model overrides, e.g. isbn: "grok-3-mini" (empty = all tasks use `model`)
    max_retries: 3                # shipped, but NOT read by code (see Options)
    timeout: 60                   # shipped, but NOT read by code (see Options)
  nara_api_key: ""                # secret; env NARA_API_KEY overrides (src/utils/config.py:_apply_env_overrides)
  noaa_api_token: ""
  calls_per_minute: 20            # Rate limit: max API calls per minute (across all threads; 1-200, validated in src/utils/config.py; divided by pool size in `grok_client._effective_calls_per_minute`)
```

**Options:**
- `base_url` — Grok API endpoint
- `model` — Default model for all tasks. Used when `model_map` has no entry for the task.
- `model_map` — Per-task model routing for cost optimization. Maps `cache_type` (task name) to a model. Uncommented entries use cheaper/faster models for simple tasks. Keys: `events`, `dates`, `places`, `people`, `peoplegroups`, `weather`, `equipment`, `logistics`, `casualties`, `supplemental`, `isbn`, `url_verify`, `copyright`, `nara_match`, `openserp_verify`. Unset keys use the default `model`.
- `max_retries`, `timeout` — present in `config.yaml.example` but not read by any code. `GrokClient` hard-codes a 600 s read timeout (`src/grok_client.py:479`, connect timeout 10 s at `:1463`) and 5 attempts with exponential backoff of 4-60 s (`:1477-1478`).
- `calls_per_minute` — Proactive rate limit across all threads (`config.yaml.example` ships 20; the code falls back to 30 if the key is absent; profiles set 30, 60, 30). Irrelevant in batch mode.

**NARA Catalog API:**
- `api.nara_api_key` — API key for National Archives catalog search. Get from [archives.gov](https://www.archives.gov/research/catalog/help/api-getting-started). Used by bibliography resolver to find digitized military records and identify Record Groups. 10,000 requests/month limit. Leave empty to skip NARA search (Grok still identifies Record Groups without it).

**External Search Cache:**
All external search results (Wikipedia, Grokipedia, Archive.org, NARA, LOC, OpenSERP) are cached in DynamoDB (AWS) or local disk (local mode):
- Positive results: cached 30 days (`POSITIVE_TTL_DAYS`, `src/utils/search_cache.py:17`)
- Negative results (not found): cached 7 days (`NEGATIVE_TTL_DAYS`, `:18`)
- Prevents redundant HTTP calls across pipeline runs

**Environment Variables Required:**
- `GROK_API_KEY` - Your Grok API key (set in `.env`)

---

## Processing

General processing options.

```yaml
processing:
  validate_ulids: true
  generate_jq_queries: false
  generate_download_scripts: false
  force_reprocess: false             # WARNING: expensive — reprocesses all content regardless of cache
  reprocess_types: []                # entity types re-extracted even if already processed (src/utils/config.py:should_reprocess)
  date_window: {start: "1930-01-01", end: "1950-12-31"}  # semantic flag date_out_of_window; env DATE_WINDOW_START / DATE_WINDOW_END override
```

**Options:**
- `validate_ulids`, `generate_jq_queries`, `generate_download_scripts` - shipped in the example; no code under `src/` or the `phase*.py` entrypoints reads them (UNVERIFIED for scripts/)
- `reprocess_types` - entity types that ignore the processed registry (`should_reprocess`)
- `date_window` - plausibility window for the advisory `date_out_of_window` flag (`src/utils/semantic_flags.py:42-49`)
- `force_reprocess` - When true, ignores existing output files and reprocesses everything. **Use with caution** — this bypasses all skip logic and will re-call the Grok API for all chapters.

---

## Batch Mode

Controls xAI Batch API usage for 50% cost reduction. When enabled, the ECS entrypoint auto-delegates to submit-only mode instead of running the phase inline.

```yaml
batch:
  phase2: true                       # Use batch API for Phase 2 extraction
  phase3: false                      # Phase 3 uses live calls (avoids a network bounce between submit/retrieve)
  max_rounds: 8                      # Phase 2 batch rounds before the batch.rounds_over_cap alert + stop
  in_task_wait_minutes: 5            # per round: how long the task waits for the batch; env BATCH_IN_TASK_WAIT_MINUTES
  min_requests: 50                   # fewer queued requests are answered live, not batched; env BATCH_MIN_REQUESTS; 0 = always batch
```

**Options:**
- `phase2` - When true, Phase 2 auto-delegates to `run_submit_only` (submits batch, enqueues job, exits). The batch poller Lambda handles retrieval.
- `phase3` - When true, Phase 3 auto-delegates to `run_submit_only` (same flow as Phase 2). Default `false`.
- `max_rounds` - Phase 2 runs batch **rounds** (`src/utils/batch_rounds.py`): collect requests, submit, repeat until a pass queues nothing. After this many rounds it alerts (`batch.rounds_over_cap`) and stops.
- `min_requests` - A submission with fewer requests than this is answered live (`GrokClient.submit_batch`, `src/utils/batch_api.py:43` `batch_min_requests`), because a batch can take up to 24 h. Default 50.
- `in_task_wait_minutes` - Each round the task polls its batch for this long (`src/utils/batch_api.in_task_wait_minutes`); an unanswered batch is handed to the batch poller Lambda, which launches a `--retrieve-only` task when it is answered.

**Behavior:** When `batch.phase2: true`, calling `ecs_entrypoint.py` with `--phase phase2` automatically runs in submit-only mode. The entrypoint detects the batch config and delegates without needing `--submit-only` explicitly. This means the trigger Lambda doesn't need to know about batch mode — it launches Phase 2 normally and the entrypoint handles the rest.

---

## Weather Extraction

Historical weather data extraction via Open-Meteo API.

```yaml
weather:
  enabled: true                    # Enable weather extraction
  fetch_api_data: true            # Fetch from Open-Meteo API
  api_provider: "open-meteo"       # API provider
  cache_responses: true            # Cache API responses
  only_precise_dates: true         # Skip approximate dates
  timeout: 30                      # API timeout (seconds)
```

**Options:**
- `enabled` - Enable/disable weather extraction
- `fetch_api_data` - Query external weather API
- `api_provider` - Currently only `open-meteo` supported
- `cache_responses`, `only_precise_dates`, `timeout` - shipped in the example; the Phase 2 weather step (`phase2_extract.py:396-415`) reads only `enabled` and `fetch_api_data` (UNVERIFIED in `src/extraction/weather*.py`)

**API:** [Open-Meteo Historical Weather API](https://open-meteo.com/)

---

## Equipment Extraction

Military equipment extraction from events (experimental).

```yaml
equipment:
  enabled: true                    # Enable equipment extraction (experimental)
  enable_enrichment: false         # Enrich with Wikipedia/Grokipedia data (slower, uses more API calls)
  verify_media_with_vision: false  # Verify media relevance with Grok vision API
```

**Options:**
- `enabled` - Enable/disable equipment extraction
- `enable_enrichment` - Enrich equipment with external data (Wikipedia, Grokipedia); read at `phase2_extract.py:446`
- `verify_media_with_vision` - Use Grok vision API to verify downloaded media is relevant (`src/extraction/equipment.py:_verify_media_with_vision`)
- `logistics.enabled` - the same on/off switch for logistics extraction (`phase2_extract.py:474`)

**Output:** `output/equipment/{name}_{ulid}.json`

**Documentation:** See `docs/archive/2026-10-11/features/equipment/MILITARY_EQUIPMENT.md` (archived)

---

## Casualties Extraction

Personnel casualty tracking from events (experimental). Tracks killed, wounded, missing, and POW. Equipment/materiel losses belong in the Equipment entity.

```yaml
casualties:
  enabled: true                    # Enable casualties extraction (experimental)
```

**Options:**
- `enabled` - Enable/disable casualties extraction

**Output:** `output/casualties/{type}_{ulid}.json`

**Fields:** `type` (killed/wounded/casualties/pow/missing), `side` (allied/axis/civilian/unknown), `count` (with qualifiers), `impacted_organizations`, `impacted_people`, `impacted_places`. No equipment or weather fields.

---

## Supplemental Material Extraction

Citations, footnotes, endnotes, and bibliographic references.

```yaml
supplemental_material:
  enabled: true                    # Enable supplemental material extraction
  extract_citations: true          # Phase 2: parse citations into structured bibliography entries
  llm_search: true                 # Phase 3: Grok "LLM search" fallback (content-verified)
  search_gutenberg: true           # Phase 3: search Gutenberg
  search_archive_org: true         # Phase 3: search Archive.org
  use_openserp: true               # Phase 3 ONLY: OpenSERP live web search
  verify_archive_urls: false       # Phase 3: Verify archive URLs
  extract_isbn: false              # Phase 3: Extract ISBN for books (post-1966)
  determine_copyright: false       # Phase 3: Determine copyright status
  max_materials_per_chapter: 500   # Limit materials per chapter
```

**Phase 2 performs NO supplemental searches.** It only talks to Grokipedia/Wikipedia and submits data to Grok for
extraction. Citations are stored in `output/bibliography/` with an EMPTY `resource_urls`; the (network-free)
license is derived from the citation fields. There is no `enrich_with_searches` flag any more.

**Phase 2 (Core):**
- `extract_citations` - Parse footnotes/endnotes into structured citation format

**Phase 3 (Search — `bibliography_resolver`):** every candidate is Grok-verified against the citation before it is stored
- `llm_search` - Ask Grok for an online copy of the cited work (last resort, only when no other source found one; the URL is fetched SSRF-safely and content-verified)
- `search_gutenberg` - Search Project Gutenberg
- `search_archive_org` - Search Internet Archive
- `use_openserp` - Use OpenSERP for live web search (images and web pages for people, equipment, groups and places; primary sources for source sections; external maps when `external_maps.enabled`; bibliography repository search)

**Phase 3 (Advanced):**
- `verify_archive_urls` - Verify that discovered archive URLs are still accessible
- `extract_isbn` - Extract ISBN numbers for books published after 1966
- `determine_copyright` - Calculate copyright status based on publication date

**Documentation:** See `docs/current/features/supplemental/SUPPLEMENTAL_COMPLETE.md`

---

## Concurrency

Parallel processing settings.

```yaml
concurrency:
  enabled: false                   # Enable concurrent processing (experimental)
  max_event_files: 3               # Process N event files concurrently
  max_extraction_group: 3          # Max parallel extractions per group
  max_enrichment_workers: 4        # enrichment fetch threads (Phase 2 bio pool, Phase 3 per entity type)
  bio_enrichment_parallel: true    # parallelize the per-person Phase 2 bio fetch (persist stays serial); false forces serial
```

**Options:**
- `enabled` - Enable/disable concurrent chapter processing
- `max_event_files` - Number of event files to process in parallel
- `max_extraction_group` - Max parallel extractions within a single group (dates, places, etc.)
- `bio_enrichment_parallel` - `src/extraction/bio_enrich_parallel.py:73`; default true.
- `max_enrichment_workers` - enrichment fetch threads: the Phase-2 biography-enrichment pool (`bio_enrich_parallel`, capped at 16) and Phase 3's per-entity-type pool (shipped 4; code fallback 6 when unset). Grok API calls are rate-limited; extra threads keep the external search/geocode/OpenSERP requests running in parallel. (Grokipedia/Wikipedia enrichment runs in Phase 2, not here.)

**Note:** Concurrent processing is experimental. See `docs/archive/FUTURE_ENHANCEMENTS.md` for distributed processing options. `src/utils/config.py` validates the three `max_*` keys as positive integers.

---

## Phase 0 Ingestion: Translation

```yaml
ingestion:
  translate: true     # default when absent: translate non-English documents to English
```

- `ingestion.translate` - Phase 0 language detection + translation for every track (convert, OCR,
  video, OOB), `src/ingestion/translation.translation_enabled`. **On by default** (key absent =
  on); `false` turns it off. `PHASE0_TRANSLATE=0` / `=1` overrides the file. English text costs
  nothing (`clearly_english`); only text that is not clearly English asks Grok its language, and only
  non-English pages / sections are translated. See
  [../dataquality/LANGUAGE_TRANSLATION.md](../dataquality/LANGUAGE_TRANSLATION.md).

## Phase 2 Extraction, Provenance Gate, Dedup, Storage

```yaml
extraction:
  window_paragraphs: 3        # paragraphs per discovery window (env WINDOW_PARAGRAPHS overrides)

provenance_validation:
  gate: warn                  # warn = report + alert, book goes on to Phase 3; block = Phase 3 held when a hard check fails

enrichment:
  re_search_after_days: 180   # re-search an entity after this many days (code default 90 if the key is absent)

storage:
  entity_backend: "filesystem"
```

- `extraction.window_paragraphs` - people, groups/units, places, dates, equipment, weather, logistics and casualties are discovered over fixed windows of this many paragraphs of the raw parsed text (`src/extraction/windows.py`), never the events' summaries. Changing it changes every windowed request (and so its cache key).
- `provenance_validation.gate` - read by `scripts/validate_provenance.py` (env `PROVENANCE_GATE` overrides without a rebuild).
- `enrichment.re_search_after_days` - `src/enrichment/enrichment_gate.py`.
- `dedup` - scoring weights and thresholds for the end-of-Phase-2 dedup report (`people`, `groups` blocks); every key is optional. `people.auto_merge_threshold: 999` means nothing auto-merges beyond byte-identical normalised names until you lower it. See the commented block in `config.yaml.example`.

---

## Maps Extraction

Extract maps from source documents during Phase 1 parsing.

```yaml
maps:
  enabled: true                    # Enable maps extraction
  extract_during_phase1: true      # Extract during document parsing
  download_images: true            # Download actual map image files
  storage_backend: "filesystem"    # filesystem or s3
  storage_path: "output/maps/"
  image_storage_path: "output/maps/files/"  # Where to store downloaded map images
```

**Options:**
- `enabled` - Enable/disable map extraction from source documents
- `extract_during_phase1` - Extract during initial document parsing
- `download_images` - Download map image files (not just metadata)
- `storage_backend` - Where to store images: `filesystem` or `s3`
- `storage_path` - Local path for map metadata
- `image_storage_path` - Local path for downloaded map images

**Note:** This extracts maps that are already in your source documents, not external searches. No code reads the `maps:` block (no `config["maps"]` lookup exists in `src/`, `phase*.py` or `ecs_entrypoint.py`); the keys are shipped defaults only. `maps.download_images` is one of the keys the cost-optimized profile sets (`config/profiles/cost-optimized.yaml`).

## Images

```yaml
images:
  enabled: true                    # Phase 2 image extraction (phase2_extract.py:597)
  download: true                   # fetch image bytes into output/ (phase3_enrich_data.py:675)
  image_storage_path: "output/images/files/"   # phase2_extract.py:605
```

Optional keys read with code defaults: `images.link_people` and `images.link_places` (both default true, `phase3_enrich_data.py:353,367`) and `images.vision_caption` (default false, `src/extraction/images.py:670`).

---

## External Maps Search

Search for historical maps using OpenSERP and verify with Grok vision API. The block shipped in `config.yaml.example` is small; the other keys below have code defaults and are read only if you add them.

```yaml
external_maps:
  enabled: false                   # paid OpenSERP web search + vision (needs OpenSERP running)
  recycle_in_doc_maps: true        # read what each in-document map depicts (Grok vision), recycle as a map-as-source document
  max_recycled_per_doc: 50         # cost cap per document (alerts when hit)
  # allowed_hosts: []              # extra hosts whose map links may be fetched (ibiblio.org is built in)
```

Keys read by code (all in `phase3_enrich_data.py` unless noted):

| Key | Code default | Use |
|---|---|---|
| `enabled` | false | external map search (`:334`) |
| `openserp_url` | `http://localhost:7001` | OpenSERP URL when `aws.enabled` is false (`src/utils/openserp_client.py:26`) |
| `max_places` | none (then `--max-items`) | limit places searched (`:343`) |
| `search_limit` | 50 | results per place (`:344`) |
| `page_download_timeout` | 10 | seconds for HTML pages (`:346`) |
| `image_download_timeout` | 30 | seconds for images (`:347`, `:630`) |
| `image_storage_path` | none | where map images are stored (`:348`) |
| `download` | true | download map images (`:641`) |
| `recycle_in_doc_maps` | false (shipped true) | in-document map recycling (`:609`); independent of `enabled` |
| `follow_in_doc_links` | true | also required for recycling (`:610`) |
| `max_recycled_per_doc` | 50 | cap per document (`:632`) |
| `allowed_hosts` | none | extra hosts for in-document map links (`:631`) |
| `storage_backend` | `filesystem` | YAML map import, `phase2_extract.py:642` |
| `allowed_licenses` | `["Public Domain", "CC0", "CC-BY", "CC-BY-SA"]` | licence filter, `src/extraction/external_maps.py:374` |

Previously documented keys `verify_with_vision`, `max_images_per_page`, `require_license`, `supported_formats`, `link_to_places`, `link_to_dates`, `s3_bucket`, `s3_prefix` and `s3_region` (under `external_maps`) are not read anywhere in the code and have been removed from this guide.

In-document maps (author-curated, linked from the narrative) are always persisted as Map entities; only the paid recycle step sits behind `recycle_in_doc_maps` (`phase3_enrich_data.py:_run_document_media_steps`).

**Increase the two timeouts if you see timeout errors for slow servers.**

### Prerequisites

**OpenSERP must be running** when `external_maps.enabled` is true:
```bash
cd openserp
./openserp serve -p 7001 &
```

**Domain blacklist configured:**
See `config/domain_blacklist.yaml` for filtering unwanted domains.

---

## OpenSERP (enrichment)

```yaml
openserp:
  results_per_query: 10
  max_images_per_entity: 3
  max_web_results_per_entity: 3
  rate_limit_seconds: 5            # spacing between serial OpenSERP queries
  request_timeout_seconds: 60      # per-query HTTP timeout
```

Read in `src/enrichment/openserp_enrichment.py` (`load_config().get("openserp")`, lines 362, 1391, 1486). `openserp.worker_visibility_seconds` (default 900) is read by `openserp_worker.py:309`. The profiles change these values (see Deploy Profiles).

---

## AWS Configuration

Settings for AWS deployment mode. Set `enabled: true` to use S3/DynamoDB instead of local filesystem.

```yaml
aws:
  enabled: false                     # Set true for AWS mode
  region: "us-east-1"
  s3_bucket: "dev-wwii-data-pipeline"
  s3_prefix: ""                      # Optional S3 key prefix
  cache_table: "dev-wwii-api-cache"  # DynamoDB table for API response cache
  cache_ttl_days: 90                 # Cache entry TTL
  secrets_id: "dev-wwii-pipeline/grok-api-key"  # Secrets Manager secret for GROK_API_KEY
  notification_email: ""             # Email for pipeline completion notifications
  openserp:
    cluster: "dev-wwii-pipeline"
    service: "dev-wwii-openserp"
    startup_timeout: 120
```

`config.yaml.example` ships only `enabled`, `region` and `notification_email` under `aws:`. The other keys are read with code defaults only if present (CloudFormation sets the matching env vars on ECS tasks; see below). `ecs_entrypoint.py:488-503` patches `aws.enabled: true`, `aws.s3_bucket` and `storage.entity_backend: filesystem` at runtime. The `health_check_url` and `database.backend/mongodb_uri` keys previously listed here are not read by any code; the health check is a fixed `GET {url}/health` (`src/utils/openserp_client.py:95`). `database.dynamodb_table_prefix` appears only in `lambda_handlers/import_handler.py`.

**Options:**
- `enabled` — When true, storage uses S3, cache uses DynamoDB, OpenSERP uses ECS Fargate
- `cache_table` — DynamoDB table for Grok API response caching (code default `wwii-api-cache`, `src/utils/backends.py:36`, `src/grok_client.py:382`)
- `cache_ttl_days` — How long cached responses are kept (default 90 days, `src/utils/backends.py:38`)
- `s3_prefix` — optional key prefix for the S3 backend (`src/utils/backends.py:20`)
- `secrets_id` — Secrets Manager secret name containing the Grok API key (`lambda_handlers/_handler_common.py:31`)
- `notification_email` — Email address for pipeline completion notifications. Read by `deploy_aws.py` and passed to CloudFormation. Leave empty to skip. Requires SNS email confirmation after first deploy.

**Note:** In ECS containers, `ecs_entrypoint.py` patches `aws.enabled: true` at runtime.

---

## Prompt Templates

Extraction prompts are loaded from YAML files in `prompts/` (34 files, listed in `PROMPT_REFERENCE.md`). Search query templates are in `search_queries/` (8 files). Missing files cause a hard build failure (validated by `deploy_all.sh`).

```
prompts/           # 34 LLM prompt templates
search_queries/    # 8 third-party search query templates
```

**Each prompt YAML contains:**
- `system_prompt` — System message for the LLM
- `prompt_template` — Main prompt with `{variable}` placeholders
- `schema` — JSON schema example for the expected output (optional for verification prompts)
- `rules` — List of extraction rules appended to the prompt

**Each search query YAML contains:** category → list of query template strings with `{variable}` placeholders.

**S3 override:** Upload to `s3://<bucket>/prompts/<name>.yaml` to override the local template at runtime. `src/utils/prompt_loader.py:_load_from_s3` checks S3 first, only when `S3_BUCKET` is set and `aws.enabled` is true, and falls back to the container's local copy. Any S3 error is swallowed and the local file is used. Loaded prompts are `lru_cache`d per process (`clear_cache()` resets).

**Cache invalidation:** Cache key hashes `system_prompt + prompt + temperature + model`. Any YAML change auto-invalidates relevant cache entries.

**Variables available in templates:**
- `{book}`, `{author}`, `{series}` — Book metadata
- `{event_name}`, `{event_id}` — Event context
- `{sub_event_summary}`, `{sub_event_id}` — Sub-event context
- `{text}` — Chapter/section text to extract from
- `{schema}` — Auto-injected from the `schema` field
- `{quote_rule}` — shared quote rule text from `src/utils/quote_match.QUOTE_RULE`, set by default

---

## Logging

Control log output and verbosity.

```yaml
logging:
  level: "INFO"
  file: "logs/pipeline.log"
  console: true
  debug_message_preview_chars: 500
  debug_response_preview_chars: 500
```

**Options:**
- `level` - Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR`
- `file` - Log file path
- `console` - Also print logs to console
- `debug_message_preview_chars` (default 500) - Characters to show in DEBUG logs for API requests
- `debug_response_preview_chars` (default 500) - Characters to show in DEBUG logs for API responses

**Command Line Override:**
```bash
python3 phase2_extract.py --log-level DEBUG
```

---

## Deploy Profiles

`scripts/deploy_all.sh` step 0 offers profiles (`DEPLOY_PROFILE=1..5` non-interactively). A
profile is an **overlay** in `config/profiles/<name>.yaml` holding only the settings it changes;
`scripts/apply_config_profile.py` deep-merges it onto `config.yaml`:

| Choice | Overlay | Intent |
|---|---|---|
| 1 | `balanced.yaml` | batch extraction, live enrichment |
| 2 | `cost-optimized.yaml` | no OpenSERP for supplemental, no map downloads |
| 3 | `performance-optimized.yaml` | live Phase 2, max concurrency |
| 4 | `review-all-data.yaml` | force reprocess everything (expensive) |
| 5 | — | keep `config.yaml` unchanged |

Profiles are **absolute over the managed keys** (the union of keys any profile sets): a managed key
the chosen profile does not set is reset to its `config.yaml.example` value, so switching profiles
never leaves another profile's settings behind. Local tuning of a managed key is overwritten by
choices 1–4 (listed in the printed diff; the backup keeps it) — use 5 to keep it. Unmanaged keys
are never touched.

The apply fails (deploy aborts, `config.yaml` untouched) on a missing overlay, an unknown choice,
or an overlay key that does not exist in `config.yaml`. On success it writes
`config.yaml.bak-<timestamp>` (gitignored — it contains API keys) and prints every changed value.
Re-serialization drops YAML comments from `config.yaml`; the backup keeps them.

## Common Configuration Scenarios

### Testing with Limited Data

```yaml
external_maps:
  max_places: 5                    # Test with just 5 places
  search_limit: 10                 # Fewer results per place
```

### Production Run

```yaml
external_maps:
  max_places: null                 # Search all places
  search_limit: 50                 # More comprehensive results
```

### Slow Network

```yaml
external_maps:
  image_download_timeout: 60       # Increase for slow connections
  page_download_timeout: 30
```

### Debug Mode

```yaml
logging:
  level: "DEBUG"
  debug_message_preview_chars: 15000
  debug_response_preview_chars: 15000
```

---

## Environment Variables

Required environment variables (set in `.env`):

```bash
GROK_API_KEY=your_api_key_here
```

Optional:
```bash
GROK_API_BASE_URL=https://api.x.ai/v1/chat/completions
GROK_MODEL=grok-4.3                # overrides api.grok.model (src/utils/grok_models.py)
```

### ECS Task Environment Variables (AWS mode)

Set by CloudFormation on ECS task definitions (`cloudformation/compute.yaml`) or by the entrypoint / trigger Lambda, as noted:

| Variable | Task | Description |
|----------|------|-------------|
| `AWS_DEFAULT_REGION` | All | AWS region |
| `S3_BUCKET` | All | S3 bucket for content and output |
| `CACHE_TABLE` | All | DynamoDB table for API cache, locks, and manifests |
| `SECRETS_ID` | Phase 2/3 | Secrets Manager secret name for Grok API key |
| `NOTIFICATION_TOPIC_ARN` | All | SNS topic for completion notifications |
| `CONTENT_TOPIC_ARN` | Phase 2 | SNS topic for content-uploaded events (used to re-trigger pipeline for pending content) |
| `DEDUP_REVIEW_URL` | Phase 2 | URL of the dedup review UI (included in notification emails) |
| `BOOK_NAME` | Phase 3 | Scopes S3 downloads to a specific book (set by trigger Lambda) |
| `SKIP_RETRY` | Retrieve task | Set by `ecs_entrypoint.py:4721` (not CloudFormation); `phase2_extract.py:868` then skips the empty-event retry pass |
| `ENV_NAME` | All | Environment name prefix (e.g., `dev`) |

### CloudFormation Timing Parameters

Configurable via CloudFormation stack parameters in `cloudformation/compute.yaml`. No code changes needed.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `BatchPollerIntervalMinutes` | 5 | How often batch poller checks xAI API |
| `ReconciliationIntervalMinutes` | 15 | Stale lock check / Phase 3 re-trigger interval |
| `NatWaitSeconds` | 270 | Seconds to wait for NAT gateway availability |
| `TeardownDelayMinutes` | 30 | Delay before tearing down networking after batch submit |

### DynamoDB Keys Used by Pipeline

| Key | Written by | Read by | Description |
|-----|-----------|---------|-------------|
| `lock#<family>` | Trigger Lambda | Trigger Lambda, entrypoint | Pipeline task locks (conditional put, 2h TTL) |
| `manifest#phase2` | Phase 2 final sync, dedup UI | Phase 2 download only (`_read_manifest`, legacy fallback after `pending#parsed#<book>`) | List of S3 keys changed by Phase 2 and dedup review. Phase 3 does not read it (see [LOCKS_AND_LEASES.md](LOCKS_AND_LEASES.md)) |
| `pending#content` | Trigger Lambda | Phase 2 post-process | Queued content keys when pipeline is busy |
| `pending#parsed#<book>` | Trigger Lambda | Phase 2 `_read_manifest` | Per-book parsed file queue (consumed on task start) |
| `pending#enrich#<book>` | Trigger Lambda | Phase 3 `_get_next_pending_enrich` | Per-book enrichment queue (consumed on completion) |
| `batch_job#<batch_id>` | Submit-only task | Batch poller Lambda | Batch job tracking (status: pending/ready/retrieved/failed, 30-day TTL) |
| `book_manifest#<book>#<entity_type>` | Phase 2 | Phase 3 | Entity files belonging to a specific book (scopes S3 downloads) |
| `name_exclusion#<type>#<name1>#<name2>` | Dedup UI, merge script | Dedup scripts | Name-based exclusion pairs (survives file recreation) |
| `metrics#<id>` | Phase 2/3 | Metrics API | Batch API metrics |

---

## Related Documentation

- [Domain Blacklist](../features/external-maps/domain-blacklist.md) - Configure URL filtering
- [Vision Verification](../features/external-maps/vision-verification.md) - How image verification works
- [External Maps](../features/external-maps/README.md) - External maps feature overview
- [Quality gate](../../../scripts/gate.sh) - Code quality tools (CI-equivalent; run before every commit)

---

## Configuration Validation

`load_config()` (`src/utils/config.py`) raises `ValueError` when:
- the file is not a YAML mapping, or the `paths` or `api` section is missing
- `api.grok.model` is not a string, or `api.calls_per_minute` is not an integer in 1-200
- `batch.phase2` / `batch.phase3` is not a boolean
- `concurrency.max_event_files`, `max_extraction_group` or `max_enrichment_workers` is not a positive integer

Nothing else is validated at load time. Paths are created by the code that writes to them. A missing OpenSERP service surfaces when the OpenSERP step runs. `GROK_API_KEY` is checked when the client is built.
