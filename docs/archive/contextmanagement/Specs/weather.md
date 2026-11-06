# Weather Extraction - Central Repository

**Version:** 2.0.0  
**Status:** ✅ Implemented  
**Last Updated:** 2026-02-24

---

## Overview

Weather extraction uses a **central repository pattern** where each unique date+location combination has its own file with both extracted mentions and API-retrieved data.

---

## Architecture

### Central Repository Structure

```
output/weather/
├── 19440606_Normandy_01ULID.json       # D-Day weather
├── 19440625_Caen_01ULID.json           # Caen weather  
├── 19440701_Cherbourg_01ULID.json      # Cherbourg weather
├── index.json                           # date+location → filename
└── api_cache/                           # Cached API responses
    └── openmeteo_19440606_49.35_-0.50.json
```

### Index Format

```json
{
  "1944-06-06_Normandy": "19440606_Normandy_01ULID.json",
  "1944-06-25_Caen": "19440625_Caen_01ULID.json",
  "1944-07-01_Cherbourg": "19440701_Cherbourg_01ULID.json"
}
```

---

## Weather File Schema

### Structure

```json
{
  "WeatherID": "01ULID",
  "date": "1944-06-06",
  "DateID": "01ULID",
  "location": {
    "place_name": "Normandy",
    "PlaceID": "01ULID",
    "latitude": 49.35,
    "longitude": -0.50
  },
  "source_type": "extracted|api|hybrid",
  "extracted_data": {
    "description": "Clear skies with light fog in early morning",
    "temperature": 15,
    "temperature_unit": "celsius",
    "measurement_system": "metric",
    "notable_impact": "Fog delayed H-Hour by 30 minutes",
    "original_text": "The morning fog lifted by 0600 hours",
    "book": "Cross-Channel Attack",
    "author": "Gordon Harrison"
  },
  "api_data": {
    "provider": "open-meteo",
    "retrieved_at": "2026-02-24T09:00:00Z",
    "temperature_max_c": 16.2,
    "temperature_min_c": 12.8,
    "precipitation_mm": 0.0,
    "windspeed_max_kmh": 15.3,
    "cloud_cover_percent": 45,
    "raw_response": {}
  },
  "event_mentions": [
    {
      "MentionID": "01ULID",
      "Event_Name": "D-Day Landings",
      "EventID": "01ULID",
      "Sub_event_Name": "Utah Beach assault",
      "Sub_eventID": "01ULID",
      "book": "Cross-Channel Attack",
      "author": "Gordon Harrison",
      "series": "United States Army in World War II"
    }
  ]
}
```

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `WeatherID` | string | 26-character ULID |
| `date` | string | ISO date (YYYY-MM-DD) |
| `DateID` | string\|null | Link to central dates repository |
| `location` | object | Place information |
| `location.place_name` | string | Place name |
| `location.PlaceID` | string\|null | Link to central places repository |
| `location.latitude` | float | Latitude for API queries |
| `location.longitude` | float | Longitude for API queries |
| `source_type` | string | `extracted`, `api`, or `hybrid` |
| `extracted_data` | object\|null | Weather mentions from documents |
| `api_data` | object\|null | Weather data from API |
| `event_mentions` | array | Events referencing this weather |

---

## Features

### 1. Central Repository

**One file per unique date+location:**
- Deduplicates weather data across documents
- Combines extracted mentions with API data
- Tracks all event mentions for each weather condition

### 2. Hybrid Data Sources

**Extracted from documents:**
- Weather descriptions from historical text
- Notable operational impacts
- Original source attribution

**Retrieved from API:**
- Actual historical weather data
- Temperature, precipitation, wind
- Cloud cover, pressure

### 3. API Integration

**Open-Meteo Historical Archive:**
- Coverage: 1940-present
- Free, no API key required
- Global coverage
- Reanalysis data fills gaps

**Configuration:**
```yaml
weather:
  enabled: true
  fetch_api_data: true
  api_provider: "open-meteo"
  cache_responses: true
  only_precise_dates: true
```

### 4. Precise Date Filtering

**Only extract weather for exact dates:**
- ✅ "June 6, 1944" → Extract
- ✅ "6 June 1944" → Extract
- ❌ "early June 1944" → Skip
- ❌ "mid-summer 1944" → Skip

**Rationale:** API requires exact dates; approximate dates produce meaningless results.

### 5. Deduplication

**Weather conditions are deduplicated by:**
- Date (exact match)
- Location (same place name or nearby coordinates)

