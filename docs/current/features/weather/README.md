# Weather Extraction

**Module:** `src/extraction/weather_central.py`  
**Status:** Optional (Disabled by default)  
**Last Updated:** 2026-10-06

---

## Overview

Weather extraction analyzes event files for weather mentions and (optionally) enriches them
with historical data from **Open-Meteo** (reanalysis) and **NOAA** (station-observed). Data
is stored in a central repository with links to dates and places.

**Key Features:**
- Extracts weather mentions from text into an `extracted_data` object
- Optional Open-Meteo historical fetch into `api_data`; NOAA observed into `noaa_observed`
- Central repository (one file per date+place combination)
- `DateID` resolved from the dates directory (not LLM-provided)
- `location` with `PlaceID` + coordinates, resolved via the places **alias-aware** index
  with bounded matching; `null` coordinates when ungeocoded (no `0.0` placeholder)
- `source_type`: `extracted`, `api_only`, or `hybrid`
- `temperature_unit` normalized to the schema enum `C`/`F`
- Operational impact tracking

**Status:** Optional feature, disabled by default in `config.yaml`

---

## Known gaps / follow-ups

- The non-batch `prompts/weather.yaml` uses a different (legacy) temperature shape; the live
  path uses `prompts/weather_batch.yaml`.

### Resolved
- **Date linking is interval-aware** — weather links a date by exact `date_start` OR by
  overlap with a date record's resolved interval (`resolved_earliest/latest`), and carries
  the date's `time_source` onto the weather record.
- **Dedup is PlaceID-first** — the weather dedup key is the canonical `PlaceID`
  (`{date}_pid_{PlaceID}`), so place aliases/spellings collapse to one file per place+date;
  falls back to the normalized name when unresolved (legacy name keys still match).
- **Place linking reuses the places library** — `_build_place_name_index` (alias-aware) +
  bounded matching; coordinates come from the resolved place record (weather never
  geocodes).

---

## Architecture

### Data Flow

```
Event File (JSON)
    ↓
Batch all Sub-events → Single Grok API call
(includes per-sub-event place/date context)
    ↓
Response: {Sub-eventID: [mentions], ...}
    ↓
For each mention:
    ↓
Filter (exact dates only)
    ↓
Resolve DateID from dates directory lookup
    ↓
Lookup Place Coordinates
    ↓
Fetch Historical Weather (Open-Meteo API)
    ↓
Find or Create Weather File
    ↓
Add Event Mention
    ↓
Update Index
```

**DateID Resolution:** DateID is resolved by matching the date string against the dates directory, not trusting LLM-provided IDs.

**Batching:** All sub-events are sent in a single API call per chapter (via `_batch_extract_weather`). Post-processing (coordinate lookup, API fetch, file creation) remains per-mention.

### Central Repository Structure

```
output/weather/
├── index.json                           # Weather lookup index
├── 19440606_Normandy_01KHYP2M.json     # D-Day weather
├── 19440701_Paris_01KHYP3N.json        # July 1 weather
└── ...
```

**Filename Format:** `{YYYYMMDD}_{PlaceName}_{ULID_prefix}.json`

---

## Data Structure

### Weather File Schema

```json
{
  "WeatherID": "01ULID...",
  "date": "1944-06-06",
  "DateID": "01ULID...",
  "location": {
    "place_name": "Normandy",
    "PlaceID": "01ULID...",
    "latitude": 49.35,
    "longitude": -0.50
  },
  "source_type": "hybrid",
  "extracted_data": {
    "description": "Heavy overcast with rain",
    "temperature": null,
    "temperature_unit": null,
    "measurement_system": null,
    "notable_impact": "Delayed air support",
    "original_text": "The weather on 6 June was overcast with light rain",
    "book": "Cross-Channel Attack",
    "author": "Gordon A. Harrison"
  },
  "api_data": {
    "provider": "open-meteo",
    "retrieved_at": "2026-03-22T16:53:30Z",
    "temperature_max_c": 16.2,
    "temperature_min_c": 12.8,
    "precipitation_mm": 4.8,
    "windspeed_max_kmh": 23.2,
    "cloud_cover_percent": 91,
    "raw_response": {}
  },
  "event_mentions": [
    {
      "MentionID": "01ULID...",
      "Event_Name": "Operation Overlord",
      "EventID": "01ULID...",
      "Sub_event_Name": "D-Day landings",
      "Sub_eventID": "01ULID...",
      "book": "Cross-Channel Attack",
      "author": "Gordon A. Harrison",
      "series": "United States Army in World War II"
    }
  ]
}
```

