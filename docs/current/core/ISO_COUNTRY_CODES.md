# ISO Country Codes for WWII Data

## Overview

The pipeline uses ISO 3166-1 alpha-3 country codes (3-letter codes) for nationality and country of origin fields to ensure consistency and enable international data exchange.

## Fields Using ISO Codes

### People (src/extraction/people.py)
- `BiographicalProfile.nationality` - Person's nationality/citizenship (optional; not fabricated at extraction time, may be inferred by enrichment)
- `BiographicalProfile.nationality_served` - The power the person served under, when it differs from `nationality` (e.g. a Frenchman in the Waffen-SS: `FRA` / `DEU`)

### People Groups (src/schemas/groups_output.py, src/extraction/enrich_groups.py)
- `nationality` and `country_of_origin` - Country where the group originated. Phase 3 group enrichment asks Grok for an ISO alpha-3 `nationality` and copies it into `country_of_origin` (`enrich_groups.py:44,180`). The `PeopleGroup` Pydantic model in `src/schemas.py` is shadowed by the `src/schemas/` package and is not imported.

### Equipment (src/extraction/equipment.py)
- `country_of_origin` - Country that designed/manufactured the equipment (a captured M10 used by Germany is still `USA`); mentions carry a per-mention operator nationality

### Casualties (src/extraction/casualties.py)
- `nationality` - Nationality of who suffered the casualties

## Common WWII Country Codes

### Allied Powers
- **USA** - United States
- **GBR** - United Kingdom
- **FRA** - France
- **CAN** - Canada
- **AUS** - Australia
- **NZL** - New Zealand
- **IND** - India
- **POL** - Poland
- **NLD** - Netherlands
- **BEL** - Belgium
- **NOR** - Norway
- **GRC** - Greece
- **YUG** - Yugoslavia
- **CHN** - China
- **SUN** - Soviet Union (the code the pipeline uses for WWII: `equipment._normalize_origin` maps USSR/Soviet/Russia to `SUN`; `RUS` is the modern Russian Federation)

### Axis Powers
- **DEU** - Germany (Deutschland)
- **ITA** - Italy
- **JPN** - Japan
- **HUN** - Hungary
- **ROU** - Romania
- **BGR** - Bulgaria
- **FIN** - Finland

### Neutral/Other
- **CHE** - Switzerland
- **SWE** - Sweden
- **ESP** - Spain
- **PRT** - Portugal
- **IRL** - Ireland
- **TUR** - Turkey

## Examples

### People
```json
{
  "name": "Dwight D. Eisenhower",
  "biographical_profile": {
    "nationality": "USA"
  }
}
```

### People Groups
```json
{
  "group_name": "1st Infantry Division",
  "group_type": "military_unit",
  "country_of_origin": "USA"
}
```

### Equipment
```json
{
  "common_name": "Sherman tank",
  "technical_identifier": "M4",
  "country_of_origin": "USA"
}
```

## Migration

Older data files use full country names (e.g., "American", "German"). New extractions are prompted for ISO codes. Equipment origins are normalised in code: `equipment._normalize_origin` maps free text to a canonical code through `src.enrichment.award_sources.canonical_nationality` and falls back to the upper-cased input (a stated value is never dropped).

## Reference

Full list of ISO 3166-1 alpha-3 codes: https://en.wikipedia.org/wiki/ISO_3166-1_alpha-3

## Implementation

- **Schema definitions**: `src/schemas/people_output.py`, `groups_output.py`, `equipment_output.py`, `casualties_output.py` (all `["string", "null"]`, no pattern); Pydantic models in `src/extraction/people.py` and the other extractors
- **Prompts**: the field descriptions in `src/extraction/people.py`, `equipment.py`, `casualties.py` (`:398`), `enrich_groups.py` (`:44`) and `prompts/casualties.yaml` (`:85,96`) ask for ISO 3166-1 alpha-3
- **Validation**: none. Any string is accepted, so a stray full name can still appear.
- **Future**: Could add validation against the official ISO code list (plus the historic `SUN`)
