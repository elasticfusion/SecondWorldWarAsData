# Grok API Request Flow

## Overview

This document describes the order and structure of API requests sent to Grok AI during the extraction pipeline.

## Request Order

### Phase 2: Event Extraction

For each `*-parsed.json` file in the output directory:

1. **Load parsed content** - Read markdown parsing results
2. **Filter footnotes** - Remove footnote paragraphs from content
3. **Build prompt** - Assemble extraction prompt with:
   - Book and chapter metadata
   - All content paragraphs (with absolute numbering)
   - Available images list
   - Available maps list
   - JSON schema example
   - ULID format requirements
4. **Check cache** - Look for cached response using prompt hash
5. **Send to Grok** (if not cached):
   - System prompt: "You are an expert historian..."
   - User prompt: Full extraction request
   - Temperature: 0.1 (low for consistency)
   - Model: grok-beta
6. **Validate response** - Check JSON against schema
7. **Retry if invalid** (up to 3 attempts):
   - Add validation error to prompt
   - Request correction
   - Re-validate
8. **Cache successful response** - Store for future runs
9. **Save output** - Write `*-event.json` file

### Processing Order

Files are processed in **alphabetical order** by filename:

```
Cross-Channel-Attack/
  chapter0a-parsed.json → chapter0a-event.json
  chapter0b-parsed.json → chapter0b-event.json
  chapter0c-parsed.json → chapter0c-event.json
  ...

BreakoutAndPursuit/
  chapter1a-parsed.json → chapter1a-event.json
  chapter1b-parsed.json → chapter1b-event.json
  ...
```

## Request Structure

### System Prompt (constant)
```
You are an expert historian analyzing World War II documents.
Extract events and sub-events from the provided text.

Requirements:
- Group related paragraphs into logical sub-events
- Each sub-event should have a clear summary
- Preserve the exact paragraph text with absolute paragraph numbers
- Extract images, maps, dates, places mentioned in each sub-event
- Identify endnote and footnote references

Return ONLY valid JSON matching the schema. No additional text.
```

### User Prompt (per file)
```
Analyze this chapter from "{book}" by {author}.

Chapter: {chapter_title}

Paragraphs:
Paragraph_1: {text}
Paragraph_2: {text}
...

Available Images:
- {description}: {url}
...

Available Maps:
- Map {id}: {url}
...

Extract the main event and sub-events. Return JSON in this exact format:
{JSON schema example with ULID requirements}
```

## Caching Strategy

### Cache Key Generation
- Hash of: `prompt + temperature + model`
- SHA256 hex digest
- Example: `a3f5c8d9e2b1...`

### Cache Location
```
cache/api/events/{hash}.json
```

### Cache Behavior
- **Hit**: Return cached response immediately (no API call)
- **Miss**: Call API, validate, then cache successful response
- **Invalid**: Don't cache, retry with feedback

### Cache Invalidation
Manual only - delete cache directory:
```bash
rm -rf cache/api/events
```

## Retry Logic

### Validation Failures
1. **Attempt 1**: Send original prompt
2. **Attempt 2**: Add validation error feedback to prompt
3. **Attempt 3**: Add validation error feedback again
4. **Failure**: Log error, skip file, continue to next

### API Errors (5xx)
- Automatic retry via `tenacity` library
- Exponential backoff: 2s → 4s → 8s
- Max 3 attempts
- Reraises exception if all fail

## Future Phases

### Phase 3: Date Extraction
- Input: `*-event.json` files
- Extract: Date mentions from sub-events
- Output: `*-dates.json` files
- Cache: `cache/api/dates/`

### Phase 4: Place Extraction
- Input: `*-event.json` files
- Extract: Place mentions with geocoding
- Output: `*-places.json` files
- Cache: `cache/api/places/`

### Phase 5: People Extraction
- Input: `*-event.json` files
- Extract: People mentions
- Output: Append to central `output/people.json`
- Cache: `cache/api/people/`

### Phase 6: Weather & Supplemental
- Input: `*-event.json` files
- Extract: Weather and supplemental materials
- Output: `*-weather.json`, `*-supplemental.json`
- Cache: `cache/api/weather/`, `cache/api/supplemental/`

## Rate Limiting

Currently **no rate limiting** is implemented. All files are processed sequentially as fast as the API responds.

To add rate limiting, modify `phase2_extract.py` to add delays between requests.

## Token Usage

Logged at DEBUG level for each request:
- Prompt tokens
- Completion tokens
- Total tokens

Check logs for usage patterns:
```bash
grep "Response tokens" logs/pipeline_*.log
```
