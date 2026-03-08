# People Deduplication Strategy

## Problem Statement

Historical texts reference people inconsistently:
- **By name**: "Dwight Eisenhower", "Dwight D. Eisenhower"
- **By title**: "Supreme Commander of the Allied Expeditionary Force"
- **By position**: "Commander of the Third Army"
- **By rank**: "General Patton"
- **By nickname**: "Ike"

Simple name matching creates duplicate entries for the same person.

## Current Limitation

The existing `_normalize_name()` function only handles:
- Case variations: "EISENHOWER" → "eisenhower"
- Whitespace: " Eisenhower " → "eisenhower"

It **does NOT** handle:
- Title references: "Supreme Commander" ≠ "Eisenhower"
- Name variations: "Dwight D. Eisenhower" ≠ "Dwight Eisenhower"
- Nicknames: "Ike" ≠ "Eisenhower"

## Two-Phase Solution

### Phase 1: Extract Everything (Current)
- Let AI extract whatever reference appears in text
- Capture in `name` field (could be name, title, or position)
- Record `position_at_event` for context
- Store biographical clues

### Phase 2: AI-Powered Consolidation (New)

**Module**: `src/extraction/people_consolidation.py`

**Process**:
1. Load all people from `people-central.json`
2. Send summary to AI with biographical context
3. AI identifies duplicate groups based on:
   - Biographical data (birth/death dates, nationality)
   - Positions held (cross-reference with titles)
   - Historical context
   - Name patterns
4. Merge duplicate entries:
   - Choose canonical name
   - Combine event mentions
   - Deduplicate awards
   - Preserve all biographical data
5. Save consolidated file

**Example AI Analysis**:
```json
{
  "duplicates": [
    {
      "canonical_name": "Dwight D. Eisenhower",
      "indices": [0, 5, 12, 18],
      "reason": "Entries 0='Dwight D. Eisenhower', 5='Eisenhower', 12='Supreme Commander of the Allied Expeditionary Force', 18='Ike' all refer to same person based on position (Supreme Commander) and context"
    }
  ]
}
```

## Workflow

### During Extraction (Automatic)
```bash
python phase2_extract.py
```
- Creates `people-central.json` with raw extractions
- May contain duplicates

### After Extraction (Manual or Scheduled)
```bash
python consolidate_people.py
```
- Analyzes `people-central.json`
- Creates `people-consolidated.json`
- Logs merge decisions for review

## Benefits

1. **Accurate Extraction**: AI extracts exactly what appears in text
2. **Intelligent Merging**: AI uses context to identify same person
3. **Audit Trail**: Consolidation logs show merge reasoning
4. **Reversible**: Original `people-central.json` preserved
5. **Reviewable**: Human can verify merge decisions

## Schema Addition

Added `aliases` field to `Person` model:
```python
class Person(BaseModel):
    PersonID: str
    name: str  # Canonical name after consolidation
    aliases: list[str]  # ["Ike", "Supreme Commander", "General Eisenhower"]
    ...
```

## Future Enhancements

1. **Confidence Scores**: AI provides certainty level for merges
2. **Manual Review UI**: Flag uncertain merges for human review
3. **Incremental Updates**: Re-run consolidation as new chapters added
4. **Cross-Book Validation**: Use multiple books to confirm identities

## Usage

```python
from src.extraction.people_consolidation import consolidate_people
from src.grok_client import GrokClient

grok = GrokClient()
consolidate_people(
    central_file=Path("output/BreakoutAndPursuit/people-central.json"),
    grok_client=grok,
    output_file=Path("output/BreakoutAndPursuit/people-consolidated.json")
)
```

## Why This Approach?

- **Leverages AI strengths**: Pattern recognition, context understanding
- **Handles ambiguity**: "Montgomery" could be Bernard or multiple others
- **Scalable**: Works across thousands of entries
- **Maintainable**: No complex fuzzy matching rules to maintain
- **Accurate**: Uses historical knowledge embedded in AI model
