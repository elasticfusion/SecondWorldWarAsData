# Equipment - People.json Pattern Implementation

**Date:** 2026-03-04  
**Status:** ✅ Implemented  
**Option:** C - Full people.json pattern alignment

---

## Overview

Equipment extraction now follows the same pattern as people.json, with rich metadata and context fields for each mention.

---

## Changes Made

### 1. Updated EquipmentMention Model

**Added fields:**
```python
book: Optional[str] = None
author: Optional[str] = None
series: Optional[str] = None
chapter: Optional[str] = None
paragraph_numbers: List[int] = Field(default_factory=list)
variant_mentioned: Optional[str] = None
context: Optional[str] = None
original_text: Optional[str] = None
Event_Name: Optional[str] = None
Sub_event_Name: Optional[str] = None
date: Optional[str] = None
```

**Result:** Mentions now have 21 fields (up from 9)

### 2. Updated EquipmentExtraction Model

**Added fields for LLM extraction:**
```python
variant_mentioned: Optional[str] = None
context: Optional[str] = None
original_text: Optional[str] = None
paragraph_numbers: List[int] = Field(default_factory=list)
```

### 3. Enhanced LLM Prompt

**Now extracts:**
- Context (brief situation summary)
- Original text mentioning equipment
- Which variant was mentioned
- Paragraph numbers where mentioned

### 4. Populated Metadata Fields

**From event file metadata:**
- book_title → book
- author → author
- series → series
- chapter_title → chapter

**From event data:**
- Event_Name (human-readable)
- Sub_event_Name (human-readable)

**From date files:**
- date (human-readable, e.g., "1944-06-13")

### 5. Updated Example File

**military_equipment_example2.json** now shows complete people.json pattern with:
- Book metadata
- Paragraph numbers
- Variant mentioned
- Context
- Original text
- Event names
- Human-readable dates

---

## Complete Mention Structure

```json
{
  "MentionID": "01KJ3DQ64DHFHYHA5WGWFHMCXV",
  "book": "Breakout and Pursuit",
  "author": "Martin Blumenson",
  "series": "United States Army in World War II",
  "chapter": "Chapter 5: The Breakthrough",
  "paragraph_numbers": [145, 146, 147],
  "variant_mentioned": "Tiger I Ausf. E",
  "context": "schwere Panzer-Abteilung 101 defensive operations near Caen",
  "original_text": "The Tigers of sPzAbt 101 engaged Allied armor at long range...",
  "EventID": "01KJ3BVHM401GGBXE1RG92RA6E",
  "Event_Name": "Defense of Caen",
  "Sub_eventID": "01KJ3BVHM43ADNXZM7T3HPQEPB",
  "Sub_event_Name": "Tank engagement near Villers-Bocage",
  "date": "1944-06-13",
  "DateID": "01KJ674B1A1R33MCCZ2BQPMTPF",
  "DateMentionID": "01KJ674B2D2XP5SGTQ2XSMF840",
  "using_unit": {
    "PeopleGroupID": "01KJ3DQ64DAAABBBCCCDDDEEEF",
    "name": "schwere Panzer-Abteilung 101"
  },
  "performance_notes": {
    "successes": ["88mm gun effective at long range"],
    "failures": ["Mechanical breakdowns frequent"],
    "field_modifications": ["Additional track links welded"],
    "maintenance_issues": ["Final drive failures common"]
  }
}
```

---

## Field Sources

| Field | Source | Populated By |
|-------|--------|--------------|
| MentionID | Generated | Code (ULID) |
| book | Event metadata | Code |
| author | Event metadata | Code |
| series | Event metadata | Code |
| chapter | Event metadata | Code |
| paragraph_numbers | LLM extraction | LLM |
| variant_mentioned | LLM extraction | LLM |
| context | LLM extraction | LLM |
| original_text | LLM extraction | LLM |
| EventID | Event data | Code |
| Event_Name | Event data | Code |
| Sub_eventID | Event data | Code |
| Sub_event_Name | Event data | Code |
| date | Date file lookup | Code |
| DateID | Date index | Code |
| DateMentionID | Date index | Code |
| using_unit | Entity linking | Code |
| using_person | Entity linking | Code |
| supporting_units | LLM extraction | LLM (future) |
| performance_notes | LLM extraction | LLM |

