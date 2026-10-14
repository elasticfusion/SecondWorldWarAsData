# Person Disambiguation — Design (DRAFT, design-first)

Status: DRAFT — design under discussion with the owner. No code yet.
Last updated: 2026-10-14

Companion: `.kiro/steering/project-overview.md` (names entity-resolution as a known
gap: "People/units are per-mention → needs entity resolution/de-duplication; the ETO
Order of Battle is the authoritative spine"), `ARTIFACT_STORE_DESIGN.md` (uploaded
service records = a source), existing dedup (`scripts/find_duplicate_people.py` —
rank/alias/promotion-aware scoring + the human `dev-wwii-dedup-ui` review gate).

---

## 1. Problem

A single real person is mentioned across many sources with varying evidence:

- **Uploaded service record** → rich, authoritative: service number, full name, rank,
  unit (4th Division), service dates, hometown.
- **Website / book mention** → "Joe Smith, a 4th Division sergeant in 1944" —
  corroborating, moderate specificity, NO service number.
- **Low-information mention** → "Sergeant Smith in the ETO" — could be any of dozens.

When do these refer to the SAME real person? We must avoid BOTH:
- **False merge** (collapse two different Smiths into one) — fabricates a person;
  catastrophic for a CITABLE corpus; hard to undo.
- **False split** (never link Joe-the-record to Joe-the-website) — breaks the future
  "show me everything about Joe Smith → map of where he was" UI.

For a citable historical corpus, **false-merge is worse than false-split.**

---

## 2. Locked decisions (owner)

1. **A `person_cluster` OVERLAY, not in-place merge (APPROVED).** Introduce a new
   identity/disambiguation record that LINKS the per-mention person records believed to
   be the same real human. The underlying mention-level `people/*.json` records are
   NEVER destroyed by resolution — they remain the evidentiary record of what each
   source said. The cluster is an overlay, reversible (detach a wrong link without
   touching the mention).
2. **People only. people_groups are NOT in this category (APPROVED).** A unit is ONE
   continuous entity whose NAME changed over time ("4th Division" in WWII →
   "4th Infantry Division" later) — a naming/ALIAS problem, not an identity-ambiguity
   problem. There is no "which of the many 4th Divisions?" the way there is "which Joe
   Smith?". So: **people need DISAMBIGUATION (one name → many people); units need
   ALIASING (one unit → many names over time).** Opposite directions. A unit alias lives
   in the unit's OWN `people_groups` file (`aliases[]`, already in the schema) — enough
   for searchability AND for a future post-WWII scope expansion. No cluster for groups.
3. **Service number is a strong AUTO-LINK key but rarely present (owner).** Present in
   personal/service records, ABSENT from book/website mentions. So two service records
   can auto-link on service number; the common record-vs-mention case rests on the
   weaker compound (name + unit + rank + date window) → an "educated guess" → human
   review, not auto-merge.
4. **Low-information mentions never auto-merge (owner).** "Sergeant Smith in the ETO"
   stays an unlinked / `unresolved` mention; it may be attached later by a human if
   stronger evidence appears, but is NEVER auto-attached to Joe's cluster.

### Backlog item spun off (TODO.md, MEDIUM)
- The human dedup UI must SHOW the mention TEXT (`original_text`/`verbatim_reference`)
  for context, because resolution is now an educated guess on common names. Not a
  priority; recorded in `docs/current/TODO.md`.

---

## 3. The model: two layers

```
MENTION LAYER (exists today)                 IDENTITY LAYER (new)
 people/<PersonID>.json   ─┐
   per-mention, per-source  │   members[]    person_clusters/<PersonClusterID>.json
   never destroyed by       ├───────────────▶  canonical identity + linked members
   resolution; the          │                   + per-member confidence/basis/status
   evidentiary record      ─┘
```

