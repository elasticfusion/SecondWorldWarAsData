# Provenance and integrity gates

**Status:** implemented. Verified against code at main `0dac86fa`. Line numbers are for that commit.

This page describes the checks that tell an operator whether the extracted data can be traced to its
source and whether it is internally consistent. It covers what each check proves, where it runs, what
it does on failure, where its output goes, and what to do about each alert. The per-check design
documents are [PHASE2_DATA_VALIDATION.md](../PHASE2_DATA_VALIDATION.md),
[PHASE3_DATA_VALIDATION.md](../PHASE3_DATA_VALIDATION.md) and [SEMANTIC_FLAGS_DESIGN.md](SEMANTIC_FLAGS_DESIGN.md).

## 1. The checks and what each one proves

The checks answer different questions. A record can pass one and fail another.

| Check | Code | Question it answers |
|---|---|---|
| Completeness | `src/utils/provenance_integrity.py` | Does every record carry a populated edge back to a source event or section? |
| Referential integrity | `src/utils/referential_integrity.py` | Does every stored cross-reference ID resolve to a record that exists? |
| Content truth | `scripts/validate_provenance.py` | Is the cited text really in the cited paragraph or note of the book? |
| Phase 2 validation | `scripts/validate_phase2.py` | Is each record schema-valid, version-matched, anchored to an event, and free of dangling references? |
| Phase 3 validation | `scripts/validate_phase3.py` | The Phase 2 checks again, plus enriched records name a source and coverage floors. |
| Systematic clusters | `src/utils/validation_stats.py` | Is one validation failure or semantic flag so common that the code, not the data, is wrong? |

### 1.1 Completeness: `provenance_integrity`

It proves a record has a provenance edge. A missing edge cannot dangle, so the referential check cannot
see it (module docstring, `provenance_integrity.py:1-18`). It also finds `TODO - Add ...` placeholders,
which are valid strings and pass every other guard.

The audit is schema-driven (`audit`, `provenance_integrity.py:197`). For each registry entity except
`events` and entities with no `output_glob` (`:209`), `_schema_provenance_kind` (`:45`) classifies the
strongest provenance edge the schema declares:

| Kind | Schema declares | Source |
|---|---|---|
| `event_mention` | `event_mentions` or `mentions` array | `:52` |
| `section` | `SourceSectionID` | `:54` |
| `event` | `EventID` or `Sub-eventID` | `:56` |
| `indirect` | only `PersonID` or `PlaceID` | `:58` |
| `none` | none of the above | `:60` |

A record passes when it has any populated edge (`_record_has_populated_provenance`, `:78`):
a non-empty `SourceSectionID`, a direct `EventID`/`Sub-eventID`, a nested `source` or `event_context`
object with one (casualties), or a mention that traces. A mention traces with an `EventID`. A mention
with `reference_type` of `footnote` or `endnote` also traces without one if it has `book`, `chapter`
and a locator (`verbatim_reference`, `reference_number` or a positive `paragraph_number`) (`:99-116`).
An ordinary entity mention must carry an `EventID`.

Records of `indirect` kind that have only a `PersonID` or `PlaceID` are counted as `indirect_only`, not
as missing (`:178`). That is weak provenance: the record resolves to another entity, not to the source.
Entities of kind `none` are listed in `structural_gaps` even when every record is fine (`:215`), because
the schema itself gives them no way to cite a source.

Placeholders are found by regex `TODO\s*-\s*Add` anywhere in the record, nested (`:42`, `:128`).
Unreadable record files are not dropped silently. Each raises `OBSERV provenance.record_unreadable`
(`:163`).

### 1.2 Referential integrity: `referential_integrity`

It proves every `*ID` reference field points at a record that exists. The per-write guard validates one
record in isolation, so it cannot see a `source_section.EventID` whose event was never written
(`referential_integrity.py:1-9`).

