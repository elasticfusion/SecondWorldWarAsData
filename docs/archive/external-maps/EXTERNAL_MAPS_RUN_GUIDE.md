# External Maps - Run Guide

**Date:** 2026-02-24  
**Status:** Ready to Run

---

## Quick Start

```bash
# Run search for all 220 places
./run_external_maps_search.sh
```

Or directly:
```bash
python3 -m src.extraction.search_external_maps
```

---

## What It Does

1. Reads all 220 places from `output/places/*.json`
2. For each place:
   - Extracts place name, date, event context
   - Waits 2 seconds (rate limiting)
   - Calls Grok to search online archives
   - Validates and imports found maps
3. Saves maps to `output/external_maps/`

---

## Rate Limiting

**Configuration:** 2 seconds between requests (30 requests/minute)

**Why?** Prevents API abuse and service throttling

**Estimated Time:**
- 220 places × 2 seconds = 440 seconds (~7-8 minutes)
- Plus Grok processing time per request

---

## Expected Results

**Not all places will have maps:**
- Some places are too specific (e.g., "Saint-Vaast-la-Hougue")
- Some places lack archival maps
- Grok returns empty array `[]` when no maps found

**Typical success rate:** 10-30% of places

**Example from test run (5 places):**
- Burma Road: 4 maps found ✅
- Sardinia: 5 maps found ✅
- Saint-Vaast-la-Hougue: 0 maps ❌
- Calais: 0 maps ❌
- Turkey: 0 maps ❌

---

## Monitoring Progress

### Watch Live
```bash
tail -f logs/external_maps_search_*.log
```

### Check Count
```bash
ls output/external_maps/*.json | wc -l
```

### View Imported Maps
```bash
jq -r '.map_title' output/external_maps/*.json
```

---

## Output

Each map saved as JSON:
```json
{
  "MapID": "01KJ...",
  "map_title": "Burma Road",
  "external_source": "Library of Congress",
  "external_source_url": "https://...",
  "license": "Public Domain",
  "EventID": "01KJ...",
  "place_name": "Burma Road",
  "found_via": "Grok search for Burma Road",
  "found_date": "2026-02-24"
}
```

---

## Error Handling

### Validation Failures
Maps without required fields are skipped:
```
⚠ Invalid map data, skipping: unknown
```

### Duplicates
Maps already imported are skipped:
```
⚠ Map already exists, skipping: Burma Road
```

### Grok Failures
Continues to next place:
```
✗ Grok search failed for PlaceName: error
```

---

## After Completion

### Review Results
```bash
# Count imported maps
ls output/external_maps/*.json | wc -l

# List map titles
jq -r '.map_title' output/external_maps/*.json | sort

# Check sources
jq -r '.external_source' output/external_maps/*.json | sort | uniq -c
```

### Check Logs
```bash
# View summary
tail -20 logs/external_maps_search_*.log

# Search for errors
grep ERROR logs/external_maps_search_*.log
```

---

## Re-running

**Safe to re-run:**
- Duplicate detection prevents re-importing
- Uses cache for repeated searches
- Idempotent operation

**To force fresh search:**
```bash
# Clear cache
rm -rf cache/api/external_maps/

# Run again
./run_external_maps_search.sh
```

---

## Troubleshooting

### No Maps Found
- Normal for many places
- Check if place names are too specific
- Review Grok responses in cache

### Rate Limit Errors
- Increase `RATE_LIMIT_DELAY` in script
- Default: 2 seconds (30/minute)

### API Errors
- Check GROK_API_KEY in environment
- Check API quota/limits
- Review error logs

---

## Performance

**Estimated Time:** 7-8 minutes for 220 places

**Breakdown:**
- Rate limiting: 440 seconds (7.3 minutes)
- Grok processing: ~1-2 seconds per request
- Total: ~8-10 minutes

**API Calls:** 220 (one per place)

**Cache:** Subsequent runs use cache (much faster)

---

## Next Steps

After search completes:

1. **Review imported maps**
   ```bash
   jq '.' output/external_maps/*.json | less
   ```

2. **Check quality**
   - Verify sources are legitimate
   - Check licenses
   - Review descriptions

3. **Update documentation**
   - Note success rate
   - Document common sources
   - List map types found

---

**Ready to run!** Execute `./run_external_maps_search.sh`
