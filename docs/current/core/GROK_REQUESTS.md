# Grok requests: every place the code asks Grok

Verified against code on 2026-11-17 (main 0dac86fa). Method: an AST scan of `src/`, `lambda_handlers/`,
`ecs_modules/`, `phase*.py`, `ecs_entrypoint.py` and `openserp_worker.py` for every call to
`chat_completion`, `extract_json`, `extract_structured`, `extract_json_with_image` and
`extract_json_with_image_base64` (plus `submit_batch`), then a read of each call site and of the
`try/except` around it. One indirect call exists: `batch_parallel._batch_extract` passes
`grok_client.extract_json` to an executor (`batch_parallel.py:790`). No other GrokClient method is called
outside `src/grok_client.py`. Scripts under `scripts/` are listed by name only (section 8). Line numbers are
call sites and move; the function name is the stable key.

Companion docs: [PHASE3_MAP.md](PHASE3_MAP.md) (what each Phase 3 step does and when),
[batch_processing](../features/batch_processing/README.md) (Phase 2 batch rounds),
`docs/current/features/*` (per feature).

Contents: 1 Client mechanics · 2 Phase 0 ingestion · 3 Phase 1 · 4 Phase 2 · 5 Phase 3 · 6 Lambdas and
entrypoint · 7 Unused request code · 8 Scripts · 9 Cost notes and limits of this inventory.

Column key: **Type** is the client method (chat = `chat_completion`, json = `extract_json`,
struct = `extract_structured`, vision = `extract_json_with_image[_base64]`). **Batch-aware** means the call
site re-raises `BatchModeCollecting`, so the request can be queued for the Batch API; other sites are
live-only or leave the choice to the caller.

Vision (since 2026-11-20, Group 2): `extract_json_with_image[_base64]` queue a cache miss in batch mode like any
text request (`GrokClient._vision_json`; the image goes into the request as a data URL). The batch caches the
answer as text under the request's key and the vision method parses it on read (an unparseable one is evicted).
The direct callers that swallowed every exception now re-raise `BatchModeCollecting` (`map_link_source`,
`image_captioner`, `unit_patch`, `chart_source_adapter`, `grok_search_maps`, `search_external_maps`,
`video_vision`; `equipment` already did); the steps above
them are audited in the rounds work (design: PHASE3_BATCHING_DESIGN.md).

---

## 1. Client mechanics (`src/grok_client.py`)

- **Endpoint and model.** `api.grok.base_url` (overridable by env `GROK_API_BASE_URL`), default
  `https://api.x.ai/v1/chat/completions`. Default model `grok_models.default_model()`: env `GROK_MODEL`, else
  `api.grok.model` (`grok-4.3`). `api.grok.model_map` (cache type to model) is `{}` in `config.yaml`, so every
  request uses the default (`_get_model`, `grok_client.py:561`).
- **Output cap and timeouts.** `max_tokens = 131072` (`batch_api.GROK_MAX_TOKENS`, `batch_api.py:20`), no
  streaming. `GrokClient.timeout = 600` s (`grok_client.py:479`) is both the HTTP read timeout and the hard
  wall-clock deadline. `api.grok.timeout: 60` in `config.yaml` is not read. `api.grok.max_retries` is 3.
- **Methods.** `chat_completion` (text in, text out); `extract_json` (text in, JSON out, strips fences, repairs,
  re-asks on truncation or bad JSON); `extract_structured` (`extract_json` with a schema, default temperature
  0.1); `extract_json_with_image` (image URL) and `extract_json_with_image_base64` (bytes). Default temperature
  0.1.
- **Rate limit.** One process-wide minimum-interval limiter, `60 / calls_per_minute` seconds between calls
  (`_RateLimiter`, line 326), `api.calls_per_minute` = 20. Under multi-doc concurrency the rate is divided by
  the pool ceiling (`_effective_calls_per_minute`, line 300; env `GROK_RATE_POOL_SIZE`). A 429 sets a backoff
  until `Retry-After`. At 20 calls per minute this limit, not the OpenSERP 5 s sleep, bounds verification-heavy
  steps.
