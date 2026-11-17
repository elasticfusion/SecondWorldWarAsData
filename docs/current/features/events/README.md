# Events Extraction

**Module:** `src/extraction/events.py`  
**Status:** Production  
**Last Updated:** 2026-11-16

---

## Overview

Events extraction is the **core feature** of the pipeline. It analyzes parsed chapter content and extracts hierarchical event structures with sub-events, participants, and contextual information.

**Key Concept:** Events represent major historical occurrences (e.g., "Battle of Normandy"), while sub-events are specific actions or phases within that event (e.g., "Initial landing at Omaha Beach").

> **Language:** Events (like all extractors) operate on English text. Non-English sources
> (e.g. German KTBs) are detected and translated to English in **Phase 0** before extraction
> — see [Language Detection & Translation](../../dataquality/LANGUAGE_TRANSLATION.md). The
> extractor itself does no language handling by design.

---

## Architecture

### Data Flow

```
Parsed Chapter (JSON)
    ↓
Filter Footnotes (footnote-only files are skipped)
    ↓
Create Extraction Prompt (prompts/events.yaml; paragraphs shown as Paragraph_N)
    ↓
Grok API (one request per chapter; batch round in batch mode)
    ↓
Validate (JSON schema, paragraph numbers in range, coverage >= 80%) - retry / auto-split on failure
    ↓
Code-side repair: paragraphs disjoint, orphan paragraphs claimed by the nearest earlier sub-event,
Sub-event_fulltext rebuilt from the parsed text, note references populated
    ↓
Deterministic EventID / Sub-eventIDs (the model's IDs are replaced)
    ↓
Event JSON Output (+ source_section record) + OBSERV events.extract
```

### Key Components

1. **Footnote Filtering** - Removes footnote sections from extraction
2. **Prompt** - `prompts/events.yaml`: the model returns `paragraph_numbers` per sub-event; each paragraph belongs to exactly one sub-event
3. **Validation Loop** - `extract_events(max_retries=3)` retries with feedback on schema violations; out-of-range paragraph numbers
   (`_validate_paragraph_range`) and coverage below 80% of the chapter's narrative text (`_MIN_COVERAGE`, `_reject_low_coverage`) are rejected and re-asked
4. **Auto-split** - a truncated answer or a low-coverage chapter is split at a heading (or the midpoint) and the parts merged (`_auto_split_and_extract`, `_split_paragraphs`)
5. **Paragraph repair** - `_make_paragraphs_disjoint`, `_claim_orphan_paragraphs`, `_reconstruct_fulltext`
6. **Deterministic IDs** - `_apply_deterministic_ids` (`src/utils/deterministic_ids.py`): EventID from book + chapter title + section, Sub-eventID from the EventID + paragraph range + name
7. **Summary Logging** - Tracks paragraph groupings and extraction metadata

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
| `EventID` | ULID | Deterministic: `event_id(book, chapter_title, section_id)`; the model's value is replaced |
| `Sub-eventID` | ULID | Deterministic: `sub_event_id(EventID, paragraph range, name)` |
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

### 4. Deterministic IDs and ULID validation

The EventID and Sub-eventIDs are derived by code (`_apply_deterministic_ids`), so re-extracting the same section gives the same IDs and the model's IDs are never trusted.
`_fix_invalid_ulids` (`src/utils/json_validator.py`) still repairs malformed ULIDs in an answer during the retry path (`_try_fix_ulid_errors`).

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

Events extraction has no specific config options. It runs automatically in Phase 2 for all parsed files: one request per chapter (`cache_type="events"`), through the xAI batch API when `batch.phase2` is true ([batch processing](../batch_processing/README.md)).

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

**Location:** next to the parsed file, `output/content/<book>/chapterN<section>-event.json` (`_save_event_output`)  
**Format:** JSON with event hierarchy

**Example:** `output/content/BreakoutAndPursuit/chapter1-event.json`

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

The per-sub-event paragraph groupings are in the event file itself (`Sub-events[].Sub-event_fulltext`); the old `logs/extraction_summary.json` is no longer written.

---

## Integration with Other Features

Events are the backbone that every other entity links to through `EventID` / `Sub-eventID` and `event_mentions`.
They are **not** the text the other extractors read. Dates, places, people, people groups, equipment, weather, logistics and casualties
are discovered over fixed 3-paragraph windows of the raw parsed text (`src/extraction/windows.py`), and each answer item is attributed to the
real sub-event whose paragraphs hold its quote (`windows.settle`, `windows.regroup`). The sub-event grouping decides **which sub-event a mention belongs to**,
not what the entity extractors see. Mention identity is `entity_records.mention_identity` (same sub-event + same normalized quote = one mention).

See the per-entity READMEs: [Dates](../dates/README.md), [Places](../places/README.md), [People](../people/README.md), [People groups](../people_groups/README.md).

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
- The first attempt uses the cache
- A rejected answer is evicted/kept aside (`_keep_rejected`) so the retry asks again

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

**Typical:** seconds per chapter live; in batch mode the events request waits for its batch round

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
- `ValueError`: If validation fails after max_retries (in batch mode the call queues the request and raises `BatchModeCollecting`)

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
- [Error Handling (archived 2026-03 spec)](../../../archive/contextmanagement/Specs/error_handling.md)
- [Pipeline Overview](../../core/PIPELINE.md)

---

## Schema Reference

See `src/json_schemas.py` for complete EVENT_SCHEMA definition.

**Key Schema Elements:**
- EventID: ULID pattern `^[0-9A-HJKMNP-TV-Z]{26}$`
- Sub-events: Array of sub-event objects
- Sub-event_fulltext: Object with `Paragraph_N` keys
- References: Arrays of integers or strings

## Phase 3 Enrichment

**None by design.** Events are the Phase-2 extraction backbone; they are not externally
enriched in Phase 3 (the entities they link to — people, places, etc. — are). See
[Phase 3 Enrichment](../../core/PHASE3_ENRICHMENT.md).
