# Domain Blacklist Configuration

The `config/domain_blacklist.yaml` file controls which domains and URL paths are excluded from external map search results.

> **Status (2026-11-17), checked against code:**
> - The Go filter is `tools/search_maps.go`, run by `openserp_maps.search_with_openserp` as `./tools/search_maps -place ... -limit ... -openserp <url>`
>   with NO `-blacklist` flag. The flag defaults to `domain_blacklist.yaml` in the working directory, and the file lives at
>   `config/domain_blacklist.yaml`, so the Go tool prints a warning and filters with an empty blacklist. Pass `-blacklist config/domain_blacklist.yaml` to make
>   the rules below apply (not done in code today).
> - `tools/` is not copied into the Docker image (`Dockerfile` copies `src/`, `scripts/`, `prompts/`, `search_queries/`, `config/`, `data/`), so
>   the Phase 3 step `openserp_external_maps` cannot run the binary on ECS (the `subprocess` failure is caught and the search returns no results).
> - The Go tool never writes to the YAML. The `# Filtered:` audit comments below are not produced by any code. The only automatic write is Python's
>   `openserp_maps._add_to_blacklist`, which inserts a domain plus `# Blacklisted: <url> (license rejected)` when the licence check rejects a page.
> - Whitelist precedence in the Go code (`filterMapURLs`): a whitelisted URL SKIPS the blacklist and source-material checks. Where this doc
>   says "blacklist always wins", the code does the opposite.
> - Other readers of the same file: `grok_search_maps.load_whitelisted_sites` (`whitelist:`), `source_retrieval.load_blacklist` (`blacklist:`),
>   `link_relevancy` (`link_relevancy_whitelist:`, separate list), and `runtime_tables` (the file must parse and `blacklist:` must be non-empty).
> - Phase 3 OpenSERP enrichment (people, equipment, groups, places) does not use this file; it drops results with the URL registry's site denylist
>   (`openserp_enrichment._drop_excluded_domains`).

## How It Works

When OpenSERP returns search results, the Go search tool (`search_maps`) filters out URLs before sending results to Python for verification.

**Approach:** Blacklist (reject known bad domains/paths) instead of whitelist (accept only known good domains)

**Logging:** Only licence rejections are logged to the YAML (see the status note). Example written by `_add_to_blacklist`:
```yaml
- curtiswrightmaps.com
# Blacklisted: https://curtiswrightmaps.com/terms-and-conditions/ (license rejected)
```

## Two Types of Filtering

### 1. Domain Blacklist
Blocks entire domains or domain paths.

### 2. Source Material Paths
Blocks specific URL paths that are already in our repository as source material.

### 3. Whitelist (Override)
Allows specific domains/paths that would otherwise be blocked. Useful for:
- Legitimate subdomains of blacklisted sites (e.g., `en.wikipedia.org` when `wikipedia.org` is blacklisted)
- Specific paths on otherwise problematic domains
- Correcting overly broad blacklist rules

**Important:** In the Go filter the whitelist takes precedence: a URL that matches the whitelist skips the blacklist and source-material checks (it must still contain "map" and the place name).

**Why separate?** Allows blocking specific paths (e.g., our source documents) while keeping the rest of the domain available for other maps.

## Filter Logic

Results are included if:
1. ✅ In whitelist (bypasses other checks) OR
2. ✅ NOT in domain blacklist AND
3. ✅ NOT in source material paths AND
4. ✅ Contains "map" in title/description/URL AND
5. ✅ Mentions the place name

**Precedence:** Whitelist > Blacklist (a whitelisted URL is never blocked by the blacklist or source-material paths)

Results are excluded if (and not whitelisted):
- ❌ URL contains any blacklisted domain (case-insensitive substring match)
- ❌ URL contains any source material path (case-insensitive substring match)

## Configuration Examples

### Block Entire Domain
```yaml
blacklist:
  - pinterest.com
```
Result: All pinterest.com URLs blocked

### Whitelist Specific Path
```yaml
blacklist:
  - wikipedia.org
whitelist:
  - en.wikipedia.org/wiki/File:
```
Result:
- ✅ `en.wikipedia.org/wiki/File:Map.jpg` - Allowed (whitelisted)
- ❌ `en.wikipedia.org/wiki/Article` - Blocked (not whitelisted)
- ❌ `de.wikipedia.org/wiki/File:Map.jpg` - Blocked (not whitelisted)
(The whitelist match is a substring test on the whole URL, so `wikipedia.org` as a whitelist entry, as in the checked-in file, whitelists every wikipedia.org URL.)

