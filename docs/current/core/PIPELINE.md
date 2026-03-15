# Pipeline Documentation

## Overview

The extraction pipeline consists of two main phases:

### Phase 1: Parsing
Converts markdown source files into structured JSON with absolute paragraph numbering.

### Phase 2: Extraction
Extracts entities and events using Grok AI.

## Phase 1: Parse

```bash
python3 phase1_parse.py
```

**Input:** `contentrepository/{Book}/chapter*/chapter*-content.md`  
**Output:** `output/{Book}/chapter*-parsed.json`

**Features:**
- Absolute paragraph numbering across entire book
- Inline entity extraction (images, maps, footnotes, page markers)
- Metadata from YAML files
- Preserves source structure
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
```

**Pipeline Stages:**
1. **Metadata Completion** - Auto-fills missing chapter titles/numbers
2. **Parallel Core Extraction** - Processes all chapters concurrently (max 3):
   - Event extraction (if event file doesn't exist)
   - Dates, Places, People Groups, People (batched API calls, parallel per chapter)
3. **Retry Missing Events** - Retries any chapters that failed event extraction (per-chapter cache clear)
4. **Optional Entity Extraction** - Sequential per event file:
   - Weather (if enabled)
   - Equipment (if enabled)
   - Logistics (if enabled)
   - Casualties (if enabled)
   - Supplemental material (if enabled)
5. **Maps Extraction** - Source maps + external maps via OpenSERP (if enabled)
6. **Analysis** - Duplicate people report + related groups report

**Output Files:**
- `chapter*-event.json` - Events and sub-events
- `dates/{YYYYMM}_{ID}.json` - Central dates repository
- `places/{Name}_{ID}.json` - Central places repository
- `weather/{YYYYMM}_{ID}.json` - Central weather repository (optional)
- `equipment/{Name}_{ID}.json` - Military equipment (optional)
- `maps/{Name}_{ID}.json` - Maps from source material (optional)
- `external_maps/{Name}_{ID}.json` - Third-party maps (optional)
- `people/{Name}_{ID}.json` - Individual person files
- `people_groups/{Group}_{ID}.json` - Organization files

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
rm output/{Book}/chapter*-{type}.json
```
