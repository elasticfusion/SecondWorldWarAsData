# Dates Extraction

**Module:** `src/extraction/dates.py`  
**Status:** Production  
**Last Updated:** 2026-03-13

---

## Overview

Dates extraction analyzes event files and extracts all temporal mentions (dates and times) into a **central repository**. Each unique date gets its own file, with event mentions linked via MentionID.

**Key Concept:** Central repository prevents duplication - multiple events referencing "1944-06-06" all link to the same date file.

---

## Architecture

### Data Flow

```
Event File (JSON)
    ↓
For each Sub-event
    ↓
Create Date Extraction Prompt
    ↓
Grok API (structured output)
    ↓
ULID Validation & Auto-fix
    ↓
Filter Invalid Dates
    ↓
Find or Create Date File
    ↓
Add Event Mention
    ↓
Update Index
```

### Central Repository Structure

```
output/dates/
├── index.json                    # Date lookup index
├── 19440606_01KHYP2M.json       # D-Day
├── 19440701_01KHYP3N.json       # July 1, 1944
├── E194406_01KHYP4P.json        # Early June 1944
├── SU1944_01KHYP5Q.json         # Summer 1944
└── ...
```

**Filename Format:**
- Exact dates: `YYYYMMDD_{ULID_prefix}.json`
- Approximate: `{Precision}YYYYMM_{ULID_prefix}.json`
  - `E` = Early, `M` = Mid, `L` = Late
  - `SP` = Spring, `SU` = Summer, `FA` = Fall, `WI` = Winter

---

## Data Structure

### Date File Schema

```json
{
  "DateID": "01KHYP2M4N6P8Q0R2S4T6V8W0X",
  "date_start": "1944-06-06",
  "date_end": null,
  "time_start": "06:30",
  "time_end": null,
  "time_precision": "exact",
  "date_precision": "exact",
  "time_source": "Allied",
  "original_text": "6 June 1944 at 0630 hours",
  "normalized_datetime": null,
  "event_mentions": [
    {
      "MentionID": "01KHYP2N5P7Q9R1S3T5V7W9X1Z",
      "Event_Name": "Operation Overlord",
      "EventID": "01KHXNSE0W41DV7VV6PEMDJJ5H",
      "Sub_event_Name": "Initial landings at Omaha Beach",
      "Sub_eventID": "01KHXNSE0WX99GG0CB53CD2242",
      "book": "Cross-Channel Attack",
      "author": "Gordon A. Harrison",
      "series": "United States Army in World War II",
      "context": null,
      "original_text": "H-Hour was set for 0630"
    }
  ]
}
```

### Index Structure

```json
{
  "1944-06-06": "19440606_01KHYP2M.json",
  "1944-06-06T06:30": "19440606_0630_01KHYP2M.json",
  "1944-06-early": "E194406_01KHYP4P.json",
  "1944-summer": "SU1944_01KHYP5Q.json"
}
```

---

## Features

### 1. Central Repository

**One file per unique date:**
- Prevents duplication across chapters/books
- Enables cross-referencing
- Supports date-based queries

**Deduplication Logic:**
```python
# Normalized key for lookup
date_key = _normalize_date_key("1944-06-06", "06:30")
# → "1944-06-06T06:30"

# Check if date already exists
if date_key in index:
    date_file = dates_dir / index[date_key]
    # Add mention to existing file
else:
    # Create new date file
```

### 2. Date Precision Levels

**Exact Dates:**
- ISO 8601 format: `YYYY-MM-DD`
- Example: `1944-06-06`

**Approximate Dates:**
- Early/Mid/Late: `early-1944-06`, `mid-1944`, `late-1944-12`
- Seasonal: `spring-1944`, `summer-1942`, `fall-1944`, `winter-1943`

**Date Precision Field:**
- `exact` - Specific date known
- `early` - First third of period
- `mid` - Middle third of period
- `late` - Last third of period
- `spring` / `summer` / `fall` / `winter` - Seasonal

### 3. Time Handling

**Time Format:** `HH:MM` (24-hour)

**Time Precision:**
- `exact` - Specific time known
- `approximate` - Rough timeframe

**Time Source:**
- `Allied` - Allied time zone
- `German` - German time zone
- `Zulu` - UTC/GMT
- `Local` - Local time

### 4. Date Ranges

For events spanning multiple days:
```json
{
  "date_start": "1944-06-06",
  "date_end": "1944-06-12",
  "date_precision": "exact"
}
```

### 5. Event Mention Tracking

Each date file tracks all events that reference it:
- Links to EventID and Sub-eventID
- Preserves original text context
- Includes book metadata for citation

**Duplicate Prevention:**
```python
# Check if sub-event already has mention
existing = [m for m in date_data["event_mentions"] 
            if m["Sub_eventID"] == sub_event_id]
if existing:
    return  # Skip duplicate
```

---

## Configuration

No specific configuration options. Runs automatically in Phase 2.

---

## Usage

### Automatic (Phase 2)

```bash
python3 phase2_extract.py
```

Dates extracted automatically after events.

### Programmatic

