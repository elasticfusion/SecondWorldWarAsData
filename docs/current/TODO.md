# Pipeline Backlog

**Last Updated:** 2026-10-01 (reprioritized + swept against code)

---

## Current Priority (reprioritized index)
**Reprioritized 2026-10-02** (after this session deployed the security/infra work
live). Ranking reflects the project goal: *finish unattended ETO ingestion, then
build RAG/search*. Current ordered priority:


### [HIGH] Data-quality program — data-scientist review backlog (2026-10-09)
From an independent data-scientist review of `DATA_QUALITY_SAFEGUARDS.md`. Key reframe: the write
guard defends against **structural (schema-shape) corruption at write time**, but the dominant
risks for this corpus + audiences (historians: citations/provenance; genealogists: person/unit/
place/date lookups) are **corpus-level integrity** and **semantic/content** validity — classes the
per-write guard is blind to. "The guard confirms a record is well-formed; nothing confirms it is
informative or correct" (observed: empty-`{}` biographies and 0.0-coord places pass validation).

**HIGH**
1. **Corpus referential-integrity audit** — resolve every cross-ref edge; report dangling counts
   per type. Known damage (DATA_QUALITY_STATUS): Casualties→PeopleGroups ~8,336/9,594 broken
   (~87%); Weather→Places 168/538 broken (a MentionID-vs-PlaceID TYPE-confusion bug — fix that).
   Caveat: the guard's empty-string-ULID *repair* mints a fresh ULID, which ORPHANS a cross-ref if
   the empty field was a reference target — restrict repair to primary keys only, never ref fields.
2. **Remediate the ~35% invalid merge fragments** (§6) via reprocess-from-provenance → re-dedup →
   purge quarantine (fragments hold UNIQUE mentions — never delete).
3. **Semantic validators as FLAGS (not hard blocks):** geocode in-theatre bounding-box sanity (43%
   of places have null/0.0 coords; nothing rejects ocean/wrong-continent); date in the 1944–45 ETO
   window (reuse the existing `resolved_earliest/latest`); citation resolvability
   (archive_reference_number present / URL dereferences / `ibid` resolved).
4. **Teach the statistical catcher to see low-information ALLOWS** — record an `allow_empty`
   outcome (populated-field count below a per-entity floor) so empty-`{}` biographies / 0.0-coord
   places are visible (today they're `allow`, so the catcher can't flag them — its biggest blind spot).

**MED**
5. **Persist validation_stats as a time series** (per run + per book) and alert on TREND/regression,
   not just absolute threshold; make the systematic rate per-(entity × validator-keyword × BOOK)
   with a lower count floor for small entities (People 1,650 / Maps 55 can hide a real cluster
   under the 5%-of-entity rate today).
6. **Fail-open-allow counter** — the in-process guard fails OPEN on validator/registry/import error
   (§1.4); add a counter so silent allows are observable (a systematic import failure would show a
   clean block_rate while admitting corruption).
7. **Persisted corpus_quality_report** (nightly, versioned, dashboard): null-rate per field,
   provenance-coverage %, cross-ref-resolvability % per edge, dedup drift/collision rate,
   geocode-in-theatre %, date-in-window %, enrichment status. Auto-regenerate the stale
   DATA_QUALITY_STATUS numbers from it.
8. **Dedup precision/recall** against a small labelled set (can't tell under- vs over-merging today).
9. **Catcher per-book/per-prompt dimension** to actually distinguish extraction-defect (spread
   across books) vs source-defect (concentrated in one book) — the docstring claims this but the
   code only keys on validator keyword.

**LOW**
10. Cross-source corroboration flags across the 3 overlapping ETO books.
11. Surface a corpus-wide, queryable `confidence` field for historian filtering.
12. Doc fixes: ULID-repair scope warning; concrete audit-invariant definitions (ref-graph edges);
    align the validation_stats docstring to the code.

### [HIGH] Data-quality follow-ups from the write-validation centralization (2026-10-09)
Added after centralizing the write guard across all paths (PR for `fix/write-validation-always-on`).
Two detective/corrective layers remain (the preventive in-process guard is done + merged):

- **(a) Remediate pre-existing invalid S3 records.** Pre-fix, the unguarded dedup/merge path wrote
  schema-invalid fragments to S3 (sampled: ~28/80 across people/people_groups/places/dates — all
  version-LESS, missing primary-key ID + name, i.e. unresolved per-mention fragments). They carry
  UNIQUE cross-ref mentions (MentionID/EventID/Sub_eventID with zero overlap vs valid records), so
  **do NOT delete** (orphans mentions). Remediation: **quarantine** to `output/_quarantine/` →
  **targeted reprocess** from each fragment's provenance (book + EventIDs) to regenerate canonical
  PK'd records + re-run dedup so name-variants merge → purge quarantine only after mentions land in
  canonical records.
- **(b) Detective layer + bypass prevention.** (1) **S3-event Lambda backstop**: on `ObjectCreated`
  under `output/`, run the SAME `_validate_entity` primitive (no reimplementation — avoid guard
  drift); on invalid, alert (phase2-complete SNS → Slack/email) + quarantine. Fail-LOUD.
  **SYNC REQUIREMENT (acceptance criterion):** the Lambda MUST run the repo's live validation code,
  never a copy. `update_lambdas.sh` already bundles `src/` into the Lambda zip, so the primitive is
  shared by construction — but add a DRIFT CHECK to `check_component_versions.sh` (the deploy
  preflight): hash `src/schemas/` + `src/utils/file_lock.py` + `src/utils/validation_stats.py`,
  compare repo-HEAD vs the deployed Lambda bundle, WARN (or `--strict` fail) on mismatch so a
  partial deploy can't leave the validation Lambda behind the pipeline. **Quarantine = tag-in-place
  (`quarantine=true`) + bucket-policy DENY reads to consumer roles, NOT a key move** (moving
  orphans the unique cross-ref mentions the bad fragments hold); lifecycle-expire the quarantine
  tag/prefix. (2) **grep gate** in `scripts/gate.sh` forbidding new raw `json.dump`/`write_text` to
  `output/` entity dirs, so the next contributor's bypass fails CI. (3) **corpus-level audit job**:
  periodic scan running the guard across all of `output/` + a referential-integrity check (every
  cross-ref ID resolves) + ID-uniqueness + dangling-mention + dedup-consistency checks.
- **(b2) Corrective loop (NO human gate — code, not people).** On a bad finding: auto-classify +
  quarantine (tag), then AUTOMATED, dedup-aware REPROCESS from the record's provenance (book +
  EventIDs), with an idempotency/loop-guard (same failure N times → mark `unrecoverable`, stop
  retrying, keep quarantined). The feedback signal is STATISTICAL, not case-by-case: the
  **validation-stats catcher** (DONE — `src/utils/validation_stats.py`, writes
  `output/metrics/validation_stats.json`, emails/Slacks on systematic clusters) is the upstream-bug
  report. Humans fix CODE in response to a systematic-cluster alert; they never adjudicate
  individual records. Isolated failures auto-reprocess silently; only systematic clusters alert.
- **(c) Minor:** make `write_json_with_lock` return a bool (did-write) so `merge._write_entity_guarded`
  consumes it directly instead of the mtime/exists heuristic; consider moving `_validate_entity` to
  `src/schemas/write_guard.py` if the single-writer-facade refactor happens.
0. **~~[CRITICAL] Deploy current `main`~~ ✅ DONE 2026-10-02** — verified live: S3
   AES256 + DenyInsecureTransport; pandoc `--sandbox` image pushed; AV scanning
   deployed + signatures seeded + EICAR-validated (`AV_SCAN_ENABLED=true`); EBS
   encryption (account default + launch-template). No Critical items remain open.
