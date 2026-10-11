# Grok Search Maps - Whitelisted Sites + Vision Verification

**Approach:** Use Grok's search capability with site whitelisting, download images, verify with Grok vision API.

## How It Works

```
1. Grok searches whitelisted sites for maps
   ↓
2. Download image files from results
   ↓
3. Grok vision API verifies relevance
   ↓
4. Save verified maps (JSON + image)
```

## Whitelisted Sites

- loc.gov (Library of Congress)
- archives.gov (National Archives)
- iwm.org.uk (Imperial War Museum)
- wikipedia.org / wikimedia.org
- army.mil / history.army.mil
- ibiblio.org
- naval-history.net

## Usage

### Test with 5 places
```bash
python3 -m src.extraction.grok_search_maps
```

### Process all places
Edit `grok_search_maps.py` and change:
```python
max_places=None  # Process all places
```

## Output

**JSON files**: `output/external_maps/{MapID}.json`
**Images**: `filestore/external_maps/{MapID}.{ext}`

## Advantages vs OpenSERP

| Feature | OpenSERP | Grok Search |
|---------|----------|-------------|
| Search method | Real engines | Grok search |
| Site control | Blacklist | Whitelist |
| Verification | Content download | Vision API |
| Setup | Requires Go/OpenSERP | Python only |
| Speed | ~3s/place | ~4-5s/place |

## Advantages vs Manual YAML

- Automated discovery
- Vision verification (not just metadata)
- Processes all 220 places
- No manual curation needed

## Configuration

Edit `WHITELISTED_SITES` in `grok_search_maps.py`:
```python
WHITELISTED_SITES = [
    "loc.gov",
    "archives.gov",
    # Add more trusted sites
]
```

## Example Output

```
[1/5] Normandy
   Found 3 potential map(s)
   🔍 D-Day Landing Beaches Map...
   ✅ Verified: Shows Normandy beaches with military positions
   ✅ Imported: 01KJXYZ...
```

## Integration with Phase 2

To integrate with phase2_extract.py, add to the external maps section:

```python
from src.extraction.grok_search_maps import import_grok_search_maps

# After other extractions
if config.get("external_maps", {}).get("use_grok_search", False):
    imported = import_grok_search_maps(
        places_dir=paths["places_dir"],
        output_dir=paths["external_maps_dir"],
        image_storage_path=paths["image_storage_path"],
        grok_client=grok_client,
        max_places=config.get("external_maps", {}).get("max_places"),
    )
    logger.info(f"Imported {imported} maps via Grok search")
```

## Caching

- Search results cached in `cache/api/grok_search_maps/`
- Vision verification cached in `cache/api/vision_verification/`
- Re-runs use cached results (fast)

## Comparison to Existing Methods

### Method 1: Manual YAML (external_maps.py)
- ✅ Full control
- ❌ Manual curation required
- ❌ Limited coverage

### Method 2: OpenSERP (openserp_maps.py)
- ✅ Real search engines
- ✅ Zero hallucinations
- ❌ Requires Go + OpenSERP setup
- ❌ Complex architecture

### Method 3: Grok Search (grok_search_maps.py) ⭐ NEW
- ✅ Python-only (no external tools)
- ✅ Vision verification (sees actual images)
- ✅ Whitelisted sites (trusted sources)
- ✅ Simple architecture
- ⚠️ Depends on Grok search quality

## Recommendation

**Use Grok Search if:**
- You want Python-only solution
- You trust Grok's search on whitelisted sites
- You want vision verification of actual images

**Use OpenSERP if:**
- You need guaranteed real search results
- You can set up Go + OpenSERP
- You want multi-engine aggregation

**Use Manual YAML if:**
- You have specific maps to import
- You want full control over sources
- You're curating a small collection
