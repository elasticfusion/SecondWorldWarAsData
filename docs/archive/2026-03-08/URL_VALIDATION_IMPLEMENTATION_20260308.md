# URL Validation for Supplemental Material

**Date:** 2026-03-08  
**Status:** Implemented

## Overview

Implemented URL validation for supplemental material to verify that resource URLs are accessible and working.

## Implementation

### 1. Validation Module (`src/extraction/validate_supplemental_urls.py`)

**Core Functions:**

```python
validate_url(url: str, timeout: float = 10.0) -> tuple[str, Optional[str]]
```
- Validates a single URL using HTTP GET request
- Returns status and optional error message
- Statuses: "validated", "broken", "timeout", "invalid"

```python
validate_material_urls(material: Dict[str, Any]) -> None
```
- Validates all URLs in a supplemental material entry
- Updates `url_validation_status` and `url_validation_date` fields
- Overall statuses: "validated", "partial", "broken", "timeout", "no_urls"

```python
validate_supplemental_file(file_path: Path, save: bool = True) -> Dict[str, int]
```
- Validates all URLs in a single endnotes/footnotes file
- Returns statistics dictionary
- Optionally saves results back to file

```python
validate_all_supplemental(output_root: Path, save: bool = True) -> None
```
- Validates URLs in all supplemental material files
- Prints summary statistics

### 2. CLI Script (`scripts/validate_supplemental_urls.py`)

Command-line interface for URL validation.

## Usage

### Validate All Files

```bash
# Validate all supplemental material files
python3 scripts/validate_supplemental_urls.py

# Dry run (don't save changes)
python3 scripts/validate_supplemental_urls.py --dry-run

# Verbose output
python3 scripts/validate_supplemental_urls.py -v
```

### Validate Single File

```bash
# Validate specific file
python3 scripts/validate_supplemental_urls.py --file output/BreakoutAndPursuit/chapter1a-endnotes.json

# Dry run on single file
python3 scripts/validate_supplemental_urls.py --file output/BreakoutAndPursuit/chapter1a-endnotes.json --dry-run
```

### Options

```
--output-dir PATH    Output directory (default: output)
--file PATH          Validate a single file instead of all files
--dry-run            Don't save changes, just report status
--timeout SECONDS    Request timeout in seconds (default: 10.0)
-v, --verbose        Verbose output
```

## Validation Statuses

### Per-URL Statuses
- **`validated`** - URL returns HTTP 200
- **`broken`** - URL returns non-200 status (404, 500, etc.)
- **`timeout`** - Request timed out
- **`invalid`** - Invalid URL or connection error

### Overall Material Statuses
- **`validated`** - All URLs validated successfully
- **`partial`** - Some URLs validated, some failed
- **`broken`** - All URLs broken or invalid
- **`timeout`** - All URLs timed out
- **`no_urls`** - No URLs to validate

## Output Format

After validation, each supplemental material entry includes:

```json
{
  "MaterialID": "01KK6J70TYPGHXYGVQ7GZMNZVV",
  "resource_urls": [
    "https://www.ibiblio.org/hyperwar/USA/USA-E-Breakout/fn1.html#fn1"
  ],
  "url_validation_status": "validated",
  "url_validation_date": "2026-03-08"
}
```

## Example Output

```
INFO: Validating URLs in chapter1a-endnotes.json
INFO: Validated 1 URL(s): validated
INFO: Validated 1 URL(s): validated
INFO: Validated 1 URL(s): validated
INFO: Saved validation results to chapter1a-endnotes.json

Validation Results:
  Validated: 6
  Partial: 0
  Broken: 0
  Timeout: 0
  No URLs: 0
```

## Integration with Pipeline

### Option 1: Manual Validation

Run validation script after extraction:

```bash
# Phase 2: Extract supplemental material
python3 phase2_extract.py

# Validate URLs
python3 scripts/validate_supplemental_urls.py
```

### Option 2: Automatic Validation (Future)

Add to Phase 2 extraction:

```python
# In phase2_extract.py, after supplemental extraction
if config.get("supplemental_material", {}).get("validate_urls", False):
    from src.extraction.validate_supplemental_urls import validate_supplemental_file
    validate_supplemental_file(supplemental_file)
```

### Option 3: Periodic Re-validation

Run as cron job to check for broken links:

```bash
# Check URLs weekly
0 0 * * 0 cd /path/to/project && python3 scripts/validate_supplemental_urls.py
```

## Error Handling

The validation is resilient to errors:

- **Network errors**: Marked as "invalid" with error message
- **Timeouts**: Marked as "timeout" (default: 10 seconds)
- **Invalid URLs**: Marked as "invalid"
- **File errors**: Logged but don't stop processing

All errors are logged for debugging.

## Performance

- **Timeout**: 10 seconds per URL (configurable)
- **Parallel processing**: Not implemented (sequential for now)
- **Rate limiting**: None (be respectful of servers)

For large datasets, consider:
- Increasing timeout for slow servers
- Adding delays between requests
- Implementing parallel validation

## Testing

```bash
# Test on single file (dry run)
python3 scripts/validate_supplemental_urls.py \
  --file output/BreakoutAndPursuit/chapter1a-endnotes.json \
  --dry-run

# Test with verbose output
python3 scripts/validate_supplemental_urls.py \
  --file output/BreakoutAndPursuit/chapter1a-endnotes.json \
  --dry-run -v

# Validate and save
python3 scripts/validate_supplemental_urls.py \
  --file output/BreakoutAndPursuit/chapter1a-endnotes.json
```

## Dependencies

- **httpx** - HTTP client library (already in requirements.txt)

No additional dependencies required.

## Future Enhancements

1. **Parallel Validation**: Use asyncio for faster processing
2. **Content Verification**: Check if page content matches expected material
3. **Archive.org Fallback**: Check Wayback Machine for broken links
4. **Rate Limiting**: Respect server rate limits
5. **Retry Logic**: Retry failed requests with exponential backoff
6. **Email Notifications**: Alert on broken links
7. **Dashboard**: Web UI showing validation status

## Related Files

- `src/extraction/validate_supplemental_urls.py` - Validation module
- `scripts/validate_supplemental_urls.py` - CLI script
- `src/json_schemas.py` - Schema with validation fields
- `output/{BookName}/*-endnotes.json` - Files to validate
- `output/{BookName}/*-footnotes.json` - Files to validate

## Example: Checking Validation Status

```bash
# Find all validated materials
find output -name "*-endnotes.json" -o -name "*-footnotes.json" | \
  xargs jq -r '.[] | .Supplemental_Material[] | 
    select(.url_validation_status == "validated") | 
    .MaterialID' | wc -l

# Find broken URLs
find output -name "*-endnotes.json" -o -name "*-footnotes.json" | \
  xargs jq -r '.[] | .Supplemental_Material[] | 
    select(.url_validation_status == "broken") | 
    {MaterialID, resource_urls, url_validation_date}'

# Find materials never validated
find output -name "*-endnotes.json" -o -name "*-footnotes.json" | \
  xargs jq -r '.[] | .Supplemental_Material[] | 
    select(.url_validation_status == null) | 
    .MaterialID' | wc -l
```
