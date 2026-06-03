# Documentation Index

**Last Updated:** 2026-06-02

---

## Active Documents

### Deployment & Operations
| Document | Description |
|----------|-------------|
| [LOCAL_DEPLOYMENT.md](LOCAL_DEPLOYMENT.md) | Prerequisites, setup, local run instructions |
| [AWS_DEPLOYMENT.md](AWS_DEPLOYMENT.md) | CloudFormation, Lambda, ECS, S3 setup |
| [AWS_DEPLOYMENT_PLAN.md](AWS_DEPLOYMENT_PLAN.md) | Architecture decisions and cost analysis |
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
| [core/ISO_COUNTRY_CODES.md](core/ISO_COUNTRY_CODES.md) | Country code reference |

### Features (Entity Types)
| Document | Description |
|----------|-------------|
| [features/README.md](features/README.md) | Feature index (all 11 entity types) |
| [features/events/README.md](features/events/README.md) | Event extraction |
| [features/dates/README.md](features/dates/README.md) | Date extraction |
| [features/places/README.md](features/places/README.md) | Place extraction |
| [features/people/README.md](features/people/README.md) | People extraction + enrichment |
| [features/people/deduplication.md](features/people/deduplication.md) | People dedup system |
| [features/people/GROUP_DEDUPLICATION_SYSTEM.md](features/people/GROUP_DEDUPLICATION_SYSTEM.md) | Group dedup |
| [features/equipment/MILITARY_EQUIPMENT.md](features/equipment/MILITARY_EQUIPMENT.md) | Equipment extraction |
| [features/weather/README.md](features/weather/README.md) | Weather extraction |
| [features/logistics/README.md](features/logistics/README.md) | Logistics extraction |
| [features/maps/README.md](features/maps/README.md) | Maps extraction |
| [features/external-maps/README.md](features/external-maps/README.md) | External map search |
| [features/supplemental/SUPPLEMENTAL_COMPLETE.md](features/supplemental/SUPPLEMENTAL_COMPLETE.md) | Supplemental materials |
| [features/batch_processing/README.md](features/batch_processing/README.md) | Batch API processing |

### Pipeline Operations
| Document | Description |
|----------|-------------|
| [pipeline/ADDING_DATA_SOURCES.md](pipeline/ADDING_DATA_SOURCES.md) | How to add new books |
| [pipeline/HYPERWAR_HTML_IMPORT.md](pipeline/HYPERWAR_HTML_IMPORT.md) | HyperWar import process |
| [pipeline/PDF_CONVERSION.md](pipeline/PDF_CONVERSION.md) | PDF to markdown conversion |
| [pipeline/RETRY_WRAPPERS.md](pipeline/RETRY_WRAPPERS.md) | Phase 2/3 retry logic |

### Guides
| Document | Description |
|----------|-------------|
| [CONTENT_CONTRIBUTOR_GUIDE.md](CONTENT_CONTRIBUTOR_GUIDE.md) | Adding content to the repository |
| [PROMPT_EDITING_GUIDE.md](PROMPT_EDITING_GUIDE.md) | Modifying extraction prompts |
| [SCHEMA_REFERENCE.md](SCHEMA_REFERENCE.md) | JSON schemas for all entity types |
| [VALIDATION_REPORTS.md](VALIDATION_REPORTS.md) | Validation report generation |

### Specifications
| Document | Description |
|----------|-------------|
| [PHASE4_SPEC.md](PHASE4_SPEC.md) | Phase 4: Document acquisition spec |
| [TODO.md](TODO.md) | Pipeline backlog (prioritized) |

### Historical
| Document | Description |
|----------|-------------|
| [end-2-end-0.md](end-2-end-0.md) | First end-to-end run issues (2026-05-25) |

---

## Archived Documents

Review documents whose actionable items have been consolidated into [TODO.md](TODO.md):

- `docs/archive/CODE_REVIEW.md`
- `docs/archive/QA_GAPS.md`
- `docs/archive/PERFORMANCE_ENHANCEMENTS.md`
- `docs/archive/DEVOPS_RECOMMENDATIONS.md`
- `docs/archive/DATA_SCIENCE_RECOMMENDATIONS.md`
- `docs/archive/DATA_FLOW_ANALYSIS.md`
- `docs/archive/PIPELINE_REVIEW.md`
- `docs/archive/DEDUP_ANALYSIS.md`
- `docs/archive/DEDUP_ANALYSIS_ALL_ENTITIES.md`
- `docs/archive/COST_OPTIMIZATION.md`
- `docs/archive/FUTURE_ENHANCEMENTS.md`
- `docs/archive/SUGGESTED_CHANGES.md`
- `docs/archive/POST_FIX_REVIEW.md`
- `docs/archive/PROMPT_REVIEW.md`
- `docs/archive/logging.md`
- `docs/archive/end-2-end-0-ds.md`
- `docs/archive/MONGODB_IMPORT_PLAN.md`
