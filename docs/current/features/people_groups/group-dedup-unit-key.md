# Group (Unit) Deduplication — Canonical Unit Key

**Last Updated:** 2026-10-05

Group/unit dedup matches on a **canonical unit key** derived from the name, not raw
string similarity — so `Ninth Division` / `9th Division` / `9th Infantry Division` all
cluster, while genuinely different units stay apart. This is the group analog of the
people weighted model (see [dedup-weighting.md](../people/dedup-weighting.md)); it is deliberately
**different** from people (structured designations, not surname rarity) and from
equipment (which is an alias/synonym problem — a separate future model).

Config: `config.yaml` → `dedup.groups`. Implementation: `src/dedup/unit_key.py` +
`scripts/find_duplicate_groups.py`.

---

## The canonical unit key

`derive_unit_key(name)` → `(numbers, branch, echelon)`:

- **numbers** — the unit's numeric designator(s). Arabic, ordinals ("Ninth"→9), ordinal
  suffixes ("2d"/"2nd"→2), and roman numerals (kept distinct: `VII Corps` ≠ `7th Corps`).
- **branch** — infantry / armored / cavalry / airborne / artillery / engineer / … .
  **INFANTRY (combat arm) IS THE DEFAULT only when the SERVICE is Army** ("9th Division" ⇒ infantry).
- **echelon** — squad / platoon / company / battalion / regiment / brigade / division /
  corps / army / … . `None` when unspecified.

**Abbreviations** are expanded before extraction: `PIR` → parachute infantry regiment,
`Inf Div` → infantry division, `Armd Div` → armored division, `Abn` → airborne, `Bn` →
battalion, etc.

## The match rule (`unit_keys_match`)

| Field | Rule |
|---|---|
| **numbers** | MUST match (and at least one number present). |
| **service** | Armed SERVICE (Army / AAF / Navy / Marines / Coast Guard), from the name. **Mismatch ⇒ ABSOLUTE VETO** (1st Marine Division ≠ 1st Infantry Division). Default ARMY. |
| **arm (combat arm)** | Absent → defaults to `infantry`; **branch mismatch ⇒ VETO**. |
| **echelon** | Absent → permissive (no veto); **present on both AND different ⇒ VETO**. |
| **nationality** | From the stored `nationality` field (or a nationality word in the name); **both known AND different ⇒ VETO** ('2nd Division (Canadian)' ≠ '2nd Division (US)'); unknown on either side is permissive. |

### Worked cases (all tested)
| A | B | Result | Why |
|---|---|---|---|
| Ninth Division | 9th Infantry Division | **match** | 9 / infantry / division |
| 9th Division | 9th Infantry Division | **match** | infantry(default) = infantry |
| **9th Armored** | **9th Division** | **NO match** | armored ≠ infantry(default) — VETO |
| 9th Infantry Division | 9th Armored Division | NO match | branch veto |
| 9th Division | 9th Regiment | NO match | echelon veto (division ≠ regiment) |
| 9th Armored | 9th Armored Division | match | echelon absent on one → permissive |
| 110th | 110th Regiment | match | bare number → echelon permissive |
| 502nd PIR | 502nd Parachute Infantry Regiment | match | abbreviation expansion |
| 9th Division | 10th Division | NO match | number mismatch |

## Primary vs fallback

The canonical key is the **PRIMARY** matcher. When a key can be formed (the names have
numbers), string similarity is **not** consulted — a key **veto blocks the pair even if
the raw strings look similar** ("9th Armored" vs "9th Division"). Only when a key can't
be formed (no number) does the matcher fall back to the old fuzzy
similarity/substring behavior.

## Proximity corroboration

Group names appear in `event_mentions` just like people, so proximity applies here too:
a bare "110th" closely followed by "110th Regiment" in the same sub-event text
corroborates the key match (`_groups_proximate`, radius from `dedup.groups.proximity`).
It is **corroboration only** — it never overrides a veto (two different-branch units
adjacent in the text are still different units).

## Not this model
- **People** → surname rarity / proximity / rank (see dedup-weighting.md).
- **Equipment** → alias/synonym resolution ("Sherman"/"M4"/"M4A3") — a different model,
  not yet built.

## Related
- [dedup-weighting.md](../people/dedup-weighting.md) — people weighted scoring
- [Rules & pipeline](GROUP_DEDUP_RULES_REVIEW.md) — dedup rules + pipeline flow
- [deduplication.md](../people/deduplication.md) — end-to-end dedup workflow

---

## Unit nicknames / sobriquets

Many WWII units are referenced by nickname with NO number ("Screaming Eagles", "Big
Red One", "Ivy Division", "Spearhead") — the canonical key can't derive these. A curated
map (`data/unit_nicknames.yaml`, our own/growable) resolves a nickname to its canonical
name BEFORE key derivation, so a nickname keys identically to its numbered unit and the
vetoes still apply (Screaming Eagles ≠ 82nd Airborne; Big Red One ≠ 1st *Armored*).
`resolve_nickname()` is used by both the group deduper and the person→group linker, so
they resolve identically.
