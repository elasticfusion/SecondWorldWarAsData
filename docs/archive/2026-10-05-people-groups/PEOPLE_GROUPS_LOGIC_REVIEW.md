# People-Groups Logic — Review

**Last Updated:** 2026-10-05
**Status:** FOR REVIEW — consolidated statement of the current people_groups logic
(extraction → enrichment → person↔group linking → dedup), verified against the code,
with gaps flagged. Companion to
[GROUP_DEDUP_RULES_REVIEW.md](GROUP_DEDUP_RULES_REVIEW.md).

> **[GAP]** = a mismatch between intent and current behavior. **[REVIEW]** = a decision
> for the owner.

---

## 1. Extraction (Phase 2) — `prompts/people_groups.yaml`, `src/extraction/people_groups.py`

The prompt already defines a **rich group taxonomy** (not just military):

- `group_type` ∈ { **country, alliance, military_unit, political_party,
  government_organization, anti_government_organization, religious_organization** }
- `military_hierarchy` (if military_unit): squad…army_group
- `nationality`, `alliance_membership`, `parent_organization`, `sub_organizations`,
  `common_name`, `members[]` (people with role + from/to date + confidence).

**[GAP] The civilian types are defined but barely populated.** Real data today is
~1,613 `military_unit` + ~376 `(none)` — **no** `government_organization` /
`political_party` / `country` instances in practice. So the civilian-entity handling we
discussed (White House, departments, Congress chambers, parties) is **schema-ready but
not exercised**; the extractor rarely assigns those types. First fix for civilian-org
work: ensure these types actually get assigned.

## 2. Enrichment (Phase 3) — `src/extraction/enrich_groups.py`

- `enrich_group` fetches Wikipedia/Grok data; `member_countries` for alliances;
  `alliance_membership` back-filled from nationality.
- Image/licence enrichment mirrors people.
- **[REVIEW]** Enrichment is keyed on the group name; no canonical-unit-key is used here
  (only in dedup). Fine today, but means enrichment can run per-name-variant before dedup
  merges them.

## 3. Person ↔ Group linking — `enrich_biographies._link_person_to_groups`

Adds a person as a `member` of a group, matched from `biographical_profile.units_served`.

- **[FIXED 2026-10-05] Linking now uses the canonical unit key** (nickname-resolved),
  the same matcher as the deduper: '9th Division'↔'Ninth Infantry Division'↔'9th
  Infantry Division' link to the same group; 'Screaming Eagles'→101st Airborne;
  service/arm/echelon/number vetoes still block false links. (Was: case-insensitive (`_find_group_file`:
  `key.lower() == unit_name.lower()`), NOT the canonical unit key. So "9th Division" on a
  person won't link to a group stored as "9th Infantry Division" — the exact kind of
  variant the dedup canonical key solves. **The linker and the deduper use different
  matching logic.** Recommend routing the linker through `src/dedup/unit_key.py` too.
- **[GAP] Civilian memberships not linked yet.** The new v2.6
  `biographical_profile.group_affiliations` (House/state/party, incl. date-unverified)
  are **not** consumed by `_link_person_to_groups` — it still only reads `units_served`.
  To realize "Representative Smith → House/NJ/Republican" as group memberships, the linker
  must also process `group_affiliations` (and respect `date_verified`).
- Membership records carry `role`, `from_date`, `to_date`, `confidence` (0.8), `source`.

## 4. Deduplication — `scripts/find_duplicate_groups.py` + `src/dedup/unit_key.py`

(Full detail in [GROUP_DEDUP_RULES_REVIEW.md](GROUP_DEDUP_RULES_REVIEW.md).) Summary:
- **Number gate** required.
- **Canonical unit key** PRIMARY: number + **service** (Army default; mismatch = absolute
  veto, e.g. 1st Marine Div ≠ 1st Inf Div) + **arm** (infantry default under Army;
  mismatch = veto) + **echelon** (mismatch when both present = veto; absent permissive).
- **Nationality veto** (2nd Division Canadian ≠ US).
- **Proximity** corroboration; **string similarity** fallback only when no number.
- **[GAP] The canonical key is MILITARY-only.** It assumes units; a
  `political_party`/`government_organization` with a number (e.g. "9th Circuit Court")
  would be mis-handled by military echelon/branch logic. Civilian dedup needs a
  **category discriminator** (route by `group_type`; cross-category = veto) + a curated
  alias model — NOT the unit key. (Equipment is a third, alias/synonym model.)

## 5. Auto-merge vs human gate
- Group **auto-merge = byte-identical normalized names only**; everything else → the
  human review gate (reviewed/excluded pairs remembered). The canonical key currently
  only ranks human-gate candidates. **[REVIEW]** expanding group auto-merge to
  "key match + no veto" is a separate deliberate change.

---

## Prioritized gaps (recommendation)

1. **[FIXED] Unified person↔group linking with the dedup canonical key** (+ curated
   nickname map data/unit_nicknames.yaml). 2026-10-05.
2. **[GAP] Link civilian `group_affiliations`** (v2.6) into groups, respecting
   `date_verified` (don't assert unverified memberships).
3. **[GAP] Populate the civilian `group_type`s** at extraction (they're defined but
   unused), then add the **category-discriminator veto** so the military unit key is only
   applied to military units and cross-category pairs never merge.
4. **[REVIEW]** group auto-merge expansion; **[PENDING]** civilian-org alias model +
   shared-geographic-place weak indicator.

## Related
- [GROUP_DEDUP_RULES_REVIEW.md](GROUP_DEDUP_RULES_REVIEW.md)
- [group-dedup-unit-key.md](group-dedup-unit-key.md)
- [dedup-weighting.md](../people/dedup-weighting.md)
- [biographical-enrichment.md](../people/biographical-enrichment.md) — title-implied memberships