**Example:**
- "D-Day weather at Normandy" (Chapter 1)
- "Weather on June 6 at Omaha Beach" (Chapter 5)

→ Same weather file (Normandy region, June 6, 1944)

### 6. Event Mention Tracking

**Each weather file tracks:**
- All events that reference this weather
- Book/chapter source
- Sub-event context
- Prevents duplicate mentions (same Sub_eventID)

---

## Extraction Process

### 1. Extract Weather Mentions

For each sub-event:
- Parse text for weather descriptions
- Link to existing DateID and PlaceID
- Extract temperature, conditions, impact
- Generate WeatherMentionID

### 2. Find or Create Weather File

For each mention:
- Normalize date+location key
- Check index for existing file
- Create new file if needed

### 3. Fetch API Data (Optional)

### 3. Fetch API Data (Optional)

If `fetch_api_data: true`:
- Look up coordinates from places repository (PlaceID or fuzzy match)
- Query Open-Meteo with date+coordinates
- Cache API response
- Add to weather file
- Update existing files if coordinates were missing

### 4. Add Event Mention

- Check for duplicate (same Sub_eventID)
- Append new mention if unique
- Save updated file

### 5. Update Index

Save index mapping date+location to filenames.

### 6. Update Existing Files

On subsequent runs:
- Check existing weather files for missing coordinates
- Look up from places repository if PlaceID available
- Fetch API data if enabled and coordinates now available
- Update files in place (idempotent)

---

## Coordinate Lookup

### Two-Tier Strategy

**Option 1: PlaceID Lookup (Preferred)**
- Use PlaceMentionID from weather mention
- Look up in places repository index
- Load place file and extract coordinates

**Option 2: Fuzzy Match Fallback**
- If PlaceID is null or not found
- Search places index by place name (case-insensitive substring match)
- Use first matching place with valid coordinates

**Example:**
```python
# Weather mention has PlaceID
mention = {"place_name": "Caen", "PlaceMentionID": "01KJ679253..."}
# → Look up directly in places/index.json

# Weather mention missing PlaceID
mention = {"place_name": "Caen", "PlaceMentionID": null}
# → Search for "caen" in places index keys
# → Find "Caen_01KJ6792.json"
# → Load and extract coordinates (49.18, -0.38)
```

**Logging:**
- "Found coordinates via PlaceID: 01KJ6792"
- "Found coordinates via fuzzy match: Caen -> Caen_01KJ6792"

---

## API Integration

### Open-Meteo Historical Archive

**Endpoint:**
```
https://archive-api.open-meteo.com/v1/archive
```

**Parameters:**
```python
{
    "latitude": 49.35,
    "longitude": -0.50,
    "start_date": "1944-06-06",
    "end_date": "1944-06-06",
    "daily": [
        "temperature_2m_max",
        "temperature_2m_min",
        "precipitation_sum",
        "windspeed_10m_max",
        "cloud_cover_mean"
    ]
}
```

**Coverage:**
- 1940-present (ERA5 reanalysis)
- Global coverage
- Free, no API key required

**Response Caching:**
- Cache by date+coordinates
- Avoid redundant API calls
- Store in `output/weather/api_cache/`

**Limitations:**
- Reanalysis data (not actual observations)
- Single point (not bounding box)
- May not match historical records exactly

---

## Configuration

### Enable Weather Extraction

```yaml
# config.yaml
weather:
  enabled: true                    # Enable weather extraction
  fetch_api_data: true             # Fetch from Open-Meteo
  api_provider: "open-meteo"       # API provider
  cache_responses: true            # Cache API responses
  only_precise_dates: true         # Skip approximate dates
  timeout: 30                      # API timeout (seconds)
```

### Disable API Fetching

```yaml
weather:
  enabled: true
  fetch_api_data: false            # Extract mentions only
```

---

## Usage

### Extract Weather

```python
from src.extraction.weather_central import extract_weather_central

weather_dir = extract_weather_central(
    event_file=Path("output/book/chapter1-event.json"),
    dates_dir=Path("output/dates"),
    places_dir=Path("output/places"),
    weather_dir=Path("output/weather"),
    grok_client=grok_client,
    fetch_api=True
)
```

### Query Weather

```python
import json

# Load index
with open("output/weather/index.json") as f:
    index = json.load(f)

# Find D-Day weather
weather_file = index.get("1944-06-06_Normandy")
if weather_file:
    with open(f"output/weather/{weather_file}") as f:
        weather = json.load(f)
    
    print(f"Extracted: {weather['extracted_data']['description']}")
    print(f"API: {weather['api_data']['temperature_max_c']}°C")
```

