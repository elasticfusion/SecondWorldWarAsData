# Phase-2 Fresh Re-Review — 2026-10-20 (round b)

Independent re-review of ALL Phase-2 code at `main` commit `fd3435ad`, by 12 parallel sub-agents
(6 domain slices × code+security), per user request. Baseline: bandit 0H/0M/82L, radon 0 D+
(139 C+ grandfathered), 2476 tests. This is the follow-on to the §1.0b campaign (PRs #487–#496).

Severity order for the fix queue. "Corroborated" = found by >1 agent independently.

## CRITICAL

- **[DONE · PR #497] [CRITICAL · corroborated] DNS-rebinding TOCTOU — SSRF fetch layer vets resolved
  IPs but does NOT pin the connection to them.** `url_hygiene.safe_get`, `images.fetch_image_bytes`,
  and `url_fetch._one_hop` call `resolved_host_is_safe(host)` then hand the HOSTNAME url to
  requests, which re-resolves at connect time. A short-TTL attacker DNS name that answers public on
  the check and 127.0.0.1/169.254.169.254/RFC1918 on the connect slips through — exactly what the
  `resolved_host_is_safe` docstring says the caller MUST prevent by pinning. Fix in ONE shared
  helper: connect to a vetted IP with the original Host header (+ TLS SNI/cert vs the hostname), or
  resolve-once-and-pin. Used by safe_get / fetch_image_bytes / url_fetch._one_hop.

- **[CRITICAL · code_utils_rest] Schema-version stamping ignores entity type → records stamped at the
  MIN version (2.24) → permanently "needs_upgrade" → genuine current-schema violations ride the
  lenient path.** `ecs_entrypoint._stamp_file` calls `needs_migration(data)`/`inject_metadata(data)`
  with NO entity arg (entity known in the caller loop). `entity_version(None)` = min across entities.
  WORSE: the fast-path samples only a weather file (whose version == the min), so it early-returns
  and never stamps places/people_groups/equipment/etc at all. Fix: thread the entity through
  `_stamp_file`; make the fast-path per-entity (or drop it).

## HIGH / WARNING

- **[WARNING · corroborated: sec_entity_extractors + code_entity_extractors] logistics `_save_extraction`
  path traversal via LLM `date_start`.** `filename = f"{category}_{type}_{date_str}_{id[:8]}.json"`
  where `date_str = extraction.date_start.replace('-','')` — category/type are enum-clamped (safe) but
  `date_start` is free-text LLM output; `.replace('-','')` does not strip `/` or `..`. Logistics is the
  ONE date-in-filename writer the Pattern-3 campaign missed (dates/weather/casualties were fixed). Route
  through `_safe_entity_filename` + `_contained_entity_path`.

- **[WARNING · corroborated: code_entity_extractors + code_enrichment] logistics weather-impact link is
  DEAD.** `_build_entity_index(output_root,"weather","WeatherID","description")` reads a TOP-LEVEL
  `description`, but weather records store it under `extracted_data.description` → `weather_index` is
  ALWAYS `{}` → `_build_weather_impact` never links (the new OBSERV `weather_unmatched candidates=0`
  fires every time — the tell). Fix the index builder to read the weather description from its real
  location. (NB: this supersedes the #494 weather bounded-match fix usefulness — the index was empty.)

- **[WARNING · corroborated: code_batch_lambdas + sec_batch_lambdas] batch_poller `_check_stalled`
  stall-notification rate-limit assumes a 5-min poll cadence (`int(age_hours)==int(age_hours-5/60)`)
  but the schedule is 15–30 min → the hourly ">4h stalled" operator alert is silently skipped. Track a
  persisted `last_stall_notify_at` and compare elapsed>=3600.

- **[WARNING · code_batch_lambdas] batch_poller 24h-stuck force-complete discards the completed fraction.**
  returns "complete" when `success>0 && age>24h` even though `success+error<total`; the ecs mapper then
  count-mismatches the partial results → all discarded ({}) → full LIVE re-extract (paid batch work +
  partial successes wasted, silently). Map the available results by position / alert N/total missing.

- **[WARNING · sec_batch_lambdas] topic-trust gate bypassed for S3 keys.** `_extract_records` appends a
  record's S3 keys UNCONDITIONALLY before/independent of `_trusted_topic_name()`; untrusted-topic keys
  still reach manifests/pending.json + `_route_topics`. Move the key-append inside the trust gate.

- **[WARNING · code_batch_lambdas] trigger_handler `_acquire_task_lock` stale-reclaim TOCTOU** — two
  invocations can both reclaim a stale lock and double-launch. Make the delete conditional on the
  observed timestamp (or single atomic update_item with `response < grace_cutoff`).

- **[WARNING · code_batch_core] truncated response >100K chars NOT evicted from cache → permanent
  GrokTruncationError on re-run.** `_handle_truncation_error` pops the poisoned cache entry only in the
  `<100000` else-branch; the >100K path raises without eviction. Move the `cache.pop` out so both branches
  evict. (Note: #489 inner-fn re-raise made truncation propagate; this is the cache-poison tail.)

- **[WARNING · sec_batch_core] write-guard blind spot — `locked_json.save()` bypasses `_validate_entity`.**
  The schema guard runs only in `write_json_with_lock`/`write_parsed_with_guard`; ALL merge paths persist
  via `locked_json` (equipment/people/places/people_groups/dates/weather/enrich_write/batch_parallel),
  which only annotates — so a merge that drops a required field is written unguarded. Call `_validate_entity`
  in the save() closure(s) + reconcile version stamping.

- **[WARNING · sec_batch_core] clean_url accepts dotted-hex / mixed-radix IP encodings**
  (`0x7f.0x0.0x0.0x1`, `127.0.0.0x1`) → True, contradicting its docstring. Backstopped at fetch by
  resolved_host_is_safe, but clean_url is ALSO a standalone registry-key/gate validator (url_registry,
  bibliography_resolver pre-fetch reject). Detect any hex/mixed-radix numeric host and coerce via inet_aton.

- **[WARNING · sec_utils_rest] safe_get byte cap runs AFTER gzip auto-decompression** — iter_content yields
  already-decompressed bytes, so a small highly-compressing body inflates to the 10MB cap in memory per
  concurrent fetch (CPU/mem DoS). Cap compressed bytes (`resp.raw.decode_content=False`) or check
  Content-Length early.

- **[WARNING · corroborated: code_enrichment] transient-error → permanent-negative cache poisoning (systemic).**
  Multiple fetchers collapse "miss" and "timeout/5xx/429/exception" into `None`, then negative-cache for 7d
  (NEGATIVE_TTL_DAYS) or persist a terminal status: nominatim/places_grok_geocode (persists `not_found`,
  no TTL re-search → only `--force` recovers), noaa_weather, grokipedia + situational_geocode +
  equipment_wikipedia + groups_wikipedia, bibliography_resolver `_search_openserp_archive` (503-from-scaling
  OpenSERP). Only negative-cache on a real 200-empty; never on transient.

## SUGGESTION / NIT (defer unless cheap)

- openserp_maps `_process_search_result` downloads `map_images[0]` not the vision-APPROVED image
  (verification-gate gap); `_check_duplicate_map` lacks the try/except json.load guard its siblings have
  (a malformed file silently drops a whole place). [code_maps_supplemental]
- supplemental `_normalize_enums` coerces an INVALID content_class to "citation" (should be "ambiguous").
- award_polite_source.polite_get reads `resp.text` with NO byte cap (route through safe_get or add cap);
  wayback.fetch_archived fetches archived_url with allow_redirects=True + no resolved-IP recheck + no cap.
- weather_central `_add_event_mention` hard-indexes `m["Sub_eventID"]` (KeyError on legacy) — the identical
  fix applied to places.py (#495) was missed here. [code_entity_extractors]
- enrich_groups `enrich_group` reads `data.get("name")` but schema stores `group_name` → never enriches a
  schema-valid record (the sibling `_build_enrichment_query` already does `name or group_name`).
- BatchCollector.add silently drops a request_id (=cache_key) collision — log at WARNING (latent
  optional-entity-zero). write_json_with_lock omits fsync before os.replace (locked_json has it).
- _submit_ocr derives names from the raw S3 key without the `_SAFE_TITLE_RE` validation every other intake
  path applies (not a traversal on S3, but breaks the intake invariant).
- prompt_loader.render_prompt / search_query_loader sequential str.replace is order-dependent; an untrusted
  value containing a `{otherkey}` token can be re-substituted (prompt mutation, not RCE). storage.LocalStorage
  ._resolve returns the UNRESOLVED path after validating the resolved one (TOCTOU/symlink). custom_validators
  _validate_ulid_fields over-matches `"ID" in key` + rejects hash BibliographyIDs. source_retrieval quarantine
  basename from URL. events.py inner-except shadows `e`. logistics `_load_existing_keys` IndexError on empty
  event_mentions swallowed.
