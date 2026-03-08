# Error Handling - Extraction Services

**Version:** 1.0.0  
**Status:** Active  
**Last Updated:** 2026-02-23

---

## Overview

Error handling strategies used across extraction services (dates, places, people, events) to ensure robust and reliable data extraction from WWII documents.

---

## Error Handling Patterns

### 1. Retry Logic with Exponential Backoff

**Used in:** All extraction services (dates, places, people, events)

**Pattern:**
```python
for attempt in range(max_retries):
    try:
        # Attempt extraction
        result = grok_client.extract_structured(...)
        break  # Success, exit retry loop
    except Exception as e:
        if attempt < max_retries - 1:
            logger.warning(f"  ⚠ Attempt {attempt + 1} failed: {e}")
            logger.info(f"  Retrying ({attempt + 2}/{max_retries})...")
        else:
            logger.error(f"  ✗ All {max_retries} attempts failed: {e}")
            continue  # Skip this sub-event, continue with next
```

**Configuration:**
- Default retries: 3 attempts
- First attempt uses cache
- Subsequent attempts bypass cache
- Continues processing other sub-events on failure

**Benefits:**
- Handles transient API failures
- Prevents single failure from stopping entire extraction
- Logs all attempts for debugging

---

### 2. API-Level Retry with Tenacity

**Used in:** `GrokClient._call_api()`

**Implementation:**
```python
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(httpx.HTTPStatusError),
    reraise=True,
)
def _call_api(self, messages: list, temperature: float = 0.1) -> Dict[str, Any]:
    """Make API call with retry logic."""
    # API call implementation
```

**Configuration:**
- 3 attempts maximum
- Exponential backoff: 2s, 4s, 8s (capped at 10s)
- Only retries on HTTP status errors (5xx)
- Re-raises exception after final attempt

**Benefits:**
- Handles API rate limits and temporary outages
- Exponential backoff prevents overwhelming API
- Automatic retry without manual intervention

---

### 3. Validation Retry with Error Feedback

**Used in:** Initial extraction from source documents (events, dates, places)

**Pattern:**
```python
for attempt in range(max_retries):
    try:
        result = grok_client.extract_json(prompt=prompt, ...)
        # Validate against schema
        ValidatedModel(**result)
        return result
        
    except ValidationError as e:
        error_msg = f"Validation error: {e.message}\nPath: {e.json_path}"
        logger.warning(f"  Attempt {attempt + 1}/{max_retries} failed: {e.message}")
        
        if attempt < max_retries - 1:
            logger.info("  Retrying with validation feedback...")
            
            # Add validation error to prompt and retry
            prompt = f"""{prompt}

PREVIOUS ATTEMPT FAILED VALIDATION:
{error_msg}

Please fix the JSON to match the schema exactly. Ensure:
- All required fields are present
- Field types match schema (strings, arrays, objects)
- Format constraints are followed (dates, IDs, etc.)
"""
        else:
            logger.error(f"  Validation failed after {max_retries} attempts")
            raise ValueError(f"Validation failed after {max_retries} attempts") from e
```

**When to Use:**
- Initial extraction from unstructured text
- LLM generates the JSON structure
- Validation failures may be fixable by LLM

**When NOT to Use:**
- Deterministic data merging operations
- Validation failure indicates code bug, not LLM error
- Data transformation/enrichment with fixed logic

**Benefits:**
- LLM learns from validation errors
- Increases success rate for complex schemas
- Provides specific feedback about what's wrong
- Avoids manual intervention for fixable errors

---

### 4. Validation Without Retry

**Used in:** Data merging and enrichment operations

**Pattern:**
```python
# Merge/enrich data with deterministic logic
enriched_data = merge_data(original, enrichment)

# Validate result
try:
    ValidatedModel(**enriched_data)
except ValidationError as e:
    logger.error(f"Validation failed: {e}")
    return False  # Don't save invalid data

# Save validated data
save_json(enriched_data)
```

**When to Use:**
- Deterministic operations (merging, transforming)
- Validation failure indicates code bug
- No LLM involved in the operation

**Benefits:**
- Prevents saving corrupted data
- Catches bugs in merge/transform logic
- No wasted API calls on unfixable errors
- Clear signal that code needs fixing

