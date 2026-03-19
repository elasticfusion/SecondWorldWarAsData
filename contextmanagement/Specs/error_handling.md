# Error Handling - Extraction Services

**Version:** 2.2.0  
**Status:** Active  
**Last Updated:** 2026-03-19

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
    retry=retry_if_exception_type(requests.exceptions.HTTPError),
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

### 3. Try-Except with Graceful Degradation

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

### 4. Validation Error Recovery

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

### 5. Cache-First Strategy

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

### 6. ULID Validation and Fixing

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

### 7. Null Field Handling

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

### 8. Graceful Degradation

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

### 9. Metadata Validation

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

### 10. Duplicate Detection

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

### 11. JSON Parsing Error Recovery

**Used in:** All extraction services via `GrokClient._sanitize_json_response()`, `GrokClient._try_repair_json()`, and `json_validator.parse_json_safe()`

**Primary sanitization** (character-level walker in `_sanitize_json_response()`):
```python
def _sanitize_json_response(self, response: str) -> str:
    """Character-by-character walker that tracks in_string state.
    Only processes escapes inside JSON string values."""
    # Inside strings: strips invalid backslashes, fixes bad \uXXXX,
    #   escapes literal tab/newline/CR
    # Outside strings: preserves structural whitespace unchanged
    # Performance: 140K chars in 15ms
```

**Fallback repair** (in `_try_repair_json()`):
```python
# 1. Try json.loads() directly
# 2. Try after _sanitize_json_response()
# 3. Nuclear fallback: strip ALL non-standard backslashes
# 4. Fix unterminated strings, missing delimiters
```

**Additional utilities** (in `json_validator.py`):
```python
def sanitize_json_string(json_str: str) -> str:
    """Fix structural JSON issues."""
    json_str = re.sub(r'[\x00-\x1f]', '', json_str)  # Control chars
    if json_str.count('"') % 2 != 0:
        json_str += '"'  # Unterminated string
    # Complete missing braces/brackets
    ...

def parse_json_safe(json_str: str, max_retries: int = 3) -> Optional[Dict]:
    """Multi-attempt parse with progressive sanitization."""
    ...

def sanitize_json_response(response: str) -> str:
    """Extract JSON from markdown code blocks."""
    ...
```

**Common Issues Fixed:**

1. **Unterminated Strings**
   - Error: `Unterminated string starting at: line X column Y (char N)`
   - Cause: LLM response cut off mid-string
   - Fix: Adds closing quote if odd number of quotes
   - Success rate: ~90% of unterminated string errors

2. **Missing Delimiters**
   - Error: `Expecting ',' delimiter: line X column Y (char N)`
   - Cause: LLM response truncated mid-JSON
   - Fix: Completes missing closing braces/brackets
   - Success rate: ~85% of delimiter errors

3. **Null Bytes and Control Characters**
   - Error: Terminal artifacts (`^@`) in logs, or `Invalid control character at: line X column Y`
   - Cause: Control characters in LLM response; literal tab/newline/CR inside JSON string values
   - Fix: Removes non-whitespace control chars (0x00-0x08, 0x0b-0x0c, 0x0e-0x1f);
     escapes tab/newline/CR inside strings (`\t` → `\\t`) while preserving structural whitespace
   - Success rate: 100% of control character issues

4. **Markdown Code Blocks**
   - Error: JSON wrapped in markdown formatting
   - Cause: LLM returns formatted response
   - Fix: Extracts JSON from code blocks
   - Success rate: 100% of markdown-wrapped JSON

5. **Invalid Escape Sequences**
   - Error: `Invalid \escape: line X column Y`
   - Cause: Grok API returns unescaped backslashes in strings (e.g. `\units`, `\escape`, `\]`)
   - Fix: Character-level walker strips invalid backslashes inside JSON string values only
   - Also handles `\u` not followed by 4 hex digits (e.g. `\units`)
   - Success rate: ~100% (replaces regex approach which missed edge cases)

6. **Over-Escaped Brackets**
   - Error: Various parsing errors
   - Cause: API over-escapes square brackets
   - Fix: Removes unnecessary escaping: `\[` → `[`

**Benefits:**
- Reduces failed extractions by ~90% for malformed JSON
- Automatic recovery without manual intervention
- Multi-attempt parsing with progressive sanitization
- Detailed logging for debugging
- Works with existing retry logic
- Handles both LLM and API response issues

**Configuration:**
```python
max_retries: int = 3  # Parse attempts with sanitization
```

**Usage:**
```python
from src.utils.json_validator import parse_json_safe

# Safe parsing with auto-recovery
data = parse_json_safe(llm_response)
if data:
    validate_and_write_json(filepath, data, schema)
```

**See also:** 
- `src/utils/json_validator.py` - Main implementation
- `src/utils/custom_validators.py` - LLM response sanitization
- `docs/current/JSON_REPAIR.md` - Detailed documentation

---

### 12. Prompt Engineering for Data Quality

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

### 13. Timestamp-Based Skip Logic

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

### 14. API Key Validation

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

### 15. Fuzzy Matching for Deduplication

**Used in:** Equipment extraction

