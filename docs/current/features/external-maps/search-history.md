# OpenSERP Search History

**Feature:** Track searched places and downloaded URLs to avoid duplicate work.

---

## How It Works

### 1. Search History Tracking
- Tracks which places have been searched
- Stored in `cache/openserp_search_history.json`
- Skips previously searched places by default

### 2. Downloaded URL Tracking
- Scans `output/external_maps/*.json` for existing URLs
- Skips URLs that have already been downloaded
- Checks both `external_source_url` and `source_url` fields

---

## Benefits

✅ **Avoid duplicate searches** - Don't re-search places  
✅ **Avoid duplicate downloads** - Don't re-download same URLs  
✅ **Faster re-runs** - Skip completed work  
✅ **Resume capability** - Continue from where you left off  

---

## Search History File

**Location:** `cache/openserp_search_history.json`

**Format:**
```json
{
  "searched_places": [
    "normandy",
    "paris",
    "berlin"
  ],
  "last_updated": "2026-03-02T14:30:00Z",
  "total_searches": 3
}
```

---

## Usage

### Default Behavior (Skip Searched)
```bash
python3 -m src.extraction.combined_map_search --max-places 220
```

Output:
```
Search history: 50 places previously searched
Found 120 previously downloaded URLs (will skip)

[1/220] Normandy - ⏭️  Previously searched
[2/220] Paris
   ✓ Found 10 potential map(s)
   ⏭️  Already downloaded: D-Day Map...
   ✅ Imported: Liberation Map...
```

### Force Re-search
```bash
python3 -m src.extraction.combined_map_search --no-skip-searched
```

This will:
- Re-search all places (even previously searched)
- Still skip downloaded URLs (prevents duplicates)

### Clear History
```bash
rm cache/openserp_search_history.json
```

---

## When Places Are Marked as Searched

A place is marked as searched when:
1. OpenSERP search completes (even if no results)
2. Immediately after search, before verification
3. Regardless of import success/failure

This ensures we don't repeatedly search places with no results.

---

## URL Deduplication

URLs are checked against:
1. **Phase 1 (Grok)** - Existing maps in `output/external_maps/`
2. **Phase 2 (OpenSERP)** - Same check, plus Phase 1 results

**Checked fields:**
- `external_source_url` - Page URL
- `source_url` - Image URL

If either matches, the URL is skipped.

---

## Example Workflow

### First Run
```bash
python3 -m src.extraction.combined_map_search --max-places 50
```

Result:
- Searches 50 places
- Imports 30 maps
- Marks 50 places as searched
- Saves 30 URLs as downloaded

### Second Run (More Places)
```bash
python3 -m src.extraction.combined_map_search --max-places 100
```

Result:
- Skips first 50 places (already searched)
- Searches places 51-100
- Skips any URLs already downloaded
- Marks places 51-100 as searched

### Re-run Same Places (Force)
```bash
python3 -m src.extraction.combined_map_search --max-places 50 --no-skip-searched
```

Result:
- Re-searches all 50 places
- Still skips downloaded URLs
- Updates search history

---

## Monitoring

### Check Search History
```bash
cat cache/openserp_search_history.json
```

### Count Downloaded Maps
```bash
ls output/external_maps/*.json | wc -l
```

### See Skipped Places
Look for log lines:
```
[5/220] Berlin - ⏭️  Previously searched
```

### See Skipped URLs
Look for log lines:
```
⏭️  Already downloaded: Map Title...
```

---

## Summary Statistics

At the end of OpenSERP phase:
```
⏭️  Skipped 50 previously searched place(s)
⏭️  Skipped 120 previously downloaded URL(s)
```

---

## Integration with Combined Search

The combined search strategy automatically:
1. **Phase 1 (Grok)** - No history tracking (always searches)
2. **Phase 2 (OpenSERP)** - Uses search history (skips searched places)

This ensures:
- Grok whitelist always runs (high-quality sources)
- OpenSERP skips redundant work (efficiency)

---

## Troubleshooting

### "Previously searched" but want to re-search
```bash
# Option 1: Clear history
rm cache/openserp_search_history.json

# Option 2: Force re-search
python3 -m src.extraction.combined_map_search --no-skip-searched
```

### "Already downloaded" but file is missing
The JSON exists but image might be missing. The system checks JSON files, not images.

To re-download:
```bash
# Remove the JSON file
rm output/external_maps/{MapID}.json

# Re-run search
python3 -m src.extraction.combined_map_search --no-skip-searched
```

### History file corrupted
```bash
# Delete and start fresh
rm cache/openserp_search_history.json
```

---

## Files

- `src/extraction/search_history.py` - History tracking module
- `src/extraction/openserp_maps.py` - Uses history in OpenSERP search
- `cache/openserp_search_history.json` - Search history data
- `output/external_maps/*.json` - Downloaded map records
