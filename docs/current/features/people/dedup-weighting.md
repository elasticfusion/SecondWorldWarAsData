# People Deduplication — Weighted Scoring & the Surname Suggestion Report

**Last Updated:** 2026-10-05

This documents the *subtleties* of the config-driven people dedup scoring and the
human-gated corpus-frequency surname suggestion report. For the end-to-end dedup
workflow (when it runs, auto-merge vs human gate), see
[deduplication.md](deduplication.md).

---

## When dedup runs (and what auto-merges)

Dedup runs at **Phase-2 task completion** (`ecs_entrypoint._run_dedup_detection`).
`find_duplicate_*.py` writes candidate groups to `duplicate_report.json`, then:

- **Auto-merge** merges only groups whose names are **byte-identical after
  normalization** (`_auto_merge_exact_duplicates`). Deliberately conservative.
- Everything else → the **human review gate** (unchanged by any of the work below).

The weighted scoring below improves the **ranking of human-review candidates**. It does
**not** by itself expand auto-merge: `dedup.people.auto_merge_threshold` defaults to
`999` (effectively off), so no new auto-merges happen until an operator deliberately
lowers it.

---

## The scoring model (all weights live in `config.yaml` → `dedup.people`)

Scoring is **additive** (`_score_pair`). The key subtleties:

### Proximity is the PRIMARY signal — and continuous
Two same-surname mentions close together in the source text are strong same-person
evidence; far apart, weak. `_proximity_weight` maps the **mention radius** (word
distance, from the shared sub-event fulltext) through decay tiers:
`tight_radius_words` → `near` → `loose` → cross-document (0). Tiers/weights are config.
(Human-friendly "same paragraph / chapter / book" from the design map onto radius —
we score the *radius* directly rather than needing a chapter field.)

### Conflicting middle initial is a VETO, not a match
`George S. Patton` vs `George P. Patton` is the author **distinguishing two people**.
`_conflicting_middle_initial` detects it; `_check_conflicting_middle` applies a strong
negative (`conflicting_middle_initial_weight`, default −5.0) that **overrides even the
tightest proximity** — two different Pattons in one paragraph never merge. A name that
merely **omits** the middle (`George Patton` vs `George S. Patton`) is NOT a conflict —
it stays a positive variant match.

### Surname commonness — nationality-aware, with a corpus fallback
A bare-surname match is only as strong as the surname is **rare in that person's
nationality**. Two mechanisms, layered:

1. **Curated nationality table** (`data/surname_frequency.yaml`, PREFERRED) — our own
   WWII-oriented, growable table: `Kowalski` is `rare` for `USA` (boost ×1.5) but
   `very_common` for `POL` (damp ×0.4). Selected by the person's `nationality` /
   `nationality_served`.
2. **Corpus-relative commonness** (FALLBACK) — computed live from the ingested people
   (`build_surname_stats`): a surname shared by ≥3 **distinct first names** is "common".
   Used only when nationality or a table entry is absent.

> Subtlety: #1 encodes external knowledge the corpus can't (Kowalski is common *among
> Poles*); #2 is self-calibrating to our data. The curated table is **never** derived
> automatically — see the suggestion report below.

### Rank × proximity — rank is point-in-time
Rank (`Major` → `Lt. Col.`) is a moment in time, so it interacts with proximity in the
**opposite** direction from names (`_rank_proximity_penalty`):

- Different rank-set + **tight** proximity → **mild negative** (likely two different
  people shown together; an author wouldn't flip one person's rank mid-passage).
- Different rank-set + **wide** separation → **neutral** (promotion over time is
  plausible).

"Different rank" is **promotion-aware**: only genuinely **disjoint** rank-sets count
(`Major` vs `Colonel`), never a record that *spans* `Major`+`Colonel`. It **dampens,
never vetoes** (unlike the conflicting-initial case, which is a hard author signal).

### Frequency (secondary) — the single-subject-book effect
A surname that dominates a corpus (a Patton biography) gives co-mentions a modest boost,
but only with a **compatible** first name/initial — never bridging a conflict.

