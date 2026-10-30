# Pipeline Backlog

**Last Updated:** 2026-10-29 (Pattern-8 observability P1 remediation A-E shipped + deployed; Slack Chatbot fixed to us-east-1; P2/P3 sweep + 3 review follow-ups + GIT_SHA gap logged below).

---

## ⭐ OPEN ISSUES (at a glance — highest priority first)

The curated list of what is still OPEN. Each points to its detail section lower in this file.
Everything not listed here is DONE (see §7 COMPLETED + the dated review sections).

### HIGH
- **[POLICY · provenance] Provenance-completeness stays NON-FATAL (owner 2026-10-30): escalate LOUD to Slack, do NOT
  fail_stop.** Rationale: many provenance misses are NON-DETERMINISTIC (LLM ULID variance) and a RE-RUN fixes them; a
  fail_stop would halt a book a retry would heal. Reserve hard failure for DETERMINISTIC causes (prompt/code bugs like the
  one above). Action: provenance_integrity enforcement -> wire the SNS/Slack alert (provenance.alert already emits; ensure
  it reaches the notify topic) + a runbook: on a provenance alert, first RE-RUN; if it persists across runs, it's
  deterministic -> hunt the prompt/code bug.
  **STATUS 2026-10-30: Slack escalation is ALREADY LIVE** — provenance_integrity._alert publishes to
  NOTIFICATION_TOPIC_ARN; verified forwarded ('Forwarded to Slack: :warning: DATA QUALITY — provenance anomalies' at
  22:51/23:24/00:19/08:05). Non-fatal confirmed. Remaining: write the operator runbook doc.
- **[HIGH · data-loss · BATCH MODE] Batch places (and likely any entity) silently dropped by the positional result-mapping desync.**
  Overnight batch run (XChannel-8 2026-10-30): places persisted 0 while SYNC mode got 96. EVIDENCE: the cached batch
  places response is VALID JSON with 8 sub-events x 119 place objects (Grok extracted them fine); yet 0 persisted AND
  there is NO `OBSERV xref.seid entity=places` line (dates/groups/people each show exact=8 and persisted) — so
  `_process_batch_response` NEVER ran for places. ROOT CAUSE: xAI batch results carry NO echoed custom_id, so
  grok_client.py:600 maps results to our cache keys PURELY BY POSITION (`zip(submitted, ordered)` + retrieve_results_ordered).
  This run had MULTIPLE sub-batches + several empty `Response: 2 chars` ({}) results interleaved — the positional zip
  desynced, so the places slot received an empty/wrong result while the real 119-place content sat in cache uncorrelated.
  The observability HALF-caught it (`phase2.summary.zero scope=core entity_types=places — probable bug, verify`) but the
  yield gate could NOT (places deterministic baseline=0). IMPACT: batch mode is NOT equivalent to sync — it can silently
  drop an entire entity's extraction. FIX DIRECTIONS: (a) stop relying on position — embed our cache key in the request
  (e.g. as a sentinel in the prompt/content the model echoes, or map via the xAI batch_request_id recorded at submit),
  (b) assert len(ordered)==len(submitted) per sub-batch AND fail LOUD (OBSERV batch.result_unmapped status=error) when an
  entity gets no result, (c) cross-check: every batched entity must emit an xref.seid line or raise. Consider forcing
  places/equipment SYNC even under batch.phase2=true until fixed. Blocks trusting batch-mode output.
