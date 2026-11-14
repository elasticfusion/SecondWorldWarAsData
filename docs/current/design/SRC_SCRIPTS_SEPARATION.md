# Plan: Separate `src/` (library) from `scripts/` (entry points)

**Status:** decisions D1 to D4 made 2026-11-06 (see §5). PR 1 in progress (branch `refactor/pr1-dedup-into-src`).
**Rule this plan establishes:** `scripts/` may import `src/`; `src/`, `lambda_handlers/`, the phase
scripts and `ecs_entrypoint.py` must never import `scripts/`. Anything the pipeline runs lives in `src/`
(or `lambda_handlers/`). `scripts/` keeps command-line tools, entrypoints and thin wrappers.

---

## 1. Current state (measured 2026-11-06)

`src/` has 226 Python files and is what the gate checks. `scripts/` has 64 top-level files, plus 21 in
`utils/` and 45 in `archive/`. Most of `scripts/` already follows the rule (20 scripts import `src/`,
58 import edges). The exceptions are below. Found with the code graph plus an AST pattern search, because
the graph does not record imports made inside a function.

### 1.1 Pipeline code that imports `scripts/`

| Consumer | Imports from `scripts/` |
|---|---|
| `ecs_entrypoint.py:1963` | `validate_phase3.run` |
| `ecs_entrypoint.py:2373` | `reclassify_military_units.reclassify` |
| `ecs_entrypoint.py:2383` | `cleanup_indexes.cleanup_index` |
| `ecs_entrypoint.py:2694` | `find_duplicate_groups._group_nationality_conflict` |
| `ecs_entrypoint.py:2773-2786` | runs `find_duplicate_{people,places_v2,groups,equipment}.py` as subprocesses |
| `phase2_extract.py:21-22` | `find_duplicate_people.generate_duplicate_report`, `find_related_groups.generate_related_groups_report` |
| `phase2_extract.py:199` | `complete_metadata_with_grok.{is_metadata_incomplete, extract_metadata_with_grok}` |
| `phase2_extract.py:1207` | `validate_phase2.run` |
| `phase3_enrich_data.py:971` | `validate_phase3.run` |
| `src/dedup/resolved_set.py:37,116` | `find_duplicate_people._extract_last_name/_normalize_unicode/_similarity_ratio` |
| `src/dedup/surname_report.py:54` | `find_duplicate_people._extract_last_name` (via `import_module`) |
| `cloudformation/compute.yaml:894` | Lambda `Handler: scripts.view_metrics.handler` |
| `scripts/update_lambdas.sh:28-32` | hand-copies 7 named scripts into the Lambda zip |

No module under `lambda_handlers/` imports `scripts/` directly.

### 1.2 What this costs

- **Not checked by the gate.** `gate.sh` runs pylint, mypy, bandit, ruff, radon and vulture over `src/` and
  `lambda_handlers/` only. `check_unreachable_modules.py` and `check_raw_writes.sh` also skip `scripts/`.
  So about 4,700 lines of pipeline code (the 11 modules in §2) get only black and tests.
- **A real data-quality bypass is hidden by that gap.** `reclassify_military_units.reclassify` (line 100)
  writes a `people_groups` **entity record** with a raw `write_text(json.dumps(...))`. That skips the central
  write guard (schema validation, PK repair, semantic flags, stats catcher). It runs every dedup pass. The
  raw-write gate exists to catch exactly this, but `scripts/` is outside its scope.
- **Possible resurrected place records (unverified).** The same function `f.unlink()`s the source place
  file but does not call `ecs_entrypoint.track_deletion`, so the final S3 sync probably does not delete it.
  The place could reappear on the next download.
- **Silent degradation.** `resolved_set._norm_key` and `surname_report._extract_last_name` wrap the
  `scripts` import in `except Exception` and quietly fall back to a cruder last-name rule. If the import
  broke, dedup blocking would change with no alert, which goes against the observability rule.
- **Silent skip of a dedup step.** `ecs_entrypoint` runs each dedup script only
  `if Path(f"/app/{script}").exists()`, with no `else`. A wrong path skips that detector with no log.
- **Metrics Lambda is probably broken (unverified).** `scripts.view_metrics.handler` is the deployed
  handler, but `view_metrics.py` is not in `update_lambdas.sh`'s copy list, so the zip likely lacks it.
  Not checked live: the AWS session had expired.

