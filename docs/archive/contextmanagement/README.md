# Archived: `contextmanagement/` (early design snapshot, April–May 2026)

Archived on 2026-11-06. Last changed 2026-05-09 (`0d701189`). Kept for history only; **do not treat as
current**. Older docs under `docs/archive/` cite these files by their old `contextmanagement/...` path; prefix
`docs/archive/` to find them.

What superseded each part:

| Was | Now |
|---|---|
| `Steering/` (product, technology) | `.kiro/steering/project-overview.md`, `architecture-decisions.md`, `development.md`. The old file listed MongoDB and "Claude 4.5", neither of which is current. |
| `Specs/*.json`, `*_schema.json`, entity `*.md` specs | `src/schemas/` (versioned per entity, enforced by the write guard) and `docs/current/SCHEMA_REFERENCE.md` |
| `Specs/error_handling.md` | `docs/current/core/error_handling.md` (identical copy; the pattern numbers match) |
| `Specs/quality_assurance.md` | `scripts/gate.sh` (the CI-equivalent gate) and `.kiro/steering/development.md` |
| `Specs/copyright-*.md` | Moved, not archived: `docs/current/core/copyright/` |
| `Specs/get_place_gps/` (Python package) | Never imported. Geocoding now lives in `src/enrichment/places_grok_geocode.py` and `nominatim_geocode.py` |
| `Specs/design.md`, `Specs/tasks.md` | Empty |