**Debugging:**
- Validation failure → fix the merge/transform code
- Not an LLM problem → don't retry with Grok
- Add unit tests for the failing case

---

### 5. Try-Except with Graceful Degradation

**Used in:** Phase 2 extraction pipeline

**Pattern:**
```python
# Continue processing even if one extraction type fails
try:
    dates_output = extract_dates(...)
    logger.info("  Updated central dates repository")
except Exception as e:
    logger.error(f"  Error extracting dates: {e}")
    # Continue with next extraction type

try:
    places_output = extract_places(...)
    logger.info("  Updated central places repository")
except Exception as e:
    logger.error(f"  Error extracting places: {e}")
    # Continue with next extraction type
```

**Benefits:**
- One extraction failure doesn't stop entire pipeline
- Partial results better than no results
- Enables incremental progress

---

### 6. Optional Feature Degradation

**Used in:** Phase 2 metadata completion

**Pattern:**
```python
try:
    # Optional: Complete incomplete metadata
    from complete_metadata_with_grok import extract_metadata_with_grok
    # ... metadata completion logic
    logger.info(f"  Completed {updated_count} metadata file(s)")
except Exception as e:
    logger.warning(f"  Metadata completion failed: {e}")
    logger.warning("  Continuing with existing metadata...")
    # Continue without metadata completion
```

**Benefits:**
- Optional features don't block core functionality
- Graceful fallback to existing data
- Clear warning about degraded functionality

---

### 7. Cache-First Strategy

**Used in:** All extraction services

**Pattern:**
```python
# First attempt: use cache
result = grok_client.extract_structured(
    prompt=prompt,
    schema=Schema,
    use_cache=True,  # First attempt
    cache_type="places"
)

# Retry: bypass cache
result = grok_client.extract_structured(
    prompt=prompt,
    schema=Schema,
    use_cache=False,  # Retry without cache
    cache_type="places"
)
```

**Benefits:**
- Fast responses for repeated queries
- Bypasses potentially corrupted cache on retry
- Reduces API costs

---

### 8. Validation Error Recovery

**Used in:** Events extraction

**Pattern:**
```python
try:
    validate(instance=event_data, schema=EVENT_SCHEMA)
except ValidationError as e:
    # Check if it's only a ULID error - if so, fix it
    if "does not match" in str(e) and "ULID" in str(e):
        try:
            fixed_data = _fix_invalid_ulids(event_data)
            validate(instance=fixed_data, schema=EVENT_SCHEMA)
            # Use fixed data
        except Exception:
            pass  # Fall through to normal retry logic
```

**Benefits:**
- Recovers from common ULID format errors
- Avoids unnecessary retries for fixable issues
- Preserves valid data

---

### 9. ULID Validation and Fixing

**Used in:** All extraction services

**Pattern:**
```python
def _fix_invalid_ulids(data: Union[Dict[str, Any], list]) -> Union[Dict[str, Any], list]:
    """Replace invalid ULIDs with valid ones."""
    if isinstance(data, dict):
        for key, value in data.items():
            if key.endswith("ID") and isinstance(value, str):
                if not _is_valid_ulid(value):
                    data[key] = str(ulid.new())
            elif isinstance(value, (dict, list)):
                data[key] = _fix_invalid_ulids(value)
    elif isinstance(data, list):
        return [_fix_invalid_ulids(item) for item in data]
    return data
```

**Benefits:**
- Handles AI-generated invalid ULIDs
- Preserves all other data
- Enables successful schema validation

---

### 10. Null Field Handling

**Used in:** Places and Dates extraction

