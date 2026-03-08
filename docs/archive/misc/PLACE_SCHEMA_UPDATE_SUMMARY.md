# Place Schema v2.0.0 - Update Summary

**Date:** 2026-02-23  
**Status:** Proposed  
**Impact:** Breaking changes requiring migration

---

## Executive Summary

The place extraction schema has been updated to v2.0.0 to address validation failures, improve data quality, and align with JSON Schema best practices. **67% of existing place files currently fail validation** due to field naming inconsistencies.

---

## Critical Issues Fixed

### 1. Field Naming Inconsistency (CRITICAL)
**Problem:** Spec uses `Sub-event_Name` (hyphen), code expects `Sub_event_Name` (underscore)  
**Impact:** 12 of 18 place files fail validation  
**Fix:** Standardized on underscores throughout

### 2. Missing Coordinate Validation
**Problem:** No constraints on latitude/longitude values  
**Impact:** Invalid coordinates could be stored (e.g., lat=200)  
**Fix:** Added min/max validation (-90≤lat≤90, -180≤lon≤180)

### 3. Unvalidated Geography Types
**Problem:** Free-form text allows typos and inconsistencies  
**Impact:** "City" vs "city", "unknown" vs "other"  
**Fix:** Enum with 21 predefined types

---

## New Features

1. **Coordinate Precision Tracking** - Document accuracy level (exact, approximate, center_point, estimated)
2. **Confidence Scores** - AI extraction confidence (0.0-1.0)
3. **Enhanced Documentation** - Field descriptions, examples, constraints
4. **Route Validation** - Minimum 2 stops required
5. **Schema Versioning** - Proper `$schema`, `$id`, `version` fields

---

## Files Created

1. **`contextmanagement/Specs/place_v2.json`** - New schema specification
2. **`docs/current/PLACE_SCHEMA_MIGRATION.md`** - Detailed migration guide
3. **`scripts/migrate_place_schema.py`** - Automated migration tool

---

## Migration Required

### Quick Start
```bash
# 1. Test migration (dry run)
python3 scripts/migrate_place_schema.py --dry-run

# 2. Apply migration
python3 scripts/migrate_place_schema.py

# 3. Validate results
python3 scripts/validate_places.py
```

### What Gets Changed
- `Sub-event_Name` → `Sub_event_Name` (all files)
- `Sub-eventID` → `Sub_eventID` (all files)
- Geography types normalized to lowercase
- Added `coordinate_precision` field (default: "approximate")
- Added `confidence` field (default: 0.8)

---

## Breaking Changes

| Change | Old Behavior | New Behavior | Files Affected |
|--------|-------------|--------------|----------------|
| Field names | Hyphens allowed | Underscores only | 18/18 |
| Coordinates | Optional | Required | 0/18 (already required) |
| Geography type | Free text | Enum (21 types) | ~5/18 |
| Route stops | No minimum | Min 2 stops | 0/18 (none affected) |

---

## Validation Results

### Before Migration
```
Found 18 place files

❌ 12 files with issues (67% failure rate)
✅ 6 files valid (33%)

Common issues:
- Missing 'Sub_event_Name' (should be 'Sub-event_Name')
- Missing 'Sub_eventID' (should be 'Sub-eventID')
```

### After Migration (Expected)
```
Found 18 place files

✅ 18 files valid (100%)
```

---

## Code Updates Required

### 1. Update Pydantic Models
```python
# src/extraction/places.py

class PlaceOutput(BaseModel):
    Event_Name: str
    EventID: str
    Sub_event_Name: str  # ← Changed from Sub-event_Name
    Sub_eventID: str     # ← Changed from Sub-eventID
    Place_Mentions: list[PlaceMention]
```

### 2. Update JSON Schema
```python
# src/json_schemas.py

# Replace PLACE_SCHEMA with contents of place_v2.json
PLACE_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "version": "2.0.0",
    # ... rest of schema
}
```

### 3. Update Validation Script
```python
# scripts/validate_places.py

required = ["Event_Name", "EventID", "Sub_event_Name", "Sub_eventID", "Place_Mentions"]
# Changed from: ["Event_Name", "EventID", "Sub-event_Name", "Sub-eventID", "Place_Mentions"]
```

---

## Rollout Timeline

| Phase | Duration | Tasks |
|-------|----------|-------|
| **1. Review** | 1 day | Review schema, approve changes |
| **2. Backup** | 1 day | Backup all place files |
| **3. Migrate** | 1 day | Run migration script, validate |
| **4. Code Update** | 2 days | Update Pydantic models, schemas |
| **5. Testing** | 2 days | Test extraction with new schema |
| **6. Deploy** | 1 day | Deploy to production |

**Total:** ~1 week

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Data loss during migration | Low | High | Backup before migration |
| Breaking existing code | Medium | Medium | Update all references |
| Invalid geography types | Low | Low | Mapping table provided |
| Coordinate validation failures | Low | Medium | Already validated in code |

---

## Testing Checklist

- [ ] Backup all place files
- [ ] Run migration in dry-run mode
- [ ] Review migration output
- [ ] Apply migration
- [ ] Run validation script (should be 100% pass)
- [ ] Update Pydantic models
- [ ] Update JSON schemas
- [ ] Test extraction on sample chapter
- [ ] Verify output matches v2.0.0 schema
- [ ] Update documentation

---

## Recommendations

### Immediate Actions
1. ✅ **Approve schema v2.0.0** - Fixes critical validation issues
2. ✅ **Run migration script** - Automated, low risk
3. ✅ **Update code** - Pydantic models and schemas

### Future Enhancements
4. Add place deduplication (like people deduplication)
5. Create place index for quick lookup
6. Integrate external geocoding API for validation
7. Add place relationship tracking (contains, near, etc.)
8. Implement temporal place name changes

---

## Questions & Support

**Schema Issues:** See `docs/current/PLACE_SCHEMA_MIGRATION.md`  
**Migration Help:** Run `python3 scripts/migrate_place_schema.py --help`  
**Validation:** Run `python3 scripts/validate_places.py`

---

## Approval

- [ ] Schema v2.0.0 approved
- [ ] Migration plan approved
- [ ] Backup completed
- [ ] Migration executed
- [ ] Validation passed (100%)
- [ ] Code updated
- [ ] Documentation updated

**Approved by:** _________________  
**Date:** _________________
