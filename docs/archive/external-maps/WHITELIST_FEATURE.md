# Whitelist Feature - Quick Reference

**Added:** 2026-02-26  
**Purpose:** Override blacklist for incorrectly filtered domains

## What It Does

Allows specific domains/paths that would otherwise be blocked by the blacklist.

## Precedence Rule

**Blacklist is decisive:** If a URL matches both whitelist and blacklist, it will be blocked.

```
Whitelist < Blacklist
```

## Configuration

Edit `domain_blacklist.yaml`:

```yaml
whitelist:
  - en.wikipedia.org/wiki/File:
  - specific-subdomain.example.com
```

## Use Cases

1. **Legitimate subdomain of blacklisted site**
   ```yaml
   blacklist:
     - wikipedia.org
   whitelist:
     - en.wikipedia.org/wiki/File:  # Allow only file pages
   ```

2. **Specific path on problematic domain**
   ```yaml
   blacklist:
     - example.com
   whitelist:
     - example.com/historical/maps/  # Allow only this path
   ```

3. **Correcting overly broad rules**
   ```yaml
   blacklist:
     - blogspot.com
   whitelist:
     - ww2history.blogspot.com  # Legitimate history blog
   ```

## How It Works

1. URL checked against whitelist first
2. If whitelisted → skip blacklist/source material checks
3. If not whitelisted → normal filtering applies
4. If both whitelisted AND blacklisted → blocked (blacklist wins)

## No Cache Clearing Needed

Changes take effect immediately:
- Configuration loaded fresh on each search
- No need to clear cache
- No need to restart services
- Just run search again

## Example

```yaml
blacklist:
  - pinterest.com
whitelist:
  - pinterest.com/historical

# Result: pinterest.com/historical still blocked (blacklist wins)
```

To allow it, remove from blacklist instead:
```yaml
blacklist:
  - pinterest.com/crafts  # Block specific path only
whitelist:
  - pinterest.com/historical  # Now this works
```