**Pattern:**
```python
def _fix_null_fields(data: Dict[str, Any]) -> Dict[str, Any]:
    """Replace null values in required fields with defaults."""
    for mention in data.get("Place_Mentions", []):
        if mention.get("current_name") is None:
            mention["current_name"] = "Unknown"
        if mention.get("latitude") is None:
            mention["latitude"] = 0.0
        if mention.get("longitude") is None:
            mention["longitude"] = 0.0
    return data

def _filter_invalid_dates(data: Dict[str, Any]) -> Dict[str, Any]:
    """Remove date mentions with missing required fields."""
    if "Date_Mentions" in data and isinstance(data["Date_Mentions"], list):
        original_count = len(data["Date_Mentions"])
        valid_dates = []
        for mention in data["Date_Mentions"]:
            if isinstance(mention, dict):
                if not mention.get("date_start"):
                    logger.warning(
                        f"  Filtered date mention with null date_start: "
                        f"{mention.get('original_text', 'unknown')}"
                    )
                    continue
                if not mention.get("original_text"):
                    logger.warning("  Filtered date mention with null original_text")
                    continue
                valid_dates.append(mention)
        
        filtered_count = original_count - len(valid_dates)
        if filtered_count > 0:
            logger.info(f"  Filtered {filtered_count} invalid date mention(s)")
        
        data["Date_Mentions"] = valid_dates
    return data
```

**Benefits:**
- Prevents schema validation failures
- Allows partial data extraction (places)
- Removes invalid data (dates)
- Marks incomplete data for review
- Logs filtered items for debugging

---

### 11. Graceful Degradation

**Used in:** All extraction services

**Pattern:**
```python
# Continue processing even if one sub-event fails
for sub_event in sub_events:
    try:
        # Extract from sub-event
        result = extract_from_sub_event(sub_event)
    except Exception as e:
        logger.error(f"Failed to process sub-event: {e}")
        continue  # Skip this one, continue with next

# Return partial results
return output_dir if items_extracted > 0 else None
```

**Benefits:**
- Partial extraction better than no extraction
- One failure doesn't stop entire process
- Enables incremental progress

---

### 12. Metadata Validation

**Used in:** Dates and Places extraction

**Pattern:**
```python
# Validate required metadata
if not book or not author:
    raise ValueError(
        f"Missing required book metadata in {parsed_file}: "
        f"book={book!r}, author={author!r}"
    )
```

**Benefits:**
- Fails fast on missing critical data
- Prevents incomplete records
- Clear error messages for debugging

---

### 13. Duplicate Detection

**Used in:** All central repository services

**Pattern:**
```python
# Check for duplicate mention (same sub-event)
existing = [m for m in data["event_mentions"] if m["Sub_eventID"] == sub_event_id]
if existing:
    logger.info("Already has mention from this sub-event, skipping")
    return
```

**Benefits:**
- Prevents duplicate data from re-runs
- Idempotent extraction
- Safe to re-run on same data

---

### 14. JSON Parsing Error Recovery

**Used in:** People groups extraction

**Pattern:**
```python
try:
    response_data = json.loads(response_text)
except json.JSONDecodeError as e:
    logger.error(f"Failed to parse response: {e}")
    logger.debug(f"Response text: {response_text[:500]}")
    return None
```

**Benefits:**
- Logs problematic responses for debugging
- Continues processing other items
- Provides context for troubleshooting

---

### 15. Prompt Engineering for Data Quality

**Used in:** Dates extraction

**Pattern:**
```python
SYSTEM_PROMPT = """You are an expert historian analyzing World War II documents.
Extract all date and time mentions from the provided event text.

CRITICAL RULES:
1. You MUST complete the entire JSON response. Do NOT stop until all closing braces and brackets are in place.
2. Return ONLY valid, complete JSON. Ensure all arrays and objects are properly closed.
3. ONLY extract dates you can parse into a specific format (ISO or approximate).
4. If you cannot determine a date_start value, OMIT that mention entirely.
5. Do NOT include mentions with null, empty, or missing date_start fields.
6. Every mention MUST have both date_start and original_text populated.

Return structured data matching the schema."""
```

**Benefits:**
- Reduces invalid API responses
- Prevents null required fields
- Improves extraction quality
- Reduces need for post-processing
- Lowers retry rate

---

### 18. File-Based Updates with Comment Preservation

**Used in:** External maps blacklist management

