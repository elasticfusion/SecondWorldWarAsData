# Group Deduplication System

**Date:** 2026-03-08  
**Status:** Production Ready  
**Version:** 2.0

---

## Overview

Automated system for finding and merging duplicate people groups while preventing false positives from hierarchically related or similarly-named distinct entities.

---

## Features

### 1. Intelligent Duplicate Detection

**Name Similarity Matching**
- Fuzzy string matching (70%+ similarity threshold)
- Core name extraction (removes prefixes/suffixes)
- Unicode normalization for ASCII variants
- Substring matching (85%+ similarity required)

**Context Analysis**
- Shared country of origin
- Alliance membership overlap
- Shared event appearances
- Same group type (military_unit, government_organization, etc.)

**False Positive Prevention**
- Skips parent-child relationships
- Detects different unit numbers (1st, 2d, 3d, 9th, 30th, etc.)
- Detects different Roman numerals (V, VII, LXXXVI, etc.)
- Detects different letter designations (Group B vs Group G)
- Prevents hierarchy matches (United States vs United States Army)

### 2. Exclusion Tracking System

**Automatic Exclusion Recording**
- User selects "exclude" during merge review
- Automatically writes to `excluded_merges.md`
- Includes timestamp, group names, and GroupIDs
- Human-readable Markdown format
- Version control friendly

**Automatic Exclusion Loading**
- `find_related_groups.py` reads `excluded_merges.md` on startup
- Parses GroupIDs from code blocks
- Skips excluded clusters automatically
- No re-review of previously excluded groups

**Dual Format Storage**
- `excluded_merges.md` - Human-readable, parsed by find script
- `not_related.json` - Machine-readable, backward compatibility

### 3. Confidence Scoring

**Scoring Factors**
- Name similarity: 0-0.5 points (based on ratio)
- Core name match: 0.6 points (if >80% similar)
- Same type: 0.3 points
- Shared context: 0.1 points
- Shared events: 0.2 points
- Substring match: 0.3 points (if >85% similar)
- Unicode variant: 0.6 points

**Threshold**
- Minimum confidence: 0.8 (increased from 0.5 for precision)
- Only clusters above threshold are suggested for merging

---

## Workflow

### Step 1: Find Related Groups

```bash
python3 scripts/find_related_groups.py
```

**Process:**
1. Loads all group files from `output/people_groups/`
2. Excludes system files (index.json, not_related.json, etc.)
3. Loads exclusion list from `excluded_merges.md`
4. Analyzes groups for relationships
5. Applies false positive filters
6. Generates report with confidence scores
7. Saves to `output/people_groups/related_groups_report.json`

**Output:**
```
INFO:__main__:Found 139 people group file(s)
INFO:__main__:Loaded 2 excluded cluster(s)
INFO:__main__:Analyzing 135 groups for relationships...
INFO:__main__:Found 7 related group clusters
INFO:__main__:Report saved to: output/people_groups/related_groups_report.json

================================================================================
RELATED PEOPLE GROUPS REPORT
================================================================================
Total groups: 135
Related clusters found: 7

1. Confidence: 1.73
   Reasons: Name substring match, Shared context, Same type: military_unit
   - U.S. Eighth Air Force (military_unit) [U_S__Eighth_Air_Force_01KK5YCW.json]
   - Eighth Air Force (military_unit) [Eighth_Air_Force_01KK5408.json]

2. Confidence: 1.49
   Reasons: Same type: military_unit, Shared context, Name similarity: 0.98
   - 2nd SS Panzer Division (military_unit) [2nd_SS_Panzer_Division_01KKPG2S.json]
   - 2d SS Panzer Division (military_unit) [2d_SS_Panzer_Division_01KHXNSG.json]
```

### Step 2: Review and Merge

```bash
python3 scripts/merge_related_groups.py
```

**Interactive Options:**
- **y** - Merge this cluster (combines into primary group)
- **n** - Don't merge, exit script
- **skip** - Skip this cluster, continue to next
- **exclude** - Mark as NOT related, add to exclusion list

**When Merging:**
1. Select primary group (keeps this file)
2. Merges event mentions from all groups
3. Combines alternate names
4. Updates all event files to reference primary GroupID
5. Deletes merged group files

**When Excluding:**
1. Automatically writes to `excluded_merges.md`
2. Includes timestamp and reason
3. Lists all group names and GroupIDs
4. Also updates `not_related.json` for backward compatibility
5. Next run of `find_related_groups.py` will skip this cluster

---

## File Structure

### Input Files

**Group Files**
- Location: `output/people_groups/*.json`
- Format: One JSON file per group
- Contains: group_name, group_type, country_of_origin, event_mentions, etc.

**Exclusion File**
- Location: `output/people_groups/excluded_merges.md`
- Format: Markdown with code blocks containing GroupIDs
- Created automatically when user excludes clusters
- Parsed by `find_related_groups.py` on startup

