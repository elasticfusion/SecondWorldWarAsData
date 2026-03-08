# WWII Historical Data Extraction Pipeline

Automated extraction and structuring of WWII historical data from US Army official histories using AI-powered analysis.

## Quick Start

```bash
# 1. Parse markdown content
python3 phase1_parse.py

# 2. Extract entities and events (with automatic retry)
python3 phase2_retry.py

# Or run phase2 directly (single pass)
python3 phase2_extract.py

# 3. Merge duplicate people
python3 scripts/merge_duplicate_people.py

# 4. Merge duplicate places
python3 scripts/merge_duplicate_places.py

# 5. Merge related groups
python3 scripts/merge_related_groups.py
```

**Working with PDFs?** See [PDF_CONVERSION.md](docs/current/PDF_CONVERSION.md) for converting PDFs to markdown.

## Data Standards

**ISO Country Codes:** All nationality and country_of_origin fields use ISO 3166-1 alpha-3 codes (USA, GBR, DEU, etc.). See [ISO_COUNTRY_CODES.md](docs/current/ISO_COUNTRY_CODES.md).

**JSON Repair:** Automatic repair of common API response errors. See [JSON_REPAIR.md](docs/current/JSON_REPAIR.md).

## Optional: OpenSERP for Better Map Search

**Recommended for external maps:** OpenSERP uses real search engines (Google, Bing, DuckDuckGo) instead of AI search, eliminating hallucinations.

### Install OpenSERP

```bash
# Run setup script (installs Go if needed, clones and builds OpenSERP)
./setup_openserp.sh

# Or manually:
brew install go  # macOS
git clone https://github.com/karust/openserp.git
cd openserp && go build -o openserp . && cd ..
go build -o search_maps search_maps.go
```

### Start OpenSERP

```bash
cd openserp
./openserp serve -p 7001 &
cd ..
```

### Usage

Phase 2 automatically detects and uses OpenSERP if available:

```bash
python3 phase2_extract.py
# Will use OpenSERP if running, otherwise falls back to Grok search
```

**Benefits:**
- ✅ Real search results (no hallucinations)
- ✅ Finds maps from Wikipedia, military history sites, archives
- ✅ Multi-engine search (Google + Bing + DuckDuckGo)
- ✅ Free (no search API keys needed)

**Without OpenSERP:** Falls back to Grok search (may produce some hallucinations, but verification catches most)

## Optional: Biographical Enrichment

Enrich people profiles with external data:

```bash
# Enable in config.yaml (optional)
enrichment:
  enabled: true

# Run Phase 3 (with automatic retry)
python3 phase3_retry.py

# Or run directly (single pass)
python3 phase3_enrich_data.py

# Limit enrichment for testing
python3 phase3_retry.py --max-items 5
```

**Features:**
- Wikipedia/Grokipedia biographical data
- Birth/death dates
- Nationalities
- Biographical summaries

**Note:** Phase 3 is optional. The pipeline is fully functional without it.

## Optional: Equipment Extraction

Extract military equipment mentions from events with media integration:

```bash
# Enable in config.yaml
equipment:
  enabled: true
  enable_enrichment: true          # Wikipedia/Grokipedia data
  verify_media_with_vision: true   # Verify images with Grok vision API

# Run Phase 2
python3 phase2_extract.py
```

**Features:**
- Equipment mentions linked to events
- External data enrichment (Wikipedia)
- Media extraction with vision verification
- Image deduplication (perceptual hashing)
- Temporal filtering (uses event dates)
- Domain blacklist compliance

**Output:** 
- Equipment files: `output/equipment/{name}_{ulid}.json`
- Media files: `filestore/equipment/{ulid}/{ulid}.{ext}`
- Automatic duplicate image removal

See `docs/current/features/equipment/EQUIPMENT_IMPLEMENTATION_SUMMARY.md` for details.

## Project Structure

```
SecondWorkldWarasData/
├── phase1_parse.py              # Parse markdown → JSON
├── phase2_extract.py            # Extract events, people, places
├── scripts/                     # Utility scripts
│   ├── find_duplicate_people.py
│   ├── merge_duplicate_people.py
│   ├── find_related_groups.py
│   ├── suggest_group_aliases.py
│   ├── consolidate_people_groups.py
│   ├── complete_metadata_with_grok.py
│   ├── generate_missing_metadata.py
│   ├── standardize_metadata.py
│   ├── extract_url.py
│   ├── review_cache.py
│   └── validate_places.py
├── tests/                       # Test scripts
├── src/
│   ├── extraction/              # Extraction modules
│   │   ├── events.py
│   │   ├── dates.py
│   │   ├── places.py
│   │   ├── people.py
│   │   ├── people_groups.py
│   │   ├── weather_central.py
│   │   ├── equipment.py
│   │   └── maps.py
│   ├── grok_client.py          # Grok API client
│   └── models.py               # Data models
├── contentrepository/          # Source markdown files
├── output/                     # Extracted JSON data
│   ├── {Book}/
│   │   ├── chapter*-parsed.json
│   │   ├── chapter*-event.json
│   │   ├── chapter*-dates.json
│   │   ├── chapter*-places.json
│   ├── dates/                  # Central dates repository
│   ├── places/                 # Central places repository
│   ├── weather/                # Central weather repository
│   ├── maps/                   # Maps from source material
│   ├── equipment/              # Military equipment (optional)
│   ├── people/                 # Individual person files
│   └── people_groups/          # Organizations, units
└── cache/
    ├── api/                    # Grok API response cache
    └── maps/                   # Downloaded map images
```

