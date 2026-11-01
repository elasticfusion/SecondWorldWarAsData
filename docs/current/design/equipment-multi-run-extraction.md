# Design: multi-run equipment extraction (broad grab → refine → disambiguate)

**Status:** DESIGN (2026-11-01, owner-approved option A). Replaces the single mega-prompt
`equipment.yaml` call with a recall→precision decomposition. Owner constraints: **cost is not a
concern (batched); provenance must ALWAYS be maintained; simplify the language.**

## Problem (why the single prompt under-performs)
`equipment.yaml` grew to ~15 rules / ~10KB, heavily weighted toward ATTRIBUTION (country/enemy/
italics/ownership). One call must simultaneously (a) find EVERY item, (b) use SPECIFIC names, and
(c) attribute each correctly — and does none well: ~18 of ~50 found, and specifics genericized
("aircraft" not "B-24"; calibers collapsed). Recall and precision are fighting in one prompt.

## Decomposition (3 runs; Run 3 already exists)

### Run 1 — BROAD GRAB (recall-max). New prompt `equipment_extract_broad.yaml`.
Dead-simple, single purpose: *list EVERY equipment reference in the text, verbatim, with its
source quote. Do NOT categorize, attribute, or dedup. It is far better to over-include than miss.
A single paragraph may name 5–15 items. Sweep every sentence.*
- Output per item: `{ name (as written), original_text (verbatim — MANDATORY provenance anchor),
  paragraph_numbers }`. Nothing else.
- Specificity rule: if the source names a specific model/caliber/designation (B-24, 155-mm gun,
  LCT), capture it VERBATIM — never genericize. Distinct calibers/marks = distinct items.
- No country, no category, no presence — those are Run 2's job. This keeps the prompt tiny and the
  model's whole attention on NOT MISSING anything.

### Run 2 — REFINE (precision, per item). New prompt `equipment_refine.yaml`.
Input: ONE Run-1 item (name + its original_text) + minimal surrounding context. Output: the full
`EquipmentExtraction` shape — category/subcategory, country_of_origin/operating_country (the
attribution rules move HERE), presence/absence_reason, quantity, etc. One focused item at a time →
attribution done well without recall pressure.
- NEVER-DROP GUARANTEE: Run 2 may reclassify but MUST emit a record for every Run-1 item. If it
  can't classify, emit with category "other" + a loud OBSERV `equipment.refine_uncertain` — never
  silently discard (anti-swallow). A Run1-count vs Run2-count mismatch is a loud anomaly.
- Provenance: carries Run-1's original_text through unchanged.

