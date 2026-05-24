# Prompt Review & Recommendations

**Date:** 2026-05-24  
**Scope:** All 9 YAML prompt files in `/prompts/`

---

## Overview

The prompts are well-structured with clear system roles, JSON schema examples, and extraction rules. However, several patterns are causing measurable data quality issues in the output. This review identifies specific changes ranked by impact on extraction accuracy.

---

## Prompt-by-Prompt Analysis

### 1. `weather.yaml` — Needs Major Revision

**Current issues:**
- Says "Link PlaceMentionID to available places when possible" but doesn't explain the format of `{places_section}` or instruct the LLM to copy IDs verbatim
- No instruction to avoid creating duplicate weather entries for the same date+location
- Schema shows `PlaceMentionID` as a new ULID to generate, contradicting the "link to available" instruction

**Recommended changes:**

```yaml
prompt_template: |
  Extract weather mentions from this WWII sub-event text.

  Event: {event_name} (ID: {event_id})
  Sub-event: {sub_event_summary} (ID: {sub_event_id})

  Available places (COPY these IDs exactly — do NOT generate new ones):
  {places_section}

  Available dates (COPY these IDs exactly — do NOT generate new ones):
  {dates_section}

  Text:
  {text}

  Return JSON matching this structure:
  {schema}

rules:
  - Generate 26-character ULIDs using only: 0-9 A-H J-K M-N P-T V-Z
  - CRITICAL: Only extract exact dates (YYYY-MM-DD), skip approximate dates
  - CRITICAL: For PlaceMentionID, COPY the ID from "Available places" above that matches the place name. Do NOT generate a new ULID. If no match exists, use null.
  - CRITICAL: For DateMentionID, COPY the ID from "Available dates" above that matches the date. Do NOT generate a new ULID. If no match exists, use null.
  - If the same weather condition is described for the same date and location, extract it only ONCE
  - If no weather mentions found, return empty Weather_Mentions array
```

**Also fix the schema example** — change `PlaceMentionID` from `"01ULID..."` to show it should be copied:
```yaml
schema: |
  {
    "Weather_Mentions": [
      {
        "WeatherMentionID": "01ULID...",
        "place_name": "Normandy",
        "PlaceMentionID": "<COPY from Available places above, or null>",
        "date": "1944-06-06",
        "DateMentionID": "<COPY from Available dates above, or null>",
        ...
      }
    ]
  }
```

**Impact:** Fixes the 81% broken PlaceID cross-reference rate.

---

### 2. `casualties.yaml` — Needs Cross-Reference Instructions

**Current issues:**
- Provides `{entity_context}` with organization names but only shows first 10 entries and doesn't include GroupIDs
- No instruction to match organization names to available entities
- Schema example shows no `PeopleGroupID` field in `impacted_organizations`

**Recommended changes:**

Add `PeopleGroupID` to the schema:
```yaml
schema: |
  {
    "SUB_EVENT_ID": [
      {
        "CasualtyID": "01ULID...",
        "type": "casualties",
        "side": "allied",
        "description": "Heavy losses during assault on Hill 192",
        "count": {
          "killed": 45,
          "wounded": 120,
          "missing": 10,
          "captured": 0,
          "total": 175
        },
        "date_string": "1944-07-11",
        "impacted_organizations": [
          {"name": "2nd Infantry Division", "PeopleGroupID": "<COPY from Available entities, or null>", "nationality": "USA", "role": "attacking_force"}
        ],
        "impacted_people": [
          {"name": "Colonel Smith", "PersonID": "<COPY from Available entities, or null>", "casualty_type": "wounded"}
        ],
        "impacted_places": [{"name": "Hill 192", "PlaceID": "<COPY from Available entities, or null>"}]
      }
    ]
  }
```

Add a rule:
```yaml
rules:
  ...
  - For impacted_organizations, match the name to "Available entities > Organizations" and COPY the ID. If no match, use null.
  - For impacted_people, match the name to "Available entities > People" and COPY the ID. If no match, use null.
```

**Also requires code change:** The `entity_context` builder in `casualties.py` currently only passes the first 10 keys without IDs. It should pass `name: ID` pairs for all available entities (or at least the full list).

**Impact:** Fixes the 86% null PeopleGroupID rate.

---

