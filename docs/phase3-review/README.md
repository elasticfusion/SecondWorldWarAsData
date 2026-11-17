# Phase 3 review: reference files

Files pulled from the live data bucket during the Phase 3 review (findings: `docs/current/TODO.md`,
"Phase 3 review"). They are read-only copies for reference; the pipeline never reads them from here.

| File | Copied from | When | What it is |
|---|---|---|---|
| `reconcile-barrier/reconcile_complete/_run.json` | `s3://dev-wwii-data-pipeline/manifests/reconcile_complete/_run.json` | 2026-11-17 | Run marker the dedup step writes when its bibliography reconcile starts (`run_id`, `started_at`). |
| `reconcile-barrier/reconcile_complete/_all.json` | `s3://dev-wwii-data-pipeline/manifests/reconcile_complete/_all.json` | 2026-11-17 | The barrier it writes when the reconcile has finished publishing (`run_id`, `completed_at`, `canonical_count`). Phase 3 waits for it: `docs/current/core/PHASE3_MAP.md` §1, "What `EP _download_inputs` does", step 2. |

Times are epoch seconds: `_run.json` started 2026-11-16 22:43:58 UTC, `_all.json` completed 22:48:58 UTC,
same `run_id`, so Phase 3 accepts this barrier.
