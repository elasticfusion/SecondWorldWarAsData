# Events Extraction

**Module:** `src/extraction/events.py`  
**Status:** Production  
**Last Updated:** 2026-03-13

---

## Overview

Events extraction is the **core feature** of the pipeline. It analyzes parsed chapter content and extracts hierarchical event structures with sub-events, participants, and contextual information.

**Key Concept:** Events represent major historical occurrences (e.g., "Battle of Normandy"), while sub-events are specific actions or phases within that event (e.g., "Initial landing at Omaha Beach").

---

## Architecture

### Data Flow

```
Parsed Chapter (JSON)
    ↓
Filter Footnotes
    ↓
Create Extraction Prompt
    ↓
Grok API (with retry + validation)
    ↓
ULID Validation & Auto-fix
    ↓
Event JSON Output
    ↓
Extraction Summary Log
```

### Key Components

1. **Footnote Filtering** - Removes footnote sections from extraction
2. **Prompt Engineering** - Structured prompt with ULID requirements
3. **Validation Loop** - Retries with feedback on schema violations
4. **ULID Auto-fix** - Automatically corrects invalid ULIDs
5. **Summary Logging** - Tracks paragraph groupings and extraction metadata

---

## Data Structure

### Event Schema

```json
{
  "Chapter": "Chapter Title",
  "Event": {
    "EventID": "01KHXNSE0W41DV7VV6PEMDJJ5H",
    "Sub-events": [
      {
        "Sub-eventID": "01KHXNSE0WX99GG0CB53CD2242",
        "Sub-event_summary": "Brief description of what happened",
        "Sub-event_fulltext": {
          "Paragraph_1": "Exact paragraph text...",
          "Paragraph_2": "Exact paragraph text..."
        },
        "Endnote_References": [1, 2, 5],
        "Footnote_References": ["*", "†"]
      }
    ]
  }
}
```

### Field Descriptions

| Field | Type | Description |
|-------|------|-------------|
| `Chapter` | string | Chapter title from source |
| `EventID` | ULID | Unique identifier for the main event |
| `Sub-eventID` | ULID | Unique identifier for each sub-event |
| `Sub-event_summary` | string | Brief summary of the sub-event |
| `Sub-event_fulltext` | object | Paragraph numbers → exact text mapping |
| `Endnote_References` | array[int] | Endnote numbers referenced |
| `Footnote_References` | array[string] | Footnote symbols referenced |

---

## Features

### 1. Hierarchical Event Structure

Events are organized hierarchically:
- **Event:** Top-level historical occurrence
- **Sub-events:** Specific actions, phases, or components

**Example:**
```
Event: "Operation Overlord"
├── Sub-event: "Airborne landings"
├── Sub-event: "Naval bombardment"
├── Sub-event: "Beach landings at Omaha"
└── Sub-event: "Beach landings at Utah"
```

### 2. Paragraph Grouping

The LLM intelligently groups related paragraphs into logical sub-events:
- Maintains absolute paragraph numbering
- Preserves exact paragraph text
- Groups by topic, time, or location

### 3. Footnote Filtering

Automatically excludes footnote sections:
- Detects footnote headers (`### Footnotes`, `## Footnotes`)
- Filters all paragraphs after footnote header
- Skips files containing only footnotes

### 4. ULID Validation & Auto-fix

**ULID Requirements:**
- Exactly 26 characters
- Characters: `0-9 A-H J-K M-N P-T V-Z` (excludes I, L, O, U)
- No spaces or special characters

**Auto-fix Logic:**
```python
# If Grok returns invalid ULID (e.g., with spaces or wrong chars)
# Automatically replace with valid ULID
if not ulid_pattern.match(value):
    new_ulid = str(ulid.new())
    data[key] = new_ulid
```

### 5. Validation with Retry

Implements retry loop with validation feedback:

```python
for attempt in range(max_retries):  # Default: 3
    response = grok_client.extract_json(...)
    
    try:
        validate_event_json(response)
        return output_file  # Success
    except ValidationError as e:
        if attempt < max_retries - 1:
            # Add error to prompt and retry
            prompt += f"\n\nPREVIOUS ATTEMPT FAILED:\n{e.message}"
        else:
            raise  # Final attempt failed
```

**Benefits:**
- LLM learns from validation errors
- Increases success rate for complex chapters
- Provides specific feedback about schema violations

### 6. Reference Extraction

Extracts citation references from paragraph text:
- **Endnotes:** Numeric references `[1]`, `[2]`
- **Footnotes:** Symbol references `*`, `†`, `‡`

### 7. Image and Map Context

