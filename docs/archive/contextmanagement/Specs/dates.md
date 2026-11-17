# Date Extraction - Central Repository

**Version:** 2.0.0  
**Status:** Active  
**Last Updated:** 2026-02-23

---

## Overview

Date extraction uses a **central repository pattern** where each unique date/time reference has its own file with event mentions tracked across all documents.

---

## Architecture

### Central Repository Structure

```
output/dates/
├── 19390901_0445_01H8XYZ8.json    # Sept 1, 1939 at 04:45
├── 19440606_01LONDON.json          # D-Day (no time)
├── M194407_01H8XYZ8.json           # mid-July 1944
├── SU1942_01H8XYZ8.json            # summer 1942
├── 1944early_01H8XYZ8.json         # early 1944
└── index.json                       # Sortable date keys → filenames
```

### Index Format

Index keys are **sortable** for chronological ordering:

```json
{
  "1939-09-01T04:45": "19390901_0445_01H8XYZ8.json",
  "1944-06-06": "19440606_01LONDON.json",
  "1944-07-mid": "M194407_01H8XYZ8.json",
  "1942-summer": "SU1942_01H8XYZ8.json",
  "1944-early": "1944early_01H8XYZ8.json"
}
```

**Key transformation:**
- `mid-1944-07` → `1944-07-mid`
- `summer-1942` → `1942-summer`
- `early-1944` → `1944-early`

---

## Date Formats

### Exact Dates

**ISO Format:** `YYYY-MM-DD`, `YYYY-MM`, or `YYYY`

**Examples:**
- `1944-06-06` - D-Day
- `1944-07` - July 1944
- `1944` - Year 1944

### Approximate Dates

**Prefix Format:** `{precision}-{date}`

**Supported Precisions:**
- `early` - Early part of period
- `mid` - Middle of period
- `late` - Late part of period
- `spring` - Spring season
- `summer` - Summer season
- `fall` / `autumn` - Fall season
- `winter` - Winter season

**Examples:**
- `mid-1944-07` - Mid-July 1944
- `early-1944` - Early 1944
- `late-1944-06` - Late June 1944
- `summer-1942` - Summer of 1942
- `winter-1944` - Winter 1944/1945

### Time Formats

**24-hour format:** `HH:MM`

**Examples:**
- `04:45` - 4:45 AM
- `14:30` - 2:30 PM
- `23:59` - 11:59 PM

---

## Date File Schema

### Structure

```json
{
  "DateID": "01H8XYZ3AB123CD456EF789GH",
  "date_start": "1939-09-01",
  "date_end": null,
  "time_start": "04:45",
  "time_end": null,
  "time_precision": "exact",
  "date_precision": "exact",
  "time_source": "German",
  "original_text": "September 1, 1939, at 4:45 AM",
  "normalized_datetime": "1939-09-01T04:45:00Z",
  "event_mentions": [...]
}
```

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `DateID` | string | 26-character ULID |
| `date_start` | string | Start date (ISO or approximate) |
| `date_end` | string\|null | End date if range |
| `time_start` | string\|null | Start time (HH:MM) |
| `time_end` | string\|null | End time if range |
| `time_precision` | string\|null | `exact` or `approximate` |
| `date_precision` | string\|null | `exact`, `early`, `mid`, `late`, `spring`, `summer`, `fall`, `winter` |
| `time_source` | string\|null | Time zone reference (German, Allied, Zulu, GMT, CET, Local) |
| `original_text` | string | Exact text from document |
| `normalized_datetime` | string\|null | ISO 8601 datetime (YYYY-MM-DDTHH:MM:SSZ) |
| `event_mentions` | array | Events where this date appears |

### Event Mention Structure

```json
{
  "MentionID": "01H8XYZM1N234OP567QR890ST",
  "Event_Name": "The Invasion of Poland",
  "EventID": "01H8XYZABC123DEF456GHJ789",
  "Sub_event_Name": "German forces cross the Polish border",
  "Sub_eventID": "01H8XYZ1MN456PQR789STU012",
  "book": "The Rise and Fall of the Third Reich",
  "author": "William L. Shirer",
  "series": "",
  "context": "start of invasion",
  "original_text": "September 1, 1939, at 4:45 AM"
}
```

---

## Features

### 1. Central Repository

**One file per unique date/time:**
- Deduplicates identical dates across documents
- Tracks all event mentions for each date
- Enables temporal analysis and timeline generation

### 2. Sortable Index

**Chronological ordering:**
- Index keys formatted for natural sorting
- Approximate dates sort correctly (early < mid < late)
- Seasons sort in calendar order

### 3. Date Ranges

**Support for multi-day events:**
```json
{
  "date_start": "1944-06-06",
  "date_end": "1944-06-30",
  "original_text": "June 6-30, 1944"
}
```

### 4. Time Ranges

**Support for duration:**
```json
{
  "time_start": "06:30",
  "time_end": "18:00",
  "original_text": "0630 to 1800 hours"
}
```

### 5. Time Zone Tracking

**Multiple time standards:**
- `German` - German military time
- `Allied` - Allied military time
- `Zulu` - UTC/GMT (military)
- `GMT` - Greenwich Mean Time
- `CET` - Central European Time
- `Local` - Local time at location

### 6. Precision Tracking

**Date precision:**
- `exact` - Specific date known
- `early` - Early part of period
- `mid` - Middle of period
- `late` - Late part of period
- Seasons - Seasonal reference

**Time precision:**
- `exact` - Specific time known
- `approximate` - Approximate time

