# JSON Repair and Sanitization

## Overview

The Grok API client (`src/grok_client.py`) includes multi-layer JSON repair to handle malformed API responses. The pipeline is: strip markdown wrapper → sanitize → parse → repair if needed.

## Pipeline

### 1. `_strip_markdown_wrapper`

Removes `` ```json `` / `` ``` `` code block wrappers that the API sometimes wraps around JSON responses.

### 2. `_sanitize_json_response` — State Machine Sanitizer

A **character-by-character state machine** that walks the response tracking whether it is inside a JSON string or not. This is critical because escape sequences are only meaningful inside strings.

**Outside strings:** characters pass through unchanged; a `"` transitions to the in-string state.

**Inside strings:**
- Unescaped `"` ends the string
- Valid escape sequences (`\"`, `\\`, `\/`, `\b`, `\f`, `\n`, `\r`, `\t`, `\uXXXX`) pass through
- **Invalid escapes** (e.g., `\x`, `\a`) — the backslash is silently dropped, keeping only the next character
- Literal control characters (`\t`, `\n`, `\r`) are replaced with their JSON escape equivalents

Non-whitespace control characters (`\x00`–`\x1f` excluding tab/newline/CR) are stripped in a pre-pass before the state machine runs.

### 3. `_try_repair_json` — Multi-Strategy Repair

Called when `json.loads()` fails with an "Invalid escape" error after sanitization. Tries four strategies in order, returning the first that parses:

| # | Strategy | What it does |
|---|----------|-------------|
| 1 | **Double-escape** | `\x` → `\\x` for any backslash not followed by a valid JSON escape character |
| 2 | **Remove invalid escapes** | `\x` → `x` — strips the backslash, keeps the character |
| 3 | **Bracket fix** | `\[` → `[`, `\]` → `]` — removes escaped brackets |
| 4 | **Nuclear** | Strips all backslashes not followed by valid JSON escape characters |

If all four strategies fail, the method returns `None` and the caller falls through to truncation/error handling with automatic cache clearing.

## Other Error Handling

- **Short responses** (<500 chars): auto-cleared from cache and retried
- **Truncated responses**: cache cleared; large responses (>100K chars) flagged as likely hitting `max_tokens`
- **Extra data / concatenated JSON**: cache cleared, fresh API call on retry
- **Corrupted cache entries**: auto-cleared on any unrecoverable `JSONDecodeError`

## Related

- [Error Handling Guide](error_handling.md)
- [Prompt Management](PROMPT_MANAGEMENT.md)
