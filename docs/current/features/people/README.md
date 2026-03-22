# File-Per-Person Implementation

**Last Updated:** 2026-03-22

## Structure

```
output/people/
├── index.json                                    # name → filename lookup
├── Dwight_D_Eisenhower_01ABC123.json            # Individual person files
├── George_S_Patton_01DEF456.json
├── Bernard_Montgomery_01GHI789.json
├── Supreme_Commander_AEEF_01JKL012.json         # Title-based (potential duplicate)
├── Eisenhower_01MNO345.json                     # Last name only (potential duplicate)
└── duplicate_report.json                         # Duplicate analysis

```

## Person File Format

Each file contains a single person:

```json
{
  "PersonID": "01ULID...",
  "name": "Dwight D. Eisenhower",
  "source_language": "English",
  "biographical_profile": {
    "birth_date": "1890-10-14",
    "death_date": "1969-03-28",
    "nationality": "American",
    "biographical_details": "...",
    "ranks": [{ "rank": "General", "branch": "US Army", "date": "1945-03-12" }],
    "units_served": [{ "unit": "SHAEF", "from": "1944-01", "to": "1945-05" }],
    "military_awards": [{ "award": "...", "class": null, "date_awarded": null }],
    "biography_sources": [{ "source": "Wikipedia", "confidence": 0.9, "fields_sourced": ["birth_date"], "page": null }]
  },
  "event_mentions": [
    {
      "MentionID": "01ULID...",
      "Event_Name": "The Allies",
      "EventID": "01ULID...",
      "Sub_event_Name": "...",
      "Sub_eventID": "01ULID...",
      "book": "Breakout and Pursuit",
      "author": "Martin Blumenson",
      "series": "United States Army in World War II",
      "date": "1944-06-06",
      "DateMentionID": "01ULID..."
    }
  ]
}
```

**Notes:**
- `biographical_profile` is populated during Phase 3 enrichment (may be absent before enrichment)
- `DateMentionID` → top-level `DateID` in `output/dates/*.json` (resolved via lookup, not LLM-provided)
- `Sub_event_Name` uses underscore (not hyphen)

## Index File

Fast lookup by normalized name:

```json
{
  "dwight d eisenhower": "Dwight_D_Eisenhower_01ABC123.json",
  "george s patton": "George_S_Patton_01DEF456.json",
  "eisenhower": "Eisenhower_01MNO345.json"
}
```

## Duplicate Detection

### Heuristics Used:

1. **Name Similarity** (80%+ match)
   - "Dwight D. Eisenhower" vs "Dwight Eisenhower"
   - Uses SequenceMatcher for fuzzy matching

2. **Same Last Name** (>3 chars)
   - "George Patton" vs "George S. Patton"
   - Filters out military titles

3. **Shared Biographical Data**
   - Same birth date
   - Same nationality + birth year

4. **Shared Positions**
   - Both held "Supreme Commander" position
   - Cross-references event mentions

5. **Substring Match**
   - "Eisenhower" in "Dwight D. Eisenhower"
   - Requires >5 chars to avoid false positives

### Confidence Scoring:

- Name similarity: 40% weight
- Same last name: 30% weight
- Shared bio data: 50% weight
- Shared positions: 30% weight
- Substring match: 40% weight

**Threshold**: 60% confidence to flag as potential duplicate

### Duplicate Report Format:

```json
{
  "total_people": 1247,
  "duplicate_groups": 23,
  "duplicates": [
    {
      "confidence": 0.95,
      "reasons": [
        "Name similarity: 0.92",
        "Same last name: eisenhower",
        "Shared positions"
      ],
      "people": [
        {
          "filename": "Dwight_D_Eisenhower_01ABC123.json",
          "name": "Dwight D. Eisenhower",
          "PersonID": "01ABC123..."
        },
        {
          "filename": "Eisenhower_01MNO345.json",
          "name": "Eisenhower",
          "PersonID": "01MNO345..."
        },
        {
          "filename": "Supreme_Commander_AEEF_01JKL012.json",
          "name": "Supreme Commander of the Allied Expeditionary Force",
          "PersonID": "01JKL012..."
        }
      ]
    }
  ]
}
```

## Workflow

### 1. Extraction (Automatic)
```bash
python phase2_extract.py
```
- Creates individual person files
- Updates index.json
- Generates duplicate_report.json

### 2. Review Duplicates (Manual)
```bash
cat output/people/duplicate_report.json
```
- Review high-confidence matches
- Verify they're actually the same person

### 3. Merge Duplicates (Manual)
```bash
python merge_people.py \
  --keep Dwight_D_Eisenhower_01ABC123.json \
  --merge Eisenhower_01MNO345.json \
  --merge Supreme_Commander_AEEF_01JKL012.json
```
- Combines event mentions
- Updates index
- Deletes merged files

## Benefits

✅ **Incremental**: Process one chapter without loading all people  
✅ **Scalable**: Handles 10,000+ people efficiently  
✅ **Human-readable**: Direct file access by name  
✅ **Git-friendly**: Diffs show which people changed  
✅ **Parallel-safe**: Can process multiple chapters simultaneously  
✅ **Smart detection**: Multiple heuristics for finding duplicates  
✅ **Reviewable**: Confidence scores help prioritize manual review  

## Queries

### Find a person:
```bash
# By name
cat output/people/index.json | jq '.["dwight d eisenhower"]'

# Direct access
cat output/people/Dwight_D_Eisenhower_01ABC123.json
```

### List all people:
```bash
ls output/people/*.json | grep -v index | grep -v duplicate_report
```

### Count people:
```bash
ls output/people/*.json | grep -v index | grep -v duplicate_report | wc -l
```

### Find people at event:
```bash
grep -l "EventID.*01ABC123" output/people/*.json
```

## Next Steps

1. Run extraction: `./cleanup_people.sh && python phase2_extract.py`
2. Review duplicates: `cat output/people/duplicate_report.json`
3. Create merge script for confirmed duplicates
4. Build query tools for common use cases
