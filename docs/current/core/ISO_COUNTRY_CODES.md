# ISO Country Codes for WWII Data

## Overview

The pipeline uses ISO 3166-1 alpha-3 country codes (3-letter codes) for nationality and country of origin fields to ensure consistency and enable international data exchange.

## Fields Using ISO Codes

### People (src/extraction/people.py)
- `BiographicalProfile.nationality` - Person's nationality

### People Groups (src/extraction/people_groups.py)
- `PeopleGroup.country_of_origin` - Country where group originated

### Equipment (src/extraction/equipment.py)
- `EquipmentExtraction.country_of_origin` - Country that manufactured equipment

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
- **RUS** - Soviet Union (Russia)

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

Existing data files use full country names (e.g., "American", "German"). These will be gradually migrated to ISO codes as data is updated.

New extractions automatically use ISO codes.

## Reference

Full list of ISO 3166-1 alpha-3 codes: https://en.wikipedia.org/wiki/ISO_3166-1_alpha-3

## Implementation

- **Schema definitions**: `src/extraction/people.py`, `src/extraction/people_groups.py`, `src/extraction/equipment.py`, `src/schemas.py`
- **Prompts updated**: All extraction prompts specify ISO codes
- **Validation**: Pydantic models accept any string (no validation yet)
- **Future**: Could add validation against official ISO code list
