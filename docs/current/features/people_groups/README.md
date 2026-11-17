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

## How units are extracted (Phase 2)

Units are discovered over 3-paragraph windows of the raw parsed text (`src/extraction/windows.py`, `batch_parallel._extract_people_groups`), at every
echelon and nationality; the item is attributed to the sub-event holding its quote. The schema version is per entity (people_groups 2.29,
`src/schemas/__init__.py`). See [groups.md](groups.md).

## Code
- Extraction: `src/extraction/batch_parallel.py::_extract_people_groups` (live, windowed); record merge helpers in `src/extraction/people_groups.py` (older per-sub-event path uses `prompts/people_groups.yaml`)
- Enrichment: `src/extraction/enrich_groups.py` (disambiguated query + wrong-article guard)
- Dedup: `src/dedup/find_duplicate_groups.py` + `src/dedup/unit_key.py` + `data/unit_nicknames.yaml`
- Person→group linking: `src/extraction/enrich_biographies.py::_link_person_to_groups`
  (canonical-key for units; name-match for civilian memberships, carrying `date_verified`).
  It is reached only through `enrich_all_people` (module CLI and tests); `phase3_enrich_data.py`
  does not call it, so the pipeline does not currently fill `members[]`.
- Wikipedia/Grokipedia text + images: `src/enrichment/groups_wikipedia.py::enrich_group_from_wikipedia`,
  called in Phase 2 from `batch_parallel` (live windowed path, line ~1813) and `people_groups.extract_people_groups` (older path)

## Related (People)
- [../people/deduplication.md](../people/deduplication.md)
- [../people/dedup-weighting.md](../people/dedup-weighting.md)
- [../people/biographical-enrichment.md](../people/biographical-enrichment.md) — title-implied memberships

## Phase 3 Enrichment

**Step 1/6** (`phase3_enrich_data.enrich_groups_data` -> `enrich_groups.enrich_all_groups`) adds
structured unit-history facts (unit_type, nationality, commanders, operations) to
`enrichment_data`, gated by the shared `enrichment_gate`. Wikipedia/Grokipedia text and images
for groups are Phase 2, not Phase 3 (see the comment above the `people_groups` step in
`phase3_enrich_data.py`). See [Phase 3 Enrichment](../../core/PHASE3_ENRICHMENT.md).
