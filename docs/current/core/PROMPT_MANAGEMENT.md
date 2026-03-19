# Grok Prompt Management

Reference for all Grok AI prompts in the pipeline. Use this to understand what each extractor sends, how responses are validated, and where to make changes.

## GrokClient API Methods

All prompts flow through `src/grok_client.py`. Four public methods:

| Method | Returns | Use Case |
|--------|---------|----------|
| `chat_completion()` | `str` | Raw text responses (consolidation, search, ISBN lookup) |
| `extract_json()` | `dict` | JSON responses with auto-retry and repair |
| `extract_structured()` | `BaseModel` | Pydantic-validated responses (dates, places, people, weather, logistics) |
| `extract_json_with_image_base64()` | `dict` | Vision analysis (map/equipment verification) |

Common parameters: `prompt`, `system_prompt`, `temperature` (default 0.1), `use_cache` (default True), `cache_type`.

### Response Validation Chain (extract_json)

1. Strip markdown code fences
2. Sanitize JSON (fix trailing commas, unescaped chars, etc.)
3. `json.loads()` — if valid, return
4. If short (<500 chars) and invalid JSON → clear cache, raise error
5. If truncated → clear cache, raise `GrokAPIError` with "splitting chapter"
6. Attempt JSON repair (close brackets, fix quotes)
7. If all fails → raise `GrokAPIError`

Short valid JSON (`[]`, `{}`) passes step 3 and is returned without retry.

## Prompt Inventory

### Phase 2 — Core Extraction

#### events.py
- **System prompt:** Module-level `SYSTEM_PROMPT` — expert historian, extract events/sub-events
- **User prompt:** 1 — ULID requirements, chapter text, schema template
- **Anti-hallucination:** ULID format enforcement
- **Schema:** `EVENT_SCHEMA`
- **Method:** `extract_json` → temperature 0.1
- **Cache:** `events` (book-scoped)
- **Auto-split:** On truncation, splits chapter at section boundaries, merges results

#### dates.py
- **System prompt:** Module-level `SYSTEM_PROMPT` — expert historian, extract dates
- **User prompt:** 1 — extract all date/time mentions from event text
- **Anti-hallucination:** "Do NOT stop until all closing braces", "Do NOT include null date_start"
- **Schema:** Pydantic `DateExtractionOutput`
- **Method:** `extract_structured`
- **Cache:** `dates` (global)

#### places.py
- **System prompt:** Module-level `SYSTEM_PROMPT` — expert historian/geographer, extract places with coordinates
- **User prompt:** 1 — extract place mentions with GPS coordinates
- **Schema:** Pydantic `PlaceExtractionOutput`
- **Method:** `extract_structured`
- **Cache:** `places` (global)

#### people.py
- **System prompt:** Module-level `SYSTEM_PROMPT` — expert historian, extract people with biographical details
- **User prompt:** 1 — extract people mentions with biographical details
- **Schema:** Pydantic `PeopleExtractionOutput`
- **Method:** `extract_structured`
- **Cache:** `people` (global)

#### batch_parallel.py
- **System prompt:** None (uses events.py SYSTEM_PROMPT via shared prompt)
- **User prompts:** 2 — multi-chapter event extraction, entity batching
- **Method:** `extract_json`
- **Cache:** `events`, `dates`, `places`, `people`, `peoplegroups`

### Phase 2 — Optional Extraction

#### weather_central.py
- **System prompt:** Module-level `SYSTEM_PROMPT` — expert historian, extract weather
- **User prompt:** 1 — batch extract weather mentions from all sub-events (includes per-sub-event place/date context)
- **Anti-hallucination:** "ONLY extract weather explicitly mentioned", "Only extract EXACT dates"
- **Schema:** Response is JSON dict keyed by Sub-eventID; each mention validated against `WeatherMention` fields
- **Method:** `extract_json` (batch), `extract_structured` (single, legacy)
- **Cache:** `weather` (book-scoped)

#### logistics.py
- **System prompt:** None (inline in prompt)
- **User prompt:** 1 — batch extract logistics issues from all sub-events
- **Schema:** Response is JSON dict keyed by Sub-eventID; each item validated via `LogisticsExtraction.model_validate()`
- **Method:** `extract_json` (batch), `extract_structured` (single, legacy)
- **Cache:** `logistics` (book-scoped)

#### casualties.py
- **System prompt:** None (inline in prompt)
- **User prompt:** 1 — batch extract casualties from all sub-events with casualty mentions
- **Method:** `chat_completion` → parsed manually (batch), same for single (legacy)
- **Cache:** `casualties` (book-scoped)

#### equipment.py
- **System prompt:** None
- **User prompts:** 5 — extraction, enrichment lookup, media search, image URL extraction, image verification
- **Method:** `extract_json` (3), `chat_completion` (2), `extract_json_with_image_base64` (1)
- **Temperature:** 0.1 (extraction), 0.0 (verification)
- **Cache:** `equipment`, `equipment_enrichment`, `equipment_media`, `equipment_image_extraction`, `vision_verification`

