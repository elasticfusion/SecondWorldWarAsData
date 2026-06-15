# WWII Data Extraction Pipeline

Extract structured data from World War II historical documents using AI-powered entity extraction. Produces cross-referenced JSON entities (events, people, places, dates, equipment, and 6 more types) from markdown source material.

**Version:** 2.3 | **Last Updated:** 2026-06-05

---

## Quick Start (Local)

```bash
# Prerequisites: Python 3.12+, Grok API key
git clone <repo> && cd SecondWorldWarAsData
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.yaml.example config.yaml  # Add your GROK_API_KEY
python phase1_parse.py               # Parse → JSON
python phase2_extract.py             # Extract entities (uses Grok API)
python phase3_enrich_data.py         # Enrich with external data

# Optional: container vulnerability scanning (used by deploy_all.sh)
curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sudo sh -s -- -b /usr/local/bin
```

Full setup: [Local Deployment Guide](docs/current/LOCAL_DEPLOYMENT.md) | AWS: [AWS Deployment Guide](docs/current/AWS_DEPLOYMENT.md)

---

## Pipeline

```
Phase 1: Parse    →  Markdown → structured JSON               (seconds)
Phase 2: Extract  →  11 entity types via Grok Batch API       (50% cost savings)
         Dedup    →  Auto-merge + human review gate
Phase 3: Enrich   →  Wikipedia, OpenSERP, Open-Meteo, NARA    (per entity)
```

In AWS mode, Phase 2 submits requests asynchronously via the Grok Batch API (50% discount), retrieves results via Lambda poller, then runs dedup and enrichment automatically.

See [Pipeline Overview](docs/current/core/PIPELINE.md) | [Workflow Diagrams](docs/current/core/WORKFLOW_DIAGRAMS.md)

---

## Output

All entities are JSON with ULIDs for cross-referencing:

```json
{
  "EventID": "01KHXNSE0W41DV7VV6PEMDJJ5H",
  "Event_Name": "Operation Overlord",
  "Sub-events": [{
    "Sub-eventID": "01KHXNSE0WX99GG0CB53CD2242",
    "Sub-event_summary": "D-Day landings at Normandy",
    "dates": ["01KHYP2M4N..."],
    "places": ["01KHYP2N5P..."],
    "people": ["01KHYP2P6Q..."]
  }]
}
```

11 entity types: Events, Dates, Places, People, Military Units, Equipment, Weather, Logistics, Casualties, Maps, Citations. See [Schema Reference](docs/current/SCHEMA_REFERENCE.md) | [Feature Index](docs/current/features/README.md)

---

## Architecture

```
SecondWorldWarAsData/
├── config.yaml              # All configuration (local + AWS)
├── ecs_entrypoint.py        # ECS orchestrator (S3 sync, batch, dedup)
├── phase{1,2,3}_*.py        # Phase scripts
├── src/
│   ├── extraction/          # 11 entity extractors
│   ├── enrichment/          # OpenSERP, Wikipedia, NARA
│   ├── dedup/               # Deduplication logic
│   ├── utils/               # Storage, cache, entity_store, config
│   └── grok_client.py       # Grok API (batch + real-time)
├── lambda_handlers/         # AWS Lambda functions
├── cloudformation/          # Infrastructure as code
├── prompts/                 # YAML prompt templates
├── tests/                   # 332 passing tests
└── scripts/                 # 40+ utility scripts
```

---

## Documentation

- **[Complete Index](docs/current/INDEX.md)** — All documentation
- [Configuration](docs/current/core/CONFIGURATION.md) | [API Reference](docs/current/core/API_REFERENCE.md) | [Architecture](docs/current/core/CODE_ARCHITECTURE.md)
- [Lambda Functions](docs/current/LAMBDA_FUNCTIONS.md) | [Development Guide](docs/current/core/DEVELOPMENT.md) | [Scripts Reference](scripts/README.md)
- [AWS Architecture Plan](docs/current/AWS_DEPLOYMENT_PLAN.md)

---

## Status

✅ Production: Events, Dates, Places, People, Groups, Maps, Supplemental, Dedup, Batch Mode, AWS/Local  
⚠️ Experimental: Weather, Equipment, Logistics, Casualties

---

## License

Public Domain (US Government works). See individual source documents for specific licenses.
