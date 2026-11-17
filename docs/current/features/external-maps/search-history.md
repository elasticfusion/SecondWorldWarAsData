# OpenSERP Search History

> **Status (2026-11-16):** `src.extraction.combined_map_search` and its CLI flags (`--max-places`,
> `--no-skip-searched`) no longer exist. The history is used by `openserp_maps.import_openserp_maps`
> (Phase 3 step `openserp_external_maps`, `skip_searched=True` by default; no CLI flag). Wherever a
> command below names `combined_map_search`, read it as that Phase 3 step with the matching
> `max_places` / `skip_searched` argument.

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

There is no CLI. The history is used only inside `openserp_maps.import_openserp_maps` (Phase 3 step `openserp_external_maps`,
`external_maps.enabled: true`), which always passes `skip_searched=True` from `phase3_enrich_data`. The commands in this section show the
equivalent behaviour; set `external_maps.max_places` in `config.yaml` for the `--max-places` limit.

### Default Behavior (Skip Searched)
`external_maps.max_places: 220`

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
Delete the history file (below), or call `import_openserp_maps(..., skip_searched=False)` from Python. Phase 3 has no setting for it.

This will:
- Re-search all places (even previously searched)
- Still skip downloaded URLs (prevents duplicates)

### Clear History
```bash
rm cache/openserp_search_history.json
```

---

## When Places Are Marked as Searched

A place is marked as searched (`openserp_maps._process_place_file`) immediately after `search_with_openserp` returns, before verification.
That includes a search that returned nothing because it failed (an OpenSERP outage, or the `./tools/search_maps` binary missing; the
`subprocess` error is caught and returns `[]`). Such a place is skipped on later runs until the history file is deleted.

Places whose only provenance is a map source (`_is_map_derived_only`) are skipped without being marked, to stop map -> place -> map recursion
(`OBSERV map_source.search_skipped`).

The history file lives under `cache/`. Only `output/` and `artifacts/` are synced to S3, so on ECS the history starts empty in each task
(UNVERIFIED for the `cache/` directory in the Phase 3 task; the downloaded-URL check below reads `output/external_maps/` and does persist).

---

## URL Deduplication

URLs are checked against:
Every record in `output/external_maps/` (`SearchHistory.get_downloaded_urls`), whichever method wrote it (Grok search, OpenSERP, YAML import).

**Checked fields:**
- `external_source_url` - Page URL
- `source_url` - Image URL

If either matches, the URL is skipped.

---

## Example Workflow

### First Run
`external_maps.max_places: 50`

Result:
- Searches 50 places
- Imports 30 maps
- Marks 50 places as searched
- Saves 30 URLs as downloaded

### Second Run (More Places)
`external_maps.max_places: 100`

Result:
- Skips first 50 places (already searched)
- Searches places 51-100
- Skips any URLs already downloaded
- Marks places 51-100 as searched

### Re-run Same Places (Force)
`external_maps.max_places: 50` with `skip_searched=False`

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

1. **Grok whitelist stage** (`grok_search_maps`, manual) - No place history; it skips a URL already in `output/external_maps/` (`check_duplicate_by_url`)
2. **OpenSERP (Phase 3)** - Uses search history (skips searched places)

This ensures:
- Grok whitelist always runs (high-quality sources)
- OpenSERP skips redundant work (efficiency)

---

## Troubleshooting

### "Previously searched" but want to re-search
```bash
# Option 1: Clear history
rm cache/openserp_search_history.json

# Option 2: call import_openserp_maps(..., skip_searched=False) from Python
```

### "Already downloaded" but file is missing
The JSON exists but image might be missing. The system checks JSON files, not images.

To re-download:
```bash
# Remove the JSON file
rm output/external_maps/{MapID}.json

# Re-run Phase 3 with external_maps.enabled: true (the place stays "searched" in the history, so
# also delete cache/openserp_search_history.json or use skip_searched=False)
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
