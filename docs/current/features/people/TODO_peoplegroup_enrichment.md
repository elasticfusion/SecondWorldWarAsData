# TODO: People-to-PeopleGroup Enrichment

**Priority:** Medium  
**Status:** ✅ Done (2026-03-16)  
**Created:** 2026-03-02

---

## Summary

After Phase 3 biographical enrichment, each person's `units_served` is matched against `output/people_groups/index.json` (case-insensitive). Matching people groups get the person added as a member with role, dates, source tracking, and confidence score. Duplicates are skipped by PersonID.

## Implementation

Three functions in `src/extraction/enrich_biographies.py`:

- `_find_group_file()` — case-insensitive lookup in group index
- `_add_member_to_group()` — adds member entry, skips if PersonID already present
- `_link_person_to_groups()` — orchestrates: reads person's units_served, matches to groups, calls add

Wired into `enrich_all_people()` after the enrichment loop completes.

## Member Entry Schema

```json
{
  "PersonID": "01KJ33MW...",
  "name": "Omar N. Bradley",
  "role": "Commander",
  "from_date": "1944-01",
  "to_date": "1944-08",
  "source": "biographical_enrichment",
  "confidence": 0.8
}
```

## QA

- pylint: 9.28
- radon: no C-rated functions
- bandit: 0 issues