- **EDGES** (`:118`, built by `_discover_edges`, `:69`): derived from the registry schemas. An edge is
  any schema field whose leaf name is a known primary-key field (`PersonID`, `EventID`, ...), except the
  entity's own key, fields of `events`, and self-references. Fields named like `PeopleGroupID` are
  aliased to `people_groups` (`:27`); `Sub_eventID` is aliased to the event key-space (`:21`). Every edge
  is nullable: a null or empty value is not a reference (`:336`). A schema that fails to load raises
  `OBSERV referential.edge_discovery_skipped` because that entity's edges would otherwise go unaudited
  (`:88`).
- **PK sets** (`build_pk_sets`, `:195`; `_build_pk_sets`, `:306`): one set of valid keys per entity from the
  entity files, plus `EventID` and every `Sub-eventID` from `content/<book>/*-event.json`
  (`_add_event_pks`, `:207`).
- **Nested `source_section`** (`_NESTED_UNDER_CONTENT`, `:122`; `_record_files`, `:125`): source sections are
  not under `output/source_section/`. They are listed from `content/<book>/source_section/*.json`.
- **Single read** (`audit`, `:262`): each entity directory is read once and buffered. Unreadable records
  raise `OBSERV referential.record_unreadable` and `referential.records_skipped` (`:159-170`), so the audit
  never runs silently on a smaller corpus.
- **Result**: per-edge `total_refs`, `dangling`, `rate`; `total_dangling` overall. Sample IDs are
  included only when `include_samples=True`, and never on the alert path.
- **Repair** (`repair_dangling_subevents`, `:359`): when events were re-extracted, deterministic
  Sub-eventIDs can change and old entity mentions point at gone IDs. For `places`, `people_groups`,
  `dates`, `people` and `equipment` (`_MENTION_REPAIR_ENTITIES`, `:356`), a mention whose sub-event ID is
  stale but whose parent `EventID` resolves has the sub-event ID set to `null` (`_repair_record_subevents`,
  `:428`). A mention with no resolvable `EventID` is a real orphan and is left for the alert. The write is
  counted only if the record passes the central write guard (`_repaired_record_is_writable`,
  `:413`). If the event key-space is empty nothing is blanked (`:375`). Emits
  `OBSERV referential.subevent_repair repaired=N` (`:406`).
- **Enforcement** (`enforce`, `:449`, and `run_for_phase`, `:504`): repair first, then audit, then alert.
  `run_for_phase` picks S3 storage when `S3_BUCKET` is set, otherwise local (`:578`). It runs under a
  thread timeout, `PHASE_REFERENTIAL_ENFORCE_TIMEOUT` (default 90 s, `:526`), and can be skipped with
  `SKIP_PHASE_REFERENTIAL_ENFORCE=1` (`:519`). A skipped, timed-out or errored run returns
  `status="unknown"` (`_unknown_report`, `:552`). `unknown` means not measured. It is never a clean result.
  Only a completed scan returns `status="ok"`.

### 1.3 Content truth: `validate_provenance`

It proves the content behind a link is true: quotes occur in the cited paragraphs, and locators point
inside the cited sub-event. A record can pass 1.1 and 1.2 and still cite the wrong place
(`validate_provenance.py:1-8`). It runs per book against `output/content/<book>/*-event.json` and the
matching `-parsed.json` and `-endnotes.json` files (`load_book`, `:224`). A mention belongs to the book when
its `EventID` is one of the book's events (`:38-40`). An ID that resolves nowhere is left to the
referential check.

`HARD` findings fail the book. `WARN` findings are reported only.