Provides available images and maps to LLM:
```
Available Images:
- Sherman tank at Normandy: https://example.com/img1.jpg
- Map of landing zones: https://example.com/map1.jpg

Available Maps:
- Map 1: https://example.com/tactical_map.jpg
```

LLM can reference these in sub-event context.

---

## Configuration

Events extraction has no specific config options. It runs automatically in Phase 2 for all parsed files.

**Related Config:**
```yaml
# In config.yaml
processing:
  validate_ulids: true  # Enable ULID validation
```

---

## Usage

### Automatic (Phase 2)

```bash
python3 phase2_extract.py
```

Events are extracted automatically for all `*-parsed.json` files.

### Programmatic

```python
from pathlib import Path
from src.grok_client import GrokClient
from src.extraction.events import extract_events

# Initialize client
grok_client = GrokClient(cache_dir=Path("cache/api"))

# Extract events from parsed file
parsed_file = Path("output/BreakoutAndPursuit/chapter1-parsed.json")
output_dir = Path("output/BreakoutAndPursuit")

event_file = extract_events(
    parsed_file=parsed_file,
    grok_client=grok_client,
    output_dir=output_dir,
    max_retries=3
)

print(f"Events saved to: {event_file}")
```

---

## Output Files

### Event File

**Location:** `output/{Book}/chapter{N}-event.json`  
**Format:** JSON with event hierarchy

**Example:** `output/BreakoutAndPursuit/chapter1-event.json`

### Processing Summary

**Location:** `logs/processing_summary.json`  
**Format:** JSONL (one line per file)

```json
{
  "book": "Breakout and Pursuit",
  "chapter": "The Situation on 1 July",
  "section": "full",
  "paragraph_range": {
    "start": 1,
    "end": 45,
    "count": 45
  }
}
```

### Extraction Summary

**Location:** `logs/extraction_summary.json`  
**Format:** JSONL (one line per file)

```json
{
  "book": "Breakout and Pursuit",
  "chapter": "The Situation on 1 July",
  "section": "full",
  "event_id": "01KHXNSE0W41DV7VV6PEMDJJ5H",
  "sub_events": [
    {
      "sub_event_id": "01KHXNSE0WX99GG0CB53CD2242",
      "summary": "Allied positions on 1 July",
      "paragraphs": [1, 2, 3, 4, 5]
    },
    {
      "sub_event_id": "01KHXNSE0WY88FF9BA42BC1131",
      "summary": "German defensive preparations",
      "paragraphs": [6, 7, 8, 9]
    }
  ]
}
```

---

## Integration with Other Features

Events serve as the foundation for all other extractions:

### Dates Extraction
- Reads event file
- Extracts temporal mentions from sub-event text
- Links dates to EventID and Sub-eventID via MentionID

### Places Extraction
- Reads event file
- Extracts geographic mentions from sub-event text
- Links places to EventID and Sub-eventID via MentionID

### People Extraction
- Reads event file
- Extracts person mentions from sub-event text
- Links people to EventID and Sub-eventID via MentionID

### Equipment Extraction (Optional)
- Reads event file
- Extracts equipment mentions from sub-event text
- Links equipment to EventID and Sub-eventID via MentionID

### Casualties Extraction (Optional)
- Reads event file
- Extracts casualty data from sub-event text
- Links casualties to EventID and Sub-eventID

---

## Error Handling

### Common Errors

#### 1. ULID Validation Errors

**Error:**
```
ValidationError: '01KHXNSE0W 41DV7VV6PEMDJJ5H' does not match '^[0-9A-HJKMNP-TV-Z]{26}$'
```

**Cause:** Grok returned ULID with spaces or invalid characters

**Solution:** Auto-fixed by `_fix_invalid_ulids()` function

#### 2. Schema Validation Errors

**Error:**
```
ValidationError: 'Sub-event_fulltext' is a required property
```

**Cause:** LLM omitted required field

**Solution:** Retry with validation feedback in prompt

#### 3. Footnote-Only Files

**Behavior:** File skipped, no event file created

**Log:**
```
INFO - Skipping chapter20-footnotes-parsed.json: contains only footnotes
```

**Solution:** Expected behavior, no action needed

### Retry Logic

**Default:** 3 attempts with validation feedback

**Exponential Backoff:** Handled by `GrokClient` (2s, 4s, 8s)

**Cache Behavior:**
- First attempt uses cache
- Retries bypass cache for fresh response

---

## Performance

### Caching

All API responses cached in `cache/api/events/`:
- Cache key: Hash of prompt + temperature
- Persistent across runs
- Significantly reduces API calls on re-runs

**Clear cache:**
```bash
rm -rf cache/api/events/*
```

