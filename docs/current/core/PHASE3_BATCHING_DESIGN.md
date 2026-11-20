# Phase 3 batching: design (Group 2, drafted 2026-11-20)

Status: DESIGN, no code yet. Owner rule: every Grok submission is batched (batches are cheaper), with
`batch.min_requests` (50) as the only exception: fewer requests are answered live. Reads
[PHASE3_MAP.md §5](PHASE3_MAP.md), [GROK_REQUESTS.md §5](GROK_REQUESTS.md) and
`src/grok_client.py`, `src/utils/batch_api.py`, `ecs_entrypoint.run_submit_only/run_retrieve_only`.

## 1. What exists

- Text requests are batch-aware: `GrokClient(batch_mode=True)` queues a cache miss as a `BatchRequest`
  (`request_id` = cache key) and raises `BatchModeCollecting`. `submit_batch` sends them; fewer than 50 are
  answered live (`_answer_small_batch_live`). Results are aligned by request id (`align_by_request_id`),
  with `desync_possible` as a guard.
- Phase 2 hands a submitted batch to a poller (`run_submit_only`, the job queue, `run_retrieve_only`), so
  no ECS task waits hours for xAI. Both are generic over `phase_script`.
- Phase 3 `--batch` does one collect pass, one submit, then `_run_batch_rerun` over six enrichers
  (PHASE3_MAP §5). Nothing in production uses it (`batch.phase3: false`).

## 2. What is missing (verified in code)

| # | Gap | Where |
|---|---|---|
| a | The three vision methods are never batch-aware: they fetch the image and call the API live. | `grok_client.extract_json_with_image` (URL), `extract_json_with_image_base64`; callers `map_link_source._vision_depicted`, `media_classify.vision_is_map` (via `ImageCaptioner.caption`), `unit_patch._vision_is_patch`, `equipment._verify_media_with_vision`, `search_external_maps._verify_single_image` |
| b | One re-run round only. Cascades need several: `bibliography_resolve` asks NARA verify, then OpenSERP verify, then URL content verify, then LLM search, each depending on the previous answer. | `_run_batch_rerun` |
| c | The re-run skips geocode, OpenSERP, document media and NOAA, and `_verify_result` caches `REJECT` for 90 days when it only caught `BatchModeCollecting` (TODO gap 5). | PHASE3_MAP §5 |
| d | Live-only text calls with no collect path: `link_relevancy._grok_link_relevant`, `image_place_link._grok_is_terrain`, `hill_geocode._grok_locate`, `places_grok_geocode.geocode_place`. They may already queue; each must be checked for the "collecting is not a verdict" rule. | step table |
| e | `run_submit_only` / `run_retrieve_only` do not call `_claim_enrich_attempt`, so the Phase 3 retry cap would not apply. | `ecs_entrypoint.py:5413, 6030` |
| f | 1.7 identity: Phase 3 requests carry no RequestID (and no BookID where about one document). | `request_identity.BOOK_BOUND` |

## 3. Design

### 3.1 Rounds (fixes b, c, d)

Phase 3 under `--batch` is a loop of passes over the SAME step list:

1. Pass N runs every step. A cache miss queues and raises `BatchModeCollecting`.
2. **Rule for every step:** `BatchModeCollecting` means "not answered yet". The step must not stamp the
   record, must not cache a negative, and must not count the item as done. A shared helper
   (`phase3_rounds.pending(...)`) is the one place that catches it, so no step keeps its own `except`.
3. At the end of the pass: 0 queued means done. Fewer than `batch.min_requests` are answered live and the
   next pass starts at once. Otherwise submit, hand the batch to the poller (`run_submit_only`), exit. The
   poller launches `run_retrieve_only`, which loads the answers into the cache and starts pass N+1.
4. A cap, `batch.phase3_max_rounds` (default 6): past it the run stops with an alert naming the steps that
   still had queued requests. Each pass logs `OBSERV phase3.round round=N queued=M by_kind=...`.

All steps are already stamp-driven and idempotent, so repeating the whole list is safe and no step list
needs to be maintained per round (the current six-enricher list is what drifts).

### 3.2 Vision (fixes a)

