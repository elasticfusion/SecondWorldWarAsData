# Data Quality Status

> **See also:** [Data Quality — Safeguards & Guarantees](dataquality/DATA_QUALITY_SAFEGUARDS.md) — how validation is enforced on every write (central guard, version-awareness, merge/enrichment coverage).


**Last Updated:** 2026-10-05  
**Schema Version:** 2.5

> This document is the **canonical source** for entity counts and enrichment
> rates. Other docs (e.g. `features/README.md`) link here rather than
> duplicating numbers. Counts are a dated snapshot and drift with each pipeline
> run; the enrichment/quality columns below predate the 2026-09 ingestion work
> and are refreshed on the next full enrichment pass.

---

## Entity Counts

| Entity Type | Files | Enriched | Not Found | No Status |
|---|---|---|---|---|
| People | 1,650 | 68 (4%) | 1,476 (89%) | 106 (6%) |
| People Groups | 1,606 | 794 (49%) | 1 (<1%) | 812 (51%) |
| Places | 2,847 | 1,058 (37%) | 183 (6%) | 1,604 (56%) |
| Dates | 1,680 | — | — | — |
| Equipment | 554 | — | — | — |
| Weather | 734 | — | — | — |
| Logistics | 7,769 | — | — | — |
| Casualties | 8,696 | — | — | — |
| Bibliography | 12,754 | — | — | — |
| Maps | 55 | — | — | — |
| Events | 866 | — | — | — |

**Total entities:** ~38,111 (excludes events; snapshot 2026-09-23)

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

Regenerate with `scripts/referential_integrity_audit.py` (read-only; writes
`docs/current/dataquality/referential_integrity_report.json`). Snapshot **2026-10-10**
(5,076 dangling refs total; PK counts: people 1,714 / places 2,855 / groups 1,682 /
equipment 551 / dates 1,912 / events 5,953):

| From → To | Total | Dangling | Rate | Notes |
|---|---|---|---|---|
| maps → events (EventID) | 54 | 54 | **100%** | every map's EventID resolves to no event — likely a wrong-ID-field extractor bug (IDs are real ULIDs, not placeholders) |
| casualties → places (impacted_places[].PlaceID) | 1,123 | 969 | **86%** | likely MentionID/PlaceID type confusion (cf. weather) |
| casualties → people (impacted_people[].PersonID) | 160 | 87 | 54% | |
| weather → places (location.PlaceID) | 754 | 361 | 48% | known MentionID-vs-PlaceID mismatch |
| casualties → events (event_context.EventID) | 8,832 | 3,543 | 40% | |
| images → events (EventID) | 387 | 51 | 13% | |
| source_section → events (EventID) | 157 | 11 | 7% | |
| casualties → people (PersonID) | 2 | 0 | 0% | |
| casualties → places (PlaceID) | 9 | 0 | 0% | |

Each high-rate edge is a distinct extractor-level reference bug to investigate + a corpus-reprocess
candidate; the audit scopes them. (Previous hand-maintained snapshot — Weather→Places 31%,
Casualties→Groups ~87% — is superseded by the audit above; the groups edge now shows 0 refs
because casualties carry group references under `impacted_organizations[]`, which currently
contains none that resolve — tracked.)


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
