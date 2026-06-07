# Documentation Index

**Last Updated:** 2026-06-07

---

## Active Documents

### Deployment & Operations
| Document | Description |
|----------|-------------|
| [RUNBOOK.md](RUNBOOK.md) | Operations runbook — re-runs, locks, debugging, emergency |
| [LOCAL_DEPLOYMENT.md](LOCAL_DEPLOYMENT.md) | Prerequisites, setup, local run instructions |
| [AWS_DEPLOYMENT.md](AWS_DEPLOYMENT.md) | CloudFormation, Lambda, ECS, S3 setup |
| [AWS_DEPLOYMENT_PLAN.md](AWS_DEPLOYMENT_PLAN.md) | Architecture decisions and cost analysis |
| [LAMBDA_FUNCTIONS.md](LAMBDA_FUNCTIONS.md) | All 9 Lambda functions documented |
| [GITHUB_ACTIONS_AWS_SETUP.md](GITHUB_ACTIONS_AWS_SETUP.md) | CI/CD with GitHub Actions + OIDC |
| [SPOT_RECOVERY.md](SPOT_RECOVERY.md) | Spot instance termination handling |

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
| [PHASE4_DYNAMODB_STORAGE.md](PHASE4_DYNAMODB_STORAGE.md) | DynamoDB entity store (Phase A-D) |
| [SCHEMA_REFERENCE.md](SCHEMA_REFERENCE.md) | JSON schemas for all entity types |

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

### Data Quality
| Document | Description |
|----------|-------------|
| [dataquality/bibliography_resolution_process.md](dataquality/bibliography_resolution_process.md) | Bibliography resolution workflow |

---

## Scripts Reference

See [scripts/README.md](../../scripts/README.md) for all 70+ utility scripts, categorized.

---

## Archived Documents

Historical implementation logs, review documents, and completed specs in `docs/archive/`. Not maintained. Items consolidated into [TODO.md](TODO.md).
