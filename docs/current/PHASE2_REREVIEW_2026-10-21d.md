# Phase-2 THIRD Re-Review — 2026-10-21 (round d)

Third independent security+code review at `main` a934699b, by 14 parallel sub-agents (7 slices ×
code+security), independent of §1.0b/§1.0c (PRs #475–#513). Baseline: bandit 0H/0M/82L, radon 0 D+
(130 C+ grandfathered), 2527 tests. Tasked to find what BOTH prior rounds MISSED + regressions/
incomplete fixes + bugs in the largely-unreviewed areas (ingestion/Phase-0, schemas, dedup, concurrency/
orchestration, lambda handlers). "Corroborated" = found by >1 agent.

## CRITICAL (dedup reference-rebase — 3 distinct broken paths, one root theme: merge deletes the secondary entity file but fails to rebase its references)

- **[CRITICAL] people_groups auto-merge uses the WRONG id_field.** ecs_entrypoint._auto_merge_entity_type
  drives ("people_groups","PeopleGroupID"), but a group RECORD's PK is `GroupID` (PeopleGroupID is only a
  referrer-side alias). merge_generic→_merge_one_secondary reads old/new id as "" → the rebase guard is
  False → update_event_refs NEVER runs, yet the secondary file is still unlink()'d → mass dangling GroupID
  refs in every event sub-event groups[] + equipment/casualties/people on every group auto-merge. Fix: pass
  the real PK (GroupID).
