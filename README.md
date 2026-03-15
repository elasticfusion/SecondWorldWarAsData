# WWII Data Extraction Pipeline

Extract structured data from World War II historical documents using AI-powered entity extraction.

**Status:** Production Ready  
**Version:** 2.0  
**Last Updated:** 2026-03-15

---

## Quick Start

### Prerequisites

```bash
# Python 3.13+
python3 --version

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set API key (choose one method)

# Option 1: .env file (recommended — auto-loaded by pipeline)
cp .env.example .env
# Then edit .env with your key

# Option 2: Environment variable
export GROK_API_KEY="your-api-key"  # Linux/macOS (current session)
```

**`.env` file format** (see `.env.example`):
```bash
# Required
GROK_API_KEY=xai-a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8s9t0u1v2w3x4y5z6

# Optional (defaults shown)
GROK_API_BASE_URL=https://api.x.ai/v1/chat/completions
GROK_MODEL=grok-beta
```

**Chrome/Chromium Required** (for PDF conversion and web scraping):

```bash
# macOS
brew install --cask google-chrome

# Ubuntu/Debian (headless)
wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | sudo apt-key add -
sudo sh -c 'echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list'
sudo apt-get update
sudo apt-get install -y google-chrome-stable

# RHEL/CentOS/Fedora (headless)
sudo dnf install -y google-chrome-stable

# Verify installation
google-chrome --version
```

### Run Pipeline

```bash
# 1. Parse markdown to JSON
python3 phase1_parse.py

# 2. Extract entities (with automatic retry)
python3 phase2_retry.py

# 3. Enrich people data (optional)
python3 phase3_retry.py

# 4. Import to MongoDB (optional)
python3 import_to_mongodb.py
```

**That's it!** Your data is now in `output/`

---

## What It Does

Extracts structured data from WWII historical documents:

- **Events** - Battles, operations, actions
- **Dates** - Temporal mentions with precision
- **Places** - Geographic locations with GPS coordinates
- **People** - Biographical profiles with enrichment
- **Military Units** - Organizations and hierarchies
- **Equipment** - Weapons, vehicles, specifications
- **Weather** - Historical weather data
- **Logistics** - Supply chain information
- **Casualties** - Casualty tracking
- **Maps** - Source and external maps
- **Citations** - Bibliography and references

---

## Project Structure

```
SecondWorldWarAsData/
├── README.md (this file)
├── config.yaml                    # Configuration
├── phase1_parse.py                # Parse markdown → JSON
├── phase2_extract.py              # Extract entities
├── phase2_retry.py                # Retry wrapper
├── phase3_enrich_data.py          # Enrich with external data
├── phase3_retry.py                # Retry wrapper
├── import_to_mongodb.py           # Import to database
├── contentrepository/             # Source documents (markdown)
├── output/                        # Extracted data (JSON)
│   ├── {BookName}/                # Per-book: *-parsed.json, *-event.json
│   ├── dates/                     # Flat: per-date JSON files
│   ├── places/                    # Flat: per-place JSON files
│   ├── people/                    # Flat: per-person JSON files (after extraction)
│   └── people_groups/             # Flat: per-group JSON files
├── cache/                         # API response cache
├── logs/                          # Pipeline logs
├── src/                           # Source code
│   ├── extraction/                # Extraction modules
│   ├── utils/                     # Utilities
│   └── ...
├── scripts/                       # Utility scripts
├── tools/                         # Go tools (search)
└── docs/                          # Documentation
    └── current/                   # Current docs
        ├── core/                  # Core documentation
        ├── features/              # Feature docs
        └── pipeline/              # Pipeline docs
```

---

## Documentation

### Getting Started
- **[Pipeline Overview](docs/current/core/PIPELINE.md)** - Complete pipeline workflow
- **[Workflow Diagrams](docs/current/core/WORKFLOW_DIAGRAMS.md)** - Visual workflow diagrams
- **[Configuration](docs/current/core/CONFIGURATION.md)** - Config options
- **[Development Guide](docs/current/core/DEVELOPMENT.md)** - Setup and development

### Core Features
- **[Events Extraction](docs/current/features/events/README.md)** - Hierarchical events
- **[Dates Extraction](docs/current/features/dates/README.md)** - Temporal entities
- **[Places Extraction](docs/current/features/places/README.md)** - Geographic entities
- **[People Extraction](docs/current/features/people/README.md)** - Biographical profiles
- **[People Groups](docs/current/features/people/groups.md)** - Military units

