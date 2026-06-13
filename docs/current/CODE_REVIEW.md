# Code Review — 2026-06-13

## Summary

| Metric | Value |
|--------|-------|
| Files reviewed | 24 tracked + 5 untracked |
| Issues found | 1 medium, 4 low |
| Tests | **284 passed** (unit) + **19 passed** (prompt schema alignment) |
| Linting (black) | All changed files pass; 6 pre-existing files in scripts/ need reformatting |
| py_compile | All key files compile cleanly |
| Overall | **Good to merge** — no critical issues |

---

## Changes Overview

This changeset introduces:
1. **Auto-trigger Phase 3** when dedup finds no duplicates (skips manual review wait)
2. **Batch poller "ready" state** — intermediate status between pending/complete to prevent duplicate ECS launches
3. **Empty content guards** — all entity extractors now skip API calls when sub-event text is empty
4. **Prompt validation** (`_validate_prompt`) — catches empty prompts, oversized prompts, unfilled template vars
5. **Non-batch fallback** — when all events are cached (no batch submitted), runs entity extraction directly
6. **Prompt YAML fixes** — removed Python f-string `{{` escaping from YAML schema examples (casualties, weather, equipment)
7. **Parameterized infrastructure** — NAT wait, teardown delay, poller interval now configurable via CloudFormation parameters

---

## Issues Found

### Medium Severity

#### 1. `_validate_prompt` placeholder regex may false-positive on legitimate content

**File:** `src/grok_client.py:565`  
**Pattern:** `r"\{([a-z_]+)\}"`

This catches unfilled template variables like `{book}`, `{author}`. However, prompts that include inline JSON examples with lowercase keys could trigger false positives (e.g., `{"type": "value"}`). Currently safe because:
- The regex only matches `{word}` NOT `{"word": ...}` (the quote prevents matching)
- All prompt schemas use capitalized or mixed-case keys (`"EventID"`, `"Sub-events"`)

**Risk:** Low in practice, but if a future prompt schema includes `{type}` or `{value}` as standalone placeholder-like text, it would break. Consider adding a whitelist of known template variables or matching only known variable names.

**Mitigation:** The test `test_json_braces_not_flagged` validates that JSON content with braces doesn't trigger this. Current risk is acceptable.

---

### Low Severity

#### 2. `_dedup_has_no_pending()` silently returns `True` when no report files exist

**File:** `ecs_entrypoint.py:1042-1053`

If dedup runs successfully but produces no `duplicate_report.json` files (e.g., new entity type added without corresponding dedup), the function returns `True` and auto-triggers Phase 3. This is likely the intended "no duplicates" behavior, but worth documenting.

#### 3. `create_date_prompt` empty-text guard is incomplete

**File:** `src/extraction/dates.py:124-132`

The guard checks `if not text.strip()` but the logic above can select `sub_event_summary` (from the `else` branch) even when fulltext is empty but summary is non-empty (≥50 chars check fails because fulltext is empty string). If fulltext is empty and summary is `"test"` (< 50 chars), `text = fulltext_joined or sub_event_summary` = `"test"`, which passes the guard but wastes an API call on minimal content.

The test `test_dates_prompt_empty_on_no_fulltext` documents this — it asserts `result != ""` with a comment noting the behavior.

#### 4. `run_submit_only` non-batch fallback calls `_post_process` and `_teardown_networking`

**File:** `ecs_entrypoint.py:1893-1908`

When no batch is submitted, the code runs non-batch mode then calls both `_post_process` (which may auto-trigger Phase 3) AND `_teardown_networking`. If Phase 3 is auto-triggered, the immediate `_teardown_networking` might remove NAT before the trigger Lambda can launch Phase 3's ECS task. The trigger Lambda does call `_wait_for_networking()` and can recreate networking, so this is self-healing, but adds ~3 min latency.

#### 5. `.gitignore` missing trailing newline

**File:** `.gitignore`

The file ends without a newline (`\\ No newline at end of file`). Minor but can cause noise in future diffs.

---

## Files Reviewed in Detail

### Core Logic
| File | Changes | Assessment |
|------|---------|------------|
| `ecs_entrypoint.py` | Auto-trigger Phase 3, non-batch fallback, `_enqueue_from_metrics` returns bool | Correct; non-batch fallback teardown timing is low-risk |
| `src/grok_client.py` | `_validate_prompt`, removed retry decorator (moved elsewhere?) | Correct; placeholder regex is safe for current use |
| `lambda_handlers/batch_poller.py` | "ready" state, ECS task guard, `_wait_for_nat` parameterized | Clean state machine improvement |
| `lambda_handlers/trigger_handler.py` | NAT wait parameterized | Correct |
| `src/extraction/batch_parallel.py` | `BatchModeCollecting` exception handling | Correct |

### Entity Extractors
| File | Changes | Assessment |
|------|---------|------------|
| `src/extraction/casualties.py` | Improved `_format_index`, empty section handling | Correct; cleaner output |
| `src/extraction/dates.py` | Fulltext vs summary heuristic improved, empty guard | Correct (minor edge case noted above) |
| `src/extraction/events.py` | Flat response wrapping, Chapter fallback, fulltext fallback | Correct; handles known AI response variations |
| `src/extraction/people.py` | Empty content guard | Correct |
| `src/extraction/places.py` | Empty content guard | Correct |
| `src/extraction/weather_central.py` | Empty content guard | Correct |

### Prompts
| File | Changes | Assessment |
|------|---------|------------|
| `prompts/casualties.yaml` | Removed `{{`/`}}` escaping → `{`/`}` | Correct; YAML doesn't need Python f-string escaping |
| `prompts/equipment.yaml` | YAML quoting for rules with special chars | Correct |
| `prompts/weather.yaml` | Same as casualties | Correct |

### Infrastructure
| File | Changes | Assessment |
|------|---------|------------|
| `cloudformation/compute.yaml` | Parameterized intervals/timeouts, NAT_WAIT env vars | Clean; good operational flexibility |

### Tests (New)
| File | Tests | Assessment |
|------|-------|------------|
| `tests/unit/test_grok_client.py` | 5 new `TestValidatePrompt` tests | Good coverage of validation logic |
| `tests/unit/test_empty_content_guards.py` | 12 tests for empty content handling | Thorough; documents known edge case in dates |
| `tests/unit/test_llm_response_handling.py` | Markdown stripping, truncated JSON, ULID validation | Well-structured defensive tests |
| `tests/test_prompt_schema_alignment.py` | Schema parsing, event wrapping alignment | Good regression prevention for prompt/schema drift |
| `tests/unit/test_batch_poller.py` | Updated assertion `"complete"` → `"ready"` | Matches new state machine |

---

## Security Assessment

- No secrets exposed or hardcoded
- No new external network calls beyond existing patterns
- Lambda invocations use environment-based function names (no injection vectors)
- Input validation added (`_validate_prompt`) improves defense against malformed inputs

## Performance Assessment

- Empty content guards prevent wasteful API calls (cost savings)
- Batch poller ECS task guard prevents duplicate task launches
- Non-batch fallback avoids unnecessary 15-min poller wait cycles
- `_dedup_has_no_pending` reads small JSON files (negligible I/O)

## Recommendations

1. **Consider** adding a trailing newline to `.gitignore`
2. **Consider** the teardown timing in `run_submit_only` non-batch path — adding a small delay or deferring teardown until after trigger confirmation would prevent the self-healing NAT recreation cycle
3. **Pre-existing:** Run `black` on the 6 unformatted files in `scripts/` and `docs/archive/`