| ID | Level | Fails or warns when |
|---|---|---|
| `P2-SRC` | hard | no readable event + parsed chapter files for the book (`:603`) |
| `P2-L1` | hard | a mention's sub-event belongs to a different event (`check_link`, `:283`) |
| `P2-L2` | hard | a cited paragraph is not in the chapter, or lies outside the cited sub-event (`check_locator`, `:304`) |
| `P2-Q1` | hard / warn | `original_text` is not in the cited paragraphs. Hard when it is elsewhere in the chapter (wrong location) or nowhere in the book. Warn on a near match only, or a quote under `MIN_VERIFY_WORDS` that is not found in place (`check_quote`, `:336`). A record-level quote (dates, logistics, casualties) is checked only when all the record's mentions are in one chapter, and only warns (`:443-460`) |
| `P2-Q2` | hard / warn | a bibliography mention's `verbatim_reference` is not in the cited chapter's notes (hard). Warn if the note text is empty, it sits under another note number, or the mention has no locator (`check_note`, `:382`) |
| `P3-B1` | hard / warn | a bibliography entry of the book is a stub (`ibid`, `op. cit.`, bare short number, empty, `Unknown`): hard. Classed `not_citation`: warn (`check_bib_source`, `:487`) |
| `P3-R1` | hard / warn | empty `license`: hard. `unknown` license, no `copyright_status`, or a NARA source with no `archive_reference_number`: warn |
| `P3-A1` | hard | an enriched people, people_groups or equipment record has no source link (`check_attribution`, `:508`) |
| `P3-C1` | warn | a note cited by a sub-event that names a source has no bibliography mention. One finding per chapter with counts (`check_citations_resolve`, `:517`) |

`--phase 3` runs all the `P2-*` checks again plus the `P3-*` checks, because Phase 3 merges and dedups
records and can break a link (`validate`, `:596-621`). Images, maps, map features, source sections and
events cite no book quote and are skipped (`_NOT_TEXT_CITING`, `:88`).

### 1.4 `validate_phase2.py` and `validate_phase3.py`

Both print `OBSERV phase2.validation` or `phase3.validation` with `result=PASS|FAIL`, then `WARN` and
`FAIL` lines. A crashing check counts as a hard failure (`validate_phase2.py:271-277`). Exit 1 means at
least one A failure.

| ID | Level | Check |
|---|---|---|
| A1 | hard | `_schema_version` is present and equals the target version (`validate_phase2.py:94-103`) |
| A2 | hard | record parses and is schema-valid (required fields, `additionalProperties`) (`:104-106`) |
| A3 | hard / warn | people, places, people_groups, equipment, dates, casualties have at least one `event_mention`, each with an `EventID` (hard). An empty `original_text` or `book` is a warning (`:139-154`) |
| A3/B1 | hard | `referential_integrity.audit` reports 0 dangling. If the audit cannot run, that is a failure, not a pass (`:157-175`) |
| A4 | hard / warn | an enriched person has a `wikipedia_url` or `grokipedia_url` (hard). Empty `biography_sources` warns. 0 enriched of N warns, or fails with `--strict-enrichment` (`:197-229`) |
| B3 | warn | an `index.json` entry points at a missing file (`:233-249`) |
| P3-A5 | hard | a record with `enrichment_status == "enriched"` (people, people_groups, equipment) has a source link, is schema-valid, matches the version, and has at least one `event_mention` with an `EventID` (`validate_phase3.py:85-150`) |
| P3-B4 | warn | 0 of N places have coordinates; 0 of N bibliography records resolved; 0 of N weather records have `noaa_observed`. `--strict-coverage` makes them hard (`:168-205`). OpenSERP coverage is not gated (`:23`) |

`validate_phase3` runs A1/A2, A3, A3/B1, P3-A5, P3-B4 (`:217-223`). `validate_phase2` runs A1/A2, A3, A3/B1,
A4, B3 (`:264-270`). The `P3-A1` check in 1.3 and `P3-A5` here use the same predicate,
`entity_records.has_source_link` (imported at `validate_provenance.py:63-71` and `validate_phase3.py:53`).

### 1.5 Systematic clusters and the Phase 3 deferral

`validation_stats` counts every write outcome by (entity, outcome, keyword, version state). At the end of
a phase `write_validation_stats` (`validation_stats.py:278`) turns repeated concerns into clusters. The
concern outcomes are `block`, `allow_empty` and `allow_flagged` (`:39`). A cluster is systematic when
either rule holds (`_qualifies_as_cluster`, `:200`):

- large batch: count at least 10 and at least 5% of the entity's writes (`:43-45`);
- small batch: count at least 3 and at least 90% of the entity's writes (`:54-58`).

