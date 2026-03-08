# Blacklist Comment Logging - Quick Reference

**Feature:** Automatic audit trail of filtered URLs

## What It Does

When `search_maps` filters out a URL (due to blacklist or source material), it automatically appends a comment to `domain_blacklist.yaml`:

```yaml
# Filtered: <URL> (<reason>)
```

## Examples

```yaml
# Filtered: https://www.pinterest.com/pin/12345/ (blacklisted domain: pinterest.com)
# Filtered: https://www.youtube.com/watch?v=abc (blacklisted domain: youtube.com)
# Filtered: https://www.ibiblio.org/hyperwar/USA/USA-E-Breakout/maps/map1.jpg (source material: ibiblio.org/hyperwar/USA/USA-E-Breakout/)
```

## Benefits

1. **Audit Trail** - See what was blocked and why
2. **Pattern Detection** - Identify frequently blocked domains
3. **Verification** - Confirm filtering works as expected
4. **Decision Support** - Decide if temporary blocks should be permanent

## Maintenance

Periodically review and clean up comments:

```bash
# View recent filtered URLs
tail -50 domain_blacklist.yaml

# Remove old comments (keep structure)
# Edit domain_blacklist.yaml and delete comment lines starting with #
```

## Implementation

- **File:** `search_maps.go`
- **Function:** `appendBlacklistComment()`
- **Trigger:** Automatic when URL is filtered
- **Format:** YAML comment (starts with `#`)
- **Location:** Appended to end of file

## No Action Required

This feature works automatically. No configuration or manual intervention needed.