- **Cache backends.** Local disk (`DiskCacheBackend`, under the cache dir; Phase 3 default `cache/grok_cache`)
  or, when `aws.enabled`, DynamoDB (`DynamoCacheBackend`, table `aws.cache_table`, TTL `aws.cache_ttl_days` = 90,
  preloaded into memory at start). Key = hash of prompt, system prompt, temperature and model
  (`cache_key_for`), unless the caller passes a stable `cache_key` (supplemental, equipment broad and refine, and
  others, so a batch submit and its re-run hit the same entry). A concurrent identical request is single-flighted.
  `GROK_CACHE_DISABLED=1` turns cache reads and writes off, including live answers to batch requests.
- **Cache scope.** `BOOK_CACHE_TYPES` (`events`, `weather`, `equipment`, `logistics`, `casualties`,
  `supplemental`, `supplemental_search`, `supplemental_advanced`) are stored per book under
  `books/<book>/<type>` when `current_book` is set; all other types are global.
- **Request identity** (`src/utils/request_identity`). Only `extract_json` (and `extract_structured` through it)
  stamps and verifies identity, and only for `BOOK_BOUND` cache types when a book is in scope
  (`current_book`, or the `book=` argument). `BOOK_BOUND` = `events`, `dates`, `places`, `people`, `peoplegroups`,
  `weather`, `logistics`, `casualties`, `people_groups`, `equipment_broad`, `equipment_refine`, `supplemental`,
  `source_section`. The prompt gets an "ANSWER IDENTITY" block with the BookID and a RequestID (a 16-hex hash of
  cache type, system prompt and prompt) and the JSON answer must echo both, live or cached. A `mismatch` is
  evicted and asked once more, then raises `GrokAPIError`. A `missing` echo is asked once more and then accepted
  with a warning. A non-object answer is accepted with a warning.
  **Phase 3 (task 1.7, 2026-11-20).** The JSON-object kinds about no single book carry their RequestID alone
  (`request_identity.REQUEST_BOUND`: `group_enrichment`, `place_enrichment`, `equipment_disambiguation`,
  `license_check`, `openserp_verify`, `people_group_source_recheck`, `equipment_source_recheck`, and the vision kinds
  `unit_patch_vision`, `vision_verification`, `external_maps_verification`); `in_doc_map_vision` (`DOC_BOUND`) also
  carries the document's BookID (`map_link_source` runs each document under `book_scope`). A request-only kind whose
  answer does not echo is accepted at once with one warning (no second paid call) and kept stamped; a mismatch
  is evicted and raises as above. Vision answers are proven in `GrokClient._vision_proof`. One-word and free-text
  answers (`bibliography_verify`, link relevancy, terrain, `default` vision captions) cannot carry an echo: their proof
  is the request id the batch returns with each result, and Phase 3 refuses to pair answers by position when none
  of the ids is ours (`batch_api.align_by_request_id(strict=True)`, alert `batch.align_positional_refused`; the
  requests are asked again next round, never answered live). Each batch's metrics file records its request ids, round,
  submit time and the models that answered, so `scripts/trace_grok_answer.py <request_id>` names the batch of any
  cached answer (`src/utils/answer_provenance`). The records an answer was used for do not carry the batch id.
- **Batch collection.** With `batch_mode=True` a cache miss is queued as a `BatchRequest` and the call raises
  `BatchModeCollecting` (`_queue_batch_request`, line 1710), which derives from `Exception`. `submit_batch`
  (line 750) first asks `_answer_without_batch`: nothing queued returns None; fewer than `batch.min_requests`
  (50; env `BATCH_MIN_REQUESTS`; 0 = always batch; `batch_api.batch_min_requests`) are answered live,
  one by one (`_answer_small_batch_live`, line 1269, `OBSERV batch.answered_live`), cached under their request
  id and returned as a `live-<n>reqs-<ts>` id so the caller re-runs against the cache. Otherwise the credit gate
  reserves the estimated cost, the JSONL is uploaded to the xAI Batch API (50% price), and
  the answers are validated (finish reason, JSON parse), cached, and failures under 20% retried live. If the
  answers are not in by `max_wait_minutes` the batch is handed off to the poller Lambda and `BatchPending` is
  raised.