---

## 2. Target layout

Moves are done with `git mv` and keep module names, so each shows as a rename and the diff stays
reviewable. Renames such as dropping `_v2` are optional follow-ups.

| From | To | Lines |
|---|---|---|
| `scripts/find_duplicate_people.py` | `src/dedup/find_duplicate_people.py` | 1,494 |
| `scripts/find_duplicate_places_v2.py` | `src/dedup/find_duplicate_places_v2.py` | 459 |
| `scripts/find_duplicate_groups.py` | `src/dedup/find_duplicate_groups.py` | 482 |
| `scripts/find_duplicate_equipment.py` | `src/dedup/find_duplicate_equipment.py` | 330 |
| `scripts/find_related_groups.py` | `src/dedup/find_related_groups.py` | 664 |
| `scripts/cleanup_indexes.py` | `src/dedup/cleanup_indexes.py` | 135 |
| `scripts/reclassify_military_units.py` | `src/dedup/reclassify_military_units.py` | 125 |
| `scripts/validate_phase2.py` | `src/validation/validate_phase2.py` (new package) | 358 |
| `scripts/validate_phase3.py` | `src/validation/validate_phase3.py` | 296 |
| `scripts/complete_metadata_with_grok.py` | `src/ingestion/complete_metadata_with_grok.py` | 158 |
| `scripts/view_metrics.py` | `lambda_handlers/view_metrics.py` | 178 |

**CLI wrappers stay in `scripts/`** for the operator commands that current docs cite (`LOCAL_DEPLOYMENT`,
`RUNBOOK`, `DATA_QUALITY_STATUS`, `CONTENT_CONTRIBUTOR_GUIDE`, `scripts/README.md`). Each is a few lines:
`from src.dedup.find_duplicate_people import main` then `main()`. These are the allowed direction.

---

## 3. Work, as separate PRs

Each PR follows `.kiro/steering/development.md`: it passes `scripts/gate.sh`, gets independent code and
security reviews, passes the GitHub pipeline, adds observability where it touches a failure path, and adds
no new AWS or Grok cost (this is a refactor).

### PR 1: Dedup family into `src/dedup/` (largest; 7 modules)

1. `git mv` the 7 dedup modules (§2). Leave CLI wrappers in `scripts/`.
2. Update imports:
   - `phase2_extract.py:21-22`
   - `ecs_entrypoint.py:2373, 2383, 2694`
   - `src/dedup/resolved_set.py:37, 116`
   - `src/dedup/surname_report.py:54`
   - `find_duplicate_groups.py:61, 425`, which import `find_duplicate_people` within the family
3. `ecs_entrypoint._run_dedup_detection` (2773-2786): run detectors as
   `[sys.executable, "-m", "src.dedup.<module>"]` and remove the `Path(...).exists()` check. A failure to
   start becomes `observe_error("dedup", "detector_failed", ...)`, not a silent skip.
4. `resolved_set` / `surname_report`: drop the `except Exception` fallback. The import is now within the
   same package. If a guard is still wanted, use `observe_warn`, not a silent fallback.
5. **Fix the bypass the move exposes:** `reclassify_military_units.reclassify` writes through
   `write_json_with_lock(dest, group_data, entity="people_groups")`. Check the `f.unlink()` /
   `track_deletion` question (§1.2). If confirmed, return the removed paths so the caller can record them.
6. Update test imports: `test_dedup_scripts`, `test_container_code`, `test_duplicate_detection`,
   `test_equipment_origin_norm`, `test_equipment_gap_fixes`, `test_equipment_country_model`. Also search
   tests for string patch targets `"scripts.find_duplicate_*..."`.
7. `update_lambdas.sh`: delete the 7-file copy loop, since `src/` is already copied.
8. Gate items, all measured in §4:
   - Add a golden-output test first (§6) that captures today's duplicate reports from a fixed fixture.
   - Grandfather the 18 complexity-C functions in `scripts/.radon_inherent` at their new paths, with the reason
     "moved unrefactored; refactor tracked in TODO" (D4).
   - Fix the 2 mypy errors in `find_related_groups`.
   - Raise the raw-write baseline by 6, with justification.
