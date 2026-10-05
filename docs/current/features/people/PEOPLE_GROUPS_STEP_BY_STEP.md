# People-Groups Logic — Step by Step (for review)

**Last Updated:** 2026-10-05
**Purpose:** a numbered, sequential walkthrough of what happens to a people_group from
extraction through deduplication, with the exact code location for each step and review
notes. Companion to the summary [PEOPLE_GROUPS_LOGIC_REVIEW.md](PEOPLE_GROUPS_LOGIC_REVIEW.md)
and [GROUP_DEDUP_RULES_REVIEW.md](GROUP_DEDUP_RULES_REVIEW.md).

Legend: ✅ working · ⚠️ gap/limitation · ❓ review decision.

---

## Phase 2 — Extraction

### Step 1 — Extract groups from each event
`src/extraction/people_groups.py::extract_people_groups(event_file, grok, output_dir)`
- For each `*-event.json`, builds a prompt (`_build_extraction_prompt`) and calls Grok
  (`cache_type="people_groups"`, temp 0.3).
- Idempotent: `_is_already_processed` skips events already done unless `should_reprocess`.
- ✅ Per-event, cached, reprocess-aware.

### Step 2 — The extracted group shape
Prompt `prompts/people_groups.yaml` yields per group:
- `group_name`, `group_type` ∈ { country, alliance, **military_unit**, political_party,
  government_organization, anti_government_organization, religious_organization },
  `military_hierarchy` (squad…army_group), `nationality`, `alliance_membership`,
  `parent_organization`, `sub_organizations`, `common_name`, `members[]`.
- ⚠️ **In practice only `military_unit` and (none) appear** (~1,613 vs ~376). The
  civilian types are schema-ready but the extractor rarely assigns them → civilian-org
  handling is not yet exercised.

### Step 3 — Persist + index
`people_groups.py` (write path, ~L169-202)
- Assigns a `GroupID` (ULID) if absent; writes `output/people_groups/<name>_<ulid>.json`.
- Updates `index.json` (normalized name → filename) via `_update_index`.
- On an existing normalized-name match, **merges** into the existing file
  (`_merge_group`: unions member_countries, sub_organizations, members-by-PersonID).
- ⚠️ The write-time merge is by **normalized name only** — variants ("9th" vs "9th
  Infantry Division") create separate files here; they are reconciled later by dedup.

### Step 4 — Relationship pass (Phase-2 step 5/5)
`phase2_extract.py` → `generate_related_groups_report` over `people_groups/`
- Produces a related-groups report (parent/child/sibling), not a merge.

---

## Phase 3 — Enrichment

### Step 5 — Enrich each group
`src/extraction/enrich_groups.py::enrich_all_groups` → `enrich_group`
- Wikipedia/Grok enrichment; `member_countries` for alliances;
  `alliance_membership` back-filled from `nationality`; image + licence.
- ❓ Enrichment is keyed on the group **name** (no canonical key), so it can run per
  name-variant before dedup merges them.

### Step 6 — Link people → groups
`src/extraction/enrich_biographies.py::_link_person_to_groups` → `_find_group_file` →
`_add_member_to_group`
- For each `biographical_profile.units_served` entry, finds the matching group and adds
  the person to its `members[]` (role + from/to + confidence 0.8 + source).
- ✅ **(2026-10-05) Matching now uses the canonical unit key** (nickname-resolved) — the
  SAME matcher as the deduper. "9th Division" links to a "9th Infantry Division" group;
  "Screaming Eagles" → "101st Airborne Division"; service/arm/echelon/number vetoes block
  false links. (Fast path: exact name; number-less names won't canonical-match.)
- ⚠️ **Civilian `group_affiliations` (v2.6, House/state/party) are NOT yet linked here** —
  `_link_person_to_groups` still reads only `units_served`. When added, it must respect
  `date_verified` (don't link/assert an unverified title membership).

---

## Phase 2 completion — Deduplication
`ecs_entrypoint.py::_run_dedup_detection` runs, in order:

### Step 7 — Reclassify mis-filed units: FROM `places/` → TO `people_groups/`
`ecs_entrypoint._reclassify_military_units` → `scripts/reclassify_military_units.py`
- The LLM sometimes mis-extracts a military unit as a **place** (a unit name can read
  place-like in narrative). This pass scans `output/places/`, detects military-unit name
  patterns (division/corps/regiment/infantry/armored/…), and **moves those records out
  of places and into `people_groups/`** with a place→group schema transformation.
- Guards against over-eager moves: `FALSE_POSITIVES` ("Infantry School" stays a place)
  and `GEO_SUFFIXES` (names ending in sector/zone/front/area/beachhead/bridgehead are
  geographic and stay in places).
- Direction is one-way: **places → groups** (never groups → places).

### Step 8 — Index cleanup + exclusion migration
`_cleanup_entity_indexes`, `_migrate_exclusions_to_dynamo` (human "not-duplicate"
decisions persist to DynamoDB).

### Step 9 — Detect duplicate groups
`_execute_dedup_scripts` → `scripts/find_duplicate_groups.py::find_duplicate_groups`
Candidate rule (detail in GROUP_DEDUP_RULES_REVIEW.md):
1. **Number gate** required.
2. **Canonical unit key** (PRIMARY): number + **service** (Army default; mismatch =
   absolute veto) + **arm** (infantry default under Army; mismatch = veto) + **echelon**
   (mismatch both-present = veto; absent permissive). **Nicknames resolved first**
   (`data/unit_nicknames.yaml`).
3. **Nationality veto** (2nd Division Canadian ≠ US).
4. **Proximity** corroboration (shared event-mention text); **string similarity**
   fallback only when no number.
- ✅ Linker (Step 6) and deduper now share this exact matcher.
- ⚠️ The key is **military-only**; a numbered civilian body ("9th Circuit Court") would
  be mis-handled — civilian dedup needs a category-discriminator veto + alias model.

### Step 10 — Auto-merge vs human gate
`_auto_merge_exact_duplicates`
- **Auto-merge only byte-identical normalized names.** Everything else (incl. all
  canonical-key candidates) → the **human review gate**; reviewed/excluded pairs are
  remembered.
- ❓ Expanding group auto-merge to "canonical-key match + no veto" is a separate,
  deliberate decision (not done).

---

## Review checklist (open items)
- ⚠️ **Civilian types unused** (Step 2) — populate political_party / government_organization.
- ⚠️ **Civilian memberships unlinked** (Step 6) — link v2.6 `group_affiliations`, respect `date_verified`.
- ⚠️ **Civilian dedup** (Step 9) — add category-discriminator veto + alias model (not the unit key).
- ❓ **Auto-merge expansion** (Step 10) — key-match-no-veto vs human gate.
- ⏳ **Shared-geographic-place** weak corroborator — pending.

## Related
- [PEOPLE_GROUPS_LOGIC_REVIEW.md](PEOPLE_GROUPS_LOGIC_REVIEW.md)
- [GROUP_DEDUP_RULES_REVIEW.md](GROUP_DEDUP_RULES_REVIEW.md)
- [group-dedup-unit-key.md](group-dedup-unit-key.md)
- [biographical-enrichment.md](biographical-enrichment.md)
