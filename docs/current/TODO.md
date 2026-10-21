# Pipeline Backlog

**Last Updated:** 2026-10-20 (reorganized: Phase-2 issues prioritized; completed items moved to bottom)

Active work is the **clean XChannel Phase-2 E2E** and the data-quality issues it surfaced. Sections
are ordered: (1) **Phase 2 — open**, (2) Phase 3 / enrichment — open, (3) architecture & design,
(4) infra/security/cost, (5) data-quality remediation, (6) lower-priority & future, (7) **Completed**.

---

## 1. PHASE 2 — OPEN (current priority)

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