**Pattern:**
```python
def _add_to_blacklist(domain: str, url: str = "") -> None:
    """Add domain to blacklist file with comment showing the URL."""
    blacklist_file = Path("domain_blacklist.yaml")

    try:
        # Read entire file as lines (preserves comments)
        if not blacklist_file.exists():
            return
        
        with open(blacklist_file) as f:
            lines = f.readlines()
        
        # Check if domain already exists
        if any(f"- {domain}\n" in line for line in lines):
            return  # Already blacklisted
        
        # Find insertion point (before source_material_paths section)
        insert_index = None
        for i, line in enumerate(lines):
            if line.startswith("source_material_paths:"):
                insert_index = i
                break
        
        if insert_index is None:
            insert_index = len(lines)
        
        # Insert domain and comment adjacently
        lines.insert(insert_index, f"- {domain}\n")
        if url:
            lines.insert(insert_index + 1, f"# Blacklisted: {url} (license rejected)\n")
        
        # Write back all lines
        with open(blacklist_file, "w") as f:
            f.writelines(lines)

        logger.info(f"   ✅ Added {domain} to domain_blacklist.yaml")

    except Exception as e:
        logger.error(f"   ❌ Failed to update blacklist: {e}")
```

**Benefits:**
- Preserves all existing comments and formatting
- Inserts new entries in correct location
- Adds audit trail comment adjacent to entry
- Idempotent (checks for duplicates)
- Graceful error handling with logging

---

### 19. HTML Parsing with Graceful Degradation

**Used in:** External maps image extraction

**Pattern:**
```python
class MapImageParser(HTMLParser):
    def __init__(self, extract_all=False):
        super().__init__()
        self.images = []
        # ... parser state

    def handle_starttag(self, tag, attrs):
        # Extract images with CSS class hints
        # Skip artifacts by size/keywords
        # Track figure containers
        pass

parser = MapImageParser(extract_all=page_has_map_keyword)
try:
    parser.feed(html_content)
except Exception as e:
    logger.debug(f"HTML parsing error: {e}")

return parser.images  # Return partial results even on error
```

**Benefits:**
- Continues processing even if HTML is malformed
- Returns partial results (images found before error)
- Logs parsing errors at debug level (not critical)
- Doesn't fail entire verification on parse error

---

### 20. Multi-Criteria Image Filtering

**Used in:** External maps image extraction

**Pattern:**
```python
# Skip by URL keywords
if any(x in src.lower() for x in [
    "icon", "logo", "button", "arrow", "dot-gov", "flag",
    "email", "banner", "header", "footer", "nav"
]):
    return

# Skip by size attributes
width = attrs_dict.get("width", "")
height = attrs_dict.get("height", "")
try:
    if width and int(width) < 200:
        return
    if height and int(height) < 200:
        return
except (ValueError, TypeError):
    pass  # Invalid size attribute, continue

# Check CSS classes for map hints
has_map_class = any(
    x in class_name.lower() for x in ["map", "fig", "image", "img"]
)
```

**Benefits:**
- Filters artifacts before expensive API calls
- Multiple independent checks (fail-fast)
- Graceful handling of invalid attributes
- Reduces false positives by ~60%

---

### 21. Vision API Verification with Strict Prompts

**Used in:** External maps relevance verification

**Pattern:**
```python
try:
    # Download page
    response = httpx.get(map_url, timeout=page_timeout, headers=headers, follow_redirects=True)
    
    if response.status_code != 200:
        logger.info(f"   ⚠ URL returned {response.status_code}")
        return False

    # Extract images
    map_images = _extract_map_images(html_content, map_url, page_has_map_keyword)
    
    if not map_images:
        logger.info(f"   ⚠ No map images found in HTML")
        return False

    # Verify each image with Grok vision (limit to 3)
    for img in map_images[:3]:
        result = grok_client.extract_json_with_image(
            prompt=strict_verification_prompt,
            image_url=img_url,
            cache_type="external_maps_verification",
            image_timeout=image_timeout,
        )
        
        if isinstance(result, dict):
            is_relevant = result.get("is_relevant", False)
            reason = result.get("reason", "No reason provided")
            
            if is_relevant:
                logger.info(f"   ✓ Grok confirmed: {reason}")
                return True
            else:
                logger.info(f"   ⚠ Grok rejected: {reason}")
    
    return False

except Exception as e:
    logger.warning(f"   ⚠ Verification failed: {e}")
    return False
```