- **Batch usage by phase.** Phase 2 core extraction runs in batch rounds (`batch.phase2: true`, `max_rounds` 8,
  `in_task_wait_minutes` 5) in `phase2_extract.py:1040-1093`; after the rounds it sets `batch_mode = False` for
  the retry and post-extraction steps. Phase 3 is live (`batch.phase3: false`); with `--batch` it collects,
  submits once and re-runs six enrichers ([PHASE3_MAP.md](PHASE3_MAP.md) section 5).
- **Model guard** (`src/utils/grok_models`). `EP._preflight_credit_check` (`ecs_entrypoint.py:799`) makes a
  1-token request, aborts on HTTP 402, 403-with-"credit" or 401, then checks that every configured model
  (default plus `model_map` values) is both listed by xAI and answers as itself (a retired model may be
  silently redirected). A failure raises `GrokModelUnavailable`, a `BaseException`, with an
  `OBSERV grok.model_unavailable` operator alert, and the task exits 1. The same check runs on each response
  (`check_served`) and each batch (`check_batch_served`).
- **Failure posture.** Retries and truncation repair are inside the client; a transient failure is never cached
  as an answer. Each call site chooses fail-closed (an error means reject) or fail-safe (an error means leave the
  record unstamped so the next run retries); the tables state which.

---

## 2. Phase 0 ingestion

All five are live requests made by the import tasks (`phase0_convert.py`, `phase0_ingest.py`,
`phase0_video.py`, `phase0_ocr_crosscheck.py`). NAT is optional for them (`_ensure_nat_if_needed`, `optional`).

| Request | File:line | Purpose | Type, cache type, prompt | Gate | Failure mode |
|---|---|---|---|---|---|
| Language detection | `ingestion/translation.py:176` `detect_language` | Detect the source language from the document's first characters | chat, `translation`, `language_detect.yaml`, temp 0.0 | `ingestion.translate` (`translation_enabled`, on by default) | no handler at the call; the caller decides |
| Translation | `ingestion/translation.py:237` `translate_markdown` | English working text; original kept | chat, `translation`, `translation.yaml`, temp 0.1 | same; used by `phase0_convert.py:319`, `phase0_ingest.py:388`, `phase0_video.py:392`, `phase0_ocr_crosscheck.py:50` and, in Phase 2, `enrichment/award_sources.py:504` | same |
| Book metadata | `ingestion/book_metadata.py:292` `_ask` | Title, author, series, dates from the opening text, for `SourceMetadata` | json, `book_metadata`, `book_metadata.yaml`, temp 0.1 | `book_metadata.enabled()`; called from `phase0_convert.py:210`, `phase0_video.py:308`, `ingestion/ocr_promote.py:93` and Phase 2 `_complete_metadata` (`phase2_extract.py:267`) | fail-safe at the caller: one try, the import goes on and a metadata-gap marker records what is missing |
| Markdown correction | `ingestion/md_correction.py:82` `correct_markdown` | OCR cleanup of markdown | chat, `default`, inline prompt, temp 0.0 | called from `phase0_convert.py:72` | returns None on any error (no correction) |
| Video frame analysis | `ingestion/video_vision.py:81` `analyze_frame` | Describe a video frame; identify speakers against a roster | vision (base64), `events`, inline prompt | `phase0_video.py:381` | returns `{}` on error |

## 3. Phase 1

`phase1_parse.py` and `src/parser.py` make no Grok request. `src/parser.py` only lifts the translation markers
that Phase 0 wrote.

---

## 4. Phase 2 (extraction, `phase2_extract.py`)

Discovery requests (dates, places, people, people groups) are asked per window of `extraction.window_paragraphs`
(3) paragraphs of raw parsed text (`windows.py`) and attributed to the real sub-event by quote. They go through
`batch_parallel._batch_extract` (`batch_parallel.py:735-793`), which calls `extract_json` with the cache type and
the book, and returns an empty result when `BatchModeCollecting` is raised.

