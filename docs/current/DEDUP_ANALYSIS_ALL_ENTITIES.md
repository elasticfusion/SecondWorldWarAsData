# Dedup Analysis: Groups, Places & Equipment

**Date:** 2026-05-23  
**Issues:** Same as people — recurring duplicates after review, name hallucination from Grok

---

## Groups (People Groups)

### How Names Are Extracted

The batch prompt asks:
```
"name": "Unit Name"
```

No instruction to use the name verbatim from source text. Grok normalizes freely:
- Source: "the 4th Division" → Grok returns: "4th Infantry Division" (adds branch)
- Source: "Gruppen von Schwerin" → Grok returns: "Kampfgruppe von Schwerin" (translates/corrects)
- Source: "CCA" → Grok returns: "Combat Command A, 2nd Armored Division" (expands abbreviation)

### Index Key

```python
make_key=lambda obj: _normalize_name(obj.get("name", ""))
```

Where `_normalize_name` is just `name.strip().lower()`. So `"4th infantry division"` and `"4th division"` create separate files.

### Dedup Detection

`find_duplicate_groups.py` uses:
- `SequenceMatcher` ratio (threshold ~0.7)
- Substring matching (`"4th division"` in `"4th infantry division"`)
- Exclusions loaded from `get_exclusion_store("groups", groups_dir)`

### Problems

| Problem | Mechanism | Example |
|---------|-----------|---------|
| Abbreviation expansion | Grok expands "CCA" to full name | `cca` ≠ `combat command a, 2nd armored division` |
| Branch insertion | Grok adds branch to numbered units | `4th division` ≠ `4th infantry division` |
| Nationality prefix variation | Grok inconsistently adds country | `2nd armored division` ≠ `us 2nd armored division` |
| Exclusion invalidation | Same as people — filename-based with ULIDs | Merged/recreated files get new ULIDs |

### Recommendation

1. **Add `source_name` field** — store the name exactly as it appears in text; use `identified_as` for Grok's expanded version
2. **Normalize index key more aggressively** — strip "the", "u.s.", "us", ordinal suffixes, and branch names before keying:
   ```python
   def normalize_group_key(name):
       name = name.lower().strip()
       name = re.sub(r"^(the|u\.?s\.?|us)\s+", "", name)
       name = re.sub(r"\s*(infantry|armored|airborne|panzer)\s*", " ", name)
       return " ".join(name.split())
   ```
3. **Switch exclusions to name-based keys** (same fix as people)

---

## Places

### How Names Are Extracted

The batch prompt asks:
```
"name": "Name"
```

The places prompt (`prompts/places.yaml`) uses `"current_name": "Normandy"` in its schema example. Grok tends to:
- Modernize names: source says "Coutances" → Grok returns "Coutances" (fine) but source says "Cherbourg" → Grok sometimes returns "Cherbourg-en-Cotentin" (modern name)
- Translate: source says "Fluss Mosel" → Grok returns "Moselle River"
- Expand: source says "the beach" → Grok returns "Omaha Beach" (infers from context)

### Index Key

```python
make_key=lambda obj: obj.get("name", "").lower().replace(" ", "_")
```

This is slightly better than people (replaces spaces with underscores) but still exact-match. `"cherbourg"` ≠ `"cherbourg-en-cotentin"`.

### Dedup Detection

`find_duplicate_places_v2.py` uses:
- Name similarity (SequenceMatcher, threshold 0.65)
- Geographic distance (haversine, max 20km)
- Combined: nearby (< 5km) + partial name match (0.4) is enough
- Exclusions via `get_exclusion_store("places", places_dir)`

The geographic distance check is a strong signal — two places with the same name but > 20km apart are correctly excluded. But it fails when coordinates are missing (index-only entries from other books have `lat: None`).

### Problems

| Problem | Mechanism | Example |
|---------|-----------|---------|
| Modern vs historical names | Grok uses current name | `cherbourg` ≠ `cherbourg-en-cotentin` |
| Translation | Grok translates to English | `fluss mosel` ≠ `moselle river` |
| Context expansion | Grok infers specific place from generic reference | `the beach` → `omaha beach` |
| Missing coordinates | Index-only entries can't use distance check | Falls back to name-only matching |
| Exclusion invalidation | Filename-based with ULIDs | Same as people |

### Recommendation

1. **Require `original_text` in the index key** — the prompt already asks for `original_text`. Use it as a secondary dedup signal: if two places have the same `original_text`, they're the same mention regardless of what Grok named them
2. **Normalize place names more aggressively**:
   ```python
   def normalize_place_key(name):
       name = name.lower().strip()
       # Remove common geographic suffixes
       for suffix in [" river", " creek", " beach", " hill", " mountain", " forest"]:
           if name.endswith(suffix):
               name = name[:-len(suffix)]
       name = name.replace("-", " ").replace("_", " ")
       return unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
   ```
3. **Propagate coordinates to index-only entries** — when a place is first created with coordinates, store them in the index so cross-book dedup can use distance
4. **Switch exclusions to name-based keys**

---

## Equipment

### How Names Are Extracted

The prompt (`prompts/equipment.yaml`) asks for:
```
"common_name": "M4 Sherman",
"technical_identifier": "M4A1"
```