1. **[HIGH] Fix idle-infra teardown + add an hourly backstop reaper (cost leak found
   2026-10-08).** Root cause observed live: `dev-wwii-openserp` ECS **service** was pinned
   `desiredCount=1` and ran 8 days (since 2026-09-30), holding a Fargate task + **1 NAT gateway**
   up idle. `openserp-manager` runs every ~10 min but logs *"Active: 1 running tasks — skipping
   teardown"* — its predicate counts the service's OWN idle task as "active work," so it can
   never scale to 0, and `nat-manager` can't reap NAT while OpenSERP is up. Two-part fix:
   (a) **Root cause** — openserp-manager must scale OpenSERP→0 when no *pipeline/phase* task has
   needed it for N minutes, not refuse because its own service task exists; (b) **Backstop
   watchdog** — a **cron'd Lambda running HOURLY** that checks pipeline progress each run
   (progress = DynamoDB manifest mtime / S3 output-object count / heartbeat marker — NOT just
   "a task exists") and stores the observation. Shutdown fires only on **TWO CONSECUTIVE
   no-progress hourly observations** (debounced two-strikes ≈ 2h confirmed stall) — one
   transient slow hour does NOT trigger teardown. On the second strike: force-scale idle
   OpenSERP/tasks to zero + alert via the phase2-complete SNS topic, then reset the strike
   counter. Defense-in-depth so a future manager bug can't silently burn money for days. Keys
   off PROGRESS not wall-clock, so a legitimately long batch that keeps advancing is never
   killed.
   IMMEDIATE: manually scale `dev-wwii-openserp` to desiredCount=0 to stop the current leak.
2. **[HIGH] Re-OCR the off-by-one corpus** — data-correctness (St. Vith/Boyer +
   ETO OOB markdown are currently shifted). Wrong data feeding extraction; now the
   top real item. Data op (re-run OCR; auto-triggers on re-upload).
2. **[HIGH] Deploy + wire OCR markdown-review UI** — now **UNBLOCKED** (stack
   healthy; deploy path proven this session). Deploy + smoke-test the `/mdreview`
   route; treat NAT as an async human-gate like dedup.
3. **[HIGH] Verify page-separator vs page count after merge** — data-integrity
   guard before markdown feeds parsers; pairs with the re-OCR work.
4. **[HIGH] Cost: budget 6× over** — reset the (stale) $75 limit + CloudWatch $85
   reduction pass. Largely explainable (one-time $135 domain), not a runaway.
5. **[HIGH] Layout-miss task-org tables → review surface** (needs design).
6. **[MED] Slack refinement; misleading batch log; SNS:ListSubscriptions IAM;
   bibliography + Archive.org enrichment UIs; delete stale chunk dirs.**
7. **[LOW/REGRESSION] mypy --strict on new files; the 5 regression tests;
   code-quality refactors.**

### Backlog items added 2026-10-08 (AWS Phase-3 E2E findings)
- **[DONE 2026-10-08] OpenSERP bumped v0.6.0-15 -> v0.8.12 + no-sandbox -> search WORKS.** The
  version bump RESOLVED the anti-bot "Found 0 results": v0.8.12's improved scraping now returns
  real Google results (Found 111/203/1.6M...), and end-to-end OpenSERP enrichment is validated in
  AWS — Grok fail-closed verify correctly accepted "Maj Gen Allen W. Jones, 106th Infantry" +
  rejected Clara Barton/obituary false-positives; "OpenSERP enriched: Alan W. Jones". The bump
  also cleared the stale Go crypto/tls CVEs. Submodule re-pinned to v0.8.12 + a local no-sandbox
  patch; task-def uses `serve --host --port` (no --raw). REMAINING: (a) OpenSERP BASE image
  (chromedp/headless-shell@sha256 pin) has ~51 fixable OS-pkg HIGH/CRITICAL (util-linux etc.) ->
  refresh the base-image digest so Trivy gating passes on deploy; (b) occasional per-engine
  blocks (Yandex) + per-search browser latency can still approach the pipeline's 30s request
  timeout -> consider raising it / proxy. 
- **[SUPERSEDED by the v0.8.12 bump above] OpenSERP Chromium --no-sandbox fix.** Root-caused the empty
  results: (1) task ran `--raw` (raw HTTP engine, blocked by search engines, no image search) —
  removed, registered dev-wwii-openserp:5 (browser mode); (2) browser mode then FATAL-crashed
  (Chromium zygote sandbox) in Fargate — FIXED by adding .Set("no-sandbox") to the rod launcher
  in the openserp submodule (built, Trivy-flagged fixable Go CVEs [stale deps], pushed, redeployed,
  verified: browser now launches + navigates google/bing/yandex, 0 FATAL). REMAINING (needs the
  v0.8.12 version bump + proxy/captcha): engines return "Found 0 results" (anti-bot/consent-page
  blocking the datacenter IP) + the per-search browser time can exceed the pipeline's 30s request
  timeout -> breaker still opens. The submodule now carries a local patch -> fold into the version
  bump. Also: Trivy gating would BLOCK the current openserp image (fixable Go crypto/tls CVEs) ->
  version bump also resolves that.

- **[HIGH] OpenSERP submodule is stale (v0.6.0-15 pinned vs upstream v0.8.12 / 78 commits
  behind) + build script does not verify component versions.** OpenSERP scrapes live search
  engines, so staleness likely contributes to empty results (markup/anti-bot drift). (a) Update
  the `openserp` submodule to the latest release tag (v0.8.12) — CAREFUL: CLI/config changed
  upstream (e.g. --log_level rename), so re-verify the task-def command (`serve --host --port`,
  NO `--raw` — see the browser-mode fix) still matches; rebuild + Trivy-scan + push. (b) ~~DONE 2026-10-08~~: scripts/check_component_versions.sh + deploy_all.sh preflight now CHECK each vendored/submodule component (openserp,
  chandra, paddle, clamav) against its upstream latest release and warn/fail if behind, so
  components don't silently rot.


- **[HIGH] OpenSERP returns empty results in AWS -> circuit breaker opens, 0 enriched.** A scoped
  Phase-3 ECS run (BOOK_NAME=TheArdennesBattleOfTheBulge, --max-items 2) connected to live
  OpenSERP (10.0.21.65:7001) and issued real queries (incl. the new multi-language place queries
  "Welscheid"/"Winterspelt"/... confirmed working), but OpenSERP's /mega/search returned
  empty/failed 5x consecutively -> breaker OPEN -> all searches skipped, 0 enriched. OpenSERP
  connectivity is fine; its SEARCH BACKEND (real Google/Bing/DuckDuckGo scraping) is returning
  nothing — likely rate-limited/blocked/misconfigured in the container, or needs search-engine
  egress config. Diagnose OpenSERP container health + a direct /mega/search probe. (Geocoding +
  the rest of Phase 3 worked: 2 attempted/2 geocoded, no BLOCKED writes.)
- **[MED] Ad-hoc Phase-3 ECS task without BOOK_NAME does a FULL S3 entity-tree download**
  (unbounded, ~13min+ stall before enrichment). Set BOOK_NAME to scope (confirmed: 700 files,
  fast). Document that ad-hoc Phase-3 runs must set BOOK_NAME or use a manifest.

### Backlog item added 2026-10-07
- **[MED] Write guard vs. BREAKING schema migrations (schema is never fully stable).** The
  central write guard (file_lock.write_json_with_lock) validates a record's SHAPE against the
  entity's *current* enforced schema and is version-agnostic. This is correct for writes +
  additive changes (old records still validate). But during a BREAKING migration, an existing
  record written under an older version that is then re-written (merge/update) could be BLOCKED
  by the guard even though the read-side `schema_contract.register_upgrade` path would upgrade
  it. The guard does not consult the upgrade registry. Mitigation today: additive-only is the
  norm (the `WWII_WRITE_VALIDATION=off` escape hatch was REMOVED — validation is now
  unconditional, so a breaking migration cannot be worked around by disabling the guard).
  Future: have the guard defer to register_upgrade (or validate against the record's declared
  `_schema_version` schema when it is older-but-registered) so a breaking migration doesn't
  block in-flight re-writes. Low
  frequency (breaking changes are rare + deliberate) but real.
- **~~[HIGH] Install Chromium in the Phase-2 container image~~ ✅ DONE 2026-10-08** — added
  `ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright` + `RUN python3 -m playwright install --with-deps
  chromium` to the main `Dockerfile` (runtime stage, shared world-readable path). Validated: the
  image builds and the non-root `pipeline` user launches headless Chromium + renders inside the
  container. **Remaining op:** rebuild + push the image on next deploy and confirm it passes the
  Trivy AV/vuln scan (the browser + its OS libs enlarge the image + attack surface).
