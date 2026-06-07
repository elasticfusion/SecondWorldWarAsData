# Scripts Reference

Utility scripts for pipeline operations, data management, and maintenance.

---

## Deduplication

| Script | Purpose |
|--------|---------|
| `find_duplicate_people.py` | Score people pairs, output duplicate_report.json |
| `find_duplicate_places_v2.py` | Score place pairs by name + coordinates |
| `find_duplicate_equipment.py` | Score equipment pairs |
| `find_duplicate_groups.py` | Score group pairs |
| `find_related_groups.py` | Find groups with overlapping members |
| `merge_equipment_dupes.py` | Auto-merge exact equipment duplicates |
| `merge_equipment.py` | Merge equipment with enrichment data |
| `merge_related_groups.py` | Merge related groups |
| `merge_dates.py` | Merge duplicate date entries |
| `consolidate_people_groups.py` | Consolidate overlapping groups |
| `consolidate_places.py` | Consolidate nearby places |
| `resolve_surname_people.py` | Resolve surname-only person refs |
| `resolve_title_people.py` | Resolve title-only person refs |

## Validation & QA

| Script | Purpose |
|--------|---------|
| `validate_all_output.py` | Full schema validation of all output |
| `validate_output.py` | Quick validation of specific files |
| `validate_data.py` | Data integrity checks |
| `validate_places.py` | Place-specific validation (coords, names) |
| `validate_supplemental_urls.py` | Check URL validity in supplemental data |
| `json_quality_report.py` | Generate quality metrics report |
| `validation_report.py` | Detailed validation report with suggestions |
| `qa_check_tests.sh` | Run QA test suite |

## Pipeline Operations (AWS)

| Script | Purpose |
|--------|---------|
| `deploy_all.sh` | Full deploy (container rebuild + Lambda + CFn) |
| `deploy_aws.py` | Python deploy helper |
| `update_lambdas.sh` | Lambda-only deploy (fast) |
| `monitor_logs.sh` | Tail CloudWatch logs + task status |
| `pipeline_status.py` | Pipeline status dashboard |
| `pipeline_status.sh` | Quick CLI status check |
| `stop_all_tasks.sh` | Emergency stop all ECS tasks |
| `trigger_phase3.sh` | Manual Phase 3 trigger |
| `view_metrics.py` | View batch/extraction metrics |

## Data Fixes & Backfills

| Script | Purpose |
|--------|---------|
| `backfill_date_fields.py` | Add missing fields to date entities |
| `backfill_equipment_media.py` | Re-run media enrichment for equipment |
| `backfill_group_fields.py` | Add missing fields to group entities |
| `fix_fake_place_ulids.py` | Replace placeholder ULIDs |
| `fix_orphaned_person_refs.py` | Clean orphaned person references |
| `fix_place_map_urls.py` | Fix broken map URLs in places |
| `fix_shared_subevents.py` | Deduplicate shared sub-event IDs |
| `reclassify_military_units.py` | Move military units from places to groups |
| `cleanup_indexes.py` | Remove stale entries from index files |
| `cleanup_book_cache.py` | Clear book-specific cache |
| `reset_archive_org_results.py` | Clear archive.org search results for retry |
| `reset_nara_results.py` | Clear NARA results for retry |
| `reset_openserp_flag.py` | Reset OpenSERP searched flag |

## Enrichment & Analysis

| Script | Purpose |
|--------|---------|
| `enrichment_stats.py` | Show enrichment coverage stats |
| `enrichment_data_check.py` | Verify enrichment data quality |
| `diagnose_enrichment.py` | Debug failed enrichment |
| `find_enriched_files.py` | List enriched entity files |
| `suggest_group_aliases.py` | Suggest alias additions for groups |
| `bib_by_source.py` | Bibliography by source type |
| `derive_bib_titles.py` | Derive titles from bibliography entries |

## Content Processing

| Script | Purpose |
|--------|---------|
| `complete_metadata_with_grok.py` | Fill incomplete chapter metadata via AI |
| `generate_missing_metadata.py` | Generate missing meta.yaml files |
| `standardize_metadata.py` | Normalize metadata format |
| `import_hyperwar_html.py` | Import HyperWar HTML to markdown |
| `pdf_to_markdown.py` | Convert PDF sources to markdown |
| `split_chapters.py` | Split large markdown files into chapters |
| `process_supplemental_info.py` | Process supplemental material |
| `migrate_output_content.py` | Migrate output content structure |

## Code Generation & Docs

| Script | Purpose |
|--------|---------|
| `generate_schema_docs.py` | Generate schema documentation |
| `generate_type_stubs.py` | Generate Python type stubs |
| `generate_dashboard.py` | Generate project dashboard |
| `render_mermaid_diagrams.py` | Render Mermaid diagrams to images |
| `stamp_schema_version.py` | Stamp schema version on output files |

## Testing & Benchmarks

| Script | Purpose |
|--------|---------|
| `run_tests.sh` | Run full test suite |
| `benchmark_performance.py` | Performance benchmarks |
| `test_nara_api.py` | Test NARA API connectivity |
| `test_nara_resolve.py` | Test NARA resolution |
| `test_noaa_api.py` | Test NOAA API connectivity |
| `validate_logging.sh` | Validate logging output |
| `validate_phase3_run.sh` | Validate Phase 3 results |

## Utilities

| Script | Purpose |
|--------|---------|
| `extract_url.py` | Extract URLs from text |
| `review_cache.py` | Inspect API cache contents |
| `analyze_logs.sh` | Analyze pipeline log files |

## Archived

Obsolete or superseded scripts in `scripts/archive/`. Not used in current pipeline.