## Features

### Phase 1: Parsing
- Discovers books and chapters
- Parses markdown with absolute paragraph numbering
- Extracts inline entities (images, maps, footnotes)
- Reads metadata from YAML files

### Phase 2: Extraction
- **Events**: Hierarchical event/sub-event structure
- **Dates**: Temporal entities with context (central repository)
- **Places**: Geographic entities with coordinates (central repository)
- **Weather**: Weather conditions with API integration (central repository)
- **Maps**: Maps and diagrams from source material
- **People**: Biographical profiles with event mentions
- **People Groups**: Organizations, military units, alliances

### Advanced Features
- **Central Repositories**: Deduplicated dates, places, weather across books
- **Weather API**: Open-Meteo Historical Archive integration
- **Map Storage**: Filesystem or S3 backend support
- **Duplicate Detection**: Finds similar people/groups
- **Alias Management**: Normalizes group names
- **Cross-Book Tracking**: Links entities across multiple books
- **Metadata Completion**: AI-powered metadata extraction
- **Quality Assurance**: Pylint, mypy, bandit, radon

## Documentation

See `docs/current/` for detailed documentation:
- `PIPELINE.md` - Complete pipeline documentation
- `PEOPLE_MANAGEMENT.md` - People extraction and deduplication
- `PEOPLE_GROUPS.md` - Group extraction and consolidation
- `METADATA.md` - Metadata management
- `MAPS.md` - Maps extraction from source material
- `S3_STORAGE.md` - S3 storage configuration

See `contextmanagement/Specs/` for technical specifications:
- `dates.md` - Central dates repository
- `places.md` - Central places repository
- `weather.md` - Weather extraction with API integration
- `maps.md` - Maps extraction specification
- `quality_assurance.md` - QA tools and standards

## Configuration

Edit `config.yaml` to customize extraction:

```yaml
# Weather extraction
weather:
  enabled: true
  fetch_api_data: true          # Open-Meteo Historical Archive
  only_precise_dates: true

# Maps extraction
maps:
  enabled: false
  download_images: false
  storage_backend: "filesystem"  # or "s3"
  s3_bucket: ""                  # Required for S3 backend
  s3_region: "us-east-1"

# External maps (third-party sources)
external_maps:
  enabled: true
  use_openserp: true             # Use real search engines (recommended)
  openserp_url: "http://localhost:7001"
  max_places: 5                  # Limit for testing, set to null for all
```

### OpenSERP Configuration

**Recommended:** OpenSERP eliminates AI hallucinations by using real search engines.

**Setup:**
```bash
./setup_openserp.sh              # One-command setup
cd openserp && ./openserp serve -p 7001 &  # Start server
```

**How it works:**
1. OpenSERP searches Google/Bing/DuckDuckGo for maps
2. Go tool filters for reputable sources
3. Python downloads actual page content
4. Grok verifies content matches expectations
5. Only verified maps are imported

**Without OpenSERP:** Falls back to Grok search (less reliable, may hallucinate)

### S3 Storage (Optional)

For S3 backend, configure AWS credentials:

```bash
# Option 1: Environment variables
export AWS_ACCESS_KEY_ID="your-key"
export AWS_SECRET_ACCESS_KEY="your-secret"

# Option 2: AWS credentials file
aws configure
```

Then enable in config:
```yaml
maps:
  enabled: true
  storage_backend: "s3"
  s3_bucket: "your-bucket-name"
  s3_prefix: "maps/"
```

## Data Sources

- **Breakout and Pursuit** (Martin Blumenson, 1961)
- **Cross-Channel Attack** (Gordon A. Harrison, 1951)
- Series: United States Army in World War II
- License: Public Domain

## Requirements

```bash
pip install -r requirements.txt
```

**Core Dependencies:**
- Python 3.13+
- Grok API key (set in environment or config.yaml)
- ~2GB disk space for cache

**Optional (Recommended for External Maps):**
- Go 1.21+ (for OpenSERP integration)
- OpenSERP (eliminates map search hallucinations)

**Optional (S3 Storage):**
- AWS credentials (for S3 storage backend)
- boto3 (included in requirements.txt)

## Output Format

All data is structured JSON with ULIDs for cross-referencing:

```json
{
  "PersonID": "01...",
  "name": "Dwight D. Eisenhower",
  "event_mentions": [
    {
      "EventID": "01...",
      "Sub-eventID": "01...",
      "position_at_event": "Supreme Commander"
    }
  ]
}
```

## License

Code: MIT  
Historical Content: Public Domain (US Government works)
