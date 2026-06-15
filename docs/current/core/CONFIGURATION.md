# Configuration Guide

**File:** `config.yaml`  
**Last Updated:** 2026-06-13

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
- `content_output` separates book-specific files (parsed/event) from entity directories. Backwards compatible: if `output/content/` doesn't exist, falls back to `output/` (old layout). Run `python3 scripts/migrate_output_content.py` to migrate.

---

## API Configuration

Settings for external API services.

```yaml
api:
  grok:
    base_url: "https://api.x.ai/v1/chat/completions"
    model: "grok-4.3"
    model_map:
      # isbn: "grok-3-mini"
      # url_verify: "grok-3-mini"
      # openserp_verify: "grok-3-mini"
    max_retries: 3
    timeout: 60
  calls_per_minute: 30            # Rate limit: max API calls per minute (across all threads)
```

**Options:**
- `base_url` — Grok API endpoint
- `model` — Default model for all tasks. Used when `model_map` has no entry for the task.
- `model_map` — Per-task model routing for cost optimization. Maps `cache_type` (task name) to a model. Uncommented entries use cheaper/faster models for simple tasks. Keys: `events`, `dates`, `places`, `people`, `peoplegroups`, `weather`, `equipment`, `logistics`, `casualties`, `supplemental`, `isbn`, `url_verify`, `copyright`, `nara_match`, `openserp_verify`. Unset keys use the default `model`.
- `max_retries` — Number of retry attempts on failure
- `timeout` — Request timeout in seconds
- `calls_per_minute` — Proactive rate limit across all threads (default 30). Irrelevant in batch mode.

