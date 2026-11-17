# People-Group (Unit) Deduplication & Pipeline

**Last Updated:** 2026-11-16
**Status:** Living doc — the single source of truth for how people_groups flow through
the pipeline and how group dedup decides matches. Implementation:
`src/extraction/batch_parallel.py::_extract_people_groups` (live extraction), `src/extraction/people_groups.py` (record helpers), `src/extraction/enrich_groups.py`,
`src/extraction/enrich_biographies.py` (linking), `src/dedup/find_duplicate_groups.py`,
`src/dedup/unit_key.py`, `src/dedup/config.py`; config: `config.yaml → dedup.groups`.
Open questions are flagged **[REVIEW]**; not-yet-built items **[PENDING]**.

---

## 0. Pipeline flow (end to end)

1. **Extract** (Phase 2, `batch_parallel._extract_people_groups`): one request per 3-paragraph window of the raw parsed text (`src/extraction/windows.py`),
   cached, attributed to the sub-event holding the quote. New records are written as `military_unit` with `nationality`; the request does not ask for members. Schema (people_groups 2.29) has
   `group_type` (country/alliance/military_unit/political_party/government/… ) +
   `military_hierarchy`, `nationality`, `members[]`, etc. ⚠️ In practice only
   `military_unit`/(none) are populated — civilian types are schema-ready but unused.
2. **Persist + index**: `GroupID` ULID; write-time merge is by **normalized name only**
   (variants reconciled later by dedup). Each new unit also gets a Phase-2 Wikipedia/Grokipedia lookup (WWII pages only, `groups_wikipedia.enrich_group_from_wikipedia`, called from `batch_parallel._enrich_in_place`).
3. **Enrich** (Phase 3 step 1/6, `enrich_groups.enrich_all_groups`; structured facts only): query is **disambiguated** (nickname-resolved
   canonical name + `(Country)` suffix) with a **wrong-article guard** →
   `enrichment_status='ambiguous'` on nationality/echelon contradiction.
4. **Link people → groups** (`enrich_biographies._link_person_to_groups`; **not wired into the pipeline**: it runs only from `enrich_all_people`, which `phase3_enrich_data.py` no longer calls, so today it is CLI/test only): units match via
   the **canonical unit key** (same matcher as dedup); civilian `group_affiliations`
   (v2.6) link by name and **carry `date_verified`** (unverified title membership →
   provisional member, confidence 0.3).
5. **Dedup** (this doc, §§1-5): detect candidates → auto-merge byte-identical → human gate.

---

## 1. When it runs
At **Phase-2 task completion** (`ecs_entrypoint._run_dedup_detection` →
`_execute_dedup_scripts`, which runs `python -m src.dedup.find_duplicate_groups`). It writes candidate clusters to
`output/people_groups/duplicate_report.json`.

## 2. What auto-merges vs what waits for a human
- **Auto-merge:** clusters whose names are **byte-identical after normalization**, plus
  (groups only) clusters whose members all share one canonical unit key with no veto and no
  nationality conflict (`ecs_entrypoint._auto_merge_exact_duplicates`, `_auto_merge_entity_type`,
  `_group_cluster_canonically_mergeable`; keyed on `GroupID`). Deliberately conservative.
- **Everything else → the human review gate** (the dedup UI, `lambda_handlers/dedup_ui_handler.py`:
  merge / skip / exclude, with undo snapshots under `dedup/history/`). The detection below
  decides *which candidates are surfaced*; only the two auto-merge cases above skip review.
- The **human gate is never bypassed**, and reviewed/excluded pairs are remembered
  (`not_related.json`, `not_duplicates.json`, `load_reviewed_pairs("groups")`).

## 3. Candidate detection — the match rules (in order)

### (a) Number gate — REQUIRED
Two groups can only be duplicates if their **unit numbers match** (`_numbers_match`).
Numbers are normalized: Arabic, ordinal words ("Ninth"→9), ordinal suffixes
("2d"/"2nd"→2), and **roman numerals kept distinct** (`_extract_numbers` stores them as `r7`),
so this gate treats `VII Corps` and `7th Corps` as different and never reaches the key.
Two names with different numbers → not a candidate.

### (b) Canonical unit key — PRIMARY matcher
`derive_unit_key(name)` → `UnitKey(numbers, service, arm, echelon)`; `unit_keys_match` decides:

| Field | Rule |
|---|---|
| numbers | must match |
| **service** | Armed SERVICE (Army / AAF / Navy / Marines / Coast Guard), from the name. **Mismatch ⇒ ABSOLUTE VETO** (1st Marine Division ≠ 1st Infantry Division). Default ARMY. |
| **arm (combat arm)** | infantry / armored / cavalry / airborne / artillery / … . **Absent ⇒ INFANTRY only for a US Army division/regiment**, else unknown. Both known and different **⇒ VETO**. |
| **echelon** | squad…division…army. Absent ⇒ permissive (no veto). Present on **both** AND different ⇒ **VETO**. |