### 7. Incremental Updates

**Non-destructive:**
- Existing date files are updated, not replaced
- New event mentions are appended
- Duplicate mentions (same sub-event) are skipped

### 8. Book Metadata

**Source tracking:**
- Book title, author, series
- Required for all mentions
- Enables citation and provenance

---

## Extraction Process

### 1. Load Event File

Read event JSON with sub-events and full text.

### 2. Load Book Metadata

Read from parsed file:
- Book title (required)
- Author (required)
- Series (optional)

### 3. Extract Dates per Sub-event

Use Grok API with structured outputs:
- Extract all date/time mentions
- Generate ULIDs for each mention
- Classify precision and format

### 4. Find or Create Date Files

For each extracted date:
- Normalize to sortable index key
- Check if date already exists
- Create new file if needed

### 5. Add Event Mentions

For each date file:
- Check for duplicate (same sub-event)
- Append new mention if unique
- Save updated file

### 6. Update Index

Save sortable index mapping dates to filenames.

---

## Usage

### Extract Dates

```python
from src.extraction.dates import extract_dates

dates_dir = extract_dates(
    event_file=Path("output/book/chapter1-event.json"),
    grok_client=grok_client,
    dates_dir=Path("output/dates"),
    parsed_file=Path("output/book/chapter1-parsed.json")
)
```

### Query Dates

```python
import json
from pathlib import Path

# Load index
with open("output/dates/index.json") as f:
    index = json.load(f)

# Find D-Day
date_file = index.get("1944-06-06")
if date_file:
    with open(f"output/dates/{date_file}") as f:
        date_data = json.load(f)
    print(f"D-Day mentioned in {len(date_data['event_mentions'])} events")
```

### Timeline Generation

```python
# Load all dates
dates_dir = Path("output/dates")
all_dates = []

for date_file in dates_dir.glob("*.json"):
    if date_file.name == "index.json":
        continue
    with open(date_file) as f:
        all_dates.append(json.load(f))

# Sort chronologically
all_dates.sort(key=lambda d: d["date_start"])

# Generate timeline
for date in all_dates:
    print(f"{date['date_start']}: {len(date['event_mentions'])} events")
```

---

## File Naming Convention

### Exact Dates

**Format:** `YYYYMMDD[_HHMM]_ULID8.json`

**Examples:**
- `19390901_0445_01H8XYZ8.json` - Sept 1, 1939 at 04:45
- `19440606_01LONDON.json` - June 6, 1944 (no time)
- `194407_01H8XYZ8.json` - July 1944

### Approximate Dates

**Format:** `{PREFIX}YYYY[MM]_ULID8.json`

**Prefixes:**
- `E` - early
- `M` - mid
- `L` - late
- `SP` - spring
- `SU` - summer
- `FA` - fall
- `AU` - autumn
- `WI` - winter

**Examples:**
- `M194407_01H8XYZ8.json` - mid-July 1944
- `SU1942_01H8XYZ8.json` - summer 1942
- `E1944_01H8XYZ8.json` - early 1944
- `L194406_01H8XYZ8.json` - late June 1944

---

## Deduplication

### Date Matching

Dates are considered identical if:
1. Same `date_start`
2. Same `time_start` (or both null)

**Example:**
- "September 1, 1939 at 4:45 AM" (German time)
- "1 Sept 1939, 0445 hours" (Allied time)

→ Same date file (different time_source tracked)

### Mention Deduplication

Event mentions are deduplicated by:
- Same `Sub_eventID`

**Prevents:**
- Duplicate mentions from re-running extraction
- Multiple mentions from same sub-event

---

## Limitations

### 1. Normalized DateTime

**Status:** Not implemented (TODO)

The `normalized_datetime` field is currently `null`. Future implementation should:
- Convert all dates to UTC
- Handle time zone conversions
- Resolve approximate dates to ranges

### 2. Context Extraction

**Status:** Not implemented (TODO)

The `context` field in event mentions is currently `null`. Should extract:
- "start of operation"
- "surrender date"
- "arrival time"
- etc.

### 3. Date Validation

**No validation for:**
- Invalid dates (e.g., Feb 30)
- Impossible times (e.g., 25:00)
- Anachronistic dates (before 1939, after 1945)

### 4. Ambiguous Dates

**Not handled:**
- "D-Day" → Should resolve to 1944-06-06
- "VE Day" → Should resolve to 1945-05-08
- "Christmas 1944" → Should resolve to 1944-12-25

---

## Schema Version

**Current:** 2.0.0  
**Schema File:** `contextmanagement/Specs/date_v2_central.json`

**Changes from v1:**
- Central repository (was per-chapter)
- Event mentions array
- Date precision field
- Sortable index keys
- Season support
- Book metadata in mentions

---

## Related Documentation

- **Schema:** `contextmanagement/Specs/date_v2_central.json`
- **Code:** `src/extraction/dates.py`
- **Phase 2:** `phase2_extract.py`

---

## Future Enhancements

1. **Normalized DateTime:** Convert all dates to UTC with ranges
2. **Context Extraction:** Extract role of date in event
3. **Date Validation:** Validate dates are within WWII period
4. **Named Dates:** Resolve "D-Day", "VE Day", etc.
5. **Date Linking:** Link dates to places (where event occurred)
6. **Timeline Visualization:** Generate interactive timelines
7. **Date Clustering:** Group related dates (same operation)
8. **Uncertainty Ranges:** Convert approximate dates to date ranges

---

**Status:** ✅ Production Ready (with noted limitations)
