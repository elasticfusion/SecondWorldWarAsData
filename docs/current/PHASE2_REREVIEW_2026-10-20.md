# Phase-2 RE-REVIEW findings (2026-10-20, post-fix) — docs/current/PHASE2_REREVIEW_2026-10-20.md

Fresh independent code+security re-review of ALL Phase-2 code on main @4b762a36, AFTER the full review
program (#475–#486, #477/#480) merged. 8 parallel sub-agents (4 disjoint slices × code+security).
Analyzer baseline on the surface: **bandit 0 High / 0 Medium / 34 Low; radon 0 D+; 2439 tests green** —
so every finding below is a logic/design/taint issue the analyzers cannot see.

**Outcome:** the merged fixes VALIDATE as correct and complete *within their declared scope* (all 8
agents confirmed the specific Pattern 1–5 / trigger / orchestration / concurrency fixes are sound). But
the re-review surfaced **1 CRITICAL** the Pattern-2 pass structurally missed + several real correctness
WARNINGs. Ranked, de-duplicated across the 3 agents that converged on the SSRF:

## CRITICAL (✅ DONE · PR #487)
- **SSRF: `grok_client.extract_json_with_image` fetches an untrusted image URL with no guard.**
  `session.get(image_url, allow_redirects=True)` + unbounded `.content`, no `clean_url` /
  `resolved_host_is_safe`. Reachable (LIVE, Phase 3): `openserp_maps.import_openserp_maps` →
  `search_external_maps._verify_map_relevance` → `_verify_single_image` → `extract_json_with_image`,
  where `image_url` = `urljoin(base_url, src)` from UNTRUSTED search-page HTML → can hit
  169.254.169.254 / internal hosts, following redirects. **Why the Pattern-2 fix + regression test
  missed it:** `test_pattern2_ssrf_fetch` greps the *cluster modules* for `requests.get(`; this fetch
  is in `grok_client.py` (not grepped) and uses `session.get` (not `requests.get`). The sibling
  `unit_patch.py` DOES pre-validate the identical call (its docstring says the caller MUST); only
  `_verify_single_image` skips it. FIX (both, defense-in-depth): (a) gate `img_url` in
  `_verify_single_image` via `clean_url + resolved_host_is_safe` (mirror `unit_patch._safe_url`);
  (b) harden `extract_json_with_image` itself — route bytes through `images.fetch_image_bytes`
  (validated + capped + no-redirect) then base64 — so the guard holds for ALL callers. + a bytes-level
  regression test (not a source grep) asserting a metadata/loopback URL is refused.

## HIGH (new correctness)
- ✅ **DONE #488** (+ NEW follow-up: inner extract_fns swallow GrokTruncationError so halving is latent)
  **`chunked_extract.extract_with_chunk_halving` halving is NOT recursive** — a half-chunk that still
  truncates is caught by `except Exception` and silently DROPPED (~half the items lost, error-logged
  only). FIX: recurse per half (keep the `BatchModeCollecting` re-raise).
- **`batch_api.retrieve_results_ordered` status gate too strict** — `valid` requires
  `finish_reason=="stop"` but ordered-retrieve defaults `"unknown"`; a completed choice with any
  non-`stop` terminal reason has its valid content DISCARDED, re-billed via realtime retry, and counted
  toward the partial-failure threshold. FIX: accept choices+content when `finish_reason != "length"`.

## WARNING (new)
- **Path-traversal fix INCOMPLETE (Pattern 3 missed 2 writers):** `weather_central._create_new_weather_file`
  (`safe_place = place_name.replace(" ","_").replace(",","")` → path) and `casualties._save_casualty`
  (`type` unclamped LLM value → `{type}_{id}.json`) do NOT route through `_safe_entity_filename` /
  `_contained_entity_path` — same traversal class fixed in dates/places/equipment. FIX: route both
  through the hardened sanitizer (+ clamp casualties `type` to its enum).
- **Decompression-bomb guard scoped too narrowly:** `equipment` (convert/resize at :568/:576) +
  `grok_search_maps` (:122/:138) restore `MAX_IMAGE_PIXELS` in `finally` BEFORE the pixel-rasterizing
  `convert()/resize()`; `verify()` doesn't rasterize, so a bomb trips after the cap is lifted. Also
  `equipment._compute_image_hash` (:945 `average_hash`) has NO cap at all. FIX: hold the cap across the
  whole decode+resize block (finally around the body), mirror `unit_patch`.
- **SSRF via url_hygiene bypass (enrichment, Slice C):** `bibliography_resolver._verify_url_content`
  (LIVE) + `source_retrieval._download_to_quarantine` (latent, not yet wired) `session.get` a
  DISCOVERED (OpenSERP/LLM) URL with `allow_redirects=True`, bypassing `safe_get`/`clean_url` that the
  module docstring mandates. FIX: route through `fetch_text_safe` / `clean_url + resolved_host_is_safe`.
- **logistics data-loss + index bugs (Slice C):** `_save_extraction` adds the dedup key BEFORE the
  write try (a failed write permanently skips a valid record that run); `_rebuild_index` key
  `category_type_date` collapses distinct sub-events (last-wins) + date-less share `_UNKNOWN`;
  `_build_weather_impact` bare-substring weather-desc match attaches the wrong WeatherID.
- **Core-extractor nits (Slice B):** `places._add_event_mention` hard-indexes `m["Sub_eventID"]`
  (KeyError on legacy mention → aborts the place update); `people._normalize_name` stacked
  double-`@lru_cache` (inner dead).

## SUGGESTION / tracked (not blockers)
- Positional batch-result mapping integrity canary (equal-count vendor reorder would mis-key — doc'd,
  verified vs live API 2026-10-14); `contextvar` not propagated into `run_in_executor` (book cache
  lands in global namespace — layout only, keys embed content so no collision); `_estimate_batch_cost`
  2000-token allowance under-estimates; realtime retry spends outside the credit reserve; batch_poller
  stall-notify 5-min lookback vs 15-30-min schedule (observability); several non-atomic registry/
  review-queue writes (idempotent / low-impact). Known-accepted: DNS-rebinding TOCTOU, ULID[:8]
  filename collision, 3 radon-INHERENT blocks.

## Verified CORRECT + COMPLETE (not re-flagged — the merged fixes hold)
credit-gate reserve/release (one-reserve⇒≤one-release, atomic conditional add, releases on any
post-reserve failure); batch size caps fail-closed; API-key never logged/cached; cache-key stability
(equipment EventID, casualties seids+chunk_sig); the 12 Pattern-2 SSRF sites all route through
safe_get/fetch_text_safe/fetch_image_bytes (no residual bare requests.get IN THOSE MODULES); url_hygiene
core (non-canonical IP/metadata/userinfo rejection); file_lock crash-atomic save (both variants) +
clean dead-Dynamo removal; run_scope corrupt-record visible downgrade (fails closed to magic-50);
trigger_handler _trusted_topic_name full-ARN trust + SAFE_TITLE key derivation + expected_count env gate
+ _update_manifest optimistic concurrency; batch_poller partial-failure alert + per-batch claim;
supplemental_info_pipeline temp-event.json (sanitized prefix, contained, unlinked); _has_document
hybrid logic; Pattern-3 sanitizer itself + dates/places/equipment writes.