```python
from pathlib import Path
from src.grok_client import GrokClient
from src.extraction.dates import extract_dates

grok_client = GrokClient(cache_dir=Path("cache/api"))

event_file = Path("output/BreakoutAndPursuit/chapter1-event.json")
parsed_file = Path("output/BreakoutAndPursuit/chapter1-parsed.json")
dates_dir = Path("output/dates")

extract_dates(
    event_file=event_file,
    grok_client=grok_client,
    dates_dir=dates_dir,
    parsed_file=parsed_file,
    max_retries=3
)
```

---

## Output Files

### Date Files

**Location:** `output/dates/{date}_{id}.json`

**Examples:**
- `19440606_01KHYP2M.json` - June 6, 1944
- `E194406_01KHYP4P.json` - Early June 1944
- `SU1944_01KHYP5Q.json` - Summer 1944

### Index File

**Location:** `output/dates/index.json`

Maps normalized date keys to filenames for fast lookup.

---

## Integration

### With Events
- Reads event files
- Extracts dates from sub-event text
- Links via EventID and Sub-eventID

### With Weather (Optional)
- Weather extraction uses date files
- Fetches historical weather for exact dates
- Links weather data to DateID

### With Places
- Dates and places often co-occur
- Both link to same EventID/Sub-eventID
- Enables temporal-spatial queries

---

## Error Handling

### Invalid Date Filtering

Automatically removes mentions with:
- Missing `date_start`
- Missing `original_text`
- Null or empty required fields

```python
# Before filtering: 5 mentions
# After filtering: 3 mentions (2 invalid removed)
logger.info("Filtered 2 invalid date mention(s)")
```

### ULID Auto-fix

Invalid ULIDs automatically replaced:
```python
# Invalid: "01KHYP2M 4N6P8Q" (has space, wrong length)
# Fixed:   "01KHYP2M4N6P8Q0R2S4T6V8W0X" (valid ULID)
```

### Retry Logic

**Default:** 3 attempts per sub-event

**Behavior:**
- First attempt uses cache
- Retries bypass cache
- Continues to next sub-event on failure

---

## Performance

### Caching

All API responses cached in `cache/api/dates/`

**Clear cache:**
```bash
rm -rf cache/api/dates/*
```

### Processing Time

**Typical:** 5-15 seconds per sub-event

**Factors:**
- Text length
- Number of date mentions
- API response time

---

## Examples

### Example 1: Exact Date with Time

**Input Text:**
```
"The attack began at 0630 hours on 6 June 1944."
```

**Output:**
```json
{
  "DateMentionID": "01KHYP2M4N6P8Q0R2S4T6V8W0X",
  "date_start": "1944-06-06",
  "date_end": null,
  "time_start": "06:30",
  "time_end": null,
  "time_precision": "exact",
  "date_precision": "exact",
  "time_source": "Allied",
  "original_text": "0630 hours on 6 June 1944"
}
```

### Example 2: Approximate Date

**Input Text:**
```
"In early June 1944, preparations intensified."
```

**Output:**
```json
{
  "DateMentionID": "01KHYP4P6R8S0T2V4W6X8Y0Z2A",
  "date_start": "early-1944-06",
  "date_end": null,
  "time_start": null,
  "time_end": null,
  "time_precision": null,
  "date_precision": "early",
  "time_source": null,
  "original_text": "early June 1944"
}
```

### Example 3: Date Range

**Input Text:**
```
"From 6 to 12 June, the beachhead was consolidated."
```

**Output:**
```json
{
  "DateMentionID": "01KHYP5Q7S9T1V3W5X7Y9Z1B3C",
  "date_start": "1944-06-06",
  "date_end": "1944-06-12",
  "time_start": null,
  "time_end": null,
  "time_precision": null,
  "date_precision": "exact",
  "time_source": null,
  "original_text": "From 6 to 12 June"
}
```

---

## API Reference

### `extract_dates()`

Extract dates from event file and add to central repository.

**Signature:**
```python
def extract_dates(
    event_file: Path,
    grok_client: GrokClient,
    dates_dir: Path,
    parsed_file: Optional[Path] = None,
    max_retries: int = 3
) -> Optional[Path]
```

**Parameters:**
- `event_file` (Path): Path to `*-event.json` file
- `grok_client` (GrokClient): Initialized Grok API client
- `dates_dir` (Path): Central dates directory (`output/dates/`)
- `parsed_file` (Path, optional): Path to parsed file for book metadata
- `max_retries` (int): Maximum retry attempts per sub-event (default: 3)

**Returns:**
- `Path`: Path to dates directory if dates were extracted
- `None`: If no dates were extracted

**Raises:**
- `ValueError`: If book metadata is missing

---

## Troubleshooting

### No dates being extracted

**Check:**
1. Event file exists and has sub-events
2. Sub-event text contains date mentions
3. API key is set
4. Check logs for "Extracted 0 dates"

### Dates not deduplicating

**Check:**
1. Index file exists: `output/dates/index.json`
2. Date normalization working correctly
3. Check logs for "Created date file" vs "Added mention"

### Invalid date mentions

**Check logs for:**
```
WARNING - Filtered date mention with null date_start
```

This is expected - LLM sometimes returns unparseable dates.

---

## Related Documentation

- [Events Extraction](../events/README.md)
- [Places Extraction](../places/README.md)
- [Weather Extraction](../weather/README.md)
- [Error Handling](../../core/error_handling.md)
