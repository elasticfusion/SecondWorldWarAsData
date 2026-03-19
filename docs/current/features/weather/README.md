# Weather Extraction

**Module:** `src/extraction/weather_central.py`  
**Status:** Optional (Disabled by default)  
**Last Updated:** 2026-03-19

---

## Overview

Weather extraction analyzes event files for weather mentions and enriches them with historical weather data from the **Open-Meteo API**. Data is stored in a central repository with links to dates and places.

**Key Features:**
- Extracts weather mentions from text
- Fetches historical weather data from Open-Meteo API
- Central repository (one file per date+place combination)
- Links to DateID and PlaceID
- Temperature, precipitation, wind, cloud cover
- Operational impact tracking

**Status:** Optional feature, disabled by default in `config.yaml`

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
  "WeatherID": "01KHYP2M4N6P8Q0R2S4T6V8W0X",
  "date": "1944-06-06",
  "place_name": "Normandy",
  "PlaceID": "01KHYP2N5P7Q9R1S3T5V7W9X1Z",
  "coordinates": {
    "latitude": 49.18,
    "longitude": -0.37
  },
  "country": "FRA",
  "api_data": {
    "temperature_2m_max": 18.5,
    "temperature_2m_min": 12.3,
    "precipitation_sum": 2.5,
    "windspeed_10m_max": 25.0,
    "cloud_cover_mean": 75.0,
    "source": "Open-Meteo Archive API",
    "fetched_at": "2026-03-13T09:40:00Z"
  },
  "event_mentions": [
    {
      "MentionID": "01KHYP2P6Q8R0S2T4V6W8X0Y2Z",
      "Event_Name": "Operation Overlord",
      "EventID": "01KHXNSE0W41DV7VV6PEMDJJ5H",
      "Sub_event_Name": "D-Day landings",
      "Sub_eventID": "01KHXNSE0WX99GG0CB53CD2242",
      "book": "Cross-Channel Attack",
      "author": "Gordon A. Harrison",
      "series": "United States Army in World War II",
      "weather_description": "overcast with light rain",
      "temperature": 15.0,
      "temperature_unit": "celsius",
      "measurement_system": "metric",
      "notable_impact": "Poor visibility delayed airborne operations",
      "original_text": "The weather on 6 June was overcast with light rain",
      "context": null
    }
  ]
}
```

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
from src.extraction.weather_central import extract_weather

grok_client = GrokClient(cache_dir=Path("cache/api"))

event_file = Path("output/BreakoutAndPursuit/chapter1-event.json")
parsed_file = Path("output/BreakoutAndPursuit/chapter1-parsed.json")
weather_dir = Path("output/weather")
places_dir = Path("output/places")
dates_dir = Path("output/dates")

extract_weather(
    event_file=event_file,
    grok_client=grok_client,
    weather_dir=weather_dir,
    places_dir=places_dir,
    dates_dir=dates_dir,
    parsed_file=parsed_file,
    config={"fetch_api_data": True, "timeout": 30}
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

### Example 1: D-Day Weather

**Input Text:**
```
"The weather on 6 June was overcast with light rain and strong winds. 
Visibility was poor, delaying airborne operations."
```

**Output:**
```json
{
  "WeatherMentionID": "01KHYP2M4N6P8Q0R2S4T6V8W0X",
  "place_name": "Normandy",
  "PlaceMentionID": "01KHYP2N5P7Q9R1S3T5V7W9X1Z",
  "date": "1944-06-06",
  "DateMentionID": "01KHYP2P6Q8R0S2T4V6W8X0Y2Z",
  "weather_description": "overcast with light rain and strong winds",
  "temperature": null,
  "temperature_unit": null,
  "measurement_system": null,
  "notable_impact": "Poor visibility delayed airborne operations",
  "original_text": "The weather on 6 June was overcast with light rain and strong winds"
}
```

**API Data Added:**
```json
{
  "api_data": {
    "temperature_2m_max": 18.5,
    "temperature_2m_min": 12.3,
    "precipitation_sum": 2.5,
    "windspeed_10m_max": 25.0,
    "cloud_cover_mean": 75.0,
    "source": "Open-Meteo Archive API",
    "fetched_at": "2026-03-13T09:40:00Z"
  }
}
```

### Example 2: Temperature Mention

**Input Text:**
```
"On 15 July, temperatures reached 30°C, making conditions difficult for troops."
```

**Output:**
```json
{
  "WeatherMentionID": "01KHYP3N5Q7R9S1T3V5W7X9Y1Z",
  "place_name": "France",
  "date": "1944-07-15",
  "weather_description": "hot conditions",
  "temperature": 30.0,
  "temperature_unit": "celsius",
  "measurement_system": "metric",
  "notable_impact": "Difficult conditions for troops",
  "original_text": "temperatures reached 30°C"
}
```

---

## API Reference

### `extract_weather()`

Extract weather from event file and add to central repository.

**Signature:**
```python
def extract_weather(
    event_file: Path,
    grok_client: GrokClient,
    weather_dir: Path,
    places_dir: Path,
    dates_dir: Path,
    parsed_file: Optional[Path] = None,
    config: Optional[Dict[str, Any]] = None,
    max_retries: int = 3
) -> Optional[Path]
```

**Parameters:**
- `event_file` (Path): Path to `*-event.json` file
- `grok_client` (GrokClient): Initialized Grok API client
- `weather_dir` (Path): Central weather directory (`output/weather/`)
- `places_dir` (Path): Places directory for coordinate lookup
- `dates_dir` (Path): Dates directory for date linking
- `parsed_file` (Path, optional): Path to parsed file for book metadata
- `config` (dict, optional): Weather configuration options
- `max_retries` (int): Maximum retry attempts per sub-event (default: 3)

**Returns:**
- `Path`: Path to weather directory if weather was extracted
- `None`: If no weather was extracted

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
