# Logistics Extraction

**Module:** `src/extraction/logistics.py`  
**Status:** Experimental (Disabled by default)  
**Last Updated:** 2026-03-13

---

## Overview

Logistics extraction analyzes event files for supply chain, transportation, and resource management information. It tracks shortages, delays, disruptions, and their impacts on operations.

**Key Features:**
- Supply tracking (ammunition, fuel, food, medical, equipment)
- Delivery method classification
- Quantity analysis (required, available, shortage/excess)
- Impact tracking (organizations, people, places, equipment)
- Weather impact correlation
- Resolution tracking

**Status:** Experimental feature, disabled by default in `config.yaml`

---

## Architecture

### Data Flow

```
Event File (JSON)
    ↓
For each Sub-event
    ↓
Extract Logistics Mentions (Grok)
    ↓
Link to Entities (People, Places, Equipment, Weather)
    ↓
Create Logistics File
    ↓
Save to output/logistics/
```

### Output Structure

```
output/logistics/
├── supply_shortage_ammunition_01KHYP2M.json
├── delivery_delay_fuel_01KHYP3N.json
├── transport_disruption_01KHYP4P.json
└── ...
```

**Filename Format:** `{type}_{category}_{ULID_prefix}.json`

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

**Six main types:**

| Type | Description | Example |
|------|-------------|---------|
| `supply_shortage` | Insufficient supplies | Ammunition shortage |
| `supply_excess` | Surplus supplies | Excess fuel stockpile |
| `delivery_delay` | Delayed shipments | Convoy delayed 3 days |
| `transport_disruption` | Transport issues | Bridge destroyed |
| `planning_requirement` | Future needs | Require 5000 tons fuel |
| `capacity_constraint` | Capacity limits | Port at max capacity |

### 2. Supply Categories

**Seven categories:**

- `ammunition` - Shells, bullets, explosives
- `fuel` - Gasoline, diesel, aviation fuel
- `food` - Rations, provisions
- `medical` - Medical supplies, equipment
- `equipment` - Vehicles, weapons, tools
- `personnel` - Troops, replacements
- `general` - Miscellaneous supplies

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

**Automatic calculation:**
- `shortage = required - available` (if available < required)
- `excess = available - required` (if available > required)

### 5. Delivery Methods

**Six transport types:**

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

**Links to affected entities:**

- **Organizations:** Military units affected
- **People:** Individuals impacted
- **Places:** Locations affected
- **Equipment:** Equipment impacted
- **Weather:** Weather-related impacts

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

**Note:** Currently only one configuration option. Feature is experimental.

---

## Usage

### Enable in Config

```bash
# Edit config.yaml
vim config.yaml

# Set logistics.enabled: true
```

### Run Phase 2

```bash
python3 phase2_extract.py
```

Logistics extraction runs automatically after equipment extraction.

### Programmatic

```python
from pathlib import Path
from src.grok_client import GrokClient
from src.extraction.logistics import extract_logistics

grok_client = GrokClient(cache_dir=Path("cache/api"))

event_file = Path("output/BreakoutAndPursuit/chapter1-event.json")
parsed_file = Path("output/BreakoutAndPursuit/chapter1-parsed.json")
output_dir = Path("output/logistics")

# Need entity directories for linking
people_dir = Path("output/people")
places_dir = Path("output/places")
equipment_dir = Path("output/equipment")
weather_dir = Path("output/weather")

extract_logistics(
    event_file=event_file,
    grok_client=grok_client,
    output_dir=output_dir,
    parsed_file=parsed_file,
    people_dir=people_dir,
    places_dir=places_dir,
    equipment_dir=equipment_dir,
    weather_dir=weather_dir
)
```

---

## Output Files

### Logistics Files

**Location:** `output/logistics/{type}_{category}_{ID}.json`

**Examples:**
- `supply_shortage_ammunition_01KHYP2M.json`
- `delivery_delay_fuel_01KHYP3N.json`
- `transport_disruption_general_01KHYP4P.json`

**No central repository** - Each logistics issue is a separate file.

---

## Integration

### With Events
- Reads event files
- Extracts logistics from sub-event text
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

### Missing Entity Links

**Graceful degradation:**
- If entity not found in repository, skip link
- Log warning
- Continue extraction

**Example:**
```
WARNING - Organization 'VII Corps' not found in people_groups repository
```

### Invalid Quantities

**Validation:**
- Quantities must be positive numbers
- Units must be specified
- Shortage/excess calculated automatically

### Retry Logic

**Default:** 3 attempts per sub-event

**Behavior:**
- First attempt uses cache
- Retries bypass cache
- Continues to next sub-event on failure

---

## Performance

### Caching

All API responses cached in `cache/api/logistics/`

**Clear cache:**
```bash
rm -rf cache/api/logistics/*
```

### Processing Time

**Typical:** 10-20 seconds per sub-event

**Factors:**
- Text complexity
- Number of logistics mentions
- Entity linking overhead
- API response time

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

### `extract_logistics()`

Extract logistics information from event file.

**Signature:**
```python
def extract_logistics(
    event_file: Path,
    grok_client: GrokClient,
    output_dir: Path,
    parsed_file: Optional[Path] = None,
    people_dir: Optional[Path] = None,
    places_dir: Optional[Path] = None,
    equipment_dir: Optional[Path] = None,
    weather_dir: Optional[Path] = None,
    max_retries: int = 3
) -> Optional[Path]
```

**Parameters:**
- `event_file` (Path): Path to `*-event.json` file
- `grok_client` (GrokClient): Initialized Grok API client
- `output_dir` (Path): Output directory (`output/logistics/`)
- `parsed_file` (Path, optional): Path to parsed file for book metadata
- `people_dir` (Path, optional): People groups directory for linking
- `places_dir` (Path, optional): Places directory for linking
- `equipment_dir` (Path, optional): Equipment directory for linking
- `weather_dir` (Path, optional): Weather directory for linking
- `max_retries` (int): Maximum retry attempts per sub-event (default: 3)

**Returns:**
- `Path`: Path to output directory if logistics were extracted
- `None`: If no logistics were extracted

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
3. Entity names match between extractions
4. Check logs for "not found in repository" warnings

### Quantities not calculating

**Check:**
1. Both `required` and `available` specified
2. Values are positive numbers
3. Unit is specified
4. Check logs for validation errors

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
- [People Groups](../people/groups.md)
- [Places Extraction](../places/README.md)
- [Equipment Extraction](../equipment/MILITARY_EQUIPMENT.md)
- [Weather Extraction](../weather/README.md)
- [Configuration](../../core/CONFIGURATION.md)
