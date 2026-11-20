# Pipeline Backlog

**Last Updated:** 2026-11-17 (Phase 3 open items reorganized into a dependency-ordered work order with test groups and owner decisions: first section; Phase 3 review started; all-docs review: code defects it found, see the first section); 2026-11-16 (docs review: three items marked done/partly done, see 2026-11-16 notes); previously 2026-10-29 (Pattern-8 observability P1 remediation A-E shipped + deployed; Slack Chatbot fixed to us-east-1; P2/P3 sweep + 3 review follow-ups + GIT_SHA gap logged below).

---

## Phase 3 work order (dependency-grouped, 2026-11-17)

The open Phase 3 items, ordered so related fixes can be tested together on the Phase 2 data. Rules:
(1) dependency first: an item another needs comes before it, and a MED or LOW that a HIGH needs moves
into that HIGH's group; (2) Phase 3 processing order next: groups 4-14 follow the step order, because each
step consumes the previous one's output; (3) priority last, inside a group. Entries are moved verbatim;
each keeps its original heading ("####") so its context travels with it.

**Test cycle for every group:** `python3 scripts/phase_snapshot.py restore --name phase2-2026-11-17`;
deploy the group's branch (owner approval); run Phase 3 for `USA-E-XChannel` (owner approval for Grok
spend); run `scripts/validate_phase3.py` and `scripts/validate_provenance.py`; compare with the Group 0
baseline (per-step counts, Grok requests and cost, alerts, S3 diff); restore before the next group. Cached
Grok book results last 10 days after last access (D9), so a group tested later than that pays Grok again.

Owner-led review of Phase 3, working down the flow chart in `docs/current/core/PHASE3_MAP.md` §1. Code
flaws found are recorded here (no code is changed during the review); a major Phase 3 refactor is
expected afterwards. Each entry: what is wrong, where (file:line at `main` cbd94d83), effect, how it
was verified. *refactor* = only a redesign fixes it. `EP` = `ecs_entrypoint.py`.

### Owner decisions (2026-11-17)