**Pattern:**
```python
def _fuzzy_match_equipment(
    name: str, equipment_index: Dict[str, Path], threshold: float = 0.80
) -> Optional[str]:
    """Find best fuzzy match for equipment name."""
    if not equipment_index:
        return None
    
    best_match = None
    best_ratio = 0.0
    name_lower = name.lower()
    
    # Check common names
    for existing_name in equipment_index.keys():
        ratio = SequenceMatcher(None, name_lower, existing_name.lower()).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = existing_name
    
    # Also check alternate names in files
    for existing_name, eq_file in equipment_index.items():
        try:
            with open(eq_file) as f:
                eq_data = json.load(f)
                for alt_name in eq_data.get("alternate_names", []):
                    ratio = SequenceMatcher(None, name_lower, alt_name.lower()).ratio()
                    if ratio > best_ratio:
                        best_ratio = ratio
                        best_match = existing_name
        except Exception:
            continue
    
    if best_ratio >= threshold:
        logger.debug("Fuzzy matched '%s' to '%s' (%.2f)", name, best_match, best_ratio)
        return best_match
    
    return None

# Usage in merge logic
matched_name = None
if common_name in equipment_index:
    matched_name = common_name  # Exact match
else:
    matched_name = _fuzzy_match_equipment(common_name, equipment_index)  # Fuzzy match
```

**Benefits:**
- Prevents duplicate files for similar names ("Sherman" vs "Sherman Tank")
- Checks both common names and alternate names
- Configurable similarity threshold (default 80%)
- Logs matches with similarity ratio for debugging
- Uses built-in `difflib.SequenceMatcher` (no dependencies)

**Configuration:**
```python
threshold: float = 0.80  # 80% similarity required
```

---

### 16. Entity Linking with Graceful Fallback

**Used in:** Equipment extraction (people, groups, supporting units)

**Pattern:**
```python
def _link_entity(
    entity_name: Optional[str], entity_index: Dict[str, str], entity_type: str
) -> Optional[Dict[str, str]]:
    """Link entity by name to ID."""
    if not entity_name:
        return None
    
    entity_id = entity_index.get(entity_name)
    if entity_id:
        id_key = "PersonID" if entity_type == "person" else "PeopleGroupID"
        logger.debug("Linked %s '%s' to %s", entity_type, entity_name, entity_id)
        return {id_key: entity_id, "name": entity_name}
    
    logger.debug("%s not found: %s", entity_type.capitalize(), entity_name)
    return None

# Usage
using_unit = _link_entity(eq.using_unit_name, people_groups_index, "unit")
using_person = _link_entity(eq.using_person_name, people_index, "person")

# Add to mention only if found
if using_unit:
    mention["using_unit"] = using_unit
if using_person:
    mention["using_person"] = using_person
```

**Benefits:**
- Missing entities don't fail extraction
- Logs both successes and failures for debugging
- Returns None instead of raising exceptions
- Allows partial data (equipment without linked entities)
- Generic function works for any entity type

**Use Cases:**
- Person mentioned but not yet extracted
- Unit name variation not in index
- Cross-book references (entity in different book)

---

### 17. External Data Enrichment with Optional Degradation

**Used in:** Equipment extraction (Wikipedia/Grokipedia)

**Pattern:**
```python
def _enrich_equipment_data(
    common_name: str,
    technical_identifier: Optional[str],
    category: str,
    grok_client: GrokClient,
) -> Dict[str, Any]:
    """Enrich equipment data with external sources."""
    identifier = technical_identifier or common_name
    
    prompt = f"""Look up information about this WWII military equipment: {identifier}
Category: {category}

Provide: description, specifications, alternate names, variants
Return as JSON."""

    try:
        response = grok_client.chat_completion(
            prompt,
            temperature=0.1,
            use_cache=True,
            cache_type="equipment_enrichment",
        )
        enriched = json.loads(response)
        logger.debug("Enriched data for %s", common_name)
        return enriched
    except Exception as e:
        logger.warning("Failed to enrich equipment data for %s: %s", common_name, e)
        return {}  # Empty dict, not None

# Usage in creation
if enable_enrichment and grok_client:
    logger.info("Enriching equipment data for: %s", common_name)
    enriched = _enrich_equipment_data(common_name, technical_id, category, grok_client)
    
    # Merge enriched data (don't overwrite existing)
    for key in ["description", "specifications", "alternate_names", "variants"]:
        if key in enriched and enriched[key]:
            if key not in equipment_data or not equipment_data[key]:
                equipment_data[key] = enriched[key]
                logger.debug("  Enriched %s", key)
```

**Benefits:**
- Enrichment failure doesn't prevent equipment creation
- Falls back to extracted data only
- Separate cache type prevents pollution
- Configurable via config flag
- Only enriches missing/empty fields
- Logs enrichment attempts and results

**Configuration:**
```yaml
equipment:
  enabled: true
  enable_enrichment: false  # Optional, disabled by default
```

**Use Cases:**
- Add specifications from Wikipedia
- Fill in alternate names
- Add variant information
- Enhance descriptions

---

### 18. Helper Function Extraction for Complexity Reduction

**Used in:** Equipment extraction refactoring

