# Equipment Extraction - Bug Fixes

**Date:** 2026-03-04  
**Issues:** Logging format errors and missing processed events tracking

---

## Issues Fixed

### 1. Logging Format String Errors

**Problem:** Mixed f-string syntax with %-formatting causing literal output

**Examples:**
```python
# Before (broken)
logger.info("Loaded {len(people_index)} people, {len(people_groups_index)} groups, %s date mentions", len(dates_index))
logger.warning("  ⚠ Attempt {attempt + 1} failed: %s", e)
logger.debug("Linked unit '{eq.using_unit_name}' to %s", group_id)

# After (fixed)
logger.info("Loaded %s people, %s groups, %s date mentions", len(people_index), len(people_groups_index), len(dates_index))
logger.warning("  ⚠ Attempt %s failed: %s", attempt + 1, e)
logger.debug("Linked unit '%s' to %s", eq.using_unit_name, group_id)
```

**Impact:** Logs now display actual values instead of literal `{variable}` strings

---

### 2. Missing Processed Events Tracking

**Problem:** Equipment extraction reprocessed same event files every run

**Root Cause:** No `.processed_events.json` registry like people/people_groups have

**Solution:** Added processed events tracking

```python
# Check if already processed
processed_registry = output_dir / ".processed_events.json"

if processed_registry.exists():
    with open(processed_registry) as f:
        processed = json.load(f)
    if str(event_file) in processed:
        logger.debug("  Already processed, skipping")
        return []

# ... extraction logic ...

# Mark as processed
processed[str(event_file)] = True
with open(processed_registry, 'w') as f:
    json.dump(processed, f, indent=2)
```

**Impact:** 
- Equipment extraction now skips already-processed files
- Saves API calls and processing time
- Consistent with people/people_groups behavior

---

## Files Changed

1. `src/extraction/equipment.py`
   - Fixed 7 logging format strings
   - Added processed events registry
   - Added skip logic for processed files

---

## Testing

### Verify Logging
```bash
# Run extraction and check logs show actual values
python3 phase2_extract.py 2>&1 | grep "Loaded"
# Should show: "Loaded 73 people, 113 groups, 87 date mentions"
# Not: "Loaded {len(people_index)} people..."
```

### Verify Skip Logic
```bash
# First run - processes files
python3 phase2_extract.py

# Second run - should skip
python3 phase2_extract.py 2>&1 | grep "Already processed"
# Should show: "Already processed, skipping" for each file
```

### Force Reprocessing
```bash
# Delete registry to force reprocessing
rm output/equipment/.processed_events.json
python3 phase2_extract.py
```

---

## Benefits

1. ✅ **Correct Logging** - Variables display actual values
2. ✅ **Faster Execution** - Skips already-processed files
3. ✅ **Reduced API Costs** - No redundant API calls
4. ✅ **Consistent Behavior** - Matches people/people_groups pattern
5. ✅ **Idempotent** - Safe to run multiple times

---

## Related Issues

These same logging format issues exist in other extraction modules:
- `src/extraction/events.py` - Uses f-strings (correct)
- `src/extraction/places.py` - Uses f-strings (correct)
- `src/extraction/people.py` - Uses f-strings (correct)

Equipment was the only module mixing formats.
