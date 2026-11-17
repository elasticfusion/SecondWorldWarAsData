# Combined Map Search Strategy

> **Status (2026-11-16):** the combined orchestrator (`src.extraction.combined_map_search`, its
> `import_all_external_maps`, and `test_grok_search.sh`) no longer exists; `scripts/archive/test_grok_search.sh`
> is the stale leftover. What runs today: Phase 3 step `openserp_external_maps`
> (`phase3_enrich_data.py` → `openserp_maps.import_openserp_maps`) when `external_maps.enabled` is true
> (currently `false` in `config.yaml`: expensive); the Grok whitelist search is a separate manual entry
> point, `python3 -m src.extraction.grok_search_maps` (5 places, hard-coded in `main()`). The
> two-phase "Grok first, then OpenSERP" flow below is the design, not an automated run. "Phase 1/2"
> in this doc mean search stages, not pipeline phases.

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

Sites are loaded from the `whitelist:` section of `config/domain_blacklist.yaml` (excerpt):

```yaml
whitelist:
- alaskool.org
- www.ww2alaska.com
- www.normandywarguide.com
- wikipedia.org
- grokipedia.com
- dhc.westpoint.edu/atlases/
```

**To add sites**: Edit `config/domain_blacklist.yaml` and add to the `whitelist:` section.

**Current whitelist** (`config/domain_blacklist.yaml`, 2026-11-16): `alaskool.org`,
`www.ww2alaska.com`, `www.normandywarguide.com`, `wikipedia.org`, `grokipedia.com`,
`dhc.westpoint.edu/atlases/` (West Point Digital History Center), `ibiblio.org` (HyperWar host).
`load_whitelisted_sites` falls back to `loc.gov`, `archives.gov`, `wikipedia.org` when the file or
list is missing.

## Usage

### Grok whitelist search (manual, 5 places)
```bash
python3 -m src.extraction.grok_search_maps
```

### OpenSERP search (Phase 3)
Runs inside `phase3_enrich_data.py` as step `openserp_external_maps` when
`external_maps.enabled: true` (OpenSERP service scaled up on demand). Tunables in `config.yaml`
`external_maps`: `max_places`, `search_limit`, `page_download_timeout`, `image_download_timeout`,
`image_storage_path`. There is no standalone CLI for it.

## Output

**Images**: Grok search CLI: `filestore/external_maps/{MapID}.{ext}` (`grok_search_maps.main`). OpenSERP: only if
`external_maps.image_storage_path` is set (no value in `config.yaml`; without it no image is downloaded).  
**JSON**: `output/external_maps/{MapID}.json`

Each JSON includes `found_via` field:
- `"grok_search"` - Found in Phase 1 (whitelist)
- `"OpenSERP <engine> search"` (for example `OpenSERP google search`) - Found in Phase 2 (broader search)

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

Edit `config/domain_blacklist.yaml`:

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

## Integration with the pipeline

Only the OpenSERP stage is wired in, in **Phase 3** (see Usage). External maps are no longer
imported from Phase 2 by web search; Phase 2 imports only the local `external_maps.yaml` corpus
(`phase2_extract._extract_external_maps`, also gated on `external_maps.enabled`).

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

- `src/extraction/grok_search_maps.py` - Phase 1 (whitelist; whitelist loaded from
  `config/domain_blacklist.yaml`, NOT a hardcoded constant — see "Whitelisted Sites" above; the dated change note is in `docs/archive/2026-11-16/features/external-maps/whitelist-config.md`)
- `src/extraction/openserp_maps.py` - Phase 2 (broader)
- `tools/setup_openserp.sh` / `tools/search_maps.go` - OpenSERP tooling (under `tools/`)

## Choosing a method (merged from the retired grok-search.md)

| Method | Source control | Verification | Setup | When to use |
|---|---|---|---|---|
| **Grok whitelist** (`grok_search_maps.py`) | Whitelist (`domain_blacklist.yaml`) | Grok **vision** (sees the image) | Python-only | Trusted authoritative sources; default Phase 1 |
| **OpenSERP** (`openserp_maps.py`) | Blacklist | Content download plus Grok vision and licence check | ECS OpenSERP service (Phase 3) or local `tools/setup_openserp.sh` | Guaranteed real multi-engine results; Phase 2 breadth |
| **Manual YAML** (`external_maps.py`) | Full manual control | Metadata only | Edit a YAML file | A small, hand-curated set of specific maps |

There is no combined orchestrator any more (see the status note at the top). The pipeline runs OpenSERP
(Phase 3, when `external_maps.enabled`) and the local YAML import (Phase 2, same flag); the Grok whitelist
search is run by hand. OpenSERP skips URLs already downloaded into `output/external_maps/`
(`SearchHistory.get_downloaded_urls`), which covers maps the Grok search imported earlier.
