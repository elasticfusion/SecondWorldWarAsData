# Documentation audit archive — 2026-10-11

These docs were moved out of `docs/current/` by the 2026-10-11 comprehensive documentation
audit because they were **stale** (superseded design / completed one-off review / never-built
proposal) and no longer describe the live code. History is preserved via `git mv`.

| Archived doc | Why archived |
|---|---|
| `PHASE3_REVIEW_FINDINGS.md` | One-off 2026-10-06 Phase-3 review; all action items closed (its open gap "casualties has no feature doc" is now false — `features/casualties/README.md` exists). |
| `dataquality/new_entity_types.md` | 2026-06 draft of Economic_Data / Policy_Items entity types; never implemented (no prompts, schemas, output dirs, or ID symbols exist). |
| `dataquality/INTAKE_FRONT_DOOR_STATE.md` | Dated deployed-state session log; superseded by `dataquality/INGESTION_PATHS.md`. Its "not wired" router claims are contradicted by the current 4-track `trigger_handler._split_by_media`. |
| `features/equipment/EQUIPMENT_ENTITY_LINKING.md` | 2026-03 before/after implementation report; contradicted by live fuzzy-linking (`_link_fuzzy`/`_link_supporting_units`). |
| `features/equipment/EQUIPMENT_ERROR_HANDLING.md` | Closed compliance review; snippets use `grok_client.chat_completion` while the live path uses `extract_json`. |
| `features/equipment/MILITARY_EQUIPMENT.md` | Self-labeled **aspirational** rich-schema proposal; the analytics it describes were never built (see `EQUIPMENT_FINAL_STRUCTURE.md` + `src/schemas/equipment_output.py` for the real schema). |
| `features/supplemental/SUPPLEMENTAL_TESTING.md` | Pre-split model; references a removed `enrich_materials_with_search()` + obsolete `output/supplemental/.materials[]` layout. |
| `features/supplemental/SUPPLEMENTAL_ERROR_HANDLING.md` | Point-in-time "error handling added" change report; snippet shape predates the `_extract_with_retry` refactor. |
| `features/external-maps/grok-implementation.md` | Dated 2026-03 QA writeup; its hardcoded `WHITELISTED_SITES` list is superseded by YAML-loaded whitelist (`load_whitelisted_sites`). Current content folded into `combined-search.md` / `whitelist-config.md`. |
| `features/external-maps/openserp-integration.md` | Old local Go / single-host `./openserp serve` setup; superseded by the AWS SQS worker architecture (`openserp_worker.py`, `src/utils/openserp_client.py`, `lambda_handlers/openserp_manager.py`). |

See `docs/current/UNVALIDATED_CLAIMS_FOR_REVIEW.md` for claims in *retained* docs that could not be
tied to code (surfaced for human review rather than archived).
