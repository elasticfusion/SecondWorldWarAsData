# Documentation review archive — 2026-11-16

Moved out of `docs/current/` by the 2026-11-16 review of Phases 0-2 (each doc checked against the
code; the docs that stayed were corrected in place). Paths below mirror `docs/current/`.

| Archived doc | Why archived |
|---|---|
| `PHASE2_REVIEW_2026-10-20.md`, `PHASE2_REREVIEW_2026-10-20.md`, `PHASE2_REREVIEW_2026-10-20b.md`, `PHASE2_REREVIEW_2026-10-21d.md` | Completed dated review reports; their findings are tracked in `docs/current/TODO.md` (§1.0c, §1.0d). |
| `CFN_SECURITY_REVIEW_2026-10-11.md` | Completed dated CloudFormation review. Its HIGH items 2 and 3 are still OPEN: tracked in `docs/current/TODO.md` ("CloudFormation review follow-ups"). |
| `FETCH_PROVENANCE_DESIGN.md` | A design "for confirmation before coding" that is now built: `src/utils/fetch_provenance.py`, `src/enrichment/fetch_review.py`. |
| `STORAGE_REVIEW.md` | Dated storage review (2026-10-02); its decisions live in `.kiro/steering/architecture-decisions.md`. |
| `dataquality/PHASE2_OBSERVABILITY_AUDIT.md` | Dated audit; every item marked done. |
| `design/PHASE2_CONTAINER_SPLIT.md` | Superseded: the Step Functions split was never built; replaced by the in-task batch wait + batch-poller hand-off (`phase2_extract`, exit 75, `batch_poller`). |
| `design/maps-architecture-review.md` | Pre-refactor review whose findings were fixed by maps steps 1-6; superseded by `docs/current/design/maps-target-architecture.md`. |
| `design/organizations-equipment-data-quality.md` | Dated 2026-10-31 findings/fix list; the fixes are in (`src/schemas/groups_output.py`, `prompts/people_groups.yaml`). |
| `features/maps/S3_STORAGE.md` | Describes an S3 map backend that was never built. |
| `features/external-maps/whitelist-config.md` | Dated 2026-03-02 change note citing files that no longer exist; the whitelist is documented in `docs/current/features/external-maps/combined-search.md`. |

Also removed from `docs/current/`: `core/error_handling.md`, a symlink to a file that does not
exist (`contextmanagement/Specs/error_handling.md`). The spec it pointed to is archived at
`docs/archive/contextmanagement/Specs/error_handling.md` (2026-03); links to it now point there.

The per-batch review reports (every change, with its code evidence) were working notes and are
summarised in the commit message.
