# Scripts Reference

Utility scripts for pipeline operations, data management, and maintenance.

## Layout

- **`scripts/*`** — scripts the project actively uses: invoked by the pipeline
  (`ecs_entrypoint.py` / `phase*.py`), CI (`.github/workflows/`), Docker images,
  pre-commit, CloudFormation Lambda handlers, the deploy/operator shell scripts,
  or the test suite.
- **`scripts/utils/`** — scripts NOT wired into the running pipeline but kept as
  **read-only diagnostic / reporting** tools that comply with the current data
  structure (status dashboards, corpus/integrity audits, enrichment reports,
  log analysis, schema validators run by hand).
- **`scripts/archive/`** — one-off, obsolete, or superseded scripts (completed
  migrations, data-fix/backfill one-offs, reset tools, stale API probes,
  prototypes). Not used by the current pipeline; preserved for reference.

> The whole `scripts/` tree is `COPY`'d into the pipeline Docker image, so
> presence in the image does NOT imply a script is used — see the categories
> above for what is actually wired in.

---

## Active — wired into the project

### Pipeline-invoked (imported or subprocess-run by the pipeline)
| Script | Purpose |
|--------|---------|
| `find_duplicate_people.py` | CLI wrapper for `src/dedup/find_duplicate_people.py` (pipeline uses the `src/` module) |
| `find_duplicate_places_v2.py` | CLI wrapper for `src/dedup/find_duplicate_places_v2.py` |
| `find_duplicate_groups.py` | CLI wrapper for `src/dedup/find_duplicate_groups.py` |
| `find_duplicate_equipment.py` | CLI wrapper for `src/dedup/find_duplicate_equipment.py` |
| `find_related_groups.py` | CLI wrapper for `src/dedup/find_related_groups.py` |
| `cleanup_indexes.py` | CLI wrapper for `src/dedup/cleanup_indexes.py` |
| `reclassify_military_units.py` | CLI wrapper for `src/dedup/reclassify_military_units.py` |
| `complete_metadata_with_grok.py` | Fill incomplete chapter metadata; imported by `phase2_extract` |
| `validate_phase2.py` | Phase-2 output validator; imported by `phase2_extract` |
| `validate_phase3.py` | Phase-3 validator; imported by `ecs_entrypoint` + `phase3_enrich_data` |
| `submit_table_recovery.py` | Table-recovery OCR submit; imported by `submit_ocr_job.py` |
| `_common.py` | Shared CSV-writer used by the `extract_*` scripts |

### Container entrypoints / workers (Docker)
| Script | Purpose |
|--------|---------|
| `chandra_entrypoint.sh` | Chandra OCR container ENTRYPOINT |
| `ocr_watchdog.py` | Progress-watchdog wrapper around the OCR subprocess |
| `paddle_entrypoint.sh` | PaddleOCR container ENTRYPOINT |
| `paddle_recover_page.py` | Per-page OCR recovery (Paddle image) |
| `av_entrypoint.py` | ClamAV scan container ENTRYPOINT |

