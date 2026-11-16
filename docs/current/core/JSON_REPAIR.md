# JSON Repair and Sanitization

## Overview

The Grok API client (`src/grok_client.py`) includes multi-layer JSON repair to handle malformed API responses. The pipeline is: extract the JSON payload → sanitize → parse → repair if needed.

## Pipeline

### 1. `_strip_markdown_wrapper`

Now a thin wrapper over the module function `json_payload` (`src/grok_client.py`), the one rule every reader uses. It returns, in order: the text as is when it parses or starts with `{` / `[` (a cut-short answer stays whole for the truncation handling); else the first fenced `` ```json `` block; else the first JSON value in the text (an object before a non-empty list); else everything from the first `{`. So prose such as `**JSON Response:** {...}` is unwrapped instead of being treated as a poisoned answer. Integers written with leading zeros (`[052]`) are fixed first (`_strip_leading_zeros`).

### 2. `_sanitize_json_response` — State Machine Sanitizer

A **character-by-character state machine** that walks the response tracking whether it is inside a JSON string or not. This is critical because escape sequences are only meaningful inside strings.

**Outside strings:** characters pass through unchanged; a `"` transitions to the in-string state.

**Inside strings:**
- Unescaped `"` ends the string
- Valid escape sequences (`\"`, `\\`, `\/`, `\b`, `\f`, `\n`, `\r`, `\t`, `\uXXXX`) pass through
- **Invalid escapes** (e.g., `\x`, `\a`) — the backslash is silently dropped, keeping only the next character
- Literal control characters (`\t`, `\n`, `\r`) are replaced with their JSON escape equivalents

Non-whitespace control characters (`\x00`–`\x1f` excluding tab/newline/CR) are stripped in a pre-pass (`src.utils.batch_api.strip_control_chars`, shared with the batch path) before the state machine runs.

### 3. `_try_repair_json` — Multi-Strategy Repair

Called when `json.loads()` fails after sanitization. Strategy 0 handles `Extra data` errors; strategies 1-4 run only for an "Invalid ... escape" error and are tried in order, returning the first that parses:

| # | Strategy | What it does |
|---|----------|-------------|
| 0 | **Merge concatenated objects** | `{"people":[..]}{"people":[..]}` (one object per sub-event instead of one document) is decoded object by object and list-valued keys are merged (`_merge_concatenated_json`) |
| 1 | **Double-escape** | `\x` → `\\x` for any backslash not followed by a valid JSON escape character |
| 2 | **Remove invalid escapes** | `\x` → `x` — strips the backslash, keeps the character |
| 3 | **Bracket fix** | `\[` → `[`, `\]` → `]` — removes escaped brackets |
| 4 | **Nuclear** | Strips all backslashes not followed by valid JSON escape characters |

If every strategy fails, the method returns `None`; the caller (`_handle_json_decode_error`) evicts the cache entry, emits an `OBSERV grok.json_failed` line and raises `GrokParseError` (a `GrokAPIError`).

## Other Error Handling

Order inside `_handle_json_decode_error`:

- **Short responses** (<500 chars, first attempt): cache entry evicted and retried once.
- **Truncated responses** (starts as JSON and the parse error is at its end, or a string is unterminated; `_cut_short`): the cache entry is always evicted; a response over 100K chars is logged as likely hitting `max_tokens`. An error in the middle of the document is malformed JSON, not truncation (re-asking returns the same answer), and goes to repair.
- **Extra data / concatenated JSON**: merged by strategy 0 above.
- **Unrecoverable `JSONDecodeError`**: entry evicted, `OBSERV grok.json_failed` logged, `GrokParseError` raised.

See [CACHE_AUTO_RECOVERY.md](CACHE_AUTO_RECOVERY.md).

## Related

- [Cache auto-recovery](CACHE_AUTO_RECOVERY.md)
- [Prompt Management](PROMPT_MANAGEMENT.md)