9. Update docs that name these paths: `scripts/README.md`, `core/CODE_ARCHITECTURE.md`, `core/PIPELINE.md`,
   `DATA_QUALITY_STATUS.md`, `LOCAL_DEPLOYMENT.md`.

### PR 2: Validators, metadata completion, metrics Lambda

1. `validate_phase2.py` and `validate_phase3.py` move to `src/validation/`. Update:
   - `phase2_extract.py:1207`, `ecs_entrypoint.py:1963`, `phase3_enrich_data.py:971`
   - `validate_phase3.py:41`, removing its `# type: ignore[import-not-found]`
   - tests: `test_validate_phase3`, `test_phase3_med_fixes`, `test_casualties_drop_and_orphan`, `test_validate_phase2`
   - Remove the unused `entity_version` import (vulture). Handle the 2 complexity-C functions in
     `validate_phase3` (D4).
2. `complete_metadata_with_grok.py` moves to `src/ingestion/`. Update `phase2_extract.py:199` and keep a
   CLI wrapper.
3. `view_metrics.py` moves to `lambda_handlers/` (the reachability allowlist already covers that prefix).
   - Set `compute.yaml:894` to `Handler: lambda_handlers.view_metrics.handler`.
   - Fix its mypy error at line 39.
   - First confirm whether the live `dev-wwii-metrics` function is failing today.
4. Deploy: this changes a CloudFormation handler and the ECS image, so it needs `deploy_all.sh`.

### PR 3: Enforce the rule

Add an AST check, either in `scripts/check_unreachable_modules.py` or a small new script. It fails if any
module in `src/`, `lambda_handlers/`, `ecs_modules/`, `phase*.py` or `ecs_entrypoint.py` imports `scripts`
(`import scripts...`, `from scripts...`, or `import_module("scripts...")`). Run it from `gate.sh` and
`.github/workflows/lint.yml`. Allowlist: empty. It must land after PR 1 and PR 2, or it fails on day one.

### PR 4: Retire the duplicate implementations (salvage first, then archive)

Order matters: port the useful logic, verify, then archive, so nothing of value is lost.

#### D1. Order of Battle: `src/ingestion/oob_markdown/` is canonical

The Order of Battle is limited-use but highly important: one fixed book, and the authoritative unit spine.
So port a small set of tested functions, not a framework. Keep oob_markdown's posture: it flags problems
and never rewrites. Put any corrected or canonical value in a separate field beside the verbatim text.

The PDF family's layout parsing is PDF-specific and can go. Its domain rules apply to any input format and
are worth keeping. Salvage list, in priority order. Source is `scripts/extract_eto_order_of_battle.py`
unless noted.

1. **Attachment parent hierarchy (highest value).**
   - Source: `resolve_attachment_parent` and helpers (lines 815-1033). They track the current combat-command
     header, the current Group header, and the Group HQ row that ends a group. Children are matched by unit
     family, and standalone tank battalions and task forces escape the combat command.
   - Drop the x-coordinate check (PDF layout).
   - Port to a new `oob_markdown/attachment_parents.py`. Add a `parent_unit` field to `AttachmentRow`
     (`models.py`) and call the port from the row loop in `attachments._parse_table`.
   - Why it matters: today the CC and Group to battalion relationships exist only in the old CSVs'
     `parent_unit_name`.
2. **Non-unit row filter.**
   - Source: `is_chronology_junk_unit_name` (3293-3341) and `is_division_page_header_unit_name` (2268-2300).
     They catch chronology notes, staff titles, bare dates and division running headers.
   - Port to `_common.py` as `is_non_unit_text()`. Flag and count rows via `observe_rows_dropped` rather
     than dropping them silently.
   - Why it matters: `attachments._parse_data_row` currently accepts any non-empty first cell.
3. **Canonical division list and known source gaps.**
   - Source: `CANONICAL_DIVISIONS` (about lines 21-80), plus the format-independent part of
     `config/eto_oob_pdf_page_division_overrides.yaml`:
     - the 28th Infantry Division is absent from the scan (printed pp. 109-119);
     - the 71st is missing printed pp. 225-226;
     - the audit script also excludes the 1st Armored as Mediterranean-only.
   - Port to a new `config/oob_known_source_gaps.yaml`, read by `reconcile.py`, so these show as expected
     gaps rather than errors. Add a division × section presence check.