### Deploy / operator (shell)
| Script | Purpose |
|--------|---------|
| `deploy_all.sh` | Full deploy (container rebuild + Lambda + CFn) |
| `deploy_aws.py` | CloudFormation deploy helper (called by `deploy_all.sh`) |
| `e2e_reset.sh` | Dev clean slate before an E2E run: pauses backstops, stops tasks, deletes NAT, sweeps DynamoDB run-state (`--wipe-title T` also clears a title's S3 content/output) |
| `update_lambdas.sh` | Lambda-only deploy (fast) |
| `apply_config_profile.py` | Apply a config overlay profile (called by `deploy_all.sh`) |
| `check_component_versions.sh` | Pre-deploy component-version preflight |
| `check_prompt_schema_alignment.py` | Prompt↔schema alignment gate (called by `deploy_all.sh`) |
| `validation_fingerprint.sh` | Hash validation code for stale-backstop detection |
| `validate_logging.sh` | Validate structured logging (called by `deploy_all.sh`) |
| `submit_ocr_job.py` | Submit a PDF to the OCR Batch pipeline (operator entry) |

### CI / quality gate
| Script | Purpose |
|--------|---------|
| `gate.sh` | Full local quality gate (mirrors CI) |
| `check_raw_writes.sh` | Raw-write bypass gate (CI + gate) |
| `check_unreachable_modules.py` | Whole-module dead-code reachability (CI + gate) |
| `radon_inherent.py` | Complexity allowlist helper (used by `gate.sh`) |
| `validation_report.py` | Schema validation report generator (CI `validation.yml`) |
| `generate_type_stubs.py` | Generate `src/types.py` (pre-commit hook) |

### Deployed as a Lambda
| Script | Purpose |
|--------|---------|
| `view_metrics.py` | Pipeline metrics viewer (`${env}-wwii-metrics` Lambda handler) |

### Structured-data ingestion toolchain (ETO Order of Battle + archival catalogs)
On-demand operator tooling for the **structured/reference-data track** (parse →
clean → reconcile printed reference material into typed CSVs). Not wired into the
narrative LLM pipeline; run by hand while that ingestion is in progress.

| Script | Purpose |
|--------|---------|
| `extract_eto_order_of_battle.py` | Parse the ETO Order of Battle PDF into typed CSVs (hub module) |
| `extract_eto_oob_division_pages.py` | Extract/merge specific OOB division page ranges |
| `clean_eto_oob_unit_names.py` | Clean OOB unit-name chronology bleed |
| `clean_eto_oob_dates.py` | Repair OOB date_from/date_to |
| `clean_eto_oob_attachment_parents.py` | Assign parent units from the group hierarchy |
| `reconcile_eto_oob_divisions.py` | Remap division labels / re-parse command staff |
| `migrate_eto_oob_attachment_detachments.py` | Re-parse mixed attach/detach pages |
| `audit_eto_oob_division_coverage.py` | Audit CSV coverage against the source PDF |
| `csv_search_replacements.py` | Shared cleanup replacements for the OOB toolchain |
| `extract_eisenhower_*.py` | Eisenhower Library catalog/guide extractors |
| `download_eisenhower_oral_histories.py` | Download Eisenhower oral-history sources |
| `extract_donovan_*.py` | Donovan research-library bibliography extractors |
| `extract_cgsc_wwii_documents.py` | CGSC WWII document extractor |
| `extract_loc_vhp_wwii_personal_narratives.py` | LoC Veterans History Project extractor |
| `build_hyperwar_biblio_index.py` | Build the HyperWar bibliography index |
| `import_hyperwar_html.py` | Import HyperWar HTML → markdown (also exercised by tests) |

### Dedup / resolution (hand-run, exercised by tests)
| Script | Purpose |
|--------|---------|
| `merge_dates.py` | Merge duplicate date entries (helpers tested) |
| `merge_equipment.py` | Merge equipment with enrichment (routes to canonical index writer) |
| `resolve_surname_people.py` | Resolve surname-only person refs (tested) |
| `resolve_title_people.py` | Resolve title-only person refs (tested) |
| `load_test.py` | Load-test sampling harness (tested) |
| `replay_cache.py` | Replay a synced Grok api-cache offline (tested) |
| `sync_to_s3.py` | Pre-stage local docs into S3 contentrepository/ (tested) |

### Documented operator / content-ingestion tools
Not auto-invoked, but documented in current guides (`LOCAL_DEPLOYMENT`,
`ADDING_DATA_SOURCES`, `CONTENT_CONTRIBUTOR_GUIDE`, `PDF_CONVERSION`,
`CODE_ARCHITECTURE`, `RUNBOOK`, `MONITORING`) as the supported way to do a task —
kept in `scripts/`.

| Script | Purpose |
|--------|---------|
| `pdf_to_markdown.py` | Convert a PDF source to markdown (PDF_CONVERSION "✅ Supported") |
| `generate_missing_metadata.py` | Generate missing chapter metadata (add-a-book workflow) |
| `extract_url.py` | Extract a URL into chapter structure (add-a-book workflow) |
| `standardize_metadata.py` | Normalize chapter metadata to YAML |
| `suggest_group_aliases.py` | Suggest group aliases (people-group consolidation workflow) |
| `consolidate_people_groups.py` | Apply group aliases / consolidate |
| `fix_orphaned_person_refs.py` | Find/fix orphaned PersonID refs (maintenance) |
| `cleanup_book_cache.py` | Clear low-value cache for a completed book (maintenance) |
| `proto_map_vision.py` | Map-interior vision extraction prototype (maps feature docs) |
| `stop_all_tasks.sh` | Emergency-stop all ECS tasks (RUNBOOK/MONITORING) |
| `run_tests.sh` | Run the test suite (LOCAL_DEPLOYMENT) |
| `ai_review.sh` | AI code-review companion to `gate.sh` (dev workflow) |
| `build_hyperwar_biblio_index.py` | Build the HyperWar bibliography index |
| `ocr_separator_check.py` | OCR separator CLI (wrapper of `src/ingestion/separator_check`) |

---

## Diagnostics — `scripts/utils/`

Read-only reporting / audit / validation tools. Not wired into the pipeline;
run by hand (or scheduled) to inspect corpus state. See `scripts/utils/README.md`.

| Script | Purpose |
|--------|---------|
| `pipeline_watch.sh` | Live run monitor: ECS tasks, alarms, S3 counts + notable lines from ALL pipeline log groups (`-f` follow, `-e` errors only, `-b BOOK`) |
| `pipeline_status.py` / `.sh` | Pipeline status dashboard / quick CLI status |
| `corpus_audit.py` | Corpus-level data-quality audit (read-only) |
| `referential_integrity_audit.py` | Cross-reference dangling-edge audit (read-only) |
| `check_output_integrity.py` | Find unparseable/corrupt output JSON |
| `validate_all_output.py` | Full schema validation of all output |
| `validate_output.py` | Quick validation of specific files |
| `validate_data.py` | Dry-run data validation |
| `validate_places.py` | Place-specific validation (coords, names) |
| `validate_supplemental_urls.py` | Check URL validity in supplemental data |
| `validate_phase3_run.sh` | Post-Phase-3 run validation sweep |
| `json_quality_report.py` / `.sh` | JSON quality / field-completeness reports |
| `enrichment_stats.py` | Enrichment coverage stats |
| `enrichment_data_check.py` | Enrichment data-presence check |
| `diagnose_enrichment.py` | Debug enrichment state |
| `find_enriched_files.py` | List files carrying enrichment data |
| `bib_by_source.py` | Bibliography grouped by source domain |
| `batch_metrics.py` | Grok Batch API throughput metrics |
| `benchmark_performance.py` | Extraction performance benchmark |
| `debug_searches.py` | Exercise all external search integrations |
| `debug_nara_search.py` | NARA search debug harness |
| `review_cache.py` | Inspect API response caches |
| `analyze_logs.sh` | Fetch + analyze recent pipeline logs |
| `monitor_logs.sh` | Tail pipeline log groups |
| `review_phase3_results.sh` | Sync + review Phase-3 bibliography results |

---

## Archived — `scripts/archive/`

One-off data-fix/backfill/reset tools, completed migrations, superseded
converters, stale API probes, and prototypes. See `scripts/archive/README.md`.
Representative (not exhaustive): `backfill_*.py`, `fix_fake_place_ulids.py`,
`fix_place_map_urls.py`, `fix_shared_subevents.py`, `reset_*.py`,
`consolidate_places.py`, `merge_equipment_dupes.py`, `merge_related_groups.py`,
`resolve_weather_places.py`, `migrate_output_content.py`, `stamp_schema_version.py`,
`process_supplemental_info.py`, `derive_bib_titles.py`, `flush_search_cache.py`,
`wipe_entity_store.py`, `rerun_equipment_only.py`, `split_chapters.py`,
`generate_dashboard.py`, `generate_schema_docs.py`, `render_mermaid_diagrams.py`,
`test_nara_api.py` / `test_nara_resolve.py` / `test_noaa_api.py`,
and the orphaned operator shells (`trigger_phase3.sh`,
`epub_monitor.sh`, `nat_teardown_after_drain.sh`, `qa_check_tests.sh`).