| Request | File:line | Type, cache type, prompt | Gate and notes | Failure mode |
|---|---|---|---|---|
| Events | `extraction/events.py:853` `_extract_chunk`, `:1116` `extract_events` | json, `events`, `events.yaml`, temp 0.1 | one per chapter chunk; halved on truncation (`_split_on_truncation`); coverage retry | `GrokAPIError` splits and retries; otherwise propagates. Batch rounds |
| Dates (windows) | `batch_parallel.py:790` via `_batch_extract` (`:1391`) | json, `dates`, `dates.yaml` | one per window | `empty` on `BatchModeCollecting`, `observe_error` on other errors |
| Dates (sub-event) | `extraction/dates.py:397` `_extract_dates_for_sub_event` | struct, `dates` | when no parsed text | retries with a cap, then gives up |
| Places (windows) | `batch_parallel.py:790` (`:1721`) | json, `places`, `places.yaml` | one per window | as dates |
| Places (sub-event) | `extraction/places.py:297` `_extract_place_for_sub_event` | struct, `places` | no parsed text | retries with a cap |
| People (windows) | `batch_parallel.py:790` (`:1936`) | json, `people`, `people.yaml` | one per window | as dates |
| People (sub-event) | `extraction/people.py:1199` `_extract_people_for_sub_event` | struct, `people` | no parsed text | retries on JSON or key errors |
| People groups | `batch_parallel.py:790` (`:1851`, cache type `peoplegroups`); `extraction/people_groups.py:378` `_call_groups_grok` (`people_groups`, temp 0.3) | json, `people_groups.yaml` | one per window; per-group call | `_call_groups_grok` re-raises |
| People consolidation | `extraction/people_consolidation.py:60` `_get_duplicate_groups` | chat, `people`, `people_consolidation.yaml` | caller `consolidate_people` has no pipeline caller (section 7) | none at the call |
| Biography facts | `extraction/enrich_biographies.py:804` `extract_biographical_data` | json, `api`, `biography.yaml`, temp 0.1 | text from Grokipedia or Wikipedia; Phase 2 people (`people.py`) | batch-aware; retries then returns nothing |
| Biography URL relevance | `enrich_biographies.py:950` `_validate_url_relevance` | json, `api`, inline | per candidate page | logged at debug, treated as not relevant |
| Unit canonicalization | `enrich_biographies.py:1537` `_validate_and_canonicalize_unit` | chat, default cache, inline | per unit name | returns None |
| Title memberships | `extraction/title_memberships.py:238` `_ask_membership` | chat, default cache, inline | called by `enrich_biographies._derive_and_validate_title_memberships` | no handler at the call |
| Weather | `extraction/weather_central.py:940` `_call_and_parse_weather` | json, `weather`, `weather_batch.yaml` | blocks packed per call (`CALL_CHARS` 30,000) | batch-aware; retries |
| Casualties | `extraction/casualties.py:429` `_call_and_parse_casualties` | json, `casualties`, `casualties.yaml` | `chunk_size=1`; `GrokNoJsonError` returns `{}` | batch-aware; truncation and identity errors re-raise |
| Logistics | `extraction/logistics.py:388` `_extract_logistics_chunk`; `:580` `_extract_logistics_with_llm` (struct) | json, `logistics`, `logistics.yaml` | `chunk_size=1` | batch-aware; returns `{}` or None |
| Supplemental (notes) | `extraction/supplemental.py:1032` `_extract_with_retry` | json, `supplemental`, `supplemental.yaml`, temp 0.1 | one per note-bearing window; stable cache key; 3 attempts | batch-aware |
| Equipment broad | `extraction/equipment.py:2254` `_extract_equipment_broad_one` | json, `equipment_broad`, `equipment_extract_broad.yaml`, temp 0.1 | one per window | batch-aware; re-raises |
| Equipment refine | `equipment.py:2536` `_call_refine` | json, `equipment_refine`, `equipment_refine.yaml`, temp 0.1 | one per broad item | batch-aware; returns None |
| Equipment designation | `extraction/equipment_disambiguation.py:296` `_try_grok` | json, `equipment_disambiguation`, `equipment_disambiguation.yaml`, temp 0.0 | one per distinct unresolved designation; env `EQUIPMENT_DISAMBIGUATION` (default true) | batch-aware; returns None |
| Named-vessel identity | `extraction/equipment_identity.py:681` `_ask_grok` | json, `equipment_disambiguation`, `equipment_vessel_choice.yaml`, temp 0.0 | choose the hull a vessel name refers to | batch-aware; returns None |
| Equipment text enrichment | `equipment.py:1306` `_enrich_equipment_data` | json, `equipment_enrichment`, `equipment_enrichment.yaml`, temp 0.1 | grounded on Grokipedia or Wikipedia text; none when no text | batch-aware; returns `{}` |
| Source re-check | `extraction/source_recheck.py:97` `recheck` | json, `<label>_source_recheck` (`equipment_source_recheck`, `people_group_source_recheck`), inline | gap-fill from retained `original_text`; env `GROUP_SOURCE_RECHECK` for groups; also runs in Phase 3 (section 5) | batch-aware; logs and skips |
| Source section summary | `extraction/source_section.py:225` `derive_summary_and_operation` | json, `source_section`, inline (`_build_section_prompt`) | one per source section (`enrich_all_source_sections`, `phase2_extract._run_source_section_enrichment`) | no handler at the call |
| Image caption (vision) | `extraction/image_captioner.py:127` `caption` | vision (base64), `default`, `_prompt` | document images (`extraction/images.py:488`, `:671`); also the map-vs-image decision in Phase 3 | returns a context-only `ImageCaption`, never raises |
| Map-vs-image | `media_classify.vision_is_map` -> `ImageCaptioner.caption` | as above | Phase 3 (section 5) | undecided stays pending |
| Dedup: ambiguous people | `dedup/find_duplicate_people.py:1426` `_verify_ambiguous_matches` | chat, `people`, temp 0.0 | `generate_duplicate_report` in `phase2_extract._run_analysis` | logged at debug, pair kept as is |
| Dedup: related groups | `dedup/find_related_groups.py:108` `_verify_duplicate_with_llm` | chat, default cache | `generate_related_groups_report` | returns `UNKNOWN` |
| Legacy media probes | `equipment.py:1064` `_extract_image_urls_from_page` (`equipment_image_extraction`, `equipment_urls.yaml`) | json | unreachable (section 7) | n/a |

