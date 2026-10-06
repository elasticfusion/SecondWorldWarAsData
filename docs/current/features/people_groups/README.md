# People-Groups (Units & Organizations)

**People-groups are a first-class entity type**, separate from (but related to) People —
like Places, Dates, Equipment. A people_group is a military unit, country, alliance,
political party, government/anti-government organization, or religious organization. A
person *belongs to* groups (concurrently); the group is its own record.

## Docs
| Document | Purpose |
|---|---|
| [groups.md](groups.md) | Person ↔ people-group linking design |
| [GROUP_DEDUP_RULES_REVIEW.md](GROUP_DEDUP_RULES_REVIEW.md) | **Single source of truth**: end-to-end pipeline flow + group dedup rules + open gaps |
| [group-dedup-unit-key.md](group-dedup-unit-key.md) | Canonical unit key mechanism (number + service + arm + echelon) + nicknames |

_Superseded/consolidated (archived 2026-10-05):_ `GROUP_DEDUPLICATION_SYSTEM.md` (stale
pre-canonical-key model) and the two point-in-time review snapshots
(`PEOPLE_GROUPS_LOGIC_REVIEW.md`, `PEOPLE_GROUPS_STEP_BY_STEP.md`) → folded into
`GROUP_DEDUP_RULES_REVIEW.md`; see `docs/archive/2026-10-05-people-groups/`.

## Code
- Extraction: `src/extraction/people_groups.py` (`prompts/people_groups.yaml`)
- Enrichment: `src/extraction/enrich_groups.py` (disambiguated query + wrong-article guard)
- Dedup: `scripts/find_duplicate_groups.py` + `src/dedup/unit_key.py` + `data/unit_nicknames.yaml`
- Person→group linking: `src/extraction/enrich_biographies.py::_link_person_to_groups`
  (canonical-key for units; name-match for civilian memberships, carrying `date_verified`)

## Related (People)
- [../people/deduplication.md](../people/deduplication.md)
- [../people/dedup-weighting.md](../people/dedup-weighting.md)
- [../people/biographical-enrichment.md](../people/biographical-enrichment.md) — title-implied memberships

## Phase 3 Enrichment

**Step 2/6** external unit-history enrichment (`enrich_groups.enrich_all_groups`), then
**step 4c** Wikipedia images + extracts (`groups_wikipedia`). Gated by the shared
`enrichment_gate`. See [Phase 3 Enrichment](../../core/PHASE3_ENRICHMENT.md).