### Conflict Resolution (Whitelist Wins)
```yaml
blacklist:
  - pinterest.com
whitelist:
  - pinterest.com/historical
```
Result:
- ✅ `pinterest.com/historical/maps` - Allowed (whitelist match skips the blacklist)
- ❌ `pinterest.com/anything` - Blocked

### Block Specific Path (Source Material)
```yaml
source_material_paths:
  - ibiblio.org/hyperwar/USA/USA-E-Breakout/maps/
```
Result:
- ✅ `ibiblio.org/other/content/` - Allowed
- ❌ `ibiblio.org/hyperwar/USA/USA-E-Breakout/maps/` - Blocked (already in our repo)

## Default Blacklist Categories

### Social Media
- facebook.com, twitter.com, instagram.com, tiktok.com, reddit.com

### Commercial/Shopping
- amazon.com, ebay.com, etsy.com, shopify.com

### Ad/Spam Sites
- pinterest.com, clickbait

### Modern Mapping (not historical)
- google.com/maps, maps.google.com, bing.com/maps

### User-Generated Content (unreliable)
- blogspot.com, wordpress.com, medium.com, tumblr.com

### Video Platforms (not primary sources)
- youtube.com, vimeo.com, dailymotion.com

## Adding Source Material Paths

When you add a new book/document to the repository:

1. Check the `external_source_url` in metadata YAML
2. Extract the base path (without specific filenames)
3. Add to `source_material_paths` in `domain_blacklist.yaml`

**Example:**
```yaml
# From chapter1-meta.yaml
external_source_url: "https://www.ibiblio.org/hyperwar/USA/USA-E-Breakout/maps/"

# Add to domain_blacklist.yaml
source_material_paths:
  - ibiblio.org/hyperwar/USA/USA-E-Breakout/maps/
```

## Adding Domains to Blacklist

Edit `domain_blacklist.yaml`:

```yaml
blacklist:
  - existing-domain.com
  - new-domain-to-block.com
```

**Note:** Substring matching is used, so:
- `pinterest.com` blocks `www.pinterest.com`, `uk.pinterest.com`, etc.
- `blogspot.com` blocks all blogspot subdomains

## Adding Domains to Whitelist

Edit `domain_blacklist.yaml`:

```yaml
whitelist:
  - specific-subdomain.example.com
  - example.org/specific/path/
```

**Use cases:**
- Legitimate subdomain of blacklisted site
- Specific path on otherwise problematic domain
- Correcting overly broad blacklist rules

**Remember:** Whitelist wins in conflicts in the Go filter (see above).

## Rebuilding After Changes

The blacklist/whitelist is read at runtime by the Go tool, so editing the YAML needs no rebuild. Rebuild the binary only after changing
`tools/search_maps.go` (`cd tools && go build -o search_maps search_maps.go`). The checked-in `tools/search_maps` binary is a build artefact.

## Cache Behavior

The blacklist/whitelist configuration is read fresh on each search:
- ✅ Changes to `domain_blacklist.yaml` take effect immediately
- ✅ No need to clear API cache
- ✅ No need to restart any services
- ✅ Just run the search again with updated configuration

## Audit Trail

Only licence rejections leave a trace in the YAML: `# Blacklisted: <url> (license rejected)` beside the domain entry. Blocked URLs
are not logged anywhere by the Go tool.

## Custom Blacklist Path

Specify a different blacklist file:

```bash
./tools/search_maps -place "Omaha Beach" -openserp http://localhost:7001 -blacklist config/domain_blacklist.yaml
```

## Why Blacklist Instead of Whitelist?

**Whitelist problems:**
- Misses legitimate sources (university archives, regional museums, specialized history sites)
- Requires constant maintenance to add new sources
- Overly restrictive

**Blacklist benefits:**
- Allows discovery of new legitimate sources
- Grok verification still validates content quality
- Only blocks known problematic domains
- More flexible for research

## Verification Still Required

The blacklist is just the first filter. All results still go through:
1. **Go filter:** Blacklist + map keywords + place mention
2. **Python download:** Fetch actual page content
3. **Grok verification:** Analyze content for relevance and authenticity

This multi-layer approach prevents both spam and hallucinations.
