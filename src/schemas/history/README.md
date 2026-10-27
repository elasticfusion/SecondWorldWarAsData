# Schema version history

Point-in-time archive of each enforced entity schema's exact JSON **shape**, one file per
version:

```
src/schemas/history/<entity>/<version>.json
```

Every schema SHAPE change (see `docs/current/SCHEMA_VERSIONING.md`) must, in addition to
bumping the entity version + re-pinning the fingerprint + adding a Change Log row, **snapshot
the new schema here**. This gives a durable, diffable record of how a schema evolved over time
(backward-compatibility review, migration authoring, and auditing what a record stamped with an
old `_schema_version` was validated against).

Snapshot the current shape of one entity:

```bash
python -c "import json; from src.schemas.entity_registry import ENTITY_REGISTRY, load_schema; \
s=next(x for x in ENTITY_REGISTRY if x.name=='people'); sch=load_schema(s); \
open(f'src/schemas/history/people/{sch[\"version\"]}.json','w').write(json.dumps(sch,indent=2,sort_keys=True,default=str))"
```

The archived file's fingerprint (sha256 of the schema with the `version` field removed, first 16
hex) MUST equal that version's pin in `tests/test_schema_fingerprint.py` — i.e. the archive is
the human-diffable twin of the machine pin.
