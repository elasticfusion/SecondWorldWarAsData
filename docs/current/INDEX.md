# Documentation Index

**Last Updated:** 2026-06-15

---

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
| [CODE_REVIEW.md](CODE_REVIEW.md) | Latest code review findings (auto-generated) |

### Core Reference
| Document | Description |
|----------|-------------|
| [core/PIPELINE.md](core/PIPELINE.md) | Complete pipeline workflow (Phases 1-4) |
| [core/CONFIGURATION.md](core/CONFIGURATION.md) | All config.yaml options |
| [core/CODE_ARCHITECTURE.md](core/CODE_ARCHITECTURE.md) | Code structure and module responsibilities |
| [core/API_REFERENCE.md](core/API_REFERENCE.md) | GrokClient API |
| [core/DEVELOPMENT.md](core/DEVELOPMENT.md) | Setup and contributing |
| [core/TESTING.md](core/TESTING.md) | Test framework and conventions |
| [core/WORKFLOW_DIAGRAMS.md](core/WORKFLOW_DIAGRAMS.md) | Mermaid diagrams of all flows |
| [core/ULID_IMPLEMENTATION.md](core/ULID_IMPLEMENTATION.md) | ULID generation and validation |
| [core/PROMPT_MANAGEMENT.md](core/PROMPT_MANAGEMENT.md) | Prompt template system |
| [core/TEXT_UTILS.md](core/TEXT_UTILS.md) | Text normalization utilities |
| [core/JSON_REPAIR.md](core/JSON_REPAIR.md) | LLM JSON output repair |
| [core/CACHE_AUTO_RECOVERY.md](core/CACHE_AUTO_RECOVERY.md) | Poisoned cache detection |

### Architecture & Design
| Document | Description |
|----------|-------------|
| [SCHEMA_REFERENCE.md](SCHEMA_REFERENCE.md) | JSON schemas for all entity types |
| [ENTITY_RELATIONSHIP_MAP.md](ENTITY_RELATIONSHIP_MAP.md) | Cross-references between entities, ID types, structural inconsistencies |

### Data Quality & Integrity
| Document | Description |
|----------|-------------|
| [DATA_QUALITY_STATUS.md](DATA_QUALITY_STATUS.md) | Entity counts, enrichment rates, known issues, cross-ref integrity |
| [dataquality/new_entity_types.md](dataquality/new_entity_types.md) | Proposed Economic Data and Policy/Legislation types |
| [dataquality/bibliography_resolution_process.md](dataquality/bibliography_resolution_process.md) | Bibliography resolution end-to-end workflow |

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
- `CODE_INTEGRITY_REVIEW.md` — 20 findings (17 resolved, 3 remaining in TODO)
- `AWS_DEPLOYMENT_PLAN.md` — Architecture decisions and cost analysis (implemented)
- `SPOT_RECOVERY.md` — Spot termination analysis (recommendations implemented)
- `PHASE4_DYNAMODB_STORAGE.md` — Design spec (fully implemented)
- `PHASE4_SPEC.md` — Original Phase 4 spec
- `end-2-end-0.md` — First end-to-end test log

Items consolidated into [TODO.md](TODO.md).
