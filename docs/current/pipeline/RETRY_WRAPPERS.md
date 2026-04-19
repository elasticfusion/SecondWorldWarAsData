# Automatic Retry Wrappers

**Status:** Production Ready  
**Version:** 1.1  
**Date:** 2026-03-13

---

## Overview

Automatic retry wrappers for Phase 2 and Phase 3 that handle transient errors (API timeouts, network issues, JSON parsing errors, etc.) without manual intervention.

---

## Phase 2 Retry Wrapper

### phase2_retry.py

Automatically retries Phase 2 extraction until all files are processed or maximum attempts reached.

**Usage:**
```bash
# Recommended: Use retry wrapper (handles transient errors)
python3 phase2_retry.py

# Custom attempts
python3 phase2_retry.py --max-attempts 5

# With debug logging
python3 phase2_retry.py --log-level DEBUG

# Direct (single pass, no retry)
python3 phase2_extract.py
```

**Features:**
- Runs phase2_extract.py multiple times automatically
- Checks for missing event files after each run
- Stops early if all files processed successfully
- Configurable maximum attempts (default: 3)
- Passes through log level settings
- **New:** Handles JSON parsing errors with automatic cache clearing suggestions

**How It Works:**
1. Run phase2_extract.py
2. Count missing event files
3. If missing files found → retry
4. If all complete → stop early
5. Maximum attempts → give up and report

**Benefits:**
- ✅ Handles API timeouts automatically
- ✅ Handles network errors automatically
- ✅ Handles transient Grok API issues
- ✅ Handles JSON parsing errors (control characters, invalid escapes)
- ✅ Provides file-specific cache clearing commands on errors
- ✅ Distinguishes transient errors from token limit issues
- ✅ No manual re-running needed
- ✅ Logs show progress across attempts

**Recent Improvements (2026-03-13):**
- JSON response sanitization (removes control characters, fixes escape sequences)
- Input size validation (warns when >100K tokens)
- Smart truncation detection (distinguishes API errors from token limits)
- File-specific cache clearing commands in error messages

---

## Phase 3 Retry Wrapper

### phase3_retry.py

Automatically retries Phase 3 enrichment until all people are enriched or maximum attempts reached.

**Usage:**
```bash
# Recommended: Use retry wrapper (handles transient errors)
python3 phase3_retry.py

# Custom attempts
python3 phase3_retry.py --max-attempts 5

# Limit for testing
python3 phase3_retry.py --max-items 10

# With debug logging
python3 phase3_retry.py --log-level DEBUG

# All options
python3 phase3_retry.py --max-attempts 5 --max-items 10 --no-references

# Direct (single pass, no retry)
python3 phase3_enrich_data.py
```

**Features:**
- Runs phase3_enrich_data.py multiple times automatically
- Checks for unenriched people after each run
- Stops early if all people enriched successfully
- Configurable maximum attempts (default: 3)
- Passes through all phase3 options

**How It Works:**
1. Run phase3_enrich_data.py
2. Count unenriched people
3. If unenriched found → retry
4. If all enriched → stop early
5. Maximum attempts → give up and report

**Benefits:**
- ✅ Handles Wikipedia API timeouts automatically
- ✅ Handles Grokipedia errors automatically
- ✅ Handles network issues automatically
- ✅ No manual re-running needed
- ✅ Logs show progress across attempts

---

## Common Options

### --max-attempts

Maximum number of complete passes through the pipeline.

**Default:** 3

**Example:**
```bash
python3 phase2_retry.py --max-attempts 5
python3 phase3_retry.py --max-attempts 5
```

### --log-level

Set logging verbosity (passed through to underlying script).

**Options:** TRACE, DEBUG, INFO, WARN, ERROR, FATAL (phase2) or DEBUG, INFO, WARNING, ERROR (phase3)

**Example:**
```bash
python3 phase2_retry.py --log-level DEBUG
python3 phase3_retry.py --log-level DEBUG
```

---

## Implementation Details

### Detection Logic

**Phase 2:**
- Counts parsed files without corresponding event files
- Missing event file = incomplete extraction

**Phase 3:**
- Counts people files without enrichment_data field
- Missing enrichment_data = incomplete enrichment

### Early Stopping

Both wrappers stop early if:
- All files/people are complete
- No errors occurred
- Saves time and API calls

### Exit Codes

- **0** - Success (all complete)
- **1** - Failure (max attempts reached with incomplete data)

---

## Troubleshooting

### Issue: Wrapper reports success but data incomplete

**Solution:**
- Check logs for warnings
- Verify detection logic matches your data structure
- Run direct script with --log-level DEBUG

### Issue: Maximum attempts reached

**Solution:**
- Increase --max-attempts
- Check for persistent errors in logs
- Verify API keys and network connectivity
- Check rate limiting

### Issue: Wrapper doesn't detect completion

**Solution:**
- Verify file structure matches expected format
- Check for permission issues
- Ensure output directory is correct

---

## Best Practices

1. **Use retry wrappers by default** - They handle transient errors automatically
2. **Start with default attempts (3)** - Usually sufficient for transient errors
3. **Use --max-items for testing** - Limit scope when testing phase3
4. **Monitor logs** - Check for patterns in errors
5. **Use direct scripts for debugging** - Easier to see single-pass behavior

---

## Related Documentation

- **Phase 2 Pipeline:** `docs/current/core/PIPELINE.md`
- **Phase 3 Enrichment:** `docs/current/PHASE3_COMPLETE.md`
- **Error Handling:** `docs/current/core/error_handling.md`
- **Configuration:** `docs/current/core/CONFIGURATION.md`

---

**Last Updated:** 2026-03-13  
**Status:** ✅ Production Ready
