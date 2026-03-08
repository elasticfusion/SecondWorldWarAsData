# Phase 2 Extraction - Reprocessing Issues and Fixes

**Date:** 2026-03-04  
**Status:** ✅ All Issues Resolved

---

## Issues Identified

### 1. Equipment Extraction ✅ FIXED

**Problem:** 
- Reprocessed all event files every run
- Added duplicate mentions to equipment files

**Root Cause:**
- No `.processed_events.json` registry
- No duplicate mention check in merge logic

**Fix Applied:**
```python
# Added processed events tracking
processed_registry = output_dir / ".processed_events.json"
if str(event_file) in processed:
    logger.debug("  Already processed, skipping")
    return []

# Added duplicate mention check
existing_mention_ids = {m["MentionID"] for m in existing.get("mentions", [])}
if new_mention["MentionID"] in existing_mention_ids:
    logger.debug("Mention %s already exists, skipping", new_mention["MentionID"])
    return eq_file
```

**Result:** Equipment extraction now skips processed files and prevents duplicate mentions

---

### 2. Maps Extraction ✅ FIXED

**Problem:**
- Reprocessed all event files every run
- Downloaded images from ibiblio.org repeatedly

**Root Cause:**
- No `.processed_events.json` registry

**Fix Applied:**
```python
# Added processed events tracking in output/maps/
processed_registry = maps_output_dir / ".processed_events.json"
if event_key in processed:
    logger.debug("Already processed %s, skipping", event_file)
    continue
```

**Result:** Maps extraction now skips processed files, preventing redundant downloads

---

### 3. Dates Extraction ✅ OK

**Status:** Has duplicate prevention by `Sub_eventID`

**Current Behavior:**
```python
# dates.py - _add_event_mention()
existing = [m for m in date_data["event_mentions"] if m["Sub_eventID"] == sub_event_id]
if existing:
    logger.info("Date already has mention from this sub-event, skipping")
    return
```

**Result:** Dates extraction is idempotent - safe to run multiple times

---

### 4. Places Extraction ✅ OK

**Status:** Has duplicate prevention by `Sub_eventID`

**Current Behavior:**
```python
# places.py - _add_event_mention()
existing = [m for m in place_data["event_mentions"] if m["Sub_eventID"] == sub_event_id]
if not existing:
    place_data["event_mentions"].append(event_mention)
```

**Result:** Places extraction is idempotent - safe to run multiple times

---

### 5. Weather Extraction ✅ OK

**Status:** Has duplicate prevention by `Sub_eventID`

**Current Behavior:**
```python
# weather_central.py - _add_event_mention()
existing = [m for m in weather_data["event_mentions"] if m["Sub_eventID"] == sub_event_id]
if existing:
    logger.info("Weather already has mention from this sub-event, skipping")
    return
```

**Result:** Weather extraction is idempotent - safe to run multiple times

---

### 6. People Extraction ✅ OK

**Status:** Has `.processed_events.json` registry
**Location:** `output/people/.processed_events.json`
**Behavior:** Skips already-processed event files

---

### 7. People Groups Extraction ✅ OK

**Status:** Has `.processed_events.json` registry
**Location:** `output/people_groups/.processed_events.json`
**Behavior:** Skips already-processed event files

---

## Architecture Issue

### Phase 2 Design

The phase2_extract.py script has this pattern:

```python
# Extract events (skip if exists)
if not event_file.exists():
    output_file = extract_events(...)

# Always run central repositories
extract_dates(...)      # ← Runs every time
extract_places(...)     # ← Runs every time
extract_people(...)     # ← Has internal skip logic
extract_people_groups(...)  # ← Has internal skip logic
extract_weather_central(...)  # ← Runs every time
extract_equipment_from_event(...)  # ← Has internal skip logic
```

**Problem:** Central repositories (dates, places, weather) are called "always" but don't check for duplicates

**Two Solutions:**

#### Option A: Add Duplicate Checks (Recommended)
- Add duplicate mention checks to dates/places/weather
- Keep "always run" behavior
- Idempotent - safe to run multiple times

#### Option B: Add Processed Tracking
- Add `.processed_events.json` to dates/places/weather
- Skip already-processed event files
- More efficient but more complex

---

## Impact Assessment

### Current State

Running phase2_extract.py multiple times causes:

1. ✅ **Equipment** - No duplicates (fixed)
2. ✅ **Maps** - No reprocessing (fixed)
3. ✅ **Dates** - No duplicates (has Sub_eventID check)
4. ✅ **Places** - No duplicates (has Sub_eventID check)
5. ✅ **Weather** - No duplicates (has Sub_eventID check)
6. ✅ **People** - No duplicates (has tracking)
7. ✅ **People Groups** - No duplicates (has tracking)

**Result:** All extraction modules are now idempotent and safe to run multiple times.

---

## Recommended Actions

### Immediate (High Priority)

**All issues resolved!** No immediate actions needed.

### Short-term (Medium Priority)

1. **Add validation tests**
   - Test that running phase2 twice doesn't create duplicates
   - Test all extraction modules for idempotency

### Long-term (Low Priority)

2. **Consider processed tracking for central repos**
   - More efficient than always running
   - Consistent with people/groups pattern
   - Would skip sub-event processing entirely if already done

---

## Testing

### Verify Duplicates Exist

```bash
# Check for duplicate MentionIDs in dates
jq '.event_mentions | group_by(.MentionID) | map(select(length > 1))' output/dates/*.json

# Check for duplicate MentionIDs in places
jq '.event_mentions | group_by(.MentionID) | map(select(length > 1))' output/places/*.json

# Check for duplicate MentionIDs in weather
jq '.event_mentions | group_by(.MentionID) | map(select(length > 1))' output/weather/*.json
```

### Verify Fixes Work

```bash
# Run phase2 twice
python3 phase2_extract.py
python3 phase2_extract.py

# Check no duplicates created
jq '.event_mentions | group_by(.MentionID) | map(select(length > 1))' output/dates/*.json
```

---

## Files Modified

1. ✅ `src/extraction/equipment.py` - Added processed tracking + duplicate check
2. ✅ `src/extraction/maps.py` - Added processed tracking
3. ✅ `src/extraction/dates.py` - Already has duplicate check by Sub_eventID
4. ✅ `src/extraction/places.py` - Already has duplicate check by Sub_eventID
5. ✅ `src/extraction/weather_central.py` - Already has duplicate check by Sub_eventID

---

## See Also

- **Equipment Bug Fixes:** `docs/current/features/equipment/EQUIPMENT_BUG_FIXES.md`
- **Phase 2 Pipeline:** `docs/current/pipeline/PIPELINE.md`
- **Error Handling:** `contextmanagement/Specs/error_handling.md`