A systematic cluster means the extraction, dedup or schema code is wrong, so reprocessing will not fix it.
The semantic flags that feed `allow_flagged` come from `src/utils/semantic_flags.py`: `geo_missing_coords`,
`geo_impossible_coords` (places); `date_out_of_window`, `date_unparseable` (dates); `citation_stub`,
`citation_unresolvable` (bibliography) (`_OWNED_FLAGS`, `semantic_flags.py:217`).

**Deferral.** Phase 2 writes places without coordinates on purpose, and Phase 3 geocodes them. The set
`RESOLVED_IN_PHASE3 = {"geo_missing_coords"}` (`semantic_flags.py:213`) is passed by Phase 2 as
`deferred_flags` (`phase2_extract.py:1158-1162`). A deferred flag code is not counted as a cluster and so
does not alert (`validation_stats.py:224-227`), but it is still in the JSON breakdown. Phase 3 calls
`write_validation_stats(args.output_dir)` with no deferral (`phase3_enrich_data.py:1050`), so the same flag
alerts after Phase 3, where it is a real failure.

## 2. Where each check runs

| Check | Phase 1 | Phase 2 | Phase 3 |
|---|---|---|---|
| Referential `run_for_phase` | end of `main()` (`phase1_parse.py:572`), any storage | local runs: `phase2_extract._local_referential_enforce` (`:654`, call at `:1483`). ECS: skipped there, runs post-sync in `ecs_entrypoint` | local runs: `phase3_enrich_data.py:1063`. ECS: post-sync |
| Completeness `run_for_phase` | no | `phase2_extract.py:1488`, always, on the local tree, before the yield gate | local runs: `phase3_enrich_data.py:1069`. ECS: post-sync (`ecs_entrypoint.py:2344`) |
| `validate_phase2` | no | `phase2_extract.py:1392`, log-only (the phase is not aborted) | no |
| `validate_phase3` | no | no | local runs: `phase3_enrich_data.py:1075`. ECS: post-sync (`ecs_entrypoint.py:2366`). Log-only |
| `validate_provenance` | no | ECS only: `_post_process` (`ecs_entrypoint.py:2415`) | ECS only: after the durable fix (`:2457`) |
| `write_validation_stats` | no | end of `_run_post_extraction_steps` (`phase2_extract.py:1162`) | `phase3_enrich_data.py:1050` |

**Why ECS runs differ from local runs.** On ECS the task's output is uploaded to S3 only after the phase
process exits (`_final_sync`). A referential audit inside the phase process would read a partly synced S3
corpus and report false danglers. It would also see a local tree that lacks `output/content/`. So with
`S3_BUCKET` set, the phase scripts skip the in-process referential and Phase 3 validation
(`phase2_extract.py:1482`; `phase3_enrich_data.py:1061`) and `ecs_entrypoint` runs them after the
sync. Without `S3_BUCKET` (a local run), the local tree is authoritative and the phase scripts run them.

**ECS sequence.**