| # | Decision |
|---|---|
| D1 | Maps store appearance links only ("this unit, place and date appear on this map"); no placement or boundary model. |
| D2 | Places get dated containment (`containedInPlace` with valid_from / valid_to). |
| D3 | A missing region/country is created with the public `_find_or_create_place`. |
| D4 | A pending URL find is held in the review queue only, written to no entity JSON until a human approves it. |
| D5 | Unit/person Wikipedia/Grokipedia images: vision only on images the free Commons-metadata pre-check flags. |
| D6 | Vision-undecided images go to a separate image-review queue; its UI is TBD. |
| D7 | Equipment images: photographs only, but any photograph of the named model, whatever the setting or date. |
| D8 | Grok unreachable at preflight: retry, then stop with an alert. |
| D8b | No NARA key: leave the archive-path records unresolved for the next run, alert. |
| D8c | OpenSERP unavailable at start or mid-run: the whole phase stops, stamps nothing, alerts (D8c-3). |
| D8c-2 | While OpenSERP is down: exit and leave the book queued for the reconcile (retry cap + alert). |
| D8d | Nominatim unreachable: skip geocoding for the run, stamp nothing, alert. |
| D9 | No reprocess purge. Grok book results (`books/<book>/...` only, D9-2) expire 10 days after last access; no TTL refresh on snapshot restore (D9-3). |
| D10 | Release the Phase 3 lock at once on failure. |
| D11 | NARA not-online UI: two use cases (more human online searches; a College Park research visit). A human-found URL is approved directly (D11-2); visit scans come in through normal intake (D11-3). |
| V1 | CONFIRMED 2026-11-17: the xAI Batch API answers vision requests. One-request probe (Map IV, pipeline `BatchRequest` JSONL format, `grok-4.3`, 1.3 MB) completed in about 2.5 h with the right answer (`is_map: true`, the map's title, five places on it); the same request live took 3.7 s. |
| V2 | NARA files are served from `catalog.archives.gov/medialz/` (checked with the catalog API v2). |
| V3 | No live evidence either way; fix the `pending#enrich#` row lifecycle in G1 regardless. |

### Group 0 - baseline and test harness

Nothing else can be tested until the deployed code is current and measurable. Deploy current `main`
(the running image is bc3ea342), make every build carry its git SHA, fix the local `.env`, then run
Phase 3 once on the restored Phase 2 data as the baseline every later group is compared with.
**Test:** the baseline run itself.

#### Local environment: `.env` pins a retired Grok model (found 2026-11-17)

- **LOW - local runs and tests ask for a retired model.** The local `.env` (gitignored) sets
  `GROK_MODEL=grok-4.20-0309-reasoning`; `grok_models.default_model` gives `GROK_MODEL` priority over
  `api.grok.model` (`src/utils/grok_models.py:142`), so a local `GrokClient` requests it (seen in the V1
  live vision test, 2026-11-17). `config.yaml:23` records that xAI retired it (May 15 2026) and redirects
  to `grok-4.3`. The tracked `.env.example` says `GROK_MODEL=grok-beta`, also stale. ECS is not affected
  (no `GROK_MODEL` in the task definitions; the model comes from `config.yaml`). Fix: remove
  `GROK_MODEL` from `.env.example` (and tell developers to drop it from `.env`), so `config.yaml` is the one
  place the model is set; or have the preflight model check (`grok_models.preflight`) warn when
  `GROK_MODEL` overrides `api.grok.model`.

#### Older backlog - HIGH list
- **[HIGH · data-quality] Wikipedia rate limits became "not found" for every entity type (FIXED 2026-11-06, not yet deployed).**
  Seen in the XChannel-8 dev E2E: every Wikipedia bio lookup after the first got 429 / Retry-After 60. The shared
  pooled session (fetch-provenance migration `5711c48d`, 2026-10-23) retried each 429 three times at the full
  Retry-After (180 s per lookup), then raised; callers recorded a miss. People were stamped `not_found` (90-day
  suppression), and groups, equipment and places stamped `wikipedia_checked_at` (never re-fetch). FIX: all 11
  Wikimedia call sites go through `src/utils/wikimedia_client.py` (per-host back-off, polite short waits, TRANSIENT on
  429/403/5xx, records left unstamped), guarded by `tests/test_wikimedia_client.py`. REMAINING:
    - After deploy, re-queue this run's casualties: people with `enrichment_status=not_found` and
      `last_enrichment_search=2026-11-06` whose fetch-provenance shows a Wikipedia `RetryError` (Eisenhower, Marshall,
      Montgomery, Ramsay, Bedell Smith, Leigh-Mallory, Tedder, Bradley, Kirk, Dönitz, Rommel, ...).
    - Grokipedia hit + Wikipedia rate limited stamps the person `enriched`, so Wikipedia is never tried again for them.
    - A deterministic parse failure (malformed body, missing key) is now deferred every run instead of negative-cached:
      add a deferral count or backstop. The same applies to a permanent 403.
    - A Commons license fetch that fails transiently is stored and positive-cached as `license="unknown"`.
    - `_extract_media_from_wikipedia` returns no media on TRANSIENT without deferring (pre-existing behaviour).
    - `scripts/utils/debug_searches.py` calls en.wikipedia.org directly (diagnostic only; the guard scans `src/`).
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

#### Older backlog - LOW list
- **[LOW · deploy-provenance] `scripts/deploy_all.sh` doesn't pass `--build-arg GIT_SHA`** so the pipeline image's
  `GIT_SHA` env/label is `unknown` (cosmetic provenance gap; image content is still current main). Pass
  `--build-arg GIT_SHA=$(git rev-parse HEAD)` in `build_scan_push`.

### Group 1 - shared foundations (no new Grok features)

What several HIGH items in later groups depend on; MED/LOW items are here when a HIGH needs them.
1. one paginated `pending#` queue module (the next-book / reconcile readers; the task deletes its own
   row only on success, keeps it on failure for the retry; owner D8c-2, V3);
2. DONE (1.5) deletes reach S3 (the final sync deletes every synced record the phase removed);
3. DONE (1.6) the global content-hash dedup claim is released when the content was not stored;
4. DONE (1.4) non-search steps independent of `use_openserp`; `use_openserp: false` honoured on ECS;
5. one alert path to `phase2-complete` (preflight aborts, crashed task, validation failure, reconcile
   failure, unreachable OpenSERP, short bibliography download);
6. MOVED to Group 2 (owner 2026-11-18): per-request identity for Phase 3 - Phase 3 runs live today, where an answer cannot be swapped; the 2026-11-11 swap was the positional batch-result mapping (fixed: `batch_api.align_by_request_id`), so the identity check is added when Phase 3 goes onto the Batch API;
7. DONE (1.8, `src/extraction/entity_writers`: `find_or_create_place` / `find_or_create_date` / `find_or_create_group`, each through the Phase 2 writer under the entity's index lock; image_place_link uses it) public find-or-create entity writers (places, dates, people_groups) shared by Phase 2 and 3. Hardened: names/dates from untrusted text are validated, a per-run creation cap alerts, a schema-refused record is never indexed (Phase 2: that place mention is skipped with an alert), and new place/date/group filenames end in the ULID's random tail (the timestamp head let two same-second records share a file). Left: the Phase 2 dates extractor still saves its whole in-memory index at the end of a chapter (last writer wins against a concurrent entry-point creation); `enrich_biographies`' filename fallback cannot find a legacy head-named group missing from its index (warns);
8. DONE (1.9, `src/utils/link_policy`: `trust_basis` (host + path), `link_scope`, `follow_limits`, logged at Phase 3 start; `link_relevancy` now trusts these sources instead of all of ibiblio.org; still to rewire in their groups: `source_material_paths`, the HyperWar recognizer incl. the PDF form, `trust_basis` on queued items and book meta, the follow limits in the OpenSERP-page and footnote-link paths; the fetch path must upgrade trusted `http://` citations to https and fetch the original URL; consolidate the host helpers `url_registry._host_of` / `link_relevancy._host_matches` / `source_retrieval.is_blacklisted` / `link_policy`; cache `follow_limits`' config once it is called per page) config: `trusted_sources` (HyperWar html+pdf; NARA `catalog.archives.gov/id/` + `/medialz/`) and
   `links.follow` (same_domain / external: max_hops 1, max_per_page 20), read in one place;
9. DONE (1.10, 2026-11-18) every Phase 3 write stamps metadata (`entity=`), so change detection works: both shared writers (`write_json_with_lock`, by `entity=` or the record's directory since 2026-11-06, and `locked_json`'s save) stamp `_schema_version` + `_last_updated`; an audit found no Phase 3 code writing an entity record raw (the raw-write ratchet, which only counts sites, is lowered to 70); pinned by `tests/test_phase3_writes_stamp.py`. Left: `bibliography_resolver` writes narrative notes with `entity="narrative_notes"`, which is not a registered entity, so they get the minimum version 2.24 (register it or write unversioned); the places_geo and OOB reconcile reports get the same fallback stamp; `bibliography.backfill_bib_titles` (no production caller) rewrites records raw.

**Test:** restore, run with OpenSERP off: the non-search steps run; force each alert once; a relocated
record disappears from S3; two queued books each run exactly once.

#### Resilience tasks added 2026-11-18 (owner: "deferred yes, dropped never")
Owner rule: no outage loses a book. After Grok funds, an AZ or AWS networking come back, the work
continues from where it stopped. Order: 1.2b, 1.1b, 1.2c, 1.2e, 1.2d, 1.2f, 1.1c.
- **1.2b - defer, never drop.** Past the attempt cap `pending_queue.claim_attempt` deletes the
  `pending#enrich#<book>` row (task 1.2), so the book is never picked up again. Instead keep the row
  as `deferred` with its cause: `funding` and `connectivity` use no attempt and resume automatically
  when their check passes; `failure` stays capped at `queue.phase3_max_attempts` and resumes with
  `scripts/resume_book.sh <book>` (or all deferred books). Preflight aborts do not count as attempts
  (owner decision 4(b)). The reconcile launches nothing while a funding or connectivity check fails.
- **1.2b follow-ups (from review, 2026-11-18):**
  - DONE (1.2e) - repeated auto-resume is capped: `connectivity` deferrals are counted
    (`connectivity_stops`); past `queue.max_connectivity_deferrals` (24) the book is held
    `failure`; `resume_book.py` resets the count. `funding` is not capped (it stops before spend).
  - DONE (2026-11-19) - a held (`failure`/`config`) book is re-announced once a day
    (`queue.held_reminder`, through `src/utils/alert_window`, the window the credit-hold alert now
    shares).
  - DONE (2026-11-19) - runnable books go never-deferred first, then the oldest `deferred_at`
    (was alphabetical), so a book that keeps renewing the hold goes last.
  - DONE (2026-11-19, `ecs_entrypoint._attempt_counts`) - an ordinary failure, then an outside
    SIGTERM in the same task, gave that attempt back; a failed phase now consumes its claim (and
    marks the task `claimed=False`, so the trigger gives nothing back either).
- **1.2c - the NAT does not fail over.** A NAT gateway is zonal by default; `nat_manager` always
  creates it in `PUBLIC_SUBNET_ID` (PublicSubnet1, `lambda_handlers/nat_manager.py:528`) and both
  private subnets route through it. If that AZ fails, both AZs lose egress and the re-create fails in
  the same subnet. Owner decision (a), 2026-11-18: keep ONE zonal NAT ($0.045/h) and fail over to
  PublicSubnet2; a regional NAT bills per AZ (~$0.09/h for our 2 AZs, VPC pricing page) for a rare
  event. Built in 1.2c: a subnet is marked bad (60 min) when its NAT cannot be created, fails,
  stays pending or never becomes available, or when a task finds no internet through a "ready" NAT
  (two probe hosts; one failover request per task, at most one failover per 10 min); the failover
  is alerted; the primary is used again for the next NAT created after the window (a running NAT
  is not moved back). Only AZ-attributable create errors mark a subnet bad; any other error, or no
  NAT in any AZ, alerts "Networking FAILED". Accepted risk: a task whose own egress is broken can
  trigger one failover (both probe hosts unreachable), at most once per 10 minutes.
- **1.2d - the resume point.** Phase 3 skips already-enriched records, but work since the last S3
  sync (`SYNC_INTERVAL` 120 s) is redone, and `_reset_openserp_searched` runs at every Phase 3 start,
  clearing "searched" on every person/equipment record with no images and no awards, so each resume
  repeats those searches. That reset cannot tell three outcomes apart; owner rules (2026-11-18):
  1. OpenSERP found nothing: log it for later review (entity ID, query), mark searched, move on.
  2. Grok rejected every candidate: log it with the Grok prompt, the candidates, the verdicts, model
     and RequestID (the prompt may need revising), mark searched, move on.
  3. Grok failed: handled by the shared Grok client library and resubmitted; never treated as a NO.
  Then delete `_reset_openserp_searched`; the existing 90-day re-search gate stays. Also check every
  other step's skip logic; test that a run killed midway resumes with no repeated Grok or OpenSERP
  work. Review log: JSONL in S3 (one prefix per book), written through the shared S3 writers.
- **1.2d built (2026-11-18):** each OpenSERP entity search is traced (queries: results / empty
  / error; Grok verdicts with prompt and model); `search_outcome` decides: empty / excluded /
  rejected -> appended to `output/review/openserp_misses/<book>/<date>-<task>.jsonl` (synced to
  S3) and marked searched; OpenSERP unavailable (a query errored) -> NOT marked;
  `_reset_openserp_searched` deleted; people are searched and written one at a time. Phase 3
  resume inventory of the other steps (review 2026-11-18) - fix in each step's group:
  - DONE (2026-11-20, owner: "record empty responses for archive.org, gutenberg, Amazon")
    bibliography_resolve re-asked archive.org / Gutendex the same question every run: every
    candidate search, the archive.org metadata fetch and the OpenSERP Gutenberg search now go
    through `search_cache.cached_answer`, which records the answer - an EMPTY one too - for
    `supplemental_material.search_cache_days` (90) across runs and tasks; an answer given while
    the source was down is never recorded; the older archive.org single-result search keeps
    empty answers for the same window. OpenSERP (a scraped engine: an empty 200 may be
    throttling) uses `openserp_cache_days` (14); archive.org metadata (a fresh upload may still
    gain files) 7 days. AMAZON: no Amazon source
    exists in the code - the same helper is ready for it (see the open question to the owner);
    ~~a NARA exception cached as a permanent miss~~ (DONE 2026-11-20); `review_queue.json` written only at the end (`:2435`);
    DONE (2026-11-20) `max_items` counted skipped records (it now counts records worked on) and
    `review_queue.json` was written only at the end (now as it grows);
  - claim-before-write (lost forever on a kill): in_doc_map_links (`map_link_source.py:219`, ties
    to 1.6), intra_corpus_links (`intra_corpus.py:212`), ~~openserp_external_maps `mark_searched`
    before processing~~ (DONE 2026-11-20);
  - DONE (2026-11-20) failures stored as terminal: hill geocode Grok exception and an
    unparseable geocode reply (both now retriable, `ask_geocode`), a transient page fetch
    (RETRY_LATER 1 h); a NARA exception is no longer negative-cached and a NARA 429/5xx or
    network error is an outage (`backend_down`, never queued as "not found online");
  - DONE (2026-11-20) disk-only state lost with the task: Nominatim answers and the OpenSERP
    map-search history now also live in the shared search cache (an outage is never stored); a
    map search is marked only after its results (`openserp_maps`). In-doc maps written to
    `contentrepository/`: moot, recycling maps is stopped (owner 2026-11-17, Group 8);
  - DONE (2026-11-20) link_asset_review queued a duplicate PENDING item on every re-run: each
    link of a source section now has a deterministic fetch_event_id recorded once
    (`record_fetch_event(once_key=...)`, a conditional put: a reviewed item is never reset);
  - DONE (2026-11-20) maps `index.json` is saved after each document.
  - DONE (2026-11-20) the OpenSERP SQS worker is traced like the directory driver: misses go to
    `review/openserp_misses/<book>/` in S3, and an entity whose searches only errored is no
    longer marked searched (it was) - it is sent again fresh;
  - DONE (2026-11-20) an entity whose queries always error is counted per run in the search
    cache and, at 5 runs, logged `openserp.entity_never_searched` (still retried).
- **1.2e - OpenSERP verification bypasses the shared Grok library** (found 2026-11-18). `GrokClient`
  (`src/grok_client.py`) already retries transport errors (`_call_api`: 5 tries, exponential backoff
  on HTTPError/ConnectionError) and repairs or re-asks a malformed JSON answer (`extract_json`). The
  OpenSERP verifiers call raw `chat_completion` and parse the text themselves
  (`src/enrichment/openserp_enrichment.py` `_verify_result`, `_verify_results_batch`,
  `_parse_batch_verdicts`):
  - a reply that does not start with "YES" becomes NO and is **cached for 90 days**, so a malformed
    reply is a permanent-looking rejection, never resubmitted;
  - in the batch path an unparseable reply gives `[]`, every result becomes NO, and each NO is
    **cached** (the comment says fail-closed and "not cached", but only the exception path skips it);
    the raw malformed reply is also cached by the client under `openserp_verify_batch`;
  - after the client's retries are spent, the error is a NO, and the record is still marked searched
    (`openserp_enrichment.py:1496`), so only the start-of-run reset retries it.
  Fix (DRY): verify through `extract_json` (JSON verdict array: repair, re-ask), cache only real
  verdicts, never cache or mark searched on a failure; a Grok outage follows D8 (retry, then stop
  with an alert) and defers the book `connectivity` (1.2b). Belongs with Group 2 batching of these
  calls. Check the other Phase 3 callers of raw `chat_completion` for the same pattern.
  **Built (2026-11-18):** the shared client now stops the run when its retries are used up on an
  outage (`grok_models.GrokUnreachable`: network, timeout, 5xx, 429) or xAI refuses for credit
  (`GrokFundsExhausted`); both are run-stopping like a missing model, `stop_cause.run_main` maps
  them to exit 81/80 in phase2/phase3, and the wrapper defers the book (1.2b). This fixes the
  outage case for EVERY Grok caller at once. The OpenSERP verifiers use `extract_json` and raise
  `VerifyFailed` on no usable verdict (not cached, entity left unsearched). Optional requests
  (book_metadata) catch the stop and go on; a transient stop expires after 5 min in long-lived
  processes. Still to do, per step group: the other Phase 3 raw `chat_completion` callers parse
  text themselves - check each for a malformed answer being taken as an answer. DONE
  (2026-11-20): one-word verdicts go through `src/utils/grok_choice.ask_choice` (first word must
  be a choice; otherwise the cached reply is evicted - `GrokClient.evict_completion` - and asked
  once more, then the caller's safe default) in image_place_link, link_relevancy, fetch_review
  (exclude scope), title_memberships and the four bibliography verifies (`_ask_yes`); the three
  Grok geocoders share `places_grok_geocode.ask_geocode` (a Grok error is transient, an
  unreadable reply is evicted, re-asked, then left retriable - never `geocode_not_found`, and
  not counted as a geocoder outage). Cache keys are unchanged (no answers re-paid). Was:
  `extraction/image_place_link.py:82`, `extraction/link_relevancy.py:82`,
  `enrichment/fetch_review.py:253`, `enrichment/hill_geocode.py:124`,
  `enrichment/places_grok_geocode.py:529`, `enrichment/situational_geocode.py:338`,
  `enrichment/bibliography_resolver.py:1114,1900,1942,2014`, `extraction/title_memberships.py:238`.
  The OpenSERP SQS worker keeps the message and pauses 5 min on a Grok stop. Mid-run only a 402
  stops for funding (a 403 that merely says "credit" is an ordinary error); funding deferrals are
  capped too (`queue.max_funding_deferrals`, 72). Left for 1.2f (review findings):
  - DONE (2026-11-20) the client's wall-clock deadline raises `requests.Timeout` (shared retries,
    then `GrokUnreachable`); it also really returns at the deadline now (the old `with` pool
    waited for the hung thread on exit). Accepted cost (LOW): a request abandoned at the deadline
    may still be billed, and the retry re-posts it (at most 5 posts; the deadline fires only
    for a connection that trickles past the 600 s read timeout);
  - DONE (owner 2026-11-20: "batches must use shared outage") every xAI Batch API call (upload,
    create, poll, retrieve, the poller's status check) goes through `batch_api.xai_request`: a
    network error / timeout / 429 / 5xx is retried (5 tries; 2 in the poller), then the run stops
    as `GrokUnreachable` (402: `GrokFundsExhausted`); a batch already submitted is kept for the
    poller (its in_task job and credit reservation stay), never marked failed; the poller keeps
    a batch `pending` while xAI is away (no process-wide stop in the Lambda; the rest of that poll
    cycle waits). Creating a batch is never retried after it may have reached xAI (no second
    billed batch; `batch.create_uncertain` says to check the console); an outage after the submit
    hands the batch to the poller (BatchPending); an in-task poll rides out ~4 min of errors.
    Alert notes now keep their spaces (`observe._safe_note`, 500 chars) so commands and S3 keys
    can be pasted; Slack markup in alert text is escaped (`slack_formatter`). Phase 2 stops are
    still told to re-trigger (no Phase 2 deferral);
  - DONE (owner 2026-11-20) Phase 0 and Lambda callers: the markdown repair is optional (a Grok
    stop sends the document to human review); the translation is required - phase0_convert /
    phase0_ocr_crosscheck / phase0_video exit 81/80 (`stop_cause.run_main`) and the alert names
    the upload to repeat (or the OCR re-drive); the OCR merge Lambda frees its claim and alerts
    `ocr_merge.grok_unavailable` with the re-drive command.
- **1.2f - every external service is resumable** (owner 2026-11-18). Grok, NOAA weather,
  geocoding (Nominatim), archive.org, NARA, OpenSERP, HyperWar and any other fetch can fail at the
  service or the network level. For each: one shared retry policy (`src/utils/failure_taxonomy`
  causes: transient -> retry with backoff), then stop with `src/utils/stop_cause.EXIT_CONNECTIVITY`
  (1.2b defers the book, no attempt used, resumes automatically) instead of stamping records as
  done or skipping silently; on resume the step continues from the first record not finished. Grok
  batches: an in-flight batch is adopted on resume; a failed or expired batch is resubmitted whole,
  a partly failed one only for its failed requests (`Policy.RESUBMIT_BOUNDED` / `RECOVER_SUBSET`,
  already defined, check they are wired). Inventory every Phase 3 caller of an external service
  first (OBSERV per service: `<service>.unavailable`), then fix each through the shared libraries;
  D8, D8b, D8c and D8d are cases of this rule.
  **AWS services too** (owner 2026-11-18): SNS, SQS, S3, DynamoDB, Lambda, Secrets Manager, and
  ECS/EC2 failing to launch a task (RunTask capacity or API errors; Spot capacity). Same rule:
  bounded retry through one shared helper, then leave the work queued (never lost) and alert; on
  recovery continue from the first unfinished record. Known cases to check: a failed `RunTask`
  keeps the queue row (task 1.2, verify every launcher); an S3 write that fails must not mark the
  record done (ties to 1.6); an undeliverable SNS alert must still leave a record (1.1b); an SQS
  message that fails processing must return to the queue, not be deleted.
  **Spot** (owner 2026-11-18): a Spot task can be reclaimed at any time (SIGTERM, ~2 min warning,
  or killed). Today that counts as a Phase 3 attempt, so repeated reclaims push a book to the
  `failure` cap. In 1.1b the trigger Lambda reads the STOPPED event's `stopCode`; for
  `SpotInterruption` it gives the attempt back (`pending_queue.give_back`), so only real failures
  count. Work since the last S3 sync (120 s) is the only loss; 1.2d makes the resume skip the rest.
- **1.2f slice 1 built (2026-11-18):** OpenSERP down at start (cannot be started) or failing
  mid-run (5 consecutive failed searches) stops Phase 3 (D8c: `grok_models.ServiceUnreachable`,
  exit 81, book deferred connectivity); a transient page fetch is no longer a 90-day REJECT;
  archive.org not answering is not cached as NOT_FOUND; a Nominatim outage is never replaced by
  an LLM-guessed coordinate and 5 transient places in a row stop geocoding for the run with an
  alert (D8d). Remaining, from the 2026-11-18 inventory:
  - DONE (owner 2026-11-18) - a step that raised no longer finishes the book: Phase 3 exits 82
    (`stop_cause.EXIT_STEP_FAILED`, an ordinary counted failure: the book stays queued and is
    retried, attempt-capped, saved work kept) or 83 when the cause was data corruption
    (`EXIT_DATA_CORRUPT`: loud `phase.data_corrupt` alert, book held `failure` until resumed).
    Definitions: run stop = a required service down (deferred); step failure = the step raised;
    record failure = one record left unmarked, retried next run, does not block the book.
    Corruption is ONLY `stop_cause.RecordCorrupt` (in the error or its cause chain), raised by
    `entity_records.read_record` for the book's own stored records; a parse/schema error on a
    service answer (HTML error page, Grok reply, write guard) is a step failure (82), so
    untrusted content can never hold a book. A held book's corpus stays unrepaired until resumed.
    DONE (1.2f slice 2, 2026-11-18): every Phase 3 record loader (`places_geo._load_place`,
    `noaa_weather`, openserp `_iter`/people/equipment, `groups_wikipedia`, `equipment_wikipedia`,
    `equipment_images`, `places_grok_geocode`, `bibliography_resolver`, `_load_parsed_docs`) now
    reads through `entity_records.load_record`: a corrupt record (not text, not JSON, not an
    object) raises a loud `record.corrupt` alert naming the file, is skipped so the step goes on
    with its other records, and Phase 3 exits 83 at the end (book held; `.phase_results.json`
    `corrupt_records` names the files in the hold alert). A missing file is an OBSERV warning,
    not corruption. Follow-ups DONE (2026-11-19): a step failure keeps its steps on the queue
    row (`last_failed_steps`, cleared by the next allowed start), the attempt-cap hold alert and
    `resume_book.py list` name them, and the failure notice says "attempt n of cap" or, on the
    final retry, "LAST attempt ... held at next start" with the resume command.
  - PART 2 (owner rule 2026-11-18), in each step's group: catch errors PER RECORD so one bad
    record does not stop the step and block every record after it. A record that fails on DATA
    CORRUPTION (unparseable / schema-invalid): error out loudly (alert, set aside for review).
    Any other record failure: leave it unmarked and queued for retry. Use
    `entity_records.read_record` / `stop_cause.is_data_corruption` (DRY). Steps that mark a record done before the work (see
    the 1.2d inventory) must be fixed in the same change.
  - DONE (1.2f slice 3, 2026-11-18, `src/utils/service_outage`): one shared way to report an
    outage. The bibliography search sources (Gutendex, archive.org search/candidates/metadata,
    OpenSERP book and Gutenberg searches) report 429/5xx/no answer as an outage, so a citation
    searched during one is `backend_down` (retried next run, never queued as "not found online");
    a missing NARA key (D8b) is the same for NARA citations and the wrapper alerts
    `nara.key_unavailable` in Phase 3. DynamoDB: URL-registry claims/finalize and the search
    cache count failures; 10 in a row stop the run (exit 81, deferred). Image hosts: a 429/5xx
    or no answer no longer uses an image's vision attempts (`host_unavailable`). S3: the wrapper
    client retries (adaptive, 10 attempts); a failed background upload no longer stops the pass;
    5 failing passes alert `s3.sync_failing` once, and the watchdog does not kill a task whose
    only "stall" is S3; a merged-away record left in S3 is alerted with the `aws s3 rm` command.
    Review hardening: 403/408 from a search host is an outage; an outage-affected citation is not
    reused for a duplicate; image hosts: only a timeout or 429/5xx is an outage (refused/TLS/DNS
    is a dead link), and after 5 such runs the image goes to needs_review (images schema 2.29
    `host_unavailable_runs`); the S3 watchdog bypass ends after the watchdog window; the
    merged-delete alert points to a key manifest (`manifests/merged_delete_left/<task>.json`),
    never a pasteable command. DONE (2026-11-20): the emergency (SIGTERM) sync is retried once
    when it fails fast (`_emergency_sync`); a citation whose search sources were down in
    `BACKEND_DOWN_RUN_CAP` (5) runs in a row is also queued for a human with the reason (count in
    the search cache, 30-day TTL; it keeps being retried). DONE (owner 2026-11-20): `watch()` follows
    work into a thread pool through `service_outage.OutagePool` / `carry` (only the watch scope is
    carried, so cache scoping in existing pools is unchanged).
  - DONE (2026-11-20) `url_hygiene._pinned_get` has the shared retry (429/502/503/504, or a
    network error that failed in under 10 s; not a stall or a TLS failure; 3 tries, Retry-After or
    2 s/4 s, capped 30 s); the local loops in `source_retrieval` and `wayback` were removed (DRY);
    `url_fetch` keeps ONE try (`_pinned_get_once`) because it falls back to Wayback itself;
  - NOAA: moved to its own task, 1.2g (owner 2026-11-18).
  - DONE (2026-11-20) a walled / rate-limited page with no archive is cached `RETRY_LATER` for
    1 h (`openserp_enrichment.RETRY_LATER_HOURS`): not re-fetched or Grok-verified again by other
    entities meanwhile, never REJECT;
  - DONE (2026-11-20) `lambda_handlers/phase3_handler.py` deleted (not deployed, no caller);
  - DONE (2026-11-20, AWS cases): every RunTask launcher checked - the trigger releases its lock
    and re-raises (Phase 3 rows stay; SQS batches redeliver), the batch poller releases its
    retrieve claim, ocr_merge alerts with the re-drive; the trigger queue's visibility timeout
    was 300 s under a 900 s Lambda (now 1800 s) and had no redrive (a failing batch retried
    silently 14 days; now 5 receives -> the alarmed trigger DLQ); the OpenSERP worker re-sends a
    message stopped by a Grok/OpenSERP outage as a fresh delayed message (15 min; at most 96 times,
    24 h, then ordinary counted retries), so an outage no longer pushes the queue into its DLQ. DONE (2026-11-20): the trigger queue reports per-record
    failures (`ReportBatchItemFailures`; a failed batch is retried record by record, only the
    failing ones go back);
  - ACCEPTED (owner 2026-11-18): each start-time OpenSERP stop still brings NAT up and waits up
    to 12 min for OpenSERP before stopping (bounded by the connectivity cap, 24).
- **1.2g - troubleshoot NOAA weather** (owner 2026-11-18, its own task). NOAA 429 sleeps 60 s per
  file and goes on (`noaa_weather.py:~81`); find why NOAA rate-limits or fails, then make it
  resumable like the other services (service_outage, no record stamped on an outage, the step
  continues where it stopped).
- **1.1c - Grok balance thresholds.** DONE (2026-11-18, `src/utils/grok_balance`). The live
  balance is `/postpaid/invoice/preview` credit minus this month's use, NOT `/prepaid/balance`'s
  `total` (a ledger that posts each month's spend on the 11th of the next month: 2026-11-18 ledger
  $192.59, console $62.31). Team ID from the inference key (`GET /v1/api-key`); management key in
  `<env>-wwii-pipeline/xai-management-key` (ECSTaskRole only; Lambda and deploy roles denied).
  Phase 2/3 start: under `api.grok.credit_floor_usd` ($50) stop, alert `phase.grok_credits_low`,
  defer `funding`; unreadable: stop (`config` held / `connectivity` auto-resumes), never guess.
  During the run: every `balance_check_minutes` (15), at or under `credit_alert_usd` ($10) an
  hourly `grok.credits_critical` alert, the run goes on. Owner 2026-11-18: a Phase 0 import whose title-page
  Grok request is on is gated too (deal with it earlier); `api.grok.balance_check: false` switches
  the check off for other deployments (open source); auto top-up is the operator's call.
  `phase0_ingest`'s translation pass has no NAT, so it is not gated. Follow-ups: DONE
  (2026-11-19) the older preflight stops (`grok_credits_depleted`, `grok_key_invalid`, model
  unavailable, Grok unreachable) free the lock and NAT lease at once (`_exit_before_work`, D10);
  DONE (1.1e) a funding retry no longer starts a task and NAT just to read the balance: the
  trigger checks it before the launch and leaves the book queued.
  The balance formula is verified on one answer (2026-11-18); re-check it around the 11th, when
  the month's spend posts to the ledger.

- **1.1e - hold Grok launches while credit is low.** DONE (owner 2026-11-18,
  `src/utils/credit_hold`, `lambda_handlers/credit_check.py`). The trigger Lambda (outside the VPC,
  no NAT) asks the `<env>-wwii-credit-check` Lambda (the only Lambda allowed the management key;
  answer cached 5 min as `credit#balance`) before a Phase 2/3 or Phase 0 import launch. Under the
  floor nothing launches: a Phase 2/3 book stays on its queue (strict write: a failed write
  launches instead), a Phase 0 import becomes an entry of the `credit#holds` item (one id per
  launch, so a redelivered event is one hold), replayed by the hourly check once credit is back
  (claimed, removed only after the launch; a stale claim is retried, so a crash never loses it).
  Only allow-listed holds replay (Phase 0 task defs, CONVERT_KEY/VIDEO_KEY under
  `contentrepository/`); ECSTaskRole is denied writes to the gate's items. One
  `credit.launches_held` alert per 6 h. Unreadable here: launch, the task's preflight decides.
  Follow-ups DONE (owner 2026-11-18, same task): the dispatcher's `enumerate_pending` leaves Phase
  2/3 docs READY while credit is low (Phase 1 still dispatched); the batch poller's direct submit
  invoke starts no task and replies `held` with what to do; unscoped Phase 2/3 runs (manual,
  dedup-complete) are credit holds too (a held `EXPECTED_COUNT` replays only while
  `ALLOW_MANUAL_EXPECTED_COUNT` is on); each task def replays only its own env; holds are spread
  over 16 items (`credit#holds#0..15`, far below 400 KB each); one failing hold no longer stops the
  others; any gate error launches; `.`/`..`/`unknown` holds are rejected; the OCR controller and
  dispatcher roles are denied writes to the gate's items; a snapshot restore keeps holds and the
  balance; the credit-check cache warning names what to check. Left: the deploy role can replace
  the credit-check code (CI trust boundary); no reserved concurrency (fails on accounts with a
  low concurrency quota).

#### Task 1.1 follow-ups (found in review, 2026-11-18)
- **DONE (2026-11-19) MED - an alert from a task that has no network path is lost.** A stop alert
  the task cannot deliver is left on its stop marker (`unsent_event` / `unsent_note`, through the
  DynamoDB gateway endpoint) and the trigger relays it as `phase.task_alert_relayed` when ECS
  reports the stop (1.1b's unreported-stop path covered SIGKILL; this keeps the task's own text). When NAT never becomes ready the
  task has no route to SNS (nat-manager creates interface endpoints for ecr, logs, secretsmanager,
  ecs, ec2, lambda and scheduler, not sns), so the `phase.nat_not_ready` alert most likely never
  arrives; the task logs `OBSERV phase.alert_undeliverable reason=no_nat` as the record. The ECS
  task-failure rule (`cloudformation/compute.yaml:735`) only sends `check_locks`. Fix (DRY): have the
  trigger Lambda, on that STOPPED/non-zero event, publish one alert through the same phase2-complete
  path when the task did not report its own stop (e.g. a `phase.stop_alerted` marker it writes to
  DynamoDB, which the gateway endpoint reaches without NAT). This also covers a SIGKILL'd task.
- **1.1b scope additions (owner 2026-11-18):**
  - Fix the SIGTERM alert text: a Spot-stopped book is relaunched at once by the
    `dev-wwii-spot-recovery` rule (`trigger_handler._handle_task_stopped_event`), not by the
    hourly reconcile. Reuse that path (DRY) for the stop marker and for giving back an attempt when
    a task is killed outright (`stopCode`, SIGKILL).
  - Spot is never benign: the alert carries the book's reclaim count and the next launch (rung).
  - **Fargate Spot price ladder.** Goal: finish the work at the lowest price available at that
    moment; NEVER above the on-demand hourly price of the original task size (critical). On each
    reclaim of a book, relaunch on Fargate Spot at double the vCPU and memory, while the Spot
    hourly price of that size stays at or below the original size's on-demand price; when the next
    rung would exceed it, launch ON-DEMAND at the ORIGINAL size. Per-launch `cpu`/`memory`
    overrides on RunTask (no new task definitions); the rung is stored on the book's queue row; a
    success resets it. Phase 3 example (0.5 vCPU / 1 GB, on-demand ~$0.025/h): Spot 0.5/1 ->
    Spot 1/2 -> on-demand 0.5/1. Prices: the AWS Price List API has Fargate ON-DEMAND rates
    (`USE1-Fargate-vCPU-Hours:perCPU` $0.04048, `USE1-Fargate-GB-Hours` $0.004445, checked
    2026-11-18) but NO Fargate Spot rates. Owner choice (b): the Spot rate actually charged, from
    Cost Explorer (cost / usage of the `SpotUsage-Fargate-*` usage types over 7 days; 2026-11-04..18
    gave $0.014542/vCPU-h and $0.001597/GB-h), refreshed daily ($0.01 a request).
- **1.1b follow-ups (from review, 2026-11-18):**
  - DONE (2026-11-19) - a failed report keeps its facts and is listed in `stopevt#failed` (one
    item, no scan); the next scheduled check (15 min) re-sends it
    (`trigger_handler._sweep_failed_stop_reports`; the invocation no longer fails for it);
    the trigger has a DLQ (`<env>-wwii-trigger-dlq`, alarmed when not empty) and its error alarm
    fires on 1 error. Left (LOW, from review): a FIRST report whose Lambda is hard-killed
    mid-report leaves a `started` row with no facts and is not listed (store the facts and list
    the id at claim); a Spot-reclaimed task's own undelivered alert is not relayed (the reclaim
    alert goes out instead). Was: MED - a stop alert that SNS never accepts is lost after Lambda's 2 async retries: the trigger
    Lambda has no DLQ / on-failure destination, the `stopevt#<task>` row stays `failed` and is
    never swept, and `wwii-trigger-errors` (`cloudformation/events.yaml`) alarms only at 3 errors
    in 5 minutes - through SNS too. Add an on-failure destination (SQS) and a sweep that
    re-reports `failed` rows; alarm on 1 error.
  - DONE (2026-11-19, `iam.yaml` `NoLaunchStateWrites` / `NoSpotLadderClimb`) - ECSTaskRole may
    not write `price#fargate#` or `stopevt#` rows, and may only delete (reset) `spot#` rows.
  - LOW - Spot rates are historical (highest daily rate actually charged in 7 days); a Spot price
    rise since the last Spot day is not seen. Documented; `spot_ladder.max_fraction_of_on_demand`
    is the margin if wanted.
- **1.1d - EC2 Spot price ladder for the GPU OCR (AWS Batch, g5/g6)** (owner 2026-11-18, its own
  task). Same rule per instance type: on reclaim, move up the instance types while the live Spot
  price (`describe_spot_price_history`) stays at or below the original type's on-demand price
  (Price List API); then the on-demand queue at the original type. Extends `ocr_spot_controller`
  (today: Spot wait -> on-demand fallback, 48 h cap; no upsizing).
- **DONE (2026-11-19: the chain invokes the trigger with `{"source": "enrich-next"}`, which runs
  `_reconcile_phase3`; shared `ecs_entrypoint._invoke_trigger`) LOW - the end-of-run chain can pick
  a book that is already running.** Queue rows now stay while
  their book runs (task 1.2), so `_get_next_pending_enrich` may name a running book; its lock is held,
  the trigger re-queues it, and the next free book waits for the hourly reconcile (up to an hour).
  Reuse the reconcile's "try each until one launches" (`trigger_handler._reconcile_phase3`).
- **LOW - Phase 3 batch mode is not attempt-counted.** `run_submit_only` / `run_retrieve_only` do not
  call `_claim_enrich_attempt`; with `batch.phase3` on (Group 2) the retry cap would not apply. Add
  the claim there when Group 2 turns batching on.
- **DONE (2026-11-19) LOW - the NAT teardown signal scans the 26 MB table three times per check.**
  `scan_prefix` takes several prefixes (one scan, OR-ed `begins_with`); the teardown uses one.
- **DONE (2026-11-19) LOW - a standalone Phase 3 re-run can repeat the barrier-timeout alert.**
  Verified: run ids are written unless the run-marker write fails (alerted). In that case the
  timeout is `reconcile.barrier_unproven` and both timeout alerts now say to re-run the Phase 2
  dedup step before Phase 3 (Phase 3 alone waits again). The alerts ask the
  operator to re-run Phase 3; if the reconcile barrier and run marker lack a `run_id`, freshness falls
  back to `completed_at >= task start - 30 s`, so the re-run waits the full 300 s and raises
  `reconcile.barrier_timeout` again with nothing new. Verify run ids are always written; if not, skip
  the wait for an operator re-run (or downgrade the repeat).

#### Step: EP run_phase preflight (PHASE3_MAP §1 "ECS wrapper") (moved here from Group 4)
- **MED - three of the four preflight aborts send no alert.** Credits depleted, invalid API key
  (`EP:820-829`) and NAT never ready (`EP:715-729`) are a `logger.error` and `sys.exit(1)`. No
  `observe`/`publish_alert`, no CloudWatch metric filter matches them (the filters in
  `cloudformation/events.yaml` are "HOLDING work", "SPEND ALERT", "mentions NOT merged",
  "BatchSubmissionError", "Failed to acquire NAT lease"), and `_notify_failure` is only called when the
  phase subprocess fails (`EP:1062`). The ECS task-failure rule (`cloudformation/compute.yaml:735`) only
  sends the trigger Lambda a `check_locks`, which clears the lock. A model that is gone is the only
  preflight failure that alerts (`grok_models.model_unavailable` -> `publish_alert`). Fix (DRY): route
  the other three through the same alert path (`phase2-complete`, both channels).

#### Step: EP post-sync steps, on success (PHASE3_MAP §1 "ECS wrapper") (moved here from Group 14)
- **HIGH - the "next book" lookups miss queued books.** `_get_next_pending_book` and
  `_get_next_pending_enrich` (`EP:2543`, `EP:2564`; `Limit=100` at `EP:2552`, `EP:2573`) `scan` with `Limit=100` and a `FilterExpression`. In
  DynamoDB `Limit` caps the items READ before the filter, and there is no pagination, so only the first
  100 of ~33,500 items in `dev-wwii-api-cache` are examined. A queued `pending#enrich#<book>` is found
  only by chance. Effect: the next book is not triggered (it waits for the hourly reconcile), and
  OpenSERP can be scaled down while books are queued. Fix (DRY): one queue reader with a `begins_with`
  Query on a key designed for it (or paginate), reused by both and by the trigger Lambda. The trigger
  Lambda has the same flaw: `_get_pending_books_for_enrich` (`lambda_handlers/trigger_handler.py:2546`,
  `Limit=100`) and `_get_pending_books` (`:2479`, `Limit=1000`, also unpaginated), so the hourly
  reconcile can miss the same books and a queued book may never run.
- **MED - a Phase 3 validation failure sends no alert.** `validate_phase3.run` only prints
  `OBSERV phase3.validation result=FAIL` / `=== PHASE3 VALIDATION FAIL ===`
  (`scripts/validate_phase3.py:233-242`), and the caller logs the exit code at INFO (`EP:2367`). No
  `observe` / `publish_alert`, no metric filter. Alert through `phase2-complete` with the failure list
  (both channels).

#### Step: EP failure path (PHASE3_MAP §1 "ECS wrapper") (moved here from Group 14)
- **MED - a stopped or crashed task sends no failure notice.** Paths 2, 3 and 4 never call
  `_notify_failure`: a Spot interruption of Phase 3 (path 3, apart from the watchdog, which notifies
  before it signals) or a wrapper exception (path 4) leaves the book unfinished with no alert (the
  task-failure rule only clears locks). Fix (DRY): the task-failure rule, or one `finally` in `run_phase`,
  publishes one actionable alert (book, phase, exit code / stop reason, log link) to `phase2-complete`.
- **MED - a failed Phase 3 is not retried and its partial output is not audited.** The only code that
  deletes a `pending#enrich#<book>` entry is `_consume_pending_enrich` (`EP:2585`), called for the NEXT
  book after a successful Phase 3 triggers it (grep: no other delete). So a book launched that way has
  no entry left when it fails (nothing re-queues it; the operator must re-trigger), while a book
  launched by the trigger's reconcile (`_reconcile_phase3`) keeps its entry, so after success it can be
  picked as its own "next book" and run again. Verify against live logs before fixing. The partial writes are uploaded, but
  the post-sync referential repair, provenance check and validation run only on success (Phase 2 has
  `_audit_failed_phase2`; Phase 3 has nothing), and no record says the book's Phase 3 is incomplete.
  Re-queue on failure (with a retry cap), run the post-sync audit as Phase 2 does, and stamp the book
  `phase3: failed`.

#### Step: EP _download_inputs (PHASE3_MAP §1 "What EP _download_inputs does") (moved here from Group 4)
- **MED - a failed bibliography reconcile is silent, and Phase 3 then runs on unmerged shards.**
  `_reconcile_bibliography_full_corpus` (dedup step at the end of Phase 2, `EP:2883`) catches every
  exception and only `logger.warning`s it (`EP:2935`): no OBSERV event, no SNS alert. It has already
  deleted the barrier and run marker (`EP:2900-2901`), so Phase 3 waits the full
  `RECONCILE_BARRIER_WAIT_SECS` (300 s of idle task) and then proceeds anyway (`EP:1489`, a log line,
  also no alert), reading the per-task `bib_<hash>__<writer>.json` shards un-merged: citations
  mentioned by different tasks are not unioned. Fix: an actionable alert on the reconcile failure
  (publish to `phase2-complete`), and decide whether Phase 3 should hold instead of proceeding.
  Verified in code; no failure observed in live logs.

#### Step: places hierarchy and link_parent_places (PHASE3_MAP §4.2) (moved here from Group 6)
- **DONE (1.10: it stamps by directory since 2026-11-06; now `entity="places"` explicitly, and a refused write is not counted as linked) - LOW - the write does not stamp metadata.** `write_json_with_lock(pf, data)` (`places.py:639`) passes
  no `entity`, so `_last_updated` / `_schema_version` are not refreshed (the geocode writer does both);
  incremental change detection misses the change.

#### Step: intra_corpus_links (PHASE3_MAP §1 step table) (moved here from Group 9)
- **DECISION (owner 2026-11-17): HyperWar volumes and their PDFs are trusted sources.** A cited
  HyperWar volume (HTML or its PDF) is queued for ingestion with no URL Grok check and no human URL
  review; it is the one exception to the "every URL to review" rule. Trust is at the source level only:
  its content goes through the normal pipeline, where Grok classifies everything inside it (events,
  people, units, places, dates, equipment, casualties, maps vs images, charts, footnotes, bibliography),
  and the links inside it to non-HyperWar sites follow the review rule. To do:
  - Define "HyperWar" in config, not code: the `ibiblio.org/hyperwar/` path, not all of `ibiblio.org`
    (`config/domain_blacklist.yaml` already lists `source_material_paths`; reuse it). The current
    `link_relevancy_whitelist: ibiblio.org` is wider than the trust decision.
- **DECISION (owner 2026-11-17): NARA records are trusted the same way, and the trusted list will grow.**
  One config list of trusted sources replaces the per-feature lists (DRY), e.g.:
  ```yaml
  trusted_sources:            # skip URL Grok check + human URL review; content still Grok-classified
    - {name: hyperwar, match: "ibiblio.org/hyperwar/", forms: [html, pdf]}
    - {name: nara, match: ["catalog.archives.gov/id/", "catalog.archives.gov/medialz/"]}
  ```
  Adding a site is a config edit (no code change), logged at run start. NARA's digital objects are served
  from the same host, `catalog.archives.gov/medialz/...` (verified 2026-11-17 with the catalog API v2:
  `digitalObjects[].objectUrl`, HTTP 200, no redirect). Rules for every entry:
  - Trust covers the source, not the match. A cited link to a trusted source is queued directly. A
    trusted record found by a SEARCH (NARA catalog search in `bibliography_resolve`) still needs the
    existing Grok check that it is the cited document (`BR` verified findings); once matched, its
    online copy is queued for ingestion with no human URL review.
  - Content is still classified by Grok in Phases 1-3; links inside it to untrusted sites follow the
    review rule.
  - The queued item and the book meta record `trust_basis: <name>`.
  - `link_relevancy_whitelist`, `source_material_paths` and the hard-coded HyperWar recognizer read this
    list instead of keeping their own.
  - Recognize the PDF form as the same volume: `hyperwar_biblio._HYPERWAR_URL` matches the HTML page
    URLs only; add the PDF URL pattern so a PDF and its HTML volume resolve to one volume key and one
    slug (one ingestion, not two).
  - Record `trust_basis: hyperwar` on the queued item and the resulting book meta, so the exception is
    visible in provenance.

#### Cross-cutting: how Phase 3 results reach the S3 JSON

Every Phase 3 step writes its Grok-confirmed result into the local entity JSON through the one shared
writer `write_json_with_lock` (schema guard + metadata). Examples: geocode `_apply_result` ->
`_write_place` (`src/enrichment/places_grok_geocode.py:633-647`, `:903`: `coordinates`, `latitude`,
`longitude`, `geocode_source`, `enrichment_status`); bibliography `save` (`supplemental_advanced.py:360`)
and `bibliography_resolver.py:2142`; people_groups, equipment, places, source sections in
`openserp_enrichment.py`; images, maps, events, weather. The background sync (`EP:399`, every 120 s, all
of `output/` and `artifacts/` except `-parsed.json`, final flush in `stop()`) and `_final_sync`
(`EP:3470`, entity dirs) then upload each changed file whole. Verified in code. Gaps:

- Concurrency: whole-file PUTs mean last writer wins on shared records; covered by "was gap 28" below.
  Latent today: `MULTI_DOC_ENABLED=false` on the live `dev-wwii-phase3-enrich` task definition, so Phase 3
  runs one task at a time.
- The map-as-source files in `contentrepository/` are outside `output/` and never uploaded; see the
  map-vision step above.

- **DONE (1.5, 2026-11-18: `ecs_entrypoint._removed_downloads` - every entity record this task downloaded or background-uploaded and the phase removed locally is deleted from S3 at the final sync; one that another task rewrote in S3 after this task synced it (ETag from the listing, else LastModified) is kept and alerted; removals are snapshotted before the final uploads, so a SIGTERM sync never deletes a record whose replacement is not uploaded; more than max(50, 5%) removals delete nothing and alert `sync.mass_delete_refused`; S3 versioning keeps deletes restorable) - HIGH - a file Phase 3 deletes locally is never deleted from S3.** `track_deletion` (`EP:209`) is the
  only way to fill `_deleted_keys`, which `_final_sync` deletes from S3 (`EP:3550`), and it has no callers
  (trace_path: 0). Phase 3 case: `bibliography_resolve` moves each `not_citation` record to
  `output/narrative_notes/` and unlinks the bibliography file (`_relocate_narrative_note`,
  `bibliography_resolver.py:2252`). The note is uploaded but the old `bib_<hash>.json` stays in
  `output/bibliography/` on S3 (534 records on the current data), so the site would list narrative
  footnotes as citations and every later Phase 3 downloads and re-classifies them. Same root cause as
  the reconcile entry above ("records the merge folds into another stay in S3"). Fix (DRY): call
  `track_deletion` from each unlink of a synced record (or one shared `delete_record` helper used by
  both), so `_final_sync` removes the key.

#### Carried over from PHASE3_MAP.md §6 (found by the 2026-11-17 documentation review) (moved here from Group 11)
- **DONE (1.2f slice 1, D8c) - HIGH (was gap 1) - An unreachable OpenSERP is silent.** It now stops the phase with an alert. A start timeout or error only logs a
  warning (`EP:781-783`), and every driver then fails the `/health` probe, logs one warning and returns
  0 (`OE:1428-1436`). The steps report `status=ok enriched=0`, no `openserp.anomaly` fires (nothing was
  eligible), and no SNS alert is sent. A run can finish with no OpenSERP enrichment and nobody is told.
  Needs an actionable alert or a `skipped` status.
- **DONE (1.4, 2026-11-18: `phase3_enrich_data._run_document_link_steps`, outside the OpenSERP gate) - HIGH (was gap 3) - Five non-search steps are gated by `use_openserp`.** `image_person_link`,
  `link_asset_review`, `image_place_link`, `intra_corpus_links` and `event_provenance_stamp` sit inside
  `_run_openserp_enrichment` (`P3:351-385`). Setting the flag false, or a failed import or
  `reset_metrics` (`P3:285-292`, returns 0), also skips them and the SourceSectionID stamp. The comment
  on `_run_document_media_steps` says document media must not depend on the OpenSERP flag.
- **DONE (1.4, 2026-11-18: the service is not started when the flag is off; `_patch_openserp_url` writes only the address) - HIGH (was gap 4) - On ECS `use_openserp: false` has no effect when the service starts.** The
  entrypoint starts the service (Fargate and NAT cost) whatever the flag says, and
  `_patch_openserp_url` then forces the flag true (`EP:786-793`). The flag only holds when the start
  fails.
- **LOW (was gap 26)** - No per-request identity: Phase 3 sets no `current_book`, so no Phase 3 Grok
  request is BookID/RequestID stamped (see [GROK_REQUESTS.md](core/GROK_REQUESTS.md) section 1).
  Whether entity-level Phase 3 calls need an equivalent is an owner decision.

#### 2.4 URL-sourced enrichment + dedup (from section 2)
- **[HIGH · data-quality] Centralized JSON result de-duplication.** Add
  `src/utils/result_dedup.py::dedup_entity(data, entity)` at the ONE central write path, driven by a
  per-schema DEDUP_FIELDS declaration: URL arrays dedup on `url_hygiene.clean_url`, mentions on
  MentionID, free-form on canonical hash. First-writer-wins, idempotent, behavior-only.

---

#### LOW: equipment find-or-create not index-locked (PR #707 deferral) (from the end of this file)

equipment.py:2170/3443 find-or-create uses a per-event in-memory index under
ThreadPoolExecutor(max_workers=3), so concurrent events creating the same new equipment can each
mint a separate ULID file. NOT data loss — the downstream dedup pass merges the duplicates
(data-preserving: write-primary-first + backup-before-delete). Converting to the shared
locked_json(index) find-or-create (like places/people_groups) is the correct long-term fix but a
larger change (equipment's index build differs); deferred. Lower value than the entity=/vision/
whitelist fixes shipped in PR #707 since duplicates are recovered, not lost.

### Group 2 - batching (owner rule: every Grok submission is batched)

- **1.7 (moved here, owner 2026-11-18) - answer identity for Phase 3 batch requests.** When Phase 3
  goes onto the Batch API, give every Phase 3 request a RequestID (and the source document's
  BookID where it is about one document: map vision, document links, photo linking), so a swapped
  answer is rejected as in Phase 2 (`src/utils/request_identity`). Cost: each cached Phase 3 answer
  is asked once more (count them first).
- **Validation pass (owner 2026-11-18, after the Phase 3 list):** cached Phase 2 answers from
  batches before 2026-11-11 may be stored under another request's key; identity-checked kinds are
  caught on read (evicted, re-asked), others are not - check them.

Every Grok-validation feature in G3 and later must be batched, so this comes before them. Reuse the
existing batch code and the under-50 rule (`batch.min_requests`); Phase 2's multi-round hand-off; vision
requests through the same queue (V1: accepted; the first run confirms).
**Test:** restore, run `--batch`: same verdicts as the baseline, fewer live requests, under-50 answered
live, rounds logged.

#### Step: in_doc_map_links, image_map_vision (PHASE3_MAP §4.4) (moved here from Group 8)
- **LOW - the map-as-source vision request is never batched.** `_vision_depicted` (`map_link_source.py:61`)
  makes one live `extract_json_with_image` call per map, up to `max_recycled_per_doc` (50) per document.
  `image_map_vision` and the tier-3 `maybe_vision_classify_map` are also one live request per image.

#### Step: link_asset_review (PHASE3_MAP §1 step table, §4.4) (moved here from Group 9)
- **LOW - not batched** (`_grok_link_relevant` is one live request per link); covered by the batching
  entry.

#### Step: equipment_images (PHASE3_MAP §4.5) (moved here from Group 13)
- Vision requests are one live call per image and are not batched (covered by the batching entry: vision
  calls bypass `batch_mode`). Up to 20 downloads per record, until 3 are verified.

#### Wikipedia / Grokipedia footnote links (owner question 2026-11-17) (moved here from Track P)
- **Batching (owner rule): every Grok relevance request on these paths is batched.** The shared
  helper collects candidates (URL, entity ULID, context) across the step and submits them through the
  xAI Batch API path (`src/utils/batch_api.py` `BatchCollector`, as `GrokClient(batch_mode=True)`
  does); results are applied on retrieve, and only positives are enqueued. Today none of these checks
  is batched: `_verify_result` (OpenSERP, one live request per result; `_verify_results_batch`
  `OE:532` puts several results in one prompt but is still a live call), `_grok_link_relevant`
  (`link_relevancy.py:71`), `_validate_url_relevance` (`enrich_biographies.py:940`), and the
  bibliography verifies (owner decision already recorded under bibliography_resolve). Already-cached
  verdicts are filtered out before the batch, so a re-run submits only new pairs. Batch mode with
  OpenSERP is broken today ("was gap 5"): fix that first, and reuse its retrieve path.

#### Cross-cutting: batch every Phase 3 Grok request (owner 2026-11-17)

- **What is and is not covered today with `--batch`** (PHASE3_MAP §5):
  - Re-run against the batch answers: people_groups, places, `link_parent_place_ids`, bibliography,
    bibliography_resolve, equipment_images (`P3:_run_batch_rerun`).
  - Not re-run, so their queued answers are never applied: geocode's Grok fallback, the OpenSERP group
    (and its verify is cached as `REJECT`, "was gap 5"), in_doc_map_links, image_map_vision,
    image_place_link (`_grok_is_terrain`), link_asset_review (`_grok_link_relevant`).
  - **Never batched at all: vision requests.** `extract_json_with_image` and
    `extract_json_with_image_base64` (`src/grok_client.py:1790`, `:1857`) call `_call_api` directly and
    never check `batch_mode`, so map vision, image map/not-map, equipment image vision and caption
    vision stay live even with `--batch`. Fix: route both through the same cache-miss path as text
    (`:1640-1652`) so they queue too, provided the xAI Batch API accepts image inputs (verify first).
- Every step gets the same OBSERV lines Phase 2 has for batch (collected, submitted, answered live
  under the threshold, rounds) and an actionable alert when `max_rounds` is reached.

- **HIGH *refactor* - DECISION: Phase 3 Grok requests are batched by default, through the existing
  batch machinery (DRY) and the existing small-batch rule.** Today `batch.phase3: false`
  (`config.yaml:97`), so every Phase 3 request is live. Reuse, do not rebuild:
  - **Collect:** `GrokClient(batch_mode=True)` queues a cache miss and raises `BatchModeCollecting`
    (`src/grok_client.py:1649`, `:1710-1744`).
  - **Submit and the under-50 rule:** `GrokClient.submit_batch` (`:750`) answers fewer than
    `batch.min_requests` (50, `config.yaml:100`; env `BATCH_MIN_REQUESTS`; `batch_api.batch_min_requests`)
    live through `_answer_small_batch_live` (`:1269`), so a small run is not held up to 24 h. No step may
    add its own threshold.
  - **Rounds:** reuse Phase 2's multi-round hand-off (`batch.max_rounds`, `in_task_wait_minutes`, the
    poller Lambda; `docs/current/features/batch_processing/README.md`) instead of `_run_batch_rerun`'s
    single re-run. Phase 3 has dependent chains (OpenSERP verify -> fetch + summarize -> enqueue; map
    vision -> entity writes), so one round is not enough.
  - **Cache first:** cached answers are filtered out before queueing, so a re-run submits
    only new requests.

#### Carried over from PHASE3_MAP.md §6 (found by the 2026-11-17 documentation review) (moved here from Group 11)
- **HIGH (was gap 5) - Batch mode with OpenSERP is wrong.** In `--batch` the verify calls are queued,
  read as NO, cached as `REJECT` for 90 days, and the entities are stamped searched. `_run_batch_rerun`
  does not re-run OpenSERP or geocoding (PHASE3_MAP.md §5). Latent while `batch.phase3` is false.
- **MED (was gap 18) - Per-result Grok verification is the cost driver.** One request per hit,
  `_verify_results_batch` has no caller, and the insignia and patch queries run twice per group (once
  as `images`, once for the patch) because search results are not cached.

#### Older backlog - HIGH list
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

#### [MED · robustness, owner 2026-11-10] Hand a long-waiting batch to the poller instead of holding a Spot task (from the end of this file)

The Phase 2 task submits its Grok batch and then polls it IN-TASK (`batch_api.poll_batch`, up to
24 h) on Fargate Spot. 2026-11-10: the Cross-Channel Attack chapter 8 re-run's 99-request batch
(submitted 17:01 UTC) was still 65/99 after 4 h when Spot reclaimed the task (exit 143, 21:03). The
task died mid-poll, before `_enqueue_from_metrics`, so the batch had NO `batch_job#` record and NO
metrics file: orphaned (recovered by hand from `diagnostics/cache/<book>/batch_requests.jsonl` + the
`cachekey.submit` log lines). Long waits are pure Spot exposure and idle compute.
- **Hand-off:** past a 90-minute in-task budget (`BATCH_INTASK_MAX_MIN`, default 90 - owner
  2026-11-10: batches often answer in ~61 min, and up/down churn is to be avoided), write the
  metrics file (`submitted_request_ids` in order) and a `pending` `batch_job#`,
  then exit cleanly; the batch-poller launches the `--retrieve-only` task on completion.
- **Register at submit, not after polling:** write the metrics + `batch_job#` (status
  `in_task`, ignored by the poller) right after `submit_batch`, flipped to `retrieved`/`pending` at
  the end - so a task killed at any point leaves a recoverable batch; the poller adopts an `in_task`
  job whose task is gone.
- Done when: a test kills the poll mid-way and the poller recovers the batch with no hand work.

#### [LOW · obs, owner 2026-11-10] Batch timing statistics from the results, not from polling (from the end of this file)

`src/utils/batch_stats` (8449f2c3) records submit at submit time and progress on every change
from the in-task poll and the poller. Simpler and more exact (owner suggestion): keep the submit
write (xAI's batch record has only a DATE `create_time`, so the submit moment must be recorded
live), drop the per-poll progress writes, and at retrieve compute first/last result and per-type
durations from each result's own `created` timestamp (to the second). Live progress stays visible
in the poller logs.

### Group 3 - the shared URL path and the review queue

Owner decisions 2026-11-17: Grok validates every link (pre-validate, then validate; batched) before a
human sees it; a pending find is held in the queue only, written to no entity JSON until approved (D4);
approval submits to Phase 0; trusted sources skip URL review. One judge-then-enqueue helper for every
finder, verdicts keyed on (URL, entity), checksum re-check of approved URLs.
**Test:** restore, run: the queue holds finds (not search calls); no duplicates on a second run; a
rejected item stays rejected; an approved item starts Phase 0.

#### Step: OpenSERP group (PHASE3_MAP §3)

- **HIGH *refactor* - DECISION (owner 2026-11-17): a page OpenSERP finds and Grok confirms goes to the
  human URL-review queue; approval submits it to Phase 0.** Today `process_positive_url`
  (`src/enrichment/openserp_enrichment.py:808`) returns `{url, title, summary, fetched_at, source}` (plus
  the Wayback fields), and the callers write it inline on the entity: places `web_results[]` (`OE:2128`),
  people_groups `web_results[]` / veterans associations (`OE:1792`), source sections
  `primary_sources[]` (`OE:1185` -> `OE:1744`), people `military_awards[]` (`OE:1272`). It gets no
  bibliography record and no human review.
  The queue, the UI and the Phase 0 submission already exist (PR #577/#578), so this reuses them:
  - **Queue**: `fetch_provenance.record_fetch_event` with `kind=TEXTUAL, outcome=HIT` sets
    `review_status=pending` for a provider in `_ENQUEUE_PROVIDERS` (`src/utils/fetch_provenance.py:52`).
    OpenSERP is not in that set and `process_positive_url` records no fetch event, so no OpenSERP find
    reaches the queue. The M4 link path already does exactly this (`link_assets._enqueue_one`,
    `src/extraction/link_assets.py:187`); move that function to one shared enqueue helper and call it
    from both.
  - **UI**: the URL-review Lambda `dev-wwii-urlreview-ui` (`lambda_handlers/urlreview_ui_handler.py`,
    `cloudformation/compute.yaml:1221`) is deployed (last modified 2026-11-16). It lists pending finds
    and approves or rejects them (`fetch_review.affirm_fetch_find`, `reject_fetch_find`).
  - **Phase 0**: `fetch_review.submit_affirmed_batch` (`fetch_review.py:562`) uploads each approved
    find to `contentrepository/_system/<title>/fetchfind-<id>.html`. The trigger routes `.html` to the
    Phase 0 convert task (`lambda_handlers/trigger_handler.py:898`), with the global content-hash
    dedup first. The slug is `fetchfind-<id>` (`book_slug`, filename stem), so it is immutable.
  Gaps to close for OpenSERP finds:
  - **The page itself is not kept.** `_find_content_bytes` (`fetch_review.py:397`) submits the file at
    `quarantine_path` / `retrieved_path`; only `source_retrieval` sets one (`source_retrieval.py:193`).
    `url_fetch.fetch` keeps no bytes, so an approved OpenSERP find would submit only the provenance stub
    (URL + summary). Keep the fetched page (or its Wayback copy) and record its path on the event.
  - **The originating entity must be carried.** Put the entity type and ULID (PersonID, PeopleGroupID,
    PlaceID, SourceSectionID) and the search query in `request_params`, as `_enqueue_one` already does
    with `origin_SourceSectionID`, so the new source's events link back to that entity.
  - **The bibliography record comes from intake.** The approved page becomes a source with its own
    BookID; no OpenSERP-specific bibliography writer is needed.
  - **DECIDED (owner 2026-11-17): a pending find is held in the queue only and is not written into
    any entity JSON until a human approves it.** The inline writes stop (`web_results[]`,
    `primary_sources[]`, web-page `military_awards[]`, Grok `source_urls` -> `biography_sources[]`).
    On approval the find goes to Phase 0; the entity link comes from the new source's events and the
    origin entity ULID on the queue item.
  - **Stop the `military_awards` misuse (was gap 7).** `_apply_verified_web` (`OE:1247`) files every confirmed page
    about a person (obituary, unit history, forum post) under `military_awards[]`. With the queue it
    is enqueued like every other find; `military_awards` is written only by an award extraction.
  - Queue volume: every confirmed hit becomes a review item. Keep the per-entity caps, and batch the
    Grok verify as decided for `bibliography_resolve`.

#### OpenSERP: keeping bad URLs out and re-checking good ones (owner question 2026-11-17)

What exists (verified in code; no OpenSERP keys are in DynamoDB yet, Phase 3 has not run on this data):
- Per-URL verdict, 90 days: `process_positive_url` (`OE:808`) checks `openserp_url_verdict` first
  (`_url_verdict_cached`, `OE:763`). `REJECT` (Grok said not relevant, or the page could not be
  fetched/summarized) returns at once; a cached summary is reused without a new request.
- Grok relevance answer cache: `_verify_result` (`OE:417`) caches YES/NO under `openserp_verify`.
- Central URL registry (`src/utils/url_registry.py`): `searched` (90-day TTL), `dead` (never re-fetched,
  after repeated DNS failures and no Wayback copy), per-domain politeness; consulted through
  `url_fetch.fetch` in `_summarize_url_page`.
- Domain filters: `config/domain_blacklist.yaml` (§3.5) and human-excluded domains
  (`url_registry.exclude_domain`, `excludedomain#`, set from the URL-review UI).
- Per entity: `openserp_searched_at` skips the whole search for 90 days (`OE:1407`), but
  `_reset_openserp_searched` clears it every run for entities with no result ("was gap 6").
- OpenSERP search responses themselves are not cached; a re-searched entity sends every query again.

Findings:
- **HIGH - a page approved for one entity is attached to other entities without being checked.** Both
  verdicts are keyed on the URL alone, but relevance is per (URL, entity): the registry's own design says
  so (`url_registry.py` header, "the relevance verdict is keyed on (url, entity) so one entity's verdict
  is never reused for another"). A cached summary short-circuits before `_verify_result` (`OE:827-836`), so
  any later entity whose search returns the same URL gets it. People and groups have only a title/name
  check first (`_name_initial_matches`, `_group_name_matches`); places (`OE:2117`) and source sections
  (`OE:1180`) have none.
- **MED - a page rejected for one entity is skipped for every entity for 90 days.** Same cause: `REJECT`
  is URL-global, so a page irrelevant to unit A is never considered for unit B. Fix for both: key the
  relevance verdict on (URL, entity ULID); keep fetch state and the page summary URL-global (reuse the
  page, re-ask only relevance).
- **MED - the two caches disagree.** The `openserp_verify` key truncates the URL to 60 characters
  (`OE:434`, `OE:492`: context[:50] | title[:50] | url[:60]), so different URLs with a common prefix share an
  answer; and it uses `search_cache` TTLs (30 days, `src/utils/search_cache.py:17`) while the URL verdict
  uses 90. Use the full URL hash and one retention constant.
- **MED - the 90-day re-check of a good URL changes nothing.** When the summary expires the page is
  fetched and summarized again (a paid Grok request), but every caller skips a URL already on the record
  (`existing_award_urls`, `existing_src_urls`, `seen`), so the new summary is discarded. The record keeps
  the old summary, gets no last-checked date, and a page that has since died stays linked. A cache hit
  also stamps `fetched_at` with the current time (`OE:835`), so the record claims a fetch that did not
  happen. Owner decision: what a re-check should do (liveness check only, cheap; or re-summarize and
  replace), and record `last_checked_at` / `status` on the entry.
- **FIX (owner 2026-11-17) - key relevance on (URL, entity); keep page state per URL.**
  - Store the Grok relevance verdict under (full-URL hash, entity ULID), in one cache, with one
    retention constant (drop the 60-character URL key and the 30-day `search_cache` TTL for it).
  - Keep fetch state, page checksum and summary per URL (registry + `openserp_url_verdict`), so a page
    is fetched and summarized once and only the relevance question is asked again for a new entity.
  - Reuse `_verify_results_batch` (`OE:532`) for the relevance questions, so the per-entity verdicts
    batch (cost).
  - Stop stamping `fetched_at` on a cache hit; carry the real fetch time from the cached entry.
  - Cache OpenSERP search responses per query for the same window, so a re-searched entity does not
    resend every query.
- **FIX - DECISION (owner 2026-11-17): a good URL is re-checked by checksum, and changed pages are
  updated.** At the re-check date:
  - Fetch the page through `url_fetch.fetch` (registry, politeness, SSRF checks, Wayback fallback) and
    hash the content (reuse `fetch_review._content_sha256`, `src/enrichment/fetch_review.py:464`; one
    helper, DRY).
  - Same checksum: no Grok request; stamp `last_checked_at` and keep the summary.
  - Different checksum: store the new checksum and `content_changed_at`, re-summarize, and update the
    entry on every entity that holds the URL (replace, not append), keeping the previous summary and
    checksum in a `history` list for provenance. Once the URL-review queue is in place, a changed page is
    re-queued for human review instead of being applied directly.
  - Dead or blocked: `url_registry.record_dns_failure` as today; mark the entry `status: dead` with the
    date, and keep the Wayback copy if there is one; never delete the citation silently.
  - Each entry gets `content_sha256`, `last_checked_at`, `status` (`live` | `changed` | `dead`). Normalize
    the HTML before hashing (strip scripts, ads, timestamps) so a page that only rotates its boilerplate
    does not count as changed.
  - OBSERV per run: `checked`, `unchanged`, `changed`, `dead`; an actionable alert only for a
    `changed` page that is cited by a published record.
- **With the URL-review queue decision:** the review status becomes the durable record. A
  human-rejected URL is never re-enqueued (`fetch_review.reject_fetch_find`; `exclude_fetch_find` can
  exclude the whole domain); an approved one is submitted once (global content-hash dedup in
  `submit_affirmed_batch`). The enqueue helper must check the queue for the (URL, entity) pair before
  adding it.

#### Step: link_asset_review (PHASE3_MAP §1 step table, §4.4) (moved here from Group 9)
- **HIGH - every Phase 3 run queues the same links again.** Each enqueue is a new event with a new ULID
  (`fetch_provenance.record_fetch_event`, key `fetchevent#<ulid>`); nothing checks for an existing
  pending, approved or rejected event with the same `url_key`. So each run duplicates the queue, and a
  link a human rejected comes back. Fix (DRY, with the shared enqueue helper): look up `url_key` (+
  entity) before enqueueing; a rejected or submitted link is never re-queued.
- **HIGH - links skip Grok or are queued "when in doubt".** `link_relevancy_whitelist` is `ibiblio.org`
  (`config/domain_blacklist.yaml:87`), so every HyperWar link is queued with no Grok verdict; a missing
  client, an error or an `UNKNOWN` answer also queues (`link_relevancy.py:71-92`, `:111`, `:121`).
  Owner rule (2026-11-17): Grok validates every link before a human sees it (recorded in the footnote
  entry; the fix goes into `should_enqueue_link`).

#### Wikipedia / Grokipedia footnote links (owner question 2026-11-17) (moved here from Track P)
- **Same rule for every URL finder** (OpenSERP, Archive.org, Gutenberg, Grok LLM
  search, map URLs, footnote links): Grok judges relevance first, a positive verdict goes to the
  URL-review queue, a human approves, approval submits to Phase 0. One shared helper does the
  judge-then-enqueue step. **No link is ever shown to a human without a positive Grok verdict.**

#### Cross-cutting: every URL a Phase 3 step finds goes to human review (owner 2026-11-17)

- **HIGH *refactor* - DECISION: the URL-review queue applies to every find that has a URL, not only
  OpenSERP.** That covers OpenSERP pages, Archive.org and Gutenberg copies found by `bibliography_resolve`,
  Grok LLM-search URLs, and map URLs (NARA records and HyperWar volumes are trusted sources: see
  `trusted_sources` under intra_corpus_links). Approval submits the find to
  Phase 0 through `fetch_review.submit_affirmed_batch`. One shared enqueue helper (the OpenSERP entry
  above) is called by every finder. Current behaviour, verified in code:
  - **The queue gets search calls, not finds.** `recorded_fetch` (`src/utils/fetch_provenance.py:185`)
    records each request with `outcome=HIT` for any 2xx status (`:256`), and `_initial_review_status`
    (`:64`) makes that `pending` for `nara`, `archive-org`, `archive-solr`, `gutendex` and `wayback`. The
    `endpoint` is the search API (`catalog.archives.gov/api/v2/records/search`, archive.org
    advancedsearch, gutendex), so a reviewer sees one item per successful query, including queries that
    matched nothing, with no link to the BibliographyID and no Grok relevance verdict.
  - **The finds bypass review.** `bibliography_resolve` writes each verified Archive.org / Gutenberg /
    NARA URL straight into the record's `resource_urls` and `source_metadata.findings`
    (`BR:330-336`, `BR:677-682`); nothing is submitted to Phase 0.
  - Fix: enqueue the found item URL (with BibliographyID or entity ULID, provider, query, Grok verdict),
    stop `recorded_fetch` from enqueueing search calls (provenance only), and keep `resource_urls` for
    approved finds (or mark the pending ones).

#### Older backlog - LOW list
- **[LOW · security-hardening · phase3 review follow-up 2026-11] `fetch_review._find_content_bytes` containment-root
  check** — the read is now 25 MiB-capped (PR #712) and the stored `quarantine_path` is basename-normalized at the write
  side (`source_retrieval.py` uses `Path(urlparse(url).path).name`), so traversal risk is LOW. Defense-in-depth: add a
  `p.resolve()`-under-quarantine-dir assertion in case a future caller stores an unsanitized path into the event.

#### 2.1 OpenSERP redesign (the measured CPU bottleneck — 98.7%) (from section 2)
- **[TODO · owner 2026-11-04 · DESIGN] Web interface to SUBMIT and REVIEW sources - both what
  Phase 3 finds AND new documents / sites (a PDF, a scan, a transcription site, an archive
  collection) that a user or operator brings - the path for expanding the knowledge base WITH
  provenance.** Goal: grow the corpus from Phase 3's discoveries (OpenSERP PDF/EPUB documents,
  Grok-proposed HTML references, NARA/bibliography resolutions) and from directly submitted
  documents and websites, all through ONE review gate, with every accepted item carrying its
  provenance end to end (where found, by what query/resolver and version, who reviewed it and
  when, the decision and reason, source URL + content hash, license/copyright status, and for a
  second copy of an existing work its edition/printing - the newer printing is deferred to,
  especially for people's names). Start from the human ADD / DISCARD(>=90-day) / NEVER gate in
  `docs/current/design/OPENSERP_SCOPE_REDUCTION.md` and the dedup-UI pattern (§1.4); an accepted
  document enters Phase 0 like any upload (scanned PDF -> Chandra; a transcription of a book we
  already hold -> `scripts/crosscheck_pdf_html.py` against it); a submitted SITE needs a
  site-level record too (owner, terms/license, crawl scope, which pages were taken and when).
  Open questions: authentication for
  submitters, static-site hosting vs API Gateway (Phase 2 of the architecture plan), and how
  review decisions are stored (versioned, auditable).

#### 2.2 NARA resolution redesign (correctness) (from section 2)
- **[HIGH · feature] Acquire + process positively-verified source documents (NARA / archive.org /
  OpenSERP).** Download the document, preserve the original under `source/` (existing Glacier-IR
  lifecycle), convert via Phase 0, process via Phase 1→3; the record flips `held_unprocessed` →
  resolved/local. Pluggable fetcher REGISTRY keyed by `search_source`; human-review gate scaled by
  FORMAT+SIZE (NARA page-image sets ≥50 → review; PDFs → review by default; text → auto-process);
  SEPARATE opt-in pass (config-gated); idempotent by naId/item-id; auto-acquire only `public_domain`.
  S3-NATIVE (idempotency = presence of `source/<search_source>/<id>/`, no Dynamo). NARA budget = 10k
  queries/MONTH (bumpable to 150k; downloads quota-free) → persist `digitalObjects` at RESOLVE time so
  acquisition doesn't re-spend a query; add an OBSERV counter.

#### 2.4 URL-sourced enrichment + dedup (from section 2)
- **[HIGH · provenance] URL-sourced enrichment + 90-day URL registry — FINISH wiring.**
  `url_hygiene.py` + `url_registry.py` DONE (SSRF-safe clean_url, DynamoDB `url#{sha256}` 90-day TTL),
  and `enrich_places.py` is PARTIALLY wired (`should_fetch_content` + register). REMAINING: wire the
  other callers (enrich_groups / openserp / grok-cited-wiki) to register/should_examine + require
  Grok-cited URLs + fetch-then-evaluate relevance. OPEN: drop vs flag (`source: grok_unverified`)
  facts with no cited URL.

#### [MED·dedup] NEAR-duplicate content detection (MinHash/LSH) — same text across two domains, not byte-identical (from the end of this file)

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

#### LOW: recorded_fetch redirect-bounce residual (Phase-2 security DiD) (from the end of this file)

recorded_fetch now gates the endpoint host via clean_url (rejects private/metadata/loopback), but a
trusted host that open-redirects could still bounce to an internal address. Flipping
allow_redirects=False globally would break the many callers relying on the implicit True for
legitimate redirects, so deferred: either re-validate each redirect hop's host, or set
allow_redirects=False per-caller where redirects aren't needed.

### Group 4 - Phase 3 wrapper, preflight, download (before step 1)

**Test:** restore, run; barrier wait and stamping logged; preflight retries then stops when Grok is
down (D8).

#### Step: EP run_phase preflight (PHASE3_MAP §1 "ECS wrapper")

Order (`EP:962-1003`, `main` 6dafabcc): NAT lease -> `_ensure_nat_if_needed` (nat-manager, up to 6
tries, abort if never ready) -> `_clear_title_cache_if_reprocess` -> `_load_secrets` (Grok key, NARA key)
-> `_preflight_credit_check` (1-token request; 402/403-credit or 401 aborts) and
`grok_models.preflight` (every configured model listed and served as itself; a missing one aborts) ->
`_preflight_notification_subscriptions` -> `_preflight_runtime_tables` -> `_patch_config` ->
`_start_openserp_if_needed`.

- Not checked at preflight: the NARA key (warning only when missing), OpenSERP (see "was gap 1"),
  Nominatim. These are separate questions with different effects (owner decisions D8b-D8d, top of file): a missing NARA key
  skips NARA for the archive-path records (DECIDED 2026-11-17: leave those records unresolved for the next
  run instead of resolving them through other sources, and alert with the count); an unreachable OpenSERP stamps entities searched ("was gap
  2"; DECIDED 2026-11-17: Phase 3 stops until OpenSERP is available, stamps nothing, alerts, and the
  book stays queued: the task exits, keeps `pending#enrich#<book>`, and the reconcile retries it, with a
  retry cap and an alert at the cap (needs the paginated queue reader first). The same applies when
  OpenSERP fails mid-run (breaker opens): the whole phase stops, nothing is stamped for the entities in
  flight, completed work is uploaded, and the re-run must skip work already done); an unreachable Nominatim sends every place to the paid Grok geocode fallback
  (`places_grok_geocode.cascade_geocoder`, `:792`, tries each geocoder in turn) with no alert. DECIDED
  2026-11-17: skip geocoding for that run (places stay un-geocoded, are not stamped as attempted, and are
  retried next run) with one actionable alert; the Grok fallback is used only for places Nominatim answered
  but could not locate.

- **LOW - "preflight unverified" proceeds even when Grok is unreachable.** Any exception other than the
  ones above is `OBSERV preflight.unverified status=warn` and the phase goes on (`EP:844-852`). Live: Phase 2
  logged it with a connection error to `api.x.ai` on 2026-11-12 and 2026-11-14, right after NAT reported
  ready, so the phase started with no egress. DECIDED (owner 2026-11-17): retry the probe after a short
  wait and stop the phase with an alert if it still fails.
- **LOW - OpenSERP start can hold the task up to 12 minutes before any work.** `_start_openserp_if_needed`
  polls 72 x 10 s for the task IP (`EP:774`) before `_download_inputs`; NAT and the Fargate task are
  billed meanwhile, and OpenSERP is not needed until step 6. Scale it up here and discover the IP just
  before the OpenSERP group.
- **LOW - two copies of the 1-token probe.** `_preflight_credit_check` posts its own request
  (`EP:808`) and `grok_models.preflight` posts the same probe per model (`src/utils/grok_models.py:199`).
  Fold the credit/key checks into `grok_models.preflight` (DRY).
- **MED - `_clear_title_cache_if_reprocess` deletes nothing on ECS, so a reprocess reuses the old Grok
  answers.** Purpose (`EP:546`): on a reprocess (`processing.force_reprocess`, a non-empty
  `processing.reprocess_types`, or `REPROCESS_TITLE` = `BOOK_NAME`) clear this book's cached Grok event
  answers, so a stale or bad cached answer does not fail every re-run. It runs for Phase 2, Phase 3 and
  the OCR cross-check, and deletes the local directory `/app/cache/api/books/<book>/events/`. On ECS the
  answers are not there: `_patch_config` sets `aws.enabled=true`, so `GrokClient` uses
  `DynamoCacheBackend` (`src/grok_client.py:429`), with keys under `books/<book>/events` in
  `dev-wwii-api-cache`; the local directory holds only batch JSONL and rejected answers (`EP:3539`
  comment), and nothing downloads cached answers into it. Effect: the reprocess flag does not refresh the
  event answers it is meant to refresh. Phase 3 also has no book-scoped cache to clear (it sets no
  `current_book`, "was gap 26").
  DECIDED (owner 2026-11-17): a book is normally not reprocessed, and when it is, it is soon after. So drop
  the reprocess purge (remove `_clear_title_cache_if_reprocess`) and give the **Grok book results** -
  the per-book answers under `books/<book>/<type>#...` (events, weather, logistics, casualties,
  supplemental, equipment; `GrokClient.BOOK_CACHE_TYPES`, `src/grok_client.py:499`) - a sliding expiry:
  stamp each entry's last access and delete it 10 days after that (today: `ttl` = created + 90 days,
  `src/utils/cache_backend.py:119`). Refresh `ttl` on a hit at most once a day per key (a hit must not
  become a write each time); preload reads do not count. Other cache rows (global Grok types, `search#`,
  `openserp_url_verdict`, `url#`) keep their current lifetimes. A book re-run after 10 days re-requests
  from Grok (accepted cost); `phase_snapshot.py restore` does not refresh TTLs (owner, D9-3). Rows written
  before the change carry created + 90 days: bring them under the 10-day rule when first touched.
  Value at stake (Phase 2 backup key list, 2026-11-17): `books/USA-E-XChannel/events` holds 53 paid
  event-extraction answers (plus 2,310 other per-book answers: supplemental 745, weather 647, logistics
  636, casualties 282). They let Phase 2 be re-run without paying Grok again; the local directory holds none.

#### Step: EP _download_inputs (PHASE3_MAP §1 "What EP _download_inputs does")

- **LOW - remove the dead DynamoDB shortcut (owner request).** `_materialize_from_dynamo()`
  (`EP:1290-1295`) is a deprecated stub that always returns False (the DynamoDB entity store was
  removed; S3 is the only entity store). Callers: `_download_inputs` (`EP:1313`) and
  `_download_dedup_data` (`EP:2849`) (trace_path). In `_download_inputs` the `config.yaml` read for
  `processing.force_reprocess` (`EP:1311-1313`) exists only to skip the stub, so it goes too. Remove
  the function, both calls and that read; the S3 download is then unconditional. Verified: function
  body is `return False`.
- **LOW - the wait timeout equals the reconcile's own run time.** The last reconcile ran 300.1 s
  (`_run.json` started 22:43:58 UTC, barrier `completed_at` 22:48:58 on 2026-11-16) for one book; the
  default wait is 300 s. A Phase 3 that starts while a reconcile is running (concurrent books) will
  time out and read a half-merged bibliography. The reconcile downloads the whole `output/bibliography/`
  prefix, so its time grows with the corpus while the timeout does not.
- **LOW - the per-book barrier is dead code.** Only the full-corpus `_all` barrier is ever published
  (one caller, `EP:2926`). The wait still polls `manifests/reconcile_complete/<BOOK_NAME>.json`, its
  docstring says "a per-book reconcile publishes the book key", and `_clear_reconcile_barrier`'s
  default key is that book key: nothing writes it.
- **LOW - the barrier body names a book.** The `_all` barrier records `"book": "USA-E-XChannel"`
  (`EP:3093` writes `BOOK_NAME` of the dedup task) although it is the cross-book barrier.
- **LOW - `canonical_count` is never compared.** The barrier records how many canonical files the
  reconcile published (`_publish_reconcile_barrier`, "so it is comparable with Phase 3's download
  coverage"), but `_observe_bibliography_download_coverage` (`EP:1629`) compares the S3 listing with
  the local count and never reads it; it is only logged (`EP:1460-1466`).
- **MED - schema stamping switches the whole pipeline's trigger off for a local-only job.**
  `_stamp_schema_versions` (`EP:3690`, run by every Phase 3 task right after the download) sets the
  trigger Lambda's reserved concurrency to 0 (`EP:3708`) while it rewrites files on the task's local
  disk, and removes the limit in a `finally` (`EP:3756`). Stamping touches no S3 object (the files
  reach S3 only at the later syncs), so the disable protects nothing. While it lasts, every pipeline
  event (uploads, phase-complete messages) is throttled and depends on the sender's retries; if the
  task is killed during stamping (OOM, SIGKILL) the `finally` never runs and the trigger stays off
  with no alert, so the pipeline silently stops starting anything. Fix: drop the disable/re-enable.
  Verified in code; for the current bibliography it rewrites nothing (all 2,035 records are already
  at 2.29).
- **LOW - stamping hides failures and bypasses the write guard.** `_stamp_file` (`EP:3664`) catches
  every exception and returns 0 with no log, so an unreadable record is skipped silently, and it writes
  with `os.replace` instead of `write_json_with_lock`, so a stamped record is not schema-checked.
- **LOW - a short bibliography download is only a log line.** `_observe_bibliography_download_coverage`
  logs a SHORTFALL warning (`EP:1679`, with a good cause analysis) but sends no alert, and a failure of
  the check itself is logged at debug (`EP:1687`).
- ***refactor* - the barrier exists only because bibliography is written as per-task shards that a
  later corpus-wide pass must merge.** Merging at write time (conditional writes on the canonical
  `bib_<hash>.json`) would remove the reconcile pass, the barrier, the run marker and the wait.

#### Carried over from PHASE3_MAP.md §6 (found by the 2026-11-17 documentation review) (moved here from Group 11)
- **MED (was gap 6) - `_reset_openserp_searched` defeats the 90-day gate.** People and equipment with
  no hit are searched again on every Phase 3 task (`EP:3583`). Grok verdicts are cached but the queries
  are not, so each repeat pays the 5 s sleep per query. It also writes with `write_text`, outside
  `write_json_with_lock` and its schema guard.

#### Older backlog - HIGH list
- **[LOW · verify] Phase-3 reconcile barrier fix (run-id marker) is unverified on AWS.**
  Fixed in code (empty `BOOK_NAME` key; freshness by run_id marker `manifests/reconcile_complete/_run.json`). Confirm on the
  next dev E2E: Phase 3 logs `reconcile.barrier_seen via=run_id` (no 300s `barrier_timeout`). Residual gap: a Phase 3
  launched BEFORE the reconcile clears the previous marker+barrier sees a consistent old pair; fully closing it needs the
  orchestrator (SFN/ECS overrides) to pass a run_id to both tasks.
- **[LOW · cost] Phase 3 downloads/uploads `output/images/files/` bytes** now that `images` is a Phase 2/3 entity dir
  (unscoped runs only; scoped runs pull manifested JSON). Consider syncing only `*.json` and fetching bytes by URL.
- **[MED · design] The real "dedup complete" path launches Phase 3 for ALL books with NO `BOOK_NAME`** (`trigger_handler.py`
  DEDUP_COMPLETE_TOPIC). Everything Phase 3 reads from the source documents (in-doc maps, image->person/place links, link review)
  only worked for book-scoped runs until the unscoped branch started downloading `output/content/` (2026-11-07). Consider
  whether the unscoped whole-corpus run should re-run paid Grok steps (image-person, link review) over every book each time.

#### Older backlog - MED list
- **[MED · observability-precision] download.file_missing cries wolf on expected-absent TRACKING files.** On every clean-slate/
  first run, Phase-2 download lists then 404s on index.json / .processed_events.json / manifests/run_scope/<book>.json — all
  TRACKING/index files that legitimately don't exist yet — and each fires OBSERV download.file_missing status=ERROR ->
  Slack. Non-fatal (run proceeds, only the parsed+event files matter) but noisy + indistinguishable from the REAL signal
  the OBSERV was built for: an ENTITY/PARSED file that vanished between list and get (true corpus-shortening data loss).
  FIX (ecs_entrypoint._download_s3_file #15): classify the key — an expected-absent tracking file (basename in
  {index.json, .processed_events.json, not_duplicates.json, not_related.json} or under manifests/run_scope/) -> status=info
  (or debug), NOT error; reserve status=error (loud Slack) for *-parsed.json / *-event.json / entity record keys. Keeps the
  real data-loss alert sharp and stops the clean-slate false alarms.
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

#### [LOW·security-hardening] _download_s3_file has no path-traversal containment (ecs_entrypoint) (from the end of this file)

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

#### [LOW · ops/cost, owner 2026-11-10] Manage the Grok response cache long-term (from the end of this file)

The cache-off validation default (`GrokCacheDisabled='1'`, #648) was reverted to `'0'` on 2026-11-10:
the Cross-Channel Attack run re-paid ~2,700 sync calls + 21 batch event answers with nothing cached,
and its bad chapter 8 events answer could not be inspected afterwards. Caching is now ON; manage it:
- **No poisoned entries.** A cached answer that a later check rejects must be EVICTED, or every
  re-run replays it. The events coverage check (chapter 8, 16% coverage) is the first case: the
  low-coverage answer must be evicted when it is rejected, before the re-ask. Audit the other
  validate-and-resubmit paths (`paragraph_range_invalid`, schema failures) for the same.
- **One write path, one key scheme.** The `--retrieve-only` path (`ecs_entrypoint.py`, batch results
  -> `grok_client._get_cache(...)[id] = ...`) ignores `GROK_CACHE_DISABLED` and never sets
  `current_book`, so its entries land as bare `casualties#<h>` / `weather#<h>` instead of
  `books/<book>/<type>#<h>` (seen 2026-11-10 13:29 from the duplicate retrieve task). Route it
  through the same guarded, book-scoped write as the in-task path.
- **Fresh runs on purpose.** A per-run / per-title "fresh" option (today only `REPROCESS_TITLE`
  clears the local per-title events cache) instead of the stack-wide kill-switch; record in each
  record's provenance whether the answer came from cache.
- **Lifetime and size.** TTL is 90 days (`aws.cache_ttl_days`). DECIDED (owner 2026-11-17): the Grok book
  results (`books/<book>/...`) expire 10 days after last access, sliding; other rows unchanged (see the
  `_clear_title_cache_if_reprocess` entry in the Phase 3 review). `GrokClient` preloads the WHOLE table
  with one Scan per task: watch its size/time as the corpus grows, and scope the preload to the book's
  prefix.
- **Observability.** Cache hit/miss and the Grok spend avoided per run in the run summary / Slack
  completion message, so a silently-off cache is visible.

### Group 5 - step 1, people_groups

**Test:** restore, run; every Grok-filled unit field carries a source.

#### Carried over from PHASE3_MAP.md §6 (found by the 2026-11-17 documentation review) (moved here from Group 11)
- **MED (was gap 16) - Model-memory facts have no citable source.** Group `enrichment_data` carries
  `_source: {type:"grok"}` and no URL; place hierarchy carries no stamp. Under the provenance rule
  these are unsourced.

#### Older backlog - HIGH list
- **[HIGH · extraction/enrichment] Group records have NO `group_name`/`name` field — ALL group enrichment + OpenSERP skipped.**
  Found in the synchronous XChannel-8 E2E (2026-10-28): Phase 2 wrote people_groups records whose ONLY keys are
  `nationality`, `event_mentions`, `GroupID`, `_schema_version`, `_last_updated` — the unit name (e.g. "1st Division")
  survives ONLY in the filename + `event_mentions[].Sub_event_Name`, never as a top-level field. Group enrichment gates on
  `data.get("group_name")` → empty → `OBSERV groups.enrich_skip_no_name` for all 6 groups → `groups.summary needed=0
  enriched=0 skipped=6` → OpenSERP never searches groups (`openserp_groups enriched=0`). FIX: Phase 2 must persist the
  group name (populate `group_name`/`name` from the extracted unit), OR enrichment must derive it from the filename/mention.
  Blocks all group web-enrichment. (Places were fine — satisfied by geocoder, `needed=0`. Link-review empty is correct:
  the source HTML has 0 links/0 footnotes.)

### Group 6 - step 2, places hierarchy, link_parent_places, geocode

Owner: dated containment (D2), create missing region/country (D3), skip geocoding when Nominatim is down
(D8d). Geocoding must run after every place-creating step.
**Test:** restore, run; parents only to coarser types; counts of unresolved / ambiguous / created.

#### Step: places hierarchy and link_parent_places (PHASE3_MAP §4.2)

How it works (`main` 2ddeffcc):
- `enrich_all_places` (`src/extraction/enrich_places.py:576`): for each place without both `continent`
  and `country` (`_needs_enrichment`, `:447`), one Grok request from model memory (`PROMPT`, `:62`:
  continent, WWII-era country, region, historical names). `_merge_hierarchy` (`:406`) only fills empty
  fields. A transient error is left unstamped and retried; a clean miss is stamped `not_found`.
- `link_parent_place_ids` (`src/extraction/places.py:611`): builds a lowercase name -> PlaceID index
  from every place's `current_name` and `aliases`, then for each place without
  `hierarchy.parent_place_id` takes the first of `region`, `country`, `continent` whose name is in the
  index (`_find_parent_id`, `:598`) and writes it.
- Live data after Phase 2 (`output/places/`, 994 records, synced to `tmp/phase3-review/imgcheck/`):
  `hierarchy.continent` on 154, `country` on 41, `region` on 0, `parent_place_id` on 0. Of the 31
  countries named, 10 have a place record.

Findings (nothing enforces the hierarchy beyond a name match):
- **HIGH - a parent is chosen by name alone.** No geography-type check (a town can become the parent of
  a region if it shares the region's name: Luxembourg city vs country, Georgia), no disambiguation, and
  the index keeps the last record for a duplicated name or alias (`places.py:589-592`). No cycle check
  beyond self. Fix: the parent must be a coarser `geography_type` (village/town/city < region < country
  < continent), ambiguous names go to review, and a cycle is refused.
- **MED - a missing level is skipped silently.** With no region record the town is linked straight to
  its country, and with no country record to its continent; most parents do not exist as records (10
  of 31 countries). DECIDED (owner 2026-11-17): create the missing region/country through
  `places._find_or_create_place` (as `image_place_link` already does, DRY), and count what was created.
- **MED - a parent is never corrected.** `if hierarchy.get("parent_place_id"): continue`
  (`places.py:634`): once set, a better parent (a region record created later) is never used. Re-derive
  each run, and record why it changed.
- **MED - two representations of the same edge.** The schema has `containedInPlace` /
  `containsPlace` with PlaceIDs (`src/schemas/places_output.py:111`) and no code writes them; the
  link goes into the untyped `hierarchy` object (`"hierarchy": {"type": ["array","object","null"]}`,
  `:100`), so `parent_place_id` is not validated as a ULID (fake ULIDs happened before:
  `scripts/archive/fix_fake_place_ulids.py`). Pick one (schema.org `containedInPlace`), give it a
  schema, and write the inverse `containsPlace` (DRY: one writer).
- **MED - the hierarchy has no source.** Grok answers from model memory with no citation, and
  `_merge_hierarchy` does not record which source filled each field (Phase 2 Wikipedia categories or
  Phase 3 Grok). Same class as "was gap 16" (unit facts). Record the source and date per field.
- **MED - one country for the whole war.** The prompt asks for "the WWII-era country"; borders changed
  during the war (Alsace, annexed 1940; Sudetenland 1938; the Baltic states). A single value cannot hold
  this. The map boundary model is not needed (owner, 2026-11-17); DECIDED (owner
  2026-11-17): dated containment (`containedInPlace` with `valid_from` / `valid_to`), done with the
  `containedInPlace` item above in one schema bump.
- **MED - places created after step 2 are not geocoded until the next run.** Geocoding runs at step 2
  (`_enrich_places_and_geocode`), but places are also created later: `image_place_link` (step 6,
  `_resolve_or_create_place`) today, and the map-vision entity writes and the missing region/country
  creation (owner decisions D1/D3) after the refactor. Run geocoding after every place-creating step, or
  geocode the places created in this run at the end.
- **LOW - region is asked only once.** `_needs_enrichment` stops at continent + country, so a place
  whose first answer lacked a region is never asked again.
- **LOW - the hierarchy-order sort does nothing.** The name index is built before the loop, so
  processing countries before towns (`places.py:623-627`) changes nothing.
- **LOW - observability.** One `OBSERV places.parent_link linked=<n>`; no count of places with a
  hierarchy but no parent found, ambiguous names, or skipped levels.

#### 2.3 People / places enrichment (from section 2)
- **[HIGH · geocoding] Phase-3 geocode MISS-RECOVERY.** On not-found: (a) native-language toponym
  retry (Farm→Ferme/Hof, Castle→Château/Schloss + Grok translate) → re-query Nominatim; (b) OpenSERP
  location-hint rescue — extract EXISTENCE+LOCATION from snippets (do NOT WWII-relevance-gate); (b2)
  EXIF-GPS from a discovered photo (guard: social media strips EXIF; sanity-check vs country); (c)
  persist the native name + region back with provenance. Bounded (~3% hard-miss, idempotent).

### Group 7 - steps 3 and 4, bibliography and bibliography_resolve

Owner: no NARA key -> leave archive-path records for the next run (D8b); NARA not-online data on the
bibliography JSON; Archive.org / Gutenberg finds through G3.
**Test:** restore, run; non-citations gone from S3; finds in the queue; NARA online copies trusted.

#### Step: EP _download_inputs (PHASE3_MAP §1 "What EP _download_inputs does") (moved here from Group 4)
- **MED - records the merge folds into another stay in S3 as duplicates.** The reconcile's near-title
  pass merges whole canonical records (`bib_<a>.json` into `bib_<b>.json`) and deletes the merged-away
  file locally (`_persist_merged_bib`, `src/extraction/bibliography.py:1039`, unlinks every file of the
  group except the one kept, line 1057). The S3 cleanup `_delete_orphaned_bibliography_shards` (`EP:3119`) only deletes names
  containing `__` (shards, `EP:3131`), so a merged-away canonical record is never removed from S3. Found
  2026-11-17: S3 holds 2,035 records; the 2026-11-16 reconcile kept 2,029 (barrier `canonical_count`
  2,030 includes `review_queue.json`); re-running `reconcile_bibliography_dir` offline on a copy of the
  S3 set folds 8 more, e.g. "V Corps G-3 Journal", "Seventh Army, KTB Anlagen 1.I.-30.VI.44", "AEAf
  Overall Air Plan, 15 Apr 44" (list: `tmp/phase3-review/bib-reconcile-dryrun` vs
  `tmp/phase3-review/bibliography`, not committed). Effect: Phase 3 resolves each duplicate separately
  (paid NARA/OpenSERP/Grok lookups) and the site would show a citation twice, one copy without the
  merged mentions. Fix: delete every S3 key in `output/bibliography/` whose basename the reconciled
  local set no longer has (shards and merged-away records), and repair the 8 now.

#### Step: bibliography_resolve (PHASE3_MAP §4.3)

`BR` = `src/enrichment/bibliography_resolver.py`.

- **MED - a NARA catalog match is recorded as "online" even when nothing is digitized.**
  `_verify_nara_hits` (`BR:1772`) returns the catalog record page `catalog.archives.gov/id/<naId>`
  for the first Grok-verified hit (`BR:1826`) whether or not NARA holds scans; the scan count
  (`_nara_digital_object_count`) is only logged (`OBSERV nara.match online=0|1`). `_apply_resolved_url`
  (`BR:325`) then sets `availability: "online"` and `search_status: "resolved"`. A record that NARA
  only describes (paper at College Park) thus reads as available online. Fix: store the scan count /
  an `online` flag on the finding, and set `availability: online` only when scans exist (else
  `archive` + `reference_identified` with the catalog link as a finding aid).
- **LOW - FIX (owner) - one physical address for every record group, and it overwrites a better one.** `_identify_record_group` (`BR:586`) writes
  "National Archives and Records Administration (NARA), College Park, MD 20740, USA" for any record
  group found. Some groups are held at other NARA buildings (believed, e.g., RG 45 at the National
  Archives Building in Washington; per-group holdings not verified). Worse, when no online copy is
  found it OVERWRITES a better address: `_enrich_from_biblio_note` first sets
  `archive_physical_address` from the source volume's own Bibliographical Note (the authoritative
  holding, `BR:261`), then `_apply_result` applies the record-group result with `entry.update(extra)`
  (`BR:311`), replacing it with the generic College Park text. Fix: a holding location per record
  group in the lookup table, never overwrite a holding taken from the note, and leave the address
  empty when unknown.
- **LOW - owner - search order.** Archive path and generic path (`_search_online_sources`, `BR:606`) run
  NARA, OpenSERP (`BR:630`), Archive.org, Gutenberg, then the Grok LLM search only if nothing was found.
  Wanted (owner, 2026-11-17): record group, NARA, Archive.org, Gutenberg, OpenSERP, Grok. OpenSERP keeps
  running even when an earlier source found a copy: a second location is wanted (no short-circuit).
  All four keep every verified finding; the order decides which one is the primary URL.
- **LOW - findings: several locations fit, but their kind is not recorded or checked.** Checked
  2026-11-17: `resource_urls` is a list of URL strings and `source_metadata.findings` a list of
  `{url, source}` (`_aggregate_findings`, `BR:676`), so any number of locations, several of one source
  type included, fit the schema (`src/schemas/bibliography_output.py`). But `source_metadata` is
  free-form (`additionalProperties: true`), so a finding's shape is not validated, and nothing says what
  a URL is: a digitized copy, a catalog/finding-aid record (NARA without scans), or a bookseller listing.
  The resolver also keeps only the first verified hit of NARA and of OpenSERP (Archive.org, Gutenberg:
  up to 3). Fix: a schema-defined `findings[]` item `{url, source, kind, digital_objects, verified_by}`.
- **MED - verification strength differs by source.** NARA hits are Grok-checked on the catalog title
  and series context (`_verify_nara_match`); Archive.org, Gutenberg and OpenSERP hits on the archive
  and generic paths are Grok-checked on the result title and URL only (`_verify_openserp_match`,
  `BR:1077`, via `_collect_verified_candidates`, `BR:864`), and accepted UNVERIFIED when there is no
  Grok client. Only the Grok LLM search and the book and article paths fetch the page and check its content
  (`_verify_url_content`). A title-only check can accept a page that merely names the document (a
  catalogue entry, a bookseller listing). Fix: content-verify every positive finding, as the LLM path
  does.
- **MED - owner - verify each citation's candidates in one Grok request.** Each candidate costs its own
  Grok call today (NARA, Archive.org, Gutenberg, OpenSERP, each up to 3-5). Batch all candidates of one
  citation into one verify prompt (one answer per candidate). `_verify_results_batch`
  (`src/enrichment/openserp_enrichment.py`) already does this for OpenSERP enrichment but has no caller.
- **MED - book path: Gutenberg and OpenSERP never return anything.** `_resolve_book_search`
  (`BR:1833`) calls `search_gutenberg_openserp` and `search_openserp`, which POST to `/search`, a route
  the OpenSERP server does not have (`openserp/core/server.go:207-235`; was gap 12; errors are swallowed
  at debug level). Books are in practice searched on
  Archive.org, then the Grok LLM search. (Where they do return, each hit is content-verified by
  `_verify_match` -> `_verify_url_content`.) Fix with the batch-verify entry above: route books
  through the same multi-source search as the archive path.
- **LOW - owner - article path: add OpenSERP.** `_resolve_article_search` (`BR:1872`) searches
  Archive.org only; many papers are on `.mil` and `.gov` / education sites that OpenSERP would find.
- **DONE (2026-11-20, owner: "Amazon is to be added to the pipeline like archive.org and
  gutenberg; no expectation of a download") Amazon as a bibliography source.** It was in this
  list only as the LOW item below (a listing stored as an online copy) and the two FEATURE items
  (affiliate / BookFinder links); the search itself was never built. Now: books (and untyped
  published works, not archival records) get an Amazon listing search through OpenSERP
  (`site:amazon.com`, template `search_queries/bibliography.yaml` `amazon`; Amazon's pages are
  never fetched), gated on `supplemental_material.search_amazon` (default on) + `use_openserp`;
  candidates must be https product pages on exactly amazon.com / www.amazon.com (no userinfo,
  port, backslash or encoded characters), stored as the canonical `https://www.amazon.com/dp/<ASIN>`
  (a third party's `tag=` is never kept); the title/author put in the query lose quotes and
  operators; the same token-gate + Grok verify as the other sources (no Grok client: no listing);
  the finding is `source_metadata.bookseller` (+ a `kind: bookseller` finding), never in
  `resource_urls`, no change to `search_status`; also for books that already have a copy; the
  search is asked once per window, an empty answer included (`openserp_cache_days`, 14), and a
  found listing is never searched for again, and a search that found none is stamped
  (`source_metadata.bookseller_searched_at`, not stamped during an outage) and not repeated within
  `openserp_cache_days`, so resolved books are not rewritten every run. Schema: bibliography 2.30 models
  `source_metadata.bookseller` (url pattern `^https://www\.amazon\.com/dp/[A-Z0-9]{10}$`),
  `findings[].kind` and `bookseller_searched_at` (additive; versions are soft-coded in
  `ENTITY_SCHEMA_VERSIONS`, the pin, history snapshot and docs are the other steps). Owner
  2026-11-20: the affiliate link, BookFinder link and Open Library ISBN/edition are future
  features, not planned work. Was: LOW - a bookseller listing can be stored as an "online copy", and no book data is taken from it.
  Nothing in the resolver reads Amazon (or any bookseller) pages for ISBN, publisher or edition, and an
  Amazon listing that passes the title-level Grok check is stored like a digitized copy. Owner
  (2026-11-17): Amazon is wanted (affiliate links, below), so `amazon.com` was REMOVED from
  `config/domain_blacklist.yaml` (that list drops links in link review, `src/extraction/link_relevancy.py`,
  and blocks downloads in `src/enrichment/source_retrieval.py`; the bibliography resolver never read
  it). Fix: record a bookseller listing as a `kind: bookseller` finding, never as an online copy, and
  take ISBN/edition from an open source (Open Library's API is free; Amazon's terms forbid scraping
  its pages). `_enrich_isbn` (`src/extraction/supplemental_advanced.py:282`) exists but `extract_isbn` is off by default (on in the
  `review-all-data` and `performance-optimized` profiles).
- **FEATURE - owner - an Amazon Associates (affiliate) link for books.** Construct the affiliate link
  (ISBN/ASIN + the associate tag, kept in config, not code) for a book citation, for the site.
- **FEATURE - owner - a BookFinder link for books.** Store a constructed
  `https://www.bookfinder.com/` search link (title/author or ISBN) on book citations, so a reader can find
  a copy to buy or borrow.
- **LOW - docstring contradicts the code.** `_identify_record_group` says "Ask Grok to identify NARA
  Record Group"; it calls `_identify_nara_record`, a deterministic table lookup (`BR:1398-1432`) that
  never uses Grok.

#### Cross-cutting: every URL a Phase 3 step finds goes to human review (owner 2026-11-17) (moved here from Group 3)
- **DECISION: a NARA record with no online copy is not queued; its data is appended to the
  bibliography JSON.** Record group, series, holding, physical address and NARA identifiers go on the
  record (`archive_physical_address` etc., `BR:261`), with the source of each value. This is the current
  behaviour apart from the overwrite fix already recorded ("one physical address for every record
  group"); confirm nothing is enqueued for it once the queue change above lands.
- **FEATURE - owner - a UI for sorting out NARA records with no online copy. Requirements (owner
  2026-11-17): two use cases.**
  1. *More human-driven online searches.* A list of bibliography records with a NARA reference but no
     online copy, filterable by record group, series, book and search status. Each row shows the
     verbatim citation, the deterministic record group and series, links that open the NARA catalog
     already filtered to that group, and what was already tried (sources, queries, dates, verdicts) so a
     person does not repeat it. The person can record a found URL (approved directly - no Grok checks, no URL-review queue -
     recorded as human-found with who and when, then submitted to Phase 0; owner 2026-11-17), a corrected record group or
     series, or "not online"; each edit is written to the bibliography JSON with who and when.
  2. *A research visit to NARA in Maryland (College Park, Archives II).* A pull list of the records still
     needed: grouped and sorted by record group, series, entry, container (box) and file unit, with the
     citations each one serves and how many records cite it (to prioritise). Export for the visit (CSV
     and a printable list). Status per item: needed -> requested -> pulled -> copied/scanned -> ingested,
     or unobtainable, with notes. Scans come back in through the normal intake (owner 2026-11-17): uploaded to
     `contentrepository/`, so Phase 0 (AV, OCR) and Phases 1-3 extract them like any source, with page-level
     citations. The UI uploads against a pull-list item and names the file, so the slug (filename stem,
     `src/ingestion/book_slug.py`; immutable) is set before upload, e.g. `NARA-RG407-<entry>-<box>-<folder>`.
     The source is bound to the BibliographyID it fulfils, with provenance (NARA, RG/series/entry/box/file
     unit, scan date, who), `archive_reference_number`, and public-domain license for US government
     records.
  Data this needs on each record (some exists, some is new): record group, series, entry, container, file
  unit, NARA identifier (NAID) when known, physical holding (Archives II), and a search history.
  Not yet decided: users (operator only, or contributors), and whether it is part of the URL-review /
  dedup UIs or a separate app.

#### Carried over from PHASE3_MAP.md §6 (found by the 2026-11-17 documentation review) (moved here from Group 11)
- **MED (was gap 20) - Bibliography verifier is fail-open in one place.** `_verify_match` accepts on a
  Grok error or without a client; every other verifier fails closed.

#### Older backlog - MED list
- **[MED · provenance/copyright] Copyright date rules have off-by-one and stale-cutoff errors (found 2026-11-06).**
  Not verified by a lawyer; these are code-vs-stated-rule mismatches against `docs/current/core/copyright/`.
    - `src/extraction/copyright_calculator.py`: for a US work with an unknown death date, only `pub_year < 1928` is
      treated as public domain. That hardcoded cutoff has gone stale: works from 1928–1930 have since entered the public
      domain, so they are now reported as `unknown`. Derive the cutoff from the current year instead.
    - Both modules treat life+70 as expired when `current_year >= death_year + 70`. The term runs to the END of
      that year, so a work enters the public domain on Jan 1 of `death_year + 71`. The code over-claims public
      domain by one year, which is the harmful direction for a citation-ready dataset.
    - `supplemental_advanced._determine_usa_copyright` has the same off-by-one for the 95-year term (`pub_year + 95`).
    - Two implementations of one rule: consolidate into `copyright_calculator` and pin it with year-boundary tests.

#### 2.2 NARA resolution redesign (correctness) (from section 2)
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

- **[HIGH] alt_title expansion not running + duplicated + not schema-enforced.** (1) The expansion
  rule (#421/#425) drives the Phase-2 supplemental BATCH (skipped/cached) and isn't clearly wired to
  UPDATE deterministic records → add alt_title expansion as a direct per-record Grok call in Phase-3
  `_enrich_bib_file` (idempotent). (2) `_build_bib_entry` keeps alt_title at top-level AND inside
  `citation` → can drift; pick a canonical home. (3) `bibliography_output.py` allows both but doesn't
  enforce they match → add a consistency check.

- **[HIGH · schema] A document must support MULTIPLE DISTINCT SOURCE ENTRIES.** Replace
  `resource_urls: [string]` with `sources: [object]`, each with url / source (nara|nara_physical|
  archive_org|hathitrust|...) / format / license+copyright_status (per-copy) / access /
  archive_reference_number / download_status. `source=nara` ONLY from a REAL catalog API hit (never a
  Grok "RG…"). Precedence: local holding → online repos → physical NARA visit LAST (`nara_physical`,
  `access=physical_only`, RG/series/box/folder — a resolved-with-location outcome). Additive + version
  bump + fingerprint re-pin.

### Group 8 - step 5, document media (in_doc_map_links, image_map_vision)

Owner: maps store appearance links only (D1).
**Test:** restore, run; Map records carry `depicted`; new places/units link to the MapID; a second run
sends no map to Grok again.

#### Step: in_doc_map_links, image_map_vision (PHASE3_MAP §4.4)

Code at `main` 17fe5296. Map vision (`prompts/map_vision.yaml`) returns a `depicted` block: `units[]`,
`places[]`, `movements[]`, `front_line_or_sector`, `date_or_window`, `operation`, `description`,
`legibility_confidence`. The only consumer is `map_source_adapter.recycle_map_as_source`
(`src/extraction/map_source_adapter.py:263`), shared by the in-doc path and the OpenSERP maps path
(`openserp_maps.py:607`). It flattens `depicted` to prose and writes `contentrepository/map-<id>/` (a
meta.yaml + content.md pair) so the map re-enters Phase 1 -> 2 -> 3 as a new "book".

- **HIGH - the map-as-source files never reach S3, so all extracted map data is lost.** The write goes to
  `/app/contentrepository` -> `WORKDIR/content` on the container. Neither the periodic sync (`EP:401`,
  `output/` and `artifacts/` only) nor the Phase 3 final sync (`EP:3470`, entity dirs + artifacts) uploads
  that directory, so the files vanish when the task exits and no Phase 1 run is ever triggered. The Grok
  vision call is still paid. Verified in code; not observed live (Phase 3 has not run since the
  recycle was enabled).
- **DONE (1.6, 2026-11-18: `fetch_review.claimed_content` / `release_content_hash`, used by fetch-review submits, intra-corpus queueing and in-doc maps; a claim is released only by its own writer) - HIGH - the dedup claim is taken before the write, so a lost map is never retried.** Note: an in-doc map's recycle still writes only to the container's contentrepository/ (the HIGH above), so its claim is kept for content that is then lost at task exit - fixed by the owner's 2026-11-17 decision to stop recycling maps (Group 8). Follow-ups: claims written before 1.6 carry no `source_id` and can never be released (a one-off cleanup of `contenthash#` items whose content is not in S3); intra-corpus digests have no `sha256:` prefix, so its dedup does not meet fetch-review's; a hard kill between claim and write still leaves the claim, so later runs see the content as a duplicate (an owner-keyed re-claim would fix it); a claim is released only by the attempt that won it. `_content_is_new`
  (`map_link_source.py:219`, `:238`) puts `contenthash#<sha of URL>` in DynamoDB before
  `_recycle_as_source` runs. With the files lost (above), or a failed write, every later run skips the map
  as a duplicate. Fix with the upload: claim after the source is durably in S3, or release the claim on
  failure.
- **HIGH *refactor* - DECISION (owner 2026-11-17): write map vision results straight into the entity
  JSON with the existing entity writers; stop recycling maps as prose sources.** Today `depicted` is
  turned into sentences ("Units depicted on the map: ...") and Phase 2 re-extracts them with Grok: a
  second paid request per map, and the unit-at-place-at-time link is lost. Nothing is stored on the Map
  record, and nothing links the derived entities back to the `MapID` (no Phase 1/2 code reads the
  meta's `source_type: map`, grep). The new flow, reusing what Phase 2 already uses:
  - **Map**: store `depicted` on the Map record (with `vision` provenance: model, RequestID,
    `map_vision_checked_at`) through `map_common.find_or_create_map_by_url` (`map_common.py:127`).
  - **Places** (towns, rivers, hills, roads): `places._find_or_create_place` (`places.py:526`) and
    `places._add_event_mention` (`places.py:686`). Geocoding then runs on them in the normal Phase 3
    step.
  - **Dates** (`date_or_window`): `dates._find_or_create_date` (`dates.py:233`) and
    `dates._add_event_mention` (`dates.py:313`).
  - **PeopleGroups** (units): `people_groups._save_group` (`people_groups.py:294`), which merges
    into an existing group or creates a new one under the index lock.
  - **The map as a citation**: `bibliography.store_bibliography_entry` (`bibliography.py:774`).
  - **Mentions**: key them on a deterministic EventID for the map, `event_id("map-<id>", 1)`, so each
    entity is linked to the map and its `verbatim_reference` is the vision `description`.
  These writers are private and take Phase 2 shapes (a mention dict plus an index), so the refactor
  makes each one a public find-or-create entry point. Phase 2 and the map path then call the same
  function: no second writer. This removes `recycle_map_as_source`'s `contentrepository/map-<id>/` write,
  and with it the two HIGH entries above (files never uploaded, dedup claim never released).
- **DECIDED (owner 2026-11-17): no structured map-observation model.** "This unit, place and date appear
  on this map" (the entity writers above) is sufficient; the rest of `depicted` stays on the Map record as
  vision text with provenance. Original finding: The writers above cover
  "this unit, this place, this date appear on this map". The vision answer has no field for what a map
  adds: unit X at place Y at time T, unit sector boundaries, national borders and phase lines.
  `front_line_or_sector` is one free-text string, `movements[]` are strings, and there is no geometry.
  Decide on a placement record (PeopleGroupID, PlaceID, datetime, boundary/geometry, MapID) and extend
  `prompts/map_vision.yaml` to return it.
- **MED - provenance of the derived data is weak.** The recycled meta has `license: Unknown`, empty
  author and copyright, and `source_url` = the image URL. The originating book, chapter and
  `SourceSectionID` that linked the map are not recorded, so events from `map-<id>` cannot cite where the
  map was found. Per the provenance rule, record the parent source on the meta and the Map.

#### Carried over from PHASE3_MAP.md §6 (found by the 2026-11-17 documentation review) (moved here from Group 11)
- **LOW (from PHASE3_ENRICHMENT.md) - `map_features` has no Phase 3 step.** The map-interior
  extraction and reverse registration (`src/extraction/map_registration.py`) are prototype-level and
  not wired into `phase3_enrich_data.py`.

#### Validate maps are NOT continually resubmitted to Grok in Phase 3 (MED)

With the tiered maps classifier adding a Grok-vision backstop ("is this an image of a map?") for
questionable images, we MUST ensure Phase 3 (enrichment/geocode/re-runs) does NOT re-submit the same
map image to Grok on every run. CHECK: (1) the vision verdict is PERSISTED on the record (e.g.
is_map / map_vision_checked_at) and the backstop SKIPS already-classified images; (2) the classifier
only runs on NEW/unclassified images (incremental, like enrichment_checked_at gating); (3) a Phase-3
re-run over an already-processed book issues ZERO new maps-vision calls (assert via OBSERV count).
A resubmission loop would silently burn cost + rate limit on every re-run. (Raised by owner 2026-11-02;
tie to the tiered-classifier work + the deterministic-first/Grok-backstop design.)

### Group 9 - step 6, document links (link_asset_review + intra_corpus_links merged)

Owner: HyperWar volumes (html + pdf) are trusted: queued whole, once, under the volume slug.
**Test:** restore, run; edges persisted; one queued volume; external links in the queue with their section.

#### Step: link_asset_review (PHASE3_MAP §1 step table, §4.4)

What it is (M4, PR #590): `_review_external_links` (`phase3_enrich_data.py:495`) loads every parsed
document and calls `link_assets.enqueue_doc_links` (`src/extraction/link_assets.py:119`) on its text.
That extracts every markdown and HTML link (`extract_link_assets`; images excluded, relative links
resolved against the document's `_source_url`, media type from the extension, no network), skips
same-page `#anchor` links, drops blacklisted hosts, and asks `link_relevancy.should_enqueue_link`
whether to queue it. A kept link becomes a `fetch_provenance` event with `provider=link-asset`,
`review_status=pending` (`_enqueue_one`, `:187`), which the URL-review UI lists; nothing is fetched until a
human approves, and approval submits it to Phase 0. Gated by `links.review_external` (default on) and,
today, by `use_openserp` ("was gap 3").

Findings:
- **MED - the provenance points at the wrong section.** `_book_section_index` maps each book to ONE
  SourceSectionID, the first one found (`image_place_link._build_book_section_index`, `setdefault`,
  `:392`), so every link in the book is attributed to that section, not to the paragraph it came from.
  Record the section (and paragraph) the link was extracted from.
- **MED - Grok gets almost no context.** `context=book` (`link_assets.py:154`): the relevance question
  sees the link text, the URL and the book slug, not the sentence or footnote around the link. Pass the
  surrounding text (the pre-validate check in the footnote entry).
- **MED - overlap with `intra_corpus_links`.** Links to other ibiblio volumes are also handled by
  `intra_corpus_links` (`src/extraction/intra_corpus.py:102`, queued as another batch), so the same
  link can land in both queues. Owner decision: in-corpus links go only to `intra_corpus_links`.

#### Step: intra_corpus_links (PHASE3_MAP §1 step table)

What it is (M8): `_process_intra_corpus_links` (`phase3_enrich_data.py:551`) loads every parsed
document and calls `intra_corpus.process_doc_links` (`src/extraction/intra_corpus.py:102`). It
extracts every link with the same `extract_link_assets` and classifies each against the document's
`_source_url` with the HyperWar volume recognizer (`hyperwar_biblio._parse_volume`):
`same_volume_anchor` (a `#page329` or another page of the same volume), `in_corpus_doc` (a different
HyperWar volume, e.g. "Utah Beach to Cherbourg"), or `external` (ignored). For each non-external link it
builds a corpus-graph edge `{from_SourceSectionID, to_url, to_volume, kind}`. For an `in_corpus_doc`
whose volume is not yet in the corpus (`_corpus_volumes_present`, from the source_section records) and
not queued this run, it fetches the linked page (`url_hygiene.fetch_text_safe`) and uploads it to
`contentrepository/_system/<volume>/intracorpus-<volume>-<sha12>.html` (`_upload_batch_item`, `:192`),
which starts Phase 0 like an uploaded book. Gated by `links.intra_corpus` (default on) and `use_openserp`
("was gap 3").

Findings:
- **HIGH - it ingests one page, not the volume, under a hash slug.** The linked page (often one chapter)
  becomes its own book, slug `intracorpus-<volume>-<sha12>` (`book_slug` = filename stem). The slug is
  immutable, so a later proper ingestion of the whole volume under its real name duplicates it, and
  each other cited page of the same volume becomes another one-page book. `_should_queue` dedups by
  volume within a run only (`:168`). Queue the volume (its table of contents) once, under its volume
  slug.
- **MED - the corpus-graph edges are thrown away.** `process_doc_links` returns them and the caller only
  counts them (`phase3_enrich_data.py:579`); nothing writes them. Persist them on the source section
  (or a link record), with the citing paragraph.
- **MED - the dedup claim is taken before the upload.** `_register_content_hash_if_new` runs before
  `put_object` (`:212`, `:222`); a failed upload is never retried (same flaw as the map-as-source entry).
- **LOW - the fetch bypasses `url_fetch`** (registry, politeness, Wayback). DRY: use `url_fetch.fetch`.
- Same wrong-section attribution as `link_asset_review` (one SourceSectionID per book).

#### Consolidate link_asset_review and intra_corpus_links into one document-links step (owner question 2026-11-17)

- **MED *refactor* - they can and should be one step.** Both load the same parsed documents
  (`_load_parsed_docs`), build the same link text (`_doc_link_content`, paragraphs + footnotes), run the
  same extractor (`extract_link_assets`), use the same book -> SourceSectionID index, and run back to back
  in `_run_openserp_enrichment`. They differ only in what they do per link, and they overlap: an ibiblio
  link to another HyperWar volume is queued for human review by `link_asset_review` (ibiblio is on
  `link_relevancy_whitelist`) and ingested directly by `intra_corpus_links`. Proposed single step:
  1. Extract each document's links once, recording the section and paragraph each came from.
  2. Classify each link once: `same_volume_anchor` | `in_corpus_doc` | `external`, and same domain vs
     external (the `links.follow` decision), with `link_scope` recorded.
  3. `same_volume_anchor`: persist the corpus-graph edge; no review, no fetch.
  4. `in_corpus_doc` (HyperWar volume, HTML or PDF): persist the edge; if the volume is not in the
     corpus, queue it for ingestion directly (trusted, owner decision above; no URL review), once,
     under its volume slug. Its content is then classified by Grok in Phases 1-3 like any book.
  5. `external`: the shared path: Grok pre-validate (URL + link text + surrounding sentence), fetch
     through `url_fetch.fetch`, Grok validate (batched, under-50 rule, cache first), positive -> the
     shared enqueue helper (dedup by `url_key` + entity; a rejected link is never re-queued) -> human
     approval -> Phase 0.
  The same shared path also serves OpenSERP page links and Wikipedia/Grokipedia footnote links, so the
  three link sources stop having three implementations. One config block (`links.review_external`,
  `links.intra_corpus`, `links.follow`), one OBSERV summary per run, independent of `use_openserp`.

### Group 10 - step 6, photos (image_person_link, image_place_link)

**Test:** restore, run; each linked Image record names its PersonID / PlaceID.

#### Step: photos from Phase 2 (image_map_vision, image_person_link, image_place_link)

How Phase 3 handles document photos today (verified in code at `main` 96703bca):
- `image_map_vision` (always runs): images Phase 2 marked `map_check=pending_vision` get a Grok vision
  map-or-not verdict; a map becomes a Map entity (PHASE3_MAP §4.4).
- `image_person_link` (`src/extraction/image_person_link.py`): parses names from each parsed image's
  caption, resolves them to PersonIDs through the people index (full name, then unambiguous surname) and
  appends `{url, title=caption, source="document:<book>"}` to that person's `images[]`.
- `image_place_link` (`src/extraction/image_place_link.py`): classifies terrain/aerial photos from the
  caption (keywords, then a Grok text question, `:71`), resolves or creates the Place through
  `places._find_or_create_place` (`:154`), appends the photo to `places.images[]` and sets
  `contentLocation {PlaceID, name}` on the Image record (`:227`).
- Both linkers read the parsed documents, not `output/images/`, and both run only when `use_openserp` is
  true (the "was gap 3" entry below).

Findings:
- **MED - a person photo is linked on one side only.** `image_person_link` writes `people.images[]` but
  never updates the Image record in `output/images/`, so an Image has no link to the people it shows,
  and the person entry carries no ImageID (URL only). `image_place_link` writes both sides
  (`contentLocation`). Fix (DRY): one shared "link image to entity" helper that writes the entity's
  `images[]` entry (with ImageID) and the Image record's field (`about` / `contentLocation`), used by
  both linkers.
- **LOW - the people-photo vision gate never looks at the photo.** `_is_people_photo`
  (`image_person_link.py:152`) calls `ImageCaptioner.caption(None, ...)`; with no bytes `caption`
  returns `classification="unknown"`, `method="context-only"` (`image_captioner.py:114-123`), so the gate
  always passes. Fix (DRY): read the Phase 2 `vision_classification` already stored on the Image record,
  and drop the dead call.

### Group 11 - step 6, OpenSERP group

Owner: OpenSERP down at start or mid-run stops the whole phase, stamps nothing, alerts, the book stays
queued for the reconcile (D8c, D8c-2, D8c-3). Page links followed one hop, two Grok checks.
**Test:** restore, run with OpenSERP on; queue items per entity; an outage stamps nothing.

#### OpenSERP: links on a Grok-verified page (owner question 2026-11-17)

Are the links on a page OpenSERP found and Grok verified followed and judged for WWII relevance? No.
`_summarize_url_page` (`OE:696`) fetches the page through `url_fetch.fetch`, and `_grok_summarize_html`
strips every tag with a regex (`OE:675`, `re.sub(r"<[^>]+>", " ", html)`) and sends the first 6,000
characters for a summary. The links are discarded; nothing in `openserp_enrichment.py` extracts them.

- **MED *feature* - DECISION (owner 2026-11-17): follow the links of a Grok-verified OpenSERP page,
  have Grok judge each for WWII relevance to the entity, and send the positives to the URL-review UI
  for a human to confirm.** Same mechanism as the footnote links above (DRY):
  - Extract the links from the HTML already fetched (`result.content`), before the tags are stripped,
    with `link_assets.extract_link_assets` (relative links resolved against the page URL, media type,
    dedup). Drop navigation, same-page anchors, ads and social links (blacklist), and links already
    judged for this entity.
  - **Two Grok checks (owner 2026-11-17): pre-validate, then validate.**
    1. *Pre-validate, before any fetch:* Grok judges the link from the URL, its anchor text and the
       surrounding sentence, against the entity in WWII. Negative -> recorded for (URL, entity), not
       fetched. This is the same step OpenSERP already does for search results (`_verify_result` on
       title + snippet + URL before `_summarize_url_page`), so reuse it.
    2. *Validate, after the fetch:* Grok judges the fetched page content (the summarize step already
       reads it). Negative -> recorded, not queued.
    Both checks are batched (under-50 rule, cache first). Only a link that passes both is enqueued, with
    provenance: parent page URL, the anchor text, the entity ULID, the original search query, and both
    verdicts.
  - **Loop guard: one hop for now, configurable (owner 2026-11-17).** A followed link's own links are
    not followed (like the map-source loop guard `book.startswith("map-")`). The hop count is a config
    item, default 1, read in one place and shared by the OpenSERP-page and footnote-link paths:
    ```yaml
    links:
      follow:
        same_domain: {max_hops: 1, max_per_page: 20}
        external:    {max_hops: 1, max_per_page: 20}
    ```
    Defaults (owner 2026-11-17): 1 hop and 20 links per page for each scope, all four values set in
    `config.yaml` (no code change to adjust them), and logged at the start of each run.
  - **Same domain vs different domain (owner 2026-11-17): classify every link and treat the two
    separately.** Compare the link's host with the parent page's host (reuse `url_registry._host_of`,
    which strips `www.`; a subdomain of the parent's host counts as the same domain, as in
    `link_relevancy._host_matches`).
    - *Same domain:* mostly site navigation, other pages of the same archive or collection. Drop
      navigation (menus, headers, footers, pagination, search, login) before Grok sees them; the parent
      page's provenance and license carry over as a starting point; fetches share the parent's
      per-domain politeness window.
    - *External:* a different publisher. Apply the domain blacklist and human domain exclusions first;
      license and provenance are judged on their own; the URL registry checks run as for any new URL.
    - Each scope has its own hop count and per-page cap (above), its own OBSERV counts (found, judged,
      positive, enqueued), and every queued item records `link_scope: same_domain | external` so the
      reviewer can filter on it.
    - Both scopes get both Grok checks; neither skips review.
  - Cost cap per page per scope (`max_per_page` above), with an OBSERV line when hit.
  - The parent page itself goes to the queue as well (OpenSERP entry above), so the reviewer sees the
    page and its relevant links together.

#### Carried over from PHASE3_MAP.md §6 (found by the 2026-11-17 documentation review)

Not yet re-reviewed step by step; each keeps its old number ("was gap N"), which `PHASE3_MAP.md`
still uses in its text. `EP` = `ecs_entrypoint.py`, `OE` = `src/enrichment/openserp_enrichment.py`,
`P3` = `phase3_enrich_data.py`.

- **HIGH (was gap 2) - An outage or open breaker marks entities as searched for 90 days.** Every driver
  stamps the entity after trying, even when every query returned `[]` (`OE:1418`, called
  unconditionally). Only people and equipment without results are rescued, by the reset in the entry that was gap 6.
  Groups, places and source sections lose their window. The SQS worker is worse: it never resets its
  breaker (`openserp_worker.py`), so after five failures it marks every later entity searched without
  searching, until it exits.
- **MED (was gap 8) - Image results are page URLs.** `images[]` items are search-result pages, not
  images, and nothing resolves them (no `og:image`, no Wikimedia File resolution). Later downloads fail
  with `OBSERV vision.image_blocked reason=not_an_image` (`images.py:77`). Yield for groups and places
  is near zero.
- **MED (was gap 9) - The breaker counts empty results as failures.** Five consecutive legitimate
  no-hit queries open it and skip the rest of that driver's searches (`OE:385-413`).
- **MED (was gap 10) - The SQS path is unwired and defective.** No producer runs; `source_section`
  enqueues nothing; the worker searches generic equipment, lacks a reachability probe and loses its
  metrics; the autoscaler starts workers for any message although the pool size is 0 (PHASE3_MAP.md
  §3.12).
- **MED (was gap 11) - `openserp_manager` cannot tell OpenSERP's task from pipeline work.** It matches
  "openserp" in task ARNs (`openserp_manager.py:31,81`), which never contain it, so the idle monitor
  never tears down while OpenSERP runs. After a failed Phase 3 (no scale-down at `EP:1053-1069`, lock
  held up to 2 h) only the hourly reaper remains, and it also requires no held lock.
- **MED (was gap 13) - External maps cannot work on ECS.** `search_with_openserp` runs
  `./tools/search_maps`, which is not copied into the image (`Dockerfile` has no `tools/` line), and
  only logs the `/mega/search` URL. The failure returns `[]`, and the place is still marked searched in
  the search history (`openserp_maps.py:718`). Off by default (`external_maps.enabled: false`).
- **MED (was gap 14) - `--max-items` does not bound OpenSERP searches** for equipment, groups, places
  and source sections. The cap tests the enriched count, so records that yield nothing keep being
  searched. Only people limit the candidates collected.
- **MED (was gap 15) - Places OpenSERP is under-instrumented and uncapped.** No `_record_eligible`, no
  name gate, no result cap, once per name variant, and image hits are not counted in `items_added`.
  `openserp.results_per_query` is unused (limit fixed at 5).
- **MED (was gap 19) - OpenSERP image items lack provenance beyond the URL.** No query, engine or
  verdict, no link to the fetch event; the verdict cache key is truncated and people image verdicts
  omit snippet and URL (PHASE3_MAP.md §3.6).

#### Older backlog - HIGH list
- **[MED · quality] Phase 3 degradation seen 2026-11-07:** OpenSERP circuit breaker opened 4x (5 consecutive empty/failed
  searches) so bibliography resolve had `backend_down=2`; 88/264 places have no coordinates (`geo_missing_coords` 33%). Check the
  OpenSERP service health on the Phase 3 NAT path and the geocoder fallbacks.

#### Older backlog - LOW list
- **[LOW · investigate] Is `config/domain_blacklist.yaml` blacklist still HONORED by OpenSERP?**
- **[LOW · observability · phase3 review follow-up 2026-11] people/people_groups/places OpenSERP write-paths
  count-iff-persisted but emit NO `write_failed` OBSERV on a failed write** (silent non-count). PR #711 added the loud
  `write_failed`=error + `records_unreadable` metrics to equipment + source_section only; extend the same
  loud-on-failed-write treatment to the people/people_groups/places paths for full write-loss observability parity.

#### 2.1 OpenSERP redesign (the measured CPU bottleneck — 98.7%) (from section 2)
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

#### [LOW·yield] OpenSERP group-image search returns HTML page URLs, not direct images (clean rerun 96c86bef) (evidence for "was gap 8"; from the end of this file)

During Phase-3 OpenSERP group enrichment, 28 image candidate URLs were vision.image_blocked
(reason=ssrf_or_invalid) and 0 images added — because OpenSERP returned SEARCH-RESULT PAGE URLs
(wikipedia.org/wiki/..., commons/wiki/Category:..., army.mil/*.aspx, ebay/itm, militaria product
pages), NOT direct image files. The image-fetch guard CORRECTLY rejects non-image content (not a bug —
SSRF/content-type guard working). But group-image yield is ~0. Follow-up (quality, not correctness):
tune the group image-search query templates / result parsing to prefer direct image URLs (e.g. target
commons File: / upload.wikimedia.org thumb URLs, or add an image-extraction step that resolves a page
URL to its og:image). Low priority.

### Group 12 - step 6, event_provenance_stamp

**Test:** restore, run; every event and sub-event, notes events included, carries SourceSectionID.

#### Step: event_provenance_stamp (PHASE3_MAP §1 step table)

What it is: `stamp_event_source_sections` (`src/extraction/event_provenance.py`) inverts the
source_section records (each carries `SourceSectionID` + `EventID`) into an EventID -> SourceSectionID
index and writes `SourceSectionID` onto each content event and every one of its sub-events, through
`write_json_with_lock(entity="events")`. It closes the entity -> event -> section ID chain the
referential-integrity check resolves. Deterministic, no Grok. Live data (`output/content/USA-E-XChannel/`,
synced to `tmp/phase3-review/imgcheck/content/`): 31 source sections, one per event (21 chapter events +
10 notes events), and no event or sub-event carries `SourceSectionID` yet.

- **MED - notes events are never stamped.** The walk skips `*-notes-event.json` (`event_provenance.py`,
  `stamp_event_source_sections`), but those events have source sections too (10 of 31) and carry
  sub-events (e.g. `chapter2full-notes-event.json`, 42 sub-events) from which entities are extracted
  (`supplemental.py:1243`). Their entities' chain stops at the event. Stamp them as well, unless there
  is a reason they are excluded (none in the code).
- **LOW - one section per event is assumed.** `index.setdefault` keeps the first section for an EventID,
  and every sub-event gets the event's section. True today (31 sections, 31 EventIDs); if a chapter is
  ever split into several sections, sub-events would point at the wrong one. Stamp sub-events from their
  own paragraph range when that happens.
- **LOW - the stamped events reach S3 only through the background sync.** `output/content/` is not in
  `_PHASE23_ENTITY_DIRS`, so `_final_sync` skips it; the background sync uploads it, but its final flush
  skips files modified in the last 2 s (`EP:434`) and nothing covers those. Harmless at this step's
  position in the run; a general gap for late writes under `output/content/`.

#### Older backlog - HIGH list
- **[HIGH · provenance] event_provenance stamp read the WRONG source_section path (fix STAGED, pending PR).**
  Found same E2E: `OBSERV event_provenance.stamp resolved=0 reason=no_source_sections` even though 2 source_section records
  existed — they live at `content/<book>/source_section/*.json`, but `_build_event_section_index` read top-level
  `output/source_section/`. Fixed locally to glob BOTH (nested + top-level) + a regression test; verified against the real
  run data (index resolves, event + 6 sub-events stamped). Needs gate→PR→review→merge→redeploy.

### Group 13 - step 7, equipment_images

Owner: undecided images go to a separate image-review queue, UI TBD (D6); photographs only, any
photograph of the named model (D7).
**Test:** restore, run; verdict counts at least the baseline; no page URLs fetched.

#### Step: equipment_images (PHASE3_MAP §4.5)

What it is (`src/enrichment/equipment_images.py`, owner rules 2026-11-07): for every equipment record,
- **Scope:** `record_scope` (Phase 2 stamp, else deterministic rule, else alias table, else the cached
  Grok resolver). GENERIC records ("rifle", "trench") have their enrichment stripped (`images`,
  `wikipedia_extract`, ... `_ENRICHMENT_FIELDS`); UNRESOLVED and unknown are left alone.
- **Candidates (SPECIFIC only):** images already on the record (Phase 2 legacy, OpenSERP) are queued as
  `vision_check: pending` unless a recorded vision check exists; plus up to 20 images from the record's
  Wikipedia article (all article images, not only the lead; icon/logo filter; Commons license), but only
  if the article text places the subject in WWII (`wikipedia_wwii_relevant` / `mentions_wwii`). Stamped
  once (`image_candidates_checked_at`).
- **Vision:** each pending image is downloaded (`url_hygiene.safe_get`, 10 MB cap, held in memory),
  exact-duplicate checked by hash, then Grok vision (`prompts/equipment_vision.yaml`: a photograph, of
  this specific model, WWII era or a preserved/museum example; "if unclear, reject"), until
  `equipment.images_per_record` (3) are verified. Verdict, reason and UTC time are stamped on the image.
  No verdict 3 times, or the download failing 5 runs, sets `needs_review`.
- One OBSERV summary and an actionable alert per cause. Live data: 689 equipment records (613 generic,
  61 specific, 15 unresolved), 22 specific records with a WWII-qualified article, no images yet.

Findings:
- **MED - `needs_review` has no review tool.** The alert says "open each and set vision_check to verified
  or rejected", i.e. edit the JSON by hand. DECIDED (owner 2026-11-17): a separate image-review queue,
  not the URL-review queue; its UI is to be designed. Reuse the review-status pattern of
  `fetch_provenance` (pending / approved / rejected) for the queue (DRY). Applies to every vision-undecided
  image (equipment, unit and person images, image map-or-not).
- **MED - OpenSERP image candidates are page URLs, so they fail.** `_queue_existing` queues OpenSERP
  images, which are mostly HTML page URLs ("was gap 8"); the download returns HTML, counts as a fetch
  failure, and after 5 runs lands in `needs_review`. Five wasted fetches each and noise for the reviewer.
  Resolve the page to its image (og:image / Commons file) before queueing, or drop page URLs.
- **LOW - a preparation failure is recorded as a vision rejection.** `_verify_media_with_vision` returns
  `(False, problem)` when the image cannot be prepared (format, too many pixels), and `_record_verdict`
  stamps `vision_check: rejected` and `vision_checked_at` although no vision request ran. Record it as
  `unusable` with the reason, not as a verdict.
- **LOW - rejected images stay on the record.** They are kept in `images[]` with `vision_check: rejected`
  (good for provenance); the site and any export must filter on `verified`. Note in the site spec.
- **DECIDED (owner 2026-11-17) - photographs only, but any photograph of the named model.** No drawings or
  illustrations. A photograph counts whatever its setting or date, as long as it shows the specific model
  (e.g. a Sherman sitting in a field). Today's prompt (`prompts/equipment_vision.yaml`) asks for "the WWII
  era or a preserved/museum example" and says "if unclear, err on the side of rejection", which can reject
  such a photo. Change: rule 3 becomes "any photograph of this model (wartime, preserved, museum, monument,
  derelict, in a field)"; "if unclear, reject" applies only to doubt about the model's identity. Re-run
  the vision check on records rejected for setting or era (`vision_reason`).

#### Cross-cutting: every URL a Phase 3 step finds goes to human review (owner 2026-11-17) (moved here from Group 3)
- **FEATURE - owner - an image-review UI for the separate image-review queue (2026-11-17). Design TBD.**

### Group 14 - step 8 to the end: NOAA, post-sync, failure path, cleanup

Owner: release the Phase 3 lock at once on failure (D10).
**Test:** restore, run; kill the task once (SIGTERM) and fail a step once: both alert, the book is
re-queued once, the partial output is audited.

#### Step: EP post-sync steps, on success (PHASE3_MAP §1 "ECS wrapper")

Order after the Phase 3 subprocess exits 0 (`EP:1083-1087`, `_post_process` `EP:2373`, Phase 3 branch
from `EP:2493`): `BackgroundSync.stop()` (final flush) -> `_final_sync` -> `_remove_lock` ->
`_phase3_durable_referential_fix` (`EP:2329`: `_post_sync_referential_enforce` repairs and re-audits
dangling references directly on S3; `provenance_integrity.run_for_phase`; `_refresh_validation_corpus`
re-downloads the corpus; `scripts/validate_phase3.run`) -> `_notify_url_review_pending` (email with the
count of pending URL finds and the review UI link) -> `_provenance_gate("3")` (mode
`provenance_validation.gate`, default `warn`; `block` holds the book for review) -> doc lifecycle to
done (multi-doc only) -> `_get_next_pending_enrich` and trigger the next Phase 3 -> scale OpenSERP to 0
if neither queue has work and `_openserp_demand_present` is false -> `_notify_complete` (email).

- **MED - the post-sync check downloads the whole corpus every run.** `_refresh_validation_corpus` is
  called without a book (`EP:2362`), so it pulls all of `output/content/` and re-downloads every
  `_MENTION_REPAIR_ENTITIES` dir with overwrite. Cost and time grow with the corpus, not the book. Pass
  the book and download only the records the repair changed.
- **LOW - the lock is released before the S3 repair.** `_remove_lock` runs first (`EP:2495`), so
  another task can start and download S3 while `_post_sync_referential_enforce` is rewriting records.
  Remove the lock after the repair and gate.

#### Step: EP failure path (PHASE3_MAP §1 "ECS wrapper")

Four ways a Phase 3 task ends badly:
1. **The Phase 3 script exits non-zero** (`EP:1047-1068`): `_notify_failure` (SNS to
   `NOTIFICATION_TOPIC_ARN` = `dev-wwii-phase2-complete`, so email + Slack; "<phase> failed with exit
   code N" and a log-tail command), `_final_sync_failed_phase` (uploads what changed; a sync error is
   `observe_error`), NAT lease released, `sys.exit(code)`. The Phase 3 lock is kept
   (`if "phase3" not in phase_script`); the ECS task-failure rule (`cloudformation/compute.yaml:735`)
   sends `check_locks`, which deletes it because no task is running, else the 2 h TTL. OpenSERP stays
   up until the teardown Lambdas find no demand.
2. **A preflight or NAT abort** before the script starts: `sys.exit(1)`, no notification (preflight
   entry above).
3. **SIGTERM** (Spot interruption, ECS stop, the watchdog; `_handle_sigterm`, `EP:120`): within the
   30 s `StopTimeout` (`compute.yaml:231`) it runs `_final_sync`, removes the lock, releases the NAT
   lease, re-queues an interrupted batch resume, exits 143.
4. **An exception in the wrapper itself** (e.g. inside `_download_inputs`): Python exits 1 with a
   traceback; only the `atexit` NAT-lease release runs.

Findings:
- **LOW - the failure message is generic.** No book name, a fixed `--since 30m` and the `dev` log group
  hard-coded (`_notify_failure`). The Slack formatter adds a log link; the email has none. Include the
  book, the task ID and the log stream.
- **LOW - the SIGTERM sync may not fit in 30 s.** `_final_sync` walks every entity dir; on a large
  corpus it can exceed the `StopTimeout`, and the lock and NAT lease are released only after it. Release
  the lock and lease first, or raise `StopTimeout` for Phase 3.
- **LOW - DECIDED (owner 2026-11-17): release the Phase 3 lock at once on failure.** The task-failure rule
  deletes it at once anyway, so keeping it changes nothing. Remove the `if "phase3" not in phase_script`
  exception (`EP:1065`, there since the first commit f13984bc with no stated reason) and document it;
  retries go through the reconcile with the retry cap and alert (D8c-2).

#### Cross-cutting: how Phase 3 results reach the S3 JSON (moved here from Group 1)
- **LOW - each changed file is uploaded twice.** `_final_sync` skips only files unchanged since download
  (`_unchanged_downloads`, `EP:262`); it does not know that `BackgroundSync.stop()` (called at `EP:1045`
  before `_final_sync` at `EP:1087`) has just uploaded the same file. One extra S3 PUT per changed
  record. Fix: share the background sync's `_uploaded_mtimes` with `_final_sync`.

#### Carried over from PHASE3_MAP.md §6 (found by the 2026-11-17 documentation review) (moved here from Group 11)
- **MED (was gap 17) - NOAA never runs and its validation warning is benign.** The token is empty and
  nothing supplies it, yet `validate_phase3` warns on every run. Weather records created before their
  place was geocoded may never get coordinates, so they would get neither Open-Meteo nor NOAA data
  (UNVERIFIED: no Phase 3 step backfills weather coordinates, but the creation order was not traced).
- **MED (was gap 28) - Concurrent Phase 3 tasks can overwrite each other's change to a shared record.**
  Each task edits its local copy of a shared map, bibliography or supplemental record and uploads the
  whole file, so when two book tasks change the same record, the later upload wins and the earlier
  task's change (for example a map reference it appended) is lost. Uploading only changed files (was
  gap 27) narrows this to records both tasks actually changed. The same exposure exists for
  bibliography. Not fixed: needs a conditional write (S3 `If-Match` on the ETag read at download) or
  serialising shared-record writes.
- **LOW (was gap 21)** - The completion email text (`EP:4016-4026`) lists "People (biographical data)"
  and "OpenSERP (images, academic papers)", omits geocoding, maps and equipment images, and says "All
  entities enriched".
- **LOW (was gap 22)** - Step labels and lock status text do not match the step list (PHASE3_MAP.md
  §1).
- **LOW (was gap 23)** - Dead or unwired code: `enrich_with_advanced_features`,
  `search_academic_sources`, `_verify_results_batch`, `_prefilter_verify_cache`,
  `_parse_batch_verdicts`, `search_person_images` and `search_military_awards` (used only by
  `scripts/utils/debug_searches.py`), `equipment._extract_media_with_openserp` chain,
  `situational_geocode`, `url_content_verdict.evaluate`, `phase3_retry.py` (copied by `Dockerfile:49`,
  no caller), ~~`lambda_handlers/phase3_handler.py`~~ (deleted 2026-11-20), `--no-references`,
  `enrich_all_people`.
- **LOW (was gap 24)** - `compute.yaml` labels `OpenSerpService` "Internal ALB for OpenSERP" and
  `openserp_manager`'s docstring mentions an ALB, but no load balancer exists. `_get_openserp_alb_dns`
  returns an IP.
- **LOW (was gap 25)** - `.phase_results.json` does not embed the OpenSERP metrics snapshot.
- **DOC** - `search_queries/*.yaml` format and the query-sharpening rules (`_person_query_terms`,
  `_equipment_query_terms`) are described only here and in code.

#### Older backlog - HIGH list
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

#### LOW: referential_integrity residual observability (PR #696 follow-up) (from the end of this file)

Two non-blocking gaps noted during PR #696 review, out of its scope (swallow sites + audit result):
- U1: _repaired_record_is_writable fails OPEN (returns True on guard error); a repair write that
  the guard then blocks is not counted/OBSERV'd. Count non-persisted repairs + emit
  OBSERV referential.subevent_repair_write_failed status=warn.
- U3: _alert send failure logs a plain warning, not a canonical OBSERV — a failed operator alert on
  a dangling corpus is a silent escalation gap. Emit OBSERV referential.alert_send_failed status=warn.

#### LOW: supplemental_info_pipeline chain is dead/unwired (PR #704 finding) (from the end of this file)

extract_from_supplemental_info / process_supplemental_information / _route_entity_extraction /
_create_pseudo_event have ZERO production callers (only each other + 2 tests). The temp *-event.json
it materializes writes NO -parsed.json sibling, so the PR-704 people_groups fail-loud WOULD raise on
that path IF it ran — but it doesn't (dead code). Also it never carried book/author/series (same
Unknown-provenance gap, pre-existing). Decide: wire it (thread book metadata + write the sibling) or
retire it. Not a live regression (dead path).

### Group 15 - after the Phase 3 refactor: failover tests (owner 2026-11-18)

Run once the refactor (Groups 1-14) is complete, in dev, with owner approval for the deploy and the
Grok spend. Each test checks the outcome the owner asked for: downtime is acceptable, a lost book or
repeated paid work is not. For each: the alert arrives on Slack and email, the book is deferred (never
dropped), and after recovery the run continues from where it stopped (compare Grok request counts and
per-step counts against an uninterrupted run).
- **Task killed mid-Phase 3** (stop the ECS task; also a SIGKILL-equivalent): relaunched, resumes.
- **AZ loss:** block AZ1 (e.g. NACL deny on PublicSubnet1/PrivateSubnet1): the NAT comes back in the
  other AZ (1.2c), a new task runs in PrivateSubnet2, the book resumes.
- **NAT / networking loss mid-run:** delete the NAT or blackhole the default route: `connectivity`
  deferral, automatic resume when NAT is back.
- **Grok funds:** under $50 at start (stops before spend); $10 alert mid-run; xAI credit refusal
  mid-run (simulate with an invalid-credit response or a test key): `funding` deferral, resume after
  top-up.
- **OpenSERP / Nominatim / NARA unavailable:** behave as D8b-D8d, and resume.
- **Every external service** in the 1.2f inventory (Grok, NOAA weather, geocoding, archive.org,
  HyperWar, ...): service down and network down, at start and mid-step; a Grok batch failed,
  expired or partly failed (resubmitted whole or only the failed requests).
- **AWS services:** SNS publish fails, SQS delivery fails, S3 put/get fails, DynamoDB throttles,
  RunTask fails (no capacity), Secrets Manager unreachable: nothing is lost, the work stays queued,
  an alert or a record exists, and the run resumes.
- **Spot reclaim:** stop a Spot task mid-step (SIGTERM) and kill one outright: the attempt is given
  back, the book resumes from its last synced state.
Owner rule: simulate ALL of these failure cases (2026-11-18). Each case also gets a unit or moto
test while it is built; this group is the live end-to-end check.

### Track P - Phase 2 changes (needs a new Phase 2 run and a new snapshot)

These change Phase 2's output, so they cannot be tested against `phase2-2026-11-17`. After the Phase 3
groups: run Phase 2 once, take a new snapshot (`scripts/phase_snapshot.py backup`), re-run Phase 3 on
it. Owner: unit/person images checked by the Commons pre-check, vision on flagged ones (D5). The rest of
Phase 2's open work stays in "1. PHASE 2 - OPEN" below.

#### Step: bibliography_resolve (PHASE3_MAP §4.3) (moved here from Group 7)
- ***refactor* - FIX IN PHASE 2 (owner) - a quarter of Phase 2's "bibliography" is not citations,
  found only in Phase 3.**
  Of the 2,035 records, 534 fail `_looks_like_citation` (narrative footnotes, e.g. "An American heavy
  tank did not reach the theater until 1945") and are only moved to `output/narrative_notes/` by the
  Phase 3 resolver (`_relocate_narrative_note`). The test is deterministic, so Phase 2 could route them
  at write time; until then they pass through the dedup review and bibliography reconcile as citations.
  Counted 2026-11-17 by running the resolver's routing functions offline on the downloaded set.

#### Step: event_provenance_stamp (PHASE3_MAP §1 step table) (moved here from Group 12)
- **MED - it runs in the wrong phase.** It needs only Phase 2 output, but runs in Phase 3 inside the
  OpenSERP group ("was gap 3"), so after Phase 2 no event has a SourceSectionID and Phase 2's own
  provenance validation cannot check the event -> section edge. Move it to Phase 2, right after the
  source_section records are written; keep a Phase 3 re-check only if needed.

#### Step: equipment_images (PHASE3_MAP §4.5) (moved here from Group 13)
- Wikipedia/Grokipedia images on units and people should reuse this module (entry "Wikipedia /
  Grokipedia images on people and units").

#### Step: photos from Phase 2 (image_map_vision, image_person_link, image_place_link) (moved here from Group 10)
- **Owner question - do photo URLs found in Phase 2 go to the URL-review queue?** Photos from the source
  document are part of an approved source, so they are not new finds. Wikipedia and Grokipedia images
  that Phase 2 attaches to people, groups and equipment are web URLs; the 2026-11-17 decision says
  every URL find goes to review. Decide whether those count.

#### Wikipedia / Grokipedia images on people and units (owner question 2026-11-17)

Owner rule: Wikipedia and Grokipedia images are trusted as a rule (no URL-review queue). Open
question: what happens when the image is not the person, or shows the unit in another war. Verified
in code and on live S3 data (`output/people/`, 531 records; `output/people_groups/`, 1,116 records,
synced to `tmp/phase3-review/imgcheck/`, not committed):
- Only the article's lead image is taken (`prop=pageimages`), never images from the article body.
  Grokipedia supplies no images (`src/enrichment/grokipedia.py`).
- Nothing checks the image. Units: `_add_wikipedia_image` (`src/enrichment/groups_wikipedia.py:242`)
  inserts the lead image as `images[0]`. The only gate is that the article text mentions WWII
  (`_in_wwii`, `:56`); the image itself is never judged. Equipment is the exception: Phase 3
  `equipment_images` confirms each image with Grok vision (pending/verified/rejected, attempt cap,
  needs_review).

Findings:
- **HIGH - unit images from the wrong era or the wrong subject are on live records.** Of 52 unit
  images, examples: "IX Bomber Command" -> `KC-135A_refuels_B-52D_during_Vietnam_War.jpg` (the lookup
  landed on the 19th Air Division article); "Tank Destroyer" -> an Italian Centauro at exercise
  "Scorpion Legacy 2025"; "Ninth Air Force" -> `Emblem (Cold War).png`; "Germans" -> a Reichstag panorama;
  "Rundstedt" (a person extracted as a unit) -> his portrait. Fix (DRY): generalize
  `src/enrichment/equipment_images.py` into one entity-image verifier (same states, attempt cap,
  needs_review and alert) used for equipment, units and people, with the prompt per entity type
  (unit: insignia or the unit in 1939-1945; person: a photo of this person). Cost control first: a
  deterministic pre-check on the Commons file metadata already fetched for the license
  (`_wikipedia_common.fetch_commons_license`, `extmetadata`: `DateTimeOriginal`, file title words such
  as "Vietnam", "Korea", "Cold War", a year after 1945), and vision only for what it cannot decide.
  DECIDED (owner 2026-11-17): vision only on the images the pre-check flags; switch to every image if
  the pre-check misses cases (count what it passes and spot-check).
- **HIGH - a person's Wikipedia portrait never reaches the person JSON.** `_store_wikipedia_image`
  (`src/extraction/enrich_biographies.py:450`) saves the photo under `person_photos/` (69 files in S3)
  and an in-memory cache; the only reader, `search_wikipedia` (`:686`), assigns it to an unused
  variable (`grok_info`). No live person record has `images` or a portrait link. When this is fixed, the
  same verifier applies: a lead image can be a signature, a grave, a ship or a map, and a non-person
  image must not be shown as the portrait.
- **MED - the article match itself can be wrong.** "IX Bomber Command" matched the 19th Air Division
  article and still passed `_in_wwii` because that article mentions WWII. A redirect to a different unit
  should be detected (requested title vs resolved title) and the match sent to review.
- **LOW - a person extracted as a unit.** "Rundstedt" is a `people_groups` record; belongs with the
  Phase 2 entity-type checks.

#### Wikipedia / Grokipedia footnote links (owner question 2026-11-17)

Are the citation (footnote) links of a Wikipedia or Grokipedia article followed and judged for WWII
relevance? No. Verified in code:
- Wikipedia: every lookup asks only for the lead section as plain text (`prop=extracts|pageimages`,
  `exintro`, `explaintext`; people `enrich_biographies._build_wikipedia_request`, units
  `groups_wikipedia._lookup_group`, equipment `equipment_wikipedia`). The reference list and its links
  are never requested (no `extlinks`, `references` or `cite_note` use anywhere in `src/`).
- Grokipedia: `_article_text_from_html` (`src/enrichment/grokipedia.py:259`) keeps the article text
  and drops every `href`, so its citation links are lost.
- People only: the biography prompt asks Grok for `source_urls` (`prompts/biography.yaml:45`), i.e.
  URLs it reads in the text or proposes itself. `validate_source_urls` (`enrich_biographies.py:968`)
  fetches each (`url_hygiene.fetch_text_safe`, Wayback fallback), asks Grok whether the page is about
  "the WWII-era person" (`:940`), and writes the relevant ones straight into
  `biography_sources[]`, with no human review. This fetch bypasses the central `url_fetch` / URL
  registry, so the same URL is fetched again for each person and each run.
- `references` in the same prompt are entity NAMES (units, people), followed by name lookups on
  Grokipedia/Wikipedia (`search_references`, max 3), not links.

- **MED *feature* - DECISION (owner 2026-11-17): follow the citation links of Wikipedia and Grokipedia
  articles, judge their WWII relevance, and send the relevant ones to the URL-review queue for a human
  to confirm.** Reuse, no new mechanism:
  - Extract links with the M4 link extractor (`link_assets.extract_link_assets`: media type, relative
    links, dedup) on the article HTML: Wikipedia `action=parse&prop=externallinks` (or the rendered
    references section), Grokipedia's already-fetched HTML before `_article_text_from_html` strips it.
  - **Same two Grok checks as the OpenSERP page links below (pre-validate from URL + footnote text
    before fetching, then validate the fetched page).**
  - **Order (owner 2026-11-17): Grok judges first; only a positive verdict reaches the human.** Grok
    is asked whether the linked page is relevant to this entity in WWII (title, snippet, footnote text,
    and the fetched page when available). Positive -> URL-review queue. Negative -> recorded (per
    URL, entity) and not queued. Reuse `link_relevancy.should_enqueue_link`
    (`src/extraction/link_relevancy.py:93`), batched, but it does not meet this rule today: a
    whitelisted host is enqueued with no Grok verdict (`:109`), and no client or any error enqueues
    "when in doubt" (`:111`, `:121`). For this path: blacklist still drops first; whitelist and errors
    do not skip Grok; a Grok error or missing client leaves the link pending for the next run (with
    an OBSERV count), never queued unjudged. **The in-document link review (M4, `link_asset_review`)
    follows the same rule (owner 2026-11-17)**: change `should_enqueue_link` itself, so every caller
    gets it.
  - Enqueue through the shared enqueue helper (OpenSERP entry above) with the article URL, the entity
    ULID and the footnote text as provenance; the human approves in the URL-review UI; approval submits
    to Phase 0.
  - Apply the (URL, entity) verdict keying and the checksum re-check from the OpenSERP section.
  - The same `links.follow` settings (hops, per-page cap per scope, same-domain vs external) as the
    OpenSERP page links below; an OBSERV line when a cap is hit.
- **MED - Grok-proposed `source_urls` skip review and the central fetch.** Keep the existing Grok
  relevance check (`_validate_url_relevance`), and send only the positive ones to the URL-review
  queue instead of writing them to `biography_sources[]`; route the fetch through `url_fetch.fetch`.
  Until a human approves, a URL is not written as a source.

#### Code defects found by the 2026-11-17 documentation review (each verified in code)
- **Person -> group membership is not wired**: nothing in Phase 2 or 3 fills `members[]`
  (`_link_person_to_groups` is reachable only from the CLI/test `enrich_all_people`). Owner decision.
- **`validate_phase2` does not trace `source_section` or `images` mentions** (`_MENTION_ENTITIES`,
  `validate_phase2.py:112-119`).

LOW
- Award sources: only the `hall_of_valor` adapter is built; the other registry rows are skipped.

#### People: surname-only names are enriched from a Wikipedia disambiguation page (HIGH)

Found 2026-11-16 (USA-E-XChannel, owner report; Grok request cache key
`19bc247c3421978c86fdd8a007251c8d65453cc9c94ef2176d2a10368fb8709b`, type `api`). The book names
"Maj. Gen. Eugene M. Landrum" once (record `eugene m landrum.json`, enriched correctly 2026-11-15)
and then "Landrum" alone (chapter 10, 90th Division drive west) - extracted as a SECOND person,
`landrum.json`. Its Phase-2 enrichment (`enrich_biographies.enrich_person_from_sources`):
- Grokipedia: `NOT_FOUND`. Wikipedia `search_wikipedia("Landrum")` returned the DISAMBIGUATION page
  ("Landrum can refer to: Bill Landrum ... Eugene M. Landrum (1891-1967), major general ... Phil
  Landrum ...").
- `extract_biographical_data` then sent that list to Grok (`prompts/biography.yaml`) with only the
  name "Landrum" - no rank, unit, date or book context - asking for one person's biography. A wasted
  request at best, a wrong person at worst. It was one of the 3 round-9 requests behind the
  2026-11-16 `batch.rounds_over_cap` alert.
- No follow-up: nothing fetches the chosen person's OWN Wikipedia / Grokipedia page after Grok
  answers; whatever Grok extracts from the snippet becomes the biography.
OPEN: when the batch answers, record whom Grok picked for "Landrum" (expected: Eugene M. Landrum,
the 90th Division's commander from 13 June 1944).
ACTION (deterministic first, cost first):
1. Resolve a surname-only person to a full-name person of the SAME book before enrichment
   (same surname, a full-name mention earlier in the book / same chapter or unit); attach the
   mentions to that record (ties to the 504 surname-merge suggestions) and skip its enrichment.
2. Never send a disambiguation page to Grok: detect "can refer to" / "may refer to" / list-only
   pages (Wikipedia `pageprops.disambiguation`) and treat as not found - or pick the entry using
   the book's context (rank, unit, date, nationality), then FETCH that entry's own page.
3. Pass the book context (rank, unit, date, the citing sentence) into the biography prompt, and
   require the answer to echo which article it used, so a wrong-person answer is detectable.
4. Count it: OBSERV `enrich.disambiguation_page` / `enrich.surname_only_skipped`.

#### Phase 2 re-runs: image / map / source-section records de-duplicate against LOCAL disk (MED)

Found 2026-11-16 with the weather/casualty duplicates (fixed: `ecs_entrypoint.REHYDRATED_ENTITY_DIRS`
now restores weather, logistics and casualties). Still exposed when a fresh container re-runs them:
`images.extract_images` (local `images/index.json` + `_load_existing_urls`),
`external_maps._check_duplicate` (globs the local maps dir), `source_section` (scans local section
dirs). Each runs once per normal run today, so a first run is clean; a later Phase 2 re-run would
write duplicates. ACTION: rehydrate their RECORD files (not `images/files/` binaries) or dedup
against S3, with a test like `tests/unit/test_phase2_resume_rehydrate.py`.

#### Older backlog - HIGH list
- **[MED · data loss] A schema-blocked weather mention leaves an empty shell record, and the event is marked processed (2026-11-07).**
  `weather_central._find_or_create_weather` writes the new record (`extracted_data: null`, `event_mentions: []`) BEFORE
  `_add_event_mention` merges the statement; if that merge is refused by the write guard (`locked_json.blocked_schema_invalid`),
  the shell stays and `extract_weather_central` still marks the event processed, so the statement is never retried. Build the
  full record (statement + mention) and write it once, and do not mark the event processed when a merge was blocked; add a test.
- **[HIGH · data loss] Entity filenames use the ULID TIMESTAMP prefix (`id[:8]` / `[:12]`) (found 2026-11-07).**
  Two records with the same name created in the same millisecond get the same filename and the second write overwrites the
  first. Equipment and weather are fixed (random tail `id[-8:]`; both locate records only via their index). Still affected:
  `src/extraction/people.py` (`person_id[:12]`), `places.py`, `dates.py`, `logistics.py`,
  `batch_parallel.py` (shared helper). Before switching each, confirm nothing recomputes the filename from the ID to find an
  existing record, then add a same-ms collision test per writer.
- **[MED · quality] Equipment records written before schema 2.29 keep ungrounded Grok text (2026-11-07).**
  The old Phase 2 gap-filled `description` / `specifications` / `alternate_names` / `variants` from an UNGROUNDED Grok
  call, for generic AND specific records, with no marker telling it apart from narrative text. Phase 3 `equipment_images`
  strips the sourced enrichment from generic records but cannot identify these fields. The planned full reset + rerun
  regenerates every record under the grounded rule; if any pre-2.29 equipment record must be kept instead, re-extract it.
- **[LOW · completeness] Unresolved named vessels are not re-resolved automatically (2026-11-07).**
  A vessel left `identity_scope: unresolved` (Wikipedia unreachable -> `identity_resolution.transient: true`, or no
  date-consistent candidate) keeps its `"<name> (unresolved vessel, <year>)"` record until an operator sets
  `canonical_name` (the `equipment.vessel_unresolved` alert says so). Add a Phase 3 pass that re-runs
  `equipment_identity.resolve_vessel` on transient ones and, when resolved, merges the record into the ship's record
  (dedup gate) - with a test that two years' mentions resolve to their two ships.
- **[MED · provenance] People marked `enriched` with no source link (P3-A5, 4 people: J. M. Stagg, M. D. Taylor, G. C. Marshall,
  D. D. Eisenhower; found 2026-11-07).** `validate_phase3` FAIL: `enrichment_status=enriched but no source link
  (top-level/biographical_profile/external_data)`. Find which enrichment path sets `enriched` without writing a source URL.
- **[LOW · observability] `enrich.summary` label `skipped_already` is misleading** (`src/extraction/people.py:893`). The
  `skipped` counter also counts "attempted, not enriched" (e.g. Wikipedia rate-limit deferral), so 59 of 62 people read as
  "already enriched" when they were deferred. Split into `skipped_already` vs `deferred`.
- **[MED · enrichment] Deferred Wikipedia lookups have no automatic retry.** XChannel-8: 194 transient (28 `rate_limited`,
  166 `backoff_active`), people enriched 3/62; Phase 3 does not retry, so records stay unstamped until Phase 2 is re-run.
  Repeated same-day dev runs share one NAT egress IP, which likely kept it rate-limited. Decide: scheduled re-enrich pass for
  records with no `wikipedia_checked_at`, or accept and document.
- **[HIGH · provenance/design] Stop trusting Grok to TRANSCRIBE paragraph numbers — resolve fulltext DETERMINISTICALLY (owner 2026-10-30).**
  Today the events extractor feeds Grok `Paragraph_1..Paragraph_N` and asks it to echo back the paragraph_numbers per
  sub-event; we then LOOK UP fulltext by those numbers. Asking an LLM to copy identifiers we already own is a mechanical
  transcription task it does badly (hallucinated sequential 264..1700 ranges -> no_resolved_paragraphs -> every entity
  extractor starves; the recurring paragraph_numbers fingerprint). This VIOLATES the codebase's own 'never trust the LLM
  for an identifier/key' rule that we already apply to PersonID/GroupID (minted server-side). PROPER FIX: the LLM returns
  only the SEMANTIC grouping (e.g. a short verbatim quote / first+last sentence of each sub-event span); the CODE maps that
  quote back to paragraph absolute_numbers deterministically (substring match against parsed text). The LLM never owns a
  provenance key. Eliminates the starvation class entirely (not just detects it). INTERIM (shipped): _validate_paragraph_range
  rejects+resubmits an events response whose paragraph_numbers are mostly out of [1..N] (band-aid; keep as defense-in-depth).
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

#### Older backlog - MED list
- **[HIGH · data quality · 2026-11-11] Extraction defects found by the provenance validation
  (USA-E-XChannel, 20 chapters; report `output/reports/provenance/USA-E-XChannel-phase2.json`).**
  **PARTLY ✅ DONE (code check 2026-11-16):** (b) mention-in-wrong-sub-event and (a)/(d) paragraph numbers: all discovery now runs over 3-paragraph raw windows and each item is settled against the window text and attributed to the sub-event holding its quote, with its paragraphs taken from where the quote is (`windows.settle_item` / `attribute_found`; `_PARAGRAPH_KEYS` = `paragraph_numbers`, `paragraphs`). (c) an unsupported quote is repaired to the source sentence or the item is dropped (`quote_unsupported`). (e) bibliography note locators: `supplemental.locate_notes` sets `reference_number` / `verbatim_reference` deterministically. Re-validate on the next E2E before closing.
  (a) logistics `paragraph_numbers` are NOT chapter paragraph numbers: the prompt gives the model the
  sub-event text with no paragraph numbers (`logistics._get_sub_event_text` joins the fulltext), so it
  numbers them itself (41 of 61 cited numbers fit a 1-based count within the sub-event; 67 fail).
  Fix: number the paragraphs in the prompt, or resolve them from `original_text` (anchor_resolve).
  (b) 183 entity mentions quote text that is in ANOTHER sub-event of the chapter (places 50, equipment
  55, dates 38, people_groups 23, people 16): the mention is linked to the wrong sub-event. (c) 66
  `original_text` values are not in the book (paraphrases, e.g. "after 1940 defeat"; chapter
  headings). (d) 11 equipment paragraph numbers exceed the chapter. (e) all 854 bibliography mentions
  of the book have no note locator (no `reference_number`, empty `verbatim_reference`).
  (f) Phase-3 run (2026-11-11, pre-Phase-3 data): 7 enriched records name no source (people
  georges bidault, frank m andrews; 5 equipment incl. V-1) - P3-A1; every cited note of the 10
  footnoted chapters has no bibliography entry (follows from (e)) - P3-C1.
  (g) SILENT places shortfall, chapter 8 (rerun 2026-11-11): places extracted for sub-events 1-2
  only (10 refs; 0 in sub-events 3-12 although the text names Colleville 25x, St. Laurent 25x,
  Carentan 24x, Merderet 18x, Vierville 14x). Task log: `chapter8full-parsed.json: places=10`, no
  warning, and the yield gate did not fire. ROOT CAUSE (2026-11-12): the places request asked the
  model for coordinates (a geographic lookup, not reading); the same request without them answered
  12/12 twice. FIXED: Phase 2 places carry no coordinates; Phase 3 geocodes the 215 places that held
  Phase-2 guesses. OPEN: a per-sub-event coverage check on entity answers (re-ask the sub-events an
  answer leaves out), and repair chapter 8 (re-ask its places).
  (h) Recall varied run to run when a whole chapter was asked at once: chapter 8 places 42 / 118 /
  89, people 55-84 mentions. CAUSE: one request per chapter (~37k tokens, 12 sub-events) - the
  model decides how much is enough (one answer: a flat 3-4 places per sub-event). FIXED for
  places (2026-11-12): one request per sub-event (`batch_parallel._per_sub_event`); three runs gave
  227 / 226 / 226 mentions, near-identical per sub-event, and the provenance check passes them.
  FIXED for dates (2026-11-12): chapter-wide 74 mentions; per sub-event 91 / 98 / 99, all 12
  sub-events covered, 0 provenance gaps (the one chapter-wide-only date was "July 1944" stored as
  exact 1944-07-01). Places and dates for a sub-event share its text as the cached first message.
  Note: per-sub-event requests have new cache keys, so re-running an already-extracted chapter
  pays again for its places/dates/people.
  FIXED for people (2026-11-12): chapter-wide 78 mentions (10/12 sub-events; 2 name nobody);
  per sub-event 105 / 103 / 102, 0 provenance gaps. Per sub-event the model names people as the
  passage does ("Gerow", "Jodl", "Sperrle"); chapter-wide it filled in full names from background
  knowledge, and once named a person the text never mentions ("Eugen König"). Surname-only and
  split records ("Bradley" / "Omar Bradley") are for entity resolution (the OOB spine), not the
  extraction prompt.
  OPEN: move the optional extractors to `_per_sub_event` too (each with a 3-run check).
  FIXED for people_groups (2026-11-12): chapter-wide 54 mentions; per sub-event with the old
  prompt 78 / 69 / 84 - the prompt's 'division|corps|army' left the model to decide per run
  whether regiments, battalions, army groups and British units counted. With every echelon and
  nationality named, full designations asked for and ships left out (they are equipment):
  258 / 267 / 266, 11/12 sub-events, 0 provenance gaps. All four core entity types now share each
  sub-event's text as one cached first message.
  OPEN (review 2026-11-12): group enrichment (`enrich_groups.enrich_all_groups`, not wired today)
  has no specificity gate - with ~4x more, finer groups ("Group under Lt. X", "SHAEF staff",
  companies) each would cost a Grok call. Gate it on a derivable unit_key before wiring it in.
  FIXED for equipment BROAD (run 1) (2026-11-12): chapter-wide 99 items (95% of quotes verbatim);
  per sub-event 358 / 331 / 317 (97-98% verbatim), 336 through the shipped code. Shared code:
  `src/extraction/sub_events.py` (views, failure alert, stop rule, `run_sync` for synchronous
  extractors) now serves all five. Items are pinned to their sub-event's paragraphs, and the run
  cache key carries the Sub-eventID (EventID alone would have served every sub-event the first
  one's answer). Run 2 (REFINE) is one call per item, ~3.4x more per chapter (chapter 8: ~100 ->
  ~340). Concurrency: 3 event workers x 8 workers = up to 24 requests in flight per process, all
  on the shared rate limiter. Old chapter-wide "broad" cache entries are orphaned: re-running an
  extracted chapter pays again.
  FIXED (2026-11-12, step 1 of 4): every equipment Grok call is batchable, ONE batch round per
  dependent stage (`sub_events.run_all`): broad -> refine -> identity -> source re-check ->
  (persist: vessel disambiguation, enrichment). Refine and prepare run concurrently; nothing is
  written before the requests a record depends on are answered; identity resolution, source
  re-check and stub creation no longer swallow the batch signal (they wrote unresolved records
  without their re-checked fields); a failed item leaves the chapter unmarked for a retry;
  enrichment's cache key is the record + source URLs, not the fetched text.
  BATCH ORCHESTRATION (owner design 2026-11-12) - remaining steps:
  (2) Phase 2 driver: loop collect -> submit -> re-run until nothing is queued (all of Phase 2,
      core + every optional extractor), with a round cap; over the cap -> ALERT plus automated
      diagnostics (queued request counts by cache_type/stage, keys queued in consecutive rounds =
      submit/lookup key divergence, sample prompts), extendable as new cases appear.
      DONE 2026-11-12 (`phase2_extract._run_batch_rounds`, `src/utils/batch_rounds.py`,
      `batch.max_rounds`: 8): a cap stop alerts, writes `.phase_results.json` `incomplete` and
      exits 1. Steps 4-5 (maps, images, source sections, analysis, stats) now run once after
      the rounds. Source-section enrichment is not batch-aware yet (runs live after the rounds)
      - audit it and the other optional extractors for swallowed BatchModeCollecting.
  (3) ECS: the container waits up to 90 min PER BATCH ROUND for Grok (goal: prevent container
      churn); a round not back in 90 min -> enqueue + exit, the Lambda poller resumes it, and the
      resumed container follows the same rule for further rounds. The retrieve re-run must keep
      batch mode for the next round (today `_retrieve_rerun_env` strips it: equipment runs live).
      The shutdown wait is configurable (shorter for testing).
      FOUND 2026-11-12: (a) `run_submit_only` monkey-patches `poll_batch` in the PARENT process but
      runs Phase 2 as a subprocess, so the patch never applies: the child already waits in-task,
      up to 24 h (`poll_batch` `max_hours=24`); (b) on that timeout a partial result is a count
      mismatch, which forces a LIVE retry of every request (`_retry_failed_batch(force=True)`);
      (c) after a round-cap stop (exit 1) the last round's batch is still enqueued, so the poller
      can launch a retrieve-only re-run that finishes the book LIVE and triggers Phase 3 - skip
      the enqueue (or refuse the retrieve) when `.phase_results.json` has `incomplete`.
      DONE 2026-11-12: each round waits `batch.in_task_wait_minutes` (90; env
      BATCH_IN_TASK_WAIT_MINUTES); a round not answered by then is HANDED OFF - metrics with the
      submitted ids saved, no live retry, credit reservation kept, Phase 2 exits 75 with
      `pending_batch`; the task enqueues exactly that batch (`_settle_phase2_task`). The
      retrieve task resumes Phase 2 IN BATCH MODE from the next round (`BATCH_ROUND_START`, the
      round is in the batch name, now kept in the metrics/job). Exit 0 -> post-process; any other
      exit (incl. the cap) -> nothing enqueued. The ineffective poll stub in `run_submit_only`
      still serves Phase 3's path (it never reaches the child either).
  (4) One container per book/resource; one book at a time for now (parallel later). A second
      book submitted while one is batched must never co-mingle with it (per-book jobs, batches,
      locks). A book split across several batches keeps provenance, one consolidated dedup and
      one consolidated Phase 3.
  CHAPTER SCOPE (2026-11-12): re-submitting one chapter now runs Phase 2 on that chapter only
  (`PHASE2_CHAPTERS` from the Phase 1 manifest; carried on the batch job to a resumed run).
  OPEN: the manifest is consumed on read (`_read_manifest` deletes `pending#parsed#<book>`), so a
  retry/reconcile after a task died before its first batch job falls back to the whole book (as
  before) - persist the scope (e.g. a `scope#<book>` row) if one-chapter retries matter.
  LOW (review): `_build_mention` falls back to a random Sub_eventID when a sub-event has none
  (merge_mentions then cannot dedup across rounds); `enrich_groups._maybe_source_recheck`
  swallows BatchModeCollecting (groups enrichment is not wired today).
  OPEN (pre-existing, review 2026-11-12): person enrichment (Grokipedia/Wikipedia fetches) runs
  synchronously inside the people index lock (`_batch_extract` -> `enrich_record`), so it blocks
  the event loop and serializes sub-events / chapters. Move it after the index write.
- **[MED · entity-resolution] Resolve `people_groups` against the ETO Order of Battle (the authoritative unit spine).**
  Moved from the steering "known gaps" list (2026-11-04). Name-level resolution exists (`find_duplicate_groups`,
  `src/dedup/unit_key.py`, `data/unit_nicknames.yaml`), but nothing resolves groups TO the OOB: the OOB crosswalk
  (`src/ingestion/oob_markdown/crosswalk.py`, `entity_emit.emit_crosswalk_to_people`) links command staff -> people only.
  Open: map each per-mention group to an OOB unit, and add the time-bounded `org_attachments[]` on `people_groups`
  (see the schema.org item under HIGH).
- **[MED · provenance] Derive endnote vs footnote `reference_type` from the SOURCE, not Grok.**
  From the retired `messages/TODO_fetch_endnote_content.md`. Endnote text is now fetched and passed to the supplemental
  prompt, but `reference_type` is still the model's call: `supplemental._build_ref_context` only adds a type hint when NO
  text was found, and `_separate_by_type` routes on whatever the model returned. Symptom seen before: Breakout and Pursuit
  (endnote-only) produced `*-footnotes.json`. Fix: set the type deterministically from the source structure (marker style /
  where the definition lives) during parse, carry it in the parsed `footnotes` entries, and override the model's value.
  See `docs/current/features/supplemental/ENDNOTE_HANDLING.md`.

#### Older backlog - LOW list
- **[LOW · observability · DRY review follow-up 2026-11] `src/extraction/logistics.py:390` swallows a whole-chunk
  loss.** `except Exception: logger.error("Batch logistics extraction failed: %s"); return {}` drops the entire
  chunk's logistics on any error with only a bare `logger.error` (no canonical OBSERV, so it never escalates). Convert
  to `observe_error("logistics", "batch_failed", ...)` (anti-swallow; surfaced during P1.4 but out of that PR's scope).
- **[LOW · data-hygiene · DRY review follow-up 2026-11] wikipedia image-URL query-strip inconsistency.**
  `equipment_wikipedia._extract_equipment_page` strips `?query`/`#fragment` from the image URL before the Commons
  license lookup; `groups_wikipedia._extract_group_page` does NOT, so a group image URL with tracking params yields a
  dirty `File:` title and a missed/incorrect license. Make groups strip the same way (surfaced during P1.9).

#### 2.2 NARA resolution redesign (correctness) (from section 2)
- **[HIGH] Back-reference / multi-document citation — two sub-items remain.** Within-chapter DONE.
  OPEN: (a) MULTI-DOC NOTE ("A; B; C" stored as ONE record — LLM overlay's semantic job); (b)
  CROSS-CHAPTER DOCUMENT IDENTITY (ch3 "Irwin Diary" == ch5 — dedup's job).

- **[HIGH] 3 footnote CLASSES — hybrid SPLIT is the gap.** CITATIONAL → bibliography, PROSE →
  ✅ DONE (code check 2026-11-16): `content_class` already carries citation / prose / hybrid with a per-entry `prose_content` (`src/json_schemas.py` supplemental schema; `supplemental._route_one_material` routes hybrid to BOTH). The "OPEN" text below is the original gap.
  notes-event, HYBRID → BOTH. The 3-way split is the Phase-2 supplemental LLM's job. OPEN: add
  per-entry `prose_content` + widen `content_class` to {citation, prose, hybrid}; prompt the LLM to
  SEPARATE prose from citation(s); route prose → `{chapter}-notes-event.json`, each citation →
  `store_bibliography_entry`; regex detector becomes a non-decisional backstop.

- **[HIGH] Capture the actual Bibliography section + abbreviations decoder.** CMH green books have a
  "Bibliographical Note" (full citations) + an "Abbreviations" list (decodes footnote shorthand). Add
  a parser back-matter detector; emit `*-bibliography.json`/`*-abbreviations.json`; prefer
  bibliography-section entries + apply the abbreviations map to footnote verbatims before NARA.

#### 2.3 People / places enrichment (from section 2)
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

- **[HIGH] Wire SourceRechecker into People enrichment** (source-first gap-fill of `nationality` etc.
  in `enrich_person_biography`, mirroring people_groups). Minor residuals: Grokipedia fetch real
  `/page/<slug>` not search-results HTML; OpenSERP stamps `openserp_searched` on empty search;
  `time.sleep(1)` on cache hits; HallOfValor name-match recall.

#### [MED] source_section derive failed for 2 sections (clean rerun e4b22d9) (from the end of this file)

"[phase2 step 1/5] Core extraction complete: 4 processed, 2 failed" + source_section.enrich errors=2:
"source_section derive failed for 0GDW2A077AXSFWX53MGV5VTT6G: <hash>" and 7EQS9MD8QY861A2VB8B0NZEYKX.
The error payload is an opaque hash (looks like a cache key / exception fingerprint, not a message) —
investigate what source_section derive raises for those 2 sections. Also benign: "Status server
failed to bind 10.0.22.19:8077 (Address already in use) — continuing" (port reuse, non-fatal).

#### [MED·dedup-false-positive] place dedup is string-only at the Phase-2 gate (no coords) — Roscheid/Schmidt (deferred fix #2) (from the end of this file)

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

#### MED: same-code consolidation (Phase-2 review, deferred to own PR) (from the end of this file)

- Grokipedia resolution forked 3x: shared src/enrichment/grokipedia.resolve_grokipedia_url exists
  (equipment uses it), but enrich_biographies._grokipedia_query_scan + enrich_places._search_grokipedia_place
  roll their own grokipedia.com/search scrape. Wikipedia API call likewise reimplemented in
  enrich_biographies (x3) + enrich_places. Route all through the shared lib (triples maintenance +
  SSRF surface + drifts behavior, e.g. transient-vs-negative cache conflation enrich_places:329).
- Conservative place-name matcher (exact -> whole-word-longest -> SequenceMatcher>=0.88)
  reimplemented in weather_central._match_place_id, equipment._resolve_place_id, and places. Extract
  one shared helper.
Larger refactor with semantic-diff risk; own PR, not bundled with the PR6 correctness/security fixes.

---

## Other open items (moved out of the Phase 3 review section; not in a Phase 3 test group)

### Code defects found by the 2026-11-17 documentation review (each verified in code)
Phase 3 defects (OpenSERP service, 90-day marker, breaker, batch mode, external maps, NOAA, ...)
are listed once, in the "Phase 3 review" section above; not repeated here. The rest:

HIGH
- **A parse-track upload with no safe slug runs Phase 1 over ALL books.** When no pending key
  yields a slug, `_resolve_pending_launch_book` returns `""` (`trigger_handler.py:2433-2437`), which
  means "all books"; the `unnamed-upload` off-ramp covers only OCR, convert and video uploads. Breaks
  the slug rule (an unnamed upload must go to `needs-review/unnamed-upload/`). Fix: route `.md`/`.txt`
  keys with no slug through `_unnamed_upload`, and never launch on an all-unsafe set.
- **Phase 1 resets the dedup review status for the whole corpus.** `_prepare_phase1`
  (`ecs_entrypoint.py:1092-1103`) writes `{"complete": false}` to `dedup/review_status.json` on every
  Phase 1 task, so parsing one new book discards a pending or finished review of the others.
- **Monitoring that can never fire.** The `{env}-wwii-pipeline` dashboard, the `phase2-duration`
  alarm and the `dlq-messages` alarm (`cloudformation/events.yaml`) watch Lambdas and queues no
  template creates (`phase1-parse`, `phase2-extract`, `phase1-dlq`, `phase2-dlq`). Delete or repoint.

MED
- **Skipped people groups come back.** The people detector never reads `reviewed#` rows; only
  Exclude is permanent for people (places, groups, equipment honour Skip). `docs/current/core/DEDUP_REVIEW_GATE.md`.
- **Dedup Undo restores the snapshot files only**: not the reference rebase, `index.json` or the
  report group (`dedup_ui_handler` undo path).
- **The ECS dedup detection passes no Grok client**, so ambiguous people matches get no Grok check.
- **`doc#` rows can be stuck**: the dispatcher's `DocFailed` state leaves a failed doc at `running`;
  `needs-review` has no exit (`human_gate` is deployed but never invoked). `docs/current/core/DOC_LIFECYCLE.md`.
- **`_seed_docs`** uses `parts[1]` as the book (the namespace for `_system`/`user-` uploads) and is a
  no-op on chapter-parsed events (`output/content/` keys).
- **Intake suffix mismatch**: `.htm`, `.xhtml`, `.webp`, `.bmp` are in the track suffix sets but not
  in `_CONTENT_SUFFIXES` (`trigger_handler.py:687`), so they are rejected as `unrecognized-type`.
- **8-character ULID filename suffixes** (`batch_parallel.py:322`, `people_groups.py:47`): the first
  10 ULID characters are the timestamp, so 8 characters separate ids only ~1 s apart - two same-named
  records minted in the same second would collide. Use 12 like `people.py`.
- **Dead config**: `api.grok.timeout` (60) and `api.grok.max_retries` are not read (the client uses a
  600 s read timeout and 5 attempts, `grok_client.py:479,1477`); also `maps.*`, several
  `external_maps.*`, `aws.*`, `weather.*` and `processing.*` keys. List: `docs/current/core/CONFIGURATION.md`.
- **"VII Corps" and "7th Corps" are never surfaced as duplicates**: `derive_unit_key` unifies them
  but `_numbers_match` (`find_duplicate_groups.py:262-289`) keeps `r7` and `7` apart.
- Unwired code: `src/utils/failure_taxonomy.py`, `src/utils/url_content_verdict.py`,
  `ecs_entrypoint._acquire_lock` / `_clear_all_locks`, `dedup_gate_handler` (deployed, never invoked),
  `consolidate_people`, `ingest_affirmed_chart`, `generate_date_summary`; the standalone
  `python -m src.extraction.external_maps` CLI is broken (`main()` passes `events_dir`).

### Tooling: widen ruff beyond `--select B` (LOW/MED)
`scripts/gate.sh` runs `ruff check src/ lambda_handlers/ --select B` (flake8-bugbear ONLY); there is
NO `[tool.ruff]` config in pyproject. Running ruff with DEFAULT rules reports ~83 findings (55 auto-
fixable — unused imports, etc.) the gate never sees. ruff "never fails" because the rule set is too
narrow, NOT because the code is clean. ACTION: widen `--select` incrementally (start `B,F` → undefined
names/unused; then `I` import-order), clear the resulting backlog per family, pin a `[tool.ruff.lint]`
config. Do it family-by-family so the gate doesn't drown. (Raised 2026-11-01.)

### Refactor: evaluate running entity extractors in PARALLEL (MED)
Observed 2026-11-01 (chapter-8 sync run): biography/Wikipedia enrichment ran SERIALLY and stalled the
whole Phase-2 for 10+ minutes (per-person live Wikipedia/Grokipedia calls — Jodl, von Rundstedt),
blocking equipment extraction (which runs AFTER core/optional entities) from even starting. The
entity extractors (people/groups/places/dates/equipment/casualties/logistics + enrichment) are
largely INDEPENDENT per chapter. ACTION: evaluate fanning them out concurrently (async/thread/process
pool, or at least decoupling slow network-bound enrichment from the critical extraction path so a
hung Wikipedia lookup can't block equipment/casualties). Consider: bounded concurrency, per-entity
timeouts, enrichment as a deferred/parallel stage. Must preserve ordering invariants (places persist
before equipment resolves place_name->PlaceID) + never-drop/observability. (Raised by owner.)

### Older backlog - HIGH list
- **[HIGH · observability] Silent "fall back to empty" loaders hide omissions (root cause of the config/ + data/ gap).**
  FIXED 2026-11-06 (branch `refactor/pr1-dedup-into-src`, not yet deployed): the ECS image had no `config/` or
  `data/` for weeks (the deployed 2026-11-03 `wwii-pipeline:latest` loaded 0 of 97 equipment aliases, 0 of 6 surname
  priors, no domain blacklist). 14 days of `/ecs/dev-wwii-pipeline` logs held no warning about any of it.
  The Dockerfile now COPYs both dirs. `src/utils/runtime_tables.check_runtime_tables` runs at every phase start
  and emits `OBSERV runtime.tables status=error tables=...` (Slack + email) when a required table is missing or
  empty. `tests/test_dockerfile_runtime_files.py` fails if code reads a table that is unregistered or not copied.
  WHY IT WAS MISSED: observability here is event-driven, and an absent file produces no event. Each loader treats
  "missing" as a legitimate empty table (`except Exception: return {}`, `if not p.is_file(): return {}`), so
  an omission looks exactly like an empty table. No metric tracked how many aliases or priors a run actually used.
  REMAINING:
    - Audit every `return {}` / `return []` fallback in `src/` loaders and make each one say whether the input was
      absent, unreadable or genuinely empty (`observe_warn`). Same class as PATTERN-8, applied to inputs.
    - Consider a per-run "inputs used" rollup (alias count, prior count, blacklist size) so a sudden zero is visible
      even when a file is present but wrong.
    - Learned equipment aliases written in ECS are lost when the task ends (pre-existing; now dockerignored).
- **[MED · data-quality] Follow-ups from the schema-stamp and basedpyright reviews (2026-11-06).**
    - Writes into an UNREGISTERED directory skip schema validation (fail-safe allow) and get the minimum-version stamp.
      Emit one counted, actionable warning when a record is written to a directory that is not a registered entity
      (catches typos and LLM-derived directory names). Callers: 13 of `_resolve_entity_type`, e.g. `batch_parallel`.
    - `locked_json` logs a failed schema stamp at DEBUG only: raise it to a counted warning (alerts must be actionable).
    - Existing `index.json` files written before the fix carry `_schema_version`/`_last_updated` entries. Dev was reset;
      before any prod run, either clean them once or make the index loaders skip keys starting with `_`.
    - basedpyright baseline (`.basedpyright/baseline.json`, 147 findings) was generated on Python 3.14 with
      `pythonVersion = "3.12"`. If the first CI run (3.12 + resolved deps) differs, regenerate it from a 3.12 env.
      Shrink it by fixing findings (77 `reportAttributeAccessIssue`, 37 `reportArgumentType`), never to hide a new one.
    - `grok_client._log_api_response`: the debug "Log preview" block is indented inside the model-deprecation branch, so
      it logs only on a redirect. It was probably meant to sit under the `choices` branch.
    - `nodejs-wheel-binaries` (transitive, unpinned range) comes with basedpyright in CI only; pin it or use hashes.
- **[MED · provenance] Stop shipping HTML/EPUB/DOCX/video books with EMPTY metadata (P2-C1 "slug+meta half"; found 2026-11-06).**
  `phase0_convert` / `phase0_video` / `ocr_merge_handler` write `chapter1-meta.yaml` with `author`, `series`, `chapter_title`,
  `license`, `copyright_date` all `''` and `source_url: "converted from <key>"` (not a URL). The pipeline continues, so every
  record cites an empty author and copyright status is unknown. (Until 2026-11-07 weather also lost EVERY record to it -
  `Missing required book metadata`; fixed: an empty author now alerts once per book and extraction continues.)
  The alert is now actionable (names the fields, the consequence and the S3 file to edit), but an alert on EVERY upload is still
  manual toil. Derive what is deterministic: HTML `<title>` (e.g. "HyperWar: US Army in WWII: Cross Channel Invasion [Chapter 8]"
  gives series + chapter), the upload's `x-amz-meta-source-url` (set by the operator) for `source_url`, and the known licence of
  the source host (ibiblio HyperWar = US Army CMH public domain). Leave `author` empty ONLY when it cannot be derived, and alert
  only for what is still missing.
  (The images/maps=0 consequence is FIXED in code: real `source_url` from S3 metadata / HyperWar derivation, parser
  guard, shared map-vs-image classifier, Phase 3 map vision; verify on the next dev E2E. This item now covers only the
  remaining empty author/series/license/copyright fields.)
- **[MED · cost/IAM] Task role cannot tag consumed sources for cold archive (found 2026-11-07 E2E).**
  Every Phase 0 logs `archive_tag.degraded ... AccessDenied PutObjectTagging`: `cloudformation/iam.yaml` ECSTaskRole
  `S3Access` allows Get/Put/List/Delete but not `s3:PutObjectTagging`, so no source ever gets `archive=cold` and all
  stay on STANDARD. Add `s3:PutObjectTagging` (and Get) to that statement, deploy the IAM stack, confirm the next
  Phase 0 logs `Tagged source ... (cold archive)`.
- **[MED · latency] Phase 1 waits ~9 min for the scheduled backstop after Phase 0 (found 2026-11-07 E2E).**
  Trigger logged `Pipeline busy (phase0-convert active), Phase 1 will run after completion` at 02:38:44; Phase 0 sent its
  completion notification at 02:38:59 while its ECS task was still stopping (stopped 02:39:22), so the trigger still saw
  it active and parked again; only `reconcile-scheduled-backstop` launched Phase 1 at 02:47:39. Fix: the trigger should
  not count the notifying phase's own task as busy on a `phase-complete` event (or the task should notify after it
  has exited / the trigger should retry shortly).
- **[MED · data-loss class] More readers still hard-code `output/source_section/` (found 2026-11-07 E2E; the main bug is FIXED).**
  The event pass writes `output/content/<book>/source_section/`. Phase 2 step 4b, Phase 3 OpenSERP source-section enrichment,
  image->place links, intra-corpus dedup and event provenance now share `src/utils/source_sections.py`. STILL on the old path:
  `phase3_enqueue_openserp.py:42` (`list_files("source_section")` -> enqueues 0) and `openserp_worker._safe_entity_path`
  (accepts only `source_section/...`); and the schema registry `output_glob` consumers `scripts/validate_phase2.py`,
  `src/utils/provenance_integrity.py:236`, `src/utils/referential_integrity.py:130`. Route them through the helper (the
  worker needs a strict `content/<safe-book>/source_section/<ulid>.json` allowance) and add a test per reader.
- **[MED · quality] Other `resp.text` reads may mojibake UTF-8 pages served without a charset (2026-11-07).**
  `requests` decodes such `text/*` responses as ISO-8859-1; Grokipedia did, so no accented name matched (fixed with
  `src/utils/http_text.response_text`). Confirm each site's server charset behaviour, then adopt `response_text`:
  `src/utils/url_hygiene.py` (`fetch_html_text`), `src/enrichment/wayback.py`, `src/extraction/openserp_maps.py`,
  `src/extraction/search_external_maps.py`, `src/url_extractor.py`, `src/enrichment/award_polite_source.py`.
- **[MED · provenance] Carry Phase 1 note provenance into the entity records (schema bumps, do together; 2026-11-07).**
  Phase 1 (src/notes.py) now records each note's `kind` + citing `paragraph_number`, and `Image.note_number` for an image
  linked only from a note (XChannel-8 note 23 "naval command chart"). Downstream drops them: (a) bibliography mentions need
  `paragraph_number` (+ `reference_type` from `kind` in the Grok supplemental path) - bibliography schema bump; (b) the image
  entity needs a `note_number`/cited-in-note field (`src/extraction/images.py`, `images_output.py` is
  `additionalProperties: False`) - images schema bump. Each bump: history snapshot, fingerprint pin, SCHEMA_VERSIONING entry.
- **[MED · security] Scanner baseline (security-scanner MCP + CLI, 2026-11-06). Nothing found in this branch's changes.**
  Branch vs `origin/main` by Checkov CLI: 141 -> 138 failed, 0 new (fixed: both `CKV_AWS_111` scoped-IAM findings and
  `CKV_AWS_79` IMDSv1). Bandit `src/` 73 LOW (B110/B112 swallowed exceptions) + 8 in `lambda_handlers/`, 0 MEDIUM/HIGH. ASH on
  `src/`: 8 HIGH, all false positives (word list flagged as "secret", `secrets.choice` alphabet, SHA-1 with
  `usedforsecurity=False`, static-registry `import_module`, `http://` adapter mount prefix, "token" in log text).
  OPEN, all pre-existing:
    - Grype: the Go tool binaries `tools/search_maps` and `tools/search_media` (tracked in git, built with go1.26.0) carry 3
      Critical / ~40 High stdlib CVEs. They are not copied by any Dockerfile, so local-only; rebuild with a patched Go or stop
      tracking binaries. The `openserp` submodule's Go modules (`brotli` Critical, `x/net`, `klauspost/compress` High) ship in the
      OpenSERP image: bump the submodule (`scripts/check_component_versions.sh`).
    - ~73 swallowed exceptions (B110/B112) conflict with the actionable-alerts rule; fold into the PATTERN-8 audit.
    - cdk-nag/Checkov CloudFormation baseline (~138): Lambda DLQ / VPC / concurrency / env-var KMS, DynamoDB PITR + CMK, SNS/SQS
      encryption, log-group KMS, API Gateway logging/tracing/authorization (`AwsSolutions-APIG4`).
    - Semgrep standalone is not installed on the dev machine (the MCP `scan_directory_with_semgrep` fails); the MCP Checkov scan
      returned a STALE copy of `ocr.yaml`. Use the Checkov CLI as the source of truth.
- **[HIGH · release] Push the local commits and open PR 1 (owner decision pending, 2026-11-06).**
  `main` has 6 unpushed local commits (`12ee5159`..`7e45750b`: pre-deploy fixes, scripts/ reorg, steering, aws/ and
  agent-history removal, endnote doc, separation plan). Branch `refactor/pr1-dedup-into-src` has 4 more on top
  (`f8db5a56` golden test, `9f9e6703` scripts/utils root fix, `27f3fd18` dedup move, `7ac6593d` reclassify fixes).
  Decide: push `main` first and open PR 1 against it (smaller PR diff), or open one PR carrying all 10. Then let CI run
  (it has not run on any of these), and after deploying do the end-to-end check in SRC_SCRIPTS_SEPARATION.md §6.
- **[POLICY · provenance] Provenance-completeness stays NON-FATAL (owner 2026-10-30): escalate LOUD to Slack, do NOT
  fail_stop.** Rationale: many provenance misses are NON-DETERMINISTIC (LLM ULID variance) and a RE-RUN fixes them; a
  fail_stop would halt a book a retry would heal. Reserve hard failure for DETERMINISTIC causes (prompt/code bugs like the
  one above). Action: provenance_integrity enforcement -> wire the SNS/Slack alert (provenance.alert already emits; ensure
  it reaches the notify topic) + a runbook: on a provenance alert, first RE-RUN; if it persists across runs, it's
  deterministic -> hunt the prompt/code bug.
  **STATUS 2026-10-30: Slack escalation is ALREADY LIVE** — provenance_integrity._alert publishes to
  NOTIFICATION_TOPIC_ARN; verified forwarded ('Forwarded to Slack: :warning: DATA QUALITY — provenance anomalies' at
  22:51/23:24/00:19/08:05). Non-fatal confirmed. Remaining: write the operator runbook doc.
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

### Older backlog - MED list
- **[MED · owner 2026-11-11] Books submitted together are processed as separate books.** When more than
  one book is uploaded at the same time, each one gets its own runs (its own Phase 0-3 tasks, Grok
  batches, locks, stats and alerts). Runs are never combined across books. One book's failure, retry,
  purge or rerun must not hold up or touch another book. Check where runs are grouped today (the
  trigger's per-wave key batching, the multi-doc task pool, Grok batch submission) and split them
  per book.
- **[MED · owner 2026-11-11] Provenance validation run for a book - Phase 2 + Phase 3 DONE.**
  `scripts/validate_provenance.py` (matcher: `src/utils/quote_match.py`) runs per book at the end of
  Phase 2 (`ecs_entrypoint._provenance_gate`): quotes in the cited paragraphs / sub-event, sub-event
  links, paragraph locators, note quotes. At the end of Phase 3 - after every merge (dedup
  auto-merge, bibliography reconcile, Dedup Review UI) - it re-runs those and adds P3-B1 (no stub
  bibliography entries), P3-R1 (license / copyright_status / NARA archive reference), P3-A1
  (enriched records name their source) and P3-C1 (every cited note has a bibliography entry).
  Gate mode `provenance_validation.gate` / `PROVENANCE_GATE`: `warn` now; `block` holds Phase 3
  (end of Phase 2) or keeps the book from "done" (end of Phase 3, multi-doc: needs-review).
  Switch to `block` once the extraction defects below are fixed. LIMIT: in serial mode a held
  Phase-3 book is alerted only (there is no doc lifecycle to hold). A Dedup Review UI merge made
  after the book's Phase 3 is validated only by the book's next Phase-3 run.
  Merge review (2026-11-11, fixed): all merges now union mentions by ONE rule
  (`entity_records.merge_mentions`: one mention per sub-event); bibliography no longer collapses
  unnumbered notes (`citation_identity`); the UI merge writes the primary before deleting.
  Still open: the UI people merge repoints only event sub-event refs, not PersonID/PlaceID/GroupID
  held in other records (referential_integrity catches it); a dropped same-sub-event mention's
  alternate quote is not kept, and its MentionID is not redirected (only `maps.PeopleGroupMentionID`
  references a mention id; dates keep their own key `(Sub_eventID, original_text)` with a
  `DateMentionID` redirect in `scripts/merge_dates.py`).
- **[MED · DESIGN · owner 2026-11-08] Book naming: a WORK key + an EDITION key, so duplicate
  titles and editions of one work are told apart.** CONSTRAINT (owner 2026-11-11): slugs are
  IMMUTABLE (`.kiro/steering/architecture-decisions.md`, "Book identity") - this scheme applies to
  NEW uploads only; every existing slug (and so its BookID and EventIDs) is grandfathered, never
  re-keyed. Today the name is the upload folder / file
  stem (`src/ingestion/book_slug.py`); a different work under a taken name is rejected with an
  opaque `{title}-{6 random chars}` suggestion, and another edition of the SAME work (different
  checksum) is treated as a different work. Target: `{work}/{edition}` (e.g.
  `Cross-Channel_Attack/CMH-1993`, `Cross-Channel_Attack/HyperWar-1951`) so editions link (the
  newer is the source of record, others cross-check it - `scripts/crosscheck_pdf_html.py`) and
  every phase's "book" key, S3 layout, figures (`_figures/` belongs to an edition) and locks carry
  both. OWNER RULE for the edition: assume the FIRST edition, unless more than one copyright /
  printing date, or a book plate, shows the copy is a later printing - then the edition is the
  printing that evidence names (e.g. Cross-Channel Attack PDF: "First Printed 1951 - CMH Pub
  7-4-1", "Washington, D.C., 1993" -> the 1993 CMH printing). Suggested names for a true title
  collision should be meaningful (`{title}__{issuer}_{year}`), random only as a last resort.
  Also OPEN: names derived outside intake (`ocr_merge_handler` from an OCR job's input filename)
  bypass the title check.
  - DONE 2026-11-08: duplicate titles are compared NORMALISED (case, punctuation, accents, a
    leading article ignored; `title_uniqueness.normalised_title`); legacy exact-key registry
    entries still honoured.

- **[MED · DESIGN · owner 2026-11-08] A human-review QUEUE of possible duplicates (overlapping
  titles), with copyright dates and author shown.** Today a taken name + different content is
  rejected with a random-suffix rename, and a SERIES-prefixed title is not matched at all
  ("The European Theater of Operations CROSS-CHANNEL ATTACK" normalises to
  `european_theater_of_operations_cross_channel_attack`, "CROSS-CHANNEL ATTACK" to
  `cross_channel_attack`: both would ingest as separate books; a full "United States Army in
  World War II: ...: Cross-Channel Attack" filename is even rejected as `unknown` for its
  colons). Target: route (a) an exact normalised match with different content and (b) a
  containment match (one title's words a whole-word prefix/suffix of the other's, or a known
  series prefix - "United States Army in World War II", "The European Theater of Operations",
  "The Mediterranean Theater of Operations", ...) to a review queue instead of a rename. Each
  entry shows side by side: both titles, AUTHOR(S), COPYRIGHT / PRINTING DATES (highlighted;
  extracted deterministically from the front matter, with page provenance), publisher /
  series, checksums, sizes, page counts - the reviewer decides SAME work (link as editions of
  one work, per the work/edition item), DIFFERENT work (meaningful name), or REPEAT. Same author
  + same earliest copyright date = strong same-work signal. OWNER RULE: when BOTH the copyright
  date AND the author differ, the works are DIFFERENT - decided without a reviewer (name them
  meaningfully). Only the remaining overlaps go to the queue. At SUBMISSION through the web
  interface, show the possible matches (same signals) up front so the submitter can
  disallow / withdraw a duplicate before it is ingested. Alerts to `phase2-complete` (both
  channels). Fits the planned submit/review web interface (§2).
- **[LOW · security · 2026-11-08] Deny writes to `contentrepository/**/_figures/*` for every
  principal but the pipeline role** (IAM / bucket policy). The trigger exempts the system
  namespace's `_figures/` from intake (no AV / OCR: it is pipeline output); contributor
  namespaces are never exempt, but an operator credential could still write there. Any future
  reader (site, CDN) must treat `_figures/` as trusted only because of that deny.
- **[MED · DESIGN · owner 2026-11-08] Maps of a scanned-PDF book: take them from the PDF (its
  numbering is definitive), link them to the text that cites them, Grok later.** Owner rules: maps
  need NO OCR; they must be associated with the text that refers to them; eventual Grok
  submission (Phase 3 analysis, `docs/current/design/maps-target-architecture.md`); where a copy
  exists in more than one source the HIGHER RESOLUTION wins; the PDF's edition rules - its map
  numbering, and whether a printing had extra maps (some green-book printings carried National
  Geographic / Rand McNally maps: the 1951 Cross-Channel Attack's Map I was a Rand McNally world
  map, absent from the 1993 CMH PDF, so HyperWar's map numbers are one higher).
  Measured (Cross-Channel Attack): PDF fold-out plates (PDF pp. 539-562, 24 maps) are embedded at
  150 dpi, 1793x1411 to 2894x1389 px colour; HyperWar's copies are 800-1200 px wide. PDF in-text
  maps 1-6 are 300 dpi bilevel (JBIG2, ~1990x2930 px) vs HyperWar 800 px greyscale -> the PDF wins
  every map here; HyperWar is the fallback where a book has no PDF (record both candidates'
  pixel size and keep the larger, per map). Steps: (1) extract the embedded map images from the
  PDF (no OCR, no Chandra description) with page, PDF map number and title/legend from the plate;
  (2) find each map's references in the corrected text ("Map VII", "See Map 3", with paragraph
  numbers + Sub-eventID per OI3) and write the Phase-1 stub Map JSON + reference footprint; (3)
  Phase 3 (Grok vision) analyses and links them. Chandra's text for the map pages is NOT the
  book's: on 10 of the 24 plates it wrote model-made descriptions ("The map illustrates ...",
  "Key features include", up to 35k chars a page) as plain paragraphs - Phase 0 must not feed
  those to Phase 1/2 as narrative (affects every map-heavy scanned book).
  STATUS 2026-11-08: BUILT as a standalone tool - `src/ingestion/pdf_figures.py` +
  `scripts/extract_pdf_figures.py` (tests: tests/test_pdf_figures.py). Cross-Channel Attack: 124
  figures, all 96 list entries placed (24 plates by printed title, 30 maps, 4 charts, 62
  illustrations + 27 further photos of listed entries), 48 citations attached, 3 HyperWar wins
  (single aerials with more pixels; HyperWar's combined images of multi-photo entries never win).
  Measured correction to the above: photos on ~70 text pages are stored as ONE 150 dpi grey
  full-page image (not 300 dpi), so each picture is cut from the stored image where Chandra's
  crop matches (Pillow template search). OPEN: (a) wire into Phase 0 after Chandra; (b) the
  images must NOT be written under `contentrepository/` - its S3 notification has no suffix
  filter and `trigger_handler` sends every image there to Chandra OCR (cost + loop); use a
  separate prefix (e.g. `figures/{book}/`) and point the promoted markdown's image links at it;
  (c) Phase-1 stub Map JSON from figures.json (+ paragraph numbers / Sub-eventID per OI3);
  (d) Phase 3 Grok submission.
- **[MED · bug · found 2026-11-08 · noisy] `ocr_merge_handler` false "ocr-empty" alerts for Chandra jobs that
  write OUTSIDE `ocr-output/`.** `_book_from_event` derives the book from the job's INPUT path,
  then `merge_ocr_output` always lists `ocr-output/{book}/`; it never reads the job's OUTPUT
  argument. A cross-check / scratch OCR run (`scripts/submit_ocr_job.py`-style job writing to
  `scratch/...`) therefore rejects to `needs-review/ocr-empty/{book}.json` and alerts Slack+email
  once per job - pretty noisy: one Slack + one email per finished chunk (seen: ch. VIII xcheck job, then the 14 per-chapter jobs of the Cross-Channel
  Attack full-book cross-check). FIX: take the output prefix from the container command (the
  second `s3://` argument); if it is not under `ocr-output/`, log `OBSERV ocr_merge.skip
  reason=output_outside_ocr_output` and return without merging or alerting; test both paths.
- **[MED · architecture/gate-coverage] Move pipeline code out of `scripts/` into `src/` (plan: `design/SRC_SCRIPTS_SEPARATION.md`).**
  The pipeline imports 11 modules from `scripts/` (dedup detectors, validators, metadata completion, the metrics Lambda
  handler), and two `src/dedup` modules import `scripts/` themselves. The gate lints only `src/` and `lambda_handlers/`,
  so ~4,700 lines of pipeline code get only black + tests. That gap hides a real bypass:
  `reclassify_military_units` writes `people_groups` entity records with a raw `write_text`, skipping the write guard.
  Plan: 4 PRs (dedup family; validators + metadata + metrics Lambda; an import-direction guard; retire duplicates after
  salvaging their useful logic). Decisions made 2026-11-06: oob_markdown is the canonical Order of Battle parser,
  `pdf_to_markdown.py` keeps its new-book scaffolding under a clearer name with its conversion replaced, generic HTML goes through `text_converters` (+ `web_video` for video pages), and the
  20 complexity-C functions are grandfathered behind a golden-output test, then refactored file by file.
  PROGRESS: PR 1 (dedup family -> `src/dedup/`) on branch `refactor/pr1-dedup-into-src`. Follow-ups it created:
    - Refactor the 18 grandfathered C blocks in `src/dedup/` (D4; `scripts/.radon_inherent` "moved unrefactored"),
      starting with `find_duplicate_people.py` (10). `tests/unit/test_dedup_golden.py` must stay green; remove each
      allowlist entry as its block drops below C.
    - `find_duplicate_places_v2._filter_excluded` and `find_duplicate_groups._filter_excluded` are byte-identical
      (same AST hash): extract one shared helper.
    - Dead code: `find_duplicate_people._check_proximity` has no callers; it and the helpers only it calls
      (`_names_within_distance`, `_get_person_titles`) can be deleted (drop the allowlist entry too).
    - Questionable pinned dedup decisions (golden records them as current behaviour): John Smith grouped with
      Walter Bedell Smith; bare "Marshall" grouped with George Marshall; Panzer Lehr clustered with 2nd Panzer.
    - PR 2: the `metrics` Lambda handler is `scripts.view_metrics.handler`, but `scripts/update_lambdas.sh` never
      packaged `scripts/view_metrics.py` (CI's deploy.yml copies all of `scripts/`, so it works only after a CI deploy).
- **[MED · verify-on-next-run] Confirm four former "known data-quality gaps" are closed in the DATA, not just the code.**
  Code now covers each, but the dev environment was empty on 2026-11-04 (data bucket had 0 objects; the entity
  DynamoDB tables are empty by design), so none could be checked against real records. After the next full run verify:
    - Places: no record left with null/`0.0` lat/lon or empty `country` (geocoding: `places_grok_geocode`, `nominatim_geocode`,
      wired in `phase3_enrich_data._enrich_places_and_geocode`).
    - Images: `description` is populated. Vision captioning (`images._apply_vision_caption`) is opt-in; check its config default.
    - Bibliography: no stub records (`ibid`, bare numbers like `1`/`26`) persisted (`bibliography._is_stub_citation` skips them
      at extraction; it does not clean records already stored).
  Then refresh `DATA_QUALITY_STATUS.md` (dated 2026-10-05; still lists "Places missing coordinates" as open).
- **[MED · storage/cache] Move the external search-result cache from DynamoDB to S3.**
  Owner direction (2026-11-04): DynamoDB is for queues only; caching is fine but belongs in S3. `src/utils/search_cache.py`
  (`_DynamoBackend`, keys `search#<source>#<hash>`) is the target, written from 17 live sites across 14 sources (nara,
  openserp_archive, openserp_verify, openserp_awards, wikipedia, wikipedia_equipment, wikipedia_group, wikipedia_situational,
  grokipedia, grokipedia_url, grokipedia_place, noaa_station, noaa_data, archive_org). The Grok response cache stays in
  DynamoDB (owner decision); this item does not touch it. Design notes for the move:
    - Layout: one object per entry, e.g. `cache/search/<source>/<hash>.json`, with the QUERY stored in the body (today only
      a one-way hash is kept, so entries cannot be audited by name).
    - TTLs differ (30d hit / 7d `NOT_FOUND`): use two prefixes with S3 lifecycle rules, or keep `ttl` in the object and check
      on read. Keep the `TRANSIENT` rule: never persist a transient failure as a negative.
    - Fix two defects while moving: (1) source `grokipedia_place` is written with a URL by `enrich_places._search_grokipedia_place`
      and with page text by `situational_geocode.grokipedia_context`, so the same string collides and returns the wrong payload
      shape: give the extract its own source name; (2) the `grokipedia` source caches the raw search-results HTML
      (`response.text`) with no size cap, and DynamoDB's 400 KB item limit makes oversized writes fail silently (caught, debug log):
      S3 removes the limit, but still decide whether to cache raw HTML at all.
    - Dev reset: a dedicated S3 prefix lets `e2e_reset` clear the cache. Today `search#` items are not part of its sweep (per
      `scripts/README.md`), so a "clean" dev run can see stale `NOT_FOUND` results.
    - Not in this item: `fetch_provenance` (`fetchevent#`) and `url_registry` (`url#`, `excludedomain#`) were judged acceptable
      in DynamoDB but can give unpredictable results in Dev; revisit with the reset behaviour above.
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

### Older backlog - LOW list
- **[LOW · test-hygiene] Tests write into the tracked `logs/processing_summary.json` (found 2026-11-06).**
  `src/extraction/events._write_processing_summary` appends to the cwd-relative `logs/processing_summary.json`, and
  event-extraction tests (for example `tests/test_events_paragraph_range.py`) run it from the repo root, adding
  `{"book": "B", "chapter": "C", ...}` lines on every test run. Fix: an autouse fixture that redirects the summary
  path (or chdirs to tmp_path), plus a gate check that `git status` is clean after pytest.
- **[LOW · sec-hygiene] Upgrade Trivy 0.71.0 -> 0.76.1** on the deploy host — the image scanner engine is 5 minor versions
  behind (DB auto-updates, engine/detection does not). deploy_all.sh now CHECKS this (loud current-vs-latest; hard-fail
  under STRICT_TRIVY_VERSION=1); remaining action = bump the installed binary on the deploy host.
- **[LOW · sec-hygiene] pylint CVE-2026-114608: FIXED by bumping requirements-ci.txt pylint 4.0.5 -> 4.0.9** (the only
  waived CVE; CI-tooling-only, NOT in the runtime image — Trivy on the image = 0 findings). Clears the pip-audit waiver.
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

### SECURITY: AV / content-sanitization policy (owner 2026-11-02) (HIGH)
Full contract:
1. EXEMPT from AV: text, CSV, markdown (no practical virus risk; av_scan.select_binary_keys already
   excludes txt/csv/md/html — verified).
2. HTML: STRIP JAVASCRIPT on save. Current _strip_html_boilerplate removes <script>/<noscript>
   (done) but inline event handlers (onclick/onerror/onload...) + `javascript:` URLs SURVIVE (gap,
   verified). FIX: strip on*= attributes + javascript:/vbscript: hrefs in _strip_html_boilerplate
   (BeautifulSoup). pandoc runs --sandbox already.
3. BINARIES (images, Word .docx, Excel .xlsx, PowerPoint .pptx, PDF): QUARANTINE -> AV scan. A
   POSITIVE finding (or unscannable, fail-closed) STAYS IN QUARANTINE — do NOT auto-delete; decide
   disposition later. (Phase-0 scan_and_gate already quarantines-not-deletes.)
4. POSITIVE finding -> THROW AN ALERT (actionable operator alert; consistent with the existing
   av_scan freeze-submitter hook + OBSERV av.summary status=anomaly; route through the phase2-
   complete SNS -> Slack+email operator path).
5. SCOPE EXTENSION (the actual gap): apply #3+#4 to the PHASE-2/3 DOWNLOAD PATH (map/image bytes via
   fetch_image_bytes/download_map_image) — today AV runs ONLY at Phase-0 S3 ingestion, so downloaded
   binaries bypass quarantine/scan/alert. Now urgent: Step 5b writes image bytes to disk + 5c-ii
   forwards them to Grok vision. Route downloaded binary bytes through quarantine->scan->alert
   before persist/forward.

### TEST: simulate a virus file end-to-end through the AV gate (EICAR) (MED)
Validate the download-path AV flow with the STANDARD EICAR test string (the safe, industry-standard
68-byte test "virus" that every AV incl. ClamAV flags as Eicar-Test-Signature, harmless). Stage an
EICAR file into unscanned/output/maps/files/ and run drain_unscanned -> assert: (1) ClamAV returns
INFECTED; (2) it is MOVED to quarantine/output/maps/files/ (NOT promoted to prod, NOT left in
unscanned/); (3) reject_to_review + freeze fire; (4) OBSERV av.verdict result=infected status=error +
av.phase3_drain quarantined>=1. Also a clean image -> promoted to prod, and (ideally) a truncated/
unscannable -> stays unscanned. Proves the three zones + alert work against a real detection, not
just mocks. (Owner 2026-11-02.)

---

## 1. PHASE 2 — OPEN (current priority)

### 1.0d Phase-2 THIRD RE-REVIEW findings (2026-10-21d) — full doc: docs/archive/2026-11-16/PHASE2_REREVIEW_2026-10-21d.md

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

### 1.0c Phase-2 FRESH RE-REVIEW findings (2026-10-20b) — full doc: docs/archive/2026-11-16/PHASE2_REREVIEW_2026-10-20b.md

A SECOND independent 12-agent re-review (6 slices × code+security) of the merged state after the 1.0b
campaign + PRs #487–#496. NEW/missed gaps (analyzer-invisible; bandit still 0H/0M). OPEN items below in
priority order; fix each via branch→gate→concurrent code+security reviews (both PASS)→CI→merge→sync.
(The DNS-rebinding CRITICAL is DONE — #497 — and archived in `docs/archive/2026-11-17/TODO_COMPLETED.md`.) Ranked:

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

- **[MED · dedup, owner 2026-11-10] Externalize the duplication factors into a config file.** Every
  weight, threshold and word list the dedup detectors score with should live in one config file
  (e.g. `config/dedup.yaml`, or the `dedup:` block of `config.yaml`), with the code holding only
  defaults, so tuning needs no code change or deploy of Python.
  - **People: the existing config is dead.** `config.yaml` `dedup.people.weights`
    (`shared_external_url`, `same_last_name`, `event_overlap_*`, `shared_biographical`,
    `middle_initial_subset`) is loaded into `DedupConfig.weights`, but `find_duplicate_people.py`
    never calls `cfg.weight(...)`: every check returns a literal (name similarity `> 0.7` and
    `× 0.5`, same surname 0.4, shared bio 0.5, shared positions 0.3, substring 0.5, single-name
    0.4/0.6 with `last_sim >= 0.85`, unicode/German variant 0.6, middle-initial 0.5, event overlap
    0.8/1.5 at 0.3/0.5 ratios, title alias 2.0, shared URL 3.0). Editing the YAML changes nothing
    today. Only proximity, rank-proximity, frequency/commonness, surname rarity, shared unit, the
    conflicting-middle veto and the different-surname veto are actually config-driven.
  - Word lists too: the rank/title and suffix sets in `_core_name_tokens`, `_TITLE_ALIASES`.
  - **Equipment** (`find_duplicate_equipment.py`): no config at all (~12 literal thresholds).
    **Places** (`find_duplicate_places_v2.py`): mostly literals. **Groups**: largely configured
    (`_GROUP_DEFAULTS`).
  - Done when: each factor is read from config with a default, the defaults reproduce today's
    scores (a test diffing the duplicate report of a fixture corpus before/after), an invalid
    value falls back to its default with a warning, and the config is documented beside each key.

### 1.5 Phase-2 ingestion polish

- **[LOW · ingestion] `chapterN-meta.yaml` sidecars rejected by the Phase-0 type-gate** → 21
  needs-review markers on the XChannel import. Non-blocking (the `.md` parses, the parser reads meta
  directly), but the type-gate should recognize `.yaml` meta sidecars.

---

## 2. PHASE 3 / ENRICHMENT — OPEN

Phase 3 items of this section moved into the "Phase 3 work order" at the top (2026-11-17);
what is left here is not part of a Phase 3 test group.

### 2.1 OpenSERP redesign (the measured CPU bottleneck — 98.7%)

- **[TODO · UI · owner 2026-11-08] What the site may SHOW depends on the source's copyright
  status (the UI has not been started - remember this when it is).** Copyrighted material will
  be processed too; the difference is only in what a visitor can see of the ORIGINAL:
  PUBLIC DOMAIN and EXPIRED copyright -> the entire body (full text, page images, figures);
  CURRENT copyright -> only a relevant, brief extract around what the visitor is looking at
  (the passage supporting a fact / citation), never the whole work, never full page images.
  Extracted data (events, people, places, citations, provenance) is shown for all. Needs: a
  per-source (and per-figure: a photo can differ from its book, e.g. "Captured German Photos"
  vs "U.S. Army Photos" credits from `figures.json`) `copyright_status` the renderer enforces
  (the records already carry `license` / `copyright_status`); an extract-length rule; and a
  default of "current copyright" (brief extract only) when the status is unknown.

- **[TODO · owner 2026-11-04] OCR-correction rules are per LANGUAGE (and script) - today's are
  English-only.** `src/ingestion/pdf_html_crosscheck` + `pdf_text_corrections` assume an English
  book in Latin script: the homoglyph rule maps Cyrillic/Greek look-alikes ("УТАН") to Latin, the
  confusion table (rn/m, vv/w, l/1, O/0 ...) and the frequency/spacing thresholds are tuned on
  Cross-Channel Attack. A Russian document is Cyrillic by right (the homoglyph rule must NOT run;
  its confusions are Cyrillic ones, e.g. ш/щ, и/й, е/ё), French needs its accents and elisions
  (l', d', qu') kept and its own hyphenation, German its umlauts/ß (and ae/oe/ue transliterations
  such as "Kanalkueste"), compound nouns and suspended hyphens ("Atlantik- und"). Dutch keeps the
  "ij" digraph (an OCR "y"/"ij" confusion is common), its diaeresis (ë, ï), name particles
  ("van", "de", "ter" - part of a person's name, never merged or dropped) and apostrophe forms
  ("'s-Hertogenbosch", "'t"). Japanese has no spaces between words, so the word tokenizer
  (`_WORD`) itself is wrong for it: it needs segmentation (e.g. a morphological analyser),
  kana/kanji look-alike confusions (ロ/口, カ/力, エ/工, ー/一), full-width digits and Latin, and
  possibly vertical text; spacing rules do not apply at all. Needed: a
  document language (from metadata or detected), a per-language rule set (scripts allowed,
  confusion table, accent policy, compound/hyphen rules, tokenizer and segmentation, apostrophes),
  and refusing to auto-correct when the language has no rule set (review only). Mixed-language
  books (English narrative with German quotations/notes, as here) need the rule chosen per
  paragraph or span. ENGLISH is not one rule set either: an American document (the CMH green
  books) and a British one (War Office / Cabinet Office histories, British unit war diaries)
  differ in spelling ("defense"/"defence", "armor"/"armour", "-ize"/"-ise", "center"/"centre",
  "maneuver"/"manoeuvre"), and period usage differs from today's ("co-ordinated", "to-day") - so
  the variety is part of the document's metadata, and a transcription in the OTHER variety must
  never "correct" the original's spelling (today's book-vocabulary rule already prefers the
  book's own forms; a cross-variety transcription would show up as html_error / book_form, not
  be applied). ITALIAN also needs its own rules: accented finals ("città", "perché" - accent
  direction matters), elisions with apostrophes ("dell'", "un'", "l'"), and its double consonants
  as an OCR confusion site.

### 2.2 NARA resolution redesign (correctness)

(All items moved into the Phase 3 work order at the top, 2026-11-17.)

### 2.3 People / places enrichment

(All items moved into the Phase 3 work order at the top, 2026-11-17.)

### 2.4 URL-sourced enrichment + dedup

(All items moved into the Phase 3 work order at the top, 2026-11-17.)


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
  `docs/archive/2026-11-16/design/PHASE2_CONTAINER_SPLIT.md`. The "submit-only" decoupling is DEFEATED by the
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

- **[MED · notes, owner 2026-11-09] Endnotes in OCR'd books.** `chandra_markdown` handles notes printed at the foot
  of each page (`<sup>N</sup>` paragraphs, gathered into one `### Footnotes` section; kind "footnote"). A book that
  collects its notes at the end of each chapter or of the book (a "Notes" / "Endnotes" section in Chandra's output,
  with the numbers restarting or running on) is NOT handled or tested: the section would stay narrative text and
  its markers unmatched. Needs: detect the section, parse its definitions (kind "endnote", the book's own heading as
  the label), match markers across chapters when the numbers run on, and a fixture from a real endnote book. The
  PDF is the defining document for notes: the repeated-number case (first definition kept, the repeat only in
  `postprocess.json`) was accepted as is.
- **[MED · ocr-resilience, found in the 2026-11-09 pre-commit review] OCR merge hardening still open.**
  (a) A Batch job of an EARLIER submission that finishes AFTER a re-OCR's manifest was written produces output
  newer than the manifest and is taken for the new submission's: write chunk output under
  `ocr-output/{book}/{submission}/chunk-…/` so staleness never depends on time. (b) DONE (2026-11-09): a cross-check task that dies hard (OOM, the NAT/credit pre-flight `sys.exit`, a failure to
  start) is relaunched once with 4 GiB by the `CrosscheckStoppedRule` EventBridge rule -> OCR merge Lambda
  (`handle_crosscheck_stopped`), and alerts (`ocr_merge.crosscheck_died`) only if the retry dies too. The
  `claim_stale` warning and the forced re-drive (`ocr_chunk_manifest.redrive_command`) remain the manual path.
  (c) A book OCR'd with no manifest (operator CLI) still merges whatever chunks exist: give
  `scripts/submit_ocr_job.py` the same manifest. (d) `chandra_markdown`: a hyphen at a break is removed when
  the book never prints the compound ("anti-"/"aircraft"); the HyperWar text, when there is a validation URL,
  could settle it.
- **[DONE in template, deploy pending · cost, owner 2026-11-08] Noncurrent-version expiry scoped to
  high-churn prefixes.** The bucket-wide 30-day `ExpireOldVersions` rule is removed from
  `cloudformation/storage.yaml`; old versions now expire only under `tmp/`, `cache/`, `scratch/` (7d),
  `av-test/` (1d), `relocated/`, `manifests/` (30d). Kept forever: `contentrepository/`, `output/`,
  `ocr-output/`, `source/`, `artifacts/`, `dedup/`, `needs-review/`, `person_photos/`. After deploy,
  check noncurrent bytes (S3 Storage Lens / inventory) after a month — `output/` is rewritten every
  run, so its retained versions are the item to watch.

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

- **[HIGH · security] CloudFormation review follow-ups** (`docs/archive/2026-11-16/CFN_SECURITY_REVIEW_2026-10-11.md`).
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
  PARTLY ✅ DONE (code check 2026-11-16): drift (1) keyed-by-Sub-eventID shape is unwrapped (`supplemental._unwrap_keyed_supplemental`, PR #576); drift (2) `reference_number` is now assigned by `supplemental.locate_notes` rather than asked of the model; the drop is counted (`OBSERV supplemental.extract ... dropped=` in `_observe_supplemental_extract`). Drift (3) `classification` vs `content_class` not re-checked.
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

Moved to `docs/archive/2026-11-17/TODO_COMPLETED.md` (2026-11-17), with the M1-M8 multimodal epic,
the "Recently CLOSED" headline list and other done items.

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

## [LOW·dep-advisory] pylint 4.0.5 -> 4.0.9 (CVE-2026-114608), pip-audit (2026-10-27)
Freshly-published advisory on the CI-pinned pylint (requirements-ci.txt: pylint==4.0.5; fix 4.0.9).
Dev-only tool (not shipped in the pipeline image / lambdas). CI (lint.yml) does NOT run pip-audit so
it's not blocking merges; the LOCAL gate.sh pip-audit trips on it. Fix: bump requirements-ci.txt to
pylint==4.0.9 as a standalone dep-maintenance change (not bundled into a feature PR). Unrelated to the
map-as-source work that surfaced it.

## LOW: gate.sh pytest step flakes (64 errors) — pre-existing, env-only
`scripts/gate.sh`'s pytest step reports `1 failed, 64 errors` (tests/unit RuntimeError
cascade) that is NOT reproducible running the identical command directly
(`pytest tests/ -m "not slow and not requires_api" -q --tb=short` → 3394 passed, 0 errors).
Confirmed PRE-EXISTING: reproduces on clean main with all local changes stashed. Likely a
gate-script subshell/env artifact (stale /tmp/gate_tests, config swap, or resource state),
not a real test failure. CI's own test job is the authoritative run. Fix: isolate the gate
pytest step (fresh env, unique tmp, or run via the same invocation CI uses).

## LOW: explicit path-containment assertion at the write-guard sink (defense-in-depth)
Security review of PR #695 noted write_json_with_lock / write_parsed_with_guard rely on callers
passing a safe final path segment (e.g. discovery's [a-z]+ section_id capture) rather than asserting
containment at the sink. Traversal is not currently reachable anywhere, but a future caller deriving
a path segment from untrusted input would escape silently. Harden: assert the resolved target is
under its intended root inside the guard (pipeline-wide, not one site).