### Shared unit affiliation — a WEAK positive, strength INVERSE to unit size
Two same-surname people in the **same people-group (unit)** is same-person evidence, but
weak — and its strength scales **inversely with the unit's size (echelon)**, because few
people share a small unit while thousands share a large one
(`_shared_unit_affiliation`, config `dedup.people.shared_unit`):

| Echelon | Default weight | Note |
|---|---|---|
| squad / section | 0.6 | smallest unit → strongest signal |
| platoon | 0.5 | |
| company / battery / squadron | 0.4 | |
| battalion | 0.25 | |
| regiment / group | 0.1 | "Smith in the 110th Infantry Regiment" — weak |
| brigade | 0.08 | |
| division / wing | 0.03 | ≈ noise |
| corps / army / fleet / command | 0.0 | too large to be evidence |
| (echelon unknown) | 0.1 | shared unit, echelon not classified |

Subtleties:
- **Matched on resolved `GroupID` first** (authoritative — same actual unit), falling back
  to **normalized `designation`** when GroupID isn't resolved yet ("502 PIR" ↔ "502nd PIR").
- Returns the weight of the **strongest (smallest-echelon)** shared unit.
- A **weak corroborator only** — all weights are small nudges (< 1.0); it never carries a
  merge on its own, it just reinforces a candidate that already has name evidence. Example:
  "Smith in the 502 PIR" is super-weak (0.1) — not zero, but highly unlikely to be
  decisive, exactly as intended.

---

## The surname SUGGESTION report (`output/people/surname_frequency_suggestions.json`)

A human-gated mechanism to let the curated table get **stronger over time** without the
dangers of auto-editing. Subtleties that make it safe:

### Suggestion-only — never auto-edits the curated table
The report only **suggests** additions/corrections to `data/surname_frequency.yaml`. A
human reviews and promotes entries by hand. The curated table is never written by code
(enforced + tested).

### Skew guard: DISTINCT PEOPLE, not mentions
The single-subject-book problem: a Patton biography has hundreds of "Patton" mentions
but **one** person — that must not make "Patton" look common. Suggestions are therefore
keyed on the count of **distinct people** sharing a `(nationality, surname)`, NOT raw
mention count. `distinct_people` drives the suggested band; `mentions` is shown for
context only. (One Patton → `rare`; twelve distinct Smiths → `very_common`.)

### Growth-triggered, not scheduled
`maybe_generate` regenerates the report **only when the people corpus has grown by ≥
`dedup.people.surname_report.min_new_people`** (default 50) since the last report
(tracked in `output/people/.surname_report_state.json`). It hooks into
`_run_dedup_detection`, so: corpus grows → dedup runs → report refreshes **if** growth
met. Avoids churn and keeps the signal meaningful.

### Nationality-conditioned
Commonness is per-nationality, so a person with **no** nationality is skipped (we can't
say a surname is "common" without the population it's common *in*).

---

## Other entity types (intentionally different — do NOT force this model on them)

- **Groups / organizations** — a *similar* pattern will follow, but keyed on **structured
  unit designations** (`9th Infantry Division` / `9th Inf Div` / `the 9th`): numerics +
  echelon, not surname rarity.
- **Equipment** — a **different** problem: the human habit of calling one thing many
  names (`Sherman` / `M4` / `M4A3` / `medium tank`). That is an **alias/synonym**
  resolution problem, not a frequency-rarity one, and needs its own model. The people
  surname model must not be applied to equipment.

---

## Related
- [deduplication.md](deduplication.md) — end-to-end dedup workflow + exclusions
- [GROUP_DEDUPLICATION_SYSTEM.md](GROUP_DEDUPLICATION_SYSTEM.md) — group dedup
- [../../SCHEMA_REFERENCE.md](../../SCHEMA_REFERENCE.md) — `nationality` / `nationality_served` / `ranks`
- Config: `config.yaml` → `dedup.people`; data: `data/surname_frequency.yaml`
