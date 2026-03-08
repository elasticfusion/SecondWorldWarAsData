# TODO: People-to-PeopleGroup Enrichment

**Priority:** Medium  
**Status:** Planned  
**Created:** 2026-03-02

---

## Objective

Bidirectionally enrich people and people groups when relationships are discovered during biographical enrichment.

---

## Use Case

**Example:**
> "George Patton was assigned to command the Third Army in the summer of 1944"

**Should update:**

1. **Person JSON** (`George_S_Patton_Jr_*.json`):
```json
{
  "biographical_profile": {
    "units_served": [
      {
        "unit": "Third Army",
        "from": "1944-07",
        "to": "1945-05",
        "role": "Commander"
      }
    ]
  }
}
```

2. **PeopleGroup JSON** (`Third_Army_*.json`):
```json
{
  "members": [
    {
      "PersonID": "01KJ33MW...",
      "name": "George S. Patton Jr.",
      "role": "Commander",
      "from_date": "1944-07",
      "to_date": "1945-05"
    }
  ]
}
```

---

## Requirements

### 1. Detect Group Mentions in Biographical Data

When enriching person biography, detect:
- Military units (e.g., "Third Army", "XIX Army Corps")
- Organizations (e.g., "Supreme Headquarters Allied Expeditionary Force")
- Commands (e.g., "21st Army Group")

### 2. Match to Existing PeopleGroups

- Search `output/peoplegroups/` for matching group
- Use fuzzy matching for name variations
- Check aliases

### 3. Create PeopleGroup if Missing

If group doesn't exist:
- Create new peoplegroup JSON
- Set basic metadata (name, type)
- Add person as first member

### 4. Update PeopleGroup Members

Add/update member entry:
- PersonID (link to person)
- Name
- Role (Commander, Member, etc.)
- Service dates (from/to)
- Source tracking

### 5. Bidirectional Linking

Ensure both files reference each other:
- Person → units_served → unit name
- PeopleGroup → members → PersonID

---

## Implementation Plan

### Phase 1: Detection
```python
def extract_group_memberships(biographical_data: Dict) -> List[Dict]:
    """Extract group memberships from biographical data."""
    memberships = []
    
    # From units_served
    for unit in biographical_data.get("units_served", []):
        memberships.append({
            "group_name": unit["unit"],
            "role": unit.get("role", "Member"),
            "from_date": unit.get("from"),
            "to_date": unit.get("to")
        })
    
    # From biographical_details (parse with Grok)
    details = biographical_data.get("biographical_details", "")
    if details:
        # Use Grok to extract additional memberships
        pass
    
    return memberships
```

### Phase 2: Matching
```python
def find_peoplegroup(group_name: str, peoplegroups_dir: Path) -> Optional[Path]:
    """Find existing peoplegroup by name or alias."""
    # Check index
    # Fuzzy match on name
    # Check aliases
    pass
```

### Phase 3: Update PeopleGroup
```python
def add_member_to_group(
    group_file: Path,
    person_id: str,
    person_name: str,
    role: str,
    from_date: Optional[str],
    to_date: Optional[str],
    source: str
) -> bool:
    """Add or update member in peoplegroup."""
    # Load group
    # Check if member exists
    # Add/update member entry
    # Add source tracking
    # Save
    pass
```

### Phase 4: Integration
```python
# In enrich_biographies.py, after enriching person:

memberships = extract_group_memberships(bio_profile)

for membership in memberships:
    group_file = find_peoplegroup(membership["group_name"], peoplegroups_dir)
    
    if not group_file:
        group_file = create_peoplegroup(membership["group_name"], peoplegroups_dir)
    
    add_member_to_group(
        group_file,
        person_id,
        person_name,
        membership["role"],
        membership["from_date"],
        membership["to_date"],
        source="biographical_enrichment"
    )
```

---

## Data Flow

```
Person Extraction
    ↓
Biographical Enrichment (Grokipedia/Wikipedia)
    ↓
Extract Group Memberships
    ↓
Find/Create PeopleGroup
    ↓
Update PeopleGroup Members
    ↓
Both JSONs Updated
```

---

## Schema Changes

### Person JSON (No changes needed)
Already has `units_served`:
```json
{
  "units_served": [
    {
      "unit": "Third Army",
      "from": "1944-07",
      "to": "1945-05",
      "role": "Commander"  // Add role field
    }
  ]
}
```

### PeopleGroup JSON (Add source tracking)
```json
{
  "members": [
    {
      "PersonID": "01KJ33MW...",
      "name": "George S. Patton Jr.",
      "role": "Commander",
      "from_date": "1944-07",
      "to_date": "1945-05",
      "source": "biographical_enrichment",  // Add this
      "confidence": 0.8  // Add this
    }
  ]
}
```

---

## Edge Cases

### 1. Duplicate Memberships
- Person already listed in group
- **Solution:** Update dates/role if new data is more complete

### 2. Name Variations
- "Third Army" vs "3rd Army" vs "U.S. Third Army"
- **Solution:** Fuzzy matching + alias checking

### 3. Conflicting Dates
- Different sources give different service dates
- **Solution:** Keep both, track sources, flag for review

### 4. Missing Groups
- Group mentioned but doesn't exist in extraction
- **Solution:** Create stub group, mark for enrichment

### 5. Role Ambiguity
- "served in Third Army" (no role specified)
- **Solution:** Default to "Member", update if better data found

---

## Testing

### Test Cases

1. **New membership to existing group**
   - Person: Patton
   - Group: Third Army (exists)
   - Expected: Add Patton to Third Army members

2. **New membership to new group**
   - Person: Eisenhower
   - Group: SHAEF (doesn't exist)
   - Expected: Create SHAEF, add Eisenhower

3. **Update existing membership**
   - Person: Bradley (already in First Army)
   - New data: More specific dates
   - Expected: Update dates, preserve existing data

4. **Multiple memberships**
   - Person: Montgomery
   - Groups: 21st Army Group, Eighth Army
   - Expected: Update both groups

---

## Benefits

### Data Completeness
- Richer peoplegroup data from biographical sources
- Automatic discovery of group memberships
- Bidirectional relationships

### Data Consistency
- Person and group data stay in sync
- Single source of truth for relationships
- Automatic updates

### Research Value
- Complete organizational charts
- Service history tracking
- Command structure visualization

---

## Estimated Effort

- **Detection logic:** 2 hours
- **Matching logic:** 2 hours
- **Update logic:** 2 hours
- **Integration:** 1 hour
- **Testing:** 2 hours
- **Documentation:** 1 hour

**Total:** ~10 hours

---

## Dependencies

- Existing peoplegroup extraction
- Biographical enrichment system
- Fuzzy matching library (fuzzywuzzy or similar)

---

## Priority Justification

**Medium Priority** because:
- ✅ Adds significant value (bidirectional enrichment)
- ✅ Builds on existing systems
- ⚠️ Not blocking other features
- ⚠️ Can be done after initial enrichment is tested

---

## Related

- **People Extraction:** `src/extraction/people.py`
- **PeopleGroup Extraction:** `src/extraction/people_groups.py`
- **Biographical Enrichment:** `src/extraction/enrich_biographies.py`
- **Spec:** `contextmanagement/Specs/peoplegroups.json`
