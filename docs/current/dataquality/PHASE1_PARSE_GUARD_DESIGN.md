# Guard Phase-1 parse output (HIGH-4b)

**Status:** proposed (2026-10-10). Close the earliest preventive gap: Phase 1 writes
`chapter*-parsed.json` via raw `json.dump` — no schema, no guard — so a malformed/empty parse flows
straight into Phase 2 (garbage-in at the very start of the pipeline).

## Scope (deliberately narrow)
Only the **content-validity + guard** half of the data-quality model applies to Phase 1. The
identity / dedup / no-duplication tiers DO NOT: parse artifacts have no PK ULIDs and re-parse is
idempotent, so there are no entities to duplicate. So:
- **Validate SHAPE** of the parsed record (the `_doc_to_dict` contract).
- **Flag an EMPTY parse** (zero / near-zero usable paragraphs = structurally valid but useless).
- NO PK repair, NO cross-ref enforcement, NO dedup.

## Parsed-content schema (`src/schemas/parsed_output.py`)
Validates the `_doc_to_dict` shape actually written by `phase1_parse.py`:
- required: `book` (str), `chapter_title` (str), `paragraphs` (array).
- `paragraphs[]`: required `absolute_number` (int) + `text` (str); other fields optional.
- optional: `chapter_number`, `section_id`, `author`, `series`, `license`, `source_file`,
  `images[]`, `maps[]`, `footnotes[]`, `table_hints`.
- `additionalProperties: true` (parse output is looser than entity output; we check the backbone,
  not every field) — the point is to catch a GARBAGE parse, not to over-constrain.

NOT added to `ENTITY_REGISTRY` (that drives entity/PK/referential machinery which must NOT apply to
parse artifacts). It is a standalone content schema consumed only by the Phase-1 writer.

## Central guarded writer (`src/utils/file_lock.py`)
`write_parsed_with_guard(path, data) -> bool`:
1. jsonschema-validate `data` against the parsed schema. On violation → LOG (path + validator
   keyword only, no values) + record `block` to the catcher under entity `"parsed"` + return False
   (do NOT write garbage downstream).
2. Count usable paragraphs (non-empty `text`). Below a small floor (`_PARSED_MIN_PARAGRAPHS`, e.g.
   1) → record `allow_empty` (keyword `below_floor`) under entity `"parsed"` (visible to the
   catcher's systematic-cluster detection, exactly like entity empties) — but still WRITE (flag,
   not block; an empty chapter can be legitimate, e.g. a plate/figure page).
3. Write via the same atomic locked path. Fail-safe: a validator/import error → write anyway (never
   lose a parse to a telemetry bug), matching the guard's fail-open-on-error stance.

Reuses the existing catcher (`record_validation`) so Phase-1 quality rolls into the SAME
phase-end stats + systematic-cluster alert path — a book that systematically parses empty/garbage
surfaces as a code-level alert.

## Wiring
A single `guard_parsed_content(name, data) -> bool` holds the ONE validation+flag definition.
Three writers use it (no path left un-guarded):
- `phase1_parse.py` local writers (main `_save_*` + split-chunk) via `write_parsed_with_guard`
  (validate → atomic tmp+replace); a blocked chapter logs `DROPPED ... re-parse needed` (not
  "Saved").
- `lambda_handlers/phase1_handler.py` (the S3/ECS production path) calls `guard_parsed_content`
  directly before `storage.write_json`, skipping the upload + logging DROPPED on block.
The notes / endnote-ref artifacts are separate (not `-parsed.json`) and out of scope here.

## Testing
- Valid parsed dict passes + writes.
- Missing `book`/`chapter_title`/`paragraphs`, or a paragraph missing `text`/`absolute_number`,
  BLOCKS (not written) + records `block`.
- A parse with zero usable paragraphs WRITES but records `allow_empty`.
- Fail-safe: a schema/import error still writes.
- Phase-1 integration: the two writers route through the guard.