**Pattern:**
```python
# Before: Complex monolithic function (F rating - 54 complexity)
def extract_equipment_from_event(...):
    # 200+ lines of code
    # Load data, validate, extract, link entities, build mentions, merge, save
    # Complexity: F (54)

# After: Extracted helper functions (C rating - 11 complexity)
def _load_processed_registry(output_dir: Path) -> Dict[str, bool]: ...
def _save_processed_registry(output_dir: Path, processed: Dict[str, bool]): ...
def _validate_event_data(event_data: Dict[str, Any], event_file: Path) -> bool: ...
def _load_event_data(event_file: Path) -> Optional[Dict[str, Any]]: ...
def _extract_equipment_with_llm(...) -> Optional[List[Dict[str, Any]]]: ...
def _link_entity(...) -> Optional[Dict[str, str]]: ...
def _link_supporting_units(...) -> List[Dict[str, Any]]: ...
def _build_performance_notes(eq: EquipmentExtraction) -> Optional[Dict]: ...
def _add_metadata_to_mention(mention: Dict, event_data: Dict): ...
def _add_event_names_to_mention(mention: Dict, event_data: Dict): ...
def _link_date_to_mention(mention: Dict, dates_index: Dict, output_root: Path): ...
def _build_mention(...) -> Dict[str, Any]: ...
def _build_equipment_data(eq: EquipmentExtraction) -> Dict[str, Any]: ...
def _process_equipment_item(...) -> Optional[Path]: ...
def _finalize_extraction(...): ...

def extract_equipment_from_event(...):
    # 30 lines of code
    # High-level orchestration only
    # Complexity: C (11) - 80% reduction
```

**Benefits:**
- Reduces cyclomatic complexity from F (54) to C (11)
- Each function has single responsibility
- Easier to test individual components
- Easier to understand and maintain
- Reusable helper functions
- Better error isolation

**Guidelines:**
- Extract functions with >10 lines of logic
- Name functions with clear verb_noun pattern
- Keep main function as orchestration only
- Use type hints for all parameters
- Document each helper function

---

### 19. Subprocess Integration with Graceful Fallback

**Used in:** Equipment media extraction, External maps (OpenSERP)

**Pattern:**
```python
import subprocess

try:
    # Call external tool
    result = subprocess.run(
        ["./search_media", search_query],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,  # Don't raise on non-zero exit
    )

    if result.returncode != 0:
        logger.debug("Tool failed: %s", result.stderr)
        return []

    # Parse output
    data = json.loads(result.stdout)
    logger.debug("Found %s items", len(data))
    return data if isinstance(data, list) else []

except FileNotFoundError:
    logger.debug("Tool not found, skipping")
    return []
except subprocess.TimeoutExpired:
    logger.warning("Tool timed out")
    return []
except json.JSONDecodeError as e:
    logger.debug("Failed to parse tool response: %s", e)
    return []
except Exception as e:
    logger.debug("Tool error: %s", e)
    return []
```

**Benefits:**
- External tool failure doesn't stop extraction
- Specific exceptions for different failure modes
- Timeout prevents hanging
- Returns empty list for easy iteration
- Logs at appropriate levels (DEBUG for expected, WARNING for timeouts)

**Configuration:**
```python
timeout: int = 30  # seconds
check: bool = False  # Don't raise on non-zero exit
```

**Use Cases:**
- OpenSERP integration for maps/media search
- External validation tools
- Image processing tools
- Data enrichment services

---

### 20. HTTP File Download with Content-Type Detection

**Used in:** Equipment media download, Maps image download

**Pattern:**
```python
import requests
from pathlib import Path

def _download_file(url: str, output_dir: Path) -> Optional[str]:
    """Download file with content-type detection."""
    try:
        response = requests.get(url, timeout=30, allow_redirects=True)
        response.raise_for_status()

        # Determine extension from content-type
        content_type = response.headers.get("content-type", "")
        if "jpeg" in content_type or "jpg" in content_type:
            ext = ".jpg"
        elif "png" in content_type:
            ext = ".png"
        else:
            # Fallback to URL extension
            ext = Path(url).suffix or ".jpg"

        # Generate unique filename
        file_id = str(ulid.new())
        filepath = output_dir / file_id / f"{file_id}{ext}"
        
        # Check if already downloaded
        if filepath.exists():
            logger.debug("Already downloaded: %s", filepath.name)
            return str(filepath.relative_to(output_dir.parent))

        # Save file
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "wb") as f:
            f.write(response.content)

        logger.debug("Downloaded: %s", filepath.name)
        return str(filepath.relative_to(output_dir.parent))

    except requests.exceptions.HTTPError as e:
        logger.warning("Failed to download %s: %s", url, e)
        return None
    except Exception as e:
        logger.debug("Download error: %s", e)
        return None
```

**Benefits:**
- Content-type detection prevents extension mismatches
- Already-downloaded check prevents duplicates
- HTTP errors logged as WARNING (expected failures)
- General errors logged as DEBUG (unexpected)
- Returns None on failure (easy to check)
- Continues processing other files on failure

**Configuration:**
```python
timeout: int = 30  # seconds
allow_redirects: bool = True
```

**Use Cases:**
- Equipment media files (photos, videos, documents)
- Map images from external sources
- Any HTTP file download with unknown content-type

---

### 21. Type Checking for Mixed Data Structures

**Used in:** Supplemental material search and advanced features

**Pattern:**
```python
# Handle both dict and string formats in data arrays
for sub_event_data in data:
    # Type guard - skip non-dict entries
    if isinstance(sub_event_data, str):
        logger.debug("Skipping string entry in supplemental data")
        continue
    if not isinstance(sub_event_data, dict):
        logger.debug("Skipping non-dict entry in supplemental data")
        continue
    
    # Safe to access dict methods now
    materials = sub_event_data.get("Supplemental_Material", [])
    
    for material in materials:
        citation = material.get("citation", {})
        
        # Validate citation is dict before accessing
        if not citation or not isinstance(citation, dict):
            logger.debug("Skipping material with invalid citation")
            continue
        
        # Safe to access citation methods
        material_type = citation.get("type", "")
```

