#!/usr/bin/env python3
"""Take a web photo back off an entity record (it is not the subject, after all).

    python3 scripts/retract_photo.py --file output/equipment/M4_Sherman.json \\
        --url https://img.example.org/sherman.jpg --reason "an M3 Lee, not a Sherman" \
        [--dry-run] [--record-only]

The image entry is removed from the record (written first), then the decision is recorded for the
photo's URL and its bytes (``web_photos.record_retraction``): neither it nor a copy of it on
another site is taken again for this entity. The stored photo bytes stay (they are
content-addressed and may back another record's photo). Runs against the local corpus
(``output/``); in AWS, run with operator credentials on a synced copy and let the next sync upload
the record.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, Optional

# run directly (python3 scripts/<name>.py): the repo root must be importable for src.*
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.enrichment import web_photos  # noqa: E402

# entity directory -> the entity type its verdicts are keyed on (find_queue's own types, so the
# key is the one the finder used)
ENTITIES: Dict[str, str] = {
    "equipment": "equipment",
    "people": "person",
    "places": "place",
    "people_groups": "people_group",
}
_EQUIPMENT_ID = (
    "EquipmentID",
)  # equipment queues no finds, so find_queue has no entry for it


def _subject(path: Path, data: Dict) -> Optional[web_photos.Subject]:
    from src.utils.find_queue import _ENTITY_ID_FIELDS

    kind = ENTITIES.get(path.parent.name)
    if kind is None:
        return None
    fields = _ENTITY_ID_FIELDS.get(kind, _EQUIPMENT_ID)
    ulid = next((str(data[f]) for f in fields if data.get(f)), "")
    if not ulid:
        return None
    key = f"{kind}#{ulid}"
    return web_photos.Subject(
        entity_key=key, text_context="", subject="", what_counts=""
    )


def _load(path: Path) -> Optional[Dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"{path}: not readable as JSON ({e})")
        return None
    return data if isinstance(data, dict) else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--file", required=True, type=Path, help="the entity record (JSON)")
    ap.add_argument(
        "--url", required=True, help="the photo's image URL, as on the record"
    )
    ap.add_argument("--reason", required=True, help="why it is not the subject")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--record-only",
        action="store_true",
        help="only record the retraction (the photo is already off the record)",
    )
    args = ap.parse_args(argv)

    data = _load(args.file)
    subject = _subject(args.file, data) if data is not None else None
    if data is None or subject is None:
        print(f"not an entity record with an ID (output/<{'|'.join(ENTITIES)}>/...)")
        return 2
    if args.record_only:
        ok = web_photos.record_retraction([], args.url, subject, args.reason)
        print(f"{'recorded' if ok else 'NOT recorded (see the log)'}: {args.url}")
        return 0 if ok else 1
    images = data.get("images") or []
    if not any(isinstance(i, dict) and i.get("url") == args.url for i in images):
        print(f"{args.file}: no photo with that URL")
        return 1
    if args.dry_run:
        print(f"would retract {args.url} from {subject.entity_key}")
        return 0
    gone = web_photos.take_off(images, args.url)
    data["images"] = images
    from src.utils.file_lock import write_json_with_lock

    # the record first: a refused write must not leave the photo on it but ruled out
    if not write_json_with_lock(args.file, data, entity=args.file.parent.name):
        print(
            f"{args.file}: the write guard refused the record (see the log); nothing changed"
        )
        return 1
    if not web_photos.record_retraction(gone, args.url, subject, args.reason):
        print(
            f"retracted {args.url} from {subject.entity_key}, but the decision was NOT recorded "
            "(see the log): once the api-cache table is reachable, run again with --record-only"
        )
        return 1
    print(f"retracted {args.url} from {subject.entity_key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