- **[MED] Promote `source_section` article endnote references → `bibliography`,
  de-duplicated by URL.** The `source_section` entity already captures Wikipedia
  article references inline (`reference_articles[].references[]`, tagged
  `source: "wikipedia-reference"`; Grokipedia refs not yet extracted). Goal: collect
  the footnoted references for later evaluation as a durable, de-duplicated set.
  Design decided 2026-10-07:
  - **Where:** promote each unique reference into a first-class `bibliography` record
    (resolvable via the existing NARA/Archive.org resolver), NOT left duplicated inline.
  - **Dedup key = the reference URL (normalized).** Two sections citing the same
    reference → ONE bibliography record + multiple back-links.
  - **Pointer approach:** `source_section` keeps lightweight reference-ID pointers to
    the deduped bibliography records (not full copies), so there is a single copy of
    each citation and the section still records which it cited.
  - Keep secondary (Wikipedia-cited) provenance clearly distinguished from
    primary-source citations.
  - **Why it matters:** a single operation (e.g. Battle of the Bulge) spans ~15 book
    chapters → 15 `source_section` records each fetch the same article → ~2,000
    duplicated reference entries today. URL-keyed bibliography records collapse that.
  - **Related follow-on (separate):** the same cross-section redundancy affects article
    TEXT and MEDIA (same Bulge article/images stored 15×). Root cause is that
    `operation` is an inline label repeated per section. Consider a canonical
    `operation`/campaign entity (keyed by normalized name/`wikipedia_title`) that holds
    the article + references + media ONCE, with sections referencing `OperationID`.
    Not scoped yet — flagged for when operation dedup becomes worth it (measure the
    actual section→operation fan-out first).
8. **[FUTURE] Postgres adapter, DynamoDB-removal, multi-job concurrency, Step
   Functions, new entity types — gated on the post-ingestion RAG/Aurora phase.**

Not-code (deferred, no action): SHAEF OB map gaps (NARA acquisition).

---

## Critical (actively losing data or breaking pipeline)

_None open._

#### ~~Deploy current `main` to AWS~~ ✅ DONE + verified live 2026-10-02
Deployed this session (verified against live AWS): S3 `AES256` +
`DenyInsecureTransport`; pandoc `--sandbox` image pushed (pulled on next convert
task); AV scanning deployed (`AvScanTaskDef`/freshclam/schedule, signatures
seeded, EICAR→infected validated, trigger `AV_SCAN_ENABLED=true`); EBS encryption
(account default `true` + launch-template `Encrypted: true`). deploy_aws.py +
deploy_all.sh now manage video/clamav images (PRs #199/#202). Remaining deploy
sliver is the OCR-review UI — tracked as its own High item (unblocked now).

## Recently Completed (2026-09-23) — OCR reliability + table recovery

_All deployed and validated end-to-end on the St. Vith / Boyer book (252-page
scanned PDF)._

#### ~~Chandra OCR killed by 1h wall-clock timeout on healthy dense chunks~~ ✅ Fixed + deployed
Progress-watchdog (`scripts/ocr_watchdog.py`) is now the primary failure
detector — fails on no-page-progress for `OCR_NO_PROGRESS_SECS` (900s), not
elapsed time. `AttemptDurationSeconds` raised 3600→14400 as a loose backstop.
Wired via `chandra_entrypoint.sh` + `Dockerfile.chandra`; 7 tests. Chandra image
rebuilt+pushed, CFN job def at rev 13 with the new env/timeout.
*Source: St. Vith end-to-end test 2026-09-23*

#### ~~submit_ocr_job.py chunk-000 collision overwrites prior OCR output~~ ✅ Fixed
Page-range/manifest re-runs all wrote to `chunk-000/` (submission-local index),
silently clobbering earlier chunks. Output dir now derived from the page range
(`chunk-p0001-0050`, zero-padded/sortable) in all submit paths + publish.
*Source: St. Vith end-to-end test 2026-09-23*

#### ~~Auto-route flattened 2-D tables to PP-StructureV3 after OCR~~ ✅ Fixed + validated
`src/ingestion/chunk_pages.py` maps `--paginate_output` chunk markdown to
physical PDF pages and flags pages with a flattened task-org table;
`submit_ocr_job.py:auto_route_table_recovery()` fires after OCR (best-effort,
`--no-recover-tables` opt-out), submitting one Paddle job per flattened page.
Validated: OCR → detect pp.154/155/156 → recovery submitted with no manual
steps; p155 produced a reconstructed `<table>`.
*Source: St. Vith end-to-end test 2026-09-23*

#### ~~PP-StructureV3 recovery recovered 0 tables (libgomp1 missing)~~ ✅ Fixed + deployed
Paddle worker crashed at import (`libgomp.so.1: cannot open shared object
file`) → CPU fallback → also crashed. `Dockerfile.paddle` now installs
`libgomp1`. Image rebuilt+pushed; re-run confirmed `device=gpu` and a recovered
table.
*Source: St. Vith end-to-end test 2026-09-23*

#### ~~NAT torn down while async recovery jobs still pending~~ ✅ Fixed
`auto_route_table_recovery` submits Paddle jobs asynchronously; `--wait` used to
tear NAT down as soon as the Chandra job finished, stranding recovery jobs
(which need NAT for first-run model download). `_wait_and_publish` now waits for
the recovery jobs to drain before releasing networking (Option B —
"networking stays up while work pending").
*Source: St. Vith end-to-end test 2026-09-23*

#### ~~Chandra image full rebuild fails on torch CUDA-dep hash drift~~ ✅ Fixed
`docker build --no-cache -f Dockerfile.chandra` failed at the `torch==2.5.1+cu121`
install: pip resolved torch 2.5.1's declared CUDA deps (`nvidia-cudnn-cu12`,
`nvidia-cusparse-cu12`, …) whose upstream hashes on the pytorch CDN no longer
matched torch's recorded metadata (a different nvidia-* pkg failed each run).
Fix: install torch/torchvision `--no-deps` (skip that resolution), then install
the exact CUDA runtime deps torch needs (incl. `nvidia-cudnn-cu12==9.1.0.70`)
from PyPI, which has no stale hash-file enforcement; a build-time `import torch`
asserts the result loads. Verified in isolation. The `Dockerfile.chandra.overlay`
is no longer needed for a full rebuild (kept as a fast code-only-change helper).
*Source: St. Vith end-to-end test 2026-09-23*

---

## High Priority (produces wrong results or wastes significant resources)

#### Wire the reusable SourceRechecker into PEOPLE enrichment
`src/extraction/source_recheck.py::SourceRechecker` (reusable, entity-agnostic) is built
and already used by people_groups (`group_source_recheck.py`) to recover missing critical
fields (nationality, CC parent division) from retained `event_mentions[].original_text`
BEFORE external enrichment — source-first, gap-fill-only, provenance-stamped, fail-safe.
**Apply the same pattern to People**: when a person is missing a critical field
(esp. `nationality` — needed for award-sourcing gate + Wikipedia disambiguation), recheck
the retained source text first. Define a `FieldRecheckSpec` for people and call it in
`enrich_person_biography` before the Wikipedia/award steps. (Owner-requested 2026-10-05.)
The same should extend to other `docs/current/features` entity types (places, equipment)
with missing required data.