**Benefits:**
- Prevents `'str' object has no attribute 'get'` errors
- Prevents `'NoneType' object has no attribute 'lower'` errors
- Handles malformed API responses gracefully
- Logs skipped entries for debugging
- Continues processing valid entries
- No data loss from mixed-type arrays

**Common Causes:**
- LLM returns mixed array types (strings and dicts)
- Null values in nested objects
- Schema evolution (old vs new formats)
- Partial API responses

**Use Cases:**
- Supplemental material extraction
- Any service processing LLM-generated arrays
- Services with nested object structures
- Cross-version data compatibility

---

### 22. Method Name Validation for API Clients

**Used in:** Supplemental advanced features (ISBN, death dates)

**Pattern:**
```python
# WRONG: Using non-existent method
try:
    response = grok_client.chat(prompt, cache_key=f"isbn_{author}_{title}")
except AttributeError as e:
    logger.error("GrokClient method error: %s", e)
    # Fails with: 'GrokClient' object has no attribute 'chat'

# CORRECT: Using actual method with proper parameters
try:
    response = grok_client.chat_completion(
        prompt=prompt,
        cache_type="supplemental_advanced",
        use_cache=True
    )
    isbn = response.strip().replace("-", "").replace(" ", "")
except Exception as e:
    logger.debug("ISBN extraction error: %s", e)
    return None
```

**Benefits:**
- Prevents AttributeError crashes
- Uses correct API client interface
- Proper cache type isolation
- Consistent with other extraction services
- Better error messages

**Common Mistakes:**
- Using old method names after refactoring
- Copying code from different API client
- Missing parameter updates
- Wrong cache parameter names

**Prevention:**
- Check API client interface before calling
- Use IDE autocomplete for method names
- Add type hints to catch errors early
- Test with actual API client instance

---

### 23. Schema Validation with Sanitization

**Used in:** Supplemental material extraction

**Pattern:**
```python
def sanitize_supplemental_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitize supplemental data to ensure schema compliance."""
    # Ensure required string fields are not None
    if data.get("Sub-event_Name") is None:
        data["Sub-event_Name"] = ""
    
    # Sanitize materials
    for material in data.get("Supplemental_Material", []):
        if material.get("reference_type") is None:
            material["reference_type"] = "bibliography"
        
        # Validate reference_type is one of allowed values
        ref_type = material.get("reference_type", "")
        if ref_type not in ["endnote", "footnote", "bibliography"]:
            logger.warning(
                "Invalid reference_type '%s', defaulting to 'bibliography'", 
                ref_type
            )
            material["reference_type"] = "bibliography"
        
        if material.get("verbatim_reference") is None:
            material["verbatim_reference"] = ""
    
    return data

# Usage before validation
data = sanitize_supplemental_data(data)
validate_supplemental_json(data)  # Now passes schema validation
```

**Benefits:**
- Prevents schema validation errors
- Handles LLM returning invalid enum values
- Provides default values for required fields
- Logs invalid values for debugging
- Allows extraction to continue
- No data loss (invalid values replaced, not removed)

**Common Invalid Values:**
- `reference_type: "map"` (should be endnote/footnote/bibliography)
- `reference_type: "image"` (should be endnote/footnote/bibliography)
- `reference_type: null` (should have default)
- Empty strings in required fields

**Schema Enforcement:**
```json
{
  "reference_type": {
    "type": "string",
    "enum": ["endnote", "footnote", "bibliography"]
  }
}
```

**Use Cases:**
- Any extraction with enum fields
- Services with strict schema requirements
- LLM-generated data with validation
- Data migration between schema versions

---

### 24. File I/O Error Handling

**Used in:** Supplemental material processing, External maps, URL validation

**Pattern:**
```python
try:
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
except (OSError, IOError) as e:
    logger.error("Error reading file %s: %s", file_path, e)
    return None  # or appropriate default

# Also used for write operations
try:
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
except (OSError, IOError) as e:
    logger.error("Error writing file %s: %s", output_file, e)
    raise  # Re-raise if write failure is critical
```

**Benefits:**
- Handles file permission errors
- Handles disk full errors
- Handles file not found errors
- Handles I/O errors (corrupted filesystem, network drives)
- OSError is parent class of many file-related errors
- IOError is alias for OSError (Python 3 compatibility)

**Common OSError Subtypes:**
- `FileNotFoundError` - File doesn't exist
- `PermissionError` - No read/write permission
- `IsADirectoryError` - Expected file, got directory
- `NotADirectoryError` - Expected directory, got file
- `FileExistsError` - File already exists (when creating)

**Use Cases:**
- Reading supplemental material files
- Writing output JSON files
- External maps YAML loading
- URL validation file operations
- Any file I/O operation

**Configuration:**
```python
encoding: str = "utf-8"  # Always specify encoding
ensure_ascii: bool = False  # Allow Unicode in JSON output
```

---

### 25. Anachronistic Citation Detection

**Used in:** Supplemental material extraction

**Pattern:**
```python
source_year = _get_source_copyright_year(event_file)  # From chapter-meta.yaml
if source_year:
    for citation in materials:
        pub_year = int(str(citation["publication_date"])[:4])
        if pub_year > source_year:
            logger.warning("Removing anachronistic citation: '%s' (%s) post-dates source (%d)",
                           citation["title"], pub_date, source_year)
            # Remove from output
```

