# Dedup Process Analysis: Recurring Duplicates & Name Hallucination

**Date:** 2026-05-23  
**Issues:**
1. Duplicates reappear after being reviewed/resolved
2. Grok hallucinates full names instead of using source text verbatim

---

## Issue 1: Grok Hallucinating Full Names

### How It Happens

The people extraction prompt (`prompts/people.yaml`) provides a schema example with a fully-qualified biographical entry:

```json
"name": "Dwight D. Eisenhower",
"biographical_profile": {
    "birth_date": "1890-10-14",
    "birth_place": "Denison, Texas, USA",
    ...
}
```

This signals to Grok: "identify who this person is and return their canonical full name." When the source text says `"General Collins ordered the advance"`, Grok returns `"J. Lawton Collins"` — a name that never appears in the source document.

The batch_parallel path (`extract_people_batch_async`) instructs:
```
name: Full name WITHOUT rank/title
```

But "Full name" still encourages Grok to expand partial references into complete names from its training data.

### Consequences

- Same person mentioned as "Patton" in one chapter and "General Patton" in another gets returned as `"George S. Patton, Jr."` and `"George Patton"` — two different index keys, two files
- German names get "corrected": source says "Dollman" → Grok returns "Friedrich Dollmann"
- Inconsistent middle initials: `"Omar N. Bradley"` vs `"Omar Bradley"`
- Each variant creates a separate file that the dedup script must then reconcile

---

## Issue 2: Previously-Resolved Duplicates Reappearing

### Cause A: Exclusion Keys Include ULIDs (Critical)

Exclusions are stored as filename pairs:
```
exclusion#people#George_S_Patton_Jr_01ABCDEF.json#George_Patton_01XYZABC.json
```

When a merge happens, the old file is deleted. On the next pipeline run, Grok re-extracts the same person and creates a new file with a **new ULID**: `George_S_Patton_Jr_01NEWULID.json`. The exclusion no longer matches because the ULID changed.

**Every merge or re-extraction invalidates the exclusion.**

### Cause B: `.processed_events.json` Uses Absolute Paths (High)

The "already processed" registry in `people.py` stores:
```python
processed[str(event_file.resolve())] = {...}
```

In AWS mode, the workdir is `/tmp/pipeline/output/content/...` — different every ECS task run. The check never matches, so ALL people are re-extracted from ALL sub-events on every run.

The index deduplicates by normalized name, but only if Grok returns the exact same name string. Any variation creates a new file.

### Cause C: Index Normalization Is Too Weak (Medium)

`_normalize_name()` is just `name.strip().lower()`. No punctuation normalization, no fuzzy matching:

- `"george s. patton, jr."` ≠ `"george s. patton jr."` (comma)
- `"friedrich dollmann"` ≠ `"friedrich dollman"` (double-n)
- `"j. lawton collins"` ≠ `"lawton collins"` (initial)

Each creates a separate file and a new duplicate pair.

### Cause D: Dedup Runs Against Full Corpus Every Time (Low)

The dedup script loads ALL people files and scores ALL pairs (minus exclusions). It doesn't track "these pairs were already shown to the user and they took no action." Only explicit "Not Duplicates" decisions are excluded. If you reviewed a pair but didn't click either merge or exclude, it reappears next run.

---

## Recommendation

### Fix Priority 1: Anchor Names to Source Text

Change the prompt to require the name as it appears in the source, with a separate `identified_as` field for Grok's best guess at the full identity:

```yaml
prompt_header: |
  Extract ALL people mentioned. For each person:
  - name: The name EXACTLY as written in the source text (without rank prefix)
  - identified_as: Your best guess at their full canonical name (for matching)
  - original_text: The sentence containing the mention
```

Then use `identified_as` for index matching (so "Collins" and "J. Lawton Collins" map to the same file), but store `name` as the source-faithful reference. This gives you both: stable matching AND source fidelity.

The index key becomes `normalize(identified_as)` instead of `normalize(name)`. The `name` field preserves what the document actually says.

### Fix Priority 2: Use Name-Based Exclusions, Not Filename-Based

Change the exclusion key from filenames (which include ULIDs) to normalized name pairs:

```
exclusion#people#george s. patton, jr.#george patton
```

This survives file recreation, merges, and re-extraction. The `ExclusionStore` already has the infrastructure — it just needs to key on `normalize_name(person["name"])` instead of the filename.

**Migration:** One-time script to read existing exclusions, look up the names from the files, and re-write as name-based keys.

### Fix Priority 3: Fix the Processed Events Registry

Replace absolute paths with relative paths (or just the event file's basename):

```python
# Before:
processed[str(event_file.resolve())] = {...}

# After:
processed[event_file.name] = {...}
```

This makes the "already processed" check work across ECS runs. Combined with the index-based dedup, this prevents re-extraction of people from chapters that haven't changed.

### Fix Priority 4: Strengthen Index Normalization

Replace the simple `strip().lower()` with a normalization that handles common variations:

```python
def normalize_name(name: str) -> str:
    """Normalize for index matching."""
    name = name.strip().lower()
    name = name.replace(",", "")       # "patton, jr." → "patton jr."
    name = re.sub(r"\s+", " ", name)   # collapse whitespace
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return name
```

This collapses punctuation and Unicode variants into a single key, reducing false-new-file creation.

### Fix Priority 5: Track "Reviewed But No Action" State

Add a `"reviewed_at"` timestamp to duplicate report entries. The dedup UI marks pairs as "seen" even without a merge/exclude decision. On subsequent runs, pairs that were reviewed within the last N days are suppressed from the report (or shown in a separate "previously reviewed" section).

---

## Implementation Order

| # | Fix | Effort | Impact on Recurring Duplicates |
|---|-----|--------|-------------------------------|
| 1 | Name-based exclusions | 2-3 hours | Eliminates ~70% of reappearances |
| 2 | Fix processed events registry | 30 minutes | Prevents re-extraction in AWS |
| 3 | Strengthen index normalization | 1 hour | Prevents punctuation-variant duplicates |
| 4 | Anchor names to source text + `identified_as` | 3-4 hours | Eliminates hallucination-caused duplicates |
| 5 | Track reviewed state | 2 hours | Eliminates "no action taken" reappearances |

Fixes 1-3 are low-risk, backward-compatible changes. Fix 4 requires a prompt change and schema update (existing files would need a backfill or would naturally update on next extraction). Fix 5 is a UI enhancement.