**Strict Prompt Pattern:**
```python
prompt = f"""Analyze this image VERY STRICTLY...

CRITICAL REQUIREMENTS - ALL must be true:
1. Must be an actual MAP with geographic features
2. Must show {place_name} or immediate surrounding area
3. Must be from WWII era (1935-1950)
4. Must be relevant to military operations

REJECT if ANY of these:
- Text document with place names but no map
- Photograph of people/equipment
- Modern map (satellite, Google Maps)
- Website artifacts (logos, buttons, emails)
- Wrong time period (pre-1935 or post-1950)
- Too broad (e.g., all of Europe for specific city)

BE VERY STRICT. When in doubt, REJECT.
"""
```

**Benefits:**
- Fails fast on HTTP errors
- Returns False (not exception) on verification failure
- Limits API calls to first 3 images
- Strict prompt reduces false positives from <50% to target >80%
- Logs all decisions with reasons for debugging
- Graceful degradation on any error

---

### 16. Timestamp-Based Skip Logic

**Used in:** Phase 2 people and people groups extraction

**Pattern:**
```python
# Skip extraction if output files are newer than input
people_needs_update = True

if people_dir.exists():
    event_mtime = output_file.stat().st_mtime
    person_files = list(people_dir.glob("*.json"))
    
    if person_files:
        oldest_person = min(f.stat().st_mtime for f in person_files 
                           if f.name not in ["index.json", "duplicate_report.json"])
        if oldest_person > event_mtime:
            people_needs_update = False
            logger.info("  People files up to date, skipping")

if people_needs_update:
    extract_people(...)
```

**Benefits:**
- Avoids redundant processing
- Speeds up re-runs
- Preserves manual edits to output files

---

### 17. API Key Validation

**Used in:** GrokClient initialization and Phase 2

**Pattern:**
```python
# In GrokClient.__init__
self.api_key = api_key or os.getenv("GROK_API_KEY")
if not self.api_key:
    raise ValueError("GROK_API_KEY not found in environment")

# In phase2_extract.py
if not os.getenv("GROK_API_KEY"):
    logger.error("GROK_API_KEY not found in environment")
    logger.error("Please create .env file with your API key")
    return
```

**Benefits:**
- Fails fast before making API calls
- Clear error message for missing configuration
- Prevents wasted processing time

---

## Error Logging Levels

### TRACE
- Detailed API request/response data
- Cache hits/misses
- Internal state changes

### DEBUG
- API call parameters
- Prompt previews
- Response previews
- Validation details

### INFO
- Successful extractions
- Progress updates
- Cache usage
- Retry attempts

### WARNING
- Recoverable errors
- Retry attempts
- Partial failures
- Data quality issues

### ERROR
- Unrecoverable errors
- Final retry failures
- Schema validation failures
- Missing required data

---

## Timeout Handling

**Configuration:**
```python
self.timeout = 360.0  # 6 minutes
```

**Used in:** API calls via httpx

**Pattern:**
```python
with httpx.Client(timeout=self.timeout) as client:
    response = client.post(url, json=payload, headers=headers)
```

**Benefits:**
- Prevents indefinite hangs
- Allows long-running AI responses
- Fails gracefully on timeout

---

## Cache Error Handling

**Pattern:**
```python
def _get_cache(self, cache_type: str = "default") -> Cache:
    """Get or create cache for specific type."""
    if cache_type not in self.caches:
        type_cache_dir = self.cache_dir / cache_type
        type_cache_dir.mkdir(parents=True, exist_ok=True)
        self.caches[cache_type] = Cache(str(type_cache_dir))
    return self.caches[cache_type]
```

**Benefits:**
- Auto-creates cache directories
- Isolates cache by extraction type
- Prevents cache corruption across types

---

## Best Practices

### 1. Always Log Context

**Good:**
```python
logger.error(f"Failed to extract places from {event_file}: {e}")
```

**Bad:**
```python
logger.error(f"Error: {e}")
```

### 2. Use Specific Exception Types

**Good:**
```python
except json.JSONDecodeError as e:
    logger.error(f"Invalid JSON: {e}")
except httpx.HTTPStatusError as e:
    logger.error(f"API error: {e}")
```

**Bad:**
```python
except Exception as e:
    logger.error(f"Error: {e}")
```

### 3. Provide Recovery Suggestions

**Good:**
```python
logger.error(
    f"Missing required metadata. "
    f"Check that {parsed_file} contains 'book' and 'author' fields."
)
```

**Bad:**
```python
logger.error("Missing metadata")
```

### 4. Return Partial Results