The `book` argument is passed by `_batch_extract` (`current_book.get()` or the parsed `book`), so the windowed
requests and the `BOOK_BOUND` types carry identity.

---

## 5. Phase 3 (enrichment, `phase3_enrich_data.py`)

Phase 3 is live (`batch.phase3: false`). With `--batch` the collect-and-re-run behaviour is in
[PHASE3_MAP.md](PHASE3_MAP.md) section 5. Order follows the step order there. The same request types run in the
SQS worker (`openserp_worker.py`) through `enrich_one_<type>`.

| Step | File:line (function) | Purpose | Type, cache type, prompt, temp | Gate | Failure mode |
|---|---|---|---|---|---|
| people_groups | `extraction/source_recheck.py:97` via `enrich_groups._maybe_source_recheck` | Gap-fill nationality or parent from the record's source text before the facts query | json, `people_group_source_recheck`, inline | per group with missing fields; env `GROUP_SOURCE_RECHECK` (default true) | logs and skips |
| people_groups | `extraction/enrich_groups.py:260` (`enrich_group`) | Structured unit facts for a disambiguated unit query | json, `group_enrichment`, inline `PROMPT` | per group without `enrichment_data`; `not_found` after 180 days | fail-safe: an error leaves the record unstamped; `BatchModeCollecting` returns False |
| places | `extraction/enrich_places.py:459` (`_try_grok`) | Continent, country, region, historical names | json, `place_enrichment`, inline `PROMPT` | per place missing continent or country | raises, so the record is not stamped `not_found` |
| geocode | `enrichment/hill_geocode.py:124` (`_grok_locate`) | Locate a hill or height from elevation and nearby anchors | chat, `places`, `_prompt_for`, temp 0.1 | height designations that Nominatim missed | `found=False` with a note |
| geocode | `enrichment/places_grok_geocode.py:529` (`geocode_place`) | Coordinates, confidence, note for a place name | chat, `places`, `_prompt_for`, temp 0.1 | last in the cascade | no handler; counted as an error by `_geocode_one_file` |
| bibliography | `extraction/supplemental_advanced.py:491` (`_call_alt_title_expansion`) | Expand citation shorthand to a searchable title | chat, `bibliography_alt_title`, `bibliography_alt_title.yaml`, temp 0.0 | per entry without an expanded `alt_title` | returns `""` |
| bibliography | `supplemental_advanced.py:90` (`extract_isbn`) | ISBN for a post-1966 book | chat, `supplemental_advanced`, `isbn_lookup.yaml` | `supplemental_material.extract_isbn` (false in `config.yaml`) | returns None |
| bibliography | `supplemental_advanced.py:111` (`get_author_death_date`) | Author death date for the copyright rule | chat, `supplemental_advanced`, `author_death_date.yaml` | `determine_copyright` (false) | returns `UNKNOWN` |
| bibliography_resolve | `enrichment/bibliography_resolver.py:1900` (`_verify_nara_match`) | Does a NARA record match the citation (unit, document type, period) | chat, `bibliography_verify`, `nara_verify.yaml`, temp 0.0 | per NARA candidate | fail-closed |
| bibliography_resolve | `bibliography_resolver.py:1942` (`_verify_match`) | Title-only check of an Archive.org or Gutenberg candidate | chat, `bibliography_verify`, `bibliography_verify.yaml`, temp 0.0 | per candidate | fail-open: accepts on Grok error, and with no client; a URL goes on to the content check |
| bibliography_resolve | `bibliography_resolver.py:1114` (`_verify_openserp_match`) | Does an OpenSERP result match the citation (equipment: is it the right photograph) | chat, `bibliography_verify`, `bibliography_verify.yaml`, temp 0.0 | at most 5 per entry (`verify_budget`) | fail-closed |
| bibliography_resolve | `bibliography_resolver.py:2014` (`_verify_url_content`) | Does the fetched page (first 2000 characters, SSRF-safe) match the citation | chat, `bibliography_verify`, temp 0.0 | per candidate URL | fail-closed; an SSRF block is a reject |
| bibliography_resolve | `extraction/supplemental_search.py:498` (`search_llm`) via `_find_in_llm` | Ask Grok for a URL for the cited work (least trusted source; content-verified before use) | chat, `supplemental_search`, `publication_search.yaml`, temp 0.0 | last resort: nothing else found, `llm_search` true, not in a batch collect pass | logs `OBSERV bib.llm_search_failed`; retried next run |
| in_doc_map_links | `extraction/map_link_source.py:82` (`_vision_depicted`) | What a recycled in-document map depicts (units, places, sector, date, movements) | vision (URL), `in_doc_map_vision`, `_build_verification_prompt` (`map_vision.yaml`) | `external_maps.recycle_in_doc_maps` (true), at most `max_recycled_per_doc` (50) per document | returns None |
| in_doc_map_links | `extraction/image_captioner.py:127` via `map_common.maybe_vision_classify_map` -> `media_classify.vision_is_map` | Tier-3 "is this a map" for an ambiguous Map image that has a local file | vision (base64), `default` | once per image (`map_vision_checked_at`) | undecided leaves the record unstamped |
| image_map_vision | `image_captioner.py:127` via `image_map_vision.py:139` -> `vision_is_map` | Map or photograph for images Phase 2 left `pending_vision` | vision (base64), `default` | a Phase 2 caption verdict is reused with no call | leftovers stay pending and alert |
| openserp_* | `enrichment/openserp_enrichment.py:442` (`_verify_result`) | YES/NO relevance of one search result to the entity | chat, `openserp_verify`, inline, temp 0.0 | one per result, every driver and `process_positive_url` | fail-closed, error not cached |
| openserp_* | `openserp_enrichment.py:679` (`_grok_summarize_html`) | 1-2 sentence summary of a verified page (first 6000 characters) | chat, `openserp_url_summary`, inline, temp 0.1 | per accepted textual URL not in the 90-day verdict cache | error in `_summarize_url_page` becomes no summary, so `REJECT` is cached 90 days |
| openserp_groups | `enrichment/unit_patch.py:62` (`_vision_is_patch`) | Is a candidate image the unit's shoulder-sleeve insignia | vision (URL), `unit_patch_vision`, inline, temp 0.0 | group without `unit_patch`, with a `GroupID` and at least one candidate | returns False |
| openserp_equipment | `extraction/equipment_identity.py:681` and `equipment_disambiguation.py:296` via `equipment_images.record_scope` | SPECIFIC or GENERIC scope when the alias tables cannot say | json, `equipment_disambiguation`, temp 0.0 | records without `identity_scope`; env `EQUIPMENT_DISAMBIGUATION` | returns None; undecided is not searched |
| openserp_external_maps | `extraction/openserp_maps.py:124` (`_analyze_license_with_grok`) | Licence of a map image from its page text | json, `license_check`, `license_check.yaml`, temp 0.0 | `external_maps.enabled` (false) | no handler at the call |
| openserp_external_maps | `extraction/search_external_maps.py:414` (`_verify_single_image`) | Is a candidate image a relevant map for the place | vision (URL), `external_maps_verification`, `map_vision.yaml` | `external_maps.enabled` (false) | no handler at the call |
| image_person_link | none | Calls `ImageCaptioner.caption(None, ...)`, which returns a context-only result without a request | n/a | `images.link_people` | n/a |
| link_asset_review | `extraction/link_relevancy.py:82` (`_grok_link_relevant`) | Is a document link relevant to a WWII corpus: YES, NO or UNKNOWN | chat, default cache, inline | non-whitelisted links, `links.review_external` | fail-safe: an error or UNKNOWN enqueues the link for human review |
| image_place_link | `extraction/image_place_link.py:82` (`_grok_is_terrain`) | One-word terrain gate on a caption | chat, default cache, inline | ambiguous captions, `images.link_places` | `(False, "grok-error")` |
| equipment_images | `extraction/equipment.py:617` (`_verify_media_with_vision`) via `equipment_images._ask_vision` | Does this image show the named equipment | vision (base64), `vision_verification`, `equipment_vision.yaml`, temp 0.0 | per pending image of SPECIFIC equipment until `equipment.images_per_record` (3) verified; 3 undecided answers set `needs_review` | no verdict leaves the image unchecked and alerts |

