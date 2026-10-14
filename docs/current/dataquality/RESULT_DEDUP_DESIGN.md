# Centralized JSON Result De-duplication (design)

**Date:** 2026-10-13 · **Status:** DESIGN (not yet implemented) · **Priority:** HIGH
**Related:** URL_SOURCED_ENRICHMENT_DESIGN.md (the "different function" split out there:
not re-SEARCHING a URL ≠ not WRITING a duplicate result — THIS doc is the latter).

## Problem

Preventing duplicate RESULTS in a feature JSON is today done ad-hoc and inconsistently:
~20 enrichers each do their own `list.append({...})` with no shared dedup
(`openserp_enrichment.py` images 712/752, sources 787/916, awards 865; places; groups;
bibliography `resource_urls`; source_section `primary_sources`; ...). The only existing
de-dup is local/partial (`entity_store._mention_key` for mentions, scattered
`seen=set()`), so "don't write a duplicate" behaves DIFFERENTLY per entity type.

Owner directive (2026-10-13): **don't repeat JSON results; dedup must be CONSISTENT
across ALL schemas via centralized function(s)** — not per-entity ad-hoc.

## Approach — dedup at the ONE central write path, driven by per-schema declarations

Every entity write already funnels through a central path:
`src/utils/file_lock.py::write_json_with_lock` / `_validate_entity` and
`src/utils/json_validator.py::validate_and_write_json`. Put the dedup THERE so it runs
once, uniformly, for every entity type — enrichers keep appending naively; the write
path collapses duplicates.

Dedup is driven by a per-schema DECLARATION of which arrays are result-lists and what
makes two entries "the same" (identity key), because "duplicate" differs per field:

```
DEDUP_FIELDS (per entity schema) — field -> identity key(s):
  places:         images[]            -> normalized url   (via url_hygiene.clean_url)
  people:         images[]            -> clean url
                  military_awards[]   -> clean url
                  academic_sources[]  -> clean url
  people_groups:  images[]            -> clean url
                  web_results[]       -> clean url
                  veterans_assoc[]    -> clean url
  bibliography:   resource_urls[]     -> clean url (string list)
  source_section: primary_sources[]   -> clean url
  <all>:          mentions[]          -> MentionID (reuse entity_store._mention_key)
```

Identity rules:
- Identity = the SOURCE URL, NOT the claim/fact text (owner 2026-10-13, Churchill
  example): "Churchill initiated the Dunkirk rescue" cited by source A AND source B =>
  TWO entries kept (different URLs = independent corroboration, which feeds veracity/
  confidence). Only the SAME source URL re-returning the SAME fact collapses to one.
  So dedup must NEVER key on fact text — only on the clean source URL.
- URL-bearing entries dedup on `url_hygiene.clean_url(entry["url"])` (so tracking-param
  / case / fragment variants collapse to one — SAME normalizer as the URL registry, so
  "searched once" and "stored once" agree).
- ID-bearing entries (mentions) dedup on their ULID / junction key.
- Plain string lists (resource_urls) dedup on clean-url string equality.

