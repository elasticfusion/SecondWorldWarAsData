# Documentation Index

**Last Updated:** 2026-11-16

---

## Phases 0-2: where to start

| Phase | Read |
|-------|------|
| All | [core/PIPELINE.md](core/PIPELINE.md) (flow), [core/CODE_ARCHITECTURE.md](core/CODE_ARCHITECTURE.md) (modules), [core/CONFIGURATION.md](core/CONFIGURATION.md) |
| 0 Ingestion | [dataquality/INGESTION_PATHS.md](dataquality/INGESTION_PATHS.md) (as-built routing), [dataquality/CHANDRA_OCR_DESIGN.md](dataquality/CHANDRA_OCR_DESIGN.md) + [OCR_OPERATIONS.md](OCR_OPERATIONS.md) (PDF OCR, merge, cross-check), [dataquality/LANGUAGE_TRANSLATION.md](dataquality/LANGUAGE_TRANSLATION.md) |
| 1 Parse | [core/PIPELINE.md](core/PIPELINE.md) (Phase 1, incl. a scanned book's own figures), [dataquality/PHASE1_PARSE_GUARD_DESIGN.md](dataquality/PHASE1_PARSE_GUARD_DESIGN.md), [dataquality/DETERMINISTIC_EVENT_IDS_DESIGN.md](dataquality/DETERMINISTIC_EVENT_IDS_DESIGN.md) |
| 2 Extraction | [features/README.md](features/README.md) (per entity), [features/batch_processing/README.md](features/batch_processing/README.md) (xAI batch rounds, poller hand-off), [PROMPT_REFERENCE.md](PROMPT_REFERENCE.md), [PHASE2_DATA_VALIDATION.md](PHASE2_DATA_VALIDATION.md) (validation + provenance checks) |

## Active Documents

### Deployment & Operations
| Document | Description |
|----------|-------------|
| [RUNBOOK.md](RUNBOOK.md) | Operations runbook — re-runs, locks, batch states, Spot recovery, cost runaway |
| [MONITORING.md](MONITORING.md) | CloudWatch alarms, dashboard, notifications, cost controls |
| [NETWORKING_LIFECYCLE.md](NETWORKING_LIFECYCLE.md) | Dynamic NAT Gateway and VPC endpoint management |
| [LAMBDA_FUNCTIONS.md](LAMBDA_FUNCTIONS.md) | All Lambda functions, schedules, SNS topics |
| [AWS_DEPLOYMENT.md](AWS_DEPLOYMENT.md) | CloudFormation, Lambda, ECS, S3 setup |
| [GITHUB_ACTIONS_AWS_SETUP.md](GITHUB_ACTIONS_AWS_SETUP.md) | CI/CD with GitHub Actions + OIDC |
| [LOCAL_DEPLOYMENT.md](LOCAL_DEPLOYMENT.md) | Prerequisites, setup, local run instructions |
| [SECURITY_POSTURE.md](SECURITY_POSTURE.md) | S3 encryption/TLS, pandoc sandbox, AV scanning, media-mismatch, submitter freeze |
| [dataquality/AV_SCANNING_DESIGN.md](dataquality/AV_SCANNING_DESIGN.md) | Demand-launched binary-only ClamAV Fargate scan (threat model + design) |

### Core Reference
| Document | Description |
|----------|-------------|
| [core/PIPELINE.md](core/PIPELINE.md) | Complete pipeline workflow (Phases 1-4) |
| [core/CONFIGURATION.md](core/CONFIGURATION.md) | All config.yaml options |
| [core/CODE_ARCHITECTURE.md](core/CODE_ARCHITECTURE.md) | Code structure and module responsibilities |
| [SHARED_HELPERS.md](SHARED_HELPERS.md) | **Cross-feature shared helpers** (enrichment gate, merge-ref-redirect, source-recheck, name index) — integrate these in new features |
| [core/API_REFERENCE.md](core/API_REFERENCE.md) | GrokClient API |
| [core/DEVELOPMENT.md](core/DEVELOPMENT.md) | Setup and contributing |
| [core/TESTING.md](core/TESTING.md) | Test framework and conventions |
| [core/WORKFLOW_DIAGRAMS.md](core/WORKFLOW_DIAGRAMS.md) | Mermaid diagrams of all flows |
| [core/ULID_IMPLEMENTATION.md](core/ULID_IMPLEMENTATION.md) | ULID generation and validation |
| [core/copyright/README.md](core/copyright/README.md) | Copyright rules by country (books, periodicals) behind `license`/`copyright_status` |
| [core/PROMPT_MANAGEMENT.md](core/PROMPT_MANAGEMENT.md) | Prompt template system |
| [core/TEXT_UTILS.md](core/TEXT_UTILS.md) | Text normalization utilities |
| [core/JSON_REPAIR.md](core/JSON_REPAIR.md) | LLM JSON output repair |
| [core/CACHE_AUTO_RECOVERY.md](core/CACHE_AUTO_RECOVERY.md) | Poisoned cache detection |
| [core/ISO_COUNTRY_CODES.md](core/ISO_COUNTRY_CODES.md) | Country codes used in records (incl. historical: SUN, DDR) |
| [core/PHASE3_ENRICHMENT.md](core/PHASE3_ENRICHMENT.md) | Phase 3 enrichment overview |
| [core/PHASE3_MAP.md](core/PHASE3_MAP.md) | Phase 3 per-entity map, OpenSERP engagement, known gaps |

### Architecture & Design
| Document | Description |
|----------|-------------|
| [SCHEMA_REFERENCE.md](SCHEMA_REFERENCE.md) | JSON schemas for all entity types (per-entity versions) |
| [features/maps/MAP_FEATURES_SCHEMA.md](features/maps/MAP_FEATURES_SCHEMA.md) | Map-interior GeoJSON FeatureCollection schema (places/units/routes, APP-6 profile, entity-graph links) |
| [ENTITY_RELATIONSHIP_MAP.md](ENTITY_RELATIONSHIP_MAP.md) | Cross-references between entities, ID types, structural inconsistencies |
| [SCHEMA_VERSIONING.md](SCHEMA_VERSIONING.md) | Per-entity schema versions and the schema-change workflow |
| [PROMPT_REFERENCE.md](PROMPT_REFERENCE.md) | Every prompt, where it is used, what it returns |
| [design/maps-target-architecture.md](design/maps-target-architecture.md) | Maps across phases (Phase 1 capture, Phase 3 persistence) + [implementation plan](design/maps-implementation-plan.md) |
| [design/MAP_AS_SOURCE_DOCUMENT.md](design/MAP_AS_SOURCE_DOCUMENT.md) | A map recycled as a source document |
| [design/SCHEMA_ORG_REVIEW.md](design/SCHEMA_ORG_REVIEW.md) | schema.org alignment review + [tier-2 scope](design/SCHEMA_ORG_TIER2_SCOPE.md) |
| [design/SRC_SCRIPTS_SEPARATION.md](design/SRC_SCRIPTS_SEPARATION.md) | src/ vs scripts/ separation plan (PR 1 done) |
| [design/schema-enforcement-unification.md](design/schema-enforcement-unification.md) | One schema-enforcement path (phase 1 done) |
| [design/equipment-multi-run-extraction.md](design/equipment-multi-run-extraction.md) | Equipment broad + refine extraction (built) · [undercount fixes](design/equipment-undercount-fixes.md) |
| [design/deterministic-paragraph-resolution.md](design/deterministic-paragraph-resolution.md) | Quote -> paragraph resolver (prototype, not called by the pipeline) |
| [design/OPENSERP_SCOPE_REDUCTION.md](design/OPENSERP_SCOPE_REDUCTION.md) | OpenSERP scope reduction (proposal) |
| [dataquality/AWARD_SOURCING.md](dataquality/AWARD_SOURCING.md) | Country-specific authoritative award-citation sourcing (registry, routing, translation, provenance) |

### Data Quality & Integrity
| Document | Description |
|----------|-------------|
| [dataquality/DATA_QUALITY_SAFEGUARDS.md](dataquality/DATA_QUALITY_SAFEGUARDS.md) | **Start here** — the write-guard + referential + semantic-flag + catcher + backstop safeguards, with a §9 table linking every per-layer design spec (deterministic IDs, semantic flags, catcher, Phase-1 guard, S3 backstop, OpenSERP SQS). |
| [DATA_QUALITY_STATUS.md](DATA_QUALITY_STATUS.md) | Entity counts, enrichment rates, known issues, cross-ref integrity |
| [dataquality/bibliography_resolution_process.md](dataquality/bibliography_resolution_process.md) | Bibliography resolution end-to-end workflow |
| [dataquality/SOURCE_QUALIFICATION_DESIGN.md](dataquality/SOURCE_QUALIFICATION_DESIGN.md) | Derived source qualifier (primary/secondary/tertiary + proximity + evidentiary-support) on the bibliography record; Dublin Core leverage options; read-through to RAG/UI (design, pending sign-off) |
| _Ingestion — which to read when:_ | **FRONT_END** = Phase-0 per-page disposition design · **INGESTION_PATHS** = as-built routing (trigger/dispatcher) · **STRUCTURED_DATA_ROUTING** = design-of-record + provenance · **MAP_IMAGE_AV_INGESTION** = vision/media branch |
| [dataquality/INGESTION_FRONT_END.md](dataquality/INGESTION_FRONT_END.md) | Phase 0 ingestion front-end: media detection, per-page disposition, region conversion, OOB markdown parsers, division inference, persist + crosswalk |
| [dataquality/STRUCTURED_DATA_ROUTING.md](dataquality/STRUCTURED_DATA_ROUTING.md) | Phase 0 design-of-record: provenance invariant + required citation metadata, format-agnostic routing (incl. docx, web-page-with-video), tabular→entity convergence, source-acquisition lifecycle |
| [dataquality/CHANDRA_OCR_DESIGN.md](dataquality/CHANDRA_OCR_DESIGN.md) | Chandra PDF→markdown OCR (Phase 0 bridge) |
| [OCR_OPERATIONS.md](OCR_OPERATIONS.md) | OCR Batch GPU operations (Chandra queues, spot controller, debugging) |
| [dataquality/INGESTION_PATHS.md](dataquality/INGESTION_PATHS.md) | Code-synced ingestion routing (trigger_handler + dispatcher), by media type |
| [dataquality/LANGUAGE_TRANSLATION.md](dataquality/LANGUAGE_TRANSLATION.md) | Per-page translate-at-the-seam (non-English → English, original preserved) |
| [dataquality/CONCURRENCY_AND_NAT_SPEC.md](dataquality/CONCURRENCY_AND_NAT_SPEC.md) | Canonical spec: multi-job concurrency + reference-counted NAT lifecycle (partially implemented) |
| [dataquality/GPU_NETWORKING_CONSOLIDATION.md](dataquality/GPU_NETWORKING_CONSOLIDATION.md) | GPU/ECS VPC endpoint consolidation (7 endpoints, AZ alignment, deploy-time GPU probe) |
| [dataquality/MAP_IMAGE_AV_INGESTION.md](dataquality/MAP_IMAGE_AV_INGESTION.md) | Map/image/moving-image routing: deterministic-first funnel to minimize Grok, per-map dating, units-as-metadata, map georeferencing |
| [PHASE2_DATA_VALIDATION.md](PHASE2_DATA_VALIDATION.md) | Phase 2 validation (validate_phase2, provenance P2-L1/L2/Q1/Q2) · [Phase 3](PHASE3_DATA_VALIDATION.md) |
| Per-layer safeguard designs | [deterministic IDs](dataquality/DETERMINISTIC_EVENT_IDS_DESIGN.md) · [Phase-1 parse guard](dataquality/PHASE1_PARSE_GUARD_DESIGN.md) · [semantic flags](dataquality/SEMANTIC_FLAGS_DESIGN.md) · [catcher low-info/flagged](dataquality/CATCHER_LOW_INFO_FLAGGED_DESIGN.md) · [result dedup](dataquality/RESULT_DEDUP_DESIGN.md) · [S3 validation backstop](dataquality/S3_VALIDATION_BACKSTOP_DESIGN.md) |
| Phase 3 designs | [URL-sourced enrichment](dataquality/URL_SOURCED_ENRICHMENT_DESIGN.md) · [artifact store](dataquality/ARTIFACT_STORE_DESIGN.md) · [external source usage](dataquality/EXTERNAL_SOURCE_USAGE.md) · [person disambiguation](dataquality/PERSON_DISAMBIGUATION_DESIGN.md) · [OpenSERP SQS scaling](dataquality/OPENSERP_SQS_SCALING_DESIGN.md) · [OpenSERP URL lifecycle](OPENSERP_URL_LIFECYCLE.md) · [Siegfried ch.1 test reference](dataquality/SIEGFRIED_CH1_PUBLISHED_WORKS.md) |

### Features (Entity Types)
| Document | Description |
|----------|-------------|
| [features/README.md](features/README.md) | Feature index (all 11 entity types) |
| [features/batch_processing/README.md](features/batch_processing/README.md) | Batch API processing |

### Pipeline Operations
| Document | Description |
|----------|-------------|
| [pipeline/ADDING_DATA_SOURCES.md](pipeline/ADDING_DATA_SOURCES.md) | How to add new books |
| [pipeline/HYPERWAR_HTML_IMPORT.md](pipeline/HYPERWAR_HTML_IMPORT.md) | HyperWar import process |
| [pipeline/PDF_CONVERSION.md](pipeline/PDF_CONVERSION.md) | PDF to markdown conversion |
| [pipeline/PAPERS_AND_ARTICLES.md](pipeline/PAPERS_AND_ARTICLES.md) | Papers and articles as sources |
| [DataSources/loc_vhp_wwii_personal_narratives.md](DataSources/loc_vhp_wwii_personal_narratives.md) | Library of Congress Veterans History Project source |

### Guides
| Document | Description |
|----------|-------------|
| [CONTENT_CONTRIBUTOR_GUIDE.md](CONTENT_CONTRIBUTOR_GUIDE.md) | Adding content to the repository |
| [PROMPT_EDITING_GUIDE.md](PROMPT_EDITING_GUIDE.md) | Modifying extraction prompts |
| [VALIDATION_REPORTS.md](VALIDATION_REPORTS.md) | Validation report generation |

### Project Management
| Document | Description |
|----------|-------------|
| [TODO.md](TODO.md) | Pipeline backlog (prioritized) |

---

## Scripts Reference

See [scripts/README.md](../../scripts/README.md) for all 70+ utility scripts, categorized.

---

## Archived Documents

Historical implementation logs, review documents, and completed specs in `docs/archive/`:
- **`2026-11-16/`** — Phase 0-2 documentation review: archived 13 docs (the 2026-10-20/21 Phase-2 reviews and re-reviews, the CloudFormation security review (open items tracked in TODO.md), the built fetch-provenance design, the storage review, the Phase-2 observability audit, 3 design docs (container split never built, maps architecture review, organizations/equipment findings), the never-built maps S3 backend, an external-maps change note). The broken `core/error_handling.md` symlink was removed; its target is `archive/contextmanagement/Specs/error_handling.md`. See `2026-11-16/README.md`.
- **`2026-10-11/`** — comprehensive documentation-audit batch: archived 11 stale docs (3 equipment, 2 supplemental, 2 external-maps, `PHASE3_REVIEW_FINDINGS.md`, `new_entity_types.md`, `INTAKE_FRONT_DOOR_STATE.md`, `people/duplicate-exclusions.md`) + 1 merged (`external-maps/grok-search.md` → `combined-search.md`). See `2026-10-11/README.md` and `UNVALIDATED_CLAIMS_FOR_REVIEW.md`.
- **`2026-10-05/`** — documentation-audit batch: `PHASE3_REVIEW.md`, `SUPPLEMENTARY_SEARCH_REVIEW.md` (unresolved findings carried into [TODO.md](TODO.md) → High Priority), `eto_oob_division_coverage_report.md`, `ISOLATION_AUDIT.md`, `UNATTENDED_READINESS.md`, `CODE_REVIEW-2026-06-13.md`, `PROMPT_AND_MODEL_RECOMMENDATIONS.md`, `TAILSCALE_EXIT_NODE.md` + `PROTONVPN_EXIT_NODE.md` (rejected egress approaches). See `2026-10-05/README.md`.
- `CODE_INTEGRITY_REVIEW.md` — 20 findings (17 resolved, 3 remaining in TODO)
- `AWS_DEPLOYMENT_PLAN.md` — Architecture decisions and cost analysis (implemented)
- `SPOT_RECOVERY.md` — Spot termination analysis (recommendations implemented)
- `PHASE4_DYNAMODB_STORAGE.md` — Design spec (fully implemented)
- `PHASE4_SPEC.md` — Original Phase 4 spec
- `end-2-end-0.md` — First end-to-end test log

Items consolidated into [TODO.md](TODO.md).
