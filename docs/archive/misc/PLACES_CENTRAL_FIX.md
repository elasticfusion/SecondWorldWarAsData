# Places Central Repository - Implementation Complete

**Date:** 2026-02-23  
**Status:** ✅ Fixed

---

## Problem

Places were being written to book-specific directories instead of central repository:
```
❌ output/BreakoutAndPursuit/chapter1a-places.json
❌ output/Cross-Channel-Attack/chapter0a-places.json
```

## Solution

Rewrote `extract_places()` to use central repository pattern (like people):
```
✅ output/places/Warsaw_01H8XYZ8.json
✅ output/places/London_01LONDON.json
✅ output/places/index.json
```

---

## Changes Made

### 1. `src/extraction/places.py`

**Before:**
```python
def extract_places(event_file, grok_client, output_dir):
    # Extracted to per-chapter JSON files
    output_file = output_dir / f"{stem}-places.json"
```

**After:**
```python
def extract_places(event_file, grok_client, places_dir):
    # Extracts to central repository
    # Creates/updates individual place files
    # Maintains index.json
```

**New Functions:**
- `_find_or_create_place()` - Find existing place or create new file
- `_add_event_mention()` - Add event mention to place file

### 2. `phase2_extract.py`

**Before:**
```python
places_output = extract_places(
    event_file=output_file,
    grok_client=grok_client,
    output_dir=parsed_file.parent,  # ❌ Book directory
)
```

**After:**
```python
central_places_dir = paths["output_root"] / "places"  # ✅ Central
places_output = extract_places(
    event_file=output_file,
    grok_client=grok_client,
    places_dir=central_places_dir,
)
```

**Also:**
- Removed `places_file` check (no longer per-chapter)
- Always runs place extraction (updates central repo)

---

## How It Works

### Extraction Flow

1. **Extract place mentions** from event text (via Grok API)
2. **For each mention:**
   - Check if place exists in `index.json`
   - If exists: Load existing place file
   - If new: Create new place file with PlaceID
3. **Add event mention** to place's `event_mentions` array
4. **Update index.json** with place name → filename mapping

### File Structure

```
output/places/
├── Warsaw_01H8XYZ8.json          # Individual place file
│   {
│     "PlaceID": "01H8XYZ8...",
│     "current_name": "Warsaw",
│     "event_mentions": [
│       {
│         "MentionID": "01...",
│         "Event_Name": "Invasion of Poland",
│         "EventID": "01...",
│         "Sub_event_Name": "...",
│         "Sub_eventID": "01...",
│         "original_text": "Warsaw"
│       }
│     ]
│   }
├── London_01LONDON.json
├── Normandy_01NORMANDY.json
└── index.json                     # Name → filename lookup
    {
      "warsaw": "Warsaw_01H8XYZ8.json",
      "london": "London_01LONDON.json"
    }
```

---

## Benefits

✅ **Cross-book tracking** - See all mentions of "Warsaw" across all books  
✅ **No duplicates** - One file per place  
✅ **Incremental updates** - New mentions added to existing places  
✅ **Fast lookup** - Index provides O(1) name → file mapping  
✅ **Consistent with people** - Same pattern as people/people_groups  

---

## Usage

### Run Extraction
```bash
python3 phase2_extract.py
```

Places automatically extracted to `output/places/`

### Query Places
```python
from pathlib import Path
import json

# Load index
with open("output/places/index.json") as f:
    index = json.load(f)

# Find place
place_file = Path("output/places") / index["warsaw"]

# Load place data
with open(place_file) as f:
    warsaw = json.load(f)

# See all events mentioning Warsaw
for mention in warsaw["event_mentions"]:
    print(f"{mention['Event_Name']} - {mention['book']}")
```

---

## Next Steps

1. ✅ Places write to central repository
2. Create `scripts/find_duplicate_places.py`
3. Create `scripts/merge_duplicate_places.py`
4. Create `scripts/consolidate_places.py` (apply aliases)
5. Populate `place_aliases.yaml` as duplicates found

---

## Testing

```bash
# Run extraction
python3 phase2_extract.py

# Check output
ls -la output/places/

# Should see:
# - Individual place JSON files
# - index.json
# - No per-chapter place files in book directories
```

---

**Status:** ✅ Complete  
**Files Modified:** 2 (places.py, phase2_extract.py)  
**Breaking Change:** Yes (old per-chapter files no longer created)