1. After any Phase 2 exit, even a failed one, `_audit_failed_phase2` runs the post-sync referential repair
   and audit (`ecs_entrypoint.py:2154`, called at `:1051`, `:4522`, `:4901`). A halted Phase 2 (for example
   the yield gate's `fail_stop`) leaves a partial corpus that an operator needs to inspect.
2. After a successful Phase 2, `_post_process` (`:2324`) runs `_post_sync_referential_enforce("Phase 2
   (post-sync)")` (`:2365`), then `_provenance_gate("2")` (`:2366`), then dedup detection, then
   `_phase3_gate_decision` and `_notify_dedup_gate` (`:2385-2387`).
3. After a successful Phase 3, `_phase3_durable_referential_fix` (`:2280`) runs: the post-sync referential
   repair and audit; the completeness audit; then it refreshes the local corpus from S3
   (`_refresh_validation_corpus`, `:2147`) and runs `validate_phase3`. The refresh downloads
   `output/content/` and re-downloads, with overwrite, the entity directories the repair mutates, because
   the local copies predate the S3-side repair (`:2164-2170`). The repair is durable here because nothing
   syncs over it afterwards.
4. Then `_provenance_gate("3", refresh=not refreshed)` runs (`:2457`). It reuses the corpus the previous step
   refreshed. If that refresh failed, the gate refreshes for itself.

`_post_sync_referential_enforce` emits `OBSERV integrity.post_sync_audit` (`:2113-2142`). `status=ok` only
when the audit completed (`referential_status=ok`). Otherwise `status=warn`.

## 3. Gate modes

`provenance_validation.gate` in `config.yaml` (`:106-107`) is `warn` or `block`. The `PROVENANCE_GATE` env
var overrides it, with no rebuild (`gate_mode`, `validate_provenance.py:578-593`). Anything else, or
nothing set, means `warn`. If the mode cannot be read at all, `ecs_entrypoint._provenance_gate_mode`
returns `block` (`:2239-2248`): a gate that cannot load its mode is closed.

The gate is also fail-closed on a run that cannot complete. If the validation raises, `_provenance_gate`
returns `(False, "provenance validation could not run (...)")` (`:2198-2205`). Under `warn` that is
alerted but the book goes on. Under `block` it holds the book.

| | `warn` | `block` |
|---|---|---|
| Report written and uploaded | yes | yes |
| `OBSERV provenance.validation status=error` (hard failure) | yes | yes |
| Phase 2 -> Phase 3 | continues. The dedup-gate reason says `gate=warn: not held` (`:2274-2275`) | held: the gate action is `block` and `held_by` includes `provenance` (`:2277-2278`). The dedup gate notice goes out with a provenance subject. In multi-doc mode the doc is set to `needs-review` (`:2396`) |
| End of Phase 3 | the doc advances to `done` (`_advance_doc_lifecycle("3")`, `:2460`) | `_phase_end_held` sends an alert (`:2251-2262`) and the doc is set to `needs-review` (`:2458`). The lifecycle is not advanced. Queued enrichment for the next book still proceeds |
| Warnings (`WARN` findings) | never hold | never hold |

Only hard (`fail`) findings fail a book. The completeness audit, the referential audit, `validate_phase2`
and `validate_phase3` are not part of this gate: they log and alert but never hold a phase.

To release a held book: fix the records and re-run, or set `PROVENANCE_GATE=warn` and re-trigger.

## 4. Outputs

### 4.1 Reports and files

| Output | Location | Written by |
|---|---|---|
| Provenance report, per book and phase | `output/reports/provenance/<book>-phase<N>.json` locally; `s3://<bucket>/output/reports/provenance/<book>-phase<N>.json` (`report_key`, `validate_provenance.py:633`; upload `ecs_entrypoint.py:2271`) | `validate_and_record` |
| Validation stats | `output/metrics/validation_stats.json` (`validation_stats.py:295`) | `write_validation_stats` |
| Phase 2 / 3 validation summary | stdout and the task log only (`OBSERV phase2.validation`, `phase3.validation`) | `validate_phase2.run`, `validate_phase3.run` |
| Completeness and referential audit result | task log (`OBSERV` lines, below). No report file | `run_for_phase` |

The provenance report has `book`, `phase`, `result`, `checked`, `failed`, `warned` and `findings`
(`Findings.to_json`, `validate_provenance.py:255-264`). Each finding has `level`, `check`, `where`
(`<entity>/<file>#<MentionID>`), `reason`, and detail fields. Quotes are cut to 120 characters
(`_snippet`, `:267`).

### 4.2 OBSERV events

| Event | Level | Meaning |
|---|---|---|
| `provenance.audit` | info | completeness summary: `missing`, `placeholders`, `structural_gaps` |
| `provenance.entity` | warn | per entity with a missing edge, a placeholder or kind `none`, with up to 3 sample file names |
| `provenance.alert` | warn | any missing, placeholder or structural-gap count above 0 (`provenance_integrity.py:285`) |
| `provenance.record_unreadable` | warn | a record was skipped by the completeness audit |
| `provenance.validation` | ok / error | content-truth result for a book and phase, with `failed`, `warned`, `report=`. `error` on a hard failure |
| `provenance.gate_timing` | info | seconds taken by the gate (`ecs_entrypoint.py:2257`) |
| `provenance.validation_skipped` | warn | no `BOOK_NAME`, so no per-book validation ran (`:2181`) |
| `provenance.validation_failed` | error | the validation could not run, so the gate failed closed (`:2196`) |
| `provenance.report_upload_failed` | error | the validation ran but the report is only in the task's local tree (`:2230`) |
| `provenance.gate_mode_unavailable` | error | the mode could not be read, so the gate is treated as `block` (`:2247`) |
| `provenance.chapter_unreadable`, `provenance.duplicate_event_id` | warn | a chapter or event file was skipped by `validate_provenance` (`:184`, `:190`) |
| `referential.audit` | warn / info | `total_dangling` and edge count. `status=warn` when above 0, `ok` when 0 (`referential_integrity.py:479-485`) |
| `referential.subevent_repair` | info | number of stale sub-event references set to null |
| `referential.record_unreadable`, `referential.records_skipped`, `referential.event_file_unreadable`, `referential.event_files_skipped`, `referential.edge_discovery_skipped` | warn | the audit ran on an incomplete corpus or edge map |
| `integrity.post_sync_audit` | ok / warn | the ECS post-sync referential audit, with `referential_status` and `dangling` |
| `integrity.local_audit` | warn | the local referential enforcement raised (not measured) |
| `validation.summary` | info | write outcome totals for the phase |
| `validation.systematic` | warn | one per systematic cluster, with entity, outcome, keyword, count, total, permille |
| `phase2.validation`, `phase3.validation` | info | `result=PASS|FAIL`, `failures`, `warnings` |

Any `status=error` OBSERV line is also published to SNS as `WWII Pipeline: OBSERV error — <scope>.<event>`
(`observe.py:135-149`). It is limited to one per `scope.event` per 300 s (`OBSERVE_SLACK_DEDUP_S`), and
`OBSERVE_SLACK=0` turns it off (`observe.py:81-87`). The limit is per process. Each phase runs in its own ECS task, so a Phase 2 and a Phase 3
`provenance.validation` error each alert.

### 4.3 SNS alerts and delivery

All alerts go through `observe.publish_alert` (`observe.py:100`) to the topic in `NOTIFICATION_TOPIC_ARN`,
which is `{env}-wwii-phase2-complete` (`cloudformation/events.yaml:56`). Subjects are cut to 100
characters. The topic has an email subscription (when `NotificationEmail` is set, `events.yaml:58-64`) and
the Slack formatter Lambda `{env}-wwii-slack-formatter` (`events.yaml:90-101`), which republishes to the
`{env}-wwii-slack` topic that Chatbot watches. The formatter always adds a "View logs" link
(`lambda_handlers/slack_formatter.py:107-115`). If no topic is set (local runs), `publish_alert` returns
False and only the OBSERV log line carries the signal. Two alerts publish with
`sns.publish` directly instead: the dedup gate (`_notify_dedup_gate`) and the systematic-failure alert
(`validation_stats._alert_systematic`). Messages hold counts and IDs, never record text.

## 5. Alert reference

| Subject | Meaning | First action |
|---|---|---|
| `WWII Pipeline: DATA QUALITY — provenance anomalies` | The completeness audit found records with no source edge, `TODO - Add` placeholders, or an entity whose schema has no source edge. Counts and phase are in the body (`provenance_integrity.py:300-326`). | Find the entities in the `OBSERV provenance.entity` log lines, which list sample file names. Open those records. A missing edge usually means the extraction step did not write `event_mentions` or `SourceSectionID`; fix that step and re-run it. For a structural gap, add a source edge to the schema |
| `WWII Pipeline: DATA QUALITY — dangling cross-references` | A record points at an ID that does not exist. The body lists edges as `dangling/total` (`referential_integrity.py:599-620`). | Check whether a Phase 2 run was re-extracted or halted. The repair already blanks stale sub-event IDs, so the remainder are real orphans. Locate the referrer using the edge name (`<entity>.<field> -> <target>`) and re-run the phase that writes the target, or fix the referrer. Run `scripts/utils/referential_integrity_audit.py` locally for sample IDs |
| `WWII Pipeline: DATA QUALITY — systematic validation failures` | One validation failure or semantic flag hit the cluster thresholds (section 1.5). It is a code defect. The body lists clusters and points to `output/metrics/validation_stats.json` (`validation_stats.py:357-396`). | Read `validation_stats.json` for the entity and keyword. Fix the extraction, dedup or schema code for that entity. Do not reprocess first, because it will reproduce the failure |
| `WWII Pipeline: OBSERV error — provenance.validation` | A hard content-truth check failed for a book: a quote not in the book, a wrong-location quote, a bad locator, a stub source, a missing license, or an un-attributable enriched record. | Open `output/reports/provenance/<book>-phase<N>.json` (path is in the alert's `report=` field). Sort `findings` by `check`. Fix the extractor or the record, then re-run the phase. With gate `warn` nothing is held, so review before the data is published |
| `WWII Pipeline: OBSERV error — provenance.validation_failed` | The validation could not run (missing book files, an exception). It is treated as a failure. | Check the task log for the exception type in the alert. Confirm `output/content/<book>/` has event and parsed files. Re-run the phase |
| `WWII Pipeline: OBSERV error — provenance.report_upload_failed` | The validation ran, but its report was not uploaded to S3. | Check the task role's S3 write permission. The report is in the task's local tree only and is lost when the task ends, so re-run the validation (`python -m scripts.validate_provenance --book <slug> --phase <N>`) |
| `WWII Pipeline: OBSERV error — provenance.gate_mode_unavailable` | The gate mode could not be read, so the gate is closed. | Check `config.yaml` and the `PROVENANCE_GATE` value. Set `PROVENANCE_GATE=warn` or `block` explicitly |
| `WWII Pipeline: provenance review needed` (also `dedup + provenance review needed`) | Gate mode is `block` and the Phase 2 validation failed. Phase 3 is held (`ecs_entrypoint.py:2663-2689`). | Open the report named in the body. Fix the records and re-run Phase 2, or set `PROVENANCE_GATE=warn` to release |
| `WWII Pipeline: provenance review needed (Phase 3)` | Gate mode is `block` and the Phase 3 validation failed. The doc is set to `needs-review` (`:2251-2262`). | Same as above. Fix the records and re-run Phase 3, or set `PROVENANCE_GATE=warn` |
| `WWII Pipeline: dedup review needed` / `dedup auto-proceed` | The dedup gate, not a provenance check. Included here because it shares the notice path. | See the Dedup Review UI link in the body |

## 6. Limits and details to know

- `validate_phase2` and `validate_phase3` do not publish an SNS alert and do not stop a phase. Their
  failures appear only as `OBSERV phase2.validation` / `phase3.validation` log lines. PHASE2_DATA_VALIDATION.md
  lists the SNS alert on FAIL as not implemented.
- `validate_phase2` runs inside the Phase 2 process against the task's local output root, with or without
  `S3_BUCKET` (`phase2_extract.py:1392-1394`). On ECS that tree is not yet synced, so its A3/B1 result can
  differ from the post-sync audit. The `integrity.post_sync_audit` result is the authoritative one. (Not
  verified against a live run.)
- The completeness audit's Phase 2 run reads the local tree, and `ecs_entrypoint` runs it post-sync for
  Phase 3 only (`:2295`). There is no post-sync completeness audit for Phase 2 on ECS.
- Whole-corpus audits outside a phase are `scripts/utils/corpus_audit.py` (`src/utils/corpus_audit.py`) and
  `scripts/utils/referential_integrity_audit.py`. Both are read-only. This page does not describe them.
- Tests: `tests/test_validate_provenance.py`, `tests/test_provenance_gate.py`,
  `tests/test_provenance_hardening.py`, `tests/test_provenance_audit_entity_shapes.py`,
  `tests/test_referential_integrity_audit.py`, `tests/test_referential_subevent_repair.py`.