Not Grok: NOAA, Nominatim, the elevation lookup, Wikipedia and Commons listing, Archive.org, Gutendex, Wayback, and
the OpenSERP search itself. NARA Record Group identification is a deterministic alias lookup
(`_lookup_record_group`).

Calls per OpenSERP result, worst case for a text result: `_verify_result` (1) plus `_grok_summarize_html` (1).
A rejected result costs 1. A cached verdict (`openserp_verify`, `openserp_url_verdict`) costs 0. Image results cost 1.

---

## 6. Lambdas and the entrypoint

| Where | File:line | Request | Status |
|---|---|---|---|
| `urlreview_ui_handler` Lambda (`compute.yaml:1226`) | `lambda_handlers/urlreview_ui_handler.py:176-200` -> `enrichment/fetch_review.py:253` `decide_exclude_scope` | chat, `exclude_scope`, temp 0.0: exclude one URL or the whole site when an operator excludes a find | wired; operator action, not a pipeline run. An error returns `SCOPE_URL` |
| `openserp_worker.py` | `:298` (`GrokClient(...)`) | the Phase 3 OpenSERP requests through `enrich_one_<type>` (section 5) | dormant: nothing fills the queue |
| `ecs_entrypoint.py` | `:4859` | builds a `GrokClient` only to write retrieved batch answers into the cache; makes no request | live |
| `lambda_handlers/phase2_handler.py` (`phase3_handler.py` deleted 2026-11-20) | `:28` (`GrokClient(...)`) | per-entity enrichment from S3 events | dormant: not referenced by any template or script |