**NARA Catalog API:**
- `nara_api_key` — API key for National Archives catalog search. Get from [archives.gov](https://www.archives.gov/research/catalog/help/api-getting-started). Used by bibliography resolver to find digitized military records and identify Record Groups. 10,000 requests/month limit. Leave empty to skip NARA search (Grok still identifies Record Groups without it).

**External Search Cache:**
All external search results (Wikipedia, Grokipedia, Archive.org, NARA, LOC, OpenSERP) are cached in DynamoDB (AWS) or local disk (local mode):
- Positive results: cached 30 days
- Negative results (not found): cached 7 days
- Prevents redundant HTTP calls across pipeline runs

**Environment Variables Required:**
- `GROK_API_KEY` - Your Grok API key (set in `.env`)

---

## Processing

General processing options.

```yaml
processing:
  validate_ulids: true
  generate_jq_queries: true
  generate_download_scripts: true
  force_reprocess: false             # WARNING: expensive — reprocesses all content regardless of cache
```

**Options:**
- `validate_ulids` - Verify ULID format for all IDs
- `generate_jq_queries` - Create jq query files for data exploration
- `generate_download_scripts` - Generate shell scripts for bulk downloads
- `force_reprocess` - When true, ignores existing output files and reprocesses everything. **Use with caution** — this bypasses all skip logic and will re-call the Grok API for all chapters.

---

## Batch Mode

Controls xAI Batch API usage for 50% cost reduction. When enabled, the ECS entrypoint auto-delegates to submit-only mode instead of running the phase inline.

```yaml
batch:
  phase2: true                       # Use batch API for Phase 2 extraction
  phase3: true                       # Use batch API for Phase 3 enrichment
```

**Options:**
- `phase2` - When true, Phase 2 auto-delegates to `run_submit_only` (submits batch, enqueues job, exits). The batch poller Lambda handles retrieval.
- `phase3` - When true, Phase 3 auto-delegates to `run_submit_only` (same flow as Phase 2).

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
- `cache_responses` - Cache API responses to avoid re-fetching
- `only_precise_dates` - Skip dates like "early June" (only process exact dates)
- `timeout` - API request timeout

**API:** [Open-Meteo Historical Weather API](https://open-meteo.com/)

---

## Equipment Extraction

Military equipment extraction from events (experimental).

```yaml
equipment:
  enabled: true                    # Enable equipment extraction (experimental)
  enable_enrichment: true          # Enrich with Wikipedia/Grokipedia data (slower, uses more API calls)
  verify_media_with_vision: true   # Verify media relevance with Grok vision API (recommended)
```

**Options:**
- `enabled` - Enable/disable equipment extraction
- `enable_enrichment` - Enrich equipment with external data (Wikipedia, Grokipedia)
- `verify_media_with_vision` - Use Grok vision API to verify downloaded media is relevant

**Output:** `output/equipment/{name}_{ulid}.json`

**Documentation:** See `docs/current/features/equipment/MILITARY_EQUIPMENT.md`

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
  enabled: true                    # Enable supplemental material extraction (Phase 1: Core)
  extract_citations: true          # Parse citations into structured format
  enrich_with_searches: true       # Phase 2: Search integration
  llm_search: true                 # Phase 2: Use LLM for search (first pass)
  search_gutenberg: true           # Phase 2: Search Gutenberg.org for books/periodicals
  search_archive_org: true         # Phase 2: Search Archive.org
  use_openserp: true               # Phase 2: Use OpenSERP for web search
  verify_archive_urls: true        # Phase 3: Verify archive URLs
  extract_isbn: true               # Phase 3: Extract ISBN for books (post-1966)
  determine_copyright: true        # Phase 3: Determine copyright status
  max_materials_per_chapter: 1000  # Limit materials per chapter
```

**Phase 1 (Core):**
- `extract_citations` - Parse footnotes/endnotes into structured citation format

**Phase 2 (Search):**
- `enrich_with_searches` - Master toggle for all search features
- `llm_search` - Use Grok to search for online versions of cited works
- `search_gutenberg` - Search Project Gutenberg
- `search_archive_org` - Search Internet Archive
- `use_openserp` - Use OpenSERP for broader web search (Phase 2: supplemental search, Phase 3: images, academic sources, event content)

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
  max_enrichment_workers: 6        # Phase 3: concurrent enrichment threads per entity type
```

**Options:**
- `enabled` - Enable/disable concurrent chapter processing
- `max_event_files` - Number of event files to process in parallel
- `max_extraction_group` - Max parallel extractions within a single group (dates, places, etc.)
- `max_enrichment_workers` - Phase 3 concurrent threads per entity type (default 6). Grok API calls are rate-limited; extra threads keep search requests (Grokipedia/Wikipedia) running in parallel.

**Note:** Concurrent processing is experimental. See `docs/current/FUTURE_ENHANCEMENTS.md` for distributed processing options.

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
  image_storage_path: "filestore/maps/"  # Where to store downloaded map images
```

**Options:**
- `enabled` - Enable/disable map extraction from source documents
- `extract_during_phase1` - Extract during initial document parsing
- `download_images` - Download map image files (not just metadata)
- `storage_backend` - Where to store images: `filesystem` or `s3`
- `storage_path` - Local path for map metadata
- `image_storage_path` - Local path for downloaded map images

**Note:** This extracts maps that are already in your source documents, not external searches.

---

## External Maps Search

Search for historical maps using OpenSERP and verify with Grok vision API.

```yaml
external_maps:
  enabled: true                    # Enable external maps search
  max_places: 50                   # Maximum places to search (null for all)
  openserp_url: "http://localhost:7001"  # OpenSERP service URL
  search_limit: 50                 # Max results per search
  verify_with_vision: true         # Use Grok vision API to verify maps
  max_images_per_page: 5           # Max images to analyze per page
  image_download_timeout: 30       # Timeout for downloading images (seconds)
  page_download_timeout: 15        # Timeout for downloading HTML pages (seconds)
```

### Core Settings

- `enabled` - Enable/disable external map searching
- `max_places` - Limit number of places to search (use `null` for all 220 places)
- `openserp_url` - URL of OpenSERP service (must be running)
- `search_limit` - Maximum search results per place

### Vision Verification

- `verify_with_vision` - Use Grok vision API to analyze actual map images
- `max_images_per_page` - How many images per page to send to Grok (1-5 recommended)

### Timeouts

- `image_download_timeout` - Seconds to wait for image downloads (default: 30)
- `page_download_timeout` - Seconds to wait for HTML page downloads (default: 10)

**Increase timeouts if you see timeout errors for slow servers.**

### Storage (Not Yet Implemented)

```yaml
  image_storage_path: "filestore/external_maps/"
  storage_path: "output/external_maps/"
  s3_bucket: ""                    # S3 bucket name (if using S3)
  s3_prefix: "maps/"               # S3 key prefix
  s3_region: "us-east-1"           # AWS region
```

### License Filtering

```yaml
  require_license: true            # Reject maps without license info
  allowed_licenses:
    - "Public Domain"
    - "CC0"
    - "CC-BY"
    - "CC-BY-SA"
    - "Unknown"
  download_images: false           # Download map images if permitted
  download_timeout: 30             # Image download timeout (seconds)
```

### Supported Formats

```yaml
  supported_formats:
    - jpg
    - png
    - tif
    - pdf
```

### Linking

```yaml
  link_to_places: true             # Link maps to PlaceIDs
  link_to_dates: true              # Link maps to DateIDs
```

### Prerequisites

**OpenSERP must be running:**
```bash
cd openserp
./openserp serve -p 7001 &
```

**Domain blacklist configured:**
See `domain_blacklist.yaml` for filtering unwanted domains.

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
    health_check_url: "/health"
    startup_timeout: 120
  database:
    backend: "dynamodb"              # "dynamodb" or "mongodb"
    dynamodb_table_prefix: "dev-wwii-"
    mongodb_uri: ""
```

**Options:**
- `enabled` — When true, storage uses S3, cache uses DynamoDB, OpenSERP uses ECS Fargate
- `cache_table` — DynamoDB table for Grok API response caching (avoids re-calling for same prompts)
- `cache_ttl_days` — How long cached responses are kept (default 90 days)
- `secrets_id` — Secrets Manager secret name containing the Grok API key
- `dynamodb_table_prefix` — Prefix for entity tables (people, places, groups, etc.)
- `notification_email` — Email address for pipeline completion notifications. Read by `deploy_aws.py` and passed to CloudFormation. Leave empty to skip. Requires SNS email confirmation after first deploy.

**Note:** In ECS containers, the `ecs_entrypoint.py` automatically patches `aws.enabled: true` at runtime.

---

## Prompt Templates

Extraction prompts are loaded from YAML files in `prompts/` (27 files). Search query templates are in `search_queries/` (6 files). Missing files cause a hard build failure (validated by `deploy_all.sh`).

```
prompts/           # 27 LLM prompt templates
search_queries/    # 6 third-party search query templates
```

**Each prompt YAML contains:**
- `system_prompt` — System message for the LLM
- `prompt_template` — Main prompt with `{variable}` placeholders
- `schema` — JSON schema example for the expected output (optional for verification prompts)
- `rules` — List of extraction rules appended to the prompt

**Each search query YAML contains:** category → list of query template strings with `{variable}` placeholders.

**S3 override:** Upload to `s3://<bucket>/prompts/<name>.yaml` to override the local template at runtime. The `prompt_loader.py` checks S3 first, falls back to the container's local copy.

**Cache invalidation:** Cache key hashes `system_prompt + prompt + temperature + model`. Any YAML change auto-invalidates relevant cache entries.

**Variables available in templates:**
- `{book}`, `{author}`, `{series}` — Book metadata
- `{event_name}`, `{event_id}` — Event context
- `{sub_event_summary}`, `{sub_event_id}` — Sub-event context
- `{text}` — Chapter/section text to extract from
- `{schema}` — Auto-injected from the `schema` field

---

## Logging

Control log output and verbosity.

```yaml
logging:
  level: "INFO"
  file: "logs/pipeline.log"
  console: true
  debug_message_preview_chars: 15000
  debug_response_preview_chars: 15000
```

**Options:**
- `level` - Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR`
- `file` - Log file path
- `console` - Also print logs to console
- `debug_message_preview_chars` - Characters to show in DEBUG logs for API requests
- `debug_response_preview_chars` - Characters to show in DEBUG logs for API responses

**Command Line Override:**
```bash
python3 phase2_extract.py --log-level DEBUG
```

---

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
GROK_MODEL=grok-beta
```

### ECS Task Environment Variables (AWS mode)

Set automatically by CloudFormation on ECS task definitions:

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
| `SKIP_RETRY` | Retrieve task | When true, skips retry logic (results already in cache) |
| `ENV_NAME` | All | Environment name prefix (e.g., `dev`) |

### CloudFormation Timing Parameters

Configurable via CloudFormation stack parameters in `cloudformation/compute.yaml`. No code changes needed.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `BatchPollerIntervalMinutes` | 5 | How often batch poller checks xAI API |
| `ReconciliationIntervalMinutes` | 15 | Stale lock check / Phase 3 re-trigger interval |
| `NatWaitSeconds` | 180 | Seconds to wait for NAT gateway availability |
| `TeardownDelayMinutes` | 30 | Delay before tearing down networking after batch submit |

### DynamoDB Keys Used by Pipeline

| Key | Written by | Read by | Description |
|-----|-----------|---------|-------------|
| `lock#<family>` | Trigger Lambda | Trigger Lambda, entrypoint | Pipeline task locks (conditional put, 2h TTL) |
| `manifest#phase2` | Phase 2 final sync, dedup UI | Phase 3 download | List of S3 keys changed by Phase 2 and dedup review |
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
- [Quality Assurance](../../../contextmanagement/Specs/quality_assurance.md) - Code quality tools

---

## Configuration Validation

The pipeline validates configuration on startup and will fail with clear error messages if:
- Required paths don't exist and can't be created
- OpenSERP is not running (when external_maps.enabled = true)
- Required environment variables are missing
- Invalid configuration values are provided

Check logs for validation errors before the pipeline starts processing.