- **[CRITICAL] merge_generic passes the PK FIELD-NAME as the sub-event ref_key.** _merge_one_secondary calls
  update_event_refs(..., id_field) i.e. "PlaceID"/"EquipmentID"/"PeopleGroupID", but event sub-events store
  refs under plural lowercase keys (places/people/groups/equipment). So se.get("PlaceID") is always empty →
  sub-event ref arrays are NEVER repointed for ANY generic entity (places/equipment/groups) on the ecs
  auto-merge path (the people path + the batch scripts pass literal plural keys, so they're OK). Fix: map
  id_field→plural sub-event key + derive ids from the record's real PK.
- **[CRITICAL] Lambda dedup-UI merge of non-people entities rebases NO references.** dedup_ui_handler
  ._merge_generic_files unions mentions + deletes secondary but never calls update_event_refs → every
  human-approved places/groups/equipment UI merge orphans all refs. Also _merge_people_files runs do_merge
  on a tmpdir containing only output/people/ so update_event_refs rglob finds no event files → people UI
  merges don't rebase either. Fix: download the referencing corpus or record (old→new) + rebase centrally.

## HIGH

- **[HIGH · corroborated: sec_concurrency_orch] mdreview_ui_handler path traversal → arbitrary S3 write /
  content injection.** _save_snippet/_get_page read `book` from the payload, check only truthiness, then
  build `ocr-output/{book}/reviewed/pN.md` and put_object — an authorized reviewer can POST
  book="../contentrepository/Book/chapter1/chapter1-content" to inject attacker markdown into Phase-1
  parse or overwrite arbitrary .md objects. The _SAFE_TITLE_RE hardening added to trigger_handler was NOT
  propagated here. Fix: validate `book` with the shared safe-token regex in both _save_snippet + _get_page.
- **[HIGH · corroborated: sec_concurrency_orch] dedup_gate_handler trusts SNS Message with NO topic-ARN
  validation → review-gate bypass.** The handler json.loads the SNS Message and, on dedup_complete,
  fan-out-ungates Phase 3 (bypassing human dedup review) with zero check the record came from our own
  DEDUP_COMPLETE topic. Port trigger_handler._trusted_topic_name; drop foreign-source records.
- **[HIGH · corroborated: sec_maps_supplemental2 + sec_entity_extractors2 (latent)] unit_patch._download
  DNS-rebind SSRF.** _safe_url vets the host then _vision_is_patch (multi-second) then _download does a
  PLAIN re-resolving get_session().get(url) — not pinned to the vetted IP — storing the bytes as the unit
  patch master. OpenSERP candidate URLs are attacker-influenceable. Route _download through
  images.fetch_image_bytes / _pinned_get (resolve+pin at fetch time). (source_retrieval._download_to_quarantine
  has the same gap but is dead/test-only — fix together.)
- **[HIGH · code_maps_supplemental2] Supplemental enrichment stores UNVERIFIED urls (approved≠saved class,
  no gate).** supplemental._enrich_material writes sequential_search's FIRST hit into resource_urls with no
  content-match; validate_supplemental_urls only checks HTTP 200 → a wrong-but-live page is stored as the
  authoritative, "validated" source (silent wrong-source attribution). Route results through the Grok verify
  gate bibliography_resolver uses, or make validate_url do a content match.
- **[HIGH · corroborated: code_hotspot_regression + code_schemas_dedup + sec_schemas_dedup] locked_json
  atomic-write breaks the cross-process flock + create-path truncate-before-lock.** (a) save→_atomic_write_json
  os.replace commits a NEW inode nobody holds a lock on → the flock no longer protects the committed bytes →
  the lost-update race the lock existed to prevent reappears (regression from the #1.0c atomic-write change).
  (b) the create branch opens "w+" (truncates) BEFORE acquiring the flock → a TOCTOU that can zero a
  just-written file. Fix: flock a stable sidecar lockfile that is never renamed (preserves atomicity + mutex);
  open create-path "a+"/O_CREAT-no-TRUNC, lock, then seek+read.
- **[HIGH · code_concurrency_orch] ecs.run_task failures unchecked + retrieve-only crash/SIGTERM stalls.**
  (H1) run_task's failures[] (common on FARGATE_SPOT) is never checked → a placement failure holds the
  lock/retrieve-claim to TTL with no task running (trigger _run_task 2h; _trigger_retrieve 6h, counted
  "complete"). (H2) a crashed/spot-reclaimed --retrieve-only task leaves job="ready" + retrieve#claim held →
  ~6h stall before recovery, paid batch results un-retrieved. (H3) _handle_sigterm's emergency sync + lock/
  claim release is a NO-OP for --retrieve-only (run_retrieve_only never sets _current_phase_script) → spot
  reclaim loses in-task entities + leaks the claim. Fix: check run_task failures + release the claim; scope
  the retrieve-claim TTL / heartbeat-release; set _current_phase_script in run_retrieve_only + release claim
  in the SIGTERM/finally path.

## WARNING

- **[WARNING · corroborated: code_hotspot_regression] grok_client batch count-mismatch discards 100% with
  ZERO retry.** _map_ordered_results all-fail on a len mismatch → _retry_failed_batch sees 100%>20% → skips
  retry ("systemic") → every event in the batch extracts 0, recoverable only on a full rerun. A single
  dropped/extra page from the results API trips it. Fix: on a count mismatch route to real-time retry
  (bypass the 20% systemic guard) or page-reconcile first.
- **[WARNING · code_hotspot_regression] search_cache._DynamoBackend.get treats a transient Dynamo error as a
  MISS** → a Dynamo read blip → re-fetch → if genuine miss, 7d negative suppression (symmetric hole to the
  #1.0c TRANSIENT fix, on the READ side). Return a TRANSIENT/unknown signal on read error; don't negative-cache.
- **[WARNING · sec_hotspot_regression] wayback _get_with_courtesy re-resolving GET (DNS-rebind pin missing)** —
  fetch_archived vets arch_host then _get_with_courtesy does a plain requests.get that re-resolves at connect;
  allow_redirects=False (good) but no pin. Route through _pinned_get (reuse the vetted IPs resolved_host_is_safe
  already returns). (Same pin-gap family as unit_patch/source_retrieval — fix together.)
- **[WARNING · code_concurrency_orch] credit reservation leaked on batch failure** (AWS submit→poller path:
  run_submit_only reserves but the poller never releases on _mark_failed/stuck) → credit#spend saturates →
  silent pipeline halt when cost.credit_gating is on. Record est_cost on the batch_job# item; release on
  terminal-fail.
- **[WARNING · code_concurrency_orch + sec_concurrency_orch] dedup merge not crash-atomic** — secondary
  unlink + event-ref repoint happen BEFORE the merged primary is written/validated → crash or a blocked
  write → lost mentions or dangling refs. Write+validate the primary FIRST, then delete+repoint.
- **[WARNING · code_concurrency_orch] non-idempotent OCR chunk submission** — _submit_ocr submits N chunks in
  a loop; an exception partway deletes the claim + re-raises → retry resubmits ALL chunks → duplicate GPU jobs.
  Make per-chunk submission idempotent (skip chunks whose output prefix exists / per-chunk claim).
- **[WARNING · code_concurrency_orch] failed phase3 leaks its lock** (run_phase failure path skips phase3
  _remove_lock; success-path release is inside _post_process which a failure doesn't reach) → lock held to 2h
  TTL. + run_phase terminal cleanup (lock + NAT-lease release) not in a finally → an uncaught exception in
  _final_sync/_post_process leaks them. Wrap in finally.
- **[WARNING · sec_concurrency_orch] batch_poller unvalidated `book` + no event-source auth** — action=submit
  → BOOK_NAME=event.get("book") unvalidated flows to S3 prefix / DynamoDB key / --book / the per-title cache
  rmtree target; validate with _SAFE_TITLE_RE (mirror trigger_handler). + EcsClamavScanner AV run-id uses
  builtin hash() (per-process-salted, %100000) → result-key collision could read another run's verdict; use
  uuid4/sha256.
- **[WARNING · code_schemas_dedup] _write_entity_guarded infers "blocked" from mtime** instead of the
  write_json_with_lock bool return → spurious "BLOCKED" on a coarse-mtime fs; use the return value.
- **[WARNING · code_schemas_dedup] merges dedup mentions by Sub_eventID only** → distinct per-sub-event
  mentions (different MentionID/paragraph/role) collapsed on merge (junction data loss). Dedup by MentionID
  where present.
- **[WARNING · sec_schemas_dedup] dedup exclusions Dynamo key is '#'-delimited but names aren't '#'-sanitized**
  → a name with '#' yields >3 '#', _load_name_dynamo (len==4 guard) silently drops the "not-duplicate"
  decision → the pair is re-proposed/re-merged. Sanitize '#' or store names as attributes.
- **[WARNING · sec_schemas_dedup] people._save_person_file omits entity="people"** → latent silent-downgrade
  (correct only while people==the min version). Pass entity="people".
- **[WARNING · code_ingestion_phase0] STATISTICS table/text merge drops rows by metric alone** (drop set keyed
  by metric, upsert keyed by (category,metric)) → a valued "Total" in one category deletes an empty "Total"
  shell in another → silent loss of real review-flagged rows. Key the drop set by (category,metric).
- **[WARNING · code_ingestion_phase0] campaigns table column misread under rowspan** → _campaign_cells does no
  rowspan expansion so cells[col] shifts left on a carried-down row → real campaign silently dropped. Expand
  rowspan/colspan into a grid (as command_staff does) before indexing.
- **[WARNING · code_ingestion_phase0] detect_scanned samples the FIRST page of each block** (front-matter bias)
  → a mixed digital-title + scanned-body PDF can misclassify → scanned body routed to pymupdf4llm (garbled).
  Sample mid-block indices.
- **[WARNING · HIGH per sec_ingestion_phase0] zip/rar member extracted with NO decompression cap (zip-bomb)**
  — prestage._extract_member does out.write(src.read()) with no per-member/aggregate uncompressed-size budget
  → a crafted archive OOMs/disk-exhausts Phase-0 (the AV gate scans the container, not compression ratio).
  Stream-copy with a running byte cap; check info.file_size. + extracted members BYPASS the AV gate (routed
  straight to fitz/pandoc/ffmpeg unscanned) → re-scan expanded members. + basename-collision silent overwrite
  on flatten → disambiguate.
- **[WARNING · code_entity_extractors2] supplemental_info_pipeline.process_supplemental_information iterates a
  dict as a list** (`for entry in data` on the phase1 endnotes/footnotes DICT → entry.get on a str →
  AttributeError, no guard) → the supplemental-info tool crashes / processes 0. Normalize the input shape.
- **[WARNING · code_entity_extractors2] equipment merge hard-indexes existing["event_mentions"] / variant_name
  on persisted (legacy) data** → KeyError swallowed by the outer try → the WHOLE merge aborts + the new
  mention is silently dropped (same legacy-KeyError class as weather/places). Use setdefault / .get().
- **[WARNING · sec_entity_extractors2] equipment/grok_search_maps mutate the PROCESS-GLOBAL Image.MAX_IMAGE_PIXELS
  under ThreadPoolExecutor(3)** → thread A's finally restores the 89.5M default mid-decode in thread B → B's
  bomb cap weakened to the default. Guard the save/set/decode/restore with a lock, or check width*height per
  decode without touching the global.

## NIT (batch when cheap)

- events.py retry-success cache write uses a 2-arg key never read back (orphaned); _write_processing_summary
  hard-indexes paragraphs[0]/[-1] (IndexError on empty). people_groups enrichment dropped on the merge path
  (wasteful re-fetch). grok_client _validate_prompt: (a) raises on any {lowercase} token in the RENDERED prompt
  → untrusted source text containing "{note}"/"{ibid}" aborts extraction (content-driven DoS) — scope the
  check to the TEMPLATE; (b) quadratic-ReDoS `\s*\n\s*\n\s*(?:Return|$)` over source text — anchor with [ \t];
  (c) detector regex [a-z_]+ weaker than the substituter [A-Za-z_][A-Za-z0-9_]*. json_validator/custom_validators
  ID-field heuristic disagree ("ID" substring vs endswith) + _fix_invalid_ulids silently randomizes any failing
  *ID (latent corruption if a non-ULID *ID field is ever routed through validate_json) — drive off the schema.
  search_cache._make_key truncates sha256 to 64 bits. result_dedup docstring says list-union but keeps-first.
  bibliography _aggregate_findings double-appends the primary url (dedup-guard comment false) — add the
  not-in guard. supplemental mentioned-people/orgs unbounded substring match (word-boundary it). images.py
  URL-less offline illustrations never deduped → re-run duplicates. maps.py marks event processed BEFORE
  extracting its maps → permanent drop on error. groups_output event_mentions lacks additionalProperties:false.
  nat_manager concurrent-create loser returns "ready" without verifying. award_source_pages ext from
  Content-Type (backstopped). HTML magic-byte sniff misses leading-whitespace/BOM. pandoc output no size cap.
  dead code: ecs_entrypoint._acquire_lock, external_maps.main arg order.
