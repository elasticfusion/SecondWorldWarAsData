# Phase 2: Event Extraction - Ready to Use

## What's Been Built

### Core Components

1. **Grok API Client** (`src/grok_client.py`)
   - HTTP client with retry logic (3 attempts, exponential backoff)
   - Disk-based caching to avoid duplicate API calls
   - JSON extraction with markdown cleanup
   - Error handling for 5xx errors

2. **Pydantic Schemas** (`src/schemas.py`)
   - EventOutput - Events and sub-events
   - DateOutput - Date mentions with temporal context
   - PlaceOutput - Place mentions with geocoding
   - PeopleOutput - Centrally managed people profiles
   - WeatherOutput - Weather mentions
   - ULID generation for all entities

3. **Event Extractor** (`src/extraction/events.py`)
   - Converts parsed JSON to Grok prompts
   - Extracts events and sub-events
   - Groups paragraphs logically
   - Preserves absolute paragraph numbering
   - Links images, maps, dates, places

4. **Main Script** (`phase2_extract.py`)
   - Processes all parsed files
   - Handles errors gracefully
   - Logs progress
   - Creates `*-event.json` files

## Setup Required

### 1. Get Grok API Key
You need a Grok API key from x.ai

### 2. Create .env File
```bash
cp .env.example .env
# Edit .env and add your API key
```

### 3. Verify Setup
```bash
python3 test_phase2_setup.py
```

Should show:
- ✓ All imports successful
- ✓ GROK_API_KEY is set
- ✓ Found 8 parsed file(s)
- ✓ ULID generation works

## Usage

### Run Event Extraction
```bash
python3 phase2_extract.py
```

This will:
1. Load all `*-parsed.json` files from Phase 1
2. For each file, call Grok API to extract events/sub-events
3. Save results as `*-event.json`
4. Cache responses in `cache/` directory

### Expected Output
```
output/BreakoutAndPursuit/
├── chapter1a-parsed.json
├── chapter1a-event.json  ← NEW
├── chapter1b-parsed.json
├── chapter1b-event.json  ← NEW
...
```

## Features

### Caching
- API responses cached by prompt hash
- Re-running script uses cache (no API calls)
- Clear cache: delete `cache/` directory

### Retry Logic
- Automatic retry on 5xx errors
- Exponential backoff: 2s → 4s → 8s
- Up to 3 attempts per request

### Error Handling
- Continues on individual file errors
- Logs all errors
- Validates JSON responses

## Output Format

Matches your `chapter1a-event.json` example:

```json
{
  "Chapter": "Chapter I: The Allies' Mission",
  "Event": "Breakout and Pursuit",
  "Sub-event": [
    {
      "Sub-event_summary": "Overview of Allied strategic objective...",
      "Sub-event_fulltext": {
        "Paragraph_1": "The heart of Germany...",
        "Paragraph_2": "Two months later..."
      },
      "Sub-Event-Images": [["url", "description"]],
      "Sub-Events-Maps": [["url", "description"]],
      "Sub-Event-Dates": ["1 July 1944"],
      "Sub-Event-Places": ["Germany", "France"],
      "Endnote_References": [1, 2],
      "Footnote_References": []
    }
  ]
}
```

## Next Steps (Future Phases)

After event extraction works, we'll add:
- **Date extraction** - Extract and parse all date mentions
- **Place extraction** - Geocode locations with coordinates
- **People extraction** - Build biographical profiles
- **Weather extraction** - Extract weather impacts
- **ULID linking** - Link all entities with ULIDs
- **Validation** - JSON schema validation
- **JQ scripts** - Generate validation queries

## Current Status

✅ **Phase 1:** Complete - Parser working, quality checks passed  
🔨 **Phase 2:** Ready - Event extraction built, needs API key to run  
⏳ **Phase 3-7:** Planned

## To Start Using

1. Get your Grok API key
2. Create `.env` file with the key
3. Run `python3 phase2_extract.py`
4. Review generated `*-event.json` files

**Ready when you are!**
