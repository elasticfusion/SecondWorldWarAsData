# Data Quality Status

**Last Updated:** 2026-06-13  
**Schema Version:** 2.3

---

## Entity Counts

| Entity Type | Files | Enriched | Not Found | No Status |
|---|---|---|---|---|
| People | 1,651 | 68 (4%) | 1,476 (89%) | 106 (6%) |
| People Groups | 1,609 | 794 (49%) | 1 (<1%) | 812 (51%) |
| Places | 2,858 | 1,058 (37%) | 183 (6%) | 1,604 (56%) |
| Dates | 1,683 | — | — | — |
| Equipment | 553 | — | — | — |
| Weather | 734 | — | — | — |
| Logistics | 7,769 | — | — | — |
| Casualties | 8,696 | — | — | — |
| Bibliography | 13,565 | — | — | — |
| Maps | 54 | — | — | — |

**Total entities:** ~39,172

---

## Source Books Processed

| Book | Author | Status |
|---|---|---|
| Cross-Channel Attack | Gordon A. Harrison | Complete (Phase 1-3) |
| Breakout and Pursuit | Martin Blumenson | Complete (Phase 1-3) |
| The Lorraine Campaign | Hugh M. Cole | Complete (Phase 1-3) |

---

## Known Data Quality Issues

### Critical

| Issue | Impact | Status |
|---|---|---|
| Places missing coordinates | 43% of places (1,256) have null lat/lon | Open — needs geocoding backfill |
| People enrichment shallow | 89% marked "not_found" — only basic fields populated | Open — enrichment prompt needs improvement |

### High

| Issue | Impact | Status |
|---|---|---|
| Bibliography URLs reset | 4,271 entries cleared pending re-verification | Awaiting Phase 3 re-run with verified resolver |
| Casualties PeopleGroupID | 87% null — entity_context now passes 50 entries (was 10) | Fixed in code, awaiting next extraction |
| Logistics severity skew | 78% high/critical despite calibration text | Open — needs few-shot examples |

### Medium

| Issue | Impact | Status |
|---|---|---|
| Date summary optimization | May miss dates only in fulltext | Mitigated — now falls back to fulltext when 3x longer |
| Event mention duplicates | Substring variants from re-extractions | Fixed locally, pipeline write-time dedup pending |
| People name resolution | 88 single-word names remain unresolved | Partially fixed (90+ resolved via Grok) |

---

## Cross-Reference Integrity

| From → To | Total Refs | Valid | Broken | Notes |
|---|---|---|---|---|
| Weather → Dates (DateID) | 662 | 662 (100%) | 0 | Fixed 2026-06-06 |
| Weather → Places (PlaceID) | 538 | 370 (69%) | 168 (31%) | MentionID vs PlaceID mismatch |
| Casualties → People Groups | ~9,594 | ~1,258 (13%) | ~8,336 | entity_context fix deployed |
| People Groups → People (members) | 24 | 20 (83%) | 4 | Fixed 2026-06-06 |

---

## Deduplication Status (Post-Cleanup 2026-06-06)

| Entity | Before | After | Reduction |
|---|---|---|---|
| Dates | 9,879 | 1,683 | 83% |
| Equipment | 1,375 | 553 | 60% |
| People (punctuation merge) | 1,872+ | 1,651 | 12% |
| Weather | 1,320 | 734 | 44% |
| People Groups | 1,620 | 1,609 | <1% |

---

## Validation Tools

- `scripts/validate_all_output.py` — Schema validation across all entities
- `scripts/json_quality_report.py` — Field completeness report
- `scripts/find_duplicate_people.py` — People dedup detection
- `scripts/find_duplicate_equipment.py` — Equipment dedup detection
- `scripts/find_duplicate_groups.py` — Groups dedup detection
- `scripts/find_duplicate_places_v2.py` — Places dedup detection

---

## Data Science Recommendations

See: `docs/current/dataquality/` for detailed analysis:
- `bibliography_resolution_process.md` — End-to-end bibliography resolution
- `new_entity_types.md` — Proposed Economic Data and Policy/Legislation types