### Processing Time

**Typical:** 10-30 seconds per chapter (depending on length)

**Factors:**
- Chapter length (paragraph count)
- API response time
- Validation retries needed

### Optimization Tips

1. **Use retry wrapper:** `python3 phase2_retry.py` handles transient errors
2. **Clear cache selectively:** Only clear cache for specific chapters if needed
3. **Monitor logs:** Check `logs/extraction_summary.json` for paragraph groupings

---

## Examples

### Example 1: Simple Chapter

**Input:** 10 paragraphs describing a single battle

**Output:**
```json
{
  "Chapter": "The Battle of Carentan",
  "Event": {
    "EventID": "01KHXNSE0W41DV7VV6PEMDJJ5H",
    "Sub-events": [
      {
        "Sub-eventID": "01KHXNSE0WX99GG0CB53CD2242",
        "Sub-event_summary": "Initial assault on German positions",
        "Sub-event_fulltext": {
          "Paragraph_1": "On 10 June, the 101st Airborne...",
          "Paragraph_2": "German forces, entrenched in...",
          "Paragraph_3": "The attack began at dawn..."
        },
        "Endnote_References": [1, 2],
        "Footnote_References": []
      },
      {
        "Sub-eventID": "01KHXNSE0WY88FF9BA42BC1131",
        "Sub-event_summary": "German counterattack and withdrawal",
        "Sub-event_fulltext": {
          "Paragraph_4": "By midday, German forces...",
          "Paragraph_5": "The 6th Fallschirmjäger Regiment..."
        },
        "Endnote_References": [3],
        "Footnote_References": ["*"]
      }
    ]
  }
}
```

### Example 2: Complex Chapter with Multiple Events

**Input:** 50 paragraphs covering multiple operations

**Output:** Single Event with 8-12 sub-events, each grouping 3-8 related paragraphs

---

## API Reference

### `extract_events()`

Extract events and sub-events from parsed content.

**Signature:**
```python
def extract_events(
    parsed_file: Path,
    grok_client: GrokClient,
    output_dir: Path,
    max_retries: int = 3
) -> Optional[Path]
```

**Parameters:**
- `parsed_file` (Path): Path to `*-parsed.json` file
- `grok_client` (GrokClient): Initialized Grok API client
- `output_dir` (Path): Directory for output file
- `max_retries` (int): Maximum validation retry attempts (default: 3)

**Returns:**
- `Path`: Path to created `*-event.json` file
- `None`: If file was skipped (footnotes only)

**Raises:**
- `ValueError`: If validation fails after max_retries

### `validate_event_json()`

Validate event JSON against schema.

**Signature:**
```python
def validate_event_json(data: Dict[str, Any]) -> None
```

**Parameters:**
- `data` (dict): Event JSON to validate

**Raises:**
- `ValidationError`: If JSON doesn't match EVENT_SCHEMA

### `create_event_prompt()`

Create extraction prompt from parsed data.

**Signature:**
```python
def create_event_prompt(parsed_data: Dict[str, Any]) -> Optional[str]
```

**Parameters:**
- `parsed_data` (dict): Parsed chapter data

**Returns:**
- `str`: Formatted prompt for Grok API
- `None`: If file contains only footnotes

---

## Troubleshooting

### Events not being extracted

**Check:**
1. Parsed file exists: `ls output/{Book}/*-parsed.json`
2. File not footnotes-only: Check logs for "contains only footnotes"
3. API key set: `echo $GROK_API_KEY`
4. Cache not corrupted: `rm -rf cache/api/events/*`

### Validation keeps failing

**Check:**
1. Grok API status: Test with simple prompt
2. Schema version: Ensure EVENT_SCHEMA is current
3. Logs: Check `logs/pipeline*.log` for specific validation errors

### Sub-events too granular or too broad

**Solution:** This is LLM behavior. Consider:
1. Adjusting prompt (requires code change)
2. Post-processing to merge/split sub-events
3. Accepting LLM's grouping decisions

---

## Related Documentation

- [Dates Extraction](../dates/README.md)
- [Places Extraction](../places/README.md)
- [People Extraction](../people/README.md)
- [Error Handling](../../core/error_handling.md)
- [Pipeline Overview](../../core/PIPELINE.md)

---

## Schema Reference

See `src/json_schemas.py` for complete EVENT_SCHEMA definition.

**Key Schema Elements:**
- EventID: ULID pattern `^[0-9A-HJKMNP-TV-Z]{26}$`
- Sub-events: Array of sub-event objects
- Sub-event_fulltext: Object with `Paragraph_N` keys
- References: Arrays of integers or strings