### Optional Features
- **[Weather Extraction](docs/current/features/weather/README.md)** - Historical weather
- **[Logistics Extraction](docs/current/features/logistics/README.md)** - Supply chain
- **[Equipment Extraction](docs/current/features/equipment/MILITARY_EQUIPMENT.md)** - Military equipment
- **[Maps](docs/current/features/maps/README.md)** - Source maps
- **[External Maps](docs/current/features/external-maps/README.md)** - Third-party maps
- **[Supplemental Materials](docs/current/features/supplemental/SUPPLEMENTAL_COMPLETE.md)** - Bibliography

### Performance
- **[Batch Processing](docs/current/features/batch_processing/README.md)** - Parallel extraction

### Reference
- **[API Reference](docs/current/core/API_REFERENCE.md)** - API documentation
- **[Error Handling](docs/current/core/error_handling.md)** - Error handling guide
- **[Scripts Reference](scripts/README.md)** - Utility scripts
- **[Tools Reference](tools/README.md)** - Go tools

### Complete Index
- **[Documentation Index](docs/current/INDEX.md)** - Complete documentation index
- **[Feature Index](docs/current/features/README.md)** - All features

---

## Configuration

Edit `config.yaml` to enable/disable features:

```yaml
# Enable optional features
weather:
  enabled: true

equipment:
  enabled: true

logistics:
  enabled: true

casualties:
  enabled: true

# Configure parallel processing
concurrency:
  max_event_files: 3              # Max chapters processed in parallel
```

See [Configuration Guide](docs/current/core/CONFIGURATION.md) for all options.

---

## Common Tasks

### Add New Content

```bash
# From HyperWar HTML (ibiblio.org)
python3 scripts/import_hyperwar_html.py https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/index.html

# From PDF
python3 scripts/pdf_to_markdown.py book.pdf "BookName"

# Then generate metadata and run pipeline
python3 scripts/generate_missing_metadata.py
python3 scripts/complete_metadata_with_grok.py
python3 phase1_parse.py
python3 phase2_retry.py
```

See [Adding Data Sources](docs/current/pipeline/ADDING_DATA_SOURCES.md) | [HyperWar HTML Import](docs/current/pipeline/HYPERWAR_HTML_IMPORT.md)

### Find and Merge Duplicates

```bash
# Find duplicates
python3 scripts/find_duplicate_people.py
python3 scripts/find_duplicate_places.py
python3 scripts/find_related_groups.py

# Merge interactively
python3 scripts/merge_duplicate_people.py
python3 scripts/merge_duplicate_places.py
python3 scripts/merge_related_groups.py
```

See [People Deduplication](docs/current/features/people/deduplication.md)

### Validate Data

```bash
# Validate all data
python3 scripts/validate_data.py

# Generate report
python3 scripts/validation_report.py

# Open dashboard
open validation_dashboard.html
```

### Clear Cache

```bash
# Clear specific chapter's cache entries (recommended)
python3 -c "from diskcache import Cache; from pathlib import Path; \
[Cache(str(d)).pop(k, None) for d in Path('cache/api').iterdir() if d.is_dir() \
for k in list(Cache(str(d))) if 'chapter8c' in str(Cache(str(d)).get(k, ''))]"

# Clear specific cache type
rm -rf cache/api/events/*

# Clear all caches (use sparingly)
rm -rf cache/api/*
```

---

## Output Format

All data is JSON with ULIDs for cross-referencing:

```json
{
  "EventID": "01KHXNSE0W41DV7VV6PEMDJJ5H",
  "Event_Name": "Operation Overlord",
  "Sub-events": [
    {
      "Sub-eventID": "01KHXNSE0WX99GG0CB53CD2242",
      "Sub-event_summary": "D-Day landings at Normandy",
      "dates": ["01KHYP2M4N6P8Q0R2S4T6V8W0X"],
      "places": ["01KHYP2N5P7Q9R1S3T5V7W9X1Z"],
      "people": ["01KHYP2P6Q8R0S2T4V6W8X0Y2Z"]
    }
  ]
}
```

See [Schema Reference](docs/current/SCHEMA_REFERENCE.md)

---

## Performance

**Typical Processing Times:**
- Phase 1 (Parse): ~1 second per chapter
- Phase 2 (Extract): ~30-60 seconds per chapter
- Phase 3 (Enrich): ~5-10 seconds per person

**Optimization:**
- All API responses cached
- Parallel chapter processing with batched API calls (3-5x faster)
- Corrupted cache entries auto-cleared on retry

See [Performance Guide](docs/current/features/batch_processing/README.md)

---

## Troubleshooting

### No events extracted

```bash
# Check logs
tail -100 logs/pipeline*.log

# Retry (corrupted cache entries are auto-cleared)
python3 phase2_retry.py
```

### JSON parsing errors

**Corrupted cache entries are auto-cleared.** Just retry:
```bash
python3 phase2_retry.py
```

If the error persists, use the cache clearing command from the log output.

### API errors

