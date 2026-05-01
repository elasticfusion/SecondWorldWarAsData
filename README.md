# WWII Data Extraction Pipeline

Extract structured data from World War II historical documents using AI-powered entity extraction.

**Status:** Production Ready  
**Version:** 2.2  
**Last Updated:** 2026-04-26

---

## Deployment Options

| Mode | Description | Guide |
|------|-------------|-------|
| **Local** | Run on your machine with filesystem storage | **[Local Deployment Guide](docs/current/LOCAL_DEPLOYMENT.md)** |
| **AWS** | ECS Fargate + S3 + DynamoDB | **[AWS Deployment Guide](docs/current/AWS_DEPLOYMENT.md)** |

Both modes use the same codebase. Set `aws.enabled: true` in `config.yaml` to switch to AWS mode.

---

## What It Does

Extracts 11 entity types from WWII historical documents:

- **Events** — Battles, operations, actions with hierarchical sub-events
- **Dates** — Temporal mentions with precision (exact, early, mid, late, seasonal)
- **Places** — Geographic locations with GPS coordinates and hierarchy
- **People** — Biographical profiles with enrichment from Wikipedia/Grokipedia
- **Military Units** — Organizations, hierarchies, and member tracking
- **Equipment** — Weapons, vehicles, specifications with media
- **Weather** — Historical weather data (extracted + Open-Meteo API)
- **Logistics** — Supply chain information with severity and resolution tracking
- **Casualties** — Casualty tracking with impacted entities
- **Maps** — Source and external maps with image downloads
- **Citations** — Bibliography and supplemental references

---

## Pipeline Phases

```
Phase 1: Parse     — Markdown → JSON (seconds)
Phase 2: Extract   — JSON → 11 entity types via Grok API (minutes per chapter)
        Dedup     — Duplicate detection + human review gate
Phase 3: Enrich    — People/groups/places/bibliography enrichment (seconds per entity)
Phase 4: Import    — JSON → MongoDB or DynamoDB (seconds)
```

See [Pipeline Overview](docs/current/core/PIPELINE.md) | [Workflow Diagrams](docs/current/core/WORKFLOW_DIAGRAMS.md)

---

## Project Structure

```
SecondWorldWarAsData/
├── config.yaml                    # Configuration (local + AWS)
├── Dockerfile                     # Pipeline container image
├── ecs_entrypoint.py              # ECS task entrypoint (S3 sync, config patch)
├── phase1_parse.py                # Parse markdown → JSON
├── phase2_extract.py              # Extract entities
├── phase2_retry.py                # Retry wrapper
├── phase3_enrich_data.py          # Enrich with external data
├── phase3_retry.py                # Retry wrapper
├── import_to_mongodb.py           # Import to MongoDB
├── import_to_dynamodb.py          # Import to DynamoDB
├── prompts/                       # YAML prompt templates (S3-overridable)
├── contentrepository/             # Source documents (markdown)
├── output/                        # Extracted data (JSON)
├── src/                           # Source code
│   ├── extraction/                # Extraction modules (11 entity types)
│   ├── utils/                     # Utilities (storage, cache, config, prompt_loader)
│   └── grok_client.py             # Grok API client
├── lambda_handlers/               # AWS Lambda (trigger, dedup UI, auth, metrics)
├── cloudformation/                # AWS infrastructure templates
├── scripts/                       # Utility scripts
├── tools/                         # Go tools (OpenSERP search)
└── docs/                          # Documentation
```

---

## Output Format

All data is JSON with ULIDs for cross-referencing:

```json
{
  "EventID": "01KHXNSE0W41DV7VV6PEMDJJ5H",
  "Event_Name": "Operation Overlord",
  "Sub-events": [
    {
      "Sub-eventID": "01KHXNSE0WX99GG0CB53CD2242",
      "Sub-event_summary": "D-Day landings at Normandy",
      "dates": ["01KHYP2M4N6P8Q0R2S4T6V8W0X"],
      "places": ["01KHYP2N5P7Q9R1S3T5V7W9X1Z"],
      "people": ["01KHYP2P6Q8R0S2T4V6W8X0Y2Z"]
    }
  ]
}
```

See [Schema Reference](docs/current/SCHEMA_REFERENCE.md)

---

## Documentation

### Deployment
- **[Local Deployment](docs/current/LOCAL_DEPLOYMENT.md)** — Prerequisites, setup, run, troubleshoot
- **[AWS Deployment](docs/current/AWS_DEPLOYMENT.md)** — CloudFormation, Lambda, ECS, S3
- **[AWS Architecture Plan](docs/current/AWS_DEPLOYMENT_PLAN.md)** — Design decisions and cost analysis

### Core
- **[Pipeline Overview](docs/current/core/PIPELINE.md)** — Complete pipeline workflow
- **[Configuration](docs/current/core/CONFIGURATION.md)** — All config options
- **[API Reference](docs/current/core/API_REFERENCE.md)** — GrokClient API
- **[Architecture](docs/current/core/CODE_ARCHITECTURE.md)** — Code structure
- **[Development Guide](docs/current/core/DEVELOPMENT.md)** — Setup and contributing

### Features
- **[Feature Index](docs/current/features/README.md)** — All 11 entity types
- **[Batch Processing](docs/current/features/batch_processing/README.md)** — Parallel extraction
- **[Scripts Reference](scripts/README.md)** — 40+ utility scripts

### Reference
- **[Schema Reference](docs/current/SCHEMA_REFERENCE.md)** — JSON schemas for all entity types
- **[Complete Documentation Index](docs/current/INDEX.md)** — Everything

---

## Project Status

**Production Ready:**
- ✅ Events, Dates, Places, People, Groups, Maps, Supplemental
- ✅ Deduplication, Validation, MongoDB/DynamoDB Import
- ✅ Local and AWS deployment modes

**Experimental:**
- ⚠️ Weather, Equipment, Logistics, Casualties

---

## License

Public Domain (US Government works). See individual source documents for specific licenses.
