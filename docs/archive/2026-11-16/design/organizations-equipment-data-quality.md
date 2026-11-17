# Design: organizations & equipment data-quality — findings + plan (2026-10-31)

Driven by the gold-standard review of USA-E-XChannel-8 (equipment ~50 real vs 7 extracted; groups
deterministic 74 vs 22 persisted) and a series of owner domain insights.

## GOVERNING PRINCIPLE (owner, 2026-10-31) — applies to EVERY entity

> **An unknown field value does NOT invalidate the fact. Capture the entity, record what the
> source states (even if partial/approximate), and MARK the unknown for deterministic/supplemental
> resolution — NEVER drop the entity and NEVER guess the value.**

Already applied this session: equipment TYPE_UNRESOLVED (PR-3a); absence_reason "unstated" (PR-2);
enemy nationality → mark Axis + null country, don't guess German (#654); ship type → supplemental,
don't drop (PR-3a). Now extended to task-force DATES (below).

## SHIPPED THIS SESSION (merged)
- #649 generics in scope + enemy→theater; #650 enemy = narrator-relative; #651 fuzzy over-merge
  guard (distinct calibers no longer merged); #652 equipment absence/presence; #653 multi-modal
  platforms + ownership-from-operational-context (prompt).
- #654 (OPEN, reviewed-pending): italics = enemy TACTICAL FIELD formation — NARROW, corpus-verified
  (numbered/named field formations ~100% italic/0% plain); EXCLUDES service branches (Luftwaffe),
  high-commands/staff (OKW/OKH/OB WEST/WFSt — classify by vocabulary), ALLIED units (never italic),
  ship names, and citation/title italics.

## CONFIRMED OPEN BUGS (groups side — interlocking, cause the 74→22 undercount)

### BUG 1 — Group silent drop (naval task forces Force U/O/B)
Model EMITS Force U/O/B (logs: Force U ×4, Force O ×5, Force B ×4) but NONE persist to
output/people_groups/ — NO skip/merge/relevance log line (SILENT, the #1-concern class). NOT dedup
(no Force merge), NOT a relevance filter. Drop is at validation/persist. Same class as the equipment
name/common_name (#634) and Equipment-key (#645) bugs. The constituent standing units (1st Division,
29th Division) DID persist — only the parent FORCE was lost.

### BUG 2 — Group persist discards most of the schema
Persisted group records keep only {name, group_name, group_type, nationality, source_language,
GroupID, event_mentions}. The prompt asks for date_range, parent_organization, sub_organizations,
aliases, alliance_membership, military_hierarchy, member_countries — ALL DROPPED at persist. So even
when the model captures a date or a compositional link, it is thrown away. Prerequisite to any
temporal/compositional modeling landing.

## DESIGN — ephemeral mission-scoped organizations (naval task forces, and similar)

Owner insight: naval task forces (Force U/O/B) are EPHEMERAL and DATE-BOUND — assembled for a
mission (deliver VII Corps / 1st Div / 29th Div to the beaches), then dissolve / stand by for
evacuation. Fundamentally different from a STANDING army formation (1st Infantry Division existed for
years). The model must represent this:
1. `organization_lifecycle`: `standing` | `ephemeral` (task force, assault force, provisional
   command). group_type may stay military_unit; lifecycle is the new axis.
2. `active_period`: { start, end, precision: exact|approximate|unresolved, original_text }.
   Per the GOVERNING PRINCIPLE, dates MAY be approximate ("D-Day", "the assault phase", "5–7 June")
   or unresolved — capture the force + its temporal NATURE regardless; flag unresolved dates for
   SUPPLEMENTAL resolution (mirror ship-type TYPE_UNRESOLVED). An unknown date never drops the force.
3. Date-bound COMPOSITIONAL link: "Force O (1st Division)" = the force DELIVERED/CONTAINED the
   standing unit for that operation. Force O.sub_organizations ⊇ 1st Division;
   1st Division.parent_organization (for that op) = Force O. Time-bounded membership.
4. Force O ≠ 1st Division — must NOT be deduped/flattened into the standing unit it carried.

## OBSERVABILITY — a first-class ACCEPTANCE CRITERION for every fix (anti-swallow principle)

The session anti-swallow rule — every anomaly/failure/degraded-fallback/empty-yield is a LOUD
canonical `OBSERV <scope>.<event> status=error|warn` AT THE POINT OF LOSS; never silent, never
count=0-as-ok — is the ROOT of why all the above bugs survived (silent drops, discarded fields, a
misleading produced-count, blind entities). Every fix below MUST satisfy:
1. POINT-OF-LOSS OBSERV: any dropped/merged/discarded entity or field emits a loud OBSERV at the
   drop site (e.g. `OBSERV people_groups.dropped status=warn group=... reason=...`,
   `OBSERV people_groups.field_discarded ...`). A loss we can't prevent must at least be observable.
2. UNKNOWNS ARE COUNTED: a captured-but-unresolved value (TYPE_UNRESOLVED, unresolved date, null
   Axis country) emits `OBSERV <scope>.unresolved ...` so "flagged for resolution" is visible, not
   silent absence.
3. COUNTS MEASURE PERSISTED REALITY: `yield.<entity>` must count PERSISTED records, not the pre-drop
   model list (the `produced=7 verdict=ok` while 3 persisted false-OK must not recur).
4. NO BLIND ENTITIES: every entity gets a deterministic baseline so undercounts self-report via
   `yield.baseline` (groups 74→22 WORKED; equipment baseline=0 and logistics/casualties no-baseline
   were BLIND). This is how undercounts get caught without a human gold-standard pass.

## EXECUTION ORDER (planned)
1. Close #654 (italics) — independent review + merge. [reviewed work, don't dangle]
2. BUG 1 + BUG 2 together (group silent-drop + persist-schema-discard) — trace model→validate→persist
   for Force U/O/B, fix the drop, stop discarding date/compositional/alias fields. Silent data loss
   = top priority. Investigate→fix→test→review.
3. Ephemeral-org model (lifecycle + active_period with precision + date-bound composition +
   no-flatten) — schema + prompt + persist + dedup-guard. Depends on BUG 2 fix.
4. PR-3b deferred: cross-entity dual-write (platform/absence → logistics & people_group) +
   supplemental type/date resolution. Owner dual-write decision still open.

## ALSO OPEN (earlier findings, not groups)
- LOGISTICS / CASUALTIES silently weak (no deterministic baseline → can't flag undercounts; logistics
  1, casualties 2 on a D-Day chapter). Add baselines.
- Roman-numeral designations (Mark IV vs VI) not caught by the numeric-conflict fuzzy guard (#651).
- PR-3b dual-write decision (option a dual-write vs c reason-only).
