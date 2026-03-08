# Supplemental Material Entity Resolution

**Date:** 2026-03-08  
**Status:** Implemented

## Overview

Enhanced supplemental material extraction to link authors and mentioned entities (people and organizations) to their existing PersonID and PeopleGroupID records, reusing entity resolution logic from `equipment.py` and `casualties.py`.

## Changes Made

### 1. Added Entity Resolution Functions (`src/extraction/supplemental.py`)

Reused existing patterns from other extractors:

```python
def _build_people_index(output_root: Path) -> Dict[str, str]:
    """Build people name -> PersonID index."""
    # Loads all people files, creates name->ID mapping
    
def _build_groups_index(output_root: Path) -> Dict[str, str]:
    """Build group name -> GroupID index (includes aliases)."""
    # Loads all group files, includes aliases for better matching

def _resolve_author_ids(authors: List[str], people_index: Dict[str, str]) -> List[Optional[str]]:
    """Resolve author names to PersonIDs."""
    
def _resolve_mentioned_people(citation_text: str, people_index: Dict[str, str]) -> List[Dict[str, str]]:
    """Extract people mentioned in citation text."""
    
def _resolve_mentioned_organizations(citation_text: str, groups_index: Dict[str, str]) -> List[Dict[str, str]]:
    """Extract organizations mentioned in citation text."""
```

### 2. Enhanced `extract_supplemental()` Function

- Loads people and groups indexes at start of extraction
- Resolves entity references before writing output files
- Adds new fields to supplemental material:
  - `citation.author_ids`: Array of PersonIDs matching authors
  - `mentioned_people`: People found in citation text
  - `mentioned_organizations`: Organizations found in citation text

### 3. Output Format

**Before:**
```json
{
  "MaterialID": "01KK6J70TYPGHXYGVQ7GZMNZVV",
  "citation": {
    "author": ["Maj. Gen. Sir Frederick Morgan"]
  },
  "verbatim_reference": "Long after the Joint Chiefs of Staff..."
}
```

**After:**
```json
{
  "MaterialID": "01KK6J70TYPGHXYGVQ7GZMNZVV",
  "citation": {
    "author": ["Maj. Gen. Sir Frederick Morgan"],
    "author_ids": ["01KK5R7QTVY2MKBVAW2CT54Q51"]
  },
  "mentioned_people": [
    {
      "PersonID": "01KK5AXEJ4RCJ2B4ZBDSC28VMN",
      "name": "Dwight D. Eisenhower"
    }
  ],
  "mentioned_organizations": [
    {
      "PeopleGroupID": "01KK52XHG1ABCDEFGH2345678",
      "name": "Joint Chiefs of Staff"
    }
  ],
  "verbatim_reference": "Long after the Joint Chiefs of Staff..."
}
```

## Benefits

1. **Reuses Existing Code**: Same entity resolution pattern as equipment and casualties extractors
2. **Links Citations to People**: Authors in footnotes/endnotes now linked to person records
3. **Discovers Relationships**: Finds people/organizations mentioned in supplemental material
4. **Enables Cross-Referencing**: Can now query "what sources mention this person/organization?"
5. **Minimal Code**: Only ~80 lines added, mostly copied from existing extractors

## Testing

```bash
# Test entity index building
python3 -c "
from pathlib import Path
from src.extraction.supplemental import _build_people_index, _build_groups_index

output_root = Path('output')
people = _build_people_index(output_root)
groups = _build_groups_index(output_root)

print(f'People: {len(people)}, Groups: {len(groups)}')
print(f'Eisenhower ID: {people.get(\"Dwight D. Eisenhower\")}')
print(f'Joint Chiefs ID: {groups.get(\"Joint Chiefs of Staff\")}')
"

# Test entity resolution
python3 -c "
from pathlib import Path
from src.extraction.supplemental import (
    _build_people_index,
    _build_groups_index,
    _resolve_mentioned_organizations
)

output_root = Path('output')
groups_index = _build_groups_index(output_root)

text = 'Long after the Joint Chiefs of Staff had become an accepted organization'
orgs = _resolve_mentioned_organizations(text, groups_index)
print(f'Found: {orgs}')
"
```

## Future Enhancements

1. **Fuzzy Name Matching**: Handle name variations (e.g., "Joint Chief" vs "Joint Chiefs")
2. **Reverse References**: Add `citations` field to people/group files showing where they're cited
3. **Citation Network**: Build graph of who cites whom
4. **Author Disambiguation**: Handle multiple people with similar names

## Implementation Notes

- Entity resolution happens after AI extraction, before file writing
- Uses simple substring matching (case-insensitive)
- Only adds fields if matches are found (keeps output clean)
- Backward compatible: existing files without these fields still work
- No schema changes required: new fields are optional

## Code Reuse

Functions copied/adapted from:
- `src/extraction/equipment.py`: `_build_people_index()`, `_build_groups_index()`
- `src/extraction/casualties.py`: `_build_entity_index()` pattern
- Same index structure used across all extractors for consistency

## Related Files

- `src/extraction/supplemental.py` - Main implementation
- `src/extraction/equipment.py` - Original entity resolution pattern
- `src/extraction/casualties.py` - Entity resolution reference
- `output/*/chapter*-endnotes.json` - Output files with new fields
- `output/*/chapter*-footnotes.json` - Output files with new fields