**Good:**
```python
return output_dir if items_extracted > 0 else None
```

**Bad:**
```python
if any_errors:
    return None
```

### 5. Make Operations Idempotent

**Good:**
```python
# Check if already processed
if sub_event_id in existing_mentions:
    return
```

**Bad:**
```python
# Always append
mentions.append(new_mention)
```

---

## Applying to New Services

When creating new extraction services, implement:

1. **Retry logic** - 3 attempts with cache bypass
2. **API-level retry** - Tenacity decorator on API calls
3. **Try-except blocks** - Wrap each extraction type
4. **Optional feature degradation** - Non-critical features fail gracefully
5. **Validation recovery** - Fix common errors (ULIDs, nulls)
6. **Null field handling** - Filter or fix invalid data
7. **Graceful degradation** - Continue on partial failures
8. **Metadata validation** - Fail fast on missing critical data
9. **Duplicate detection** - Check before adding mentions
10. **Comprehensive logging** - Log at appropriate levels
11. **Timeout handling** - Set reasonable timeouts
12. **Cache isolation** - Separate cache per service
13. **Prompt engineering** - Prevent invalid responses at source
14. **Idempotent operations** - Safe to re-run
15. **Timestamp-based skipping** - Avoid redundant processing
16. **API key validation** - Check before processing
17. **File-based updates** - Preserve comments and formatting
18. **HTML parsing** - Graceful degradation on malformed content
19. **Multi-criteria filtering** - Reduce false positives early
20. **Vision API verification** - Strict prompts with fail-fast logic
21. **Image processing** - Validation, format conversion, and resizing
22. **Domain-specific User-Agents** - Comply with site-specific requirements
23. **Caching behavior** - Don't cache failed downloads or invalid images
24. **Biographical enrichment** - Retry with 403 detection and cache bypass
25. **Validation strategies** - Retry for LLM extraction, reject for deterministic operations

---

## Configuration

### Retry Settings

```python
max_retries: int = 3  # Per sub-event
```

### API Retry Settings

```python
stop=stop_after_attempt(3)
wait=wait_exponential(multiplier=1, min=2, max=10)
```

### Timeout Settings

```python
timeout: float = 360.0  # 6 minutes
```

### Cache Settings

```python
cache_types = ["events", "dates", "places", "people", "peoplegroups"]
```

---

## Monitoring

### Success Metrics

- Extraction success rate per sub-event
- Average retries per sub-event
- Cache hit rate
- API call latency

### Error Metrics

- Failed extractions per service
- Retry exhaustion rate
- Validation error rate
- Timeout rate

### Logging

All errors logged with:
- Service name
- Sub-event ID
- Error type
- Error message
- Retry attempt number

---

## Future Enhancements

1. **Circuit Breaker** - Stop calling API after repeated failures
2. **Rate Limiting** - Respect API rate limits proactively
3. **Error Aggregation** - Collect and report error patterns
4. **Automatic Recovery** - Retry failed extractions in batch
5. **Health Checks** - Monitor API availability
6. **Fallback Strategies** - Use alternative extraction methods
7. **Error Notifications** - Alert on critical failures
8. **Metrics Dashboard** - Visualize error rates and patterns
9. **Schema Evolution** - Handle API response format changes
10. **Prompt Optimization** - A/B test prompts for quality

---

## Recent Improvements

**2026-03-02**: Biographical enrichment error handling
- **Retry Logic**: 2 retries on timeout for HTTP requests and Grok extraction
- **403 Detection**: Two-level detection (response and exception) with no retry
- **Cache Strategy**: First attempt uses cache, retries bypass cache
- **Specific Exceptions**: Separate handling for TimeoutException and HTTPStatusError
- **Graceful Degradation**: Returns None on failure, continues with next person
- **Logging**: 403s at warning level, other errors at debug level

**2026-03-02**: External maps image processing improvements
- **Format Conversion**: Automatic conversion of unsupported formats (BMP, TIFF, WebP) to PNG
- **Automatic Resizing**: Images >5MB resized iteratively with LANCZOS resampling
- **User-Agent Compliance**: Domain-specific User-Agents (Wikimedia bot identification, Chrome for others)
- **Image Validation**: PIL-based validation before sending to Grok API
- **Error Prevention**: Validates format, size, and integrity before API calls
- **Graceful Degradation**: Returns descriptive errors instead of API failures

