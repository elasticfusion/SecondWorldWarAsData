# Source Tracking Enhancement

**Date:** 2026-03-02  
**Status:** Complete

---

## Overview

Enhanced biographical source tracking to record which specific fields came from each source.

---

## Changes Made

### 1. BiographySource Model Enhancement

**Added field:**
```python
class BiographySource(BaseModel):
    source: str
    page: Optional[int] = None
    confidence: Optional[float] = None
    fields_sourced: list[str] = Field(
        default_factory=list,
        description="List of biographical fields sourced from this reference"
    )
```

**Example:**
```json
{
  "source": "Cross-Channel Attack",
  "page": 123,
  "confidence": 0.95,
  "fields_sourced": ["birth_date", "ranks", "units_served", "biographical_details"]
}
```

---

### 2. Extraction Prompt Update

Added instructions to track field sources:
```
For biography_sources, include:
- source: Book title
- page: Page number if mentioned
- confidence: 0.0-1.0 (how certain the extraction is)
- fields_sourced: List of field names extracted from this source
```

---

### 3. Merge Logic Enhancement

Added biography_sources merging:
```python
# Merge biography_sources
existing_sources = existing_bio.get("biography_sources", [])
new_sources = new_bio.get("biography_sources", [])
source_set = {json.dumps(s, sort_keys=True) for s in existing_sources}
for source in new_sources:
    source_json = json.dumps(source, sort_keys=True)
    if source_json not in source_set:
        existing_sources.append(source)
existing_bio["biography_sources"] = existing_sources
```

---

## Source Tracking Levels

### 1. Biographical Profile Sources
**Location:** `biographical_profile.biography_sources[]`

**Tracks:**
- Which book/document provided biographical data
- Which specific fields came from that source
- Page number (if available)
- Confidence level

**Example:**
```json
"biography_sources": [
  {
    "source": "Cross-Channel Attack",
    "page": 45,
    "confidence": 0.95,
    "fields_sourced": ["birth_date", "nationality", "ranks"]
  },
  {
    "source": "Crusade in Europe",
    "page": null,
    "confidence": 1.0,
    "fields_sourced": ["biographical_details"]
  }
]
```

### 2. Event Mention Sources
**Location:** `event_mentions[].book/author/series`

**Tracks:**
- Which book mentioned this person in this event
- Exact quote (`original_text`)
- Event and sub-event context

**Example:**
```json
{
  "MentionID": "01H8XYZJ2MN456PQ789RS012TU",
  "Event_Name": "Planning for D-Day",
  "book": "Cross-Channel Attack",
  "author": "Gordon A. Harrison",
  "series": "United States Army in World War II",
  "original_text": "General Eisenhower was appointed Supreme Commander"
}
```

---

## Benefits

### Provenance Tracking
- Know exactly where each piece of biographical data came from
- Can cite sources for specific facts
- Enables verification and fact-checking

### Conflict Resolution
- When multiple sources provide different data, can compare confidence
- Can prioritize primary sources over secondary
- Can identify contradictions

### Data Quality
- Track confidence per source
- Identify which fields need verification
- Find gaps in biographical coverage

### Citation Generation
- Automatically generate citations for biographical facts
- Link facts to specific pages
- Support academic rigor

---

## Usage Examples

### Query: "Where did we get Eisenhower's birth date?"
```python
for source in person["biographical_profile"]["biography_sources"]:
    if "birth_date" in source["fields_sourced"]:
        print(f"Source: {source['source']}, Page: {source['page']}")
```

### Query: "Which sources mention this person?"
```python
# Biographical sources
bio_sources = person["biographical_profile"]["biography_sources"]

# Event mention sources
event_sources = {m["book"] for m in person["event_mentions"]}

all_sources = set(s["source"] for s in bio_sources) | event_sources
```

### Query: "What confidence do we have in this data?"
```python
for source in person["biographical_profile"]["biography_sources"]:
    if "ranks" in source["fields_sourced"]:
        print(f"Ranks confidence: {source['confidence']}")
```

---

## Migration

Existing people files already have empty `biography_sources` arrays from previous migration. The new `fields_sourced` field will be populated on next extraction run.

**No additional migration needed** - field has default value of empty list.

---

## Future Enhancements

1. **Automatic field detection** - Infer fields_sourced from what changed
2. **Source conflict detection** - Flag when sources disagree
3. **Citation formatting** - Generate formatted citations
4. **Source quality scoring** - Rate sources by reliability
5. **Cross-reference validation** - Verify facts across sources

---

## Files Modified

- `src/extraction/people.py` - Model, prompt, merge logic
- `contextmanagement/Specs/people.json` - Spec example

---

## Quality Checks

- ✅ Mypy: 0 errors
- ✅ Black: Formatted
- ✅ Model test: Passed

---

## Related Documentation

- `contextmanagement/Specs/people.json` - Full schema
- `docs/current/features/people/implementation.md` - Implementation details