4. **Unit-name canonicalisation, as a derived field.**
   - Port the generic rules only: abbreviation expansion (the `unit_name_*` sets in
     `config/csv_search_replacements.yaml`), the ordinal convention (42nd → 42d), CC A → CCA, and the
     Armd/(Mech) variants.
   - Not the roughly 100 one-off garble fixes tied to the old PyMuPDF text layer.
   - Port to a new `oob_markdown/unit_names.py`, writing a `unit_canonical` field.
5. **Position aliases.** Keep the canonical targets of `POSITION_ALIASES` and `normalize_position` (lines
   158-200 and 1301-1323) as `canonical_position()` in `command_staff.py`. Drop the OCR-variant keys.
6. **Date repair, as suggestions only.**
   - Source: `fix_ocr_day`, `fix_ocr_year`, `fix_duplicated_day_as_year_pair` and `expand_partial_date` in
     `scripts/clean_eto_oob_dates.py`.
   - Port to `_common.py` as `repair_date_candidate()`, which emits a suggested value and a note and keeps
     the original.
   - **Do not** port date propagation from neighbouring rows (`propagate_dates_*`, `fill_*`). It guesses
     dates, which breaks the provenance rule.
7. **Old-OCR misread tables:** only if Chandra output shows the same errors.

**Not ported (PDF-specific):**
- spatial band clustering and x-thresholds
- fitz page iteration
- page-number-keyed overrides
- `extract_eto_oob_division_pages.py`
- the migrate machinery. Keep its list of 101st Airborne detachment pages as a regression check that
  Chandra classifies those units under DETACHMENTS.

**Before archiving:**
- Do a one-time diff per division of the old `contentrepository/.../eto_oob_*.csv` against
  `output/oob/<section>/*.json`, and explain or fix every difference.
- Keep the old CSVs (in place or in the archive). They hold the only `parent_unit_name` values until item 1
  lands.
- The PDF family has no tests, so each ported rule needs new ones. The old regexes and the YAML description
  fields make good fixtures.
- A string search found no reader of the family's CSVs or of `csv_search_replacements.yaml` outside the
  family itself.
- Then `git mv` the 9 PDF-family scripts to `scripts/archive/` and correct `scripts/README.md`, which still
  lists them as active.

#### D2. `scripts/pdf_to_markdown.py`: keep the book scaffolding, rename it, replace only the conversion

The name is misleading. Its valuable job is **staging a new book**, not converting PDFs:
- create `<book>/chapter1/` and `<book>/sourcedocument/`
- write a safe `chapter1-meta.yaml` template with `yaml.safe_dump`, using empty fields rather than fake
  values
- write a needs-review metadata marker so the missing metadata is detectable
- move the PDF into `sourcedocument/`

Only its conversion step is wrong. It runs `pymupdf4llm` on every PDF, which produces garbled text for
scanned books. Neither phase calls the script. PDFs are handled in Phase 0:
- In the cloud, the trigger sends every PDF to Chandra OCR.
- Locally, `phase0_ingest._convert_source_pdfs` calls `src/ingestion/pdf_pipeline.convert_pdf_to_markdown`,
  which converts digital PDFs in-process and hands scanned ones to Chandra.

Plan:
- Rename the script to describe what it does, e.g. `scripts/stage_new_book.py`.
- Keep the scaffolding, the safe metadata template and the needs-review marker.
- Replace the `pymupdf4llm` call with `pdf_pipeline.convert_pdf_to_markdown`, so a scanned PDF reports
  `needs_ocr` instead of writing garbled markdown.
- Keep a short-lived `pdf_to_markdown.py` wrapper that prints the new name, so documented commands don't
  break at once.

**Then update:** `pipeline/PDF_CONVERSION.md`, `LOCAL_DEPLOYMENT.md`, `CONTENT_CONTRIBUTOR_GUIDE.md`,
`core/CODE_ARCHITECTURE.md` and `scripts/README.md`.

#### D3. Generic HTML: `text_converters.html_to_markdown`, with `web_video.capture_web_page_with_video` for pages with video

ibiblio keeps its site-specific converter, `scripts/import_hyperwar_html.py`.