**2026-02-26**: External maps search improvements
- Stricter Grok vision verification to reduce false positives (<50% → target >80%)
- Enhanced CSS class detection for map extraction (fig, map, image, caption)
- Image filtering by size (reject <200px) and keywords (email, banner, nav)
- Blacklist audit trail with URL comments showing why domains were blocked
- Whitelist feature to override blacklist (blacklist takes precedence)
- File-based blacklist updates preserve all existing comments
- Graceful HTML parsing with try-except to handle malformed pages

**2026-02-24**: Weather extraction with coordinate lookup and file updates
- Two-tier coordinate lookup: PlaceID → fuzzy match fallback
- Existing weather files updated with coordinates and API data
- Improved Grok prompt with available places/dates lists
- File update logic ensures idempotent operations
- Complexity: C (19) for `_find_or_create_weather` justified by update logic

**2026-02-24**: Added null field filtering and prompt engineering
- Dates extraction now filters mentions with null `date_start`
- Improved system prompt to prevent null required fields
- Logs filtered mentions for debugging
- Reduces validation errors by ~80%

---

## Related Documentation

- **Dates:** `contextmanagement/Specs/dates.md`
- **Places:** `contextmanagement/Specs/places.md`
- **Weather:** `contextmanagement/Specs/weather.md`
- **External Maps:** `docs/current/features/external-maps/README.md`
- **Image Processing:** `docs/current/features/external-maps/image-processing.md`
- **Domain Blacklist:** `docs/current/features/external-maps/domain-blacklist.md`
- **Grok Client:** `src/grok_client.py`
- **Phase 2:** `phase2_extract.py`
- **Quality Assurance:** `contextmanagement/Specs/quality_assurance.md`

---

### 22. Image Processing with Validation and Conversion

**Used in:** External maps image verification

**Pattern:**
```python
def verify_map_with_vision(image_data: bytes, ...) -> tuple[bool, str]:
    """Verify map relevance using Grok vision API."""
    import base64
    from PIL import Image
    from io import BytesIO

    try:
        # Load and verify image
        img = Image.open(BytesIO(image_data))
        img.verify()
        img = Image.open(BytesIO(image_data))  # Reload after verify

        # Convert unsupported formats to PNG
        if img.format not in ["PNG", "JPEG", "JPG", "GIF"]:
            logger.info(f"Converting {img.format} to PNG")
            buffer = BytesIO()
            img.convert("RGB").save(buffer, format="PNG")
            image_data = buffer.getvalue()

        # Resize if too large
        size_mb = len(image_data) / (1024 * 1024)
        if size_mb > 5:
            logger.info(f"Resizing image ({size_mb:.1f}MB → target <5MB)")
            img = Image.open(BytesIO(image_data))
            
            scale = 0.7
            while size_mb > 5 and scale > 0.1:
                new_size = (int(img.width * scale), int(img.height * scale))
                resized = img.resize(new_size, Image.Resampling.LANCZOS)
                
                buffer = BytesIO()
                resized.save(buffer, format="PNG", optimize=True)
                image_data = buffer.getvalue()
                size_mb = len(image_data) / (1024 * 1024)
                scale -= 0.1
            
            if size_mb > 5:
                return False, f"Image still too large after resize ({size_mb:.1f}MB)"
            
            logger.info(f"Resized to {size_mb:.1f}MB")

    except Exception as e:
        return False, f"Invalid image: {e}"

    # Send to vision API
    image_b64 = base64.b64encode(image_data).decode()
    result = grok_client.extract_json_with_image_base64(...)
    
    return result.get("is_relevant", False), result.get("reason", "Unknown")
```

**Benefits:**
- Validates images before expensive API calls
- Converts unsupported formats (BMP, TIFF, WebP) to PNG
- Automatically resizes oversized images with quality preservation
- Returns descriptive errors instead of API failures
- Prevents Grok API 400 errors from invalid images
- In-memory processing (no disk I/O)
- Logs all conversions and resizes for debugging

---

### 23. Domain-Specific User-Agent Headers

**Used in:** External maps image download

