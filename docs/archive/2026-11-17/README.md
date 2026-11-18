# Documentation review archive — 2026-11-17

Moved out of `docs/current/` by the 2026-11-17 review of all documentation against the code (main
`0dac86fa`). The docs that stayed were corrected in place. Paths below mirror `docs/current/`.

| Archived doc | Why archived |
|---|---|
| `design/maps-implementation-plan.md` | A step plan that is fully shipped (steps 1-6; step 7 shipped differently). The OBSERV event names it planned (`maps.analyze`, `maps.vision calls=`, `entity.backfill`) were never used. Current behaviour: `docs/current/features/maps/README.md`, `docs/current/design/maps-target-architecture.md`, `docs/current/core/PHASE3_MAP.md`. |
| `OPENSERP_URL_LIFECYCLE.md` | Dated review. Finding F2 is fixed (specific fetch-failure reasons in `src/extraction/images.py`, `src/grok_client.py`); F1 (a page URL stored as an image URL) is still open and tracked in `docs/current/TODO.md`. Current OpenSERP behaviour: `docs/current/core/PHASE3_MAP.md` section 3. |
| `TODO_COMPLETED.md` | Completed backlog items moved verbatim out of `docs/current/TODO.md` (§7 COMPLETED, the M1-M8 multimodal epic, "Recently CLOSED", the gap 27 fix), so the backlog lists open work only. |
