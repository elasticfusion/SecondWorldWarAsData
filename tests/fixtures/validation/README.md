# Validation fixtures

Committed, schema-checked sample records — one small set **per entity
classification** — used to validate the schemas on every schema-touching change.

The real `output/**` dataset is **git-ignored** (it lives in S3; dev data is
disposable). Without a committed sample, the Data Validation workflow had nothing
to check and silently no-op'd. These fixtures give CI (and the local gate) real
shapes to validate against, so a schema edit that breaks real-shaped data fails
fast — before it reaches production data.

## Layout

```
tests/fixtures/validation/<schema>/sample.json
```

`<schema>` is a registry schema name (`people`, `people_groups`, `equipment`,
`casualties`, …). Each category dir holds **two single-record files** —
`minimal.json` (required fields only) and `expanded.json` (every optional field
populated) — each a standalone entity record validated directly against the
authoritative registry schema (one record per file, NO wrapper; the old
`{"people": [...]}` wrapper + `sample.json` list were pre-refactor shapes and
have been retired now that the registry serves the write-guard's per-entity
schemas). Both the unit test and the CI `validate` step (which globs `*.json`
and validates each file as one record) check them.

## The two-record contract (per category)

Each `sample.json` holds exactly **two** records:

1. **Minimal** — only the schema's REQUIRED fields. Proves nothing optional is
   secretly required (and that a sparse real record, e.g. a name+rank-only OOB
   person, is valid).
2. **Fully (or nearly) expanded** — every optional field populated, including the
   newest ones. Proves the schema accepts the full current shape.

The expanded record must be strictly richer than the minimal one
(`tests/test_validation_fixtures.py` enforces both the schema validity and the
two-record / richer-expanded contract).

## Maintaining fixtures

- Internal metadata keys (`_schema_version`, `_last_updated`) are stripped — the
  validator ignores `^_` keys, and fixtures should be version-agnostic.
- When a schema change is **intended**, update the affected `sample.json`
  (especially the expanded record) in the same PR so it exercises the new fields.
- Source records from real `output/**` data where possible; hand-expand only the
  fully-expanded record to cover brand-new optional fields.
