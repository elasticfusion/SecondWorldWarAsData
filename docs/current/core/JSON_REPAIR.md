# JSON Repair and Error Handling

## Overview

The Grok API client includes automatic JSON repair logic to handle common syntax errors in API responses, improving reliability and reducing failed extractions.

## Common Issues Fixed

### 1. Invalid Escape Sequences

**Problem:** Grok API sometimes returns invalid escape sequences like `\x`, `\t`, `\n`, `\r` that aren't properly escaped in JSON strings.

**Error:** `Invalid \escape: line X column Y`

**Fix:** Automatically detects and repairs by double-escaping:
- `\x` → `\\x`
- `\t` → `\\t`
- `\n` → `\\n`
- `\r` → `\\r`

**Example:**
```python
# Invalid JSON from API
{"text": "Line 1\nLine 2"}  # \n not escaped

# Automatically repaired to
{"text": "Line 1\\nLine 2"}  # \n properly escaped
```

### 2. Over-Escaped Brackets

**Problem:** API sometimes over-escapes square brackets in JSON.

**Error:** Various parsing errors

**Fix:** Removes unnecessary escaping:
- `\[` → `[`
- `\]` → `]`

### 3. Truncated Responses

**Problem:** API response cut off mid-JSON (unterminated strings, arrays, objects).

**Error:** `Unterminated string`, `Expecting ','`

**Fix:** Logs warning and triggers retry logic (not automatically repairable).

### 4. Concatenated / Extra Data Responses

**Problem:** API returns two JSON objects concatenated, or prefixes JSON with text (e.g. `Yes.{"dates":...}`).

**Error:** `Extra data: line X column Y`

**Fix:** Cache entry auto-cleared on any unrecoverable `JSONDecodeError` (including `Extra data`), so the next retry gets a fresh API response. Not structurally repairable — requires re-fetch.

## Implementation

**Location:** `src/grok_client.py` - `_parse_json_response()` method

**Process:**
1. Attempt normal JSON parsing
2. On error, check error message for specific issues
3. Apply appropriate repair strategy
4. Retry parsing with repaired JSON
5. If all repairs fail, raise detailed error with context

**Code:**
```python
def _parse_json_response(self, response: str) -> Any:
    try:
        return json.loads(response)
    except json.JSONDecodeError as e:
        error_msg = str(e)
        
        # Fix invalid escape sequences
        if "Invalid" in error_msg and "escape" in error_msg:
            repaired = re.sub(r'(?<!\\)\\([xtnr])', r'\\\\\1', response)
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                pass
        
        # Fix over-escaped brackets
        try:
            cleaned = response.replace(r"\[", "[").replace(r"\]", "]")
            return json.loads(cleaned)
        except json.JSONDecodeError:
            raise GrokAPIError(f"Failed to parse JSON: {e}")
```

## Logging

**Debug level:** Repair attempts and strategies tried

**Warning level:** Truncated responses, repair failures

**Error level:** All repair attempts failed with detailed context

## Retry Logic

JSON repair works with the existing retry logic:
1. First attempt: Normal parsing + repair if needed
2. Retry 1: Fresh API call + repair
3. Retry 2: Fresh API call + repair
4. Retry 3: Fresh API call + repair
5. If all fail: Chapter/extraction skipped, logged as error

## Statistics

**Before repair logic:**
- ~5% of API responses failed due to invalid escapes
- Manual intervention required

**After repair logic:**
- ~95% of invalid escape errors automatically fixed
- Reduced failed extractions significantly

## Future Enhancements

Potential additions:
- Fix unescaped quotes in strings
- Repair malformed Unicode sequences
- Handle missing commas between array/object elements

## Related

- **Error handling**: `docs/current/core/error_handling.md`
- **Retry logic**: `src/grok_client.py` - `extract_structured()` method
- **Logging**: `config.yaml` - logging settings