- `BatchRequest` gains `kind` (`"text"` default, `"json"`, `"vision_json"`) so retrieval knows how to store
  the answer. Today a batch answer is stored as the completion text. A vision method caches the parsed JSON
  under its own key, so the retrieve step must parse (same `_sanitize_json_response` and
  `_parse_image_json`) and write the same key the live method reads.
- In batch mode a vision method, on a cache miss, fetches the image (same SSRF-safe
  `fetch_image_bytes_with_reason`), builds the same messages, queues the request, raises
  `BatchModeCollecting`. A fetch failure is NOT queued (it is the same error as live).
- V1 (owner 2026-11-17) confirmed xAI Batch answers vision requests (1.3 MB image, about 2.5 h).
- Open: batch file size. An image goes into the JSONL as a base64 data URL. Images are downscaled before
  they are sent (to be measured against the V1 result), so a file of N images stays under xAI's limit.
  A request that is still too large is answered live, counted and logged.

### 3.3 Identity, task 1.7 (fixes f)

Two kinds of protection already exist: the result is aligned by request id, and `desync_possible`
rejects a positional mix-up. The echo (BookID + RequestID inside the answer) adds proof for kinds whose
answer is a JSON object. Proposal:

- JSON-object kinds about ONE document get the full stamp (BookID + RequestID): `in_doc_map_vision`,
  `people_group_source_recheck`. They join `BOOK_BOUND`.
- Cross-book kinds (`group_enrichment`, `place_enrichment`, `bibliography_verify` ...) have no book: a
  RequestID-only echo, when the answer is a JSON object.
- One-word answers (`openserp_verify` YES/NO, link relevance, terrain gate, map-or-photo) cannot carry an
  echo without turning them into JSON. They rely on alignment by request id. Changing them costs a
  re-ask of every cached answer (the prompt is part of the key) for a small gain.
- Cost: each cached answer of a kind that changes is asked once more. Count first: the production cache
  table (`dev-wwii-api-cache`, 33,428 rows on 2026-11-20) holds NO Phase 3 kinds at the moment
  (`group_enrichment`, `place_enrichment`, `bibliography_verify`, `openserp_verify` ... all absent: it was
  restored to the Phase 2 snapshot). The count comes from the Group 0 baseline run, which fills them.

### 3.4 Attempt counting (fixes e)

`run_submit_only` and `run_retrieve_only` call `_claim_enrich_attempt` once per round-trip, so the retry cap
applies to batch runs. `batch.phase3_max_rounds` bounds the rounds inside one attempt.

### 3.5 Validation pass (owner 2026-11-18)

A script, `scripts/validate_batch_answers.py`: for every Phase 2 cached answer written before 2026-11-11,
re-derive the request id from the stored prompt and compare with the key. Identity-checked kinds are caught
on read already; the script reports the others by kind and count and can evict the mismatches. Read only
unless `--evict` is given. It needs the production cache, so it runs after you approve access (a table scan
of about 33k rows costs a fraction of a cent).

## 4. Order of work (each commit gated and reviewed)

1. 3.2 vision in batch mode (client + retrieve) and the `kind` field, with tests on a fake xAI.
2. 3.1 the rounds loop and the `BatchModeCollecting` helper, then move every step onto it (c, d), including
   the `REJECT` cache fix.
3. 3.4 attempt counting.
4. 3.3 identity (after the count exists).
5. 3.5 the validation script.

**Test (from the TODO):** restore, run `--batch`: same verdicts as the baseline, fewer live requests,
under-50 answered live, rounds logged. Needs your approval to deploy and for the Grok spend.

## 5. Questions for the owner

1. Rounds are capped at 6 (`batch.phase3_max_rounds`). Right, or higher?
2. A request that is too large to batch (after downscaling) is answered live and logged. Acceptable as the
   only exception besides the under-50 rule?
3. Identity: the 3.3 split (full stamp for document-bound JSON kinds, RequestID-only for other JSON kinds,
   alignment only for one-word answers). Agreed?
4. Image downscaling before a batch changes what the model sees, and so the verdicts, versus the live
   baseline. Acceptable, or should images go at full size and the over-limit ones live?
