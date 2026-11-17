# Logistics Extraction

**Module:** `src/extraction/logistics.py`  
**Status:** Enabled (`logistics.enabled: true` in `config.yaml`)  
**Last Updated:** 2026-11-17

---

## Overview

Logistics extraction analyzes the chapter's raw parsed text (3-paragraph windows) for supply chain, transportation, and resource management information. It tracks shortages, delays, disruptions, and their impacts on operations.

**Key Features:**
- Supply tracking (ammunition, fuel, food, medical, equipment)
- Delivery method classification
- Quantity analysis (required, available, shortage/excess)
- Impact tracking (organizations, people, places, equipment)
- Weather impact correlation
- Resolution tracking

**Status:** Switched by `logistics.enabled` (currently `true` in `config.yaml`)

---

## Architecture

### Data Flow

```
Event File (JSON)
    ↓
Parsed chapter text → 3-paragraph windows (src/extraction/windows.py)
One Grok request per window (_ask_logistics, chunk_size=1)
    ↓
Response: {window id: {logistics: [items]}, ...}
    ↓
windows.attribute_found: settle each quote, assign the real Sub-eventID
(unsupported quote → dropped)
    ↓
For each item (_save_extraction):
    ↓
Statement dedup (_statement_key vs files already on disk)
    ↓
_build_logistics_data: clamp enums, resolve cross-refs via SHARED libraries (see below)
    ↓
Logistics.model_validate()
    ↓
Save to output/logistics/ (write_json_with_lock) and rebuild index.json
```

## Cross-references (shared libraries)

Logistics links to six entities, each resolved via that category's **shared library** (not
bespoke string matching — the old exact→substring matcher produced false links like
"9th"→"9th Armored"):

- **organizations** → PeopleGroupID via `group_resolver` (canonical `unit_key`: structural
  number/service/arm/echelon match; fully-specified names win via exact-key, ambiguous
  decline to null).
- **places** → PlaceID via the alias-aware `places._build_place_name_index` + `_match_place_id`.
- **equipment** → EquipmentID via `equipment_disambiguation.resolve_designation` + index.
- **dates** → DateID (`temporal.DateID_start/end`) via the interval-aware `_resolve_date_link`
  (exact `date_start` OR resolved-interval overlap).
- **people** → PersonID by exact name (people are exact-by-design across the codebase).
- **weather** → WeatherID (description index).

Unresolved references keep the name with a `null` ID (never fabricated).

**Reverse link:** casualties cross-reference back to logistics — a casualty from a supply
failure links to a LogisticsID (explicit, or an inferred `co_occurring` candidate on shared
GroupID + overlapping date). See [Casualties](../casualties/README.md). The casualty↔logistics
join keys are GroupID + time (`logistics_resolver`), not place, although a logistics record does
carry `impacted_places[].PlaceID`.

**Windows:** discovery runs over the chapter's windows of the parsed text, one window per request, never the events' summaries (`_ask_logistics` → `windows.window_event_for`, `windows.attribute_found`). Without a parsed file the sub-events are asked as before. Entity linking and file creation remain per-item.

### Output Structure

```
output/logistics/
├── ammunition_supply_shortage_19440715_W0X1Y2Z3.json
├── fuel_delivery_delay_19440620_9Y1Z3A5B.json
├── general_transport_disruption_UNKNOWN_4P6R8T0V.json
├── index.json
└── ...
```

**Filename Format:** `{category}_{logistics_type}_{date_start without dashes, or UNKNOWN}_{last 8 chars of LogisticsID}.json`
(sanitized and contained by `_safe_entity_filename` / `_contained_entity_path`; on failure the
file is `{LogisticsID}.json`). `index.json` maps each file stem to its file name; a stem collision logs
`OBSERV logistics.index_key_collision`.

**Re-runs do not duplicate.** `_save_extraction` skips a statement already on disk. The key
(`_statement_key`) is the shared statement identity (`entity_records.mention_identity`: sub-event,
`original_text`, category, type, `date_start`, description) plus category, type and date, so a quote
that holds several shortages ("lacking vehicles, supplies, ammunition") yields one record each.