### Output Files

**Related Groups Report**
- Location: `output/people_groups/related_groups_report.json`
- Format: JSON with clusters array
- Contains: confidence scores, reasons, group details
- Used by `merge_related_groups.py`

**Not Related (Legacy)**
- Location: `output/people_groups/not_related.json`
- Format: JSON with exclusion pairs
- Maintained for backward compatibility

---

## Exclusion File Format

### excluded_merges.md

```markdown
# Excluded Group Merges

This file records group clusters that have been reviewed and explicitly excluded from merging.
These clusters will be skipped in future runs of `find_related_groups.py`.

## Excluded Clusters

### Different Corps Numbers (Roman Numerals)
**Reason:** Different numbered corps are distinct military units, not duplicates
**Date Excluded:** 2026-03-08
**Groups:**
- LXXXVI Corps (LXXXVI_Corps_01K2M3N4.json)
- LXXXIV Corps (LXXXIV_Corps_01F2G3H4.json)
- LXXXVIII Corps (LXXXVIII_Corps_01KK5NX5.json)

**GroupIDs:**
```
01K2M3N4
01F2G3H4
01KK5NX5
```

---

### Excluded on 2026-03-08 11:49
**Reason:** User excluded during merge review
**Date Excluded:** 2026-03-08
**Groups:**
- Test Group 1 (test1.json)
- Test Group 2 (test2.json)

**GroupIDs:**
```
01TEST001
01TEST002
```

---
```

