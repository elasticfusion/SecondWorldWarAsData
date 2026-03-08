# Domain Blacklist Configuration

The `domain_blacklist.yaml` file controls which domains and URL paths are excluded from external map search results.

## How It Works

When OpenSERP returns search results, the Go search tool (`search_maps`) filters out URLs before sending results to Python for verification.

**Approach:** Blacklist (reject known bad domains/paths) instead of whitelist (accept only known good domains)

**Automatic Logging:** When a URL is filtered out, it's automatically appended as a comment to `domain_blacklist.yaml` with the reason for filtering. This creates an audit trail of what was blocked and why.

Example comments added automatically:
```yaml
# Filtered: https://www.pinterest.com/pin/normandy-map/ (blacklisted domain: pinterest.com)
# Filtered: https://www.ibiblio.org/hyperwar/USA/USA-E-Breakout/maps/map1.jpg (source material: ibiblio.org/hyperwar/USA/USA-E-Breakout/)
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

**Important:** Blacklist always takes precedence. If a URL matches both whitelist and blacklist, it will be blocked.

**Why separate?** Allows blocking specific paths (e.g., our source documents) while keeping the rest of the domain available for other maps.

## Filter Logic

Results are included if:
1. ✅ In whitelist (bypasses other checks) OR
2. ✅ NOT in domain blacklist AND
3. ✅ NOT in source material paths AND
4. ✅ Contains "map" in title/description/URL AND
5. ✅ Mentions the place name

**Precedence:** Whitelist < Blacklist (blacklist is decisive in conflicts)

Results are excluded if:
- ❌ URL contains any blacklisted domain (even if whitelisted)
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

### Conflict Resolution (Blacklist Wins)
```yaml
blacklist:
  - pinterest.com
whitelist:
  - pinterest.com/historical
```
Result:
- ❌ `pinterest.com/historical/maps` - Blocked (blacklist is decisive)
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

**Remember:** Blacklist always wins in conflicts. If a URL matches both, it will be blocked.

## Rebuilding After Changes

After modifying the blacklist, rebuild the Go binary:

```bash
go build -o search_maps search_maps.go
```

**Note:** The blacklist/whitelist is loaded at runtime, so changes take effect immediately. No cache clearing needed.

## Cache Behavior

The blacklist/whitelist configuration is read fresh on each search:
- ✅ Changes to `domain_blacklist.yaml` take effect immediately
- ✅ No need to clear API cache
- ✅ No need to restart any services
- ✅ Just run the search again with updated configuration

## Audit Trail

The tool automatically appends comments to `domain_blacklist.yaml` when URLs are filtered:

```yaml
# Filtered: https://example.com/map (blacklisted domain: example.com)
```

This creates an audit trail showing:
- Which URLs were blocked
- Why they were blocked (domain blacklist vs source material)
- When they were encountered

You can periodically review these comments to:
- Verify filtering is working correctly
- Identify patterns in blocked content
- Decide if domains should be permanently blacklisted

## Custom Blacklist Path

Specify a different blacklist file:

```bash
./search_maps -place "Omaha Beach" -blacklist /path/to/custom_blacklist.yaml
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