Abbreviations are expanded first (`PIR`→parachute infantry regiment, `Inf Div`→infantry
division, `Abn`→airborne, `Bn`→battalion, …).

### (c) Nationality — VETO
From the stored `nationality` field **or** a nationality word in the name. When **both**
groups have a known nationality and they **differ ⇒ VETO**
(`2nd Division (Canadian)` ≠ `2nd Division (US)`). Unknown on either side ⇒ permissive.

### (d) String-similarity — FALLBACK ONLY
Fuzzy name similarity (≥0.85) or substring match is used **only when a canonical key
can't be formed** (e.g. a name with no number). When a key exists, a **veto blocks the
pair even if the strings look similar**.

### (e) Proximity — CORROBORATION
Groups carry `event_mentions`, so a bare "110th" appearing near "110th Regiment" within
the configured radius **corroborates** a key match (`_groups_proximate`). It **never
overrides a veto**.

## 4. Worked examples (all tested)

| A | B | Result | Rule |
|---|---|---|---|
| Ninth Division | 9th Infantry Division | **merge-candidate** | key: 9 / infantry / division |
| 9th Division | 9th Infantry Division | **merge-candidate** | infantry(default)=infantry |
| 9th Armored | 9th Division | **NOT** | branch veto (armored ≠ infantry) |
| 9th Infantry Division | 9th Armored Division | **NOT** | branch veto |
| 9th Division | 9th Regiment | **NOT** | echelon veto |
| 9th Armored | 9th Armored Division | **merge-candidate** | echelon absent → permissive |
| 110th | 110th Regiment | **merge-candidate** | echelon permissive (+proximity) |
| 502nd PIR | 502nd Parachute Infantry Regiment | **merge-candidate** | abbreviation |
| 2nd Division (Canadian) | 2nd Division (US) | **NOT** | nationality veto |
| 9th Division | 10th Division | **NOT** | number mismatch |

## 5. Configuration (`config.yaml → dedup.groups`)
- `canonical_key.enabled`, `canonical_key.infantry_default`
- `proximity.*` (radius tiers + weights for corroboration)
All optional; absent → built-in defaults (= behavior above).

## 6. Pending / open items

- **[PENDING] Shared geographic place within a chapter = weak corroborator.** Units are
  often tied to one/multiple places in a chapter; a shared place would be a *weak*
  positive (like proximity). Not yet built.
- **[FIXED 2026-10-05] Infantry-default scope.** The infantry combat-arm default now
  applies ONLY at **division/regiment** echelon (Army, bare arm). A bare "9th Battalion"/
  "9th Company" keeps arm=unknown (not infantry). **Combat Commands (CCA/CCB/CCR)** — the
  armored-division brigade-equivalent combined-arms formations — are recognized as
  echelon `combat_command`, arm `armored`, with the command letter captured so CCA≠CCB≠CCR.
- **[PARTIAL] Roman/Arabic corps numbering.** `unit_key._numbers` maps roman→arabic, so
  `derive_unit_key("VII Corps")` equals `derive_unit_key("7th Corps")`. The detector's earlier
  `_numbers_match` gate (§3a) still keeps roman and arabic apart, so such a pair is not surfaced.
- **[FIXED 2026-10-05] people_groups auto-merge on canonical-key match.** Unlike People
  (fuzzy/weighted → human gate), a people_group's canonical unit key is DETERMINISTIC, so
  a clean key match with no veto IS the completed disambiguation → it **auto-merges** (no
  human gate; `ecs_entrypoint._all_keys_match` + `_any_nationality_conflict`). Byte-identical
  names also auto-merge. Underspecified names (no number, bare CC) are not auto-merged.
- **Not this model:** Equipment dedup is an alias/synonym problem ("Sherman"/"M4"/
  "M4A3") — a different model, not yet built.
- **[GAP] Civilian group_type unused** — political_party / government_organization are
  defined in extraction but rarely assigned; civilian dedup needs a category-discriminator
  veto + alias model (NOT the military unit key).
- **[GAP] Civilian dedup** — "State Dept" vs "Treasury Dept" (shared word ≠ identity),
  "House" vs "Senate" (distinct chambers), parties — need the alias/category model.

## Related
- [group-dedup-unit-key.md](group-dedup-unit-key.md) — canonical-key mechanism detail
- [groups.md](groups.md) — person↔group linking design
- [../people/dedup-weighting.md](../people/dedup-weighting.md) — the parallel people model
- Archived (superseded): `docs/archive/2026-10-05-people-groups/` (old GROUP_DEDUPLICATION_SYSTEM + the two point-in-time review snapshots, consolidated here)
- [deduplication.md](../people/deduplication.md) — end-to-end dedup workflow

See also: [Dedup review gate (Phase 2 to Phase 3)](../../core/DEDUP_REVIEW_GATE.md) for detection, auto-merge, the gate, the review UI and the operator runbook.
