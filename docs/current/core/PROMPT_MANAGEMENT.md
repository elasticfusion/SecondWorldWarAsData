# Grok Prompt Management

**Last Updated:** 2026-11-16

How prompts reach Grok, how answers are validated, and where to change things. The per-prompt catalog (which code invokes which prompt, with its variables and cache lane) is [PROMPT_REFERENCE.md](../PROMPT_REFERENCE.md); how to edit a prompt safely is [PROMPT_EDITING_GUIDE.md](../PROMPT_EDITING_GUIDE.md). This file does not repeat them.

## Where prompts live

Prompt text is **not** in the Python modules. Each prompt is a YAML file in `prompts/` (34 files) with `system_prompt`, `prompt_template`, an optional `schema` and an optional `rules` list. Code loads it through `src/utils/prompt_loader.py`:

- `render_prompt(name, **vars)` returns the rendered user prompt. Substitution is **single pass**: each `{name}` is replaced once, left to right, so untrusted source text that happens to contain `{other}` is never re-interpolated. An unknown `{name}` is left intact (JSON braces survive). `{schema}` comes from the YAML's `schema` block, with only the structural placeholders (`event_id`, `sub_event_id`, `event_name`, `sub_event_summary`) pre-filled. `{quote_rule}` is the shared quote-provenance rule (`src/utils/quote_match.QUOTE_RULE`). `rules` are appended as a bullet list.
- `get_system_prompt(name)` returns the system message.
- `load_prompt(name)` is `lru_cache`d; `clear_cache()` resets it. When `aws.enabled` and `S3_BUCKET` is set it reads `s3://<bucket>/prompts/<name>.yaml` first and falls back to the local file, so a prompt can be changed in AWS without a rebuild.

A few prompts still have text in code (for example short inline system prompts in some Phase 3 helpers). Search for `system_prompt=` to find them; new prompts belong in `prompts/`.

## GrokClient methods

All requests go through `src/grok_client.py` (`GrokClient`; the model is `GROK_MODEL` env, else `api.grok.model`, `grok-4.3`).

| Method | Returns | Use |
|--------|---------|-----|
| `chat_completion()` | `str` | Raw text answers |
| `extract_json()` | `dict` | JSON answers with eviction, retry and repair. All Phase 2 chapter requests use it |
| `extract_structured()` | `BaseModel` | Answers validated against a Pydantic model |
| `extract_json_with_image_base64()` / `extract_json_with_image()` | `dict` | Vision checks |

Common parameters: `prompt`, `system_prompt`, `temperature` (default 0.1), `use_cache` (default True), `cache_type`; `extract_json` also takes `cache_key` and `book`. Signatures are in [API_REFERENCE.md](API_REFERENCE.md).

In batch mode (`batch_mode=True`, the Phase 2 default in AWS) these calls queue a request and raise `BatchModeCollecting`; the batch round loop (`src/utils/batch_rounds.py`) submits the queue, and the answers are read back through the same cache keys on the next round.

### Book identity and request identity

For a book-bound `cache_type` (`request_identity.BOOK_BOUND`: events, dates, places, people, peoplegroups, people_groups, weather, logistics, casualties, equipment_broad, equipment_refine, supplemental, source_section) `extract_json` stamps the prompt with the **BookID** and a per-request **RequestID** and requires the answer to echo both. A `mismatch` (the answer belongs to another request) is evicted and asked once more; a second mismatch raises `GrokAPIError`, so such an answer is never used. `missing` is asked once more, then accepted with a warning (in batch mode it is accepted at once); a non-object answer is accepted with a warning (`unverifiable`). Their cache lives under `books/<book>/<kind>`.

### Response validation chain (`extract_json`)

1. `json_payload`: take the JSON from the answer (as is, else the first fenced block, else the first JSON value in prose)
2. Sanitize escapes and control characters (`_sanitize_json_response`)
3. `json.loads()`; valid answers are returned, including short ones such as `[]` or `{}`
4. An invalid answer under 500 chars, or a cached answer with no JSON at all, is evicted and asked once more
5. A truncated answer (starts as JSON, error at its end) is evicted; over 100K chars the log says to split the input
6. Repair (`_try_repair_json`: merge concatenated objects, fix invalid escapes)
7. Otherwise evict, log `OBSERV grok.json_failed`, raise `GrokParseError` (a `GrokAPIError`)

Details: [JSON_REPAIR.md](JSON_REPAIR.md), [CACHE_AUTO_RECOVERY.md](CACHE_AUTO_RECOVERY.md).

## Prompt patterns

- **Windows.** All entity discovery prompts (dates, places, people, people_groups, equipment, weather, logistics, casualties) see a window of 3 paragraphs of the raw parsed text (`src/extraction/windows.py`); `event_name` / `sub_event_summary` are empty for them. Only `events` is one request per chapter.
- **Quotes are provenance.** Every answer item carries the verbatim quote it came from; `windows.settle` checks it against the source and drops unsupported items. The quote wording lives in the shared `{quote_rule}`.
- **Anti-hallucination.** Prompts say to extract only what the text states and not to copy the schema's example values; ids are not the model's to invent (EventID and Sub-eventID come from `deterministic_ids`, see [ULID_IMPLEMENTATION.md](ULID_IMPLEMENTATION.md)).
- **Temperature.** 0.1 for extraction. Lookup and yes/no verification calls pass 0.0 explicitly; grep the call site for `temperature=`.

## Cache

The cache key is a hash of the system prompt, the prompt, the temperature and the model, so editing a prompt YAML changes the key and the old entries are simply not hit. One sub-cache per `cache_type`; book-bound kinds are scoped `books/<book>/<kind>`. Local mode keeps them under `cache/api/`; AWS mode uses the DynamoDB cache table (`{env}-wwii-api-cache`). `GROK_CACHE_DISABLED=1` switches all cache reads and writes off. Clear one entry with `GrokClient.clear_cache_entry(...)`; locally you can delete `cache/api/books/<book>/<kind>/` for a whole kind.

## Modifying prompts

1. Find the prompt in [PROMPT_REFERENCE.md](../PROMPT_REFERENCE.md) and edit `prompts/<name>.yaml`.
2. Keep the placeholders the calling code supplies (the Context vars column). A removed placeholder is silently left out; an added unknown one is left as literal text.
3. Run the tests that render it (`tests/unit/`, see [TESTING.md](TESTING.md)).
4. To use the change in AWS without a rebuild, upload the file to `s3://<bucket>/prompts/<name>.yaml`; `deploy_all.sh` validates that every prompt file exists.
5. Run `bash scripts/gate.sh` before committing.

## Known issues

1. `reference_type` (endnote vs footnote) is still the model's call when note text is present; the note text itself is fetched and passed in (see [ENDNOTE_HANDLING.md](../features/supplemental/ENDNOTE_HANDLING.md)).
2. A few Phase 3 helpers still keep prompt text inline instead of in `prompts/`.
