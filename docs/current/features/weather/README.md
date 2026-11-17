# Weather Extraction

**Module:** `src/extraction/weather_central.py`  
**Status:** Optional, enabled in `config.yaml` (`weather.enabled: true`)  
**Last Updated:** 2026-11-17

---

## Overview

Weather extraction analyzes the chapter's raw parsed text (3-paragraph windows) for weather mentions and (optionally) enriches them
with historical data from **Open-Meteo** (reanalysis) and **NOAA** (station-observed). Data
is stored in a central repository with links to dates and places.

**Key Features:**
- Extracts weather mentions from text into an `extracted_data` object
- Optional Open-Meteo historical fetch into `api_data`; NOAA observed into `noaa_observed`
- Central repository (one file per date+place combination)
- `DateID` resolved from the dates directory (not LLM-provided)
- `location` with `PlaceID` + coordinates, resolved via the places **alias-aware** index
  with bounded matching; `null` coordinates when ungeocoded (no `0.0` placeholder)
- `observation_type` (`observed` default, `forecast`, `planning_requirement`): a forecast or a
  requirement ("a low tide at dawn was needed") is never stored as observed weather
- Condition fields (schema 2.25): `wind_text`, `wind_speed_low/high/unit`, `wind_direction`,
  `sea_state_text`, `visibility_text`, `cloud_text`, `tide_text`, `light_text`
- `source_type`: `extracted`, `api_only`, or `hybrid`
- `temperature_unit` normalized to the schema enum `C`/`F`
- Operational impact tracking

**Status:** Optional feature, switched by `weather.enabled` (currently `true` in `config.yaml`)

---

## Known gaps / follow-ups

_None currently open._

### Resolved
- **Single weather prompt file** — the dead `prompts/weather.yaml` (which carried a
  legacy nested-object temperature shape, `{"value":15,"unit":"celsius"}`, contradicting the
  enforced flat `temperature` + `temperature_unit: C/F`) has been removed. The live batch
  path now uses `prompts/weather_batch.yaml` for BOTH the user template and the system
  prompt (previously it mixed `weather_batch.yaml`'s template with `weather.yaml`'s system
  prompt).
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
Parsed chapter text → 3-paragraph windows (src/extraction/windows.py)
One Grok request per window (raw text only, no summary, no places list)
    ↓
Response: {window id: [mentions], ...}
    ↓
windows.attribute_found: settle each quote against the window text,
assign the real Sub-eventID (unsupported quote → dropped)
    ↓
For each mention:
    ↓
Normalize (_normalize_weather_mentions: drop a mention with no descriptive text at all)
    ↓
Find or Create Weather File (_find_or_create_weather):
   exact date (_exact_date; approximate/missing date -> "" = undated, KEPT)
   dedup key: {date}_pid_{PlaceID} (else normalized name), + _ev_{EventID} when undated,
   + _{observation_type} when not "observed"
   new file: resolve DateID (+ time_source), place coordinates, then Open-Meteo fetch if
   fetch_api and the place has real coordinates and the date is exact
    ↓
Add Event Mention
    ↓