### `person_cluster` record (proposed shape)
```jsonc
{
  "PersonClusterID": "01...",
  "canonical_name": "Joseph T. Smith",
  "identity": {                      // strongest discriminators (from the best member)
    "service_number": "32123456",    // if ANY member has it
    "rank": "Sergeant",              // point-in-time / highest known
    "unit_id": "01<4th Division GroupID>",
    "service_dates": {"start": "1944", "end": "1945"}
  },
  "members": [
    { "PersonID": "01<service-record Joe>", "confidence": 1.0,  "basis": "service_number", "status": "resolved" },
    { "PersonID": "01<website Joe>",        "confidence": 0.78, "basis": "name+unit+date", "status": "candidate" }
  ],
  "resolution": { "method": "...", "reviewed_by": "...", "reviewed_at": "..." }
}
```
- A **singleton** cluster (one member) is fine — most people start as a singleton; a
  cluster forms/grows only when a link is justified.
- `status` per member: `resolved` (confirmed link), `candidate` (pending human review),
  `unresolved` (low-info; present but NOT asserted to be this person).
- Wrong link → detach the member; the `people/<PersonID>.json` is untouched.

---

## 4. The discriminator ladder (how members are proposed)

Match on IDENTITY EVIDENCE, not names (names are the weakest signal, worst for common
surnames). Strength order:

1. **Service number** — unique key → **AUTO-LINK** (confidence ~1.0). Rare across
   record↔mention; common only record↔record.
2. **Unit + rank + time window** — strong compound ("4th ID sergeant, 1944"). Few
   matches → **CANDIDATE → human review** (the Joe case).
3. **Name specificity** — "Joseph T. Smith" ≫ "Joe Smith" ≫ "Sgt. Smith". A
   **common-surname penalty** lowers confidence (Smith/Jones/Müller ≫ rare names).
4. **Co-occurring entities** — same events/places/dates/people as another mention →
   corroboration bump.

Reuse the existing rank/alias/promotion-aware scorer in
`scripts/find_duplicate_people.py` (`_RANK_TOKENS`, `_rank_set`) + the confidence
grouping already feeding the dedup gate — extend it with service-number exact-match,
the unit+date compound, and the common-surname penalty.

---

## 5. Three outcomes (wired to the EXISTING human gate)

| Evidence | Outcome |
|---|---|
| Service-number exact match | **auto-link** → member `status: resolved` |
| Name + unit + rank + date align (no service #) | **candidate** → routed to the human dedup gate (`dev-wwii-dedup-ui`); operator confirms using the mention TEXT (backlog item) |
| Bare name / low info ("Sgt Smith in the ETO") | **unresolved** → stays a standalone mention / singleton; NEVER auto-attached |

The dedup gate promotes `candidate` → `resolved` (or rejects → detach). This is the
same gate already blocking Phase 3 on duplicate groups — people clusters become another
reviewable surface on it.

---

## 6. Spines (what resolves against what)

- **Unit spine:** the ETO Order of Battle `people_groups` (with `aliases[]` carrying
  the WWII↔later names). Mentions resolve their `unit_id` against it.
- **Person spine:** uploaded authoritative **service records** seed high-confidence
  `identity` blocks; web/book mentions resolve AGAINST those seeds via the ladder.
- A web mention of "Joe Smith, 4th Division sergeant" → matched to the service-record
  Joe's cluster as a `candidate` (name+unit+rank+year), confirmed by a human.

---

## 7. Open questions (before code)

1. **Cluster storage:** new entity type `person_clusters/<id>.json` under `output/`
   (own schema + ENTITY_SCHEMA_VERSIONS entry + referential-integrity wiring) — confirm
   it's a first-class entity vs an index-only artifact.
2. **Auto-link default for name+unit+date (no service #):** owner indicated these are
   educated guesses → default **hold-for-human-review** (safer, more operator load) vs
   auto-link above a confidence threshold. Lean: review (false-merge is worse).
3. **Common-surname list / penalty source:** frequency table (corpus-derived, like the
   existing surname report) vs a static list.
4. **When does clustering run** — a Phase-3 step, or a separate resolution pass over the
   whole corpus (like dedup)? Likely alongside the existing people dedup.

---

## 8. Explicitly NOT doing
- No in-place merge-destroy of mention records (overlay only).
- No cluster for people_groups (units use `aliases[]`).
- No auto-merge on low-information mentions.
- No code until §7 Q1 (cluster as entity) + Q2 (review-vs-auto-link default) are
  confirmed.
