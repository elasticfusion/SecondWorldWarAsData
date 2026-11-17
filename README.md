# WWII Data Extraction Pipeline

Extract structured data from World War II historical documents using AI-powered entity extraction. Produces cross-referenced JSON entities (events, people, places, dates, equipment, and 6 more types) from markdown source material.

**Schema version:** per entity, 2.24 to 2.30 (`src/schemas/__init__.py`) | **Last Updated:** 2026-11-17

---

## Quick Start (Local)

```bash
# Prerequisites: Python 3.12+, Grok API key
git clone <repo> && cd SecondWorldWarAsData
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.yaml.example config.yaml  # Add your GROK_API_KEY
# Optional: build/compile step (if any) then:
python phase0_ingest.py               # Phase 0: normalize sources (OOB tables → output/oob/)
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
Phase 0: Ingest   →  Any source → Markdown; scanned tables → rows   (media detect, disposition, OCR)
Phase 1: Parse    →  Markdown → structured JSON                     (seconds)
Phase 2: Extract  →  Entities + events via Grok Batch API          (50% cost savings)
                     + ALL Grokipedia/Wikipedia enrichment (text, images, portraits,
                     award citations) + source_section anchor (operation + maps)
         Dedup    →  Auto-merge + human review gate
Phase 3: Enrich   →  Geocoding, Open-Meteo/NOAA, OpenSERP, NARA + non-NARA archives
                     (everything EXCEPT Grokipedia/Wikipedia — that is Phase 2)
```

Phase 0 (ingestion normalization, `src/ingestion/`) detects media type,
classifies each page's disposition, converts to Markdown, translates non-English
text to English by default (`ingestion.translate`), and parses scanned
reference tables (e.g. the ETO Order of Battle) into structured rows. It is
runnable as `python phase0_ingest.py` (local) or `ecs_entrypoint.py
phase0_ingest.py` (AWS); see [Ingestion Front-End](docs/current/dataquality/INGESTION_FRONT_END.md).

In AWS mode, Phase 2 submits requests in rounds through the Grok Batch API (50% discount; up to `batch.max_rounds` = 8, and fewer than `batch.min_requests` = 50 queued requests are answered live) and retrieves results via a Lambda poller. It then runs dedup detection. Phase 3 starts automatically only when no duplicates are pending; otherwise a human reviews them in the dedup UI first.

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
├── phase{1,2,3}_*.py        # Phase scripts (+ phase{2,3}_retry.py wrappers)
├── phase0_*.py             # Phase 0 entry points (ingest, convert, video, OCR cross-check)
├── src/
│   ├── ingestion/           # Phase 0: media detection, OCR, translation, disposition,
│   │   └── oob_markdown/    #          region conversion, OOB table parsers + crosswalk
│   ├── extraction/          # Phase 2 entity extractors
│   ├── enrichment/          # geocoding, OpenSERP, NARA, NOAA, bibliography, Wikipedia
│   ├── dedup/               # Deduplication logic
│   ├── loader/              # Relational loader (SQLite today)
│   ├── utils/               # Storage, cache, entity_store, config
│   └── grok_client.py       # Grok API (batch + real-time)
├── lambda_handlers/         # AWS Lambda functions
├── cloudformation/          # Infrastructure as code
├── prompts/                 # YAML prompt templates
├── tests/                   # 4,700+ test functions
└── scripts/                 # 85+ utility scripts and subfolders
```

---

## Documentation

- **[Complete Index](docs/current/INDEX.md)** — All documentation
- [Configuration](docs/current/core/CONFIGURATION.md) | [API Reference](docs/current/core/API_REFERENCE.md) | [Architecture](docs/current/core/CODE_ARCHITECTURE.md)
- [Lambda Functions](docs/current/LAMBDA_FUNCTIONS.md) | [Development Guide](docs/current/core/DEVELOPMENT.md) | [Scripts Reference](scripts/README.md)
- [AWS Architecture Plan](docs/archive/AWS_DEPLOYMENT_PLAN.md)

---

## Status

✅ Production: Events, Dates, Places, People, Groups, Maps, Bibliography, Dedup, Batch Mode, AWS/Local  
⚠️ Experimental: Equipment, Weather, Logistics, Casualties (see [Feature Index](docs/current/features/README.md))

---

## License

This project's code is licensed under the **Apache License 2.0** — see [LICENSE](LICENSE).

Source documents processed by the pipeline retain their own licenses (many are US Government
works in the public domain); see individual source documents for specifics.
