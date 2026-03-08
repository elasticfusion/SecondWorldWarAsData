# Phase 2: Event and Entity Extraction

## Overview

Phase 2 uses Grok API to extract structured entities from the parsed markdown content:
- Events and sub-events
- Dates with temporal context
- Places with geocoding
- People with biographical profiles
- Weather mentions
- Supporting materials

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Grok API Key

Create `.env` file in project root:

```bash
cp .env.example .env
# Edit .env and add your Grok API key
```

Your `.env` should contain:
```
GROK_API_KEY=your_actual_api_key_here
GROK_API_BASE_URL=https://api.x.ai/v1/chat/completions
GROK_MODEL=grok-beta
```

### 3. Verify Phase 1 Output

Ensure you have parsed files from Phase 1:
```bash
ls output/BreakoutAndPursuit/*-parsed.json
```

## Usage

### Extract Events (Step 1)

```bash
python3 phase2_extract.py
```

This will:
- Load all `*-parsed.json` files
- Call Grok API to extract events and sub-events
- Generate `*-event.json` files
- Cache API responses to avoid duplicate calls

### Output Format

Each section produces an event file: `chapter{N}{section}-event.json`

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
      "Sub-Event-Images": [
        ["https://url", "description"]
      ],
      "Sub-Events-Maps": [],
      "Sub-Event-Dates": ["1 July 1944", "September 1944"],
      "Sub-Event-Places": ["Germany", "France"],
      "Endnote_References": [1, 2],
      "Footnote_References": []
    }
  ]
}
```

## Features

### Caching
- API responses cached in `cache/` directory
- Avoids duplicate API calls
- Speeds up re-runs and testing

### Retry Logic
- Automatic retry on 5xx errors
- Exponential backoff (2s, 4s, 8s)
- Up to 3 attempts per request

### Error Handling
- Continues processing on individual file errors
- Logs all errors for review
- Validates JSON responses

## Next Steps

After event extraction, Phase 2 will continue with:
- Date extraction (`*-date.json`)
- Place extraction (`*-place.json`)
- People extraction (centrally managed `people.json`)
- Weather extraction (`*-weather.json`)
- Supporting materials (`*-supplemental.json`)

## Troubleshooting

### API Key Not Found
```
Error: GROK_API_KEY not found in environment
```
**Solution:** Create `.env` file with your API key

### No Parsed Files
```
Error: No parsed files found
```
**Solution:** Run `python3 phase1_parse.py` first

### API Rate Limits
If you hit rate limits, the retry logic will handle temporary errors. For persistent issues, check your API quota.

### JSON Parse Errors
If Grok returns invalid JSON, check the logs for the raw response. You may need to adjust the system prompt.