---

## File Naming Convention

**Format:** `YYYYMMDD_PlaceName_ULID8.json`

**Examples:**
- `19440606_Normandy_01KHYP2M.json`
- `19440625_Caen_01KJ6792.json`
- `19440701_Cherbourg_01KHYP2M.json`

---

## Limitations

### 1. Approximate Dates Not Supported

**Skipped:**
- "early June 1944"
- "mid-summer 1944"
- "late 1944"

**Reason:** API requires exact dates.

### 2. Location Precision

**Challenge:** Historical text may reference regions, not specific coordinates.

**Solution:** Use representative point (city center, operation HQ).

### 3. API Coverage

**Open-Meteo limitations:**
- Reanalysis data (not actual observations)
- May not match historical records exactly
- Limited to single point (no bounding box)

### 4. Weather Station Gaps

**1940s Europe:**
- Many stations destroyed/offline during war
- Occupied territories have data gaps
- Reanalysis fills gaps but less accurate

---

## Validation

### Compare Extracted vs API

**Detect discrepancies:**
```python
extracted_temp = weather["extracted_data"]["temperature"]
api_temp = weather["api_data"]["temperature_max_c"]

if abs(extracted_temp - api_temp) > 5:
    logger.warning(f"Temperature mismatch: {extracted_temp} vs {api_temp}")
```

### Quality Checks

- Extracted description matches API conditions
- Temperature units converted correctly
- Notable impacts align with severe weather

---

## Future Enhancements

1. **Multiple API Providers** - NOAA, Visual Crossing as fallbacks
2. **Weather Station Data** - Link to actual observation stations
3. **Validation Reports** - Compare extracted vs API systematically
4. **Weather Maps** - Generate historical weather maps
5. **Impact Analysis** - Correlate weather with operation outcomes
6. **Seasonal Patterns** - Analyze weather trends across campaigns
7. **Bounding Box Queries** - Sample multiple points for regions

---

## Quality Assurance

### Code Quality Metrics

**File:** `src/extraction/weather_central.py`  
**Lines of Code:** 526  
**Last QA Run:** 2026-02-24

| Tool | Score | Status |
|------|-------|--------|
| **Pylint** | 9.78/10 | ✅ Pass |
| **Mypy** | 0 errors | ✅ Pass |
| **Black** | Formatted | ✅ Pass |
| **Bandit** | 0 issues | ✅ Pass |
| **Radon CC** | A-C (1-19) | ✅ Pass |
| **Radon MI** | A (33.78) | ✅ Pass |

### Complexity Analysis

**Functions by Complexity:**
- `_find_or_create_weather` - C (19) - Moderate (coordinate lookup + API + file updates)
- `extract_weather_central` - C (13) - Moderate (retry logic, loops)
- `_filter_invalid_weather` - C (12) - Moderate (validation)
- `_fix_invalid_ulids` - C (11) - Moderate (recursive)
- `create_weather_prompt` - B (7) - Low (places/dates extraction)
- `_add_event_mention` - B (6) - Low
- `_lookup_coordinates` - A (5) - Low (two-tier lookup)
- `_fetch_weather_from_api` - A (2) - Low
- `_normalize_weather_key` - A (1) - Low

**Assessment:** Production-ready. Moderate complexity justified by:
- Two-tier coordinate lookup (PlaceID → fuzzy match)
- File update logic for existing records
- Error handling patterns

### Error Handling Patterns Applied

1. ✅ Retry logic (3 attempts, cache bypass)
2. ✅ Null field filtering
3. ✅ ULID fixing
4. ✅ Prompt engineering
5. ✅ Graceful degradation
6. ✅ Metadata validation
7. ✅ Duplicate detection
8. ✅ Comprehensive logging
9. ✅ API error handling
10. ✅ Timeout handling
11. ✅ Cache-first strategy
12. ✅ Idempotent operations

---

## Related Documentation

- **Schema:** `contextmanagement/Specs/weather_v2_central.json`
- **Code:** `src/extraction/weather_central.py`
- **Dates:** `contextmanagement/Specs/dates.md`
- **Places:** `contextmanagement/Specs/places.md`
- **Error Handling:** `contextmanagement/Specs/error_handling.md`
- **Quality Assurance:** `contextmanagement/Specs/quality_assurance.md`

---

**Status:** ✅ Implemented and QA Verified