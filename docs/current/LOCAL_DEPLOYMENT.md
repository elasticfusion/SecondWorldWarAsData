# Local Deployment Guide

Run the WWII Data Extraction Pipeline on your local machine with filesystem storage.

**Last Updated:** 2026-04-19

---

## Prerequisites

- **Python 3.12+**
- **Grok API key** from [x.ai](https://x.ai)
- **Chrome/Chromium** (for PDF conversion and OpenSERP web scraping)
- **Go 1.21+** (optional — for building OpenSERP search tool)
- **MongoDB** (optional — for database import)

### Install Chrome

```bash
# macOS
brew install --cask google-chrome

# Ubuntu/Debian
sudo apt-get install -y google-chrome-stable

# Amazon Linux / Fedora
sudo dnf install -y google-chrome-stable
```

### Install Go (optional — for OpenSERP)

```bash
# macOS
brew install go

# Ubuntu/Debian
sudo apt-get install -y golang-go

# Verify (requires 1.21+)
go version
```

---

## Setup

```bash
# Clone and enter project
cd SecondWorldWarAsData

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install project in editable mode
pip install -e .

# Configure API key
cp .env.example .env
# Edit .env with your GROK_API_KEY
```

### Configuration

Edit `config.yaml` to enable/disable features. See [Configuration Guide](core/CONFIGURATION.md) for all options.

### Prompt Templates

Extraction prompts are in `prompts/*.yaml`. Edit these to customize what Grok extracts:

```bash
# Edit the people extraction prompt
vim prompts/people.yaml

# Changes take effect on next Phase 2 run (no rebuild needed locally)
```

See [Configuration Guide — Prompt Templates](core/CONFIGURATION.md#prompt-templates) for details.

### Feature Toggles

```yaml
equipment:
  enabled: true       # Military equipment extraction

weather:
  enabled: true       # Historical weather data

logistics:
  enabled: true       # Supply chain tracking

casualties:
  enabled: true       # Casualty tracking

aws:
  enabled: false      # Keep false for local mode
```

See [Configuration Guide](core/CONFIGURATION.md) for all options.

---

## Run Pipeline

```bash
# 1. Parse markdown to JSON
python3 phase1_parse.py

# 2. Start OpenSERP (optional — for external maps and equipment media)
cd tools && bash setup_openserp.sh && cd ..

# 3. Extract entities (with automatic retry)
python3 phase2_retry.py

# 3b. Or use Batch API for 50% cost reduction (async)
python3 phase2_extract.py --batch

# 4. Enrich people/places/groups (optional)
python3 phase3_retry.py

# 5. Import to database (optional)
python3 import_to_mongodb.py     # MongoDB
python3 import_to_dynamodb.py    # DynamoDB
```

Output is in `output/` as JSON files.

---

## Add New Content

```bash
# From HyperWar HTML
python3 scripts/import_hyperwar_html.py <index_url>

# From PDF
python3 scripts/pdf_to_markdown.py book.pdf "BookName"

# Generate metadata and run pipeline
python3 scripts/generate_missing_metadata.py
python3 scripts/complete_metadata_with_grok.py
python3 phase1_parse.py
python3 phase2_retry.py
```

See [Adding Data Sources](pipeline/ADDING_DATA_SOURCES.md) | [HyperWar Import](pipeline/HYPERWAR_HTML_IMPORT.md) | [PDF Conversion](pipeline/PDF_CONVERSION.md)

---

## Post-Extraction

```bash
# Find and merge duplicates
python3 scripts/find_duplicate_people.py
python3 scripts/merge_duplicate_people.py

# Fix orphaned references
python3 scripts/fix_orphaned_person_refs.py --dry-run

# Validate data
python3 scripts/validate_output.py

# Clean up cache for completed books
python3 scripts/cleanup_book_cache.py --all --dry-run
```

See [Scripts Reference](../../scripts/README.md)

---

## OpenSERP

OpenSERP is a Go HTTP server that searches Google/Bing/DuckDuckGo for historical maps and media. It's optional but recommended.

```bash
# Build and start
cd tools && bash setup_openserp.sh && cd ..

# Or manually
cd openserp && ./openserp serve -p 7001 &

# Verify
curl -s "http://localhost:7001/mega/search?text=test&limit=1" | head -c 200

# Docker alternative
cd openserp && docker compose up -d
```

If OpenSERP is not running, the pipeline falls back to Grok-based search (less accurate).

See [OpenSERP Integration](features/external-maps/openserp-integration.md)

---

## Troubleshooting

### No events extracted
```bash
tail -100 logs/pipeline*.log
python3 phase2_retry.py    # Corrupted cache auto-cleared on retry
```

### Pipeline appears hung
Both Phase 2 and Phase 3 log a warning if no progress for 5 minutes:
```
WARNING - Phase 2: no progress for 5 minutes. Last: chapter8c-event.json
```

### API errors
```bash
echo $GROK_API_KEY
curl -H "Authorization: Bearer $GROK_API_KEY" https://api.x.ai/v1/chat/completions
```

### Clear cache
```bash
# One book's event cache
rm -rf cache/api/books/BookName/events/*

# All caches
rm -rf cache/api/*
```

See [Error Handling Guide](core/error_handling.md)

---

## Testing

```bash
# Run tests
scripts/run_tests.sh

# With coverage
scripts/run_tests.sh coverage
```

See [Testing Guide](core/TESTING.md)

---

## Performance

| Phase | Speed | Notes |
|-------|-------|-------|
| Parse | ~1s/chapter | CPU-bound, no API calls |
| Extract | ~30-60s/chapter | API-bound, parallel processing |
| Enrich | ~5-10s/entity | API-bound, per-entity |

Use `--batch` flag for 50% cost reduction via xAI Batch API (async processing).

See [Batch Processing](features/batch_processing/README.md)