#### supplemental.py
- **System prompt:** Module-level `SYSTEM_PROMPT` — expert librarian/historian
- **User prompts:** 2 — reference extraction, narrative content extraction
- **Anti-hallucination:** "CRITICAL: Only extract references that actually appear", "Do NOT invent, fabricate, or copy example references"
- **Multi-source instruction:** Split semicolon/period-delimited sources into separate entries
- **Schema:** `SUPPLEMENTAL_SCHEMA`
- **Method:** `extract_json` → temperature 0.1
- **Cache:** `supplemental`, `supplemental_narrative` (both book-scoped)

#### supplemental_search.py
- **System prompt:** Inline — "You are a research librarian. Only provide URLs you are certain about."
- **User prompt:** 1 — find URL for publication
- **Method:** `chat_completion` → temperature 0.0
- **Cache:** `supplemental_search` (book-scoped)

#### supplemental_advanced.py
- **System prompt:** None
- **User prompts:** 2 — ISBN lookup, author death date
- **Method:** `chat_completion`
- **Cache:** `supplemental_advanced` (book-scoped)

### Phase 2 — Maps

#### grok_search_maps.py
- **User prompts:** 2 — search for WWII maps, verify image relevance
- **Method:** `extract_json`, `extract_json_with_image_base64`
- **Temperature:** 0.1 (search), 0.0 (verification)
- **Cache:** `grok_search_maps`, `vision_verification`

#### openserp_maps.py
- **User prompt:** 1 — analyze website license terms
- **Method:** `extract_json` → temperature 0.0
- **Cache:** `license_check`

### Phase 3 — Enrichment

#### enrich_biographies.py
- **User prompt:** 1 — extract biographical data from Wikipedia/Grokipedia text
- **Method:** `extract_json` → temperature 0.1
- **Cache:** `api` (global)

### Phase 2 — Analysis

#### people_consolidation.py
- **System prompt:** Inline — "You are an expert historian identifying duplicate person entries."
- **User prompt:** 1 — identify duplicate people entries
- **Method:** `chat_completion`
- **Cache:** `people` (global)

## Prompt Patterns

### System Prompt Locations

Two patterns in use:

1. **Module-level constant** — `SYSTEM_PROMPT = """..."""` at top of file
   - Used by: events, dates, places, people, weather_central, supplemental
   - Easier to find and review

2. **Inline string** — `system_prompt="..."` passed directly in function call
   - Used by: supplemental_search, supplemental (narrative), people_consolidation
   - Harder to audit — search for `system_prompt=`

### Anti-Hallucination Guards

Currently present in:
- **supplemental.py** — "CRITICAL: Only extract references that actually appear" + "Do NOT invent, fabricate, or copy"
- **dates.py** — "Do NOT include mentions with null date_start"
- **weather_central.py** — "ONLY extract weather explicitly mentioned"

Not present in: events, places, people, logistics, casualties, equipment. These rely on schema validation to catch bad data rather than prompt-level guards.

### Temperature Settings

| Temperature | Used By | Purpose |
|-------------|---------|---------|
| 0.1 (default) | events, dates, places, people, weather, logistics, supplemental, equipment extraction, enrich_biographies | Deterministic extraction |
| 0.0 | supplemental_search, openserp_maps (license), grok_search_maps (verification), equipment (verification) | Zero-creativity tasks (URL lookup, yes/no verification) |

### Schema Enforcement

| Approach | Extractors | Validation |
|----------|-----------|------------|
| Pydantic (`extract_structured`) | dates, places, people, weather, logistics | Pydantic model validates before return |
| JSON schema in prompt | events, supplemental | Schema template shown in prompt, validated post-extraction |
| No schema | casualties, equipment (some), maps, consolidation | Manual parsing of response |

## Cache Architecture

Book-scoped types (under `cache/api/books/{BookName}/`):
- events, weather, equipment, logistics, casualties
- supplemental, supplemental_narrative, supplemental_search, supplemental_advanced

Global types (under `cache/api/`):
- dates, places, people, peoplegroups

Other (under `cache/api/`):
- grok_search_maps, vision_verification, license_check, equipment_media, equipment_image_extraction, equipment_enrichment, api (biographies)

## Known Issues

1. **Short response rejection** — `TODO_short_response_rejection.md`. The `chat_completion` path (L179) now only warns on short responses. The `extract_json` path (L745) tries `json.loads` before retrying. But 5 extractors using `chat_completion` + manual JSON parsing may still waste retries on valid short responses.

2. **Endnote content not fetched** — `TODO_fetch_endnote_content.md`. Supplemental prompt receives reference numbers but not actual text. Grok fabricates `verbatim_reference` content.

3. **Inconsistent system prompt patterns** — Mix of module-level constants and inline strings makes auditing harder.

## Modifying Prompts

1. Find the extractor file in the inventory above
2. Locate the system prompt (module-level `SYSTEM_PROMPT` or inline `system_prompt=`)
3. Locate the user prompt (search for `prompt = f"""`)
4. Make changes
5. Clear the relevant cache type: `rm -rf cache/api/books/*/{cache_type}/*` (book-scoped) or `rm -rf cache/api/{cache_type}/*` (global)
6. Test with `--max-items 1` or similar flag to verify output
7. Run QA: black, pylint, radon, bandit, vulture
