# Guard Phase-1 parse output (HIGH-4b)

**Status:** BUILT (proposed 2026-10-10; verified against the code 2026-11-16). Close the earliest preventive gap: Phase 1 writes
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
Every `-parsed.json` writer uses it (no path left un-guarded). `_PARSED_MIN_PARAGRAPHS` is 1:
- `phase1_parse.py` local writers (the main chapter file and each split-chunk file of a chapter over
  400k chars) via `write_parsed_with_guard`
  (validate → atomic tmp+replace); a blocked chapter logs `DROPPED ... re-parse needed` (not
  "Saved").
- `lambda_handlers/phase1_handler.py::_write_parsed_doc` (the S3/ECS production path) calls
  `guard_parsed_content` directly before `storage.write_json`, skipping the upload and logging
  `DROPPED schema-invalid parse ... re-parse needed` on block.
`guard_parsed_content` returns True (writes) when the validator itself errors, and logs a warning so
the swallow is visible.
The notes / endnote-ref artifacts are separate (not `-parsed.json`) and out of scope here.

## Testing
- Valid parsed dict passes + writes.
- Missing `book`/`chapter_title`/`paragraphs`, or a paragraph missing `text`/`absolute_number`,
  BLOCKS (not written) + records `block`.
- A parse with zero usable paragraphs WRITES but records `allow_empty`.
- Fail-safe: a schema/import error still writes.
- Phase-1 integration: the two writers route through the guard.