---

## Data Structure

### Logistics File Schema

```json
{
  "LogisticsID": "01KHYP2M4N6P8Q0R2S4T6V8W0X",
  "logistics_type": "supply_shortage",
  "category": "ammunition",
  "description": "Critical shortage of 105mm howitzer shells",
  "severity": "critical",
  "temporal": {
    "date_start": "1944-07-15",
    "date_end": "1944-07-20",
    "date_type": "range",
    "DateID_start": "01KHYP2N5P7Q9R1S3T5V7W9X1Z",
    "DateID_end": "01KHYP2P6Q8R0S2T4V6W8X0Y2Z",
    "DateMentionID": "01KHYP2Q7R9S1T3V5W7X9Y1Z3A"
  },
  "quantity": {
    "required": 10000.0,
    "available": 3000.0,
    "unit": "rounds",
    "shortage": 7000.0,
    "excess": null
  },
  "delivery_method": "ground_transport",
  "status": "in_progress",
  "event_mentions": [
    {
      "EventMentionID": "01KHYP2R8S0T2V4W6X8Y0Z2B4C",
      "EventID": "01KHXNSE0W41DV7VV6PEMDJJ5H",
      "Sub_eventID": "01KHXNSE0WX99GG0CB53CD2242",
      "paragraph_numbers": [15, 16, 17],
      "context": "Artillery units reported critical ammunition shortages"
    }
  ],
  "impacted_organizations": [
    {
      "PeopleGroupID": "01KHYP2S9T1V3W5X7Y9Z1B3D5E",
      "group_name": "VII Corps Artillery",
      "impact_description": "Unable to provide sustained fire support"
    }
  ],
  "impacted_places": [
    {
      "PlaceID": "01KHYP2T0V2W4X6Y8Z0B2D4F6G",
      "place_name": "Saint-Lô",
      "country": "FRA",
      "impact_description": "Offensive delayed due to ammunition shortage"
    }
  ],
  "original_text": "verbatim source passage",
  "weather_impact": {
    "WeatherID": "01KHYP2V1W3X5Y7Z9B1D3F5H7J",
    "impact_description": "Heavy rain delayed supply convoys",
    "severity": "high"
  },
  "resolution": {
    "resolved": true,
    "resolution_date": "1944-07-20",
    "resolution_description": "Emergency air delivery of ammunition",
    "resolution_method": "air_delivery"
  },
  "extracted_date": "2026-03-13T09:40:00Z"
}
```

---

## Features

### 1. Logistics Types

**Twelve types** (`_VALID_TYPES`; any other value is clamped to `supply_shortage`):

| Type | Description |
|------|-------------|
| `supply_shortage` | Insufficient supplies |
| `supply_excess` | Surplus supplies |
| `delivery_delay` | Delayed shipments |
| `transport_disruption` | Transport issues (bridge destroyed) |
| `capacity_constraint` | Capacity limits (port at max capacity) |
| `production_delay` | Delayed production |
| `supply_issue` | Routine issue of supplies (positive) |
| `embarkation` | Loading / embarkation (positive) |
| `marshaling` | Marshaling of units or materiel (positive) |
| `movement` | Routine movement (positive) |
| `reinforcement` | Reinforcement arriving (positive) |
| `resupply` | Resupply delivered (positive) |

The six positive types (`_POSITIVE_TYPES`) default to severity `low` when none is given; the
others default to `medium`. `planning_requirement`, listed in earlier versions of this doc, is not
accepted.

### 2. Supply Categories

**Fifteen categories** (`_VALID_CATEGORIES`; anything else is clamped to `general`):

- `ammunition`, `fuel`, `food`, `medical`, `equipment`, `personnel`, `general`
- `vehicles`, `supplies`, `artillery`, `armored_support`, `heavy_weapons`, `reinforcements`,
  `maps`, `transport`

### 3. Severity Levels

**Four levels:**