Neither chosen function fetches a URL:
- `html_to_markdown` takes a local file.
- `capture_web_page_with_video` takes page text that is already extracted, and has no production caller yet
  (tests only).

So the `URLExtractor` / `extract_url` path (`src/url_extractor.py`, using `html2text`) is retired, with
salvage:
- **SSRF-safe fetch:** this is already the shared helper `url_hygiene.safe_get`, so call it. Nothing to port.
- **Content selector and chapter splitting:** `extract_main_content` (CSS selector), plus
  `split_into_chapters` and `save_chapters` (chapter structure from headings).
  - Port these into a small fetch-and-stage step: fetch with `safe_get`, apply the selector, save the `.html`
    under `contentrepository/<title>/`, and let Phase 0 convert it with `text_converters`.
  - Check whether Phase 0's convert step already splits chapters before porting `split_into_chapters`.

**Then:** remove `URLExtractor.html_to_markdown`, update or retire `scripts/extract_url.py`, and update
`ADDING_DATA_SOURCES.md` and `CODE_ARCHITECTURE.md`. Check that nothing else uses `html2text` before dropping
the dependency.

---

## 4. Gate impact of moving the 11 modules (measured on the current files)

| Check | Result | Action |
|---|---|---|
| radon (C+ hard-fails unless allowlisted) | 20 C blocks: `find_duplicate_people` 10, `find_duplicate_groups` 3, `find_duplicate_places_v2` 2, `find_duplicate_equipment` 2, `cleanup_indexes` 1, `validate_phase3` 2. No D or worse. | D4 |
| mypy | 3 errors: `find_related_groups.py:30, 197`; `view_metrics.py:39` | Fix in the move PRs |
| vulture | 1: unused import `entity_version` in `validate_phase2.py:33` | Remove |
| raw-write gate | 7 new sites. 6 are non-record sinks (5 duplicate/related reports, 1 `index.json`). 1 is an entity record (`reclassify_military_units:100`). | Baseline 64 to 70 for the 6; route the entity write through the guard |
| pylint | 9.78/10 (gate floor 5.0) | none |
| bandit `-ll`, ruff `B` | clean | none |

## 5. Decisions (made 2026-11-06)

- **D1.** `src/ingestion/oob_markdown/` is the canonical Order of Battle parser. It is the newer code (last
  changed 2026-11-03), whereas the PDF family has had no logic changes since 2026-09-18. Salvage per PR 4
  before archiving the PDF family.
- **D2.** Keep `scripts/pdf_to_markdown.py`'s new-book scaffolding and rename the script to say what it
  does. Replace only its conversion step with `pdf_pipeline.convert_pdf_to_markdown` (PR 4).
- **D3.** Generic HTML uses `text_converters.html_to_markdown`, with `web_video.capture_web_page_with_video`
  for pages with video. ibiblio keeps `import_hyperwar_html.py`. `URLExtractor` is retired after its
  selector and chapter-splitting logic is salvaged.
- **D4.** Hybrid approach:
  - add the golden-output test before moving anything;
  - grandfather the 20 complexity-C functions at their new paths with an honest reason;
  - refactor file by file afterwards, starting with `find_duplicate_people.py` (10 of the 20), removing each
    allowlist entry as it drops below the threshold.

  Since the golden test catches any change in dedup output, useful logic is preserved through the refactor.

## 6. Verification

- **Behaviour unchanged:** before PR 1, record `duplicate_report.json` and `related_groups_report.json` from
  the 4 detectors and `find_related_groups` on a fixed fixture corpus, with external calls disabled. After
  the move, the outputs must be identical. Add this as a test.
- Gate, GitHub CI, and independent code and security reviews on every PR.
- After deploying PR 1 and PR 2, do one end-to-end book run. Check that the logs show each dedup detector
  ran (no silent skip), that the reports reached S3, that reclassified groups pass the write guard, and that
  the `dev-wwii-metrics` Lambda invokes.

## 7. Out of scope

- Refactoring the grandfathered complexity hotspots (track separately).
- Removing `COPY scripts/ scripts/` from the main `Dockerfile`. After PR 1 the container no longer needs it
  at runtime, but operators may still use the tools in-container.
- Extending gate lint coverage to the rest of `scripts/`.