**`source_type` values** (schema enum):
- `extracted` — narrative weather description only (no API data)
- `api_only` — Open-Meteo API data only (no narrative extraction)
- `hybrid` — both narrative description and API data

**Provenance layers:** `extracted_data` (narrative, with `original_text`/book),
`api_data` (Open-Meteo reanalysis), `noaa_observed` (NOAA station-observed, phase-3). A
record may carry any combination; `temperature_unit` is normalized to the schema enum
`C`/`F` (`null` if unknown).

**Narrative vs. scientific measurements (both kept, non-redundant).** A quantity like
snowfall is captured in BOTH layers when available, and they are NEVER merged:
- *Narrative* (`extracted_data`): what the source document SAYS — `precipitation_text`
  (verbatim, e.g. "3 inches of snow") + parsed `precipitation_amount`/`precipitation_unit`
  (in/cm/mm)/`precipitation_type` ONLY when the source states a number (else text only;
  never fabricated).
- *Scientific* (`noaa_observed`): the station-OBSERVED measurement (e.g.
  `snowfall_mm: 81.3`) with `station_id` + `source`/`source_url`.
"3 inches of snow (narrative, Green Book)" and "81.3 mm observed (NOAA station X)" are
different claims from different authorities about the same event — both preserved and
attributable.

**Cross-references:**
- `DateID` → top-level `DateID` in `output/dates/*.json`
- `location.PlaceID` → top-level `PlaceID` in `output/places/*.json` (resolved via the
  places alias-aware index; `null` + `null` coordinates when unresolved/ungeocoded — geo
  is owned by the places subsystem, not fabricated here)

---

## Features

### 1. Central Repository

**One file per date+place combination:**
- Prevents duplication
- Enables cross-referencing
- Supports weather-based queries

### 2. Open-Meteo API Integration

**Historical Weather Data:**
- Temperature (max/min, °C)
- Precipitation (mm)
- Wind speed (km/h)
- Cloud cover (%)

**API Details:**
- Service: Open-Meteo Archive API
- URL: `https://archive-api.open-meteo.com/v1/archive`
- Free tier: No API key required
- Rate limit: Reasonable for batch processing
- Data availability: 1940-present

**Example API Call:**
```
GET https://archive-api.open-meteo.com/v1/archive
  ?latitude=49.18
  &longitude=-0.37
  &start_date=1944-06-06
  &end_date=1944-06-06
  &daily=temperature_2m_max,temperature_2m_min,precipitation_sum,windspeed_10m_max,cloud_cover_mean
```

### 3. Exact Dates Only

**Filters out approximate dates:**
- ✅ Accepted: `1944-06-06` (exact date)
- ❌ Rejected: `early-1944-06` (approximate)
- ❌ Rejected: `summer-1944` (seasonal)

**Reason:** API requires exact dates for historical data

### 4. Place Coordinate Lookup

**Automatic coordinate resolution:**
1. Check if PlaceMentionID provided
2. Look up place in `output/places/` repository
3. Extract latitude/longitude
4. Use coordinates for API call

**Fallback:** If place not found, skip API fetch (still save mention)

### 5. Operational Impact Tracking

**Notable impacts extracted:**
- Visibility effects
- Mobility restrictions
- Operational delays
- Equipment performance

**Examples:**
- "Poor visibility delayed airborne operations"
- "Heavy rain made roads impassable"
- "Fog prevented air support"