No other Lambda (including `batch_poller.py`) imports `GrokClient` or makes a chat request.

---|---|---|---|
| `urlreview_ui_handler` Lambda (`compute.yaml:1226`) | `lambda_handlers/urlreview_ui_handler.py:176-200` -> `enrichment/fetch_review.py:253` `decide_exclude_scope` | chat, `exclude_scope`, temp 0.0: exclude one URL or the whole site when an operator excludes a find | wired; operator action, not a pipeline run. Error returns `SCOPE_URL` |
| `openserp_worker.py:298` | the Phase 3 OpenSERP requests through `enrich_one_<type>` | as section 5 | dormant (nothing fills the queue) |
| `ecs_entrypoint.py:4910` | builds a `GrokClient` only to populate the cache from retrieved batch answers; makes no request | n/a | live |
| `lambda_handlers/phase2_handler.py` (`phase3_handler.py` deleted 2026-11-20) | `:28` (`GrokClient(...)`) | per-entity enrichment from S3 events | dormant: not referenced by any template or script |

| `batch_poller` and other Lambdas | no chat request | n/a | n/a |

---

## 7. Unused request code

Callers found by grep and `trace_path`; none outside tests.

| Code | Request | Evidence |
|---|---|---|
| `openserp_enrichment._verify_results_batch` (`:584`), `_prefilter_verify_cache`, `_parse_batch_verdicts` | `openserp_verify_batch` | no caller |
| `utils/url_content_verdict.evaluate` (`:128`) | `url_content_verdict` | no importer |
| `enrichment/situational_geocode.geocode` (`:338`) | `places` | not in `cascade_geocoder`'s list in `P3` |
| `equipment._extract_image_urls_from_page` (`:1064`) | `equipment_image_extraction` | only reached by `_extract_images_from_pages` <- `_extract_media_with_openserp`, which nothing calls |
| `extraction/people_consolidation.consolidate_people` (`:176`) | `people` consolidation | no caller outside its module |
| `extraction/chart_source_adapter.ingest_affirmed_chart` (`:321`) | chart vision (`chart_vision.yaml`) | no caller |
| `extraction/date_summary.generate_date_summary` (`:140`) | `date_summary` (`date_summary.yaml`, temp 0.1) | no caller |
| `extraction/grok_search_maps.search_maps_with_grok` (`:81`), `verify_map_with_vision` (`:202`) | `grok_search_maps` (`map_search.yaml`, temp 0.1), `vision_verification` | CLI only (`python3 -m src.extraction.grok_search_maps`) |
| `lambda_handlers/phase2_handler.py` (`phase3_handler.py` deleted 2026-11-20) | via `enrich_*` | no template or script |

