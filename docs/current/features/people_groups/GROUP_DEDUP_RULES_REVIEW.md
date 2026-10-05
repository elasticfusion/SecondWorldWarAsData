# People-Group (Unit) Deduplication & Pipeline

**Last Updated:** 2026-10-05
**Status:** Living doc — the single source of truth for how people_groups flow through
the pipeline and how group dedup decides matches. Implementation:
`src/extraction/people_groups.py`, `src/extraction/enrich_groups.py`,
`src/extraction/enrich_biographies.py` (linking), `scripts/find_duplicate_groups.py`,
`src/dedup/unit_key.py`, `src/dedup/config.py`; config: `config.yaml → dedup.groups`.
Open questions are flagged **[REVIEW]**; not-yet-built items **[PENDING]**.

---

## 0. Pipeline flow (end to end)

1. **Extract** (Phase 2, `people_groups.py`): per-event, cached, idempotent. Schema has
   `group_type` (country/alliance/military_unit/political_party/government/… ) +
   `military_hierarchy`, `nationality`, `members[]`, etc. ⚠️ In practice only
   `military_unit`/(none) are populated — civilian types are schema-ready but unused.
2. **Persist + index**: `GroupID` ULID; write-time merge is by **normalized name only**
   (variants reconciled later by dedup).
3. **Enrich** (Phase 3, `enrich_groups.py`): query is **disambiguated** (nickname-resolved
   canonical name + `(Country)` suffix) with a **wrong-article guard** →
   `enrichment_status='ambiguous'` on nationality/echelon contradiction.
4. **Link people → groups** (`enrich_biographies._link_person_to_groups`): units match via
   the **canonical unit key** (same matcher as dedup); civilian `group_affiliations`
   (v2.6) link by name and **carry `date_verified`** (unverified title membership →
   provisional member, confidence 0.3).
5. **Dedup** (this doc, §§1-5): detect candidates → auto-merge byte-identical → human gate.

---

## 1. When it runs
At **Phase-2 task completion** (`ecs_entrypoint._run_dedup_detection` →
`find_duplicate_groups.py`). It writes candidate clusters to
`output/people_groups/duplicate_report.json`.

## 2. What auto-merges vs what waits for a human
- **Auto-merge:** only clusters whose names are **byte-identical after normalization**
  (`_auto_merge_exact_duplicates`, keyed on `GroupID`). Deliberately conservative.
- **Everything else → the human review gate.** The weighted/keyed detection below
  improves *which candidates are surfaced and how they're ranked*; it does **not**
  auto-merge on its own.
- The **human gate is never bypassed**, and reviewed/excluded pairs are remembered
  (`not_related.json`, `not_duplicates.json`, `load_reviewed_pairs("groups")`).

## 3. Candidate detection — the match rules (in order)

### (a) Number gate — REQUIRED
Two groups can only be duplicates if their **unit numbers match** (`_numbers_match`).
Numbers are normalized: Arabic, ordinal words ("Ninth"→9), ordinal suffixes
("2d"/"2nd"→2), and **roman numerals kept distinct** (so `VII Corps` ≠ `7th Corps`).
No shared number → not a candidate.

### (b) Canonical unit key — PRIMARY matcher
`derive_unit_key(name)` → `(numbers, branch, echelon)`; `unit_keys_match` decides:

| Field | Rule |
|---|---|
| numbers | must match |
| **service** | Armed SERVICE (Army / AAF / Navy / Marines / Coast Guard), from the name. **Mismatch ⇒ ABSOLUTE VETO** (1st Marine Division ≠ 1st Infantry Division). Default ARMY. |
| **arm (combat arm)** | infantry / armored / cavalry / airborne / artillery / … . **Absent ⇒ defaults to INFANTRY.** Branch **mismatch ⇒ VETO**. |
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
- **[REVIEW] Branch default scope.** INFANTRY default is applied at **all** echelons when
  branch is absent (not only divisions). Confirm this is desired for e.g. "9th Battalion".
- **[REVIEW] Roman-vs-Arabic numbering.** `VII Corps` and `7th Corps` are treated as
  **different** numbers today (roman kept distinct). Confirm — if they should unify,
  the number normalizer must map roman→arabic.
- **[REVIEW] Auto-merge expansion.** Group auto-merge is still byte-identical-only; the
  canonical key currently only ranks human-gate candidates. Expanding auto-merge to
  "key match + no veto" would be a separate, deliberate change.
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
