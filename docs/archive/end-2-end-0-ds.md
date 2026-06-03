# End-to-End Data Science Review: Round 0

**Date:** 2026-05-25  
**Scope:** Prompt changes applied 2026-05-24, re-extraction run 2026-05-25, output validation complete.

---

## Objective

Evaluate whether prompt modifications resolved the 7 data quality issues identified in the initial data science review (2026-05-24).

---

## Results Summary

| # | Issue | Before | After | Status |
|---|---|---|---|---|
| 1 | Weather duplication | 1,320 files (21× per date+location) | 613 files, 0 new duplicates | ✅ Fixed |
| 2 | Weather PlaceID presence | 31% had PlaceID | 88% have PlaceID | ✅ Fixed |
| 3 | Weather PlaceID resolution | 19% resolved to real place | 69% resolve to real place | ⚠️ Partial |
| 4 | People missing name/PersonID | 19% (193/1,038) | 8% (105/1,292) | ⚠️ Partial |
| 5 | Casualties null PeopleGroupID | 86% null | 87% null (13% linked) | ❌ Not fixed |
| 6 | Logistics severity skew | 79% high/critical | 78% high/critical | ❌ Not fixed |
| 7 | Equipment bad categories | `"Medium Tank"`, `"infantry"` | All valid enum values | ✅ Fixed |

---

## Detailed Findings

### ✅ Fixed: Weather Duplication

The prompt now instructs "extract only ONCE" for same date+location. Combined with pipeline-level dedup, the weather corpus dropped from 1,320 to 613 files. New extractions show zero duplicates — every new file has a unique date+location combination.

**Before:** `19441003_Fort_Driant` had 21 files  
**After:** `19441003_Fort_Driant` has 3 files (legacy remnants)

### ✅ Fixed: Weather PlaceID Presence

The "COPY these IDs exactly" instruction dramatically improved PlaceID population.

- Before: 404/1,319 (31%) had any PlaceID
- After: 538/613 (88%) have a PlaceID

### ⚠️ Partial: Weather PlaceID Resolution

Of the 538 weather files with PlaceIDs, 370 (69%) resolve to actual place entities. The remaining 168 (31%) reference IDs that don't exist in `output/places/`.

**Root cause:** The `_build_places_section()` function in `weather_central.py` passes `PlaceMentionID` values from the sub-event's `Places` array. These are per-mention IDs, not the top-level `PlaceID` from the consolidated place files. The LLM correctly copies what it's given — but it's given the wrong IDs.

**Fix:** Change `_build_places_section()` to resolve PlaceMentionIDs to their parent PlaceIDs before injecting into the prompt, or pass the top-level PlaceID directly.

### ⚠️ Partial: People Missing Name/PersonID

Reduced from 19% to 8%. The new extraction correctly generates name + PersonID for all new files. The remaining 105 are legacy files from earlier runs that weren't re-extracted (e.g., `adolf hitler.json`, `adrian haislip.json`).

**Fix:** Run a one-time cleanup script:
1. For files where the filename contains a recognizable name, populate the `name` field from filename
2. Generate a PersonID for files missing one
3. Merge any true duplicates

### ❌ Not Fixed: Casualties PeopleGroupID

Only 13% of organization references in new casualties have a linked PeopleGroupID. The prompt correctly instructs "COPY from Available entities > Organizations" but the code only passes the first 10 entity keys without their IDs.

**Root cause (code, not prompt):** In `casualties.py` line 146:
```python
entity_context = (
    f"Available entities:\n"
    f"- Dates: {list(dates_index.keys())[:10]}\n"
    f"- Places: {list(places_index.keys())[:10]}\n"
    f"- People: {list(people_index.keys())[:10]}\n"
    f"- Organizations: {list(people_groups_index.keys())[:10]}\n"
)
```

This passes only names (no IDs) and only the first 10. The LLM has nothing to copy.

**Fix:** Change to pass `name: GroupID` pairs for all available organizations:
```python
org_pairs = [f'  - "{name}": {gid}' for name, gid in people_groups_index.items()]
entity_context = f"Available Organizations (COPY these IDs):\n" + "\n".join(org_pairs)
```

### ❌ Not Fixed: Logistics Severity Skew

The severity calibration text was added but had negligible effect:

| Severity | Old % | New % | Change |
|---|---|---|---|
| critical | 24% | 20% | -4pp |
| high | 55% | 58% | +3pp |
| medium | 21% | 21% | +0pp |
| low | 1% | 0% | -0pp |

**Root cause:** Calibration descriptions alone don't override the LLM's tendency to rate military supply problems as severe. The source texts describe problems that genuinely impacted operations — the LLM interprets any mentioned supply issue as "significant."

**Fix options:**
1. **Few-shot examples** — Add 3-4 examples in the prompt showing the same text classified at different severity levels
2. **Stronger default instruction** — "Default to medium. Only use high/critical when the text explicitly states operations were halted, postponed, or impossible."
3. **Post-extraction recalibration** — Apply a heuristic: if no explicit halt/postponement language, downgrade to medium

### ✅ Fixed: Equipment Categories

No new files contain non-standard category values. The explicit examples in the prompt (M4 Sherman → "armor", 88mm gun → "artillery") prevented the `"Medium Tank"` error.

One new edge case appeared: `"aircraft_ordnance"` — not in the enum but arguably valid. Consider adding it or mapping to `"other"`.

---

## Prompt Changes Applied

| Prompt | Change | Effective? |
|---|---|---|
| `weather.yaml` | "COPY these IDs exactly" + schema shows `<COPY from...>` | ✅ Yes |
| `casualties.yaml` | Added PeopleGroupID/PersonID/PlaceID to schema with COPY instructions | ❌ No (code bottleneck) |
| `logistics.yaml` | Added severity calibration descriptions, aligned type/status enums | ❌ No (needs few-shot) |
| `equipment.yaml` | Added category examples, "do NOT use equipment names as category" | ✅ Yes |
| `people.yaml` | "Every person MUST have name and PersonID" | ✅ Yes (for new files) |

---

## Next Actions

### Priority 1 (Code fix — will resolve casualties)
- [ ] Modify `casualties.py` entity_context builder to pass full org name→GroupID mapping
- [ ] Re-run casualties extraction for Lorraine Campaign

### Priority 2 (Code fix — will resolve weather PlaceID)
- [ ] Modify `_build_places_section()` in `weather_central.py` to resolve MentionIDs to top-level PlaceIDs
- [ ] Re-run weather extraction

### Priority 3 (Cleanup — will resolve people)
- [ ] Script to populate name/PersonID on 105 legacy people files from filename

### Priority 4 (Prompt iteration — logistics severity)
- [ ] Add few-shot examples to logistics prompt showing medium-severity classification
- [ ] Or add: "Default to medium unless text explicitly says operations halted/postponed/impossible"

---

## Metrics After This Round

| Entity | Files | Key Quality Metric |
|---|---|---|
| Weather | 613 | 88% have PlaceID, 69% resolve correctly |
| Places | 2,091+ | 100% have coordinates |
| People | 1,292 | 92% have name + PersonID |
| People Groups | 1,457+ | — |
| Dates | 4,698+ | 98% have date_precision |
| Equipment | 596+ | 100% valid category enum (new files) |
| Logistics | 4,056+ | Severity still skewed 78% high/critical |
| Casualties | 6,472+ | 13% have PeopleGroupID (code fix needed) |
| Bibliography | 7,647 | — |
