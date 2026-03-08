# Combined Map Search Strategy

**Approach:** Run Grok whitelist search first (high quality), then OpenSERP (broader coverage).

## Strategy

```
Phase 1: Grok Whitelist Search
  → Searches high-quality sources
  → West Point DHC, LOC, NARA, IWM, etc.
  → Vision verification
  → Imports verified maps
  
Phase 2: OpenSERP Search
  → Searches broader web (Google/Bing/DuckDuckGo)
  → Duplicate detection (skips Phase 1 maps)
  → Content verification
  → Imports additional maps
```

## Why This Order?

1. **Quality First** - Whitelisted sites are curated, authoritative sources
2. **Efficiency** - High-quality maps found first, reducing OpenSERP load
3. **Deduplication** - OpenSERP skips maps already found by Grok
4. **Fallback** - If Grok finds nothing, OpenSERP provides broader coverage

## Whitelisted Sites (Phase 1)

Sites are loaded from `domain_blacklist.yaml` whitelist section:

```yaml
whitelist:
- alaskool.org
- www.ww2alaska.com
- www.normandywarguide.com
- wikipedia.org
- grokipedia.com
- dhc.westpoint.edu/atlases/
```

**To add sites**: Edit `domain_blacklist.yaml` and add to the `whitelist:` section.

**Current whitelist** (as of 2026-03-02):
- `dhc.westpoint.edu/atlases/` - West Point Digital History Center ⭐
- `wikipedia.org` - Wikipedia
- `grokipedia.com` - Grokipedia
- `alaskool.org` - Alaska history
- `www.ww2alaska.com` - WWII Alaska
- `www.normandywarguide.com` - Normandy War Guide

## Usage

### Quick Test (5 places)
```bash
./test_grok_search.sh
```

Or directly:
```bash
python3 -m src.extraction.combined_map_search --max-places 5
```

### Process All Places
```bash
python3 -m src.extraction.combined_map_search --max-places 220
```

### Grok Only (Skip OpenSERP)
```bash
python3 -m src.extraction.combined_map_search --skip-openserp
```

### Custom OpenSERP URL
```bash
python3 -m src.extraction.combined_map_search --openserp-url http://localhost:7002
```

## Output

**Images**: `filestore/external_maps/{MapID}.{ext}`  
**JSON**: `output/external_maps/{MapID}.json`

Each JSON includes `found_via` field:
- `"grok_search"` - Found in Phase 1 (whitelist)
- `"openserp"` - Found in Phase 2 (broader search)

## Example Output

```
Phase 1: Grok Whitelist Search
[1/5] Normandy
   Found 3 potential map(s)
   🔍 D-Day Beaches - West Point Atlas...
   ✅ Verified: Shows Normandy landing zones
   ✅ Imported: 01KJXYZ...
   
✅ Phase 1 complete: 8 maps from whitelisted sites

Phase 2: OpenSERP Search
[1/5] Normandy
   ✓ Found 15 potential map(s) from OpenSERP
   🔍 Verifying: Normandy Campaign Map...
   ⚠️  Duplicate: Already imported in Phase 1
   🔍 Verifying: Allied Advance Map...
   ✅ Imported: 01KJXYZ...
   
✅ Phase 2 complete: 5 additional maps from OpenSERP

Import Summary
Grok whitelist:  8 maps
OpenSERP:        5 maps
Total imported:  13 maps
```

## Duplicate Detection

Maps are deduplicated by URL:
- Checks `external_source_url` and `source_url`
- Phase 2 skips any URL found in Phase 1
- Prevents duplicate downloads and processing

## Performance

**Phase 1 (Grok):**
- ~5-8s per place
- Vision verification for each image
- High success rate on whitelisted sites

**Phase 2 (OpenSERP):**
- ~3-5s per place
- Content verification
- Lower success rate (broader search)

**Total:** ~8-13s per place with both phases

**220 places:** ~30-50 minutes total

## Configuration

### Adding Whitelisted Sites

Edit `domain_blacklist.yaml`:

```yaml
whitelist:
- dhc.westpoint.edu/atlases/
- wikipedia.org
- your-trusted-site.com  # Add here
```

Sites in the whitelist are:
- Searched by Grok in Phase 1
- Considered high-quality sources
- Processed before broader OpenSERP search

### Removing Sites from Whitelist

Simply remove the line from `domain_blacklist.yaml` whitelist section.

## Integration with Phase 2

Add to `phase2_extract.py`:

```python
from src.extraction.combined_map_search import import_all_external_maps

if config.get("external_maps", {}).get("enabled"):
    grok_count, openserp_count = import_all_external_maps(
        places_dir=paths["places_dir"],
        output_dir=paths["external_maps_dir"],
        image_storage_path=paths["image_storage_path"],
        grok_client=grok_client,
        max_places=config.get("external_maps", {}).get("max_places"),
        use_openserp=config.get("external_maps", {}).get("use_openserp", True),
    )
    logger.info(f"Imported {grok_count + openserp_count} external maps")
```

## Advantages

✅ **Best of both worlds** - Quality + coverage  
✅ **Efficient** - High-quality sources first  
✅ **No duplicates** - Automatic deduplication  
✅ **Flexible** - Can skip OpenSERP if desired  
✅ **Transparent** - Clear logging of each phase  

## Monitoring

Watch for:
- **Phase 1 success rate** - Should be high (50-80%)
- **Phase 2 duplicates** - Shows overlap between methods
- **Phase 2 additions** - New maps not in whitelisted sites
- **Total coverage** - Combined results per place

## Troubleshooting

**No Phase 1 results:**
- Check Grok API key
- Verify whitelisted sites are accessible
- Review vision verification logs

**No Phase 2 results:**
- Check OpenSERP is running: `curl http://localhost:7001`
- Verify `search_maps` binary exists
- Check OpenSERP logs

**Too many duplicates in Phase 2:**
- Good sign! Means Phase 1 found most maps
- OpenSERP is working as fallback

## Files

- `src/extraction/combined_map_search.py` - Orchestrator
- `src/extraction/grok_search_maps.py` - Phase 1 (whitelist)
- `src/extraction/openserp_maps.py` - Phase 2 (broader)
- `test_grok_search.sh` - Quick test script