---

## 8. Scripts that also call Grok (not part of a pipeline run)

`scripts/complete_metadata_with_grok.py`, `scripts/resolve_surname_people.py`, `scripts/resolve_title_people.py`,
`scripts/suggest_group_aliases.py`, `scripts/proto_map_vision.py`, `scripts/utils/debug_nara_search.py`,
`scripts/archive/*`. Not traced for call type.

---

## 9. Cost notes and limits of this inventory

- Phase 2 core extraction is batched at 50% of token pricing when 50 or more requests are queued in a round.
  Phase 3 pays full price. Its largest request counts are per OpenSERP search result (`openserp_verify`,
  unbatched), per bibliography candidate (`bibliography_verify`) and per equipment image
  (`vision_verification`). Cached answers are free on re-run.
- `EP._reset_openserp_searched` makes people and equipment with no hit repeat their OpenSERP candidate
  verifications on every Phase 3 task; the verdict caches absorb only repeated candidates.
- Prompts written inline are named by function; their wording is not copied here. Per-request token counts and
  measured cost were not collected. `log_cache_stats` and `_log_token_usage` log cache hit rates and token usage;
  `.phase_results.json` has no per-step Grok count (the OpenSERP `verify_yes` and `verify_no` counters are the only
  per-step ones).
- Batch-aware versus live was read from the `BatchModeCollecting` handlers, not traced through every Phase 2 round.
- The phase in which `image_captioner` (`images.py:488`, `:671`) and `title_memberships` run was taken from their
  callers.