### Run 3 — DISAMBIGUATE (canonical identity), BATCHED. EXISTING `equipment_disambiguation.yaml`.
`_resolve_canonical_identity` stamps canonical_name/identity_source; feeds the (numeric-conflict-
guarded, #651) merge. Owner: DISAMBIGUATION IS BATCHED TOO — it is naturally per-item, so it is
Batch Pass C (one request per refined item). The Grok client ALREADY batches any extract_json call
automatically when batch_mode=True (queues via BatchModeCollecting), so the change is ORCHESTRATION
(run it as a batch pass with stable-key reassembly), not a new call path. Same never-drop + stable-
key (EventID+item) reassembly as Run 2.

## Batch-mode interaction (owner: this WILL be batched — BOTH runs)
BOTH Run 1 and Run 2 batch. Run 2 is the BETTER batch workload: it fans out to ONE request PER
Run-1 ITEM (many small independent requests = ideal batch). Flow:
- Batch Pass A: equipment_extract_broad, 1 request per EVENT -> raw item lists.
- Batch Pass B: equipment_refine, 1 request per ITEM (fan-out of Pass-A items) -> refined records.
- Batch Pass C: equipment_disambiguation, 1 request per ITEM -> canonical identity, then merge+persist.
All three leverage the client's automatic batch_mode queueing; the per-item passes (B, C) use stable-
key reassembly (NOT positional, #637) + never-drop.
Reassembly MUST be by a STABLE per-item key (EventID + item-index/hash), NOT positional — the xAI
results API does not echo custom_id (the #637 positional-desync class). NEVER-DROP: every Pass-A
item must return a Pass-B refinement; a Run1-count != Run2-count is a loud OBSERV
equipment.refine_missing. Deterministic cache keys per run (cache_type equipment_broad /
equipment_refine + EventID + item key).
---
## (superseded note) Batch-mode interaction
Batch mode is COLLECT-then-RETRIEVE (async), so Run 2 cannot synchronously consume Run 1 within one
batch pass. Two clean options:
- **(preferred) Two batch passes:** Pass A submits all Run-1 (broad) requests; on retrieve, Pass B
  submits Run-2 (refine) requests seeded by Run-1 results. The pipeline already does multi-pass
  batch (collect/retrieve) — add a Run-1→Run-2 stage boundary. Deterministic cache keys per run
  (EventID + run-tag) as today.
- Synchronous mode runs Run1→Run2 inline (trivial). Keep both paths working (BATCH_PHASE2 switch).

## Named equipment: official vs unofficial (owner 2026-11-01)
- OFFICIAL names ARE the identity: a capital ship / commissioned vessel's name is its identifier
  (Quincy/Barton/Talybont) → common_name = the ship name, category naval.
- UNOFFICIAL per-unit nicknames (aircraft nose-art, tank names) are NOTED, not the identity:
  "Memphis Belle" is a B-17F → common_name "B-17 Flying Fortress" (TYPE = identity),
  alternate_names ["Memphis Belle"]. Keeps the airframe grouped with its type, not orphaned.
- Reuses existing schema fields (common_name / technical_identifier / alternate_names) — NO schema
  change. Handled in Run-2 refine. Run-1 broad must preserve the nickname verbatim so Run-2 can note it.

## Context for attribution: original_text + extended_text (owner 2026-11-01)
A tight quote ("a tank sally") often lacks the clue needed to attribute (German vs American,
captured, operator, place) — the clue is in the neighboring paragraphs. So each item carries TWO
verbatim fields:
- `original_text` = the TIGHT quote the reference came from (the provenance anchor; existing field).
- `extended_text` = the reference's paragraph PLUS the paragraph BEFORE and AFTER (n-1, n, n+1).
Assembly: Run-1 broad emits `paragraph_numbers`; CODE builds `extended_text` deterministically from
the parsed-paragraph lookup (`_reconstruct_fulltext`'s {absolute_number: text}) — VERBATIM, NOT
LLM-reproduced (avoids paraphrase). parsed_file is already passed to the optional-entities pass.
Run-2 refine consumes BOTH (text = anchor, extended_text = attribution context) so it can resolve
nationality/captured/operator instead of guessing from a 5-word quote.
PERSIST extended_text on the record (owner: keep — helps diagnose attribution errors + enables
future re-refinement without re-reading the source). Schema adds `extended_text` (nullable string),
versioned bump when implemented.

## Provenance (owner: ALWAYS)
original_text is MANDATORY at Run 1 and preserved verbatim through Run 2 and Run 3. No run may
produce an item without its source quote. The event_mention/PlaceID linking (Run-2/downstream)
is unchanged.

## Simplification (owner)
Each prompt is SHORT and single-purpose (Run 1 ≈ 10 lines; Run 2 ≈ the attribution rules, now
un-crowded). The current mega-`equipment.yaml` attribution rules (enemy-perspective, italics,
ownership-from-context) MOVE to Run 2 where they belong, and can be tightened since they no longer
compete with the recall task.

## Rollout / validation
1. Build Run-1 + Run-2 prompts + orchestration (sync path first, then batch two-pass).
2. A/B on USA-E-XChannel-8 vs the ~50 gold list: target recall >> the single-prompt ~18.
3. Keep the numeric-conflict merge guard (#651) + the schema (2.27) fix — this is extraction-only.
4. Generalize the pattern (broad→refine) to casualties/logistics later if it wins.

## Simplification outcome (owner: option A — LEAN)
- The 269-line single `equipment.yaml` mega-prompt is REPLACED by broad (49) + refine (84) = 133
  lines — ~half the size, recall/precision separated.
- `equipment.yaml` is RETIRED when the orchestration lands (nothing calls it once broad→refine is
  wired). Do NOT gut it before then — it is the live prompt until the orchestration PR.
- DROPPED/DEFERRED from refine (option A — these are low-frequency rich-detail, NOT part of the
  ~50-item coverage goal): crew_accounts, environmental_performance, related_equipment,
  supporting_units, images. If wanted later, a SEPARATE optional enrichment pass — not every refine.
- KEPT in refine (high-value attribution, condensed): category/subcategory, specific-name-preserve,
  country_of_origin/operating_country + ownership-from-context + narrator-relative enemy + italics,
  presence/absence_reason, quantity, place_name, multi-modal ship/aircraft routing + platform type/
  TYPE_UNRESOLVED, official-vs-unofficial name identity.

## Non-goals
Not changing Run-3 disambiguation, the merge, the schema, or the place back-fill (separate design).