### 6. Temperature Unit Handling

**Supports both systems:**
- Celsius (metric)
- Fahrenheit (imperial)

**Conversion:** API returns Celsius, original text may use Fahrenheit

---

## Configuration

### Enable Weather Extraction

```yaml
# config.yaml
weather:
  enabled: true                    # Enable weather extraction
  fetch_api_data: true            # Fetch from Open-Meteo API
  api_provider: "open-meteo"       # API provider
  cache_responses: true            # Cache API responses
  only_precise_dates: true         # Skip approximate dates
  timeout: 30                      # API timeout (seconds)
```

### Configuration Options

| Option | Default | Description |
|--------|---------|-------------|
| `enabled` | `false` | Enable weather extraction |
| `fetch_api_data` | `true` | Fetch historical data from API |
| `api_provider` | `"open-meteo"` | API provider (only Open-Meteo supported) |
| `cache_responses` | `true` | Cache API responses |
| `only_precise_dates` | `true` | Skip approximate dates |
| `timeout` | `30` | API request timeout (seconds) |

---

## Usage

### Enable in Config

```bash
# Edit config.yaml
vim config.yaml

# Set weather.enabled: true
```

### Run Phase 2

```bash
python3 phase2_extract.py
```

Weather extraction runs automatically after places extraction.

### Programmatic

```python
from pathlib import Path
from src.grok_client import GrokClient
from src.extraction.weather_central import extract_weather_central

grok_client = GrokClient(cache_dir=Path("cache/api"))

extract_weather_central(
    event_file=Path("output/BreakoutAndPursuit/chapter1-event.json"),
    weather_dir=Path("output/weather"),
    grok_client=grok_client,
    places_dir=Path("output/places"),
    parsed_file=Path("output/BreakoutAndPursuit/chapter1-parsed.json"),
    fetch_api=True,
)
```

---

## Output Files

### Weather Files

**Location:** `output/weather/{YYYYMMDD}_{Place}_{ID}.json`

**Examples:**
- `19440606_Normandy_01KHYP2M.json`
- `19440701_Paris_01KHYP3N.json`

### Index File

**Location:** `output/weather/index.json`

Maps date+place keys to filenames.

---

## Integration

### With Events
- Reads event files
- Extracts weather from sub-event text
- Links via EventID and Sub-eventID

### With Dates
- Links to DateID
- Requires exact dates
- Filters approximate dates

### With Places
- Links to PlaceID
- Uses coordinates for API calls
- Requires place in repository

---

## Error Handling

### Invalid Weather Filtering

Automatically removes mentions with:
- Missing date
- Approximate date (not YYYY-MM-DD)
- Missing weather_description
- Missing original_text

```python
# Before filtering: 5 mentions
# After filtering: 2 mentions (3 invalid removed)
logger.info("Filtered 3 invalid weather mention(s)")
```

### API Failures

**Graceful degradation:**
- API failure doesn't stop extraction
- Mention saved without API data
- Logged as warning
- Can be retried later

**Common failures:**
- Network timeout
- Invalid coordinates
- Date out of range (pre-1940)
- Rate limit exceeded

### Retry Logic

**Default:** 3 attempts per sub-event

**Behavior:**
- First attempt uses cache
- Retries bypass cache
- Continues to next sub-event on failure

---

## Performance

### Caching

**Two-level caching:**
1. **Grok API responses:** `cache/api/weather/`
2. **Open-Meteo API responses:** Cached in weather files

**Clear caches:**
```bash
rm -rf cache/api/weather/*
```

### API Rate Limits

**Open-Meteo:**
- Free tier: ~10,000 requests/day
- Reasonable for batch processing
- No API key required

**Optimization:**
- Cache API responses
- Deduplicate date+place combinations
- Batch process chapters

### Processing Time

**Typical:** 10-20 seconds per sub-event

**Factors:**
- Number of weather mentions
- API response time
- Network latency
- Cache hit rate

---

## Examples

### Example: D-Day Weather