```bash
# Check API key
echo $GROK_API_KEY

# Test API
curl -H "Authorization: Bearer $GROK_API_KEY" https://api.x.ai/v1/chat/completions
```

See [Error Handling Guide](docs/current/core/error_handling.md)

---

## Architecture

**Phase 1: Parse**
- Markdown → JSON
- Absolute paragraph numbering
- Metadata extraction
- Auto-splitting for large chapters

**Phase 2: Extract**
- Events → Dates, Places, People, Groups (parallel, batched)
- Retry missing events (per-chapter cache clear)
- Optional: Weather, Equipment, Logistics, Casualties, Supplemental (sequential per event)
- Optional: Source maps, External maps
- Analysis: Duplicate people, Related groups

**Phase 3: Enrich**
- Wikipedia/Grokipedia biographical data
- Grok AI structured extraction (birth/death, ranks, awards, units)
- Reference following for additional context
- Pydantic validation before save

See [Architecture Guide](docs/current/core/CODE_ARCHITECTURE.md)

---

## API

**Grok API:**
- Model: `grok-beta`
- Max output tokens: 131,072
- Temperature: 0.1 (deterministic)
- Caching: All responses cached via diskcache

**Open-Meteo API (Weather):**
- Free tier, no API key
- Historical data 1940-present

**OpenSERP (Maps/Media Search):**
- Local Go service — uses real search engines (Google, Bing, DuckDuckGo) instead of AI search
- Required for: external maps, equipment media, supplemental material searches
- Falls back gracefully if not running (Grok search used instead, but may hallucinate)

Setup:
```bash
# Option 1: Quick setup (clones, builds, starts)
cd tools && bash setup_openserp.sh

# Option 2: Manual — requires Go 1.24+
cd openserp
go build -o openserp .
cd ..
```

Start/Stop:
```bash
# Start (background, port 7001 to match config.yaml)
cd openserp && ./openserp serve -p 7001 &
echo $! > ../.openserp.pid
cd ..

# Verify
curl -s "http://localhost:7001/mega/search?text=test&limit=1" | head -c 200

# Stop
kill $(cat .openserp.pid)
```

Docker alternative:
```bash
cd openserp
docker compose up -d    # Runs on port 7001
```

See [OpenSERP Integration](docs/current/features/external-maps/openserp-integration.md) for full details.

---

## Testing

```bash
# Run tests
pytest

# With coverage
pytest --cov=src --cov-report=html

# Open coverage report
open htmlcov/index.html
```

See [Testing Guide](docs/current/core/TESTING.md)

---

## Contributing

1. Read [Development Guide](docs/current/core/DEVELOPMENT.md)
2. Check [Documentation Standards](docs/current/features/DOCUMENTATION_STATUS.md)
3. Run tests before committing
4. Update documentation for new features

---

## Data Sources

**Currently Supported:**
- US Army in World War II series (Green Books)
- Public domain historical documents
- Markdown format

**Adding New Sources:**
- See [Adding Data Sources](docs/current/pipeline/ADDING_DATA_SOURCES.md)
- See [HyperWar HTML Import](docs/current/pipeline/HYPERWAR_HTML_IMPORT.md)
- See [PDF Conversion](docs/current/pipeline/PDF_CONVERSION.md)
- See [Papers and Articles](docs/current/pipeline/PAPERS_AND_ARTICLES.md)

---

## License

Public Domain (US Government works)

See individual source documents for specific licenses.

---

## Support

**Documentation:**
- [Complete Documentation Index](docs/current/INDEX.md)
- [Feature Documentation](docs/current/features/README.md)
- [Pipeline Documentation](docs/current/core/PIPELINE.md)

**Issues:**
- Check [Error Handling Guide](docs/current/core/error_handling.md)
- Check [Troubleshooting](docs/current/core/PIPELINE.md#troubleshooting)
- Review logs in `logs/`

---

## Project Status

**Production Ready:**
- ✅ Events, Dates, Places, People, Groups
- ✅ Maps, Supplemental Materials
- ✅ Deduplication, Validation
- ✅ MongoDB Import

**Experimental:**
- ⚠️ Weather, Equipment, Logistics, Casualties

**Comprehensive Documentation:**
- ✅ All features documented
- ✅ Complete API reference
- ✅ Troubleshooting guides

---

## Quick Links

- [Pipeline Overview](docs/current/core/PIPELINE.md)
- [Configuration](docs/current/core/CONFIGURATION.md)
- [Feature Index](docs/current/features/README.md)
- [Scripts Reference](scripts/README.md)
- [Error Handling](docs/current/core/error_handling.md)
- [Complete Documentation](docs/current/INDEX.md)

---

**Get Started:** `python3 phase1_parse.py && python3 phase2_retry.py`