---

## Benefits

### 1. Consistency with People.json
- Same field names and structure
- Same metadata pattern
- Same linking approach

### 2. Rich Context
- Original text preserved for verification
- Context provides quick understanding
- Paragraph numbers enable source lookup

### 3. Human-Readable
- Event names (not just IDs)
- Human-readable dates
- Book metadata for citations

### 4. Traceable
- Every mention links back to source
- Paragraph numbers for precise location
- Original text for validation

### 5. Queryable
- Can search by book, author, chapter
- Can filter by date range
- Can find specific variants

---

## Comparison: Before vs After

### Before (Minimal Schema)
```json
{
  "MentionID": "01...",
  "EventID": "01...",
  "Sub_eventID": "01...",
  "DateID": "01...",
  "DateMentionID": "01...",
  "using_unit": {...},
  "performance_notes": {...}
}
```
**7 fields, IDs only**

### After (People.json Pattern)
```json
{
  "MentionID": "01...",
  "book": "Breakout and Pursuit",
  "author": "Martin Blumenson",
  "series": "United States Army in World War II",
  "chapter": "Chapter 5",
  "paragraph_numbers": [145, 146],
  "variant_mentioned": "M4A1",
  "context": "2nd Armored Division attack",
  "original_text": "The Shermans advanced...",
  "EventID": "01...",
  "Event_Name": "Operation Cobra",
  "Sub_eventID": "01...",
  "Sub_event_Name": "St. Lô Breakthrough",
  "date": "1944-07-25",
  "DateID": "01...",
  "DateMentionID": "01...",
  "using_unit": {...},
  "performance_notes": {...}
}
```
**18+ fields, rich metadata**

---

## Usage Examples

### Find Equipment by Book
```bash
jq '.mentions[] | select(.book == "Breakout and Pursuit")' output/equipment/*.json
```

### Find Equipment by Date Range
```bash
jq '.mentions[] | select(.date >= "1944-06-01" and .date <= "1944-06-30")' output/equipment/*.json
```

### Find Specific Variant Mentions
```bash
jq '.mentions[] | select(.variant_mentioned == "Tiger I Ausf. E")' output/equipment/*.json
```

### Extract Original Text
```bash
jq -r '.mentions[].original_text' output/equipment/Tiger_I_*.json
```

### Find Equipment in Specific Chapter
```bash
jq '.mentions[] | select(.chapter | contains("Chapter 5"))' output/equipment/*.json
```

---

## Testing

### Validate Models
```bash
python3 -c "from src.extraction.equipment import EquipmentExtraction, EquipmentMention; print('OK')"
```

### Validate Example
```bash
python3 -c "
import json
with open('contextmanagement/Specs/military_equipment_example2.json') as f:
    data = json.load(f)
    assert 'book' in data['mentions'][0]
    assert 'Event_Name' in data['mentions'][0]
    assert 'date' in data['mentions'][0]
    print('Example validated')
"
```

### Test Extraction
```bash
python3 -m src.extraction.equipment output/BreakoutAndPursuit/chapter1a-event.json
```

---

## Migration Notes

### Existing Equipment Files

**No migration needed** - new fields are optional. Existing files will continue to work.

### Re-extraction

To populate new fields in existing equipment:
```bash
# Clear equipment directory
rm -rf output/equipment/*

# Re-run phase 2
python3 phase2_extract.py
```

---

## Future Enhancements

### Media Fields (Not Yet Implemented)
```json
{
  "media": {
    "photos": [...],
    "videos": [...],
    "audio": [...],
    "documents": [...]
  }
}
```

**Status:** Planned but not implemented

### Supporting Units Extraction
**Status:** Model exists but LLM doesn't extract yet

---

## See Also

- **People Schema:** `contextmanagement/Specs/people.json`
- **Equipment Example:** `contextmanagement/Specs/military_equipment_example2.json`
- **Equipment Proposal:** `docs/current/features/equipment/MILITARY_EQUIPMENT.md`
- **Entity Linking:** `docs/current/features/equipment/EQUIPMENT_ENTITY_LINKING.md`