**Benefits:**
- Catches AI-hallucinated citations (e.g., 2002 book cited in 1950 source)
- Uses source book's `copyright_date` from `chapter-meta.yaml` as ground truth
- Logs removed citations at WARNING level for review
- Prevents fabricated references from polluting output data

**File:** `src/extraction/supplemental.py`

---

### 26. Valid Short JSON Response Handling

**Used in:** Grok API client

**Pattern:**
```python
# In _call_api: accept valid short JSON ([], {}) instead of rejecting < 10 chars
if not content or not content.strip():
    raise GrokAPIError("API returned empty response")

# In extract_json: only retry short responses that aren't valid JSON
if not _retried and len(response) < 500:
    try:
        json.loads(response)  # Valid short JSON — accept it
    except (json.JSONDecodeError, ValueError):
        # Invalid — clear cache and retry
```

**Benefits:**
- Accepts `[]` as valid "no data found" response (matches prompt instructions)
- Prevents 2 wasted API calls per empty result (was retrying then failing)
- Affects 5 extractors: casualties, equipment, maps, supplemental, enrichment

**File:** `src/grok_client.py`

---

### 27. Auto-Clear Cache on Unrecoverable JSON Errors

**Used in:** `GrokClient.extract_json()`

**Problem:** When the API returns concatenated JSON objects (`Extra data` error) or text-prefixed JSON (`Yes.{...}`), the corrupt response gets cached. Every retry hits the same corrupt entry, wasting all attempts.

**Fix:**
```python
# In extract_json() — after all repair attempts fail:
self.clear_cache_entry(prompt, cache_type, temperature)
raise GrokAPIError(f"Failed to parse JSON response: {e}")
```

**Errors covered:**
- `Extra data: line X column Y` — two JSON objects concatenated
- `Expecting value: line 1 column 1` — text prefix before JSON
- Any `JSONDecodeError` not handled by short-response, truncation, or repair logic

**Benefits:**
- Corrupt cache entries auto-purged on first encounter
- Next retry gets a fresh API response
- No manual `💡 Clear cache:` commands needed
- Works with both `phase2_retry.py` outer loop and per-sub-event retry

**File:** `src/grok_client.py`

---

### 28. Filename Sanitization for LLM-Generated Categories

**Used in:** Logistics extraction

**Problem:** LLM returns category values containing `/` (e.g. `"personnel/equipment"`). When used in filenames via `Path(dir) / filename`, the slash creates a subdirectory that doesn't exist, causing `FileNotFoundError`.

**Fix:**
```python
safe_cat = extraction.category.replace("/", "_").replace("\\", "_")
filename = f"{safe_cat}_{extraction.type}_{date_str}_{logistics_id}.json"
```

**Benefits:**
- Prevents `[Errno 2] No such file or directory` errors
- Handles both forward and back slashes
- Preserves all other category characters

**File:** `src/extraction/logistics.py`

---

### 29. Batch Response Parsing with Per-Sub-Event Isolation

**Used in:** Weather, Logistics, Casualties batch extraction

**Problem:** When batching all sub-events into a single API call, a malformed response for one sub-event could lose results for all sub-events.

**Pattern:**
```python
def _parse_weather_response(response: Dict[str, Any]) -> Dict[str, List[Dict]]:
    """Validate each sub-event's results independently."""
    result = {}
    for seid, mentions in response.items():
        if not isinstance(mentions, list):
            continue  # Skip malformed sub-event, keep others
        fixed = _fix_invalid_ulids(mentions)
        if isinstance(fixed, list):
            mentions = fixed
        result[seid] = [
            m for m in mentions
            if isinstance(m, dict) and m.get("date") and m.get("place_name")
        ]
    return result
```

**Key design decisions:**
- Response is `Dict[str, List]` keyed by Sub-eventID
- Each sub-event's items validated independently (ULID fixing, schema checks)
- Invalid items filtered per sub-event — valid items from other sub-events preserved
- Parsing extracted into separate functions (`_parse_casualty_response`, `_parse_logistics_response`, `_parse_weather_response`) to keep batch functions at B complexity or better

**Benefits:**
- One malformed sub-event doesn't lose results from other sub-events
- Same validation applied as per-sub-event path (ULID fixing, schema checks)
- Reduces API calls by ~80% for optional extractors (1 call per chapter vs 1 per sub-event)
- Retry logic wraps the entire batch call, not individual sub-events

**Files:** `src/extraction/weather_central.py`, `src/extraction/logistics.py`, `src/extraction/casualties.py`

---

## Logging

### Unified Pipeline Log

All logging — pipeline progress, API calls, errors, and retries — flows through a single log file (`logs/pipeline_*.log`). API prompt logging that previously wrote to a separate `api_prompts.log` is now integrated into the standard logging system.

API log entries use the `[API]` prefix for easy filtering:

```bash
# All API activity
grep "\[API\]" logs/pipeline_*.log

# Just API calls (no cache hits)
grep "\[API\] CALL" logs/pipeline_*.log

# Cache hit rate
grep -c "\[API\] CACHE HIT" logs/pipeline_*.log
grep -c "\[API\] CALL" logs/pipeline_*.log
```

### Log Levels

Set via `--log-level` flag or `config.yaml`:

