# Completed backlog items (archived 2026-11-17)

Moved verbatim out of `docs/current/TODO.md` on 2026-11-17 so the backlog lists open work only.
Open work: `docs/current/TODO.md`.

---

<!-- From "Phase 3 work order", Group 4 (`_download_inputs`) -->

- **DONE 2026-11-17 (PR #756; was gap 27) - a single-book Phase 3 downloaded no map, image or
  narrative-note records.** Fixed: per-book types read from the book manifest (now incl. images),
  shared types downloaded in full, map binaries only when a vision retry needs them, changed-only
  final sync; details in `PHASE3_MAP.md` §1 "What `EP _download_inputs` does", step 3. Runs before the
  fix are not repaired: re-run Phase 3 for a book that ran scoped.

---

<!-- From "Other open items": ✅ Recently CLOSED -->

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


---

<!-- §7 COMPLETED -->

### 7. COMPLETED

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
8 concurrent sub-agents -> 5 root patterns (docs/archive/2026-11-16/PHASE2_REVIEW_2026-10-20.md). Every finding
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
  `docs/archive/2026-11-16/dataquality/PHASE2_OBSERVABILITY_AUDIT.md`.
- Tooling: `scripts/utils/batch_metrics.py`; `scripts/ai_review.sh`.

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
  SQLite-guard + Postgres-adapter doc~~**. **~~Storage role-split review~~** (`docs/archive/2026-11-16/STORAGE_REVIEW.md`).

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




---

<!-- Multimodal epic M1-M8 (all ✅ DONE) and "Resolved this session" -->

### [M1 · HIGH · in-doc-asset-links] ✅ DONE (PR #587) — Follow in-document map LINKS -> map-as-source  (did FIRST)
Evidence: `[Map VII]..[Map XIV]` in the narrative link directly to `maps/USA-E-XChannel-*.jpg` (author-
curated, highest-quality maps on the page). GAP: extract_images matches `![]()` not `[]()`, and nothing
follows a `maps/*.jpg` link from text. FIX: a parse-time extractor that finds `[text](url)` links whose
target is an image ext OR under `maps/`, and routes them into the EXISTING map-as-source adapter
(map_vision -> depicted -> contentrepository/ source). Reuses #585 wholesale; only the discovery front-end
is new (in-doc link vs OpenSERP search). SSRF-safe + same-host (ibiblio) allowlist. Highest value, lowest
ambiguity (the map is explicitly linked + captioned). Addresses RC1+RC3.

### [M2 · HIGH · embedded-image-extraction] ✅ DONE (PR #588) — Capture caption-only / bare embedded images (RC1)
Evidence: "PREINVASION SCENES", "ALLIED INVASION CHIEFS", "PARACHUTE TROOPS", "HEDGEROW COUNTRY", aerial
beach views — rendered as plain-text captions with NO `![]()` in the parsed markdown, so extract_images
never sees them -> the photo is never ingested at all. FIX: (a) in the HTML->md converter (the new
text_converters.html_to_markdown path), PRESERVE `<img>` as `![alt](src)` instead of dropping it; (b) for
HyperWar's figure-caption convention, pair an ALL-CAPS caption line with its image. Unblocks M3/M4/M5
(nothing downstream can classify/link an image that was never extracted). Addresses RC1.

### [M3 · HIGH · image-to-person] ✅ DONE (PR #589) — Named-group + headshot photos -> PersonID linkage (RC2)
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

### [M4 · HIGH · footnote-asset-links] ✅ DONE (PR #590) — Follow footnote links: MoH citations + org chart (RC3+RC4)
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

### [M5 · MED · image-to-place] ✅ DONE (PR #591) — Topology/aerial photos -> PlaceID linkage (RC2)
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

### [M6 · MED · chart-as-source] ✅ DONE (PR #592) — Org / command-structure charts -> people_groups source (RC4)
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

### [M7 · MED · embedded-low-q-maps] ✅ DONE (PR #593) — Inline figure maps + shared confidence gate
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

### [M8 · LOW · intra-corpus-links] ✅ DONE (PR #594) — Footnote links to OTHER ibiblio docs as INTERNAL corpus links
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

