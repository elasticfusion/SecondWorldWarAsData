# Diagnostic Utilities (`scripts/utils/`)

Read-only **diagnostic / reporting / validation** tools that are NOT wired into
the running pipeline (no import, subprocess, CI, Docker, pre-commit, or Lambda
reference) but are kept because they collect valuable diagnostic information and
comply with the current data structure.

## Why these are here (vs `scripts/archive/`)

- They **do not mutate** the corpus — they read `output/` (or CloudWatch logs /
  DynamoDB) and emit a report, dashboard, or validation result.
- They track the **current** schema/output layout (`output/<entity>/`,
  `output/content/<book>/`, `_schema_version`, cross-reference edges), so they
  still run against today's data.
- Archived scripts, by contrast, are one-off data fixers/migrations, superseded
  converters, stale API probes, or prototypes.

## Contents

| Script | Reads | Produces |
|--------|-------|----------|
| `pipeline_status.py` / `pipeline_status.sh` | DynamoDB / ECS / S3 | pipeline status dashboard |
| `corpus_audit.py` | `output/` | corpus data-quality audit (schema + referential + PK) |
| `referential_integrity_audit.py` | `output/` | dangling cross-reference report |
| `check_output_integrity.py` | `output/` | unparseable/corrupt JSON report |
| `validate_all_output.py` | `output/` | full schema validation |
| `validate_output.py` | `output/` | quick per-file validation |
| `validate_data.py` | a directory | dry-run schema validation |
| `validate_places.py` | `output/places/` | place structure/coord validation |
| `validate_supplemental_urls.py` | supplemental files | URL validity report |
| `validate_phase3_run.sh` | S3 + `output/` | post-Phase-3 validation sweep |
| `json_quality_report.py` / `.sh` | logs / `output/` | JSON quality / field completeness |
| `enrichment_stats.py` | `output/` | enrichment coverage stats |
| `enrichment_data_check.py` | `output/` | enrichment data-presence check |
| `diagnose_enrichment.py` | `output/` | enrichment state debug |
| `find_enriched_files.py` | `output/` | list enriched files |
| `bib_by_source.py` | `output/bibliography/` | bibliography-by-source-domain report |
| `batch_metrics.py` | Grok Batch API / DynamoDB | batch throughput metrics |
| `benchmark_performance.py` | `output/` | extraction performance benchmark |
| `debug_searches.py` | external search APIs | search-integration debug |
| `debug_nara_search.py` | NARA API | NARA search debug |
| `review_cache.py` | api cache | cache inspection |
| `analyze_logs.sh` | CloudWatch | recent-log analysis |
| `monitor_logs.sh` | CloudWatch | live log tail |
| `review_phase3_results.sh` | S3 | Phase-3 bibliography review |

## Running

Run from the repo root with the venv active, e.g.:

```bash
source .venv/bin/activate
python scripts/utils/corpus_audit.py
python scripts/utils/enrichment_stats.py
bash scripts/utils/pipeline_status.sh
```