### 3. `logistics.yaml` — Schema/Output Mismatch

**Current issues:**
- Prompt defines `type` values: `supply_shortage, transportation_disruption, capacity_constraint, distribution_failure, production_delay`
- Actual output contains: `supply_shortage, supply_excess, delivery_delay, transport_disruption`
- Prompt defines `status` values: `resolved, unresolved, partially_resolved`
- Actual output contains: `resolved, unresolved, in_progress, worsened`
- No severity calibration — 79% of output is high/critical

**Recommended changes:**

Align the enum values to what you actually want in the output:
```yaml
rules:
  - Type values: supply_shortage, supply_excess, delivery_delay, transport_disruption, capacity_constraint, production_delay
  - Severity values: critical, high, medium, low
  - Status values: resolved, unresolved, in_progress, worsened
```

Add severity calibration:
```yaml
  - Severity calibration:
    - critical: Operations completely halted or impossible (e.g., army cannot advance due to zero fuel)
    - high: Significant degradation requiring workarounds (e.g., ammunition rationing, delayed attack by days)
    - medium: Noticeable constraint but operations continue (e.g., resupply delayed 1-2 days, substitutions needed)
    - low: Minor inconvenience with minimal operational impact (e.g., comfort items unavailable, slight delay)
  - Most logistics mentions in historical texts describe medium-severity issues. Reserve "critical" for explicitly stated operational halts.
```

**Impact:** Fixes severity skew (currently 79% high/critical → expect ~40-50% after calibration). Eliminates schema drift confusion.

---

### 4. `equipment.yaml` — Missing Category Enforcement

**Current issues:**
- Lists category values in rules but the schema example only shows one (`"armor"`)
- Output contains non-standard values like `"Medium Tank"` and `"infantry"`
- No instruction about what `subcategory` should contain vs `category`

**Recommended changes:**

Add explicit category guidance:
```yaml
rules:
  - Category values (use EXACTLY one of these): armor, aircraft, naval, artillery, infantry_weapons, vehicles, communications, engineering, other
  - Category is the BROAD type. Subcategory is the specific type within that category.
    Examples:
    - M4 Sherman → category: "armor", subcategory: "medium_tank"
    - P-47 Thunderbolt → category: "aircraft", subcategory: "fighter_bomber"
    - 105mm Howitzer → category: "artillery", subcategory: "field_howitzer"
    - Jeep → category: "vehicles", subcategory: "utility_vehicle"
  - Do NOT use equipment-specific names as category values (e.g., "Medium Tank" is wrong — use "armor")
```

**Impact:** Eliminates the 6 non-standard category values and prevents future drift.

---

### 5. `people.yaml` — Good but Overly Ambitious Schema

**Current issues:**
- The schema example is extremely detailed (education, family, awards, etc.) but the LLM rarely has this information from a single text passage
- This leads to 94% of people having no biographical_profile — the LLM either returns nothing or returns the full empty structure
- No instruction about what to do when only a name and rank are mentioned (the common case)

**Recommended changes:**

Add a minimal extraction instruction:
```yaml
prompt_template: |
  ...
  IMPORTANT: Most people will only have a name, rank, and nationality mentioned in the text.
  That is fine — extract what IS mentioned. Do NOT fabricate biographical details.
  Only populate biographical_profile fields that are EXPLICITLY stated in the text.
  If only name and rank are available, return those with an empty biographical_profile object.

  Every person MUST have:
  - PersonID (generate a ULID)
  - name (full name as mentioned, e.g., "Omar N. Bradley" not just "Bradley")
  - At least one event_mention
```

Add a rule:
```yaml
rules:
  ...
  - Every person entry MUST have a "name" field and a "PersonID" field. If you cannot determine the full name, use the most complete form available (e.g., "Colonel Abrams" rather than just "Abrams").
  - When text says "Bradley" and context makes clear this is "Omar N. Bradley", use the full name.
```

**Impact:** Fixes the 193 people files missing name/PersonID. Sets correct expectations about biographical_profile completeness.

---

### 6. `events.yaml` — Minimal but Functional

**Current issues:**
- No guidance on sub-event granularity (how many paragraphs per sub-event?)
- No instruction about handling chapters with 100+ paragraphs