### UPSERT (refresh + merge), not discard (owner 2026-10-13)
On a match (same clean URL already present), do NOT append a duplicate AND do NOT just
drop the new one — UPSERT the existing entry in place:
- refresh its `checked_at` to now; AND
- MERGE under SAFE-MERGE rules (hole #6, owner 2026-10-13) — never silently overwrite
  good data with a worse/empty re-search:
  * FILL-ONLY: a re-search fills EMPTY/absent fields; it does NOT overwrite a non-empty
    field unless the new value is strictly RICHER (upgrade-only). Thinner/empty/blocked
    re-search -> KEEP stored (never downgrade).
  * LISTS = UNION / never-shrink: add genuinely-new items; NEVER drop a stored item just
    because it didn't reappear this time (missing != removed; could be transient).
  * A non-authoritative fetch (block page, empty extraction, error, thin Wayback) writes
    NO field changes — at most refreshes recheck history.
- GROK FACT-CHECK on merge (owner 2026-10-13): Grok classifies each new claim and
  verifies it AGAINST THE SOURCE CONTENT (does the cited page actually substantiate it)
  — NOT against Grok's own general knowledge (ungrounded Grok facts stay untrusted):
  * FACT, confirmed by the source      -> WRITE (store with the confirming evidence).
  * FACT, NOT confirmed by the source  -> DO NOT WRITE (don't record an unverified/
    contradicted fact).
  * OPINION / interpretation           -> WRITE, but TAGGED `claim_type: "opinion"`
    (recorded as an opinion with its source URL; never mixed in as a confirmed fact).
  Each stored claim carries `claim_type: fact|opinion` (+ facts carry confirmed=true).
  Pairs with source_qualification: claim_type says WHAT KIND of statement; the
  qualifier says HOW well-sourced.
- TRACK RESULTS OVER TIME (option 1, owner 2026-10-13): each result entry keeps a
  `history[]` of observations `[{checked_at, status: live|wayback|dead|blocked,
  found/confidence/snapshot}]` so content/liveness EVOLUTION is auditable (still-there /
  degraded / 404'd), not collapsed into one value. The safe-merge rules decide the
  CURRENT best values; `history[]` preserves what each recheck saw. Cap retention (keep
  last N observations / coalesce consecutive "still live" runs) so it doesn't grow
  unbounded over years of 90-day rechecks.
- DEPENDS ON hole #7: `history[]` + `checked_at` change every recheck, so they MUST be
  excluded from the `_source_hash`/change-detection (else the entity churns every run,
  breaking incremental publish). Resolve #7 before building this.
- Order preserved, list does not grow on a same-URL hit.

Post-90-day scenario (the governing example): the URL registry entry has expired, so
Phase 3 RE-SEARCHES the URL. On write:
- same URL, same data  -> no duplicate; `checked_at` refreshed (proof it's still live).
- same URL, NEW data   -> no duplicate; `checked_at` refreshed + new fields merged in.
- NEW source URL       -> a NEW entry (independent corroboration — the Churchill case).
So: "it is re-searched, duplicate data is NOT captured, NEW data IS captured" — exactly.

- The URL registry records "searched this URL" (90-day TTL for `searched`; permanent
  for `dead`); the per-result `checked_at` records "this stored result was re-confirmed
  on date X".
- Idempotent: re-running an enricher never grows the list; a same-URL hit only refreshes
  `checked_at`.

Centralized function(s):
- `src/utils/result_dedup.py::dedup_entity(data, entity)` — pure; applies the entity's
  DEDUP_FIELDS declaration (collapse same-URL, refresh checked_at). Called from the
  central write path so NO enricher has to remember to dedup.
- The field->identity declaration lives with the schemas (one source of truth), so a new
  result array is deduped the moment it's declared.

## Consistency guarantees

- One function, one rule set, applied at one choke point -> identical behavior for every
  schema (the owner requirement). Adding an entity type = add its DEDUP_FIELDS row.
- Shares the URL normalizer with the URL registry, so "don't re-search this URL" and
  "don't store this URL twice" use the SAME canonical URL.

## Scope / sequencing

- Additive + behavior-only (collapses dupes on write); no schema version bump required
  (shape unchanged). Existing records with dupes are cleaned on next write-through; an
  optional one-pass sweep can dedup at rest.
- Independent of, but complementary to, the URL registry (search-dedup) and
  source_qualification (rating).

## Open decisions

1. Declaration location: a `DEDUP_FIELDS` map in `src/schemas/` (alongside the schema)
   vs a dedup block inside each schema dict. Recommend a single central map keyed by
   entity (easiest to audit for consistency).
2. Clean at-rest now (one sweep over existing output/) or only on write-through
   (lazy)? Recommend lazy + an optional sweep script.
3. Case where an entry has no url AND no id (free-form) — dedup on a canonical JSON
   hash of the entry, or leave as-is? Recommend canonical-hash fallback.
