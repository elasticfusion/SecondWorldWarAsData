# Equipment Schema - Description Fields

**Date:** 2026-03-03  
**Clarification:** Separate description fields at equipment and mention levels

---

## Two Description Fields

### 1. Equipment-Level Description

**Location:** Root level of equipment record  
**Purpose:** General description of the equipment

```json
{
  "EquipmentID": "01H8XYZDAB123CD456EF789GH",
  "common_name": "Sherman",
  "technical_identifier": "M4",
  "description": "American medium tank, primary Allied armored vehicle in Western Europe. Reliable and easy to maintain but outgunned by German heavy tanks.",
  "category": "armor"
}
```

**Contains:**
- General role and purpose
- Overall characteristics
- Historical significance
- General strengths/weaknesses

### 2. Mention-Level Description

**Location:** Within each mention in `mentions` array  
**Purpose:** Event-specific details and observations

```json
{
  "mentions": [
    {
      "MentionID": "01H8XYZ...",
      "EventID": "01H8XYZ...",
      "Event_Name": "Operation Cobra",
      "context": "2nd Armored Division attack on St. Lô",
      "description": "The Shermans performed admirably in the initial assault across open ground, but encountered difficulties in the bocage terrain. Several tanks were knocked out by well-concealed German anti-tank guns. Crews reported that the 75mm gun was inadequate against Panthers encountered during the advance.",
      "original_text": "The Shermans of the 2nd Armored Division advanced..."
    }
  ]
}
```

**Contains:**
- How equipment performed in this specific event
- Notable observations from this engagement
- Event-specific issues or successes
- Crew reports or field observations
- Tactical employment details

---

## Key Differences

| Field | Scope | Example |
|-------|-------|---------|
| **Equipment description** | General | "American medium tank, reliable but outgunned" |
| **Mention description** | Event-specific | "Performed well in open terrain but struggled in bocage during St. Lô attack" |

---

## Context vs Description in Mentions

### Context Field
**Purpose:** Brief summary of the situation  
**Length:** 1-2 sentences  
**Example:** "2nd Armored Division attack on St. Lô"

### Description Field
**Purpose:** Detailed observations about equipment in this event  
**Length:** Multiple sentences, detailed  
**Example:** "The Shermans performed admirably in the initial assault across open ground, but encountered difficulties in the bocage terrain. Several tanks were knocked out by well-concealed German anti-tank guns. Crews reported that the 75mm gun was inadequate against Panthers encountered during the advance."

---

## Complete Example

```json
{
  "EquipmentID": "01H8XYZDAB123CD456EF789GH",
  "common_name": "Sherman",
  "technical_identifier": "M4",
  "description": "American medium tank, primary Allied armored vehicle in Western Europe. Reliable and easy to maintain but outgunned by German heavy tanks.",
  "mentions": [
    {
      "MentionID": "01H8XYZ...",
      "EventID": "01H8XYZ...",
      "Event_Name": "Operation Cobra",
      "Sub_eventID": "01H8XYZ...",
      "Sub_event_Name": "St. Lô Breakthrough",
      "context": "2nd Armored Division attack on St. Lô",
      "description": "The Shermans performed admirably in the initial assault across open ground, but encountered difficulties in the bocage terrain. Several tanks were knocked out by well-concealed German anti-tank guns. Crews reported that the 75mm gun was inadequate against Panthers encountered during the advance.",
      "original_text": "The Shermans of the 2nd Armored Division advanced through the hedgerows...",
      "performance_notes": {
        "successes": ["Effective against infantry", "Good mobility in open terrain"],
        "failures": ["Outgunned by Panthers", "Vulnerable to 88mm guns"],
        "field_modifications": ["Sandbags added", "Hedgerow cutters welded"]
      }
    }
  ]
}
```

---

## Why Two Description Fields?

### Equipment Description
- **Reusable** - Same across all mentions
- **General** - Applies to all uses of this equipment
- **Historical** - Overall historical assessment

### Mention Description
- **Specific** - Unique to this event/sub-event
- **Contextual** - Tied to specific circumstances
- **Observational** - Based on this particular engagement

---

## Extraction Guidelines

### For Equipment Description
Extract from:
- General historical sources
- Technical manuals
- Overall assessments in text
- Historical summaries

### For Mention Description
Extract from:
- Event-specific text
- After-action reports
- Crew observations
- Field reports
- Combat narratives

---

## Summary

- ✅ **EquipmentID** - ULID (26 characters)
- ✅ **Equipment description** - General description of equipment
- ✅ **Mention description** - Event-specific observations
- ✅ **Context** - Brief situation summary
- ✅ **Original text** - Source text for verification

Each field serves a distinct purpose and should not be confused with ULIDs or other identifiers.