**Input Text:**
```
"The weather on 6 June was overcast with light rain and strong winds. 
Visibility was poor, delaying airborne operations."
```

**Output (weather file):**
```json
{
  "WeatherID": "01ULID...",
  "date": "1944-06-06",
  "DateID": "01ULID...",
  "location": {
    "place_name": "Normandy",
    "PlaceID": "01ULID...",
    "latitude": 49.35,
    "longitude": -0.50
  },
  "source_type": "hybrid",
  "extracted_data": {
    "description": "overcast with light rain and strong winds",
    "temperature": null,
    "temperature_unit": null,
    "measurement_system": null,
    "notable_impact": "Poor visibility delayed airborne operations",
    "original_text": "The weather on 6 June was overcast with light rain and strong winds",
    "book": "Cross-Channel Attack",
    "author": "Gordon A. Harrison"
  },
  "api_data": {
    "provider": "open-meteo",
    "retrieved_at": "2026-03-22T...",
    "temperature_max_c": 16.2,
    "temperature_min_c": 12.8,
    "precipitation_mm": 4.8,
    "windspeed_max_kmh": 23.2,
    "cloud_cover_percent": 91,
    "raw_response": {}
  },
  "event_mentions": [
    {
      "MentionID": "01ULID...",
      "Event_Name": "Operation Overlord",
      "EventID": "01ULID...",
      "Sub_event_Name": "D-Day landings",
      "Sub_eventID": "01ULID...",
      "book": "Cross-Channel Attack",
      "author": "Gordon A. Harrison",
      "series": "United States Army in World War II"
    }
  ]
}
```

---

## API Reference

### `extract_weather_central()`

Extract weather from an event file and add to the central repository (batched; one Grok
call per chapter). **This is the live entry point** (the older per-sub-event
`extract_weather` was removed).

**Signature:**
```python
def extract_weather_central(
    event_file: Path,
    weather_dir: Path,
    grok_client: GrokClient,
    places_dir: Optional[Path] = None,
    parsed_file: Optional[Path] = None,
    fetch_api: bool = False,
    max_retries: int = 3,
) -> Optional[Path]
```

**Parameters:**
- `event_file` (Path): `*-event.json` file.
- `weather_dir` (Path): central weather directory (`output/weather/`).
- `grok_client` (GrokClient): initialized client.
- `places_dir` (Path, optional): places repo for alias-aware PlaceID + coordinate lookup.
- `parsed_file` (Path, optional): parsed file for book metadata.
- `fetch_api` (bool): fetch Open-Meteo historical data (default False).
- `max_retries` (int): retries per batch call.

### NOAA historical enrichment — `enrich_weather_with_noaa()`

Phase-3 pass (`src/enrichment/noaa_weather.py`, wired in `phase3_enrich_data.py`): for each
weather file with real coordinates and a date ≥ 1940, finds the nearest NOAA station and
attaches observed data under `noaa_observed`. Skips already-enriched files and
null/unresolved coordinates. (Open-Meteo `api_data` is reanalysis; `noaa_observed` is
station-observed — both may be present.)

---

## Troubleshooting

### No weather being extracted

**Check:**
1. Weather enabled in config: `weather.enabled: true`
2. Event file has weather mentions in text
3. Dates are exact (YYYY-MM-DD format)
4. API key not required for Open-Meteo

### API data not fetching

**Check:**
1. `fetch_api_data: true` in config
2. Places exist in repository (for coordinates)
3. Network connectivity
4. API timeout setting (increase if needed)
5. Check logs for API errors

### Approximate dates filtered

**Expected behavior:**
```
WARNING - Filtered weather mention with approximate date: early-1944-06
```

**Solution:** Only exact dates supported. This is by design.

---

## Related Documentation

- [Events Extraction](../events/README.md)
- [Dates Extraction](../dates/README.md)
- [Places Extraction](../places/README.md)
- [Configuration](../../core/CONFIGURATION.md)
- [Error Handling](../../core/error_handling.md)