Update Index
```

**DateID Resolution:** DateID is resolved by matching the date string against the dates directory, not trusting LLM-provided IDs.

**Windows:** `_ask_weather` builds the chapter's windows from the parsed file (`windows.chapter_windows`; size from `WINDOW_PARAGRAPHS` / `extraction.window_paragraphs`, default 3) and sends one window per request (`_batch_extract_weather(..., chunk_size=1)`). Each statement is attributed to the real sub-event that holds its quote. A statement with no quote keeps its description as `original_text` (`_describe_unquoted`). Without a parsed file the sub-events are asked as before. Post-processing (coordinate lookup, API fetch, file creation) remains per-mention. A chapter with failed windows is not marked processed, so the failed windows are retried on the next run.

### Central Repository Structure

```
output/weather/
├── index.json                           # Weather lookup index
├── 19440606_Normandy_01KHYP2M.json     # D-Day weather
├── 19440701_Paris_01KHYP3N.json        # July 1 weather
└── ...
```

**Filename Format:** `{YYYYMMDD or undated}_{PlaceName}_{last 8 chars of WeatherID}.json`
(sanitized; a pure-`{WeatherID}.json` name if containment blocks it). Records are located only
through `index.json`, never by recomputing the name. `.processed_events.json` in the same directory
marks event files already extracted.

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
    "precipitation_text": "light rain",
    "precipitation_amount": null,
    "precipitation_unit": null,
    "precipitation_type": "rain",
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
  `snowfall_mm: 81.3`) with full station provenance — `station_id` (GHCND code),
  `station_name` + `station_latitude`/`station_longitude` (the resolved station place),
  `station_distance_km` (how far the station is from the requested place), and
  `source`/`source_url`.
"3 inches of snow (narrative, Green Book)" and "81.3 mm observed (NOAA station X)" are
different claims from different authorities about the same event — both preserved and
attributable.

**NOAA is the unbiased quantitative baseline.** Combat narrative mentions weather only when
it is a *problem* (mud, grounding fog, frozen crossings) and is silent when weather is not a
factor — a clear 72 °F day goes unrecorded. Narrative silence therefore does NOT mean "no
weather"; it means "weather was not an obstacle." NOAA GHCND records every day's actual
conditions regardless of tactical relevance, so it (a) supplies the benign days the
narrative omits and (b) puts narrative claims in quantitative context. A `noaa_observed`
record can validly exist with no narrative weather mention. The full standard GHCND daily
element set is captured: `temperature_high/low/avg_c`, `precipitation_mm`, `snowfall_mm`,
`snow_depth_mm`, `wind_speed_ms`, `wind_gust_fastest2min/5sec_ms`,
`wind_dir_fastest2min/5sec_deg` (whichever the station reported that day; others `null`).

**All NOAA data is absorbed — nothing is dropped.** The request does not restrict
`datatypeid`, so NOAA returns every element the station reported. Common/queryable elements
are promoted to the named canonical fields above; **every** element (including ones not
pre-mapped — weather-type flags `WT**`, `WESD`, `PGTM`, soil/evaporation elements, etc.) is
also preserved verbatim in `noaa_observed.raw_elements`, keyed by its raw GHCND datatype
code. New GHCND elements are captured automatically without a schema change.

**Station provenance — the GHCND code AND the resolved place are both preserved.** The
nearest station is often not the requested place (e.g. a St. Vith record may be sourced from
an Antwerp station ~140 km away). The record keeps the opaque `station_id`
(`GHCND:BE000006447`) **and** the station resolved to a readable place — `station_name`
(e.g. `ANTWERPEN/DEURNE, BE`) + `station_latitude`/`station_longitude` — plus
`station_distance_km`, the straight-line distance from the requested place to the station,
so a reader can judge how representative the observation is. Distance uses the shared
great-circle helper `src/utils/geo.haversine_km` (one implementation, also used by place
deduplication).

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

### 3. Exact Dates Only for the API

`_exact_date` accepts only `YYYY-MM-DD`. A mention with an approximate, seasonal or missing date
is **kept as an undated record** (`date: ""`, one record per event, so statements of different events
never share a file), not dropped. Only exact-date records get an Open-Meteo fetch, a DateID and a
NOAA lookup, because the archive APIs need a day.
(`_filter_invalid_weather` / `_is_valid_weather_mention`, which rejected such mentions, remain in
the module but nothing calls them.)

### 4. Place Coordinate Lookup

**Automatic coordinate resolution** (`_lookup_coordinates`): the place name is matched against the
places alias-aware index (exact, whole-word containment, `SequenceMatcher >= 0.88`); the LLM-provided
`PlaceMentionID` is not used as an ID. Coordinates come from the matched place record; a `0.0/0.0`
placeholder counts as not geocoded.

**Fallback:** if the place is not found, `PlaceID` and coordinates are null, the Open-Meteo fetch is
skipped, and the mention is still saved.

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

Only two keys are read by code (`phase2_extract._extract_weather`):

| Option | `config.yaml` | Description |
|--------|---------------|-------------|
| `enabled` | `true` | Enable weather extraction |
| `fetch_api_data` | `true` | Fetch Open-Meteo historical data (the function default is False) |

`api_provider`, `cache_responses`, `only_precise_dates` and `timeout` appear in `config.yaml` but no
code reads them: Open-Meteo is the only provider, exact-date handling is fixed (see above), and the
API timeout is the `_fetch_weather_from_api` default of 30 s. Phase 3 NOAA enrichment is gated by
`api.noaa_api_token`.

---

## Usage

### Run Phase 2

```bash
python3 phase2_extract.py
```

Weather runs first among the optional entity extractors (`_extract_optional_entities`: weather, equipment, logistics, casualties, supplemental), once per event file; an event already in `output/weather/.processed_events.json` is skipped unless `should_reprocess("weather")`.

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
    places_dir=Path("output/places"),   # output/dates is read from places_dir.parent
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
- Reads event files for the real Sub-events
- Extracts weather from the raw paragraph windows and attributes each to a sub-event
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

### Mention filtering

`_normalize_weather_mentions` drops a mention with no descriptive text in any content field
(`weather_description`, `description`, `condition`, `original_text`, `notable_impact`, precipitation,
wind, sea-state, visibility, cloud, tide or light text) and copies the first one found into
`weather_description`. Date problems do not drop a mention (see "Exact Dates Only"). A chapter
needs book and author provenance (`require_book_provenance`); without it the event raises.

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

### Failed windows

Failed window calls are counted; the answered windows are saved and the event is NOT marked processed.
The function then raises `WeatherCallFailed`, so the failed windows are retried on the next run (a
re-run de-duplicates the answered ones by statement).

---

## Performance

### Caching

**Grok responses** are cached by `GrokClient` under cache type `weather`. The Open-Meteo response is
stored in the weather file's `api_data` and is not fetched again for an existing file that already has it.

### API use

Open-Meteo needs no key. One request per new weather file with an exact date and real coordinates;
date+place deduplication keeps that count down.

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
    "precipitation_text": "light rain",
    "precipitation_amount": null,
    "precipitation_unit": null,
    "precipitation_type": "rain",
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

Extract weather from an event file and add to the central repository (one Grok request per
3-paragraph window). **This is the live entry point** (the older per-sub-event
`extract_weather` was removed).

**Signature:**
```python
def extract_weather_central(
    event_file: Path,
    weather_dir: Path,
    grok_client: GrokClient,
    places_dir: Path,
    parsed_file: Optional[Path] = None,
    fetch_api: bool = False,
    max_retries: int = 3,
) -> Optional[Path]
```

Returns `weather_dir` when at least one mention was written, else `None`.

**Parameters:**
- `event_file` (Path): `*-event.json` file.
- `weather_dir` (Path): central weather directory (`output/weather/`).
- `grok_client` (GrokClient): initialized client.
- `places_dir` (Path): places repo for alias-aware PlaceID + coordinate lookup; `output/dates` is taken as `places_dir.parent / "dates"`.
- `parsed_file` (Path, optional): parsed file for book metadata.
- `fetch_api` (bool): fetch Open-Meteo historical data (default False).
- `max_retries` (int): retries per batch call.

### NOAA historical enrichment — `enrich_weather_with_noaa()`

Phase-3 pass (`src/enrichment/noaa_weather.py`, wired in `phase3_enrich_data.py`): for each
weather file with real coordinates and a date ≥ 1940, finds the nearest NOAA station and
attaches observed data under `noaa_observed`. The nearest station is resolved to its code +
name + coordinates, and the distance from the requested place is recorded
(`station_distance_km`). The full standard GHCND daily element set is promoted to named
fields and **every** returned element is also kept in `raw_elements` (nothing dropped).
Skips already-enriched files, files with a missing or zero latitude or longitude, and files dated
before 1940-01-01 or undated. The station search is a box of +/-0.5 degree, then +/-1.0, around the place
(`find_nearest_station`; the first station returned is used, not ranked by distance). Rate limit 5
requests/second; a 429 pauses 60 s. A NOAA outage is not negative-cached. One NOAA data call per
`(station, date)` — multiple places sharing a station (and reruns) hit the disk cache, not
the API. (Open-Meteo `api_data` is reanalysis; `noaa_observed` is station-observed — both
may be present.)

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

### Approximate dates

An approximate date produces an undated weather record (`date: ""`) with no DateID, Open-Meteo data or
NOAA data. Only exact `YYYY-MM-DD` dates are enriched.

---

## Related Documentation

- [Events Extraction](../events/README.md)
- [Dates Extraction](../dates/README.md)
- [Places Extraction](../places/README.md)
- [Configuration](../../core/CONFIGURATION.md)
- [Error Handling (archived 2026-03 spec)](../../../archive/contextmanagement/Specs/error_handling.md)

## Phase 3 Enrichment

**Step 6/6 (runs only when `api.noaa_api_token` is non-empty and `output/weather/` exists).
`config.yaml` ships `api.noaa_api_token: ""`, so NOAA enrichment never runs and logs nothing
until a token is set; there is no alert for the missing token.** NOAA GHCND station-observed data supplements
the Phase-2 Open-Meteo reanalysis (`enrich_weather_with_noaa`); full element set + raw
passthrough + station provenance. See [Phase 3 Enrichment](../../core/PHASE3_ENRICHMENT.md).