Grok tends to:
- Normalize designations: source says "Sherman tank" → Grok returns `"M4 Sherman"` (adds designation)
- Expand abbreviations: source says "88" → Grok returns `"88mm Flak 36/37"` (full name)
- Vary naming: `"Panther"` vs `"Panzerkampfwagen V Panther"` vs `"PzKpfw V"`

### Index Key

Equipment uses `common_name` as the primary key in the index. The batch extraction doesn't go through `batch_parallel.py` for equipment — it uses the sequential `_extract_equipment` path which calls `extract_equipment_from_event` directly.

### Dedup Detection

`find_duplicate_equipment.py` uses:
- Best name match across `common_name`, `technical_identifier`, and `alternate_names` (threshold 0.85 for high confidence, 0.7 for medium)
- Name containment (one name is substring of another, > 60% length ratio)
- Same category boost
- Exclusions via `get_exclusion_store("equipment", equipment_dir)`

### Problems

| Problem | Mechanism | Example |
|---------|-----------|---------|
| Designation expansion | Grok adds model numbers | `sherman` ≠ `m4 sherman` |
| Full vs common name | Grok inconsistently uses technical name | `panther` ≠ `panzerkampfwagen v panther` |
| Caliber format variation | Different formatting | `.50-caliber` ≠ `50 caliber` ≠ `12.7mm` |
| Variant confusion | Same base, different variant | `m4a1` vs `m4a3` (different tanks) |
| Exclusion invalidation | Filename-based with ULIDs | Same as people |

### Recommendation

1. **Use `technical_identifier` as primary index key when available** — it's more stable than `common_name`. Fall back to `common_name` only when no technical ID exists
2. **Build an equipment alias table** — map known equivalents:
   ```python
   EQUIPMENT_ALIASES = {
       "sherman": "m4 sherman",
       "panther": "pzkpfw v panther",
       "tiger": "pzkpfw vi tiger",
       "88": "88mm flak 36",
       ...
   }
   ```
3. **Normalize caliber formats** in the index key (the dedup script already does this, but extraction doesn't):
   ```python
   name = re.sub(r"^\.(\d)", r"\1", name)       # .50 → 50
   name = re.sub(r"(\d+)\s*-?\s*mm", r"\1mm", name)  # 155-mm → 155mm
   ```
4. **Switch exclusions to name-based keys**

---

## Cross-Cutting Issues (All Entity Types)

### The Exclusion Key Problem (Affects All)

All four entity types use the same `ExclusionStore` with filename-based keys:
```
exclusion#{entity_type}#{file1}#{file2}
```

Filenames include ULIDs that change on re-extraction. **This is the single biggest cause of recurring duplicates across all entity types.**

### The Index Normalization Gap (Affects All)

Each entity type has a different normalization function, but all are too weak:
- People: `strip().lower()`
- Groups: `strip().lower()`
- Places: `lower().replace(" ", "_")`
- Equipment: uses `common_name` directly in index

None handle punctuation, abbreviations, or Unicode normalization at the index level. The dedup scripts have stronger normalization, but by then it's too late — separate files already exist.

### The "Full Corpus Re-scan" Problem (Affects All)

All four dedup scripts load ALL files and score ALL pairs on every run. There's no concept of "only check new files against existing ones." This means:
- O(n²) comparisons every run
- Previously-reviewed pairs reappear if exclusion keys don't match
- No incremental dedup

---

## Unified Recommendation

### Priority 1: Name-Based Exclusions (All Types, 3-4 hours)

Change `ExclusionStore` to key on normalized names instead of filenames:

```python
def _make_pair_key(entity_type: str, name1: str, name2: str) -> str:
    a, b = sorted([normalize_name(name1), normalize_name(name2)])
    return f"exclusion#{entity_type}#{a}#{b}"
```

This survives file recreation, merges, and re-extraction. Requires a one-time migration of existing exclusions (read old filename-based keys, look up names, write new name-based keys).

### Priority 2: Stronger Index Normalization (All Types, 2-3 hours)

Create entity-specific normalization functions that collapse known variations at extraction time, preventing duplicate file creation:

| Entity | Normalize |
|--------|-----------|
| People | Strip punctuation, collapse initials, ASCII-fold |
| Groups | Strip "the/us/u.s.", remove branch names, collapse ordinals |
| Places | ASCII-fold, strip geographic suffixes, normalize hyphens |
| Equipment | Normalize caliber formats, apply alias table, prefer technical ID |

### Priority 3: Source-Anchored Names (All Types, 4-5 hours)

Add `source_name` (verbatim from text) and `identified_as` (Grok's canonical name) to all entity types. Use `identified_as` for index matching, store `source_name` for provenance.

### Priority 4: Incremental Dedup (All Types, 3-4 hours)

Track which files are new since last dedup run. Only score new files against the full corpus, not all-vs-all. Store `last_dedup_run` timestamp; files with `mtime > last_dedup_run` are "new."

---

## Summary

| Fix | Effort | Entities | Impact |
|-----|--------|----------|--------|
| Name-based exclusions | 3-4h | All | Eliminates ~70% of reappearances |
| Stronger index normalization | 2-3h | All | Prevents 30-50% of duplicate file creation |
| Source-anchored names | 4-5h | All | Eliminates hallucination-caused duplicates |
| Incremental dedup | 3-4h | All | Reduces review fatigue, faster runs |
| Equipment alias table | 1h | Equipment | Handles known equivalents |
| Place coordinate propagation | 1h | Places | Enables distance-based dedup cross-book |
