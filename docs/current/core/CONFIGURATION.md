# Configuration Guide

**File:** `config.yaml`  
**Last Updated:** 2026-02-26

Complete reference for all configuration options in the WWII data extraction pipeline.

---

## Paths

Directory structure for the project.

```yaml
paths:
  content_root: "contentrepository"  # Source documents
  output_root: "output"              # Extracted data
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

---

## API Configuration

Settings for external API services.

```yaml
api:
  grok:
    base_url: "https://api.x.ai/v1/chat/completions"
    model: "grok-beta"
    max_retries: 3
    timeout: 60
  calls_per_minute: 30            # Rate limit: max API calls per minute (across all threads)
```

**Options:**
- `base_url` - Grok API endpoint
- `model` - Model to use (`grok-beta`, `grok-2`, etc.)
- `max_retries` - Number of retry attempts on failure
- `timeout` - Request timeout in seconds
- `calls_per_minute` - Proactive rate limit across all threads (default 30). Increase if API allows more; decrease if still seeing 429s.

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
```

**Options:**
- `validate_ulids` - Verify ULID format for all IDs
- `generate_jq_queries` - Create jq query files for data exploration
- `generate_download_scripts` - Generate shell scripts for bulk downloads

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
  enabled: false                   # Enable equipment extraction (experimental)
```

**Options:**
- `enabled` - Enable/disable equipment extraction

**Output:** `output/equipment/{name}_{ulid}.json`

**Documentation:** See `docs/current/features/MILITARY_EQUIPMENT.md`

---

## Maps Extraction

Extract maps from source documents during Phase 1 parsing.

```yaml
maps:
  enabled: true                    # Enable maps extraction
  extract_during_phase1: true      # Extract during document parsing
  download_images: false           # Download actual map image files
  storage_backend: "filesystem"    # filesystem or s3
  storage_path: "output/maps/"
```

**Options:**
- `enabled` - Enable/disable map extraction from source documents
- `extract_during_phase1` - Extract during initial document parsing
- `download_images` - Download map image files (not just metadata)
- `storage_backend` - Where to store images: `filesystem` or `s3`
- `storage_path` - Local path for map storage

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
  max_images_per_page: 3           # Max images to analyze per page
  image_download_timeout: 30       # Timeout for downloading images (seconds)
  page_download_timeout: 10        # Timeout for downloading HTML pages (seconds)
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
  image_storage_path: "output/maps_images/"
  s3_bucket: ""                    # S3 bucket name (if using S3)
  s3_prefix: "maps/"               # S3 key prefix
  s3_region: "us-east-1"           # AWS region
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
