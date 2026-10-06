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

### 2a. Resolved interval (sortable, queryable) — the vagueness-safe layer

`date_start` is the **verbatim** source form (ISO *or* approximate like `early-1944-06`),
so it is not directly range-sortable. Every record therefore also carries a **resolved ISO
interval** derived DETERMINISTICALLY from the stated precision (`src/extraction/
date_resolution.py`, run at write time):

| Field | Meaning |
|---|---|
| `resolved_earliest` | earliest instant the stated date could be (ISO `YYYY-MM-DD`) |
| `resolved_latest` | latest instant it could be |
| `resolution_method` | `precision_rule` \| `range` \| `unresolved` |

Examples: `1944-06-06` → `[1944-06-06, 1944-06-06]`; `early-1944-06` →
`[1944-06-01, 1944-06-10]`; `summer-1944` → `[1944-06-01, 1944-08-31]`; `1944` →
`[1944-01-01, 1944-12-31]`; a stated range → `[start, end]`.

**Principles (non-negotiable):**
- **The source is the sole authority.** The resolver performs NO disambiguation and NO
  guessing — it only expands the precision the source already stated into bounds. A vague
  source yields a WIDE interval (the honest answer); more precision comes only from better
  source documents.
- **Verbatim preserved.** `date_start` + `original_text` are never overwritten; the
  interval is a derived, additive layer.
- **Never fabricate.** A date that cannot be mechanically bounded (e.g. an un-anchored
  "the following spring") gets `resolved_* = null` + `resolution_method: unresolved` — it
  is stored and linked but excluded from interval queries, not guessed.
- Config-driven: month-third splits + season bounds live in small tables in the resolver.
- Relative/contextual dates ("three days later", anchored to the sub-event's date) are a
  deferred Tier-2 follow-up.

**Datetime bounds (time folded in):** `resolved_earliest`/`resolved_latest` are full
ISO-8601 **datetimes** (`YYYY-MM-DDThh:mm:ssZ`). A stated time tightens the bounds —
"5 Jan 1945 at 0500" → `[1945-01-05T05:00:00Z, 1945-01-05T05:00:00Z]`; no time stated →
the honest full-day span `[…T00:00:00Z, …T23:59:59Z]` ("sometime that day"). This makes
intra-day ordering and "after 0500" queries work on the interval itself.

### 2b. Significance summary (synthesized, derived — `summary`)

A date can accumulate 1–1000+ `event_mentions`. Each record therefore also carries a
**1–2 sentence significance summary** so a reader/RAG result gets the gist without reading
every mention (`src/extraction/date_summary.py`, run as a separate pass `summarize_dates`).

| Field | Meaning |
|---|---|
| `summary` | 1–2 sentence "what this date is about", synthesized from THIS date's mentions |
| `summary_source` | `synthesized` (derived — NOT an extracted fact) |
| `summary_generated_at` | ISO timestamp of generation |
| `summary_mention_count` | how many mentions the summary covered (staleness anchor) |
| `mention_count` | cheap always-present importance signal (count of event_mentions) |

**Principles:**
- **Source-grounded only.** The LLM summarizes STRICTLY the date's own `event_mentions`
  (names + `original_text`) — never outside/world knowledge. The authoritative facts remain
  the individual mentions; the summary is a convenience layer, clearly marked synthesized.
- **Never fabricate / fail-open.** On error, no summary is written (count still stamped).
- **Staleness-gated, content-aware.** Regenerated when the mention set's CONTENT changes —
  a cheap `summary_mentions_hash` over `(Sub_eventID, time_start, original_text)` — so a
  corrected/replaced mention at the same count still triggers re-summary (not count-only),
  plus a `>=N` growth secondary trigger.
- **Partial-view honest for huge dates.** A date with more mentions than the render cap is
  summarized on a REPRESENTATIVE slice (one per distinct event first), and the prompt is
  told it's a "partial view of N total" — so the summary reflects overall significance and
  scale, not an arbitrary first-N-by-file-order sample.
- **Batched/parallel.** The pass uses a thread pool (like people/groups/places enrichment)
  and is xAI Batch-API compatible (50% discount) when the client is in batch mode.

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

**Duplicate Prevention** (keyed on `(Sub_eventID, time_start, original_text)`): a sub-event
re-run is skipped, but the SAME sub-event citing this date at a DIFFERENT time ("0500" vs
"1800") or in DIFFERENT words is kept as a distinct mention. Each mention's own id is
**`DateMentionID`** (the name all consumers reference it by).
```python
new_key = (sub_event_id, mention.get("time_start"), mention.get("original_text", ""))
existing = {(m.get("Sub_eventID"), m.get("time_start"), m.get("original_text", ""))
            for m in date_data["event_mentions"]}
if new_key in existing:
    return  # exact re-run -> skip
```

> **`normalized_datetime` is legacy/exact-only.** It is emitted ONLY for an exact-ISO
> `date_start` (never for approximate forms like `summer-1944`). For sortable bounds on ANY
> date, use `resolved_earliest`/`resolved_latest` (the resolved datetime interval), which
> supersede it. Those bounds are **naive** in the source's `time_source` zone (German/
> Allied/Zulu/Local) — the resolver does not convert, so cross-`time_source` comparisons
> must account for the offset. Also note: `early/mid/late` resolve for both a month
> (`early-1944-06`) and a whole year (`mid-1944` → May–Aug).

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

## Phase 3 Enrichment

**None by design.** Dates are extracted, interval-resolved, and summarized in Phase 2; they
receive no external enrichment in Phase 3. See
[Phase 3 Enrichment](../../core/PHASE3_ENRICHMENT.md).