#### Enrichment-review findings carried forward (from archived PHASE3_REVIEW + SUPPLEMENTARY_SEARCH_REVIEW, 2026-10-02)
These were captured in the two 2026-10-02 read-only review docs (now in
`docs/archive/`). The top items (geocoding C1 PR#217, failure-visibility C2/C3 PR#221,
M3/H3 PR#222, award sourcing) were fixed; the following remain **open in code** and are
tracked here by name so they are not lost:
- **Grokipedia stores raw search-results HTML as the "biographical source"**
  (`enrich_biographies.py:~175` `cache_result("grokipedia", name, response.text)`) —
  low-signal input; fetch + cache the actual `/page/<slug>` content instead. (SUPP #1/#4)
- **Verifiers fail OPEN** — `_verify_result` (`openserp_enrichment.py:~166`) and the
  bibliography_resolver verifiers admit a result on a Grok/HTTP blip; make them fail
  CLOSED or tag `verified=false`. (SUPP top-5 #1)
- **phase3_handler Lambda is a dormant double-enrich path** (batch vs non-batch
  divergence). (PHASE3 H1/H5)
- **OpenSERP stamps `openserp_searched` even on an empty/failed search** — re-runs then
  skip legitimately-unsearched people. (PHASE3 H2)
- **Unconditional `time.sleep(1)` even on cache hits** in the OpenSERP path. (PHASE3 H4)
- HallOfValor **name matching / search recall** (last+initial over-matches AND surname
  particles / Jr-Sr suffixes under-match) — dedicated hardening pass with a real-person
  precision/recall test set. (person-capture review C1/H1/H2)
- Theater/nationality/Western-name assumptions pervasive in supplementary search prompts.


#### Cost: AWS spend ~6× the $75 budget — reset limit + CloudWatch reduction pass
Found 2026-10-01: `dev-wwii-pipeline-monthly` budget **ACTUAL $471 vs LIMIT $75**
(MTD). Decomposed via Cost Explorer by service (Sept) — mostly explainable, NOT a
runaway, and NOT Grok (xAI bills separately):
- **Amazon Registrar $135** — ONE-TIME domain registration (not recurring).
- **CloudWatch $85** — genuinely high for this workload; the real inefficiency to
  chase (log retention/ingestion + ECS container-insights). **Actionable.**
- **S3 $78** — the ~500 GB corpus; expected + growing (already > the whole $75).
- **VPC $54 / EC2-compute $44 / EC2-other $29 / ECR $16** — NAT during runs + GPU
  OCR + Fargate + image storage; consumption-driven, no idle leak (verified: NAT 0,
  GPU 0, no ALB at rest).
Two actions: (1) **reset the budget limit** to reflect the real corpus+GPU
footprint (the $75 predates the 500 GB corpus — S3 alone exceeds it); (2) a
**CloudWatch cost-reduction pass** (log-group retention policies, trim ingestion,
reconsider container-insights). High (resource waste), not Critical (no runaway;
no correctness impact; biggest line is one-time). Use AWS Cost Explorer/Budgets
for any forward projection.
*Source: critical survey 2026-10-01 (budget alarm $471 vs $75)*

#### Re-OCR the off-by-one corpus (data-correctness) — PROMOTED from Medium 2026-10-01
The OCR page-range off-by-one is **fixed in code** (`_to_chandra_range`), but all
OCR output produced before the fix is shifted by one page and must be **re-OCR'd**
to be correct: notably `stvith_boyer_full` (St. Vith/Boyer) and any ETO OOB
Chandra markdown generated via the old path. This is wrong *data* feeding the
parsers/extraction, so it ranks above cleanup/observability. No image rebuild
needed — it's a client-side `submit_ocr_job` re-run (now auto-triggered on
re-upload). Scope + cost per corpus TBD. Pairs with the page-separator check
(below) and the stale-chunk-dir cleanup (Medium) after re-OCR is verified.
*Source: M1019 off-by-one 2026-09-24; promoted to High 2026-10-01*

#### Verify page-separator count vs. page count after OCR merge — PROMOTED from Medium 2026-10-01
`stvith_boyer_full` merged with **246 separators for 252 pages** (6 short).
Likely blank/near-blank scans (Chandra emits no separator), but a missing
separator **shifts per-page mapping** — a data-integrity risk before the markdown
feeds the OOB parsers / extraction. Add a lightweight post-merge check reporting
`separators vs. pages` + listing pages with no separator for a quick blank-page
eyeball. Belongs with the re-OCR work (verify the re-OCR'd output here).
*Source: stvith_boyer_full re-OCR 2026-09-26; promoted to High 2026-10-01*

#### ~~OCR page-range off-by-one dropped the first page of every range~~ ✅ Fixed (code); re-OCR needed
`submit_ocr_job.py` emitted **1-based** `--page-range` values, but Chandra's
`--page-range` is **0-based**, matched against 0-based PDF page indices
(`chandra.input.parse_range_str("1-2")` → `[1,2]`). So every OCR job **dropped
the first page of its range and read one page past the end**. Auto-chunk runs
(e.g. St. Vith `1-50`, `51-100`, …) were each shifted by one page — physical
page 1 of every chunk missing, chunk boundaries off by one; a single-page/small
range dropped its first page entirely (found via the M1019 2-page test: only
page 2 OCR'd). Fix: `_to_chandra_range` converts physical N → index N-1 at the
Chandra arg only (chunk-dir naming, logs, manifests, merge mapping stay 1-based
physical). Verified against Chandra's own parser; 6 tests.
**Re-OCR consequence (open):** all OCR output produced before this fix is
off-by-one and should be **re-OCR'd** to be correct — notably the St. Vith /
Boyer corpus and any ETO OOB Chandra markdown generated via this path. Scope +
cost TBD; the fix is deployed-pending (ships in the Chandra path with the next
`submit_ocr_job` run — no image rebuild needed, it's a client-side arg change).
*Source: M1019 language-path investigation 2026-09-24*

#### ~~NAT torn down between compute phases (persistent race)~~ ✅ Done + verified (job-aware NAT leases)
Fixed by the **job-aware NAT lease** system (`src/utils/nat_lease.py`,
CONCURRENCY_AND_NAT_SPEC §4), which replaces the timing-heuristic teardown
(task-count + lock-scan + age windows that raced a PROVISIONING task) with an
explicit cluster-wide **demand** signal. Verified in code + tests 2026-10-01:
- Each network-needing task `acquire_lease` → `heartbeat_lease` → `release_lease`
  (wired in `ecs_entrypoint.py` 2366/2380/2394). A lease exists the moment Phase 2
  starts, so Phase 1's teardown sees demand **before** Phase 2 holds its lock —
  closing the exact "lock not held yet" gap this item described.
- **Every** teardown path consults demand: `nat_manager._nat_demand_present`
  guards both the SNS-completion path and `_delete_all` (direct deletes too),
  cross-checking live leases + running pipeline tasks + in-flight OCR Batch jobs +
  pending queues (ground truth). `ecs_entrypoint._nat_demand_present` likewise via
  `has_nat_demand`.
- Fail-safe: every uncertainty path returns "demand present" (keep NAT up); TTL
  expiry prevents a crashed task leaking demand forever.
- Tests: `test_nat_lease.py`, `test_nat_lease_entrypoint.py`,
  `test_nat_manager_demand.py` (asserts the SNS path keeps NAT when demand),
  `test_nat_readiness.py` — 39 passing.
The intended lifecycle (NAT up across compute, down at the dedup human gate,
final teardown at completion) is realized: a human gate releases its lease →
demand drops → NAT tears down; resume re-creates it. Related open piece: wiring
the **OCR markdown-review UI** as an async gate reuses this same lease/demand
mechanism.
*Source: St. Vith run 2026-09-27; fixed via nat_lease (M3) + verified 2026-10-01*

#### Deploy + wire the OCR markdown-review UI
The markdown-review Lambda + UI is **built and tested but not deployed**
(commit d1c9869): `lambda_handlers/mdreview_ui_handler.py` (two-pane page image
+ editable snippet, saves to `ocr-output/{book}/reviewed/pN.md`), merge-time
substitution + consume-once in `submit_ocr_job.merge_outputs`, and CFN
(`MdReviewUIFunction` + `/mdreview` routes on the existing DedupApi). Remaining:
1. **Deploy** — rebuild the Lambda code bundle + main-stack CFN update
   (`deploy_all.sh` main path), then smoke-test the `/mdreview` route (basic
   auth via the shared dedup authorizer).
   **Deploy-path health (investigated 2026-09-27 — must clear before deploying):**
   the parent stack `wwii-pipeline-dev` is in **UPDATE_ROLLBACK_COMPLETE** (last
   attempt 2026-07-01: ComputeStack "Validation failed with 8 errors"). History
   of recurring ComputeStack update failures. Known causes seen in events:
   (a) `PipelineDashboard` name collision with EventsStack — **appears fixed**
   (compute.yaml now uses `-wwii-pipeline-logs` vs events' `-wwii-pipeline`),
   verify no other duplicate logical/physical names; (b) TaskDef
   "Container.image should not be null or empty" — deploy MUST pass
   `--pipeline-image`/`--openserp-image` (use the `deploy_all.sh` path, not a
   bare `deploy_aws.py`); (c) enumerate the July "8 validation errors" via
   `describe-stack-events` on ComputeStack before retrying. Do a **change-set /
   dry-run first**, and NOT during a live Phase 2/3 run (ComputeStack holds the
   phase task defs + the API).
2. **Direct UI↔pipeline wiring (deferred by design)** — today the UI and merge
   are decoupled via the `reviewed/` prefix. Decide whether a reviewer save
   should trigger anything (e.g. re-merge/re-publish) or stay pull-based at the
   next OCR/merge run.
3. Confirm reviewers can discover the URL + credentials (same pattern as dedup).
4. **NAT management — treat as an async human-review gate (like dedup).** The
   OCR markdown review is an **indefinite human pause**, exactly like the dedup
   gate. So NAT must follow the same lifecycle: **tear down NAT while waiting on
   the reviewer** (no compute is running; don't pay for idle NAT), and **bring
   it back up when review completes** and downstream work (re-merge/re-publish,
   or continued OCR) resumes. Mirror the dedup gate's teardown/resume mechanism
   (the delayed-teardown at the gate + recreate-on-resume) rather than holding
   NAT up. This ties into the "NAT torn down between compute phases" fix: the
   rule is NAT UP across compute, DOWN at async human gates (dedup **and** OCR
   markdown review), final teardown at completion.
*Source: human-review compromise for layout-miss task-org tables, 2026-09-24;
NAT-as-async-gate requirement added 2026-09-27*

#### Layout-miss task-org tables (e.g. p156) → human review (DECIDED 2026-09-24)
**Decision:** sparse/borderless task-org pages that PP-StructureV3's layout
detector classifies as `text` (no `table`-class box — verified on p156 via
`layout_boxes`: text@0.49/0.43 + paragraph_title@0.32, zero table boxes) are
**resolved by human review, not further recovery-engine work**. Ruled out:
`layout_threshold` (nothing to admit) and orientation tuning. This matches the
pipeline's existing "flag, don't fabricate" compromise. The page's structure is
preserved in the recovery JSON `flattened_hints` (group→units, `needs_review`);
`flattened_hints` carry content+grouping, NOT a reconstructed 2-D grid — a
reviewer supplies the column crosstab. See CHANDRA_OCR_DESIGN.md "Recovery
limits & the human-review compromise". Dense tables (p155, OOB corpus generally)
still auto-recover to `<table>`.

**Open follow-up (path being decided by owner):** wire flagged pages'
`flattened_hints` into the existing review surface (with page image + parsed
groups) so a human actually sees and completes them — the gap between "flagged"
and "reviewed". Not yet designed. Escape hatches if a specific page is
high-value: (A) image preprocess to make the grid table-like; (B) crop to the
columnar region and run the table-structure model directly (bypass layout
detection). Also: residual recovered-cell OCR noise (`1703dd -arch South`).
*Source: St. Vith end-to-end test 2026-09-23/24*

#### ~~ULID fix generates different replacements for same invalid ID~~ ✅ Already fixed + now regression-tested
Same invalid ULID referenced in multiple places within one response gets different replacements, breaking internal referential integrity. Fix: build replacement map and reuse same new ULID for repeated occurrences.
**Resolution (2026-09-27):** verified already fixed — `src/utils/json_validator.py`
`_fix_invalid_ulids` builds a `replacement_map` keyed by the invalid value and
`_fix_ulids_recursive` reuses it, so repeated occurrences collapse to one ULID
(the dedup path `src/dedup/merge.py:_replace_id_in_obj` is likewise deterministic).
Behavioral check confirmed same-invalid→one replacement across nested
fields/lists; different-invalid→own id; input not mutated. Added regression test
`tests/test_ulid_replacement_consistency.py` (2 tests) to lock it in.
*Source: CODE_INTEGRITY_REVIEW.md #5*

#### SHAEF OB map corpus gaps → source acquisition (likely a NARA visit)
The `SHAEF OB Maps` daily situation-map set has coverage gaps that limit
date-based queries (e.g. "unit X on date Y → show the map"). These are
**acquisition** issues, not code — deferred; likely require obtaining the
missing sheets from NARA.
- **December 1944 entirely missing** — no `44-12` folder. The Ardennes/Bulge
  month is absent, so any query in Dec 1944 (e.g. "3rd Armored on 15 Dec 1944")
  is unanswerable from this set. Coverage is Oct–Nov 1944 + Jan–Apr 1945.
- **Missing 15 Feb 1945** — the `45-02` folder has 29 files (a single-day gap).
- **`311144 SHAEF OB.jpg` mislabeled** — filename encodes 31 November (does not
  exist); needs disambiguation (likely 30 Nov or 1 Dec) when the corpus is
  revisited.
- Interim behavior: the ingestion should return "no map for that date" rather
  than a wrong neighboring map. See
  `docs/current/dataquality/MAP_IMAGE_AV_INGESTION.md`.
*Source: B405 / SHAEF map assessment 2026-09-26*

#### ~~Wire Phase 0 as an auto-triggered phase + type-routing (close the ingest→extract seam)~~ ✅ Done + verified
Adding a **raw** document now runs source-to-finish unattended; all three manual
seams are closed (verified in code 2026-10-01):
1. **OCR auto-triggers on upload** — `trigger_handler._submit_ocr` (from the
   `_split_by_media` → `ocr_keys` loop) submits the Chandra OCR Batch job
   automatically on a raw PDF/image upload (per-book claim dedup, page-range
   chunking, media-mismatch reject). No hand-run `submit_ocr_job.py`.
2. **Phase 0 → Phase 1 handoff is chained** — `ocr_merge_handler` (EventBridge on
   Batch SUCCEEDED) merges OCR output and promotes it to
   `contentrepository/{book}/chapter1/chapter1-content.md`, whose ObjectCreated
   event triggers parse. CONVERT (`phase0_convert.py`) and VIDEO
   (`phase0_video.py`) write the same chapter contract directly. All four tracks
   converge → Phase 1 → 2 → dedup gate → 3.
3. **Document-type routing at ingest** — `_split_by_media` routes by extension
   (OCR / CONVERT / VIDEO / PARSE); `_is_structured_reference` tags OOB docs with
   a `.structured` marker and the merge handler's `_is_structured`/`_route_to_oob`
   sends structured-reference OCR to the deterministic `phase0_ingest` parser
   (`Phase0IngestTaskDef`), NOT narrative extraction (`test_oob_routing.py`
   asserts it does not write the narrative chapter). An AV gate + reject-net +
   translation now also sit on this path.
The dedup review gate + OCR markdown-review UI remain **intentional** human gates.
Still open separately: the maps/images/film **vision branch** (design only, see
`MAP_IMAGE_AV_INGESTION.md`) and the "reprocess already-imported data after Phase
0 routing lands" backfill (Future/Research).
*Source: wiring review 2026-09-26; closed/verified via the phase0-autotrigger-routing branch (PRs #186-#188) 2026-10-01*

---

## Medium Priority (efficiency, observability, developer experience)

### Pipeline Efficiency

#### Slack notifications — formatter built, needs refinement
**Mechanism DONE (verified 2026-10-01):** `lambda_handlers/slack_formatter.py`
subscribes to the phase2-complete topic and wraps free-form text in Chatbot's
custom-notification schema (emoji triage, phase-aware "View logs" deep link,
financial amount/Cost-Explorer links), republishing to the Slack topic
(`cloudformation/events.yaml` `SlackFormatterSubscription`). This closed the
original "free-form text silently dropped by Chatbot" gap.
**OPEN — refinement (per owner 2026-10-01):** the formatting/UX needs another
pass. Specifics TBD — candidate areas: clearer success-vs-failure-vs-review
distinction (a dedup-gate `needs-review` block currently reads like a failure —
see the "pipeline failure email" false-alarm), richer actionable links/structure,
noise reduction, and live-render verification against a deployed stack. Scope the
exact refinements with the owner before implementing.
*Source: St. Vith 2026-09-27; mechanism verified + refinement flagged 2026-10-01*

#### ECS task role missing `SNS:ListSubscriptionsByTopic` (notification preflight always warns)
The new `_preflight_notification_subscriptions` (non-blocking) hits
`AuthorizationError` — `dev-wwii-ecs-task-role` lacks `SNS:ListSubscriptionsByTopic`
on the notification topic, so the preflight can't actually verify subscriptions
and always logs its warning (then correctly continues). Add the read permission
to the task role (`cloudformation/iam.yaml`) so the preflight does its job.
Non-blocking; the run proceeds regardless.
*Source: St. Vith verification run 2026-09-27 log*

#### Misleading "0 processed, N failed" log in batch mode
In batch mode, the synchronous core-extraction step does **not** process inline —
it *collects* requests for the xAI Batch API. But it still logs
`Core extraction complete: 0 processed, 140 failed` / `Phase 2 complete: 0
processed, 140 failed` (70 events + 70 dates requests deferred to the batch),
which reads like a total failure when the batch actually submitted fine
(`num_requests: 70, num_success: 70`). Reword the batch-mode path to report
"N requests collected for batch" instead of "0 processed / N failed" so operators
(and the zero-count warning) don't false-alarm. Observability, not correctness.
*Source: St. Vith verification run 2026-09-27 — batch_bb0c1ba0 succeeded 70/70 while log said "140 failed"*

#### ~~Confirm batch-poller sees ECS-submitted batch jobs (poller visibility)~~ ✅ Done + verified (keys match, tested)
Resolved: the ECS task writes `batch_job#{batch_id}` (status=pending) via
`src/utils/job_queue.enqueue_job` to `CACHE_TABLE`, and
`lambda_handlers/batch_poller._get_pending_jobs` scans the SAME table on
`begins_with(cache_key, "batch_job#")` with status pending/ready. The
`metrics#{batch_id}` record (`grok_client.py`) the TODO flagged is a SEPARATE
metrics record, not what the poller keys on. `ecs_entrypoint` enforces routing
through submit-only (anti-orphan guard: alerts if the enqueue/read-back is
absent). Tested: `tests/unit/test_batch_poller.py` seeds `batch_job#{id}` and
asserts the poller finds→marks ready→triggers retrieve (19 passing w/
test_job_queue); `tests/test_batch_routing_guard.py` guards the submit-only path.
*Source: St. Vith 2026-09-27; verified 2026-10-01*

#### Delete stale legacy `chunk-NNN` OCR dirs superseded by re-OCR
Re-OCR runs (off-by-one fix) write new page-range chunk dirs
(`chunk-p0001-0050`, …) but leave the **old pre-fix `chunk-000`..`chunk-NNN`**
dirs in place. The merge correctly **skips** them ("legacy chunk dir without
page range — cannot map to physical pages"), so output is not polluted, but the
stale dirs are confusing clutter and could trip tooling that globs `chunk-*`.
Delete the superseded legacy dirs from
`s3://dev-wwii-data-pipeline/ocr-output/{source}/` after a re-OCR is verified.
Affected so far: `stvith_boyer_full` (chunk-000..005), and `ETO_Order_of_Battle`
(chunk-000..019) once it is re-OCR'd. Mildly destructive (S3 rm) — verify the
new page-range chunks + merged output first.
*Source: stvith_boyer_full re-OCR 2026-09-26*

#### ~~Batch ALL entity types, not just events~~ ✅ Done + verified
All core extractors route Grok calls through `grok_client.extract_json` →
`chat_completion`, which in batch_mode collects into the batch collector and
raises `BatchModeCollecting` — so dates/places/people/groups
(`src/extraction/batch_parallel._batch_extract`) AND events
(`src/extraction/events.extract_events`) all batch, and `phase2_extract.main`
runs a second batch cycle for the optional extractors
(weather/equipment/logistics/casualties/supplemental). Empirically confirmed
2026-10-01: invoking `extract_dates_batch_async` + `extract_people_batch_async`
with `batch_mode=True` made NO live call and collected 2 requests
(cache_types ['dates','people']). The TODO premise (only events batch) is stale.
*Source: Ardennes 2026-06-13; verified 2026-10-01*

#### ~~Bibliography resolver processes duplicate citations redundantly~~ ✅ Fixed 2026-09-27
Same citation referenced by multiple sub-events is processed N times (NARA identify + search). Cache prevents duplicate API calls but generates log noise (4x identical log lines). Fix: deduplicate by citation text before resolution loop.
**Done:** `resolve_bibliography_dir` now dedups by citation key (verbatim, else
author|title); identical citations resolve once and reuse the result (`deduped`
stat). Tested in `tests/test_bibliography_resolver_guard.py`.
*Source: Phase 3 log observation 2026-06-16*

#### ~~Narrative content misclassified as document_reference reaches NARA resolver~~ ✅ Fixed 2026-09-27 (guard)
Footnotes with factual narrative (e.g., "In October 1941, the Germans had discussed...") are being sent to NARA identification. Two fixes needed: (1) improve supplemental.yaml classification prompt to better distinguish narrative from citations, (2) add guard in bibliography_resolver to skip text that doesn't match citation patterns (no author/title/date structure).
**Done (guard, #2):** `_looks_like_citation` gates the resolvers — prose with no
bibliographic structure is marked `not_citation` and never sent to NARA
(preserves legitimate NARA lookups: any RG/archive-ref/structured entry still
resolves). **Still open (#1):** tighten `supplemental.yaml` classification prompt
to reduce misclassification upstream (defense in depth).
*Source: Phase 3 log observation 2026-06-16*

#### Bibliography human-disposition UI (review_queue.json)
`resolve_bibliography_dir` now writes `review_queue.json` — real citations that
could not be grabbed online (per the "grab what's legitimately online, human-
disposition the rest" intent). There is **no UI** to work that queue yet. Build a
disposition UI analogous to the dedup UI + OCR markdown-review UI: show the
structured citation + verbatim, let a human mark disposition (request-from-
archive, manual link, mark-not-a-source, etc.). **NAT: treat as an async human-
review gate like dedup + mdreview** (tear down while waiting, resume on
completion). Deploy alongside the other UIs (same ComputeStack deploy-path
caveats).
**Retrieval mechanism now exists (2026-09-27):** `src/enrichment/source_retrieval.py`
downloads legitimately-online resolved items into a **quarantine** area
(`bibliography/retrieved/pending_review`, deliberately outside content/ so Phase 1
can't auto-ingest), gated on the existing `config/domain_blacklist.yaml` (NOT a
strict allow-list — download unless blacklisted; human review decides import).
Marks entries `retrieved_pending_review` and **never auto-processes** them.
Legitimacy basis: the pipeline downloads to AI-summarize (Grok) + attribute, not
republish. The UI needs to: drive retrieval for queued/resolved items, show the
downloaded content, and let a human accept (→ move into processable corpus) or
reject. Mechanism is staged/tested but intentionally NOT auto-wired to a phase.
*Source: bibliography-resolution intent discussion 2026-09-27*

#### Archive.org metadata + legitimacy enrichment (free, no new key)
The resolver already calls the Archive.org Metadata API (`/metadata/{id}`) but
only extracts PDF url + page count. From the **same call** we can also capture
**publisher, ISBN, date, creator, rights/license** (actionable publication data
for the professional-historian audience even when not downloadable) and check
`access-restricted-item`/rights so only **legitimately** public items are
presented as "grabbed online" — lending-only/restricted ones route to the
human-disposition queue. Also consider OpenLibrary (book metadata) and Crossref
(journal DOIs) as free authoritative metadata sources. Additive to the resolver;
ties into the existing "Amazon metadata enrichment" future item.
*Source: bibliography-resolution intent discussion 2026-09-27*

### Prompts & LLM Integration

#### ~~Prompt versioning (cache invalidation)~~ ✅ Fixed
Cache key hash now includes system_prompt. Any YAML change (prompt or system prompt) auto-invalidates.

---

## Low Priority (code quality, minor improvements)

#### Finish ecs_entrypoint.py → ecs_modules/ extraction
2473 lines. Proposed splits: `notifications.py` (~110 lines), `dedup.py` (~280 lines), `locks.py` (~150 lines). Would reduce entrypoint to ~1900 lines.
*Source: QA radon 2026-06-13*

#### Consolidate find_duplicate_*.py scripts
Strategy pattern or shared base would cut ~40% code.
*Source: QA review 2026-06-13*

#### Refactor phase3_enrich_data.py:main (D(23) complexity)
*Source: QA radon 2026-06-13*

#### Fix `.gitignore` trailing newline (black already clean)
`black --check` passes repo-wide (427 files, verified 2026-10-01) — the "6
scripts" half is DONE. Remaining: `.gitignore` has no terminating newline on its
last line (`tmp/`); add one.
*Source: Code review 2026-06-13; black-half verified done 2026-10-01*

#### Reduce image memory usage in equipment.py
*Source: CODE_REVIEW.md*

#### DynamoEntityStore.query_unenriched does full table scan
Add GSI on `entity_type` + `enrichment_status`.
*Source: CODE_INTEGRITY_REVIEW.md #14*

#### Per-book queue ordering not guaranteed
DynamoDB scan order is undefined. Low impact (sequential processing, just affects which book goes first).
*Source: Review 2026-06-14*

#### Hard-coded step lists in Phase 3 notifications
`_build_phase_section` and `_update_lock_status` duplicate the enrichment step list. Could drift.
*Source: Review 2026-06-14*

#### CloudFormation drift detection
*Source: DEVOPS_RECOMMENDATIONS.md*

#### Evaluate residential proxy for OpenSERP
Tailscale exit node preferred. Free, no third-party trust.
*Source: end-2-end-1 Phase 3 observation*

---

## Regression Protection

#### Smoke test CI job — full pipeline on chapter99
Run Phase 1→2→3 end-to-end with real file I/O on test chapter. Catches multi-phase coordination bugs that unit tests miss (threading, file locks, phase transitions).
*Source: QA regression review 2026-06-15*

#### Thread-safety stress test (50 threads, @pytest.mark.slow)
Current `test_event_mention_race.py` uses 10 threads. Add variant with 50 threads for CI only. Race conditions often only manifest under load.
*Source: QA regression review 2026-06-15*

#### Expand prompt-schema contract tests to all 27 YAMLs
Assert every YAML has valid `prompt_template`, `schema` (parseable JSON), `system_prompt`. Verify all `{placeholders}` in templates have matching function arguments. Currently only events + 5 types covered.
*Source: QA regression review 2026-06-15*

#### Phase coordination integration test
Mock 2 concurrent phases and verify: Phase 3 can't tear down NAT while Phase 2 lock held, per-book queue processes correctly, lock status updates at each step.
*Source: QA regression review 2026-06-15*

#### Snapshot/golden tests for merge output
If `_merge_person`, `merge_generic`, or `update_event_refs` change behavior, output could silently drift. Add golden file assertions: input A + B → expected merged C.
*Source: QA regression review 2026-06-15*

#### Enforce mypy --strict on new files only
Prevent `find_related_groups.py`-style issues from accumulating. Don't enforce on legacy but require new code to pass strict mode.
*Source: QA regression review 2026-06-15*

---

## Future / Research

#### Explore removing DynamoDB as a general data store (esp. Phase 3 bulk transfer)
Investigate whether DynamoDB is still earning its place, and scope removing it as
a *general* data store. Code review (2026-09-30) found it plays three distinct
roles — they must be evaluated separately, not lumped together:

> **Storage review 2026-10-02** (`docs/current/STORAGE_REVIEW.md`) decided the
> role-split: remove the entity *materialization* copy (→ Postgres with the RAG
> phase), **preserve** the concurrency-safe `merge_entity`, **keep** the tiny
> coordination KV (locks/`pending#*`/NAT leases/`batch_job#`) on DynamoDB
> long-term. pgvector single-store AFFIRMED but deferred to the post-ingestion
> RAG phase. Also tracked there: **S3 `output/` JSON consolidation** (45k
> per-entity objects → per-type NDJSON/Parquet) — an efficiency/cost
> optimization, NOT a correctness fix; do when Phase-3 bulk-read time is a felt
> bottleneck OR when the Postgres load is built, whichever first.

1. **Bulk Phase 3 materialization (weak — prime candidate for removal).**
   `s3_sync._materialize_from_dynamo` calls `DynamoEntityStore.list_all` once per
   entity type = **11 sequential full-table `Scan`s** (`FilterExpression`
   `begins_with(cache_key, "entity#<type>#")`), deserializing every entity's
   `data` blob. A filtered `Scan` reads/bills the whole table then discards
   non-matches — the same "crawl everything" anti-pattern as the S3
   `list_objects_v2` + per-object `download_file` loop it's meant to beat, so it
   is unlikely to be a real bulk-transfer win. **This is the "S3 is slow on Phase
   3" path the removal question is really about.**
2. **Small coordination (KV-shaped, legitimately good on DynamoDB).** Manifests
   (`manifest#phase2`, `pending#parsed`) and the Phase 3 lock/status
   (`_update_lock_status`). Tiny; keep unless the whole store goes.
3. **Concurrency-safe entity merge (genuinely earns its keep).**
   `merge_entity` / `store_bibliography` use version-conditional writes so two
   books extracting the same entity (e.g. "Eisenhower") don't clobber each
   other's `event_mentions`. Comments note this replaced flock-guarded S3 JSON
   writes that were per-host, NOT cross-host safe. Removing this without a
   replacement reintroduces that race — see "True multi-job concurrency" and
   `CONCURRENCY_AND_NAT_SPEC.md`.

**Fix options for the Phase 3 transfer bottleneck (cheapest first), to benchmark
before deciding:**
- (a) **Parallelize the S3 transfer** — current `s3_sync` loop is serial
  `download_file`; use concurrent downloads / `aws s3 sync`. Lowest-risk quick
  win, zero architecture change; may erase the pain that motivated
  `_materialize_from_dynamo`.
- (b) **Consolidate into few large objects** — write one Parquet/NDJSON blob per
  entity type so Phase 3 pulls ~11 objects instead of thousands (attacks the
  actual cause: object count; Parquet adds columnar projection).
- (c) **If DynamoDB materialization stays, stop using `Scan`** — add a GSI on
  `entity_type` so `list_all` becomes a `Query`. (Overlaps the existing Low-Prio
  item "DynamoEntityStore.query_unenriched does full table scan".) Weaker than
  (a)/(b).

**Decision inputs to gather (not yet done):** measured Phase 3 timings +
corpus/object counts, DynamoDB-scan vs. parallel-S3 vs. Parquet crossover, and a
plan for preserving role #3's concurrency safety (Postgres row-level
locking/`ON CONFLICT`, or keep a minimal Dynamo lock table) if the general store
is removed. Ties into the Aurora/pgvector direction ("Postgres dialect adapter
for src/loader") — the eventual query store is Postgres, so DynamoDB here is
really ingestion-time coordination, not a query store.
**Status:** exploration only — nothing to implement yet (per owner 2026-09-30).
*Source: DynamoDB value review 2026-09-30*

#### OCR structural fidelity: raise render DPI + evaluate augmenting Chandra
Chandra runs at the 192 render-DPI baseline (`chandra/settings.py:IMAGE_DPI`,
not exposed on the CLI); its own benchmark recommends 300. Measured: 192→300 =
+83% usable detail (3.41→6.24 MP the model actually sees); the
`scale_to_fit(max_size=(3072,2048))` ceiling caps gains above 300. **Do:** (1)
empirically diff 192-vs-300 re-OCR of the hard St. Vith pages (p155 2-D
task-org table, p103 casualty table) via `tmp/dpi_probe.py`; if 300 helps, make
it the corpus-wide default (override the library setting — no CLI flag exists).
(2) For the persistent structural blind spots that DPI won't fix (2-D task-org
flattening, block-quote markup loss), evaluate augmenting Chandra with a
dedicated table engine — **PaddleOCR PP-StructureV3** (Apache-2.0, table
structure + multi-column reading order) is the first candidate; Docling as a
cross-check; ensemble-disagreement → `needs_review`. Full analysis in
`docs/current/dataquality/CHANDRA_OCR_DESIGN.md` (Render DPI / Augmenting
Chandra sections).
*Source: OCR fidelity investigation 2026-09-22*

#### Postgres dialect adapter for src/loader (Aurora target)
The `output/` → relational loader (`src/loader/`) currently runs **SQLite only**,
though its docstring claims "works with any PEP-249 connection ... psycopg
against Aurora." Three SQLite-specific spots block Postgres: `create_schema`
uses `conn.executescript` (not in psycopg), `_insert` uses `INSERT OR REPLACE`
(Postgres needs `ON CONFLICT (pk) DO UPDATE`), and `?` placeholders (psycopg
uses `%s`). `transform.py` is already DB-agnostic, so this is a contained seam,
not a rewrite. Also missing: `schema_pg_extras.sql` (pgvector/PostGIS/HNSW),
referenced in a comment but not on disk. Defer the full adapter until the Aurora
target is provisioned (untestable before then) and the embedding dimension is
settled (`content_chunks VECTOR(?)` depends on the chosen embedder). **Done
(2026-09-22):** `load.py` docstring corrected to "SQLite today; Postgres via a
planned dialect adapter," and a `_require_sqlite` guard now rejects non-sqlite
connections with a clear `NotImplementedError` (tested). Remaining: the adapter
itself + `schema_pg_extras.sql`.
*Source: loader schema-gap review 2026-09-22*

#### Reprocess already-imported data after Phase 0 routing lands
Once the format-agnostic ingestion + media classification work is built (see
`docs/current/dataquality/STRUCTURED_DATA_ROUTING.md`), build a specialized
audit-and-reimport script that:

> **Status (2026-09-18):** the Phase 0 ingestion front-end + OOB scanned-table
> parsers are now built (`src/ingestion/`, see
> `docs/current/dataquality/INGESTION_FRONT_END.md`). This reprocess/backfill
> script remains pending and should run once Phase 0 is wired as a runnable
> phase and the entity-convergence bridge lands.

1. **Audits existing output** — scans already-extracted content and entities for
   sources affected by the pre-Phase-0 gaps: embedded image-maps that were lost
   between `images.py` and `maps.py`, images misclassified by keyword-only
   `_classify_content_type`, images dropped by `alt_text` dedup, and any
   sources that never parsed (raw PDFs sitting in `contentrepository/`).
2. **Reports** what would change (per source: images recovered, maps
   reclassified, entities newly created) before touching anything.
3. **Re-attempts import** through the finished Phase 0 pipeline, feeding results
   into dedup so recovered media/entities merge with existing ones rather than
   duplicating.
Applies across ALL sources (not ibiblio-specific) — the underlying fixes are
made once in the shared parser/media path, and this script backfills everything
imported before the fix.
*Source: Structured-data routing analysis 2026-06-30*

#### Amazon metadata enrichment for confirmed books
For bibliography entries confirmed as published books, search Amazon.com for metadata: book cover image, ISBN, edition info, page count, publisher details. Supplements Archive.org/Gutenberg data with commercial metadata.
*Source: Search debug session 2026-06-17*

#### True multi-job concurrency
Multiple books in parallel. Requires per-book locking, shared DynamoDB entity store, dedup coordination.
**Spec written 2026-09-27:** `docs/current/dataquality/CONCURRENCY_AND_NAT_SPEC.md` — per-book locks, reference-counted NAT, limit-aware dispatcher pool (Grok rate + Fargate vCPU as binding limits), global dedup barrier, shared-entity-store hardening. Motivated by the WWIIArchives backlog (628 files / ~502 GB).

#### Grok function calling for Phase 3 enrichment
Blocked on: function calling support in batch API.

#### Step Functions pipeline orchestration
Replace SNS→SQS→Lambda→ECS with Step Functions.

#### New entity types: Economic Data, Policy/Legislation
Prompts and schemas drafted in `dataquality/new_entity_types.md`.

#### UK National Archives (Discovery API)

#### Model routing expansion (grok-3-mini for simple extractors)
Dates, places, weather, casualties, logistics could use cheaper model. Needs A/B testing with quality metrics before rolling out.
*Source: Prompt deep dive 2026-06-14*

#### Prompt version in cache key
Enables safe prompt iteration without manual cache invalidation.
*Source: Prompt deep dive 2026-06-14*

#### Cost quantification for model_map expansion
Token counts and per-run cost estimates needed for ROI analysis.
*Source: Kiro evaluation 2026-06-14*

#### Retry escalation (cheap model → expensive model on validation failure)
Undefined policy for when grok-3-mini fails validation. Retry same or escalate?
*Source: Kiro evaluation 2026-06-14*

---

## Completed (2026-06-15)

#### ~~BatchCollector thread safety~~ ✅ — `threading.Lock` added to `.add()`
#### ~~DynamoDB dual-write reconciliation~~ ✅ — wired into `_materialize_from_dynamo`
#### ~~Supplemental_Materials key normalization (all paths)~~ ✅ — 5 entry points normalized
#### ~~Bibliography resource_urls array~~ ✅ — prompt + sanitizer + code aligned
#### ~~Event mention dedup~~ ✅ — checks (EventID + book + Sub_event_Name)
#### ~~merge_generic cross-ref updates~~ ✅ — `update_event_refs` + targeted JSON replacement
#### ~~Optional extractor idempotency~~ ✅ — `.processed_events.json` markers
#### ~~Phase results zero-count warning~~ ✅ — logs + SNS notification
#### ~~BatchModeCollecting explicit catch~~ ✅ — DEBUG in all 4 optional extractors
#### ~~Truncation recovery~~ ✅ — `GrokTruncationError` + `extract_with_chunk_halving`
#### ~~update_event_refs corruption~~ ✅ — `_replace_id_in_obj` (recursive, field-targeted)
#### ~~Phase 1 chunk overlap~~ ✅ — 3-paragraph overlap between splits
#### ~~System prompt centralization~~ ✅ — all use `get_system_prompt(name)` from YAML
#### ~~All prompts externalized~~ ✅ — 27 YAML files, hard fail on missing
#### ~~EntityCreatedTopic~~ ✅ — removed (redundant, dead handler cleared)
#### ~~Enrichment started notification~~ ✅ — SNS after downloads, before API calls
#### ~~Stalled batch notification~~ ✅ — after 4h, hourly, with progress n/n
#### ~~Secrets scanning in deploy~~ ✅ — gitleaks/detect-secrets, blocking
#### ~~Scripts portability~~ ✅ — ENV_NAME/AWS_DEFAULT_REGION
#### ~~Orchestration tests~~ ✅ — 14 tests (locks, sync, threads, loaders)
#### ~~Prompt schema alignment expanded~~ ✅ — 29 tests, output schema validation
#### ~~BackgroundSync skips enriched files~~ ✅ — `_downloaded_keys` tracks mtime, re-uploads modified
#### ~~Search queries externalized~~ ✅ — `search_queries/*.yaml` + loader + deploy validation

---

## Completed (2026-06-13 through 2026-06-14)

#### ~~additionalProperties: false drops casualties/equipment~~ ✅ Fixed
Added CasualtyID, date_string, impacted_* to CASUALTY_ITEM_SCHEMA. Added 8 fields to PEOPLE_GROUP_ITEM_SCHEMA.

#### ~~Supplemental_Materials key mismatch (primary path)~~ ✅ Fixed
Prompt updated to singular, `sanitize_supplemental_data()` normalizes plural→singular.

#### ~~index.json race condition~~ ✅ Fixed
Per-directory `threading.Lock` wraps read-modify-write. `_build_date_id_lookup` outside lock.

#### ~~BackgroundSync uploads partially-written files~~ ✅ Fixed
Skips `.tmp` files + requires mtime stable >2s before upload.

#### ~~DynamoDB dual-write silent failures~~ ✅ Partial
Upgraded to WARNING, added `_track_failed_write` tracking. Reconciliation not yet wired.

#### ~~Phase 3 no-op on review-all-data~~ ✅ Fixed
`force_reprocess` now skips DynamoDB materialization, downloads full S3 corpus.

#### ~~Phase 3 tears down NAT while Phase 2 running~~ ✅ Fixed
`_teardown_networking` and `_stop_openserp_if_running` check for other phase locks before tearing down.

#### ~~NAT guardrail kills networking during long Phase 3~~ ✅ Fixed
openserp_manager checks `_any_lock_held()` before force teardown.

#### ~~EIP leak from overnight bounce loop~~ ✅ Fixed
`_create_nat` releases orphaned EIPs before allocating. Fresh EIP each time (reputation isolation).

#### ~~Reconciliation launches Phase 3 with no book (network thrash)~~ ✅ Fixed
Only relaunches if `pending#enrich#*` queue has entries. Uses book name from queue.

#### ~~Per-book queues (Phase 2 + Phase 3)~~ ✅ Implemented
`pending#parsed#{book}`, `pending#enrich#{book}`. Networking stays up between books.

#### ~~Prompts externalized to YAML~~ ✅ Done
people_groups, casualties, logistics, weather, biography — no inline fallback, fail on missing YAML.

#### ~~Deploy-time prompt validation~~ ✅ Done
11 YAML files validated (existence, template, schema JSON) — blocks deploy on failure.

#### ~~Phase 3 lock status + book name~~ ✅ Done
Lock item includes `book` and `status` fields. Phase 3 updates status at each step.

#### ~~Download progress logging~~ ✅ Done
Every 500 files with elapsed time. Total count shown upfront.

#### ~~Cache hit periodic logging~~ ✅ Done
Every 100 hits at INFO level.

#### ~~Trivy blocking + pip-audit fallback~~ ✅ Done
Deploy fails on HIGH/CRITICAL vulnerabilities. Podman support added.

#### ~~Entity validation skip list~~ ✅ Fixed
`index.json`, `duplicate_report.json`, `not_duplicates.json`, `not_people.json`, `not_related.json`, dotfiles excluded from entity schema validation.

#### ~~phase3_enrich_data.py missing `import json`~~ ✅ Fixed

---

## Completed (2026-06-09 through 2026-06-13)

_(See git log for details — 23 items including Phase transitions, batch states, empty guards, prompt fixes)_

---

## Completed (2026-06-03 through 2026-06-07)

_(See git log for details — 30+ items including DynamoDB entity store, incremental dedup, cost optimizations)_
