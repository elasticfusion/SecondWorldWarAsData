# TODO: Short Response Rejection Breaks Valid Empty Results

**Priority:** High  
**Created:** 2026-03-16

## Problem

`grok_client.py` rejects valid short JSON responses like `[]` and `{}`:

1. **Line ~211**: `len(content) < 10` raises `GrokAPIError` — rejects `[]` (2 chars), which is the correct "no data found" response
2. **Line ~688**: `len(response) < 500` auto-retries and clears cache — wastes an API call when `[]` is the intended answer

Prompts explicitly say "Return empty array [] if no data found", but the client treats that as an error.

## Affected Extractors (5)

All go through `extract_json` and instruct Grok to return `[]` or `{}` when no data found:

- **casualties.py** — "Return empty array [] if no casualties found" (confirmed broken in logs)
- **equipment.py** — "If no images found, return empty array: []"
- **grok_search_maps.py** — "Return empty array [] if no maps found"
- **supplemental.py** — "Return JSON array of sub-events, or empty array []"
- **enrich_biographies.py** — "Return empty object if no data found" (`{}` = 2 chars)

Not affected: dates.py, places.py, weather_central.py — their empty arrays are wrapped in an outer object (>10 chars).

## Observed

```
ERROR - ✗ All 3 attempts failed: API returned suspiciously short response (2 chars): []
```

Each occurrence wastes 2 extra API calls (retry + cache clear) before failing.

## Fix

- Line ~211: Allow responses that are valid JSON regardless of length. Check `json.loads(content)` succeeds instead of length threshold.
- Line ~688: Same — try parsing before deciding to retry. If it parses as `[]`, `{}`, or `null`, accept it.

## Files

- `src/grok_client.py` — `chat_completion()` line ~211, `extract_json()` line ~688