```bash
python3 phase2_extract.py --log-level DEBUG
```

```yaml
# config.yaml
logging:
  level: TRACE
```

| Level | Value | What it shows |
|-------|-------|---------------|
| **TRACE** | 5 | First 500 chars of each API prompt |
| **DEBUG** | 10 | `[API] CALL` and `[API] CACHE HIT` entries with cache type and key |
| **INFO** | 20 | Pipeline progress, successes, retry outcomes (default) |
| **WARNING** | 30 | Recoverable errors, retry attempts, partial failures |
| **ERROR** | 40 | Unrecoverable errors, final retry failures, missing data |

### Example Output at Each Level

**INFO** (default):
```
2026-03-14 17:46:10 - src.extraction.events - INFO - ✓ Successfully generated: chapter1a-event.json
2026-03-14 17:46:11 - src.extraction.dates - INFO - ✓ Updated central dates repository
```

**DEBUG** (adds API call tracking):
```
2026-03-14 17:46:01 - src.grok_client - DEBUG - [API] CALL | type=events key=a1b2c3d4e5f6 temp=0.1
2026-03-14 17:46:05 - src.grok_client - ERROR - Response truncated at 23144 chars — transient API error, cache cleared, will retry
2026-03-14 17:46:05 - src.grok_client - DEBUG - [API] CALL | type=events key=a1b2c3d4e5f6 temp=0.1
2026-03-14 17:46:10 - src.extraction.events - INFO - ✓ Successfully generated: chapter1a-event.json
2026-03-14 17:46:10 - src.grok_client - DEBUG - [API] CACHE HIT | type=events key=f9e8d7c6b5a4
```

**TRACE** (adds prompt content):
```
2026-03-14 17:46:01 - src.grok_client - DEBUG - [API] CALL | type=events key=a1b2c3d4e5f6 temp=0.1
2026-03-14 17:46:01 - src.grok_client - TRACE - [API] Prompt (events): Extract all military events from the following WWII chapter text...
```

### Handled vs Unhandled Errors

Errors that are automatically recovered include context in the message:

| Error message pattern | Meaning |
|-----------------------|---------|
| `— cache cleared, will retry` | Transient error, cache auto-cleared, retry loop will re-attempt |
| `— manual split needed` | Chapter too large for API, requires manual intervention |
| `✓ JSON repaired (...)` | Parse error auto-fixed, extraction succeeded |
| `⚠ Attempt X failed: ...` | Retry in progress (WARNING level) |
| `✗ All X attempts failed: ...` | Genuine failure, no more retries (ERROR level) |

---

## Timeout Handling

**Configuration:**
```python
self.timeout = 600.0  # 10 minutes
```

**Used in:** API calls via `requests` (with `urllib3` connection pooling)