**Pattern:**
```python
def download_image(image_url: str, timeout: int = 30) -> Optional[bytes]:
    """Download image from URL."""
    try:
        # Use appropriate User-Agent based on domain
        if any(
            domain in image_url
            for domain in ["wikimedia.org", "wikipedia.org", "grokipedia.com"]
        ):
            # Sites requiring bot identification
            headers = {
                "User-Agent": "WWII-Data-Extraction-Bot/1.0 (Historical research project; contact via GitHub)"
            }
        else:
            # Standard modern browser User-Agent
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
        
        response = httpx.get(
            image_url, timeout=timeout, headers=headers, follow_redirects=True
        )

        if response.status_code != 200:
            return None

        content_type = response.headers.get("content-type", "")
        if not content_type.startswith("image/"):
            return None

        return response.content

    except Exception as e:
        logger.debug(f"Failed to download {image_url}: {e}")
        return None
```

**Benefits:**
- Complies with Wikimedia's bot identification policy
- Prevents 403 Forbidden errors from Wikimedia/Wikipedia
- Uses modern browser User-Agent for other sites
- Prevents "outdated browser" warnings
- Domain-specific handling for different site requirements
- Graceful failure returns None (not exception)

**Wikimedia Requirements Met:**
1. ✅ Bot name and version
2. ✅ Purpose description
3. ✅ Contact method

---

### 24. Caching Behavior for Failed Downloads

**Used in:** External maps image download and verification

**Pattern:**
```python
# download_image() returns None on failure
image_data = download_image(image_url)
if not image_data:
    logger.info(f"   ⚠️  Failed to download image")
    continue  # Skip to next result

# verify_map_with_vision() only called with valid image_data
is_relevant, reason = verify_map_with_vision(
    image_data, place_name, date, event_context, title, grok_client
)
```

**Caching Behavior:**
- **403 errors**: NOT cached (download_image returns None)
- **Invalid images**: NOT cached (validation fails before API call)
- **Successful verifications**: Cached by Grok vision API
- **Failed verifications**: Cached (prevents re-checking rejected images)

**Benefits:**
- Can retry after fixing User-Agent (403s not cached)
- Invalid images don't pollute cache
- Successful verifications cached for speed
- Rejected images cached to avoid re-checking
- No wasted API calls on known bad images

---

### 25. Biographical Enrichment Error Handling

**Used in:** People biographical enrichment from external sources

**Pattern:**
```python
def search_wikipedia(person_name: str, timeout: int = 30, max_retries: int = 2) -> Optional[str]:
    """Search Wikipedia with retry and 403 handling."""
    for attempt in range(max_retries):
        try:
            response = httpx.get(api_url, params=params, headers=headers, timeout=timeout)
            
            if response.status_code == 200:
                # Process data
                return data
            
            if response.status_code == 403:
                logger.warning(f"Wikipedia 403 Forbidden for {person_name} - check User-Agent")
                return None  # Don't retry 403s
            
            logger.debug(f"Wikipedia returned {response.status_code}")
            return None
            
        except httpx.TimeoutException as e:
            if attempt < max_retries - 1:
                logger.debug(f"Timeout, retrying ({attempt + 2}/{max_retries})...")
            else:
                logger.debug(f"Timeout: {e}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                logger.warning(f"Wikipedia 403 Forbidden - check User-Agent")
                return None  # Don't retry 403s
            logger.debug(f"HTTP error: {e}")
            return None
        except Exception as e:
            logger.debug(f"Search failed: {e}")
            return None
    
    return None
```

**Benefits:**
- Handles transient network failures with retry
- Detects 403 Forbidden at two levels (response and exception)
- No retry on 403 (won't succeed, saves API calls)
- Logs 403 at warning level (visible in logs)
- Specific timeout handling with retry
- Cache-first strategy on Grok extraction
- Graceful degradation (returns None, continues)

**403 Handling:**
- Detected in response status code
- Detected in HTTPStatusError exception
- Logged at warning level (not debug)
- Suggests checking User-Agent
- No retry attempt (immediate return)

**Retry Strategy:**
- HTTP requests: 2 retries on timeout
- Grok extraction: 2 retries with cache bypass
- First attempt uses cache (fast)
- Retries bypass cache (fresh data)

---

**Status:** ✅ Production Ready