**Parsing Logic:**
- Reads file line by line
- Detects code block boundaries (```)
- Extracts GroupIDs from within code blocks
- Stores as sets for fast lookup
- Compares cluster GroupIDs against excluded sets

---

## False Positive Prevention

### 1. Parent-Child Relationships

**Detection:**
- Checks if one group's name is substring of another
- Checks parent_organization field

**Action:**
- Skip entirely (don't suggest merge)

**Examples:**
- ❌ "United States" ↔ "United States Army"
- ❌ "British Army" ↔ "British Second Army"

### 2. Different Unit Numbers

**Detection:**
- Regex: `\b(\d+)(?:st|nd|rd|th|d)?\b`
- Matches: 1st, 2nd, 3rd, 2d, 3d, 9th, 30th, etc.

**Action:**
- Skip if different numbers found

**Examples:**
- ❌ "1st Armored Division" ↔ "2d Armored Division"
- ❌ "9th Infantry Division" ↔ "30th Infantry Division"

### 3. Different Roman Numerals

**Detection:**
- Regex: `\b([IVXLC]+)\s+(?:Corps|Army|Division|Panzer)`
- Matches: V Corps, VII Corps, LXXXVI Corps, etc.

**Action:**
- Skip if different Roman numerals found

**Examples:**
- ❌ "V Corps" ↔ "VII Corps"
- ❌ "LXXXIV Corps" ↔ "LXXXVI Corps"
- ❌ "XLVII Panzer Corps" ↔ "II SS Panzer Corps"

### 4. Different Letter Designations

**Detection:**
- Regex: `\b(?:group|army|corps)\s+([a-z])\b`
- Matches: Group B, Army G, Corps A, etc.

**Action:**
- Skip if different letters found

**Examples:**
- ❌ "Army Group B" ↔ "Army Group G"
- ❌ "Corps A" ↔ "Corps B"

### 5. Different Countries

**Detection:**
- Compares country_of_origin field

**Action:**
- Skip unless name similarity >90%

**Examples:**
- ❌ "German 1st Army" ↔ "American 1st Army" (different countries)
- ✅ "British Eighth Army" ↔ "8th British Army" (same country, high similarity)

---

## Configuration

### Thresholds

```python
# Name similarity
MIN_SIMILARITY = 0.7          # Initial match threshold
SUBSTRING_SIMILARITY = 0.85   # Required for substring matches
CORE_SIMILARITY = 0.8         # Core name match threshold

# Confidence
MIN_CONFIDENCE = 0.8          # Minimum to suggest merge (was 0.5)

# Weights
NAME_SIMILARITY_WEIGHT = 0.5
CORE_NAME_WEIGHT = 0.6
SAME_TYPE_WEIGHT = 0.3
SHARED_CONTEXT_WEIGHT = 0.1   # Reduced from 0.4
SHARED_EVENTS_WEIGHT = 0.2    # Reduced from 0.5
SUBSTRING_WEIGHT = 0.3        # Reduced from 0.5
UNICODE_VARIANT_WEIGHT = 0.6
```

### System Files to Exclude

```python
excluded_files = {
    "index.json",
    "related_groups_report.json",
    "not_related.json",
    ".processed_events.json",
}
```

---

## Examples

### True Duplicates (Should Merge)

**Naming Variants**
- ✅ "U.S. Eighth Air Force" ↔ "Eighth Air Force"
- ✅ "2nd SS Panzer Division" ↔ "2d SS Panzer Division"
- ✅ "British Second Army" ↔ "2nd British Army"

**Spelling Variants**
- ✅ "Schutzstaffel (SS)" ↔ "SS (Schutzstaffel)"
- ✅ "OKW" ↔ "Oberkommando der Wehrmacht (OKW)"

### False Positives (Should NOT Merge)

**Hierarchies**
- ❌ "United States" ↔ "United States Army"
- ❌ "British Army" ↔ "British Second Army"
- ❌ "U.S. Air Force" ↔ "U.S. Ninth Air Force"

**Different Units**
- ❌ "1st Armored Division" ↔ "2d Armored Division"
- ❌ "V Corps" ↔ "VII Corps"
- ❌ "Army Group B" ↔ "Army Group G"

**Different Organizations**
- ❌ "Joint Planning Staff" ↔ "Combined Planning Staff"
- ❌ "British Chiefs of Staff" ↔ "Combined Chiefs of Staff"

---

## Improvements from Version 1.0

### Detection Improvements

1. **Roman Numeral Support** - Now detects and skips different Roman numeral units
2. **Increased Confidence Threshold** - 0.5 → 0.8 for higher precision
3. **Reduced Context Weights** - Shared context/events less influential
4. **Parent-Child Skip** - Hierarchies now completely skipped
5. **Tighter Substring Matching** - Requires 85%+ similarity

### Exclusion System

1. **Automatic Recording** - User exclusions automatically saved
2. **Dual Format** - Both Markdown (human) and JSON (machine)
3. **Automatic Loading** - Exclusions respected on next run
4. **Timestamp Tracking** - Records when and why excluded
5. **Version Control Friendly** - Markdown format for git

### Results

**Version 1.0:**
- 13 clusters found
- Many false positives (hierarchies, different units)
- Manual exclusion editing required

**Version 2.0:**
- 7 clusters found (46% reduction)
- High precision (mostly true duplicates)
- Automatic exclusion tracking
- No manual editing needed

---

## Troubleshooting

### Issue: Too Many False Positives

**Solution:**
- Increase `MIN_CONFIDENCE` threshold
- Reduce context/event weights
- Add more false positive filters

### Issue: Missing True Duplicates

**Solution:**
- Decrease `MIN_CONFIDENCE` threshold
- Increase `MIN_SIMILARITY` threshold
- Check if groups are in exclusion list

### Issue: Excluded Clusters Still Appearing

**Solution:**
- Verify `excluded_merges.md` exists
- Check GroupIDs are in code blocks
- Ensure code blocks use triple backticks
- Check file is in `output/people_groups/`

### Issue: System Files Being Processed

**Solution:**
- Add filename to `excluded_files` set in script
- System files: index.json, not_related.json, etc.

---

## Future Enhancements

### Potential Improvements

1. **Word Number Detection** - Handle "Nineteenth Army" vs "Fifteenth Army"
2. **Better Hierarchy Detection** - Use parent_organization field more effectively
3. **Prefix Handling** - Better detection of "Combined" vs "Joint" vs "British"
4. **LLM Verification** - Use AI to verify relationships (currently disabled)
5. **Alias Expansion** - Check alternate_names field for matches
6. **Historical Context** - Use date ranges to determine if groups could be same

### Configuration Options

1. **Adjustable Thresholds** - Make weights configurable via config.yaml
2. **Filter Toggles** - Enable/disable specific false positive filters
3. **Batch Mode** - Auto-merge high-confidence matches
4. **Dry Run** - Preview merges without making changes

---

## Related Documentation

- **People Groups Extraction:** `contextmanagement/Specs/people_groups.md`
- **Deduplication:** `docs/current/PEOPLE_GROUPS.md`
- **Error Handling:** `contextmanagement/Specs/error_handling.md`

---

## Scripts

### find_related_groups.py

**Purpose:** Analyze groups and find potential duplicates

**Location:** `scripts/find_related_groups.py`

**Key Functions:**
- `_load_excluded_clusters()` - Load exclusion list
- `_is_cluster_excluded()` - Check if cluster is excluded
- `find_related_groups()` - Main analysis function
- `_similarity_ratio()` - Calculate name similarity
- `_extract_core_name()` - Remove prefixes/suffixes

**Output:** `output/people_groups/related_groups_report.json`

### merge_related_groups.py

**Purpose:** Interactive tool to merge or exclude clusters

**Location:** `scripts/merge_related_groups.py`

**Key Functions:**
- `merge_groups()` - Combine group data
- `add_to_exclusion_list()` - Record exclusions
- `merge_related_cluster()` - Interactive merge process
- `get_user_action()` - Get user decision

**Output:** 
- Merged group files
- `output/people_groups/excluded_merges.md`
- `output/people_groups/not_related.json`

---

**Status:** ✅ Production Ready  
**Last Updated:** 2026-03-08  
**Version:** 2.0