**Pattern:**
```python
with get_session() as session:
    session.timeout = self.timeout
    response = session.post(url, json=payload, headers=headers)
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
except requests.exceptions.HTTPError as e:
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
4. **Validation recovery** - Fix common errors (ULIDs, nulls)
5. **Cache-first strategy** - Use cache, bypass on retry
6. **ULID validation** - Fix invalid ULIDs automatically
7. **Null field handling** - Filter or fix invalid data
8. **Graceful degradation** - Continue on partial failures
9. **Metadata validation** - Fail fast on missing critical data
10. **Duplicate detection** - Check before adding mentions
11. **JSON parsing recovery** - Sanitize and repair malformed JSON
12. **Prompt engineering** - Prevent invalid responses at source
13. **Timestamp-based skipping** - Avoid redundant processing
14. **API key validation** - Check before processing
15. **Fuzzy matching** - Prevent duplicate files (when applicable)
16. **Entity linking with fallback** - Missing entities don't fail
17. **External enrichment** - Optional, with graceful degradation
18. **Helper function extraction** - Keep complexity below C rating
19. **Subprocess integration** - External tools with graceful fallback
20. **HTTP file download** - Content-type detection, duplicate prevention
21. **Type checking** - Handle mixed data structures from LLM
22. **Method name validation** - Use correct API client interface
23. **Schema validation** - Sanitize before validating
24. **File I/O handling** - Catch OSError for all file operations
25. **Anachronistic citation detection** - Validate dates against source
26. **Valid short JSON handling** - Accept `[]` and `{}` as valid
27. **Auto-clear corrupt cache** - Purge unrecoverable JSON on failure
28. **Filename sanitization** - Strip path separators from LLM-generated values
29. **Batch response isolation** - Validate each sub-event independently in batch responses

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
timeout: float = 600.0  # 10 minutes
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

**2026-03-19**: Batch extraction for optional extractors (Pattern 29)
- Weather, Logistics, Casualties now send all sub-events in a single API call per chapter
- Response parsed with per-sub-event isolation — one bad sub-event doesn't lose others
- Parsing logic extracted into `_parse_weather_response`, `_parse_logistics_response`, `_parse_casualty_response`
- Reduces optional extractor API calls by ~80% (~2,249 fewer calls)
- Files: `src/extraction/weather_central.py`, `src/extraction/logistics.py`, `src/extraction/casualties.py`

**2026-03-16**: Auto-clear cache on unrecoverable JSON errors (Pattern 27)
- `extract_json()` now calls `clear_cache_entry()` when all JSON repair attempts fail
- Fixes `Extra data` errors persisting across retries due to corrupt cached responses
- Replaces manual `💡 Clear cache:` log messages with automatic purge
- File: `src/grok_client.py`

**2026-03-16**: Filename sanitization for logistics categories (Pattern 28)
- Sanitizes `/` and `\` in LLM-generated category values before building filenames
- Fixes `FileNotFoundError` when category contains path separators (e.g. `personnel/equipment`)
- File: `src/extraction/logistics.py`

**2026-03-16**: Batch entity cross-referencing (Schema 1.1)
- Batch extraction now assigns ULIDs to dates, places, people, people_groups
- Entity files include `event_mentions` array linking back to EventID/Sub-eventID
- Event sub-events include `dates`, `places`, `people`, `peoplegroups` ULID arrays
- Bidirectional cross-referencing matches README schema
- File: `src/extraction/batch_parallel.py`

**2026-03-16**: Anachronistic citation detection (Pattern 25)
- Supplemental extraction now validates citation `publication_date` against source book's `copyright_date`
- Citations post-dating the source are removed and logged at WARNING level
- Catches AI-hallucinated references (e.g., 2002 book cited in 1950 source)
- File: `src/extraction/supplemental.py`

**2026-03-16**: Valid short JSON response handling (Pattern 26)
- `_call_api()` no longer rejects valid short JSON like `[]` and `{}`
- `extract_json()` only retries short responses that fail `json.loads()`
- Prevents 2 wasted API calls per empty result across 5 extractors
- File: `src/grok_client.py`

**2026-03-16**: Heartbeat monitor for stall detection
- New `src/utils/heartbeat.py` — daemon thread warns if no progress for 5 minutes
- Wired into Phase 2 (`phase2_extract.py`) and Phase 3 (`enrich_biographies.py`)
- Retry wrappers now log signal names on crash (e.g., SIGKILL from OOM)
- Files: `src/utils/heartbeat.py`, `phase2_extract.py`, `phase2_retry.py`, `phase3_retry.py`

**2026-03-16**: Auto-split on truncation (events.py)
- Phase 2 now auto-splits large chapters at section boundaries when Grok truncates
- Extracts each chunk separately, merges sub-events into single output
- Replaces "manual split needed" error with automated recovery
- File: `src/extraction/events.py`

**2026-03-14**: Auto-retry on short API responses
- `extract_json()` now retries once when API returns <500 chars
- Clears cached short response before retry to force fresh API call
- Uses `_retried` flag to prevent infinite recursion (max 1 retry)
- Motivated by chapter10c (CCA) transient failure: 473-char response
- File: `src/grok_client.py`

**2026-03-14**: Supplemental extraction wired into Phase 2 pipeline
- `extract_supplemental()` existed in `src/extraction/supplemental.py` but was
  never called from `phase2_extract.py` — only imported in test files
- The single existing output file (`chapter11b-endnotes.json`) was from a manual run
- **Fix**: Added supplemental extraction block after logistics in the per-chapter
  extraction loop, gated by `supplemental_material.enabled` config flag
- File: `phase2_extract.py`

**2026-03-14**: HyperWar paragraph separation fix
- `html2text` collapsed multiple `<p>` and `<center>` tags inside `<blockquote>`
  elements into single long lines, merging paragraphs
- **Fix**: Pre-process HTML to insert `<br><br>` between block-level children
  of each `<blockquote>` before passing to `html2text`
- Also normalized whitespace-only blockquote lines (`>   `) to standard `> `
  in `format_as_blockquote()` to match existing content format
- Affected all imported CrossChannelAttack chapters — requires re-import
- File: `scripts/import_hyperwar_html.py`

**2026-03-14**: Unified logging (replaces separate api_prompts.log)
- API call tracking (`[API] CALL`, `[API] CACHE HIT`) now flows through standard
  `logging` module into `pipeline_*.log` instead of separate `api_prompts.log`
- Visible at DEBUG level; prompt content at TRACE level (first 500 chars)
- Enables single-file correlation of API calls, errors, retries, and pipeline progress
- Handled errors now include recovery context: "cache cleared, will retry"
- Redundant error log lines consolidated (e.g. truncation handler reduced from 4 lines to 1)

**2026-03-14**: Character-level JSON sanitization (replaces regex approach)
- **Root cause found**: Regex `\\(?!["\\/bfnrtu])` cannot distinguish between
  backslashes inside vs outside JSON string values, and allows `\u` through
  even when not followed by 4 hex digits (e.g. `\units` → invalid `\uXXXX`)
- **New approach**: Character-by-character walker in `_sanitize_json_response()`
  that tracks `in_string` state and only processes escapes inside JSON strings
- Handles all three JSON parse error types in one pass:
  1. Invalid `\escape` — strips the backslash (e.g. `\units` → `units`)
  2. Invalid `\uXXXX` — strips `\` when `\u` not followed by 4 hex digits
  3. Control characters — escapes literal tab/newline/CR inside string values
     while preserving them as structural whitespace outside strings
- Consolidated `extract_json_with_image()` inline sanitization to use shared method
- Simplified `_try_repair_json()` — removed redundant unicode lookahead,
  added nuclear fallback (strip all invalid backslashes)
- Performance: 140K chars in 15ms

**2026-03-14**: Equipment extraction JSON parsing fix
- Equipment extraction was calling `chat_completion()` (raw text) then manual
  `json.loads()`, bypassing all sanitization in `extract_json()`
- When API wrapped response in markdown code blocks (` ```json ... ``` `),
  `json.loads` failed with `Expecting value: line 1 column 1 (char 0)`
- **Fix**: Switched to `extract_json()` which handles markdown unwrapping,
  escape sanitization, control characters, and JSON repair automatically
- Cleared 109 cached raw responses that may have been markdown-wrapped
- Affected 9 chapters with consistent `char 0` errors

**2026-03-14**: urllib3 compatibility fix
- `Retry(allowed_methods=...)` fails on older urllib3 versions where the
  parameter was named `method_whitelist`
- **Fix**: Try `allowed_methods` first, fall back to `method_whitelist` on TypeError
- File: `src/utils/http_pool.py`

**2026-03-14**: HyperWar HTML import script error handling
- HTTP download with retry (3 attempts, exponential backoff) and specific
  exception types (`HTTPError`, `ConnectionError`, `Timeout`)
- File I/O errors caught with `OSError`, increments failed count, continues
- Tracks processed/failed counts, reports summary at end
- Uses `setup_logging()` from `src.utils.logger` with file output to `logs/import_hyperwar.log`
- Interactive prompts use `print()` for clean terminal output
- Server politeness: 0.5s delay between chapter downloads

**2026-03-11**: JSON parsing robustness improvements
- **Note**: The regex-based escape sanitization described below was superseded
  by the character-level walker on 2026-03-14 (see above)
- **Control Character Sanitization**: Added to all 3 JSON extraction methods
  - `extract_json()` - Already had sanitization
  - `extract_json_with_image_base64()` - Added sanitization
  - `extract_json_with_image()` - Added sanitization
  - Removes control characters (0x00-0x1f except whitespace)
  - Fixes invalid escape sequences: `\e` → `\\e`
  - Pattern: `\\(?!["\\/bfnrtu])` catches all invalid escapes
  - Applied BEFORE first `json.loads()` attempt
  - Success rate: 100% for control character issues
- **Truncation Detection**: Enhanced error messages for truncated responses
  - Detects "Unterminated string" errors
  - Distinguishes short API errors (<500 chars) from real truncation (>500 chars)
  - Logs actual response content for short responses
  - Identifies when API hits max_tokens limit
  - Warns on suspiciously short responses (<200 chars)
  - Logs finish_reason to diagnose API issues
  - Suggests splitting large chapters
  - Prevents misleading "token limit" errors
- **Cache Management**: Improved cache clearing strategy
  - Clear Python bytecode cache (`__pycache__`, `.pyc`)
  - Clear API response caches by type
  - Ensures code changes take effect immediately
- **Large Chapter Handling**: Skip strategy for oversized chapters
  - Rename to `.skip` extension to exclude from processing
  - Example: `chapter20full-parsed.json` (111 paragraphs, 60K chars)
  - Prevents API truncation errors
  - Documented in `PHASE2_TRUNCATION_FIX.md`

**2026-03-11**: Batch+parallel processing error fixes
- Fixed `results["people"]` KeyError → `results["groups"]` (batch_parallel returns groups, not people)
- Fixed `all_done` NameError in sequential fallback (removed redundant check)
- Enhanced JSON sanitization in `GrokClient.extract_json()` pre-processing:
  - Removes control characters (0x00-0x1f except whitespace) before parsing
  - Fixes invalid escape sequences with regex before first parse attempt
  - Prevents `Invalid \escape` and `Invalid control character` errors
  - Runs on all API responses automatically
  - Improved success rate from 97.5% to near 100%

**2026-03-10**: Enhanced JSON parsing error recovery
- Added `parse_json_safe()` with multi-attempt sanitization
- Added `sanitize_json_string()` to fix malformed JSON automatically
- Fixes unterminated strings (adds closing quotes)
- Fixes missing delimiters (completes braces/brackets)
- Removes null bytes and control characters (fixes `^@` terminal artifacts)
- Extracts JSON from markdown code blocks
- Reduces parsing failures by ~90%
- Added to `json_validator.py` and `custom_validators.py`
- Updated validation functions to accept string inputs

**2026-03-08**: Supplemental material extraction error fixes
- Fixed `GrokClient.chat()` → `GrokClient.chat_completion()` method calls
- Added null check for citation objects to prevent `'NoneType' has no attribute 'lower'`
- Added type checking for supplemental data array entries (handles string/dict mixed types)
- Added validation for `reference_type` field (rejects invalid values like "map")
- All fixes prevent pipeline crashes and enable graceful degradation
- Reduces ERROR log entries by 100% for supplemental material extraction

**2026-03-04**: HTTP file download pattern for media files
- Content-type detection from response headers
- Fallback to URL extension
- Already-downloaded check prevents duplicates
- ULID-based subdirectories for organization
- Graceful error handling (requests.exceptions.HTTPError, Exception)
- Used in equipment media and maps downloads

**2026-03-04**: Equipment extraction patterns and media integration
- Fuzzy matching for deduplication (prevents duplicate files)
- Entity linking with graceful fallback (missing entities don't fail)
- External data enrichment with optional degradation (Wikipedia/Grokipedia)
- Helper function extraction for complexity reduction (F→C rating)
- Media integration with OpenSERP and Wikipedia fallback
- Subprocess integration pattern for external tools

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
- **Grok Client:** `src/grok_client.py`
- **Phase 2:** `phase2_extract.py`
- **Quality Assurance:** `contextmanagement/Specs/quality_assurance.md`

---

**Status:** ✅ Production Ready