**Recommended addition:**
```yaml
rules:
  ...
  - Target 3-8 paragraphs per sub-event. Split larger narrative blocks into coherent action sequences.
  - A chapter should typically produce 5-20 sub-events depending on length and complexity.
  - Sub-event_summary should be a single sentence describing the key action or decision.
```

**Impact:** Low — events extraction is working well. This just improves consistency.

---

### 7. `dates.yaml` — Clean, Minor Improvement

**Current issues:**
- `precision` values in prompt (`exact, early, mid, late, seasonal, approximate`) don't match the output field name `date_precision`
- No guidance on handling date ranges vs single dates

**Recommended addition:**
```yaml
rules:
  ...
  - For date ranges (e.g., "5-10 September"), set date_start and date_end
  - For single dates, set date_start only and leave date_end as null
  - "time_source" should indicate the timezone context: "Allied" (local time), "German" (Berlin time), "Zulu" (GMT), or null if unclear
```

**Impact:** Low — dates are 98% clean already.

---

### 8. `places.yaml` — Well-Designed

**Current issues:**
- The military unit exclusion rules are good and clearly working (no unit contamination observed)
- No issue with coordinate quality (all places have coordinates)

**One addition:**
```yaml
rules:
  ...
  - For the same place mentioned multiple times in one sub-event, extract it only ONCE
  - Use the most specific name available (e.g., "Fort Driant" not "the fort")
```

**Impact:** Minimal — places extraction is the cleanest entity type.

---

### 9. `supplemental.yaml` — Functional

**Current issues:**
- `availability` values in prompt (`online, offline, archive, unknown`) differ from output format (`public_domain, restricted, unknown`)
- This is documented as a known schema difference

**Recommended:** Align to output values or add a comment explaining the mapping:
```yaml
rules:
  ...
  - Availability values: public_domain, restricted, unknown (based on document age and source)
```

---

## Cross-Cutting Recommendations

### A. Add "DO NOT GENERATE — COPY" Pattern for All Cross-References

Every prompt that receives available entity IDs should use this pattern:
```
Available [entities] (COPY these IDs exactly — do NOT generate new ones):
{entity_list}
```

This is the single highest-impact change across all prompts. The LLM reliably copies IDs when explicitly told to, but generates new ones when the instruction is ambiguous.

### B. Add Count/Qualifier Pattern to Casualties

The output schema uses `{"value": 500, "qualifier": "approximately"}` but the prompt schema shows raw integers. Align:
```yaml
"count": {
  "killed": {"value": 45, "qualifier": "exact"},
  "wounded": {"value": 120, "qualifier": "exact"},
  "total": {"value": 175, "qualifier": "exact"}
}
```

Qualifier values: `exact, approximately, greater_than, less_than, unknown`

### C. Standardize Field Naming Across Prompts

| Current (inconsistent) | Recommended (pick one) |
|---|---|
| `Sub-eventID` / `Sub_eventID` | `Sub_eventID` everywhere |
| `Sub-event_summary` / `Sub_event_Name` | `Sub_event_summary` everywhere |
| `event_mentions` / `mentions` | `event_mentions` everywhere |

This requires a migration of existing data but prevents ongoing confusion.

### D. Add "Extraction Confidence" Field

Consider adding a confidence score to each extracted entity:
```yaml
"extraction_confidence": 0.9  # How certain the LLM is about this extraction
```

This enables downstream filtering — low-confidence extractions can be flagged for human review rather than silently polluting the dataset.

---

## Priority Summary

| # | Change | Prompt | Impact |
|---|---|---|---|
| 1 | Add "COPY ID" instructions for cross-refs | weather, casualties | Fixes 81% broken PlaceIDs, 86% null GroupIDs |
| 2 | Add severity calibration | logistics | Fixes 79% high/critical skew |
| 3 | Require name + PersonID | people | Fixes 193 identity-less records |
| 4 | Align enum values to actual output | logistics, supplemental | Eliminates schema drift |
| 5 | Enforce category enum | equipment | Fixes 6 non-standard values |
| 6 | Add PeopleGroupID to casualties schema | casualties | Enables unit-level analysis |
| 7 | Add sub-event granularity guidance | events | Improves consistency |

Changes 1-3 should be applied before the next extraction run. Changes 4-7 can be applied incrementally.
