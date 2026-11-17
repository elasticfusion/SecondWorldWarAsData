# Whitelist Configuration Update

**Date:** 2026-03-02  
**Change:** Whitelist now loaded from `domain_blacklist.yaml` instead of hardcoded

---

## What Changed

### Before
Whitelisted sites were hardcoded in `grok_search_maps.py`:
```python
WHITELISTED_SITES = [
    "dhc.westpoint.edu/atlases",
    "loc.gov",
    # ... hardcoded list
]
```

### After
Whitelisted sites loaded from `domain_blacklist.yaml`:
```yaml
whitelist:
- dhc.westpoint.edu/atlases/
- wikipedia.org
- grokipedia.com
- alaskool.org
- www.ww2alaska.com
- www.normandywarguide.com
```

---

## Benefits

✅ **Centralized configuration** - All domain filtering in one file  
✅ **Easy to modify** - Edit YAML, no code changes  
✅ **Consistent** - Same file used for blacklist and whitelist  
✅ **Version controlled** - Changes tracked in git  

---

## How to Add Sites

Edit `domain_blacklist.yaml`:

```yaml
whitelist:
- existing-site.com
- new-trusted-site.edu  # Add your site here
```

No code changes needed. The search will automatically use the updated list.

---

## Current Whitelist

As of 2026-03-02:
- `dhc.westpoint.edu/atlases/` - West Point Digital History Center
- `wikipedia.org` - Wikipedia
- `grokipedia.com` - Grokipedia
- `alaskool.org` - Alaska history
- `www.ww2alaska.com` - WWII Alaska
- `www.normandywarguide.com` - Normandy War Guide

---

## Implementation Details

### Loading Function
```python
def load_whitelisted_sites(blacklist_file: Path) -> List[str]:
    """Load whitelisted sites from domain_blacklist.yaml."""
    with open(blacklist_file) as f:
        data = yaml.safe_load(f)
    return data.get("whitelist", [])
```

### Usage
```python
whitelisted_sites = load_whitelisted_sites(Path("domain_blacklist.yaml"))
results = search_maps_with_grok(place, date, context, grok, whitelisted_sites)
```

### Fallback
If YAML file is missing or whitelist is empty, defaults to:
- `loc.gov`
- `archives.gov`
- `wikipedia.org`

---

## Testing

```bash
# Test with current whitelist
python3 -m src.extraction.combined_map_search --max-places 5

# Check which sites are being used
# Look for log line: "Loaded N whitelisted sites from domain_blacklist.yaml"
```

---

## Files Modified

- `src/extraction/grok_search_maps.py` - Load whitelist from YAML
- `src/extraction/combined_map_search.py` - Pass blacklist file path
- `docs/current/COMBINED_MAP_SEARCH.md` - Updated documentation
- `docs/current/GROK_SEARCH_MAPS.md` - Updated documentation

---

## Migration

No migration needed. Existing `domain_blacklist.yaml` already has whitelist section.

If you previously modified the hardcoded list in `grok_search_maps.py`, move those sites to `domain_blacklist.yaml` whitelist section.