- `critical` - Immediate operational impact
- `high` - Significant impact
- `medium` - Moderate impact
- `low` - Minor impact

### 4. Quantity Tracking

**Detailed quantity information:**

```json
{
  "required": 10000.0,
  "available": 3000.0,
  "unit": "rounds",
  "shortage": 7000.0,
  "excess": null
}
```

All five fields come from the model's answer (`QuantityInfo`); the code does not compute
`shortage` or `excess` itself.

### 5. Delivery Methods

**Six transport types** (prompt vocabulary; `delivery_method` is stored as the model gave it, not clamped):

- `sea_transport` - Ships, landing craft
- `air_delivery` - Aircraft, airdrops
- `ground_transport` - Trucks, convoys
- `rail` - Railway transport
- `pipeline` - Fuel pipelines
- `mixed` - Multiple methods

### 6. Status Tracking

**Four status values:**

- `unresolved` - Issue not addressed
- `in_progress` - Being addressed
- `resolved` - Issue resolved
- `worsened` - Situation deteriorated

### 7. Impact Tracking

**Links to affected entities** (see Cross-references): organizations, people, places,
equipment and weather. The weather link (`_build_weather_impact`) needs the model's `weather`
text to equal an indexed weather description after normalization, or contain it as a whole phrase
(indexed description of at least 4 characters); otherwise `OBSERV logistics.weather_unmatched`.

### 8. Resolution Tracking

**Tracks how issues were resolved:**

```json
{
  "resolved": true,
  "resolution_date": "1944-07-20",
  "resolution_description": "Emergency air delivery",
  "resolution_method": "air_delivery"
}
```

---

## Configuration

### Enable Logistics Extraction

```yaml
# config.yaml
logistics:
  enabled: true  # Enable logistics extraction
```

**Note:** `logistics.enabled` is the only option. Window size comes from `extraction.window_paragraphs`
(env `WINDOW_PARAGRAPHS`).

---

## Usage

### Run Phase 2

```bash
python3 phase2_extract.py
```

`_extract_optional_entities` runs logistics after weather and equipment and before casualties
and supplemental. An event file already marked processed for `logistics` is skipped unless
`should_reprocess("logistics")`.

### Programmatic

```python
from pathlib import Path
from src.grok_client import GrokClient
from src.extraction.logistics import extract_logistics_from_event

grok_client = GrokClient(Path("cache/api"))
result = extract_logistics_from_event(
    event_file=Path("output/content/<book>/chapter1-event.json"),
    output_root=Path("output"),   # entity indexes are read from output_root/{people,people_groups,places,equipment,weather,dates}
    grok_client=grok_client,
)
```

---

## Output Files

### Logistics Files

**Location:** `output/logistics/{category}_{logistics_type}_{date}_{ID8}.json`

**Examples:**
- `ammunition_supply_shortage_19440715_W0X1Y2Z3.json`
- `fuel_delivery_delay_19440620_9Y1Z3A5B.json`

**No central repository** - Each logistics issue is a separate file.

---

## Integration

### With Events
- Reads event files
- Extracts logistics from the raw paragraph windows and attributes each item to a sub-event
- Links via EventID and Sub-eventID

### With People Groups
- Links to affected military units
- Tracks organizational impact

### With Places
- Links to affected locations
- Tracks geographic impact

### With Equipment
- Links to affected equipment
- Tracks equipment-related issues

### With Weather
- Links to weather impacts
- Correlates weather with disruptions

### With Dates
- Links to temporal information
- Tracks date ranges

---

## Error Handling

- Unresolved entity references keep the name with a null ID (`_link_*`).
- A save failure (validation or write) logs `OBSERV logistics.save_failed` and does not mark the key
  as seen, so a later identical extraction is still saved.
- A prompt-render failure returns `{}` with `OBSERV logistics.prompt_build_failed` and makes no API call.
- Chunks that truncate are halved and re-asked (`extract_with_chunk_halving`); window runs use
  `chunk_size=1`.
- `observe_yield("logistics", ...)` records the per-event expected-yield baseline.

