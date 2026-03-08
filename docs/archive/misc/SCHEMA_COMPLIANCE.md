# Schema Compliance Review

## Summary
Comparing code schemas in `src/schemas.py` against spec files in `contextmanagement/specs/`

---

## ✅ Event Schema - COMPLIANT (after recent fixes)
**Spec:** `event.json`
- ✅ EventID with ULID
- ✅ Sub-eventID with ULID  
- ✅ Event is object with EventID and Sub-events array
- ✅ Sub-events (plural)

---

## ❌ Date Schema - NON-COMPLIANT
**Spec:** `date.json`

### Issues:
1. **Field name mismatch:**
   - Spec: `DateMentionID` (PascalCase)
   - Code: `date_mention_id` (snake_case)

2. **Missing top-level fields:**
   - Spec has: `Event_Name`, `EventID`, `Sub-event_Name`, `Sub-eventID` at root
   - Code has: `event_name`, `event_id`, `sub_event_name`, `sub_event_id` at root ✅

3. **Date fields:**
   - Spec: All dates are strings (ISO format)
   - Code: ✅ Correct

### Required Changes:
```python
class DateMention(BaseModel):
    date_mention_id: str = Field(default_factory=generate_ulid, alias="DateMentionID")
    # ... rest of fields
```

---

## ❌ Place Schema - NON-COMPLIANT  
**Spec:** `place.json`

### Issues:
1. **Field name mismatch:**
   - Spec: `PlaceMentionID` (PascalCase)
   - Code: `place_mention_id` (snake_case)

2. **Missing route support:**
   - Spec has complex route structure with sequence, multiple places
   - Code has: `route: Optional[List[Dict]]` (too generic)

3. **Top-level structure:**
   - Spec: `Event_Name`, `EventID`, `Sub-event_Name`, `Sub-eventID` at root ✅
   - Code: Has these ✅

### Required Changes:
```python
class PlaceMention(BaseModel):
    place_mention_id: str = Field(default_factory=generate_ulid, alias="PlaceMentionID")
    # Add proper route structure
```

---

## ❌ Weather Schema - NON-COMPLIANT
**Spec:** `weather.json`

### Issues:
1. **Field name mismatches:**
   - Spec: `WeatherMentionID`, `PlaceMentionID`, `DateMentionID` (PascalCase)
   - Code: `weather_mention_id`, `place_id`, `date_id` (snake_case)

2. **Missing fields:**
   - Spec has: `temperature`, `temperature_unit`, `measurement_system`, `api_source`, `image_references`
   - Code has: `unit_of_measurement` (wrong field name)
   - Code missing: `temperature`, `temperature_unit`, `measurement_system`, `api_source`, `image_references`

3. **Field name differences:**
   - Spec: `weather_description`
   - Code: `description`

### Required Changes:
```python
class WeatherMention(BaseModel):
    weather_mention_id: str = Field(default_factory=generate_ulid, alias="WeatherMentionID")
    place_name: str
    place_mention_id: str = Field(alias="PlaceMentionID")
    date: str
    date_mention_id: str = Field(alias="DateMentionID")
    weather_description: str
    temperature: Optional[float] = None
    temperature_unit: Optional[str] = None
    measurement_system: Optional[str] = None
    notable_impact: Optional[str] = None
    api_source: Optional[str] = None
    image_references: List[Dict] = Field(default_factory=list)
    original_text: str
```

---

## ❌ People Schema - NON-COMPLIANT
**Spec:** `people.json`

### Issues:
1. **Field name mismatches:**
   - Spec: `PersonID`, `MentionID`, `EventID`, `Sub-eventID`, `DateMentionID` (PascalCase)
   - Code: `person_id`, `mention_id`, `event_id`, `sub_event_id`, `date_mention_id` (snake_case)

2. **Missing fields in MilitaryAward:**
   - Spec: `award`, `class`, `date_awarded`
   - Code: `award`, `award_class`, `date_awarded` (wrong field name for class)

3. **Top-level structure:**
   - Spec: Root object with `People` array
   - Code: Has `PeopleOutput` with `People` array ✅

### Required Changes:
```python
class MilitaryAward(BaseModel):
    award: str
    award_class: str = Field(alias="class")  # 'class' is Python keyword
    date_awarded: Optional[str] = None

class PersonEventMention(BaseModel):
    mention_id: str = Field(default_factory=generate_ulid, alias="MentionID")
    event_name: str = Field(alias="Event_Name")
    event_id: str = Field(alias="EventID")
    # ... etc
```

---

## ❌ People Group Schema - NOT IMPLEMENTED
**Spec:** `peoplegroup.json`

### Status: Missing entirely from code

### Required Implementation:
```python
class PeopleGroupEventMention(BaseModel):
    mention_id: str = Field(default_factory=generate_ulid, alias="MentionID")
    event_name: str = Field(alias="Event_Name")
    event_id: str = Field(alias="EventID")
    sub_event_name: str = Field(alias="Sub-event_Name")
    sub_event_id: str = Field(alias="Sub-eventID")
    date: Optional[str] = None
    date_mention_id: Optional[str] = Field(None, alias="DateMentionID")
    context: str
    original_text: str

class PeopleGroup(BaseModel):
    group_id: str = Field(default_factory=generate_ulid, alias="GroupID")
    group_name: str
    group_type: str  # country, military_unit, alliance, political_party, etc.
    country_of_origin: Optional[str] = None
    alliance_membership: Optional[List[str]] = None
    source_language: str = "English"
    description: str
    # Optional fields based on group_type
    military_hierarchy: Optional[str] = None
    parent_organization: Optional[str] = None
    member_countries: Optional[List[str]] = None
    common_name: Optional[str] = None
    event_mentions: List[PeopleGroupEventMention] = Field(default_factory=list)

class PeopleGroupOutput(BaseModel):
    people_groups: List[PeopleGroup] = Field(default_factory=list, alias="People_Groups")
```

---

## ❌ Image Schema - NOT IMPLEMENTED
**Spec:** `image.json`

### Status: Missing from schemas (only in parser models)

---

## ❌ Map Schema - NOT IMPLEMENTED  
**Spec:** `maps.json`

### Status: Missing from schemas (only in parser models)

---

## ❌ Supplemental Material Schema - NOT IMPLEMENTED
**Spec:** `supplementalmaterial.json`

### Status: Missing entirely

---

## Action Items

### High Priority (Phase 2 - Events)
1. ✅ Fix Event schema (DONE)
2. ❌ Update all ID fields to use PascalCase aliases

### Medium Priority (Phase 3-5)
3. ❌ Fix Date schema field names
4. ❌ Fix Place schema with proper route structure
5. ❌ Fix Weather schema with all required fields
6. ❌ Fix People schema field names
7. ❌ Implement PeopleGroup schema

### Low Priority (Phase 6)
8. ❌ Implement Image schema for extraction
9. ❌ Implement Map schema for extraction
10. ❌ Implement Supplemental Material schema

---

## Pattern: PascalCase for IDs in JSON

All ID fields in the specs use PascalCase:
- `EventID`
- `Sub-eventID`
- `DateMentionID`
- `PlaceMentionID`
- `WeatherMentionID`
- `PersonID`
- `MentionID`
- `GroupID`
- `ImageID`

Code should use aliases:
```python
field_name: str = Field(alias="FieldName")
```
