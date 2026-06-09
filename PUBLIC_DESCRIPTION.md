# Second World War as Data

## Project Overview

**Second World War as Data** is an open-source initiative that transforms World War II historical documents into structured, machine-readable data using artificial intelligence.

The project applies AI-powered entity extraction to primary and secondary source materials, producing a comprehensive, cross-referenced knowledge graph of the Second World War — covering events, people, places, dates, military units, equipment, weather conditions, logistics, casualties, maps, and citations.

## Why This Matters

Decades of WWII scholarship exist in narrative form — books, reports, after-action reviews, and government archives. This project makes that knowledge computationally accessible for the first time, enabling:

- **Researchers** to query and cross-reference events, people, and places at scale
- **Educators** to build interactive timelines and data-driven curricula
- **Developers** to create applications on top of structured WWII data
- **Archivists** to connect disparate document collections through shared entity references

## How It Works

A three-phase pipeline processes historical documents:

1. **Parse** — Converts markdown source material into structured JSON
2. **Extract** — Identifies 11 entity types using AI (via the Grok API), with automatic deduplication and human review
3. **Enrich** — Augments entities with data from Wikipedia, NARA (National Archives), weather records, and web sources

Every entity receives a unique identifier (ULID), allowing full cross-referencing across the dataset.

## Technical Highlights

- 11 entity types with defined schemas
- Batch API processing for cost efficiency (50% savings)
- Runs locally or on AWS (ECS + Lambda + S3)
- Automatic deduplication with human review gate
- 332 passing tests
- Public domain output (US Government works)

## Current Status

The project is in active development. Events, Dates, Places, People, Groups, Maps, and Supplemental entities are production-ready. Weather, Equipment, Logistics, and Casualties extraction is experimental.

## Get Involved

This is a public domain project. Contributions, feedback, and collaboration from historians, data scientists, and developers are welcome.

## Contact

For inquiries about the project, collaboration opportunities, or media requests, please open an issue on the repository.

---

*Turning history into data — so the past can inform the future.*