---

## Performance

Responses are cached by `GrokClient` under cache type `logistics`. Phase 2 collects window requests
for the xAI batch rounds like the other extractors (see
[Batch Processing](../batch_processing/README.md)).

---

## Examples

### Example 1: Ammunition Shortage

**Input Text:**
```
"By 15 July, VII Corps Artillery reported a critical shortage of 105mm 
howitzer shells. Only 3,000 rounds remained of the 10,000 required for 
the planned offensive at Saint-Lô."
```

**Output:**
```json
{
  "LogisticsID": "01KHYP2M4N6P8Q0R2S4T6V8W0X",
  "logistics_type": "supply_shortage",
  "category": "ammunition",
  "description": "Critical shortage of 105mm howitzer shells",
  "severity": "critical",
  "quantity": {
    "required": 10000.0,
    "available": 3000.0,
    "unit": "rounds",
    "shortage": 7000.0
  },
  "temporal": {
    "date_start": "1944-07-15",
    "date_type": "specific"
  },
  "impacted_organizations": [
    {
      "group_name": "VII Corps Artillery",
      "impact_description": "Unable to support planned offensive"
    }
  ],
  "impacted_places": [
    {
      "place_name": "Saint-Lô",
      "impact_description": "Offensive delayed"
    }
  ]
}
```

### Example 2: Fuel Delivery Delay

**Input Text:**
```
"Heavy rain on 20 June delayed fuel convoys by three days. The 2nd Armored 
Division was forced to halt operations due to fuel shortages."
```

**Output:**
```json
{
  "LogisticsID": "01KHYP3N5Q7R9S1T3V5W7X9Y1Z",
  "logistics_type": "delivery_delay",
  "category": "fuel",
  "description": "Fuel convoy delayed by heavy rain",
  "severity": "high",
  "temporal": {
    "date_start": "1944-06-20",
    "date_type": "specific"
  },
  "delivery_method": "ground_transport",
  "status": "unresolved",
  "impacted_organizations": [
    {
      "group_name": "2nd Armored Division",
      "impact_description": "Forced to halt operations"
    }
  ],
  "weather_impact": {
    "impact_description": "Heavy rain delayed convoys",
    "severity": "high"
  }
}
```

---

## API Reference

### `extract_logistics_from_event()`

**Signature:**
```python
@for_chapter_book
def extract_logistics_from_event(
    event_file: Path,
    output_root: Path,
    grok_client: Optional[GrokClient] = None,
) -> Optional[Path]
```

**Returns:** `output_root/logistics` when at least one record was written, else `None` (also `None`
when no Grok client is passed).

---

## Troubleshooting

### No logistics being extracted

**Check:**
1. Logistics enabled in config: `logistics.enabled: true`
2. Event file has logistics mentions in text
3. API key is set
4. Check logs for extraction attempts

### Entity links not working

**Check:**
1. Entity repositories exist (people, places, equipment, weather)
2. Entities extracted before logistics
3. Entity names match between extractions (an unmatched name keeps a null ID, no warning is logged)

### Quantities not calculating

**Check:**
Nothing calculates `shortage` or `excess`; check the model's answer in the cache or `quantity` in the record.

---

## Limitations

**Experimental Status:**
- Schema may change
- Entity linking may be incomplete
- Quantity extraction may be imprecise
- Resolution tracking may be inconsistent

**Recommendations:**
- Review extracted data manually
- Verify entity links
- Validate quantities
- Use for research, not production

---

## Related Documentation

- [Events Extraction](../events/README.md)
- [People Groups](../people_groups/groups.md)
- [Places Extraction](../places/README.md)
- [Equipment Extraction](../equipment/README.md)
- [Weather Extraction](../weather/README.md)
- [Configuration](../../core/CONFIGURATION.md)

## Phase 3 Enrichment

**None by design.** Logistics records are extracted and cross-referenced in Phase 2; they
receive no external enrichment in Phase 3. See
[Phase 3 Enrichment](../../core/PHASE3_ENRICHMENT.md).
