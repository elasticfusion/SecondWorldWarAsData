# Equipment Media Integration

**Status:** implemented in Phase 3 (`src/enrichment/equipment_images.py`,
`src/enrichment/openserp_enrichment.py`, `src/extraction/equipment.py::_verify_media_with_vision`).
Phase 2 fetches no images. An earlier design that searched OpenSERP and downloaded to
`filestore/equipment/` during Phase 2 is gone: `_extract_media_with_openserp` and the helpers
it calls remain in `equipment.py` with no caller, and the old Phase 2 filestore copy never reached S3.

## Owner rules (2026-11-07)

- No image is attached unless Grok vision confirms it shows the named equipment (WWII era or a
  preserved example). A vision error is UNDECIDED (retried, then `needs_review`), never a silent
  rejection.
- Only SPECIFIC records get images (`identity_scope == specific`). GENERIC records ("trench",
  "rifle") stay in the JSON unenriched; Phase 3 also strips enrichment a generic record received
  earlier (`_strip_enrichment`: `images`, `media`, `external_data`, `wikipedia_*`,
  `description_source_url`, `image_candidates_checked_at`, `enrichment_status`,
  `enrichment_checked_at`; plus `timeline`, `technical_evolution` and `logistics` when every block's
  `source` is grokipedia, wikipedia or enrichment. Narrative-sourced fields stay).
- "World War II" qualifies TEXT: Wikipedia candidates come only from an article whose text places
  the subject in WWII. Images are judged by vision alone.

## Phase 3 order (`phase3_enrich_data.main`)

1. `openserp_equipment` (only when `supplemental_material.use_openserp` is true; true in
   `config.yaml`): `enrich_equipment_with_openserp`.
2. `equipment_images` (always runs for non-`--people-only` runs; not gated by `use_openserp`):
   `resolve_equipment_images(out/equipment, grok_client, images_per_record)`.

## Step 1: OpenSERP candidates (`enrich_equipment_with_openserp`)

- Skips the whole step with a warning when `{openserp_url}/health` is not 200
  (`_openserp_reachable`, 3 s probe, URL `external_maps.openserp_url`, default
  `http://localhost:7001`). Resets the circuit breaker first.
- Per record: skipped if searched in the last 90 days (`openserp_searched`,
  `openserp_searched_at`), if the name is unusable, or if `record_scope` is not SPECIFIC
  (generic/unresolved/undecided records are never searched). Skipped inside
  `enrich_one_equipment` if the record already has `images`.
- `search_equipment_images` renders `search_queries/equipment.yaml` `images` templates
  (`"{name}" {facts} WWII photo`, `"{identifier}" {facts} World War II photograph`,
  `"{name}" {year} {facts}`); `{facts}` is category + `country_of_origin`
  (`_equipment_query_terms`). Each result goes through `_verify_result` (Grok text check of title,
  snippet and URL against "Photo of <name>"); up to 3 verified results are stored as
  `images: [{url, title, source: "openserp"}]`.
- These images are only candidates. They carry no `vision_check`, so step 2 queues them for vision.
- Search mechanics (rate limit `openserp.rate_limit_seconds` 5, timeout
  `openserp.request_timeout_seconds` 60, breaker after 5 consecutive empty/failed responses,
  domain exclusions, metrics file `output/metrics/openserp_metrics.json`) are shared with the
  other entity types.
- The record is stamped `openserp_searched` / `openserp_searched_at` after every attempt, found or not. On ECS, `ecs_entrypoint._reset_openserp_searched` removes `openserp_searched` at the start of each Phase 3 task from equipment (and people) records that have no `images`, so a record with no hits is searched again every task; the 90-day gate holds only for records that got images.

## Step 2: candidates and vision (`resolve_equipment_images`)

Per equipment record (`_process`), by `record_scope`:

- GENERIC: `_strip_enrichment` (counted `generic_cleaned`).
- SPECIFIC:
  1. `_queue_existing`: images with no `vision_check` (legacy Phase 2, OpenSERP) become
     `pending`; a recorded earlier vision pass (`vision_verified` true and `vision_checked_at`)
     is kept as `verified`. The legacy loose `media` copy is dropped.
  2. `_add_article_candidates`: once per record (`image_candidates_checked_at`), the qualified
     Wikipedia article's images (`article_image_candidates`, up to `MAX_CANDIDATES` 20, Commons
     imageinfo in batches of 5) are added as `pending`, deduped by URL. A Wikipedia/Commons
     outage leaves the record unstamped (`candidates_transient`, retried).
  3. `_verify_pending`: images are downloaded SSRF-safe into memory (`url_hygiene.safe_get`,
     10 MB cap) and judged by `prompts/equipment_vision.yaml` until `images_per_record`
     (`equipment.images_per_record`, default 3) are verified; the rest stay pending.
     A duplicate of a verified image (perceptual hash) is rejected. Outcome fields on each
     image: `vision_check` (`pending|verified|rejected`), `vision_verified`, `vision_reason`,
     `vision_checked_at`, `phash`, `vision_attempts`.
- Limits: 3 undecided vision attempts or 5 failed downloads put the image in `needs_review`.
- `record_scope` returns None when it cannot decide without Grok; the record is left unchanged.
- In a batch collect pass `BatchModeCollecting` leaves the record untouched (`scope_deferred`).
- Images are never written to a filestore; only the URL and verdict are stored.

## Alerts and observability

One line per run: `OBSERV equipment.image_vision records=… generic_cleaned=… candidates=…
verified=… rejected=… duplicate=… undecided=… fetch_failed=… needs_human=… deferred=…
scope_deferred=… status=ok|warn`. `observe_warn("equipment", <event>)` fires for
`equipment_image_vision_incomplete`, `equipment_image_fetch_failed`,
`equipment_image_needs_human`, `equipment_image_candidates_deferred`,
`equipment_image_record_unreadable`, `equipment_image_write_failed`; each note says what to
check. Per-record failures log `OBSERV equipment.image_vision_error`.

## Configuration

```yaml
equipment:
  enabled: true
  images_per_record: 3            # read by phase3 _resolve_equipment_images (default 3)
supplemental_material:
  use_openserp: true              # gates step 1 only
openserp:
  rate_limit_seconds: 5
  request_timeout_seconds: 60
```

`equipment.enable_enrichment` and `equipment.verify_media_with_vision` appear in `config.yaml`
but do not control anything: Phase 2 text enrichment follows identity resolution and vision
always runs in step 2.

## Vision check (`_verify_media_with_vision`)

Pillow validates the image (PNG/JPEG/GIF, under 5 MB after shrink, 50 M pixel decode cap), then
`GrokClient.extract_json_with_image_base64` (cache type `vision_verification`, temperature 0)
returns `{is_relevant, reason}`. A non-boolean or missing verdict, or a Grok error, is
undecided (`None`). The prompt asks for a photograph of the specific model, WWII era or a
preserved example, and says to reject when unclear.

## Related

- [EQUIPMENT_FLOW.md](EQUIPMENT_FLOW.md) Stage 5 (Phase 2 text enrichment).
- Error-handling review: `docs/archive/2026-10-11/features/equipment/EQUIPMENT_ERROR_HANDLING.md`.