- **[HIGH · data-loss] OPTIONAL-entity persistence bugs — DIFFERENTIATED root causes (XChannel-8 overnight 2026-10-30).**
  Investigation split the earlier conflated 'count->persist=0' into distinct bugs (places is SEPARATE — see the batch
  positional-mapping bug above):
    - **supplemental (write crash): ✅ FIXED (PR #635)** — mkdir(parents=True) the bibliography dir before the review_queue write.
    - **logistics (100% schema-drop):** Grok returned items but ALL dropped at validation
      (`logistics.items_dropped dropped=4 received=4 kept=0`, `chunk.summary items_in=8 results_out=0`, yield.logistics
      regression). Same CLASS as the groups/equipment field-mismatch (#634): investigate the logistics item schema vs
      the prompt's emitted fields; likely a required-field/field-name mismatch rejecting every item.
    - **equipment:** genuine Grok-returned-0 this run (the #634 common_name coercion is correct but wasn't exercised);
      recall-vs-truth still unverifiable (equipment deterministic baseline=0). Not a bug this run.
    - **maps:** ran, found 0 — likely legitimate for a fetched-HTML chapter (maps need in-doc figure links). Low priority.

- **[HIGH · throughput] Synchronous Phase-2 per-person bio enrichment is ~3 min/person, serial.** XChannel-8 (2026-10-29)
  took 2h40m (9613s) because enrich_biographies runs Grokipedia+Wikipedia search + photo fetch INLINE per person for ~17+
  people serially ('Searching Wikipedia...' ~3 min each). FIX candidates: (a) parallelize/bound per-person enrichment, (b)
  move bio enrichment OUT of the synchronous Phase-2 critical path (it's enrichment, not extraction), (c) a hard per-person
  wall-clock. Batch mode hides this (defers); synchronous mode exposed it.
- **[MED · test] Simulated Spot-instance-loss test (automated recovery verification).** The XChannel-8 2026-10-29 Spot
  interruption was a real-world test that EXPOSED the recovery gaps (grace-vs-liveness skip + disabled stale-lock sweep).
  Build a repeatable test that INJECTS a Spot interruption and asserts the pipeline self-heals: (a) unit/integration —
  simulate the ECS Task State Change STOPPED event ('stoppedReason: Your Spot Task was interrupted') into the trigger
  handler and assert _handle_task_stopped_event -> reconciliation relaunches the phase (PR #630); (b) optional live/staging —
  stop-task a running Spot phase mid-run and assert a replacement task launches within the stale-lock-check window and the
  phase resumes from pending# without data loss or double-launch. Covers both the event-driven fast path and the 15-min
  sweep backstop. Guards against the recovery logic silently regressing.
  **SEQUENCING (owner 2026-10-29): do this AFTER the entity-level resume-manifest design (§3) is implemented** — only then
  can the test assert TRUE mid-event resume (picks up the remaining entities, no re-work, no duplicates). Until the manifest
  exists, a simulated-loss test could only verify lock reclaim + whole-event restart, not the resume behavior we want to lock in.
- **[MED · observability] Long-running phase has NO mid-phase progress notification to Slack.** Phase-2/3 only notify
  at start + complete/fail (_notify_complete/_notify_failure). During the ~2.5h serial bio-enrichment window NOTHING
  reaches Slack (the heartbeat monitor only logs locally, src/utils/heartbeat.py:54 — no SNS/Slack path), so a
  legitimately-slow phase is indistinguishable from a hung one in the operator's inbox (observed XChannel-8 2026-10-29,
  'last Slack msg was extract-started'). FIX: emit a periodic progress notification (e.g. enrich N/total every K min, or
  route the heartbeat through observe()/NOTIFICATION_TOPIC_ARN). Pairs with the heartbeat-false-alarm fix below.
- **[MED · observability] Phase-2 heartbeat FALSE-ALARM.** 'no progress for N minutes' counts up to 99 min while the
  per-person enrichment loop IS progressing — the heartbeat only resets on Step-level progress, not per-entity. Fix: reset
  the heartbeat on per-person enrich.persisted (or any OBSERV progress), so a real stall is distinguishable from slow work.
- **[P1 Phase-0 security DONE (PR #618)]** AV default-on+non-dev hard-fail, docx/epub ZIP spoof detection, convert_key crash off-ramp, prestage decompression-bomb cap, book_slug key validation+safe_dump meta, book-derivation convergence. FOLLOW-UPS: (a) av_scan.py:131 still has its own _book_from_key copy — converge onto book_slug; (b) epub non-canonical-mimetype soft-accept (intentional, revisit if strict). Remaining Phase-0 Tier-1 P2 (PHASE0_REVIEW_consolidated.md): real author/title/series extraction (P2-C1 big half), fitz pixmap/page cap (P2-S1), pandoc output-size/memory cap (P2-S2), AV scan-tag binding on convert/video path (P2-S3/P2-S2.3), S3 download size cap + ffmpeg -t (P2-S4), Grok-fallback payload cap (P2-S5), translation chunk cap (P2-S6), uuid4 AV run_id (P2-S7), reject-marker per-source-hash key (P2-C2), _patch_config setdefault aws (P2-C3).
- **[HIGH · data-quality] Deterministic-vs-Grok YIELD-RATIO quality signal (design, owner-aligned 2026-10-28).** Cumulative,
  self-calibrating data-quality assessment: a DETERMINISTIC high-recall scan of the source text produces an expected-yield
  FLOOR per entity type; Grok's extracted count is measured against it; `ratio = produced / deterministic`. grok≈det (25 vs
  25-28) = good; grok≪det (25 vs 0-5) = data-quality ALARM (the starvation signature). SEVERITY LADDER (owner 2026-10-28 — a
  deterministic shortfall may JEOPARDIZE AN ENTIRE BOOK, so worst case must STOP THE JOB until root cause is determined):
    • ratio≈1 -> INFO, proceed (healthy).
    • one entity moderately low (ratio ~0.3-0.5, modest baseline) -> WARN + recall-gap review, proceed (isolated dip).
    • BOOK-JEOPARDIZING signature -> **FAIL-STOP**: produced==0 while deterministic>=floor on a single entity, OR shortfall
      across >=2 entity types simultaneously (the paragraph_numbers fingerprint: upstream fault starves ALL passes). Emit loud
      `OBSERV yield.fail_stop` + `raise SystemExit(nonzero)` at END of Phase 2 so ecs_entrypoint (subprocess returncode!=0 ->
      _notify_failure + sys.exit, lines 816-841) HALTS the chain (Phase 3 never triggers), fires a failure notification, and
      PRESERVES partial output for diagnosis. Check runs per-event/early so a 20-chapter book aborts at chapter 1, not after
      burning spend on a corrupt corpus. Threshold (floor, min-baseline, single-vs-multi-entity) tunable in config.
  (no hand-maintained golden list); ratios accrue over many books into a WEIGHTED assessment. Reuses existing assets:
  people_group_aliases/equipment_aliases/place_aliases.yaml, data/unit_nicknames.yaml, military_equipment.yaml, unit-designator
  regex (`N(st|d|th) Division/Corps/Army`, `Force [A-Z]`, RAF, OB WEST). Alarm only when baseline >= min (e.g. 5) & ratio <
  floor (e.g. 0.5), tunable in config. Append each (book,chapter,entity,deterministic,produced,ratio) to
  output/metrics/yield_quality.json. Wire into observe_yield (PR-A).
  **PEOPLE need special handling (owner):** count DISTINCT person-CLUSTERS, not mentions — "General Cota"/"Norman Cota"/"Cota"
  = 1 (reuse batch_parallel._strip_rank + caption_names detect + src/dedup/surname_report distinct-people clustering + its
  rare→very_common frequency bands). TWO signals, not one ratio:
    (A) QUALITY RATIO on the HIGH-CONFIDENCE band only (rank+distinctive surname, or First Last, or repeated First M. Last) — can alarm.
    (B) RECALL-GAP REVIEW for the AMBIGUOUS band ("Private Smith" = low-rank + common surname): deterministically detected but
        NOT resolved to an extracted person -> emit a HUMAN FOLLOW-UP item (new `recall-gap` review category, reuse the
        needs-review path), NEVER silently dropped and NEVER fails the run. RATIONALE (owner): "Private Smith may be a Medal of
        Honor awardee" — ambiguity != unimportance; the asymmetric cost means ambiguous-and-unextracted must err toward
        SURFACING for verification. GENERALIZES to all entities: any deterministic-found-but-extraction-missed item -> recall-gap
        follow-up (unit the regex caught but Grok dropped, gazetteer place Grok missed). Over time, how often recall-gap items
        prove real/notable also feeds the weighted assessment.

- **[HIGH · observability] Observability SWALLOWS ITS OWN ALERTS — systemic, ~40 sites across 7 patterns** (audit 2026-10-28,
  full report `OBSERVABILITY_SWALLOWED_ALERTS_consolidated.md`). The `paragraph_numbers` fulltext bug (fixed PR #608) was ONE
  instance; the CLASS is open. Patterns: (1) silent except/except-pass (~15 sites); (2) anomaly logged at DEBUG (~10) — incl
  the EXACT same empty-fulltext-skip still live at **places.py:239, people.py:569, dates.py:139, weather_central.py:683**
  (#608 only fixed events.py); (3) zero/empty yield logged status=ok (~8: equipment.py:3027, places.py:415, people.py:1296,
  dates.py:536, phase2 optional extractors); (4) degraded fallback w/o warning (~5: openserp breaker elif, reconcile barrier,
  referential_integrity timeout reads clean); (5) NO expected-yield baseline (~6: openserp searched 1-of-14 unflagged,
  skip-no-name, validation count>=10 gate misses small batches); (6) NO cross-metric correlation (~5: rich sub_events vs 0
  entities never correlated); (7) size-masks-empty-content (events fallback FIXED). ROOT: no `observe_yield(entity, sub_events,
  fulltext_nonempty, produced)` helper — produced=0 is indistinguishable from eligible=0. FIX PLAN (5 PRs): PR-A observe_yield
  helper+baseline (enabler, would have caught the original by design); PR-B extraction starvation (#1,2,8,9,10); PR-C
  enrichment starvation (#4,5,6); PR-D integrity gates (#3,7 + provenance SNS parity + Phase-3 reconcile gate); PR-E
  debug→warn sweep + untraced-except counters. TOP-10 P1 list in the report.
  - **TEST-SUITE MIRROR GAP:** the existing OBSERV tests (test_phase3_comprehensive_observability, test_enrichment_observability)
    DO assert anomaly correctness (groups.anomaly 0-of-N, openserp 0-of-4, places.anomaly), not mere presence — but ONLY for
    the ENRICHMENT layer where an anomaly signal existed. The EXTRACTION layer had no anomaly concept, so there was nothing to
    test (can't test an alarm that doesn't exist); and NO test asserts the cross-stage contradiction (rich sub_events + 0
    downstream entities). Worse, the gates the tests validate ask the wrong question (groups.anomaly keys on `needed`, which was
    itself corrupted upstream — test passes, gate fires correctly, bug still ships). FIX: every observability PR must add the
    cross-stage yield-correlation test alongside the signal. PR-A (observe_yield) adds the first such test
    (sub_events>0 & fulltext_nonempty=0 -> starvation) — the one that would have caught the original incident.

- **[HIGH · extraction/enrichment] Group records have NO `group_name`/`name` field — ALL group enrichment + OpenSERP skipped.**
  Found in the synchronous XChannel-8 E2E (2026-10-28): Phase 2 wrote people_groups records whose ONLY keys are
  `nationality`, `event_mentions`, `GroupID`, `_schema_version`, `_last_updated` — the unit name (e.g. "1st Division")
  survives ONLY in the filename + `event_mentions[].Sub_event_Name`, never as a top-level field. Group enrichment gates on
  `data.get("group_name")` → empty → `OBSERV groups.enrich_skip_no_name` for all 6 groups → `groups.summary needed=0
  enriched=0 skipped=6` → OpenSERP never searches groups (`openserp_groups enriched=0`). FIX: Phase 2 must persist the
  group name (populate `group_name`/`name` from the extracted unit), OR enrichment must derive it from the filename/mention.
  Blocks all group web-enrichment. (Places were fine — satisfied by geocoder, `needed=0`. Link-review empty is correct:
  the source HTML has 0 links/0 footnotes.)
- **[HIGH · provenance] event_provenance stamp read the WRONG source_section path (fix STAGED, pending PR).**
  Found same E2E: `OBSERV event_provenance.stamp resolved=0 reason=no_source_sections` even though 2 source_section records
  existed — they live at `content/<book>/source_section/*.json`, but `_build_event_section_index` read top-level
  `output/source_section/`. Fixed locally to glob BOTH (nested + top-level) + a regression test; verified against the real
  run data (index resolves, event + 6 sub-events stamped). Needs gate→PR→review→merge→redeploy.

- **[HIGH · schema-design] schema.org entity enhancements** — ✅ LARGELY DONE (2026-10-27/28).
  Proposal `docs/current/design/SCHEMA_ORG_REVIEW.md`; ADOPTED + POPULATED: places 2.30 (sameAs/
  containedInPlace/containsPlace/geo/additionalProperty/elevation_m, PR #596) + images 2.27 (14 ImageObject
  fields, #597); Tier-1 deterministic population (image dims/encodingFormat #598; images.contentLocation +
  places.sameAs from wiki/grok urls #599); Tier-2 (Wikidata QID #600; OSM/Wikidata via Nominatim #601;
  opt-in elevation_m from DEM #602). REMAINING (backlog, owner-decide): sameAs+identifier rollout to
  people/people_groups/bibliography; people_groups time-bounded `org_attachments[]` (OOB, M6-populated);
  GeoNames gazetteer id (needs a free username); a prompt/schema subset-consistency guard (§11); source-only
  image fields (licensing/dateCreated/caption) via prompt IF wanted. See SCHEMA_ORG_TIER2_SCOPE.md.
- **[HIGH · ingestion-bug] Book-name derivation mismatch: title-folder vs filename-stem aborts Phase 0.**
  Found in the XChannel-8 cloud E2E (2026-10-28). Uploading `contentrepository/{TITLE}/{file}.html` where
  TITLE != the file stem fails Phase 0 convert: `trigger_handler._book_from_key` derives the book from the
  TITLE FOLDER (e.g. `USA-E-XChannel`), but the launched phase0-convert task used the FILENAME STEM
  (`USA-E-XChannel-8`) as the book-scoped download prefix -> `contentrepository/USA-E-XChannel-8/` had 0
  files -> ERROR "Book-scoped Phase 1 downloaded 0 files ... aborting (would parse an empty book)". The two
  code paths (Lambda trigger book-derivation vs the convert task's BOOK_NAME/prefix) DISAGREE. Workaround
  used: upload with folder==stem (`contentrepository/USA-E-XChannel-8/USA-E-XChannel-8.html`). FIX: make the
  convert/phase task derive its book-scoped prefix from the SAME `_book_from_key`/title the trigger computed
  (pass the derived book to the task, or have the task reuse the identical derivation), + a test for the
  TITLE!=stem shape. Evidence: ECS log `/ecs/dev-wwii-pipeline` ~2026-10-28T12:52:46Z.
- **[HIGH · deploy/CICD] Merge-to-main does NOT rebuild/redeploy the pipeline image — stale `:latest` ran the E2E.**
  Root-caused during the XChannel-8 E2E (2026-10-28): the run produced 0 of the page's ~11 maps + 0 of its 26
  in-document photos (only 5 Wikipedia enrichment images) because the deployed `wwii-pipeline:latest` was
  pushed 2026-10-27 08:59 EDT — HOURS BEFORE M1 (in-doc map links, #587 @14:04) and M2 (embedded images,
  #588 @14:53) merged, so the running pipeline literally lacked the extraction code. The code was on `main`
  but `main` was never rebuilt/pushed after the afternoon merges. THREE fixes (priority order):
    1. **Deploy-on-merge:** a CI job (or documented post-merge step) that rebuilds + pushes the pipeline
       image and re-registers the ECS phase task defs when `main` changes — close the merge≠deploy gap.
       (CI currently runs lint+test on PRs only; it does NOT build/push the Docker image.)
    2. **Pin task defs by image DIGEST, not the mutable `:latest`** (+ stamp the git SHA as an image tag/label)
       so "which commit is live" is unambiguous/auditable (this bug was invisible because task defs point at
       `:latest`).
    3. **Emit the running git SHA in a startup OBSERV** (`OBSERV pipeline.version sha=<...>` at phase start)
       so any run's logs self-identify the code version (no more reverse-engineering "did this run have M2?").
  NOTE: my first diagnosis ("image from Oct 13") was WRONG — I misread the task-def registeredAt as the image
  push time; the image is ~1 day old but predates the M1/M2 afternoon merges.

### MED
- **[MED · notification] Dedup "notify on zero duplicates" reaches no inbox — silent on a clean run.**
  Found in the XChannel-8 E2E (2026-10-28). `_notify_dedup_gate` (ecs_entrypoint) ALREADY notifies on BOTH
  outcomes — subject "dedup auto-proceed" for zero-dup clean runs, "dedup review needed" when blocked — and
  the run logged `Dedup gate: auto_proceed — zero duplicate groups across 16 entities`. But no email arrived
  because it publishes to `NOTIFICATION_TOPIC_ARN`, which has NO confirmed email endpoint (config
  `aws.notification_email` is empty; the confirmed email sub is on `dev-wwii-phase2-complete`, a different
  topic). So the SNS publish "succeeds" into a topic nobody receives. FIX (code): make `_notify_dedup_gate`
  (and the sibling `_notify_url_review_pending`) emit a greppable OBSERV with the outcome + whether the topic
  has a confirmed subscriber (so a no-inbox publish is visible, not a silent success). FIX (operational, not
  code): set `aws.notification_email` / subscribe the operator email to `NOTIFICATION_TOPIC_ARN`.
- **[MED · phase3-race] Phase 3 proceeds past the reconcile barrier on timeout → late bibliography records missed.**
  Found in the XChannel-8 E2E (2026-10-28). Phase 3 logged `OBSERV reconcile.barrier_timeout ... waited=300s —
  proceeding (Phase 3 may snapshot before reconcile finished; a late bibliography record could be missed this
  run)`, then the bibliography step ran with `No bibliography files found` / `bib.resolve in=0`. The supplemental
  Phase-2 batch that would have written endnote/bibliography records was offloaded to the (disabled) batch-poller
  and never completed, so Phase 3 timed out waiting and enriched an empty bibliography. FIX: the barrier-timeout
  is currently a WARNING that proceeds silently-ish; make it a loud anomaly AND, when there are KNOWN outstanding
  queued batches for this book, either (a) block/defer the bibliography step rather than snapshot an empty set,
  or (b) mark the book "bibliography-incomplete, re-enrich pending" so a later poller-drained run fills it.
  Ties to the disabled-batch-poller [HIGH] item — a disabled poller turns this race into a guaranteed miss.
  ✅ DONE (PR #604): added canonical OBSERV phase0.converted/skipped/rejected/error + phase1.parsed/
  skipped/summary (fail-safe, +4 tests). ORIGINAL FINDING: Investigated during the XChannel-8 E2E (2026-10-28). Unlike Phase 2/3 (rich `OBSERV <comp>.<event>` +
  summaries), phase0_convert.py has 0 OBSERV lines and phase1_parse.py has 0 OBSERV lines — they rely
  entirely on the generic `ecs_entrypoint` wrapper (`[step] ...` + a success-path `phase_complete` w/
  duration) + (Phase 1 only) a single `OBSERV run_scope.record`. Gaps: (a) phase0 convert OUTCOME is a plain
  `logger.info("Converted %s -> %s (%d chars)")` (line ~174), not `OBSERV phase0.converted`; (b) the convert
  ERROR/abort paths (lines ~103/122 + the book-prefix abort) emit a plain logger.error with NO OBSERV and NO
  phase_complete — so a Phase-0 FAILURE is not greppable as a canonical anomaly (this is exactly why the
  book-derivation abort above was hard to see); (c) phase1 'Saved ...-parsed.json (N paragraphs)' / 'Phase 1
  complete' are readable `root` logs, not `OBSERV phase1.parsed`. FIX: add canonical OBSERV domain events to
  both phases (phase0.converted/skipped/error with book+chars; phase1.parsed with book+chapters+paragraphs)
  + ensure the convert abort emits an observable anomaly line, mirroring Phase 2/3.
- **[MED] source_section derive failed for 2 sections** (clean rerun e4b22d9).
- **[MED · dedup-false-positive] place dedup is string-only at the Phase-2 gate (no coords)** — Roscheid/
  Schmidt (deferred fix #2).
- **[MED · dedup] NEAR-duplicate content detection (MinHash/LSH)** — same text across two domains, not
  byte-identical.
- Phase-2 re-review follow-ups (WARNING/NIT) still open in **§1.0d/§1.0c**: ocr_merge_handler run_task
  guard, ecs retrieve-path count-mismatch, search_cache transient-miss, wayback DNS-rebind pin-gap,
  credit-reservation leak, non-idempotent OCR resubmit, ingestion/Phase-0 zip-bomb/AV-bypass, + the NIT batch.
- Standing open sections: **§2** Phase-3/enrichment (OpenSERP redesign 98.7% CPU, NARA, people/places),
  **§3** architecture, **§4** infra/security/cost, **§5** data-quality detective.
- **[MED · observability] Pattern-8 remediation — P2 sweep (PRs F/G/H) + P3 sweep.** The 72-finding anti-swallow
  audit (`/home/dchristian/Downloads/OBSERVABILITY_PATTERN8_AUDIT.md`) is P1-COMPLETE (findings 1-16 shipped as PRs
  A-E #624/#626/#625/#628/#627 + the batch_error #622; all LIVE in the deployed image 2026-10-29 17:15 UTC). REMAINING:
  P2 findings 17-44 (PR-F schema/validation fail-open hardening #5/#27/#35/#38/#42/#59; PR-G dedup/merge durability
  #29/#30/#31/#32/#33/#34 — note #30/#34 already done in PR-E; PR-H phase2/3 extractor+loader point-of-loss OBSERV
  #17-28/#37/#39/#40/#41) — mostly convention-conformance, route each failure path through `observe()/observe_error`.
  Then P3 findings 45-72 (promote debug→warn, canonicalize labels, all-zero anomalies, pre-harden the unwired
  phase1/2/3/import Lambda handler twins #67-70 so they're not P1-on-wiring). Plus a cross-cutting CI greppability-lint:
  any `return None/[]/{}`/`except: pass`/`continue` in a failure branch must carry an `OBSERV ... status=error|warn`
  within N lines or an explicit `# OBSERV-EXEMPT: <reason>`.

### LOW
- **[LOW · investigate] Is `config/domain_blacklist.yaml` blacklist still HONORED by OpenSERP?**
- **[LOW · dep-advisory] pylint 4.0.5 → 4.0.9** (CVE-2026-114608); pip-audit — not run in CI.
- **[LOW · security-hardening] `_download_s3_file` path-traversal containment** (ecs_entrypoint).
- **[LOW · data-hygiene] people index/duplicate_report count drift** (overlapping-rerun artifact).
- **[LOW · ci-flake] test_validation_performance wall-clock thresholds.**
- **[LOW · yield] OpenSERP group-image search returns HTML page URLs, not direct images.**
- **[LOW · observability PR-D-L3] Lambda handlers' PRE-EXISTING inline `logger.error` lines interpolate book/key/batch_id**
  (latent log-forgery vector, not widened by PR-D) — route those diagnostics through the `observe()` sink (sanitized) or
  newline-strip. (From the PR-D security review 2026-10-29.)
- **[LOW · observability PR-E-L1] `ExclusionLoadError` uncaught at the dedup drivers** (`find_duplicate_people.py:996`,
  `find_duplicate_groups.py:120`) — a persistent exclusions read-failure now fails CLOSED (correct) but exits with a raw
  traceback instead of surfacing the clean `OBSERV dedup.exclusions_load_error` as the primary signal. Add a top-level
  handler that logs the OBSERV and exits nonzero cleanly.
- **[LOW · observability PR-E-L2] `dedup_ui_handler` `add_by_name` returns HTTP 200 "excluded" even when the pair was NOT
  persisted** (curated-file read-fail path). Operator truth is the Slack/OBSERV alert; UX gap only — surface the non-persist
  in the HTTP response.
- **[LOW · deploy-provenance] `scripts/deploy_all.sh` doesn't pass `--build-arg GIT_SHA`** so the pipeline image's
  `GIT_SHA` env/label is `unknown` (cosmetic provenance gap; image content is still current main). Pass
  `--build-arg GIT_SHA=$(git rev-parse HEAD)` in `build_scan_push`.

### ✅ Recently CLOSED (headline)
- **MULTIMODAL epic M1–M8 COMPLETE** (PRs #587–#594): in-doc maps, embedded/caption images, image→Person,
  recognize+relevancy-gate all links→human review, terrain/aerial→Place, chart-as-source, map legibility
  gate, intra-corpus links→batch.
- **[CRITICAL] ULID attribution across ALL M-issues RESOLVED** (M3/M4 backfilled, PR #595).
- **[provenance] Event→source traceability HARDENED** (2026-10-28): (1) Phase 3 now STAMPS a resolvable
  `Event.SourceSectionID` (+ each Sub-event) from the committed source_section records (events 2.24→2.25),
  so entity → event → SECTION is a hard, validator-enforceable ID chain (was filename-convention only).
  (2) New SCHEMA-DRIVEN provenance-completeness guard `src/utils/provenance_integrity.py` wired into the
  Phase 2 + Phase 3 end-of-phase enforcement: covers EVERY registry entity, flags records missing a
  populated provenance edge + leftover `TODO - Add …` placeholder attribution (non-fatal, OBSERV
  `provenance.audit`/`provenance.entity` — observability tested). Validated finding (corrects an earlier
  shallow read): deep schema walk shows EVERY persisted entity declares a provenance edge — casualties &
  bibliography carry nested `EventID`/`Sub-eventID` (+ bibliography `mentions[]`), so there are NO
  structural `none` gaps. The real residual gap is DATA (unpopulated `TODO` author/chapter attribution),
  now surfaced by the guard. event_provenance stamp emits OBSERV `event_provenance.stamp`.
- **Schema evolution discipline**: history archive `src/schemas/history/` + Change Log + fingerprint-guard
  reminder (PR #591).

---

Active work history below. Sections: (1) **Phase 2 — open**, (2) Phase 3 / enrichment — open,
(3) architecture & design, (4) infra/security/cost, (5) data-quality remediation, (6) lower-priority &
future, (7) **Completed**, then the dated epics (MULTIMODAL M1-M8) + flat backlog.

---

## 1. PHASE 2 — OPEN (current priority)

### 1.0d Phase-2 THIRD RE-REVIEW findings (2026-10-21d) — full doc: docs/current/PHASE2_REREVIEW_2026-10-21d.md

A THIRD independent 14-agent review (7 slices × code+security) after §1.0b/§1.0c (#475–#513), tasked
to find what both prior rounds MISSED + regressions + bugs in the largely-unreviewed areas (ingestion/
Phase-0, schemas, DEDUP, concurrency/orchestration, lambda handlers). The biggest new surface is DEDUP
reference-rebase (unreviewed before) + sibling lambda handlers that never got trigger_handler's topic-trust/
safe-book hardening. Fix order (CRITICAL → HIGH → WARNING → NIT); each via branch→gate→concurrent code+
security reviews (both PASS)→CI→merge→sync→move to §7. bandit still 0H/0M.

- **[CRITICAL] ✅ DONE (PR #514) — dedup merge deletes the secondary entity file but fails to REBASE its references:**
  all 3 paths fixed — (1) people_groups now resolves the real PK GroupID (not the PeopleGroupID alias) via
  _record_id/_ENTITY_DIR_PK + ecs passes GroupID; (2) merge_generic now maps the PK field to the real ON-DISK
  sub-event ref key (group array is "peoplegroups" per batch_parallel key_map, not "groups") + repoints every
  occurrence + OBSERV on noid/unknown/zero; (3) the Lambda dedup-UI generic + people merges now rebase the event
  corpus via storage (correct output/content listing, rebase-before-delete). Equipment handled as no-sub-event-array
  (still rebases cross-entity EquipmentID). Tests: test_dedup_rebase_subevent_refs.py (6) + test_dedup_ui_rebase_refs.py
  (3). Gate PASS 2536 tests; concurrent code+security reviews both PASS. Moved to §7.
  Follow-ups surfaced by the review (pre-existing, NOT regressions from #514):
  - **[WARNING] UI equipment merge does not rebase cross-entity EquipmentID refs** held in OTHER entity records
    (logistics/casualties/weather) — the ECS/merge.py path does (entity-dir scan), the dedup_ui storage path does
    not. Give the UI path a cross-entity scan.
  - **[NIT] update_event_refs entity-dir scan omits the `places/` dir** — a merge won't rebase cross-refs stored
    inside places/*.json. Add "places" to the scanned subdirs.
  - **[NIT] UI event-write goes through the schema write-guard** (S3Storage/LocalStorage.write_json → _validate_entity
    classifies *-event.json as "events" and silently drops a schema-invalid rebased write + logs), whereas the local
    merge.py path uses raw json.dump. Low risk (a ULID swap won't change validity) — note the inconsistency.
- **[HIGH] mdreview_ui_handler path traversal → arbitrary S3 write / Phase-1 content injection** — _save_snippet/
- **[HIGH] ✅ DONE (PR #522) — mdreview_ui_handler path traversal → arbitrary S3 write / Phase-1 content
  injection** — _save_snippet/_get_page used an unvalidated `book` in the S3 key. Fixed: added _safe_book()
  (mirrors run_scope._safe_book / trigger_handler _SAFE_TITLE_RE: fullmatch [A-Za-z0-9._-], reject empty/../ / \\)
  applied in BOTH entrypoints before any key build, 400 + OBSERV on reject; +10 regression tests. Gate PASS,
  concurrent code+security reviews both PASS. Moved to §7.
- **[HIGH] ✅ DONE (PR #523) — dedup_gate_handler trusts SNS Message with no topic-ARN check → human-review-gate
  bypass** — added _is_trusted_dedup_topic() (structured ARN match vs DEDUP_COMPLETE_TOPIC_ARN) gating the Phase-3
  fan-out; CFN wires the ARN onto DedupGateFunction; +10 tests. Gate PASS, both reviewers PASS. Moved to §7.
- **[HIGH] ✅ DONE (PR #524) — unit_patch._download + source_retrieval._download_to_quarantine DNS-rebind SSRF** — both now pin the fetch to a vetted IP via url_hygiene._pinned_get (capture IPs from resolved_host_is_safe, keep Host+SNI=host, refuse redirects). +tests. Gate PASS, both reviewers PASS. Moved to §7.
- **[HIGH] ✅ DONE (PR #525) — supplemental enrichment stored UNVERIFIED urls** — _enrich_material now routes the sequential_search hit through _supplemental_url_verified -> bibliography_resolver._verify_match (SSRF-safe content fetch + Grok match) before storing resource_urls; non-matching hit DROPPED +OBSERV. +4 tests. Gate PASS, both reviewers PASS. Moved to §7.
- **[HIGH] ✅ DONE (PR #526) — locked_json atomic-write broke the cross-process flock + create-path truncate-before-lock** — now flocks a STABLE sidecar <path>.lock (never renamed) across the read-modify-write so the lock survives os.replace; reads the data file read-only after the lock (no truncating open). +2 cross-process regression tests (fail against main, pass with fix). Gate PASS, both reviewers PASS. Moved to §7.
- **[HIGH] ✅ DONE (PR #527) — ecs.run_task placement failures unchecked + retrieve-only crash/SIGTERM stalls** — all 3 run_task callers now detect {tasks:[],failures:[...]} (FARGATE_SPOT placement failure, run_task doesn't raise): trigger raises+releases lock, poller submit returns error, _trigger_retrieve releases the retrieve-claim + returns False (not counted complete); run_retrieve_only sets _current_phase_script so SIGTERM sync+lock-release fire. +3 tests. Gate PASS, both reviewers PASS. Moved to §7.
  - **[WARNING follow-up] ocr_merge_handler._route_to_oob has the same unguarded run_task pattern** (lower risk: FARGATE not SPOT, no lock/claim) — add the same failures[]/empty-tasks check.
- **[WARNING] ✅ DONE (PR #529) — grok_client batch count-mismatch discards 100% with zero retry** — _map_ordered_results returns (failed_ids, count_mismatch); _retry_failed_batch gains force=True that bypasses the 20%-systemic guard on a count mismatch (envelope problem, not model failure) so a dropped results page no longer zeros the batch. +3 tests. Both reviewers PASS.
  - **[WARNING follow-up] ecs_entrypoint._map_batch_results_by_index (AWS --retrieve-only path) has the SAME count-mismatch-zeros-batch class** (returns {} on mismatch, re-run with SKIP_RETRY=1 -> no retry) — carry count_mismatch through the ECS retrieve path. (both reviewers)
- **[WARNING] search_cache._DynamoBackend.get treats a transient Dynamo error as a MISS** → read blip → 7d
  negative suppression (symmetric to the #1.0c TRANSIENT fix, on the read side).
- **[WARNING] wayback _get_with_courtesy re-resolving GET (DNS-rebind pin missing)** — same pin-gap family as
  unit_patch/source_retrieval; route through _pinned_get.
- **[WARNING] credit reservation leaked on batch failure** (poller never releases on _mark_failed/stuck) →
  credit#spend saturates → silent halt when gating is on.
- **[WARNING] ✅ DONE (PR #528) — dedup merge not crash-atomic** — do_merge/merge_generic now write+validate the primary BEFORE deleting each secondary (deepcopy-candidate for people, snapshot/rollback of the shared mention/alias state for generic), so a blocked/crashed write keeps the secondary intact with no mention loss or cross-iteration double-count. +4 tests. Both reviewers PASS.
- **[WARNING] non-idempotent OCR chunk submission** → partial-failure retry resubmits ALL chunks (duplicate GPU
  jobs). + failed phase3 leaks its lock; run_phase terminal cleanup not in finally.
- **[WARNING] batch_poller unvalidated `book` + no event-source auth**; EcsClamavScanner AV run-id uses builtin
  hash() (per-process-salted) → result-key collision.
- **[WARNING] _write_entity_guarded infers 'blocked' from mtime** instead of the bool return (spurious blocks).
- **[WARNING] merges dedup mentions by Sub_eventID only** → distinct per-sub-event mentions collapsed (junction loss).
- **[WARNING] dedup exclusions Dynamo key '#'-delimited but names not '#'-sanitized** → a '#' name silently drops
  the not-duplicate decision → re-merge. + people._save_person_file omits entity="people" (latent downgrade).
- **[WARNING] ingestion/Phase-0:** STATISTICS merge drops rows by metric-alone vs (category,metric) upsert key →
  silent row loss; campaigns table rowspan misread → campaign dropped; detect_scanned front-matter sampling bias;
  zip/rar member extracted with NO decompression cap (zip-bomb OOM) + extracted members BYPASS the AV gate +
  basename-collision silent overwrite.
- **[WARNING] supplemental_info_pipeline.process_supplemental_information iterates a dict as a list** (crashes on
  the phase1 endnotes/footnotes shape). + equipment merge hard-indexes event_mentions/variant_name on legacy data
  (silent mention drop). + equipment/grok_search_maps mutate the PROCESS-GLOBAL Image.MAX_IMAGE_PIXELS under
  ThreadPool(3) → bomb cap weakened mid-decode (concurrency).
- **[NIT batch]** events retry-cache orphaned key + paragraphs[0] IndexError; people_groups enrichment dropped on
  merge; grok_client _validate_prompt content-DoS (raises on {token} in rendered prompt) + quadratic-ReDoS +
  weaker detector regex; json_validator/custom_validators ID-heuristic disagreement + _fix_invalid_ulids silent
  randomization (latent); search_cache 64-bit key; result_dedup keep-first vs documented union; bibliography
  _aggregate_findings double-append primary url; supplemental mentioned-people/orgs unbounded substring;
  images.py offline-illustration re-run duplicates; maps.py marks processed before extracting; groups_output
  event_mentions additionalProperties:false; nat_manager concurrent-create loser returns 'ready' unverified;
  award_source_pages Content-Type ext (backstopped); HTML sniff leading-whitespace/BOM; pandoc output no size
  cap; dead code (_acquire_lock, external_maps.main arg order).

### 1.0c Phase-2 FRESH RE-REVIEW findings (2026-10-20b) — full doc: docs/current/PHASE2_REREVIEW_2026-10-20b.md

A SECOND independent 12-agent re-review (6 slices × code+security) of the merged state after the 1.0b
campaign + PRs #487–#496. NEW/missed gaps (analyzer-invisible; bandit still 0H/0M). OPEN items below in
priority order; fix each via branch→gate→concurrent code+security reviews (both PASS)→CI→merge→sync.
(The DNS-rebinding CRITICAL is DONE — #497 — and moved to §7 COMPLETED.) Ranked:

### 1.1 Data-quality from the clean XChannel run

- **[FIX MERGED · PR #472 — VERIFY on next re-run] equipment extraction persisted 0.** Root cause
  (confirmed by security review): equipment embeds the full event_data in its cached prompt, and
  `_stamp_schema_versions` REWRITES `*-event.json` between the batch COLLECT and RETRIEVE passes
  (injects `_schema_version`/`_metadata`) → the retrieve prompt differs → `_make_cache_key` differs
  → retrieve cache-MISS → empty equipment_list → 0 persisted (supplemental #399/#401 class). Wiring +
  the #461 collect-raise were confirmed CORRECT (not the bug). FIX: (a) `sort_keys=True` canonical
  serialization (equipment.py + people_groups.py); (b) a STABLE content-independent `cache_key` =
  sha256(`equipment:v1:{model}:0.1:{EventID}`) keyed on the deterministic EventID (immune to ADDED
  keys, not just ordering) + a defensive `_get_model` fallback. 4 regression tests incl. the decisive
  added-keys case. VERIFY on the next E2E: equipment persists >0 + `OBSERV cachekey.submit==lookup`
  for equipment (the run's api-cache wasn't synced to S3, so it couldn't be confirmed offline).

- **[MED · verify on re-run] maps/images coverage.** The capture/caption/download fixes
  (#469/#470/#471) only take effect on a NEW image build + re-run. Verify: maps persist (was 0; real
  maps under `/maps/`), images capture all per page (was 1/page), captions populate
  `image_title`/`description`, local_copy is relative, bytes download under `output/` (S3-synced).

- **[MED · verify on re-run] bibliography.** The footnote-text import fix (#467) was offline-proven
  (0 → 1,387 stored); a full E2E re-run should confirm bibliography populates + titles derive (#460)
  on the live pipeline.

### 1.2 Phase-2 run-scope + chaining

- **[✅ DONE · PR #473 — VERIFY on next re-run] Source-vs-actual reconciliation replaced the
  magic-50-only ceiling + fixed the Phase 1→2 chaining bug.** Phase 1 now records the authoritative
  per-book section count it PRODUCED (`output/manifests/run_scope/<book>.json`, S3-native,
  independent — not a re-glob of Phase 2's input); `enforce_scope` fails loud only on FAN-OUT
  (actual > recorded — never legitimate to process more than Phase 1 produced), while a full run
  (== recorded) OR an incremental resume (< recorded) passes. The recorded count AUTHORIZES a >50
  run, so the chained Phase 2 no longer aborts at the handoff with EXIT_UNDECLARED_LARGE_RUN — the
  hand-carried expected_count is no longer required. ecs_entrypoint downloads the manifest so it
  survives the AWS handoff; magic-50 remains the FALLBACK when nothing was recorded. Rule chosen
  per operator guidance ("don't exceed") over the initial exact-match design (which would have
  false-positived on AWS incremental resumes). Accepted residual: a tamper-UP recorded count is
  inherent to any declared-expectation model (trust boundary = output-bucket write access). 13+7
  tests. VERIFY on the next clean E2E that the chained Phase 2 proceeds without a manual
  expected_count and OBSERV `run_scope.record`/`run_scope.reconciled` appear.

### 1.3 Phase-2 observability

- **[OBSERVABILITY] Per-document CORRELATION id on every OBSERV line (deferred, cross-cutting).** The
  `OBSERV <stage>.<event> key=val` convention + the Phase-2 P1/P2/P3 roll-ups + `phase2.summary`
  end-of-run line are DONE (#464/#465/#466). Remaining: add a per-document correlation/trace id so one
  CloudWatch Insights query reconstructs a single document's full journey (Phase 0 → 1 → each Phase-2
  extractor → dedup → each Phase-3 enricher). Build incrementally.

- **[INFRA · CORRECTED 2026-10-20] CloudWatch retention is fine (14 days); the real gap is offline
  replay.** `/ecs/dev-wwii-pipeline` (all phase OBSERV) = 14-day retention — ample. The earlier
  "OBSERV aged out overnight" note was WRONG: it was a flaky stream-list query, not expiry; the
  lines were retained. The genuine diagnosability gap is that the Grok **api-cache is not synced to
  S3** (see §1.3), so batch RESPONSES can't be replayed offline after a run — that, not log
  retention, forced the code-level repro. INVERSE issue for the cost pass: ~12 Lambda log groups
  (incl. phase2-extract ~11MB, s3-validation-backstop ~14MB) have NEVER-EXPIRE retention
  (unbounded growth) + containerinsights perf = 1 day — set sane retention in the §4 CloudWatch
  reduction pass.

- **[✅ DONE · PR #474] Sync the Grok api-cache to S3 for offline replay.** Phase 2/3 _final_sync
  now uploads /app/cache/api -> s3://.../diagnostics/cache/<book>/ (the whole cache: batch_requests
  + the per-type cached request/RESPONSE entries). scripts/replay_cache.py downloads a book's cache
  for offline GrokClient replay (no Grok calls). Makes a run diagnosable post-hoc — e.g. the
  equipment=0 class could now be replayed offline instead of code-level repro. OBSERV cache.synced.

### 1.4 Dedup-UI (surfaced during XChannel dedup)

- **[HIGH · dedup UX] Add a real "edit canonical name" tool to the dedup UI.** "Fix name"
  (`_rename_to_match_content`) only syncs the FILENAME to the record's existing `name` — it CANNOT set
  a new canonical name (e.g. "Bernard Montgomery" → "Bernard L. Montgomery" returns "Filename already
  matches content"). Add an action that writes `name`/`current_name`, re-derives the filename, updates
  `index.json` + duplicate_report refs, behind the basic-auth authorizer. Supporting bug:
  `new_filename = name.lower().replace(" ", " ")` (~L489) replaces a space WITH a space (likely meant
  `" " → "_"`). (Dedup API stage is `app` not `dev`; authorizer needs WILDCARD `apiId/app/*/*` — fixed
  live + in CFN.)

- **[MED · dedup UX] Show the mention TEXT in the duplicate-group detail view.** People resolution is
  increasingly an educated guess (no service number in book mentions). Surface each mention's
  `original_text`/`verbatim_reference` (mentions junction → `*-parsed.json`) so a reviewer can judge
  "same Smith?" against real context.

### 1.5 Phase-2 ingestion polish

- **[LOW · ingestion] `chapterN-meta.yaml` sidecars rejected by the Phase-0 type-gate** → 21
  needs-review markers on the XChannel import. Non-blocking (the `.md` parses, the parser reads meta
  directly), but the type-gate should recognize `.yaml` meta sidecars.

---

## 2. PHASE 3 / ENRICHMENT — OPEN

### 2.1 OpenSERP redesign (the measured CPU bottleneck — 98.7%)

- **[DESIGN · operator-directed] OpenSERP scope reduction — PDF/EPUB source discovery + HTML
  references via Grok + human-gated acquisition.** DOC:
  `docs/current/design/OPENSERP_SCOPE_REDUCTION.md`. OpenSERP finds acquirable PDF/EPUB **documents**;
  Grok proposes authoritative **HTML reference** links; both flow into a human **ADD /
  DISCARD(≥90-day) / NEVER(permanent url/host block)** gate (UI renders a clickable link; no
  snapshot). KEY FIX: `_openserp_fetcher` must RESOLVE item/landing pages to the document
  (archive.org/HathiTrust item pages), not reject on a bare-suffix rule. Reuses Grok-verify, SSRF
  guard (#453), dedup-UI pattern, acquisition fetchers. Open Q: are place/person IMAGES user-facing
  today? measure before dropping image search. (Supersedes the A00 "Grok-proposes + liveness + human
  audit" framing — same intent, now fully specced in the doc.)

### 2.2 NARA resolution redesign (correctness)

- **[HIGH] STOP using Grok to determine Record Groups — deterministic RG lookup + structured query.**
  Grok's RG determination is poor (fabricated RG/NAID; `archive_reference_number` even stored Grok's
  prose). REDESIGN: (1) RG = deterministic lookup against NARA's Record Group Explorer (static
  alias→RG map: unit records→RG 407, "MS #"/Foreign Military Studies→RG 549, Army operational→RG 338,
  HQ ETO→RG 498, Allied HQ→RG 331, War Dept→RG 165, seized German→RG 242); (2) query via the catalog
  API's STRUCTURED filters (`ancestorNaId=<RG>` + `levelOfDescription=series`) not verbatim-as-`q`;
  (3) Grok SEMANTIC ONLY. Replace `_identify_record_group`/`_identify_nara_record`; parse
  `archive_reference_number` to the bare RG.

- **[HIGH] Phase-3 bibliography resolution: re-attempt + concurrency.** (1) The resolver SKIPS records
  with a terminal `search_status` so an improved resolver never re-runs on `not_found` — treat
  `not_found` as NON-terminal (re-attempt until genuinely resolved; consider a resolver-version
  stamp). (2) Manual `aws ecs run-task` Phase-3 launches are NOT guarded by `lock#dev-wwii-phase3-
  enrich`, so overlapping tasks clobber `output/bibliography/*.json` (observed resolved=9 clobbered by
  stale resolved=2). Guard manual launches; run ONE Phase 3 at a time.

- **[HIGH] Back-reference / multi-document citation — two sub-items remain.** Within-chapter DONE.
  OPEN: (a) MULTI-DOC NOTE ("A; B; C" stored as ONE record — LLM overlay's semantic job); (b)
  CROSS-CHAPTER DOCUMENT IDENTITY (ch3 "Irwin Diary" == ch5 — dedup's job).

- **[HIGH] 3 footnote CLASSES — hybrid SPLIT is the gap.** CITATIONAL → bibliography, PROSE →
  notes-event, HYBRID → BOTH. The 3-way split is the Phase-2 supplemental LLM's job. OPEN: add
  per-entry `prose_content` + widen `content_class` to {citation, prose, hybrid}; prompt the LLM to
  SEPARATE prose from citation(s); route prose → `{chapter}-notes-event.json`, each citation →
  `store_bibliography_entry`; regex detector becomes a non-decisional backstop.

- **[HIGH] alt_title expansion not running + duplicated + not schema-enforced.** (1) The expansion
  rule (#421/#425) drives the Phase-2 supplemental BATCH (skipped/cached) and isn't clearly wired to
  UPDATE deterministic records → add alt_title expansion as a direct per-record Grok call in Phase-3
  `_enrich_bib_file` (idempotent). (2) `_build_bib_entry` keeps alt_title at top-level AND inside
  `citation` → can drift; pick a canonical home. (3) `bibliography_output.py` allows both but doesn't
  enforce they match → add a consistency check.

- **[HIGH · feature] Acquire + process positively-verified source documents (NARA / archive.org /
  OpenSERP).** Download the document, preserve the original under `source/` (existing Glacier-IR
  lifecycle), convert via Phase 0, process via Phase 1→3; the record flips `held_unprocessed` →
  resolved/local. Pluggable fetcher REGISTRY keyed by `search_source`; human-review gate scaled by
  FORMAT+SIZE (NARA page-image sets ≥50 → review; PDFs → review by default; text → auto-process);
  SEPARATE opt-in pass (config-gated); idempotent by naId/item-id; auto-acquire only `public_domain`.
  S3-NATIVE (idempotency = presence of `source/<search_source>/<id>/`, no Dynamo). NARA budget = 10k
  queries/MONTH (bumpable to 150k; downloads quota-free) → persist `digitalObjects` at RESOLVE time so
  acquisition doesn't re-spend a query; add an OBSERV counter.

- **[HIGH · schema] A document must support MULTIPLE DISTINCT SOURCE ENTRIES.** Replace
  `resource_urls: [string]` with `sources: [object]`, each with url / source (nara|nara_physical|
  archive_org|hathitrust|...) / format / license+copyright_status (per-copy) / access /
  archive_reference_number / download_status. `source=nara` ONLY from a REAL catalog API hit (never a
  Grok "RG…"). Precedence: local holding → online repos → physical NARA visit LAST (`nara_physical`,
  `access=physical_only`, RG/series/box/folder — a resolved-with-location outcome). Additive + version
  bump + fingerprint re-pin.

- **[HIGH] Capture the actual Bibliography section + abbreviations decoder.** CMH green books have a
  "Bibliographical Note" (full citations) + an "Abbreviations" list (decodes footnote shorthand). Add
  a parser back-matter detector; emit `*-bibliography.json`/`*-abbreviations.json`; prefer
  bibliography-section entries + apply the abbreviations map to footnote verbatims before NARA.

### 2.3 People / places enrichment

- **[HIGH] People `biographical_profile` (bio TEXT) — confirm Phase configuration.** 0/59 on the
  Phase-2 clean run; likely EXPECTED (bio enrichment moved out of the standard Phase-2 path). Verify
  whether/where it is intended to populate; `enrich.persisted entity=person enriched_now/has_bio/
  sources` (#465) will show the truth on a re-run.

- **[FOLLOW-UP] People bio + group enrichment attribution + back-fill.** Group `_source` stamped
  (#462). Remaining: (a) verify/stamp people `biographical_profile` attribution once bios repopulate;
  (b) pre-fix 0/74 unsourced group records only back-fill on a forced re-enrich — add a one-time
  backfill if needed.

- **[HIGH] Phase 2 should capture place HISTORICAL NAMES / ALIASES from Grokipedia/Wikipedia.**
  `enrich_place_from_wikipedia` writes only url+image+hierarchy; pull infobox/native/former names
  (Wikidata P1448/P1705) + Grokipedia body into `historical_names[]` tagged by source. Feeds Phase-3
  name-variant geocoding.

- **[HIGH] Capture CO-REFERENCED place aliases from surrounding text.** `PlaceMention.historical_name`
  is SINGULAR → Grok keeps one. "Bogeyman Hill (Hill 697, the Schwarzer Mann)" names one place three
  ways; we keep the ungeocodable nickname. Change the extraction model to an ALIAS ARRAY (+ prompt)
  tagged by type+language. Geocode recovery (cheapest tier): on a miss, re-read the place's own
  `original_text` for aliases before web/translate tiers.

- **[HIGH · geocoding] Phase-3 geocode MISS-RECOVERY.** On not-found: (a) native-language toponym
  retry (Farm→Ferme/Hof, Castle→Château/Schloss + Grok translate) → re-query Nominatim; (b) OpenSERP
  location-hint rescue — extract EXISTENCE+LOCATION from snippets (do NOT WWII-relevance-gate); (b2)
  EXIF-GPS from a discovered photo (guard: social media strips EXIF; sanity-check vs country); (c)
  persist the native name + region back with provenance. Bounded (~3% hard-miss, idempotent).

- **[HIGH] Wire SourceRechecker into People enrichment** (source-first gap-fill of `nationality` etc.
  in `enrich_person_biography`, mirroring people_groups). Minor residuals: Grokipedia fetch real
  `/page/<slug>` not search-results HTML; OpenSERP stamps `openserp_searched` on empty search;
  `time.sleep(1)` on cache hits; HallOfValor name-match recall.

### 2.4 URL-sourced enrichment + dedup

- **[HIGH · provenance] URL-sourced enrichment + 90-day URL registry — FINISH wiring.**
  `url_hygiene.py` + `url_registry.py` DONE (SSRF-safe clean_url, DynamoDB `url#{sha256}` 90-day TTL),
  and `enrich_places.py` is PARTIALLY wired (`should_fetch_content` + register). REMAINING: wire the
  other callers (enrich_groups / openserp / grok-cited-wiki) to register/should_examine + require
  Grok-cited URLs + fetch-then-evaluate relevance. OPEN: drop vs flag (`source: grok_unverified`)
  facts with no cited URL.

- **[HIGH · data-quality] Centralized JSON result de-duplication.** Add
  `src/utils/result_dedup.py::dedup_entity(data, entity)` at the ONE central write path, driven by a
  per-schema DEDUP_FIELDS declaration: URL arrays dedup on `url_hygiene.clean_url`, mentions on
  MentionID, free-form on canonical hash. First-writer-wins, idempotent, behavior-only.

---

## 3. ARCHITECTURE & DESIGN

- **[HIGH · resilience/design] Entity-level Phase-2 resume via a worklist MANIFEST + S3 diff (replace/upgrade the per-event `.processed_events.json`).**
  PROBLEM (XChannel-8 2026-10-29): Phase-2 resume granularity is PER-EVENT — `_update_processed_registry` marks an event
  done only AFTER the whole event finishes. Hyperwar chapters are ONE giant event each (`Step 1 batch 1: 1 chapters`, 50+
  people, ~2.5h serial enrichment), so a Spot interruption mid-event leaves the event UNmarked → on restart the entire event
  re-processes from the top (re-runs extraction Grok calls). The per-person `last_enrichment_search`/`_is_already_enriched`
  gate saves the expensive enrichment re-work (done people are skipped), but the event scaffolding + any in-flight unpersisted
  person are lost, and there is no event-internal checkpoint.
  PROPOSAL (owner 2026-10-29): at Phase-2 start, write the FULL set of entities INTENDED to be enriched (the "to-do manifest")
  to disk/S3 right after EXTRACTION (fast, pre-enrichment). On any (re)invocation, Phase 2 diffs the manifest against the
  entity JSONs already in S3 and processes only the remainder → stateless, self-correcting, resumes at the individual-entity
  level after a crash at any instant. Preferred over an in-memory "last processed index N" cursor (fragile to ordering).
  THREE CORRECTNESS SUBTLETIES (must be in the design):
    1. "written to S3" != "done". A person file is written at CREATION (post-extract, pre-enrich) AND updated post-enrich.
       The diff must compare against a TERMINAL enrichment signal on the written record (`enrichment_status ∈ {enriched,
       not_found}`), NOT mere file existence — else a resume marks extracted-but-unenriched people as done.
    2. Key on the NATURAL/name key, NOT the entity id. PersonID is minted fresh server-side each run (`ulid.new()` in
       _process_person), so a manifest keyed on PersonID would re-create DUPLICATES on resume. Use `_normalize_name` (people)
       / the group key / etc. — the same stable keys the index already uses.
    3. Write the manifest BEFORE the crash window and sync it immediately (durable version of the in-memory extracted_* list),
       so even an early Spot kill leaves a valid to-do list. Cost: one S3 LIST + compare at startup (trivial).
  Generalizes to all per-event entities (people/groups/dates/places + optional casualties/equipment/logistics/supplemental).
  Non-trivial change to the extraction/enrichment flow (new manifest write + startup reconciliation pass) — design + gate +
  test carefully; pairs with the serial-enrichment throughput + spot-retry work.

- **[DOCS/SCHEMA] Schema version CHANGELOG + drift-proof current-schema doc.** Two gaps found while
  auditing the schema-stamping bug: (1) there is NO per-entity schema version HISTORY anywhere except git
  — `ENTITY_SCHEMA_VERSIONS` (src/schemas/__init__.py) gives the CURRENT number per entity but nothing
  records what changed at 2.24→2.25→2.26→2.27→2.28, when, or why. The `_schema_version`/`needs_migration`/
  write-guard ("old=lenient, current=strict") machinery is a migration system with no changelog. Add a
  lightweight `docs/current/SCHEMA_CHANGELOG.md`: per entity, per version, a one-line "what changed +
  migration note" (e.g. "places 2.27: added `historical_names[]`; back-compat, nullable"). Most valuable
  BEFORE the RAG/embedding layer (a silent field-semantics change would poison embeddings). (2)
  `docs/current/SCHEMA_REFERENCE.md` is hand-maintained + DRIFTING (dated 2026-10-06; quotes stale version
  numbers inline) — it already punts to ENTITY_SCHEMA_VERSIONS as source-of-truth but has no single
  rendered "entity → current version" table. Auto-GENERATE that table (and ideally field lists) from
  `ENTITY_SCHEMA_VERSIONS` + `src/schemas/*_output.py`, and add a gate check that fails if the rendered
  table ≠ code, so the doc can never go stale again.

- **[ARCH/PERF] Phase-2 container split — fix submit-only subprocess gap first.** DOC:
  `docs/current/design/PHASE2_CONTAINER_SPLIT.md`. The "submit-only" decoupling is DEFEATED by the
  subprocess boundary: `run_submit_only` monkey-patches `poll_batch`, but `phase2_extract.py` runs as
  a `subprocess.run` child that doesn't inherit it → the container BLOCKS the whole batch wait and
  `batch_job#` is never enqueued (poller path dead for Phase 2). STEP 1 (cheap, high value): pass
  `BATCH_SUBMIT_ONLY=1` into the subprocess so it returns after submit + the parent enqueues →
  releases Fargate during the wait + activates the poller. Then the full Step-Functions split (Stage A
  collect+submit exits → poller → Stage B retrieve → Stage C enrich/dedup fan-out), preserving the
  #452 reconcile barrier + dedup gate + scope guard + phase2.summary invariants per-stage.

- **[PERF DATA] Phase-2 is over-provisioned — right-size.** 48h Container Insights: CPU peak 30% / avg
  1.2%, mem peak 10.2% (~208 MB) on a 1 vCPU / 2 GB reservation. Right-size to ~512 MB / 0.25–0.5 vCPU
  — cuts idle-wait Fargate cost ~4×. Bottleneck is wall-clock (batch wait), not compute.

- **[DIRECTION] DynamoDB deprecated for JSON/entity storage; KEPT for queue/coordination.** REMOVE the
  JSON role (DynamoEntityStore entity/bibliography paths, materialize bridge, `_skip_bib_download`);
  KEEP coordination (locks/pending/nat-lease/batch_job#/dedup-run/reaper/manifest). PREREQUISITES
  before flipping entity_backend off: (1) concurrency-safe S3 bibliography writes (per-record
  immutable hash-keyed files + rebuilt index), (2) scoped reads. Phased cutover, each a gated PR.
  Forward-compatible (entity_backend defaults to filesystem).

- **[MED · security/architecture] Centralize + make secrets dynamic.** Keys load statically at startup
  into `os.environ` across 7 sites → a rotated key isn't picked up until rebuild (rotation-unsafe
  mid-run). DIRECTION: one `secrets.get(name)` accessor with short-TTL cache + rotation-awareness, out
  of ambient env, replacing all 7 loads.

- **[MED] Write guard vs BREAKING schema migrations.** The guard validates against the CURRENT schema
  and doesn't consult the upgrade registry → a breaking migration could block an in-flight re-write.
  Mitigated today (additive-only norm). Future: defer to `register_upgrade` / validate against the
  record's declared `_schema_version`.

- **[PRIORITY — NAT architecture] Consolidate ALL NAT lifecycle into ONE Lambda.** Phase code must
  only emit "I need a NAT"; the Lambda owns up-on-demand + 4-hour demand-check teardown. Removes the
  recurring NAT flapping + the orphaned-lock deadlock + poller-wedge (§4).

---

## 4. INFRA / SECURITY / COST

- **[TRIVIAL · housekeeping, found 2026-10-20 in #486 review] Stale `concurrent.py` string literals
  in scripts/archive/qa/.** Dead archived one-off QA scripts reference the now-deleted module; never
  imported/CI'd. Optional cleanup in a future housekeeping pass.

- **[LOW · PATTERN-concurrency #3, found 2026-10-20] Write-guard tripwire blind spots.** The
  raw-write gate (scripts/check_raw_writes.sh, baseline 65) matches only `json.dump(` / `.write_text(`
  and VERIFIES 0 raw writes target an entity-record path today — so the opt-in bypass is governed. But
  it misses equivalent sinks: `f.write(json.dumps(rec))`, `open(path,'w').write(...)`,
  `path.write_bytes(json.dumps(rec).encode())`. A future per-entity write via those idioms would slip
  through. FIX (separate audit): extend the detector to the file-write forms (excluding boto
  Payload/Body=...encode() which are not file writes) + classify the ~14 current matches. The 4
  concurrency/crash-safety MEDs #1/#2/#4 are fixed; #3 is this tripwire-hardening follow-up.

- **[LOW · found 2026-10-20, out of trigger-hardening scope] _submit_ocr derives pdf_name with a
  raw rsplit.** It feeds a DynamoDB claim key (ocr#{pdf_name}), S3 keys (ocr-output/{pdf_name}/...),
  and Batch job commands. PRE-EXISTING; low blast radius (flat S3 keys, not a filesystem-rmtree/--book
  sink). Route through _safe_stem_from_key for consistency with the trigger-Lambda hardening (PR #482).

- **[MED · latent, found 2026-10-20 during PATTERN 3] Entity filename id-suffix `[:8]` is
  timestamp-only → same-ms collisions.** Entity writers compose `{name}_{ULID[:8]}.json`, but a
  ULID's first 8 chars are the millisecond timestamp — two entities minted in the same ms share the
  suffix and (if names also match/truncate) can collide to one file. Pervasive: batch_parallel:267,
  dates, equipment, logistics. The full ULID inside the record is the real PK, so this is a filename
  disambiguation weakness, not an identity bug. PATTERN 3 bumped only the NEW fallback paths to
  `[:12]` (adds random bits); the main composed names still use `[:8]`. FIX: standardize on
  `[:12]`+ (or a short random token) for the filename suffix across all entity writers.

- **[DEPS · prod-install hygiene, 2026-10-20] Sync + pin the remaining dependency surfaces.** The
  top-level `requirements.txt` was hardened (pillow 12.3.0→13.0.0 PR #478; certifi pinned 2026.10.19 +
  urllib3 declared 2.8.0 + full-env pip-audit PR #479). STILL OUT OF SYNC / loose:
  - `requirements-lambda.txt` — REAL deploy surface (scripts/update_lambdas.sh + .github/workflows/
    deploy.yml package Lambdas from it; gate.sh + lint.yml audit it). Uses `requests>=2.32.0` (range)
    + UNPINNED `diskcache` → non-reproducible Lambda builds + can resolve a vulnerable transitive
    certifi/urllib3. FIX: hard-pin to match requirements.txt (requests==2.33.1, certifi==2026.10.19,
    urllib3==2.8.0, diskcache==5.6.3) or switch to pip-compile.
  - `Dockerfile.paddle:44` installs `pillow>=12.3.0` (range) — bump to `==13.0.0` to match the CVE fix
    + the standardized pin (12.3.0 has the 8 CVEs #478 fixed).
  - Confirmed FINE: main Dockerfile + Dockerfile.video install from requirements.txt (inherit fixes);
    requirements-ci.txt (type stubs only) + requirements-test.txt (no runtime deps) need nothing;
    Dockerfile.chandra/.clamav carry no own pillow/pip-from-reqs.
  - The one accepted ignore is documented in gate.sh: PYSEC-2026-2447 (diskcache pickle RCE, NO fix
    exists, low exposure); remove + bump when a fix ships.
- **[DEPS · openserp submodule upgrade, 2026-10-20] Bump the vendored openserp Go submodule.** User
  received an upstream upgrade notice. `openserp/` is a git SUBMODULE (github.com/karust/openserp, a Go
  search service we run at :7001 — NOT in our Python import graph), so this is a `git submodule update
  --remote openserp` + rebuild of its container, SEPARATE from the Python deps above. Check the
  upstream release notes for breaking API/flag changes to the `/mega/search` endpoint our enrichment
  calls before bumping. (openserp/examples/*/requirements.txt are the upstream author's samples — NOT
  our surface, do not edit.)

  (and `src/utils/url_fetch.py`) validate the resolved IP then let `requests.get` RE-RESOLVE DNS and
  connect — a hostile resolver can answer public at check, private/metadata at connect. Mitigated (no
  redirects; literal-IP + metadata blocked; needs authoritative DNS + a race) → WARNING, accepted
  house pattern. Proper fix is shared: resolve once + PIN to a vetted IP across ALL fetchers. Cleanup:
  `maps._determine_map_extension` is dead; `timeout` params on the map downloaders are ignored.
  config.yaml is gitignored but baked via Dockerfile COPY — generate from the tracked example in CI or
  assert download flags + output/-rooted paths at startup.

- **[HIGH · infra reliability] NAT-manager orphaned-lock deadlock.** `lock#nat-manager` uses only
  `attribute_not_exists` (no stale-takeover); a crashed holder orphans it → "Another instance running"
  forever → no NAT. FIX: stale-lock takeover on acquire.

- **[HIGH · infra reliability] Batch-poller retrieve-claim wedge.** `_trigger_retrieve` claims
  `retrieve#<id>` THEN waits for NAT; a Lambda timeout leaves the claim held with no task launched →
  the batch is permanently wedged at `ready`. FIX: TTL/release-on-failure on the claim; claim AFTER a
  successful `run_task`, or drop the long synchronous `_wait_for_nat()`.

- **[HIGH · security] CloudFormation review follow-ups** (`CFN_SECURITY_REVIEW_2026-10-11.md`).
  Remaining HIGH: GitHub-deploy-role `iam:PassRole` lacks `PassedToService`; internet-facing
  dedup/mdreview/metrics Lambdas share the broad LambdaExecutionRole. MED: entity-table PITR,
  deploy-role GetSecretValue, TriggerQueue SSE+DLQ, OcrSpotController `batch:*` on `*`, public
  dedup/metrics APIs, constant-time auth compare.

- **[ACTION REQUIRED · deploy] Activate the `relocated/` 60-day lifecycle (#451).** `storage.yaml`
  changed (ExpireRelocatedArchive); until a StorageStack deploy, archived objects won't auto-expire
  (harmless, recoverable). Deploy the storage stack.

- **[HIGH · deploy] OCR markdown-review UI** — built + tested, not deployed. Deploy + smoke-test the
  `/mdreview` route (async-human-gate NAT pattern like dedup). Clear the ComputeStack
  UPDATE_ROLLBACK_COMPLETE path first (change-set/dry-run; pass `--pipeline-image`/`--openserp-image`).
  Covers the layout-miss task-org table review surface (same UI).

- **[HIGH · cost] AWS budget reset + CloudWatch reduction pass.** Reset the stale $75 limit (S3 alone
  exceeds it; Sept $471 was mostly a one-time $135 domain + $85 CloudWatch + $78 S3 — not a runaway).
  Trim CloudWatch retention/ingestion; reconsider container-insights.

- **[HIGH · deploy] Idle-infra teardown backstop reaper** — code-complete, NOT deployed. Deploy via
  `deploy_all.sh` + `update_lambdas.sh`.

- **[HIGH · sourcing] Source qualification** — derived qualifier on the bibliography record
  (source_type/proximity/evidentiary_support + method/basis/confidence). Additive schema bump; no
  reprocess; read-through `event→mentions[].BibliographyID→bibliography`. See
  `dataquality/SOURCE_QUALIFICATION_DESIGN.md`; pending owner sign-off on DCMI direct-vs-derivation.

---

## 5. DATA-QUALITY REMEDIATION / DETECTIVE (open layers)

The PREVENTIVE write guard + DETECTION catcher are DONE (§7). Remaining:

- **(a) Remediate pre-existing invalid S3 records.** Version-less fragments missing PK+name carry
  UNIQUE cross-ref mentions — do NOT delete. QUARANTINE to `_quarantine/` → targeted reprocess from
  provenance → purge only after mentions land in canonical records.

- **(b2) Corrective reprocessor (NO human gate — code).** On a systematic-cluster alert (the catcher
  is the signal; humans fix CODE, never adjudicate records): auto-classify + quarantine, then AUTOMATED
  dedup-aware reprocess from provenance with an idempotency/loop-guard. CRITERIA: only Tier-3 content;
  re-run the RELEVANT extractors for the whole SOURCE UNIT (not one schema); MUST resolve-before-write
  (merge into the canonical record, never duplicate — deterministic). Highest-risk (first to MUTATE
  the live corpus) — design-first + review.

- **(b) Detective backstop + bypass prevention.** (1) S3-event Lambda running the SAME `_validate_entity`
  primitive (add a drift check to `check_component_versions.sh` hashing `src/schemas` +
  `file_lock.py` + `validation_stats.py` repo-HEAD vs deployed bundle); quarantine = tag-in-place +
  bucket-policy DENY. (2) grep gate forbidding new raw `json.dump`/`write_text` to `output/` entity
  dirs. (3) periodic corpus-level audit (guard + referential-integrity + ID-uniqueness +
  dangling-mention + dedup-consistency).

- **[MED] data-quality follow-ups** — validation_stats time-series + regression alert (per
  entity×validator×BOOK); fail-open-allow counter; persisted corpus_quality_report dashboard; dedup
  precision/recall vs a labelled set; catcher per-book dimension; incremental referential-integrity
  audit (persist PK index + `_source_hash`, re-read only changed records).

- **[HIGH · data loss] supplemental prompt↔schema drift + silent drop** (found by the 2026-10-24
  RoerDams Phase-1+2 validation run). `extract_supplemental` validates each LLM response against
  the single-shape `SUPPLEMENTAL_SCHEMA`; on a `ValidationError` it logs ERROR, `break`s, and
  returns nothing → the sub-event's supplemental/bibliography is SILENTLY DROPPED (no OBSERV
  count of dropped sub-events — same blind-spot class as the enrichment no-op). Three drifts seen:
  (1) some responses are keyed-by-Sub-eventID (`{"<ULID>": {...}}`) but the schema is the flat
  `{Event_Name,EventID,Sub-event_Name,Sub-eventID,Supplemental_Material}` with
  `additionalProperties:false` → ULID keys rejected as "do not match '^_'"; (2) schema REQUIRES
  `Supplemental_Material[].reference_number` but the `supplemental.yaml` prompt example never emits
  it → "reference_number is a required property"; (3) prompt emits `classification` while the schema
  enum field is `content_class` (reconciled downstream, but still drift). Fix: reconcile prompt↔
  schema (align required fields + the keyed/flat shape + classification/content_class), and add a
  `supplemental.dropped` OBSERV counter so a drop is LOUD. Caught live; captured not fixed (run was
  in flight).

---


- **[LOW · alert noise] `*-meta.yaml` sidecars rejected-to-review on every upload.** The trigger's
  content-type split treats per-chapter `chapter*-meta.yaml` as "unrecognized type" -> writes a
  `needs-review/unrecognized-type/<book>.json` marker + sends an alert EMAIL every run (observed on
  the RoerDams runs). Harmless to extraction (the `.md` content parses; Phase 1 completes), but it
  spams the operator. Fix: recognize `*-meta.yaml` as a known metadata sidecar in the trigger and
  skip it QUIETLY (no needs-review marker, no alert).

## 6. LOWER-PRIORITY / FUTURE

- **[MED] Promote `source_section` article endnote refs → `bibliography`, deduped by URL.** One
  operation spans ~15 chapters → ~2,000 duplicated reference entries; URL-keyed records collapse that
  (sections keep lightweight reference-ID pointers). Related: canonical `operation` entity holding
  article+refs+media once (measure fan-out first).

- **[MED] Archive.org metadata enrichment** (publisher/ISBN/date/creator/rights from the SAME
  `/metadata/{id}` call; OpenLibrary/Crossref) + bibliography human-disposition UI
  (`review_queue.json`) + Amazon metadata for confirmed books.

- **[LOW] external_maps license config exposure; code-quality refactors (ecs_entrypoint→ecs_modules,
  consolidate find_duplicate_*.py, phase3 main complexity); mypy --strict on new files; atomic
  `_update_manifest`; s3 skip-existing for book-scoped Phase 1; `.gitignore` trailing newline.**

- **[LOW] data-quality tail:** cross-source corroboration flags across the 3 overlapping ETO books;
  corpus-wide queryable `confidence` field; doc-hygiene (ULID-repair scope warning, audit invariant
  defs).

- **[REGRESSION PROTECTION]** smoke-test CI (Phase 1→2→3 on chapter99); 50-thread stress test;
  prompt-schema contract tests across all 27 YAMLs; phase-coordination integration test; merge-output
  golden tests; enforce mypy --strict on new files.

- **[FUTURE]** Postgres/pgvector + RAG (must expose `bibliography.source_qualification` as a queryable
  facet); DynamoDB-removal completion; true multi-job concurrency (`CONCURRENCY_AND_NAT_SPEC.md`);
  Step Functions orchestration; Postgres dialect adapter for `src/loader`; new entity types
  (Economic/Policy); UK National Archives; model routing (grok-3-mini for simple extractors);
  reprocess already-imported data after Phase-0 routing; OCR render-DPI 192→300 + augmenting Chandra;
  SHAEF OB map corpus gaps (NARA acquisition, not code).

---
---

### Prompt / schema quality follow-ups (from the 2026-10-22 prompt review)
- **[ENHANCEMENT] Biography provenance linkage** — people `biography_sources` ties only to a
  free-text book title: no `Sub_eventID`/`MentionID` link to the event/mention it was extracted
  from, and no `BibliographyID` when the source is a footnote/endnote (footnotes→bibliography DO
  carry a `BibliographyID` ULID). Add those refs so bio claims join the event + bibliography graph.
  Also tighten the shared `EVENT_MENTIONS_SCHEMA` + `units_served` — they're under-specified vs what
  the prompt emits (extra fields pass only because there's no `additionalProperties:false`).
  Requires a `people` schema version bump + migration. (steer: biography provenance)
- **[WARNING] Wire situational_geocode into the Phase-3 cascade** — `src/enrichment/situational_geocode.py`
  exists (handles "Aachen Gap"/ridge/bridgehead relative features) but is NOT in the
  nominatim→hill→grok cascade; situational places currently resolve via the generic Grok fallback.
- **[NIT] Bump black → 26.10.1** (CVE-2026-114134) in requirements-ci.txt; review any reformatting.
- **[NIT] Add a general yaml-lint step to gate.sh** — the gate validates CFN (cfn-lint) + JSON
  (json-lint) but NOT general YAML; prompt/config/search_queries YAML is only safe_load-checked at
  deploy + in tests, so a malformed prompt YAML (e.g. a colon-in-list-item) passes the gate's lint
  phase. Add safe_load over prompts/*.yaml + search_queries/*.yaml + config*.yaml.
- **[NIT] places.yaml / source_section.yaml task-line** still say "WWII text" after the system
  prompt was de-scoped to any-theater — drop the residual era label for full consistency.

## 7. COMPLETED

- **✅ Provenance restored (PRs #636, #635, #634 — 2026-10-30):** people/places prompt examples now render REAL ULIDs
  (braced {event_id}/{sub_event_id}; render_prompt substitutes structural placeholders inside the injected schema);
  Phase-3 downloads content/<book>/ so event_provenance stamp resolves SourceSectionID; supplemental review_queue
  write creates its parent dir; groups persist group_name + equipment coerces name->common_name. (Validation re-run
  in flight.)

### §1.0d WARNING — batch count-mismatch retry (PR #529, 2026-10-21)
A batch result count-mismatch marked all requests failed, then the 20%-systemic guard skipped retry -> whole batch zeroed. _map_ordered_results now returns (failed_ids, count_mismatch) and _retry_failed_batch's force=True bypasses the guard on a mismatch (it's a pagination/envelope problem, not model failure). Genuine-systemic guard unchanged for force=False. +3 tests. Gate PASS (2581 tests, bandit 0H/0M); both reviewers PASS. Follow-up: the AWS --retrieve-only path (ecs_entrypoint._map_batch_results_by_index) has the same class (SKIP_RETRY=1).

### §1.0d WARNING — dedup merge crash-atomic (PR #528, 2026-10-21)
do_merge (people) + merge_generic (generic) now persist the merged primary (validated, crash-atomic) BEFORE deleting each secondary; a blocked/crashed primary write keeps the secondary intact (no mention loss). People merges into a deepcopy candidate (adopt only on success); generic snapshots + rolls back the shared mentions/aliases/seen state on block (no cross-iteration double-count). +4 regression tests (block tests fail against main, pass with fix). Gate PASS (2579 tests, bandit 0H/0M); both reviewers PASS round 2.

### §1.0d HIGH — ecs run_task placement failures + retrieve-only SIGTERM (PR #527, 2026-10-21)
All 3 ECS launch paths now detect a FARGATE_SPOT placement failure ({tasks:[],failures:[...]}; run_task doesn't raise): trigger _launch_ecs_task raises+releases the lock, batch_poller _handle_submit returns an error, _trigger_retrieve releases the retrieve-claim + returns False (not counted complete, no ~6h stall). run_retrieve_only sets _current_phase_script so a --retrieve-only spot-reclaim SIGTERM runs the emergency sync + lock release instead of a no-op. +3 regression tests. Gate PASS (2575 tests, bandit 0H/0M); both reviewers PASS. Follow-up: ocr_merge_handler._route_to_oob same pattern (lower risk).

### §1.0d HIGH — locked_json sidecar flock (PR #526, 2026-10-21)
locked_json held flock on the data fd, but save()'s os.replace committed a new inode nobody locked -> cross-process lost-update race (regression from the §1.0c atomic-write) [H1]; create path 'w+' truncated before the flock [H2]. Fixed by flocking a stable sidecar <path>.lock (never renamed, survives os.replace) + reading the data file read-only after the lock. +2 real-process regression tests (fail against main, pass with fix). Gate PASS (2572 tests, bandit 0H/0M); both reviewers PASS.

### §1.0d HIGH — supplemental unverified-url store (PR #525, 2026-10-21)
supplemental._enrich_material now content-verifies a sequential_search hit (via bibliography_resolver._verify_match -> _verify_url_content: SSRF-safe fetch + Grok page match) before writing resource_urls; a non-matching hit is dropped with OBSERV supplemental.url_rejected. Fail-open only when no grok_client. +4 regression tests. Gate PASS (2570 tests, bandit 0H/0M); both reviewers PASS.

### §1.0d HIGH — DNS-rebind SSRF unit_patch + source_retrieval (PR #524, 2026-10-21)
Both download sites vetted the host then re-resolved on a plain get (rebind TOCTOU). Now capture the vetted IPs from resolved_host_is_safe and fetch via _pinned_get (pin to vetted IP, Host+TLS SNI=host, redirects off). unit_patch re-vets+pins at fetch time (keeps 15MB cap); source_retrieval pins both the GET and the 429-retry, passes the original query-preserving url, drops the dead session param. +tests (unit_patch 3, source_retrieval reworked 12, loopback regression updated). Gate PASS (2566 tests, bandit 0H/0M); both reviewers PASS.

### §1.0d HIGH — dedup_gate SNS topic-trust (PR #523, 2026-10-21)
dedup_gate_handler now validates each SNS record's TopicArn against DEDUP_COMPLETE_TOPIC_ARN (structured partition/service/region/account/name match) before honoring a dedup_complete signal, closing a human-review-gate bypass (spoofed/cross-account SNS could force-ungate Phase 3). CFN wires the ARN onto DedupGateFunction. +10 regression tests. Gate PASS (2562 tests, bandit 0H/0M, cfn-lint); both reviewers PASS.

### §1.0d HIGH — mdreview path traversal (PR #522, 2026-10-21)
mdreview_ui_handler _save_snippet/_get_page validated `book` via a new _safe_book() (canonical safe-token: fullmatch [A-Za-z0-9._-], reject ../ / \) before any S3 key build (400+OBSERV on reject), closing an arbitrary-S3-write / Phase-1 content-injection path. +10 regression tests. Gate PASS (2552 tests, bandit 0H/0M); both reviewers PASS.

### Radon grandfather reduction campaign (PRs #515–#521, 2026-10-21)
Per §1.0d steering ("genuinely grandfather intrinsically complex, fix other"): the radon allowlist
held 125 entries but only 3 carried a substantive intrinsic-complexity reason; the other 122 were a
dated bulk "C+ backlog 2026-10-20" bless. Reduced ALL 122 below radon C via behavior-preserving
helper extraction (NOT re-grandfathering), in 7 file-clustered PRs — each gate-green + concurrent
code+security review (both PASS) + CI + merge:
- #515 wave1: openserp_enrichment(10) + bibliography_resolver(10) [review caught + fixed a NARA
  transient-cache-poison regression; +tests/test_nara_transient_cache.py]
- #516 wave2a: grok_client(4) + casualties(4) + bibliography(4)
- #517 wave2b: trigger_handler(6) + equipment(4) + enrich_biographies(4) [review caught + fixed a
  phase1 no-launch→launch-all collapse; +tests/test_trigger_phase1_no_launch.py]
- #518 wave3a: 10 extraction modules (23 fns); #519 wave3b: dedup_ui/semantic_flags/award/surname/
  merge/nat/reaper (15 fns); #520 wave4a: 19 utils/ingestion/lambda singles; #521 wave4b: final 19
  extraction/enrichment singles.
Allowlist drained 125 → 3. The 3 remaining are genuinely intrinsic (documented): group_resolver
.resolve_group_id C19, url_fetch._do_fetch C18, places_grok_geocode._toponym_from_prose C16.
Two real regressions were caught by the concurrent-review discipline (proving blind extraction is
unsafe) — both fixed with regression tests before merge. Gate green throughout (2542 tests, bandit
0H/0M). The C+ gate now fails any NEW C+ unless it is deliberately added to the 3-entry intrinsic
allowlist with a reason.

### §1.0d CRITICAL — dedup reference-rebase (PR #514, 2026-10-21)
dedup merges deleted the secondary entity file but failed to rebase its event sub-event refs (3 paths:
wrong id_field for groups, PK-name-as-ref_key for generic merges, no-rebase in the Lambda UI). Fixed in
src/dedup/merge.py (real-PK resolution + on-disk ref_key map where the group array is "peoplegroups" per
batch_parallel key_map + OBSERV on noid/unknown/zero + equipment cross-entity-only rebase), ecs_entrypoint.py
(people_groups id_field→GroupID), lambda_handlers/dedup_ui_handler.py (storage-based corpus rebase,
output/content listing, rebase-before-delete). 9 new tests; pruned 5 stale radon grandfathers. Gate PASS
(2536 tests); concurrent code+security reviews both PASS (1 NEEDS_CHANGES round caught the groups→peoplegroups
on-disk key + the empty-prefix listing). Follow-ups (pre-existing) tracked in §1.0d.

### Phase-2 code+security review program (2026-10-20) — ALL findings merged
8 concurrent sub-agents -> 5 root patterns (docs/current/PHASE2_REVIEW_2026-10-20.md). Every finding
fixed on its own branch -> concurrent code+security review (both PASS) -> CI -> squash-merge, each
with added OBSERV; data fixes regenerate only on a NEW run:
- #475 PATTERN 1 (batch 0-persist: in-task positional mapping + casualties stable cache_key)
- #476 PATTERN 2 (SSRF: 12 sites -> url_hygiene.safe_get/fetch_text_safe/fetch_image_bytes)
- #481 PATTERN 3 (path traversal: dates/places/equipment -> hardened sanitizer + stem length cap)
- #483 PATTERN 4 (silent drops: dates year-veto/casualties keywords/people_groups wrapper/supplemental
  dedup+hybrid/supplemental_info dict->Path/write-return honored)
- #482 trigger-Lambda (key SAFE_TITLE / SNS full-ARN trust / expected_count gate / dedup prompt-
  injection / _update_manifest optimistic-concurrency race)
- #484 orchestration (credit-leak release-on-any-failure / weather processed-gate / partial-failure
  alert / unbounded-batch cap / image decompression-bomb guard)
- #485 concurrency/crash-safety (grok_client cache lock / atomic locked_json.save both variants /
  run_scope corrupt-record visible downgrade)
- #486 PATTERN 5 (dead-code: delete concurrent.py + combined_map_search.py orphans + file_lock dead
  Dynamo machinery; dedup max_tokens -> GROK_MAX_TOKENS; 456 lines removed)
- #477/#480 radon gate no-op fix + simplified ALL 15 D+ and 24 of 27 high-C(16-20) blocks (+3 INHERENT
  documented w/ content-hash resurface mechanism)

### Radon complexity + dependency hardening (2026-10-20)
- **~~radon gate silent no-op + complexity debt~~ ✅ #477 + #480** — fixed BOTH no-op greps
  (`--show-complexity`); simplified all **15 D+** blocks below D (#477) and evaluated all **27
  high-C(16-20)** (#480: 24 simplified <C16; 3 INHERENT documented). Prominent C surfacing + the
  INHERENT content-hash **resurface mechanism** (`scripts/radon_inherent.py` + `.radon_inherent`:
  suppress-unless-modified). C stays non-blocking. Behavior-preserving; 2399 tests. Also fixed an
  orphaned `ctype` NameError surfaced in `_search_nara` during the refactor.
- **~~pillow CVEs~~ ✅ #478** — bump pillow 12.3.0→13.0.0 (8 CVEs); usage is stable core API only.
- **~~certifi CVE + urllib3 undeclared + audit gap~~ ✅ #479** — pin certifi==2026.10.19 (fixes
  PYSEC-2026-4320), declare urllib3==2.8.0 (imported directly in http_pool.py), add a FULL-ENV
  pip-audit (catches vulnerable transitive deps the `-r` audits missed), and document the one
  accepted ignore (PYSEC-2026-2447 diskcache pickle RCE — no fix exists, low exposure, remove-on-fix).

### Clean-XChannel-E2E data-quality fixes (2026-10-19 / 2026-10-20)
- **~~CRITICAL optional-entity-zero~~ ✅ #461** — `_call_and_parse_*` swallowed `BatchModeCollecting`
  on the collect pass → extractor empty → marked processed → retrieve skipped. Re-raise before the
  broad except (casualties/weather/equipment/logistics + chunked_extract); + people bio same swallow.
- **~~Optional-entity UNDER-persist (weather/logistics)~~ ✅ #468** — over-strict parse filters
  (weather required date AND place; logistics required a wrapper + strict validate) dropped extracted
  mentions. Relaxed: keep on descriptive content, accept both shapes, coerce enum defaults, drop only
  empties. (equipment=0 is a SEPARATE open cause, §1.1.)
- **~~A000 bibliography title derivation~~ ✅ #460** — `_derive_bib_title` + `backfill_bib_titles()`;
  live 57/57 titled.
- **~~bibliography=0 (footnote text dropped)~~ ✅ #467** — importer emits the `<h3>Footnotes</h3>`
  `name=fnN href=#cnN` definition anchors as the parser's `[N](url#cnN) text` form + bracket-strip.
  Offline-proven 0 → 1,387 stored.
- **~~Enrichment source attribution (groups)~~ ✅ #462** — group `enrichment_data._source`; OBSERV
  enrich.persisted; excluded from the diff.
- **~~Person identity (no name/PersonID)~~ ✅ #463** — merge-path integrity guard +
  `backfill_people_identity()`; live S3 fixed, 0 remaining.
- **~~Image capture (1/page) + full-res + maps~~ ✅ #469** — dedupe on URL not empty-alt;
  HTML-combined `[![alt](FULL)](<THUMB>)` branch; prefer the full-res `<a href>` (7-13×); maps classify
  under `/maps/`. 1→10 images/page.
- **~~Download image/map bytes (SSRF-safe)~~ ✅ #470** — `fetch_image_bytes` (clean_url + resolved-IP +
  no-redirects + 25MB cap + content-type/magic-byte); all 3 downloaders rewired; stored under output/.
- **~~Descriptive captions + relative local_copy~~ ✅ #471** — caption adjacent to `<a><img>` →
  image_title/description; relative local_copy; caption cross-assignment guard + two-pass replace.

### Phase-2 observability (2026-10-19)
- **~~P1 silent steps~~ ✅ #464** — maps/images/source_section emit outcome OBSERV on every exit path.
- **~~P2 thin steps~~ ✅ #465** — dates/places/events roll-ups + places.parent_link + people
  enrich.persisted.
- **~~P3 end-of-run summary + schema~~ ✅ #466** — `OBSERV phase2.summary` per-type counts (CORE
  zero=LOUD, optional=INFO) + standardized colon-format lines. Audit:
  `docs/current/dataquality/PHASE2_OBSERVABILITY_AUDIT.md`.
- Tooling: `scripts/batch_metrics.py`; `scripts/ai_review.sh`.

### Identity / id discipline + session review fixes (2026-10-18)
- **~~#448~~** `_fix_invalid_ulids` skips reference ids; **~~#454~~** unconditional identity-id skip;
  **~~#455~~** HIGH#7 two-part id discipline (mint NEW server-side; honor/verify EMBEDDED;
  reconcile_echoed_keys); **~~#449~~** geocode place-name extraction; **~~#456~~** entity-context
  disambiguation; **~~#450~~** read-side index.json path-traversal; **~~#457~~** TOCTOU/symlink
  containment; **~~#451~~** narrative-relocation archive-not-delete + lifecycle; **~~#452~~** Phase-2→3
  reconcile drain barrier; **~~#453~~** HyperWar SSRF guard; **~~#458~~** Ruff --select B; **~~#459~~**
  declare expected_count for large runs.

### Bibliography deterministic-primary program (2026-10-14/16) — merged
- **~~0-bibliography multi-session blocker~~** — stable supplemental cache key (#401), Dynamo
  `__contains__` fall-through (#402), submission-order batch mapping (#393/#395), deterministic primary
  pass from parsed footnotes (#405), S3-authoritative within Phase 2 (#413).
- **~~Capture footnote/endnote CITATION TEXT~~ ✅ #381**; **~~batch-collect swallow~~ ✅ #383**;
  **~~refs on sub-events~~ ✅ #390**; **~~prompt-determinism/cache key~~ ✅ #399/#401**.
- **~~Within-chapter back-reference resolution~~** (short-title repeat, ibid/op.cit.+content, bare
  stubs); **~~citation detector military/archival signals~~**; **~~RG-lookup resolved=9~~ #424**.

### Data-quality program (2026-10-10/11) — merged
- **~~Referential-integrity enforcement~~** (schema-derived edges, end-of-phase), **~~deterministic
  EventIDs~~ #325/#326**, **~~semantic flags~~** (flag-not-block), **~~catcher low-info allows~~**
  (allow_empty/allow_flagged), **~~Phase-1 parse guard~~**, **~~read/write validator reconciliation~~**,
  **~~referential-integrity audit tool~~**.

### NAT / infra / cost — merged
- **~~Idle-infra teardown root cause~~** (openserp_manager excludes own tasks) + reaper
  (code-complete; deploy pending §4). **~~Job-aware NAT leases~~**. **~~NAT flapping single-decider~~
  #409**. **~~Chatbot GuardrailPolicies~~ #347**. **~~Operator email alerts~~ #411**. **~~Re-OCR
  off-by-one corpus~~** + **~~OCR page-range off-by-one~~** (`_to_chandra_range`). **~~Chandra
  wall-clock/chunk-collision/table-auto-route/libgomp1/NAT-teardown-while-pending/torch-CUDA-hash~~**
  (St. Vith, 2026-09-23).

### Enrichment / verifier — merged
- **~~Verifiers fail-OPEN → fail-CLOSED~~**, **~~phase3_handler double-enrich removed~~**,
  **~~bibliography duplicate-citation dedup~~**, **~~narrative-misclassified-as-citation guard~~**,
  **~~OpenSERP v0.6→v0.8.12~~**, **~~Chromium in Phase-2 image~~**, **~~component-version preflight~~**,
  **~~ULID same-invalid→one replacement~~**.

### Phase 0 / loader / storage / deploy — merged
- **~~Phase 0 auto-trigger + type-routing~~** (OCR auto-submit → merge → Phase 1; CONVERT/VIDEO write
  the chapter contract; structured-ref → deterministic OOB parser). **~~Deploy main to AWS~~** (S3
  AES256 + DenyInsecureTransport, pandoc --sandbox, AV scanning, EBS encryption). **~~Loader
  SQLite-guard + Postgres-adapter doc~~**. **~~Storage role-split review~~** (`STORAGE_REVIEW.md`).

### Slack / notifications / batch — merged
- **~~Slack formatter detail parity + triage~~ #347**, **~~SNS ListSubscriptions IAM~~**,
  **~~batch-poller key visibility~~**, **~~batch ALL entity types~~**, **~~prompt versioning in cache
  key~~**.

### Older completed (2026-06)
- 2026-06-15: BatchCollector thread safety, DynamoDB dual-write reconciliation, Supplemental key
  normalization, bibliography resource_urls array, event mention dedup, merge_generic cross-refs,
  optional-extractor idempotency, zero-count warning, truncation recovery, update_event_refs
  corruption, phase-1 chunk overlap, system-prompt centralization, all prompts externalized (27
  YAMLs), orchestration tests, BackgroundSync skip-enriched, search queries externalized.
- 2026-06-13/14: additionalProperties drops, Supplemental key mismatch, index.json race,
  BackgroundSync partial-write, Phase-3 no-op on review-all, Phase-3 NAT teardown guards, EIP leak,
  per-book queues, prompts→YAML, deploy-time prompt validation, download/cache logging, Trivy
  blocking, entity validation skip-list.
- 2026-06-03/09: DynamoDB entity store, incremental dedup, cost optimizations, phase transitions,
  batch states, empty guards (see git log).

---

Not-code (deferred): SHAEF OB map gaps (NARA acquisition). Full historical detail for any collapsed
item is in git history + the dated design docs under `docs/current/` and `docs/archive/`.

### Phase-2 FRESH RE-REVIEW (2026-10-20b) — DONE items (moved from §1.0c)
- **[DONE · PR #513] [SUGGESTION] openserp verification-gate image mismatch.** _verify_single_image returns the
  APPROVED image url; threaded through _verify_map_relevance -> _process_search_result -> _download_map_image,
  which downloads THAT exact vision-approved image (SSRF-safe) instead of a re-extracted map_images[0].
- **[DONE · PR #512] [SUGGESTION] prompt single-pass substitution + _submit_ocr SAFE_TITLE validation.**
  render_prompt/render_search_queries single-pass regex (untrusted {token} in a value no longer re-substituted);
  _submit_ocr derives the name via _safe_stem_from_key (intake-invariant). Both below C.
- **[DONE · PR #511] [SUGGESTION] award/wayback untrusted-fetch byte-cap + wayback archived-host recheck.**
  award_polite_source.polite_get streams + 10MB-caps the body; wayback.fetch_archived SSRF-rechecks the
  archived_url host + allow_redirects=False + caps the body. +fail-safe on mid-stream read error.
- **[DONE · PR #510] [WARNING · follow-up] clean_url unicode/IDNA homograph hosts.** FIXED: _host_is_safe now
  NFKC-normalizes + IDNA-encodes the host before classification (ideographic-dot/fullwidth loopback caught; legit
  IDNs pass via punycode). Original: The standalone
  validator (url_registry keys / bibliography+source_retrieval pre-fetch gates) accepts ①②⑦.0.0.1 /
  127。0。0。1 (ideographic dots) / fullwidth digits, which NFKC/IDNA-normalize to 127.0.0.1. Backstopped at
  the FETCH layer (resolved_host_is_safe blocks them), so NOT a live fetch SSRF — but the standalone gate
  should NFKC-normalize + host.encode('idna') before classification. (sec_batch_core #507 adversarial probe.)
- **[DONE · PR #509] [WARNING] transient-error → permanent-negative cache poisoning (remaining 3 sites).**
  Completes the class after #501: situational_geocode (TRANSIENT sentinel on grokipedia/wiki),
  bibliography_resolver._search_openserp_archive (saw_200 gate — only negative-cache a real 200-miss, not a
  503-while-scaling outage), and nominatim/places_grok_geocode (GeocodeResult.transient -> non-terminal
  geocode_error status, RETRIED on non-force re-run, instead of a permanent no-TTL not_found). +OBSERV.
- **[DONE · PR #508] [SUGGESTION batch]** weather_central._add_event_mention hard-indexes m["Sub_eventID"] (KeyError on legacy —
  places got the #495 fix, weather missed); enrich_groups.enrich_group reads `name` but schema stores
  `group_name` → never enriches a schema-valid record; openserp_maps downloads map_images[0] not the vision-
  APPROVED image + _check_duplicate_map lacks the sibling try/except json.load guard (a malformed file drops a
  whole place); supplemental _normalize_enums coerces an INVALID content_class to "citation" (should be
  "ambiguous"); award_polite_source.polite_get / wayback.fetch_archived lack a byte cap / resolved-IP recheck;
  prompt_loader & search_query_loader order-dependent str.replace (untrusted `{token}` re-substitution);
  storage.LocalStorage._resolve returns the UNRESOLVED path; _submit_ocr skips _SAFE_TITLE_RE; write_json_with_lock
  omits fsync. Batch the cheap ones.

- **[DONE · PR #507] [WARNING] clean_url accepts dotted-hex/mixed-radix IP encodings** (`0x7f.0x0.0x0.0x1`, `127.0.0.0x1`) →
  True, contradicting its docstring; backstopped at fetch by resolved_host_is_safe but clean_url is ALSO a
  standalone registry-key/pre-fetch-gate validator. Detect any hex/mixed-radix numeric host and coerce via
  inet_aton. + safe_get byte cap runs AFTER gzip auto-decompression (small highly-compressing body → 10MB in
  memory per concurrent fetch); cap compressed bytes (resp.raw.decode_content=False) / check Content-Length.
- **[DONE · PR #506] [WARNING] write-guard blind spot: locked_json.save() bypasses _validate_entity.** The schema guard runs
  only in write_json_with_lock/write_parsed_with_guard; ALL merge paths persist via locked_json (equipment/
  people/places/people_groups/dates/weather/enrich_write/batch_parallel) which only annotates → a merge that
  drops a required field is written unguarded. Call _validate_entity in save() + reconcile version stamping.
- **[DONE · PR #505] [WARNING] truncated >100K response not evicted from cache** → permanent GrokTruncationError on re-run
  (_handle_truncation_error pops only in the <100K else-branch; the >100K path raises without eviction). Move
  the cache.pop out so both branches evict. + BatchCollector.add silently drops a request_id(=cache_key)
  collision → OBSERV at WARNING (latent optional-entity-zero).
- **[DONE · PR #504] [WARNING] trigger_handler** — topic-trust gate bypassed for S3 keys (_extract_records appends a record's
  keys to s3_keys BEFORE/independent of _trusted_topic_name → untrusted-topic keys reach manifests + _route_topics);
  move the append inside the trust gate. + _acquire_task_lock stale-reclaim TOCTOU (two invocations both reclaim
  → double-launch); make the delete conditional on the observed timestamp.
- **[DONE · PR #503] [WARNING] batch_poller** — _check_stalled stall-alert rate-limit assumes a 5-min poll cadence
  (`int(age_hours)==int(age_hours-5/60)`) but the schedule is 15–30 min → hourly ">4h stalled" alert
  silently skipped; persist last_stall_notify_at. + 24h-stuck force-complete returns "complete" with
  success+error<total → ecs mapper count-mismatches → discards the completed fraction → full LIVE re-cost;
  map available results by position / alert N/total.
- **[DONE · PR #501] [WARNING] transient-error → permanent-negative cache poisoning (systemic) — tranche 1.**
  search_cache.TRANSIENT sentinel (cache_result no-ops on it) threaded through grokipedia, equipment_wikipedia,
  groups_wikipedia, noaa_weather so a transient timeout/5xx/429/exception is no longer negative-cached for 7d
  (grokipedia is corpus-wide). +OBSERV *.transient. 3 remaining sites tracked as a §1.0c follow-up.
- **[DONE · PR #500] [WARNING] logistics weather-impact link DEAD.** _build_entity_index reads TOP-LEVEL `description`,
  but weather stores it under `extracted_data.description` → weather_index is ALWAYS {} → _build_weather_impact
  never links (the new OBSERV weather_unmatched candidates=0 fires every time). Fix the index builder.
- **[DONE · PR #499] [WARNING] logistics _save_extraction path traversal via LLM date_start** — the ONE date-in-filename
  writer the Pattern-3 campaign (#492) missed. `date_str = extraction.date_start.replace("-","")` leaves
  `/`,`..`. Route through _safe_entity_filename + _contained_entity_path. +test.
- **[DONE · PR #498] [CRITICAL] Schema-version stamping ignored entity type.** ecs_entrypoint._stamp_file calls
  needs_migration/inject_metadata with NO entity → stamps the MIN version (2.24) → records permanently
  "needs_upgrade" + genuine current-schema violations ride the lenient write-guard path; the fast-path
  samples only a weather file (==min) so higher-versioned dirs are never stamped. Thread the entity
  through; make the fast-path per-entity (or drop it). +test.
- **[DONE · PR #497] [CRITICAL · corroborated] DNS-rebinding TOCTOU in the SSRF fetch layer.** safe_get /
  images.fetch_image_bytes / url_fetch._one_hop vetted the RESOLVED IPs via resolved_host_is_safe but
  then handed the HOSTNAME url to requests, which re-resolves at connect → a short-TTL rebind to
  127.0.0.1/169.254.169.254/RFC1918 slips through. FIXED: new url_hygiene._pinned_get pins the
  connection to a vetted IP (iterates the vetted list) while keeping Host + TLS SNI/cert = the original
  host; allow_redirects=False. +TLS-pin-seam + IP-fallback tests.

### 1.0b Phase-2 RE-REVIEW (2026-10-20) — ALL findings merged (moved from §1; was OPEN)

Fresh 8-agent re-review of the merged state. Merged fixes validated correct+complete in-scope; these
are NEW gaps (analyzer-invisible; bandit still 0H/0M). Ranked:

- **[DONE · PR #487] [CRITICAL · SSRF] grok_client.extract_json_with_image fetched an untrusted image URL unguarded.**
  session.get(image_url, allow_redirects=True) + unbounded .content; reachable LIVE in Phase 3 via
  search_external_maps._verify_single_image (img url from scraped page HTML). Pattern-2 + its grep
  test missed it (fetch is in grok_client via session.get, not a cluster-module requests.get). FIX:
  gate img_url in _verify_single_image (clean_url+resolved_host_is_safe like unit_patch._safe_url) +
  harden extract_json_with_image to route bytes via images.fetch_image_bytes; + a bytes-level
  regression test (not a source grep).
- **[DONE · PR #488] [HIGH] chunked_extract.extract_with_chunk_halving halving not recursive** — now
  recurses to size-1 (base case drops only the oversized item); +OBSERV. FOLLOW-UP found by #488
  review (NEW HIGH, below): the halving is currently LATENT in production.
- **[DONE · PR #489] [HIGH · found in #488 review] Inner extract_fns swallowed GrokTruncationError -> halving
  never engaged + whole chunk dropped to {}.** FIXED: casualties/weather/logistics inner fns now re-raise
  GrokTruncationError before the broad except, so the #488 recursive halving engages. Original (stale): casualties._call_and_parse_casualties / weather._call_and_parse_weather
  / logistics._extract_* catch `except Exception` (which includes GrokTruncationError) and return {}
  after retries, so GrokTruncationError never reaches extract_with_chunk_halving — a truncated chunk
  silently yields {} (worse than the ~half-drop just fixed). FIX: re-raise GrokTruncationError from
  those inner fns (before/bypassing the broad except) so the recursive halving actually runs. Pre-
  existing (the old halving was equally never-triggered); #488 made the halving correct + ready.
- **[DONE · PR #490] [HIGH] batch_api.retrieve_results_ordered valid-gate too strict** — required finish_reason=='stop'
  but default is 'unknown' -> valid content discarded + re-billed. FIXED content-first: BatchResult.ok/status
  treat non-empty content as valid unless finish_reason=='length' (truncated) or error/empty. Applied to BOTH
  retrieve paths (_map_ordered_results AND ecs_entrypoint._map_batch_results_by_index — the latter still hardcoded
  'stop', the identical bug on the async retrieve-only path, caught in review). Original (stale):
- **[DONE · PR #492] [WARNING] Pattern-3 path-traversal INCOMPLETE** — FIXED: weather_central._create_new_weather_file
  (place_name) + casualties._save_casualty (type clamped to enum) now route through _safe_entity_filename +
  _contained_entity_path + OBSERV. Original (stale): weather_central._create_new_weather_file +
  casualties._save_casualty build filenames from LLM values without _safe_entity_filename/_contained
  (same class fixed elsewhere). Route both; clamp casualties 'type' to its enum.
- **[DONE · PR #493] [WARNING] Decompression-bomb guard scoped too narrowly** — FIXED: hold MAX_IMAGE_PIXELS across
  the WHOLE decode (verify+convert+resize) in equipment._verify_media_with_vision + grok_search_maps.verify_map_with_vision;
  cap _compute_image_hash's average_hash decode; DecompressionBombError now caught->reject+OBSERV. Original (stale): equipment convert/resize + grok_search_maps
  restore MAX_IMAGE_PIXELS before the rasterizing convert/resize; equipment._compute_image_hash has no
  cap. Hold the cap across the whole decode block (mirror unit_patch).
- **[DONE · PR #491] [WARNING] SSRF via url_hygiene bypass (codebase-wide follow-up)** — FIXED. Routed every
  UNTRUSTED-host fetch through url_hygiene: bibliography_resolver._verify_url_content (LIVE, ->fetch_text_safe,
  block=hard-reject), enrich_biographies._preserve_person_photo (->safe_get) + _fetch_url_content (->fetch_text_safe,
  the live source-url fetch the #491 security review caught as MISSED; falls through to the wayback/archive.org
  fallback on block), equipment._download_media_file (->safe_get) + _extract_image_urls_from_page (->fetch_text_safe),
  source_retrieval._download_to_quarantine + award_polite_source.polite_get (validate-only clean_url+resolved_host_is_safe
  + allow_redirects=False, fetch ORIGINAL url so a significant query isn't stripped), url_extractor.fetch_url (->safe_get).
  Trusted-LITERAL-host fetches (archive.org/gutendex/grokipedia-search/commons/wikipedia-API/NARA/nominatim/open-meteo/
  wayback->archive.org) are NOT SSRF and left as-is (host is a hardcoded literal, review-confirmed). +OBSERV on every
  block. Security re-audit: ZERO untrusted-host fetches remain unrouted across src/. Original (stale):
  _verify_url_content (LIVE) + source_retrieval._download_to_quarantine (latent) + (flagged by the
  #487 security review) enrich_biographies:358, equipment:1071/1172, supplemental_search:108/163,
  award_polite_source, grokipedia, situational_geocode, wayback, url_extractor — all session.get/
  requests.get a DISCOVERED/external URL with allow_redirects=True, bypassing url_hygiene. Route each
  through fetch_text_safe/fetch_image_bytes/clean_url+resolved_host_is_safe. (The CRITICAL vision
  path is fixed in #487; these are the remaining untrusted-fetch sites.)
- **[DONE · PR #494] [WARNING] logistics** — FIXED: _save_extraction adds the dedup key only AFTER a confirmed write
  (was poisoning on write-fail -> silent drop); _rebuild_index keys on the full ULID-unique stem (was collapsing distinct
  sub-events); _build_weather_impact uses normalized exact / word-bounded match (was bare substring -> 'rain' matched
  'terrain'). +OBSERV on each. Original (stale): _save_extraction poisons dedup key on write-fail (data loss);
  _rebuild_index key collapses distinct sub-events; _build_weather_impact bare-substring WeatherID.
- **[DONE · PR #495] [WARNING] core nits** — FIXED: places._add_event_mention uses m.get('Sub_eventID') (was KeyError on
  legacy mentions) + OBSERV; people._normalize_name collapsed to a single @lru_cache (dead inner removed). Original (stale):
  places._add_event_mention hard-indexes m['Sub_eventID'] (KeyError on
  legacy); people._normalize_name stacked double-lru_cache (inner dead).


### 1.0 Phase-2 code+security review findings (2026-10-20) — ALL merged (moved from §1; was OPEN)

Audit of ALL Phase-2 code (8 concurrent code+security sub-agents, synthesized to 5 root patterns).
bandit baseline 0H/0M/23L unaffected — all logic/design. Ranked:

> ✅ **§1.0 COMPLETE (2026-10-20):** every review finding fixed + merged, each with concurrent
> code+security review PASS + CI green, each with added OBSERV. Patterns 1 (#475), 2 (#476),
> 3 (#481), 4 (#483); trigger-Lambda (#482); orchestration (#484); concurrency/crash-safety (#485);
> PATTERN 5 dead-code (#486); + the radon-gate/complexity campaign (#477/#480). Caveat: data fixes
> only take effect on a NEW run. Follow-ups spun out to §4 (deps/openserp/ULID[:8]/write-guard
- **[DONE · PR #496] Radon gate threshold D->C** — run_radon now HARD-FAILS any non-allowlisted C-or-worse block
  (was D+ only). The 139-block C+ backlog is grandfathered in scripts/.radon_inherent (body-hash) via a new
  radon_inherent.py `seed` subcommand; only NEW/MODIFIED C+ fails; D+ never suppressible; +fail-closed if radon absent.
> blind-spots/_submit_ocr) + a minor housekeeping item (stale `concurrent.py` string literals in
> scripts/archive/qa/, dead cruft, optional cleanup).


- **[DONE · PR #477 + #480] radon gate was a silent no-op + complexity debt cleared.** `scripts/gate.sh`
  run_radon grepped `' - [D-F] '` (trailing space) but `radon cc --min C` emits `- D` (no trailing
  space) → NEITHER the D+ hard-fail NOR the C+ surfacing ever fired. FIXED: add `--show-complexity`.
  Then cleared the debt it had been hiding — **ALL 15 D+ blocks simplified below D** (#477: incl.
  F(42) bibliography_resolver._search_online_sources, E(33) supplemental.extract_supplemental, and
  D grok_client.extract_json / url_hygiene.clean_url / trigger_handler.handler+_run_task /
  merge.merge_generic / bibliography.reconcile_bibliography_dir / …) and **all 27 high-C(16-20)
  evaluated** (#480: 24 simplified below C16; 3 kept INHERENT — group_resolver.resolve_group_id,
  url_fetch._do_fetch, places_grok_geocode._toponym_from_prose — documented in-code). Gate now
  surfaces C PROMINENTLY (D+ listed+hard-fail; high-C listed w/count; C11-15 count-only) and has an
  INHERENT **resurface mechanism** (`scripts/radon_inherent.py` AST-body-hash + `scripts/.radon_inherent`):
  a listed INHERENT block is suppressed ONLY while unchanged; a logic edit changes its hash → it
  resurfaces for re-eval. Standing policy: **C is non-blocking** (surfaced, not hard-fail); ~130 C11-15
  blocks remain surfaced by design. Behavior-preserving refactors; 2399 tests green.

- **[DONE · PR #475] [CRITICAL · PATTERN 1 — batch 0-persist] In-task `submit_batch` maps results by
  xAI SERVER id, not our cache key.** ✅ FIXED (positional mapping via retrieve_results_ordered +
  casualties stable cache_key + chat_get_completion verified correct). NOTE: only regenerates on a NEW run. `batch_api.retrieve_results` keys by `batch_request_id`; `submit_batch`
  looks up by our sha256 key → valid content cached under unread keys → classified "missing" → 0
  persisted. Live-path sibling of the equipment=0 / optional-entity-zero family. FIX: use
  `retrieve_results_ordered()` positional mapping (like `ecs_entrypoint._map_batch_results_by_index`).
  SUB-ITEMS: (a) casualties.py:184-216,261 still has the non-deterministic-prompt cache-key bug
  (unsorted/sliced entity_context, no stable cache_key) → likely persists 0 next run; (b) ⚠️ VERIFY the
  `chat_get_completion` field — RESOLVED: verified correct (the successful XChannel AWS retrieval reads
  it as the only content path and produced real entities; a wrong name would have yielded 0 for all).

- **[DONE · PR #476] [HIGH · PATTERN 2 — SSRF] 12 unguarded `requests.get(allow_redirects=True)` on untrusted URLs**
  across openserp_maps (50,78,149,168,358,372), search_external_maps (98,138,317), grok_search_maps (99),
  validate_supplemental_urls (22), supplemental_advanced (231), fetch_endnotes (75-92,178) — bypass the
  repo's own `clean_url`+`resolved_host_is_safe`+`allow_redirects=False` guard (used correctly in
  maps.py/images.py). Reachable over NAT → metadata/internal hosts. FIX: one shared SSRF-safe fetch
  helper across all 12 (also closes the map-module duplication + image-bomb DoS).

- **[DONE · PR #481] [HIGH · PATTERN 3 — path traversal] In-process entity writers skip the hardened sanitizer.**
  dates.py:228-243 (worst — doesn't even strip `/`), equipment.py:~1975,2674, places.py:392 build
  filenames from LLM values via ad-hoc `.replace()`; `write_json_with_lock` mkdir(parents=True) on any
  path. FIX: route all through `batch_parallel._safe_entity_filename`+`_contained_entity_path` (#450/#457).

- **[DONE · PR #482] [HIGH · trigger Lambda] key-derived book bypasses `_SAFE_TITLE_RE`** (_seed_docs/_reconcile_pending/
  _queue_pending write unvalidated book to DynamoDB/S3 keys); **no SNS ARN/signature trust** (trust from
  topic-name suffix only); **manual `expected_count` is an unauthenticated scope-bypass**; **prompt-
  injection via uploaded file head flips the dedup decision**; **`_update_manifest` lost-update race**
  (get→union→put on pending.json). FIX per the doc.

- **[DONE · PR #483] [HIGH · PATTERN 4 — silent drops] over-strict filters + swallowed errors:** dates year-veto
  (<1919/>1955, dup in batch_parallel:221); casualties keyword pre-filter misses losses/fell/wiped out;
  people_groups wrapper-key assumption; supplemental `_append_to_file` re-appends narrative every pass
  (dedup on absent reference_source); supplemental `_has_document` drops hybrid citation half;
  supplemental_info_pipeline passes dict where Path expected → 100%% dead (always 0); write_json_with_lock
  return ignored in date_summary/people_consolidation.

- **[DONE · PR #484] [HIGH · orchestration] credit reservation leaks on poll/retrieve failure** (only released on submit
  failure); **weather+equipment lack the `_is_processed` gate** (re-extract every retrieve pass);
  **partial-failure batches silently drop the failed fraction** (no alert unless error>=total);
  **unbounded batch size**; **image decompression-bomb DoS** (equipment/grok_search_maps, no MAX_IMAGE_PIXELS).

- **[DONE · PR #485] [MED] concurrency/crash-safety:** grok_client `self.caches`/counters mutated without a lock;
  `locked_json.save()` truncates-in-place (not crash-atomic); write-guard is opt-in (some plain
  open()+json.dump bypass it); run-scope caller fail-open downgrades to magic-50 on read error (debug
  not warn).

- **[DONE · PR #486] [MED · PATTERN 5] dead code + duplication:** 5 overlapping map-search modules; concurrent.py dead
  (+ latent wrong-kwarg); dead Dynamo dual-write in file_lock; duplicated max_tokens / ignored timeout params.



## [LOW·data-hygiene] people index/duplicate_report count drift (observed 2026-10-24, overlapping-rerun artifact)
On the clean RoerDams rerun, output/people/ had **6 real person files** (Hodges, Brooks, Gerow,
Keppler, Cota, Wegelein — all distinct individuals), but `duplicate_report.json.total_people=7` and
`index.json` listed **8** entries. The dedup DECISION (0 duplicate_groups) is CORRECT — no two files
share an identity; cross-chapter recurrences were already merged at write time by
`_find_or_create`/`_merge_person`. The 6/7/8 drift is stale index/report entries left by the two
OVERLAPPING reruns (a record written by run-1 was merged/removed by run-2's write-time merge but its
index entry wasn't pruned). NOT a dedup bug. Follow-up: (a) confirm on a SINGLE clean run the counts
reconcile (6/6/6); (b) if index can outlive its file generally, add an index-prune or a
validate_phaseN check that `index entries == existing files` (B-level warn). Low priority — cosmetic
count only; no data loss, no false merge.

## [LOW·ci-flake] test_validation_performance perf tests use absolute wall-clock thresholds
test_hook_overhead_single_validation asserts `overhead < 1.0` (ms/validation) — an ABSOLUTE
wall-clock threshold that spuriously fails on a loaded/slow CI runner (observed 2.15ms in CI run
40245487333 while passing 3/3 locally at ~0.2s). Unrelated to any logic. Fix: make these perf
asserts RELATIVE (vs a measured baseline, like test_hook_overhead_batch's `batch < individual*1.5`)
or mark them @pytest.mark.perf / skip-in-CI, so CI isn't red on timing noise. Several asserts in
that file share the pattern (lines ~201, plus the elapsed-based ones).

## [MED] source_section derive failed for 2 sections (clean rerun e4b22d9)
"[phase2 step 1/5] Core extraction complete: 4 processed, 2 failed" + source_section.enrich errors=2:
"source_section derive failed for 0GDW2A077AXSFWX53MGV5VTT6G: <hash>" and 7EQS9MD8QY861A2VB8B0NZEYKX.
The error payload is an opaque hash (looks like a cache key / exception fingerprint, not a message) —
investigate what source_section derive raises for those 2 sections. Also benign: "Status server
failed to bind 10.0.22.19:8077 (Address already in use) — continuing" (port reuse, non-fatal).

## [MED·dedup-false-positive] place dedup is string-only at the Phase-2 gate (no coords) — Roscheid/Schmidt (deferred fix #2)
Clean RoerDams rerun flagged Roscheid vs Schmidt as a duplicate ("name similarity 67%") and gated
Phase 3 on it. It is NOT a duplicate — difflib.SequenceMatcher("roscheid","schmidt")=0.667 clears the
>=0.65 threshold by over-crediting scattered shared runs (sch, d). INHERENTLY string-ambiguous: the
SAME 0.667 is scored by the REAL variant pair "St. Vith"/"Saint Vith" (and St Lô/Saint-Lô is a real
dup at 0.43) — so NO string threshold/metric separates the false positive from real variants
(verified: bigram-Jaccard veto and threshold-raise each kill real dups too). ROOT CAUSE: the one
disambiguating signal — COORDINATES (Roscheid and Schmidt are ~40km apart; St.Vith/Saint-Vith are the
same point) — does not exist at the Phase-2 dedup gate because geocoding runs in Phase 3 (13/18 places
had geo_missing_coords). DURABLE FIX OPTIONS (pick when we tackle it): (a) run/augment place dedup
AFTER Phase-3 geocoding so the haversine distance check can confirm/veto; (b) add an LLM adjudication
step for name-only matches that lack coordinates (ask Grok "same place?", mirroring the group
enrichment disambiguation); (c) partial cheap: raise threshold to ~0.72 + add 'st./saint' to the
geo-prefix strip (still misses St Lô/Saint-Lô). Interim (done 2026-10-26): rejected in the dedup UI as
not-a-duplicate to unblock Phase 3. Two prior heuristic attempts were REVERTED (each introduced false
negatives) — do NOT re-attempt a string-only fix; use coords or LLM.

## [LOW·yield] OpenSERP group-image search returns HTML page URLs, not direct images (clean rerun 96c86bef)
During Phase-3 OpenSERP group enrichment, 28 image candidate URLs were vision.image_blocked
(reason=ssrf_or_invalid) and 0 images added — because OpenSERP returned SEARCH-RESULT PAGE URLs
(wikipedia.org/wiki/..., commons/wiki/Category:..., army.mil/*.aspx, ebay/itm, militaria product
pages), NOT direct image files. The image-fetch guard CORRECTLY rejects non-image content (not a bug —
SSRF/content-type guard working). But group-image yield is ~0. Follow-up (quality, not correctness):
tune the group image-search query templates / result parsing to prefer direct image URLs (e.g. target
commons File: / upload.wikimedia.org thumb URLs, or add an image-extraction step that resolves a page
URL to its og:image). Low priority.

## [MED·dedup] NEAR-duplicate content detection (MinHash/LSH) — same text across two domains, not byte-identical
We now have (a) GLOBAL EXACT content dedup at submit time: `fetch_review._register_content_hash_if_new`
claims `contenthash#<sha256>` across ALL sources/domains/titles, so byte-identical content submitted
from two different domains (or re-submitted under a host-derived title) is caught and skipped
(`deduped` count). What this does NOT catch: NEAR-identical pages — the common real case where the
same article at domainA vs domainB differs only in boilerplate/ads/whitespace/tracking params, so the
sha256 differs and both pass. The existing lexical tools (SequenceMatcher edit-distance, Jaccard
token-overlap, Grok same/different classifier) run on short strings (names/titles), NOT on document
bodies, and we have NO vector-embedding/cosine or MinHash path.

FIX (b): add a MinHash + LSH near-duplicate gate over document SHINGLES at affirm/submit (and reuse
for book submissions). Sketch: shingle the quarantined body (k-word or char n-grams) -> MinHash
signature -> query an LSH index (keyed in the same DynamoDB cache table, e.g. `contentlsh#<band>`
buckets) for an existing signature with Jaccard >= threshold (~0.8) -> on hit, skip/merge instead of
ingesting a second near-copy; on miss, add the signature and proceed. Keep it FAIL-OPEN (a borderline
score never blocks a legit submit) and pin `datasketch` at an exact version (MinHash/MinHashLSH). An
optional later tier: sentence-embedding cosine for PARAPHRASE-level (semantically-equal but lexically
different) dedup — heavier, only if paraphrase dup becomes a real problem. Ref: the user's text-similarity
survey (Jaccard/Euclidean/Cosine + TF-IDF/Word2Vec/Sentence-Transformers); MinHash/LSH is the scalable
near-dup tool for document bodies, which that survey does not emphasize but is the right fit here.
Ties to: fetch_review.submit_affirmed_batch (where the exact (a) gate already lives).


## [LOW·security-hardening] _download_s3_file has no path-traversal containment (ecs_entrypoint)
Pre-existing (not introduced by the stale-tree validate fix). ecs_entrypoint._download_s3_file composes
WORKDIR/obj["Key"] from the raw S3 key with no realpath containment check, so a crafted "../" key could
escape /tmp/pipeline. Keys come from the pipeline's OWN trusted bucket (low risk), but defense-in-depth:
assert the resolved local path is within WORKDIR before download_file. Flagged by the dual review of the
phase3-durable-revalidate fix; out of scope there.


# MULTIMODAL INGESTION GAPS — prioritized epic (from XChannel ch8 analysis, 2026-10-27)  ✅ COMPLETE (M1-M8 all merged, PRs #587-#594)

Source analysis: https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/USA-E-XChannel-8.html — a richly
multimodal HyperWar page used to stress-test every content type. Each item below is a concrete gap with
its code location + page evidence. Ordered by VALUE × TRACTABILITY (do top-down). The map-as-source work
(spec docs/current/design/MAP_AS_SOURCE_DOCUMENT.md, PRs #583/#584/#585) is the TEMPLATE: a non-text asset
becomes a SOURCE, an event/entity is derived, everything flows through the normal pipeline.

CROSS-CUTTING ROOT CAUSES (fixing these knocks out multiple M-items):
  RC1. parser.extract_images matches only IMAGE markdown (`![alt](url)`), NOT links (`[text](url)`) and
       NOT bare caption text. So linked maps/charts AND caption-only embedded photos are invisible.
  RC2. No image->entity linkage model: captions name people/places but nothing records "image depicts
       PersonID/PlaceID"; ImageCaptioner returns free-text + a coarse class, no structured entity list.
  RC3. In-document asset links are never FOLLOWED as sources. map-as-source added WEB-SEARCH discovery,
       but the author already curated the best maps/charts/MoH citations as direct links in the document.
  RC4. org chart + MoH citation are unmodeled SOURCE TYPES (each a natural map-as-source analog).

## [M1 · HIGH · in-doc-asset-links] ✅ DONE (PR #587) — Follow in-document map LINKS -> map-as-source  (did FIRST)
Evidence: `[Map VII]..[Map XIV]` in the narrative link directly to `maps/USA-E-XChannel-*.jpg` (author-
curated, highest-quality maps on the page). GAP: extract_images matches `![]()` not `[]()`, and nothing
follows a `maps/*.jpg` link from text. FIX: a parse-time extractor that finds `[text](url)` links whose
target is an image ext OR under `maps/`, and routes them into the EXISTING map-as-source adapter
(map_vision -> depicted -> contentrepository/ source). Reuses #585 wholesale; only the discovery front-end
is new (in-doc link vs OpenSERP search). SSRF-safe + same-host (ibiblio) allowlist. Highest value, lowest
ambiguity (the map is explicitly linked + captioned). Addresses RC1+RC3.

## [M2 · HIGH · embedded-image-extraction] ✅ DONE (PR #588) — Capture caption-only / bare embedded images (RC1)
Evidence: "PREINVASION SCENES", "ALLIED INVASION CHIEFS", "PARACHUTE TROOPS", "HEDGEROW COUNTRY", aerial
beach views — rendered as plain-text captions with NO `![]()` in the parsed markdown, so extract_images
never sees them -> the photo is never ingested at all. FIX: (a) in the HTML->md converter (the new
text_converters.html_to_markdown path), PRESERVE `<img>` as `![alt](src)` instead of dropping it; (b) for
HyperWar's figure-caption convention, pair an ALL-CAPS caption line with its image. Unblocks M3/M4/M5
(nothing downstream can classify/link an image that was never extracted). Addresses RC1.

## [M3 · HIGH · image-to-person] ✅ DONE (PR #589) — Named-group + headshot photos -> PersonID linkage (RC2)
Shipped: `caption_names.parse_caption_person_names` (positional-cue + Title+Name parse, conservative) +
`image_person_link` (people name-index full-name + unambiguous-surname -> resolve -> append
`{url,title=caption,source=document:<book>}` to people.images[] via the central guarded writer, dedup by
url; optional ImageCaptioner vision gate, no network; fail-safe). Phase-3 gated step `image_person_link`
(`images.link_people`) reusing the shared `_load_parsed_docs` (also refactored the M1 step onto it). OBSERV
`image_person.*` + `phase3.step`. 10 tests, radon 0 new C, pytest 2843, dual review PASS (code review caught
a test-fixture ULID bug -> fixed, write-path persistence re-verified).
Evidence: "ALLIED INVASION CHIEFS. Left to right: Bradley, Ramsay, Tedder, Eisenhower, Montgomery,
Leigh-Mallory, Smith" (7 identified) + "GERMAN FIELD COMMANDERS" (4). GAP: even if extracted (needs M2),
there is no structure linking an image to the PersonIDs it depicts, and no use of the left-to-right
positional naming. FIX: extend ImageCaptioner/vision to return a STRUCTURED depicted-persons list + wire a
caption-name -> PersonID resolver (reuse people name-index), writing people.images[] (document-sourced) +
an image->person link. Depends on M2. Addresses RC2.

## [M4 · HIGH · footnote-asset-links] ✅ DONE (PR #590) — Follow footnote links: MoH citations + org chart (RC3+RC4)
Shipped (broadened to ALL links per owner): `link_assets.extract_link_assets` (all markdown+HTML-anchor
links, resolve relative/#anchor vs `_source_url`, recognize media type via media_detection ->
html/image/pdf/tif->image/video->moving_image/docx/epub/text/unsupported, dedup, exclude ![images]/mailto)
+ `link_relevancy` (external EDITABLE `link_relevancy_whitelist` [ibiblio.org] host-or-subdomain bypass;
blacklist dropped first; else one-word Grok verdict; fail-safe=ENQUEUE) + `enqueue_doc_links` ->
fetch_provenance TEXTUAL HIT `provider=link-asset` -> review_status PENDING (URL-review UI; human affirm ->
normal ingestion; NO auto-fetch/ingest). Traceability: event carries source_url+source_doc+link_text+
endpoint+url_key. Phase-3 gated step `link_asset_review` (`links.review_external`) reusing shared
`_load_parsed_docs`. OBSERV link_asset.* + phase3.step. config: ibiblio.org moved blacklist->whitelist.
12 tests, radon 0 new C, pytest 2855, dual review (code+security) BOTH PASS. On affirm: chart-image->M6,
pdf->pdf_pipeline, MoH-html->person/source enrichment (follow-on).

Evidence: fn63 -> `../../MoH_P-T.html#roosevelt`, fn74 -> `#pinder` (person-specific award/biography
primary sources); fn23 -> `charts/USA-E-XChannel-4.jpg` ("naval command chart"). GAP: extract_footnotes
captures footnote text/url but nothing FOLLOWS the embedded links; MoH citation is not attached to the
PersonID (people.military_awards[] exists but is OpenSERP-fed, not in-doc-link-fed); org chart is an
unmodeled source type. FIX: (a) extract links inside footnote citation text; (b) MoH link -> fetch (SSRF-
safe) -> attach to PersonID as a biography source + award; (c) org chart -> chart-as-source (see M6).
Addresses RC3+RC4.

## [M5 · MED · image-to-place] ✅ DONE (PR #591) — Topology/aerial photos -> PlaceID linkage (RC2)
Shipped: `image_place_link.classify_terrain_aerial` (caption keyword cues first; Grok-vision subject-matter
fallback when ambiguous, fail-safe) + `parse_caption_place_names` (AERIAL VIEW OF UTAH/OMAHA BEACH ->
[UTAH BEACH, OMAHA BEACH]; TERRAIN ON X) + `_resolve_or_create_place` (existing PlaceID OR routes a
described-but-new place to the canonical `places._find_or_create_place` -> CREATES it) + guarded append to
place.images[] with CRITICAL durable `SourceSectionID` ULID attribution (book->SourceSectionID from
out/source_section/*.json), dedup by url. Phase-3 gated step `image_place_link` (`images.link_places`) reusing
shared `_load_parsed_docs`. SCHEMA EVOLVED: people images[] item + nullable SourceSectionID (2.26->2.27,
ADDITIVE, re-pinned) + NEW src/schemas/history/<entity>/<version>.json archive (all 14 entities) + guard +
SCHEMA_VERSIONING.md Change Log/archive step + fingerprint-guard reminder echo. OBSERV image_place.* +
phase3.step. 10 tests (incl. ULID-attribution proof) + schema-history guard, radon 0 new C, pytest 2866, dual
review (code+security) BOTH PASS.

Evidence (original): "HEDGEROW COUNTRY", "AERIAL VIEW OF UTAH/OMAHA BEACH", "TERRAIN ON OMAHA BEACH".

## [M6 · MED · chart-as-source] ✅ DONE (PR #592) — Org / command-structure charts -> people_groups source (RC4)
Shipped: `chart_source_adapter` (mirrors map_source_adapter) — `_render_command_edges`/`_render_chart_text`
(command hierarchy -> grounded prose), `build_chart_parsed_source` (create_event_prompt contract,
source_type='chart'), `recycle_chart_as_source` (contentrepository/chart-<id>/ meta.yaml+content.md SOURCE
pair -> re-enters Phase 1->2->3 -> people_groups/command edges via the event spine, no chart-specific
logic). `prompts/chart_vision.yaml` (verify+extract command structure, anti-hallucination). `ingest_affirmed_
chart` (affirmed chart link -> vision -> recycle; rejects non-chart). ULID TRACKING TO THE ORIGINAL EVENT:
origin_EventID + origin_SourceSectionID in BOTH `_chart_source` provenance AND meta.yaml (from the link
event's request_params, fallback book->SourceSectionID via M5). OBSERV chart_source.*. raw-write baseline
67->69 (contentrepository SOURCE pair, mirrors map adapter). 9 tests, radon 0 new C, pytest 2875, dual review
(code+security) BOTH PASS. Reuse: map_source_adapter + prompt_loader + grok vision + M5 book->section index.

## [M7 · MED · embedded-low-q-maps] ✅ DONE (PR #593) — Inline figure maps + shared confidence gate
SCOPING (verified against the owner's example maps/USA-E-XChannel-2.jpg, the captured OB WEST map): a
low-quality/captured map is STRUCTURALLY IDENTICAL to a high-quality map and is ALREADY handled end-to-end
by M1 (extract_maps resolves the relative maps/*.jpg link) + map-as-source (vision read + recycle). So M7
was NOT a new low-q-map ingestion path. Shipped the ONE genuine enhancement the spec identified: the
map-wide confidence gate it deferred — a shared legibility gate at map_source_adapter._render_depicted_text
(the single choke point for build_map_parsed_source + render_map_markdown, so it covers OpenSERP maps +
in-doc map links): vision reports legibility_confidence 0.0-1.0 (prompts/map_vision.yaml); below 0.35 the
map is skipped (OBSERV map_source.skipped reason=low_legibility) rather than injecting a low-confidence
event; absent confidence not gated; malformed fails open. Observability on BOTH decisions (built line carries
legibility=X). 7 tests, radon 0 new C, pytest 2882, dual review BOTH PASS.
RESIDUAL (folded into RC1 backlog, not M7): bare "Map 1/2/3" TEXT labels with NO [Map X](url) link and no
image have no ingestable asset — an RC1 extraction concern, nothing to recycle.

## [M8 · LOW · intra-corpus-links] ✅ DONE (PR #594) — Footnote links to OTHER ibiblio docs as INTERNAL corpus links
Shipped: `intra_corpus` — classify_link (in_corpus_doc / same_volume_anchor / external, reusing the
hyperwar_biblio volume recognizer) + process_doc_links (ULID-attributed corpus-graph edges from_SourceSectionID
via the M5 index) + QUEUE a not-yet-in-corpus cited volume as ANOTHER BATCH (owner directive): SSRF-safe
fetch_text_safe -> upload to contentrepository/_system/<vol>/ (the content-uploaded front door -> one Phase-1
launch), TRIPLE-deduped (already-in-corpus + already-queued-this-run + global content-hash). Same-volume
#page anchors = intra-corpus nav (not re-ingested). Phase-3 gated step intra_corpus_links (links.intra_corpus).
OBSERV intra_corpus.* + phase3.step. Bundled an M* OBSERVABILITY REMEDIATION (independent-audit-driven):
fixed 5 silent decision/error paths in M3 image_person_link (no_names/not_people_photo/unresolved/vision_error)
+ M6 chart_source_adapter (_resolve_origin_ulids origin_unresolved) + mypy fixes. 8+2 tests, radon 0 new C,
mypy PASS, pytest 2892, dual review BOTH PASS.

Evidence (original): `Utah Beach to Cherbourg`, `Omaha Beachhead`, `Small Unit Actions`, cross-chapter `#page329`.

SEQUENCING: M1 (self-contained, reuses #585) -> M2 (unblocks the image chain) -> M3/M4 (person linkage +
footnote assets) -> M5/M6/M7 (place/chart/low-q maps) -> M8 (polish). RC1 is the common unlock for
M1/M2/M7; RC3 for M1/M4/M6.

M4 SCOPE (owner-confirmed, 2026-10-27): broaden from "footnote links" to ALL links in the HTML/parsed doc.
Per link: extract -> resolve (SSRF-safe) -> RECOGNIZE media type (reuse media_detection: html/image/pdf/
tif/video(moving_image)/docx/epub/text/unsupported) -> (3b) RELEVANCY GATE: if the domain is on an
EXTERNAL, EDITABLE whitelist (new `link_relevancy_whitelist:` key in config/domain_blacklist.yaml, seeded
with ibiblio.org) skip Grok and trust; else Grok evaluates relevancy (fail-safe default = enqueue when the
call errors) -> ENQUEUE for human review via fetch_provenance TEXTUAL PENDING (new `footnote-asset`/link
provider) carrying recognized type + relevancy verdict + provenance; appears in the URL-review UI; on affirm
flows through the normal ingestion path. NO auto-ingestion of external assets. Dedup by clean_url; honor the
existing exclude-domain/blacklist. Chart-image -> M6; pdf -> pdf_pipeline on affirm.

- **[LOW · investigate] Is `config/domain_blacklist.yaml` (the `blacklist:`) still HONORED by OpenSERP?**
  The blacklist is the human-reviewed license-rejection gate (source_retrieval references it; openserp_maps
  appends to it). Confirm the OpenSERP enrichment path (src/enrichment/openserp_enrichment.py + the OpenSERP
  core under openserp/) actually filters results against `blacklist:` at query/result time — not just the
  maps sub-path — so a license-rejected domain can never re-enter via OpenSERP. If not honored, wire the
  drop (reuse the Task-#7 `_drop_excluded_domains` / url_registry exclusion). Surfaced while adding the M4
  `link_relevancy_whitelist` (and moving the mislocated `ibiblio.org` from blacklist -> whitelist).

- **[CRITICAL · provenance · ALL M-issues] ✅ RESOLVED (PR #595) — ULID attribution for every multimodal linkage (owner-confirmed CRITICAL, 2026-10-27).**
  Every image/asset linked by the multimodal epic carries a durable `SourceSectionID` ULID reference back to
  its originating source_section (the canonical source entity), NOT just the human-readable
  `source: "document:<book>"` string. "Schemas exist to evolve" (owner): the people/place `images[]` item
  schema gained a nullable `SourceSectionID` field (M5, people 2.27).
  DONE across ALL M-issues:
    * M5 (#591) place.images[] — from the start (schema evolved + book->SourceSectionID resolver).
    * M6 (#592) chart-as-source — origin_EventID + origin_SourceSectionID in provenance + meta from the start.
    * M8 (#594) corpus-graph edges carry from_SourceSectionID.
    * M3 (#589 -> BACKFILLED #595) people.images[] now writes SourceSectionID via the same resolver.
    * M4 (#590 -> BACKFILLED #595) link-asset review events carry request_params.origin_SourceSectionID
      (which the M6 chart ingest reads — forward-compat loop closed).
  Reuse: source_section records under `out/source_section/*.json` (`book`+`EventID`+`SourceSectionID`) via the
  shared `_build_book_section_index`. Observable (OBSERV ...source_section=<ULID>).

- **[HIGH · schema-design] Review schema.org for future JSON entity enhancements (owner, 2026-10-27).**
  Survey schema.org vocabularies for ideas to enrich our entity schemas (people/places/events/images/
  source_section/etc.) — e.g. Person, Place, Event, Photograph/ImageObject, CreativeWork, Map, Organization,
  and their properties/relationships (sameAs, spatialCoverage, temporalCoverage, contentLocation, depicts,
  isBasedOn, citation, author, license). Goal: align our fields with an established, interoperable vocabulary
  where it adds value (richer linkage, external-ID cross-walks via `sameAs`, better provenance), WITHOUT
  over-coupling — adopt selectively.
  STATUS (2026-10-27): ✅ PROPOSAL DELIVERED — docs/current/design/SCHEMA_ORG_REVIEW.md (schema-by-schema
  additive suggestions, suggestions-only, no schemas changed). Top recommendation: a first-class nullable
  `sameAs: [url]` cross-walk on people/places/people_groups/bibliography/maps, then typed `identifier`,
  ID-typed hierarchy links (containedInPlace/parentOrganization/subEvent), and the ImageObject licensing +
  contentLocation/dateCreated vocabulary; keep ULID identity, SourceSectionID provenance, source_qualification,
  and fuzzy-date models (stricter than schema.org). REMAINING: owner decision on which suggestions to adopt,
  then implement each via the evolve-+-archive discipline (bump version, re-pin fingerprint, archive to
  src/schemas/history/, Change Log).


### Resolved this session (moved out of the active flat list)
- [DONE · PR #562] bibliography resolver download_path rename + search_status enum
- [DONE · PR #571] down-bind stale Sub_eventID to resolving parent EventID
- [DONE · PR #568] unlink shards only after confirmed canonical write (_persist_merged_bib)
- [DONE · PR #574] per-entity SCHEMA_TARGET via _entity_target_for
- [DONE · PR #576] _unwrap_keyed_supplemental keyed->list unwrap + ULID provenance
- [DONE · PR #577/#578] OPENSERP_URL_LIFECYCLE doc + Lambda URL-review UI/ingestion
- [DONE · PR #581] overwrite re-download repaired entity dirs before validate_phase3
- [DONE · PR #575] snapshot needed pre-loop (_place_needs_work)
- [DONE · PR #575] removed the ISBN-only 0-of-N false anomaly
- [DONE · PR #581] SAME as the durability item — false FAIL from stale local tree; fixed by overwrite re-download

## [LOW·dep-advisory] pylint 4.0.5 -> 4.0.9 (CVE-2026-114608), pip-audit (2026-10-27)
Freshly-published advisory on the CI-pinned pylint (requirements-ci.txt: pylint==4.0.5; fix 4.0.9).
Dev-only tool (not shipped in the pipeline image / lambdas). CI (lint.yml) does NOT run pip-audit so
it's not blocking merges; the LOCAL gate.sh pip-audit trips on it. Fix: bump requirements-ci.txt to
pylint==4.0.9 as a standalone dep-maintenance change (not bundled into a feature PR). Unrelated to the
map-as-source work that surfaced it.
