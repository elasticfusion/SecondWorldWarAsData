# Pipeline Backlog

**Last Updated:** 2026-10-20 (reorganized: Phase-2 issues prioritized; completed items moved to bottom)

Active work is the **clean XChannel Phase-2 E2E** and the data-quality issues it surfaced. Sections
are ordered: (1) **Phase 2 — open**, (2) Phase 3 / enrichment — open, (3) architecture & design,
(4) infra/security/cost, (5) data-quality remediation, (6) lower-priority & future, (7) **Completed**.

---

## 1. PHASE 2 — OPEN (current priority)

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

- **[OBSERVABILITY] Sync the Grok api-cache to S3 (or at least optional-batch responses).** Only
  `output/dedup/manifests` sync to S3, so after a run the cached batch responses are gone and
  optional-entity extract-vs-persist can't be replayed offline (forced code-level repro). Syncing
  `cache/api` makes a run diagnosable post-hoc (same spirit as the log-retention item).

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

- **[SEC FOLLOW-UP] DNS-rebind TOCTOU in image/url fetchers (codebase-wide).** `fetch_image_bytes`
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

---

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

## 7. COMPLETED

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
