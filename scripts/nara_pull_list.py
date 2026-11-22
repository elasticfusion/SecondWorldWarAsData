#!/usr/bin/env python3
"""The NARA pull list: records to retrieve in person at NARA, and the scans brought back.

    # the list for a visit (CSV + a printable Markdown list), with each item's status
    AWS_PROFILE=wwii python3 scripts/nara_pull_list.py build --env dev --out tmp/nara_visit

    # a person's status change: needed / requested / pulled / scanned / ingested / unobtainable
    AWS_PROFILE=wwii python3 scripts/nara_pull_list.py status nara-305271 pulled --by "D. Christian" \\
        --notes "Box 1234, folder 7"

    # a scan brought back, bound to its BibliographyID (dry run first)
    AWS_PROFILE=wwii python3 scripts/nara_pull_list.py add-scan --env dev --bib 01HX... \\
        --file ~/scans/rg407-box1234.pdf --by "D. Christian" --scanned-on 2026-12-03 \\
        [--item nara-305271] [--part 1] [--us-government-record] [--notes "..."] [--dry-run]

The scan is uploaded as a new source to ``contentrepository/nara-scans/<slug>.<ext>`` (normal intake:
antivirus, OCR, Phases 1-3); its provenance is the ``pullscan#<slug>`` row, written first. Run with
operator credentials: the pipeline roles cannot write ``pull#`` / ``pullscan#`` rows.
See src/enrichment/nara_pull_list.py.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

# run directly (python3 scripts/<name>.py): the repo root must be importable for src.*
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.enrichment import nara_pull_list as npl  # noqa: E402

logger = logging.getLogger("nara_pull_list")
BIB_PREFIX = "output/bibliography/"


def _aws(env: str, region: str):
    import boto3

    table = boto3.resource("dynamodb", region_name=region).Table(
        f"{env}-wwii-api-cache"
    )
    return table, boto3.client("s3", region_name=region), f"{env}-wwii-data-pipeline"


def _load_records(
    s3: Any, bucket: str, bib_dir: Optional[Path]
) -> List[Dict[str, Any]]:
    """Every bibliography record, from a local copy or S3."""
    if bib_dir:
        files = sorted(bib_dir.glob("bib_*.json"))
        return [r for r in (_read(f.read_text(encoding="utf-8")) for f in files) if r]
    keys: List[str] = []
    for page in s3.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=BIB_PREFIX
    ):
        keys += [
            o["Key"]
            for o in page.get("Contents", [])
            if o["Key"].rsplit("/", 1)[-1].startswith("bib_")
        ]

    def get(key: str) -> Optional[Dict[str, Any]]:
        return _read(
            s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
        )

    with ThreadPoolExecutor(16) as ex:
        return [r for r in ex.map(get, keys) if r]


def _read(text: str) -> Optional[Dict[str, Any]]:
    try:
        rec = json.loads(text)
    except ValueError:
        return None
    return rec if isinstance(rec, dict) else None


def _nara_events(table: Any) -> List[Dict[str, Any]]:
    from src.enrichment import fetch_review
    from src.utils import fetch_provenance as fp

    events, errors = fetch_review._load_events_dynamo(table, fp)  # noqa: SLF001
    if errors:
        raise SystemExit(
            f"could not read every review-queue event ({errors} errors): not building"
        )
    return [e for e in events if e.get("provider") == "nara-find"]


def _items(args, table, s3, bucket) -> List[npl.PullItem]:
    records = _load_records(s3, bucket, args.bib_dir)
    items = npl.build_items(records, _nara_events(table))
    return npl.apply_statuses(items, table)


def cmd_build(args) -> int:
    table, s3, bucket = _aws(args.env, args.region)
    items = _items(args, table, s3, bucket)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    npl.export_csv(items, out / "nara_pull_list.csv")
    npl.export_markdown(items, out / "nara_pull_list.md")
    exact = sum(1 for i in items if i.kind == npl.EXACT)
    print(
        f"{len(items)} items ({exact} identified in the catalog, {len(items) - exact} to locate "
        f"first) -> {out}/nara_pull_list.csv, nara_pull_list.md"
    )
    return 0


def cmd_status(args) -> int:
    table, _s3, _b = _aws(args.env, args.region)
    npl.set_status(table, args.item, args.status, args.by, args.notes or "")
    print(f"{args.item}: {args.status}")
    return 0


def _resolve_bib(env: str, bib: str) -> str:
    """The record's current id (a merged-away id resolves to the record that absorbed it)."""
    import os

    from src.utils import id_aliases

    os.environ.setdefault("ID_ALIAS_TABLE", f"{env}-wwii-id-aliases")
    current = id_aliases.resolve("bibliography", bib)
    if current is None:
        raise SystemExit(f"could not resolve {bib} (see the id_aliases error above)")
    if current != bib:
        print(f"{bib} was merged into {current}: the scan is bound to {current}")
    return current


def _content_bound_elsewhere(table: Any, sha256: str, slug: str) -> str:
    """The slug intake already bound this exact file to (``source#<checksum>``, the trigger's
    immutable-identity row, checksum as ``title_uniqueness.content_checksum``), else "": the
    same scan under a second slug is rejected at intake."""
    item = (
        table.get_item(Key={"cache_key": f"source#sha256:{sha256}"}).get("Item") or {}
    )
    other = str(item.get("title") or "")
    return other if other and other != slug else ""


def _slug_in_use(s3: Any, bucket: str, table: Any, slug: str) -> bool:
    if table.get_item(
        Key={"cache_key": npl.SCAN_PREFIX + slug}, ConsistentRead=True
    ).get("Item"):
        return True
    for prefix in (
        f"{npl.SCAN_DIR}/{slug}.",
        f"contentrepository/{slug}/",
        f"output/content/{slug}/",
    ):
        if s3.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1).get(
            "KeyCount", 0
        ):
            return True
    return False


def _remove_row(table: Any, slug: str) -> None:
    try:
        table.delete_item(Key={"cache_key": npl.SCAN_PREFIX + slug})
        logger.error(
            "OBSERV nara_pull.scan_rolled_back slug=%s status=error - the upload failed",
            slug,
        )
    except Exception as e:  # noqa: BLE001
        logger.error(
            "OBSERV nara_pull.scan_rollback_failed slug=%s err=%s status=error - delete the "
            "cache-table item %r by hand, else Phase 3 binds a file that was never uploaded",
            slug,
            type(e).__name__,
            npl.SCAN_PREFIX + slug,
        )


def _positive(text: str) -> int:
    n = int(text)
    if n < 1:
        raise argparse.ArgumentTypeError("--part must be 1 or more")
    return n


def _target(args, table, s3, bucket) -> "tuple[str, Optional[npl.PullItem]]":
    """The record (current id) and pull-list item the scan is for. Exits on a mismatch."""
    bib = _resolve_bib(args.env, args.bib)
    if not args.item:
        known = {
            str(r.get("BibliographyID"))
            for r in _load_records(s3, bucket, args.bib_dir)
        }
        if bib not in known:
            raise SystemExit(f"no bibliography record {bib}")
        return bib, None
    item = next(
        (i for i in _items(args, table, s3, bucket) if i.item_id == args.item), None
    )
    if item is None:
        raise SystemExit(f"no pull-list item {args.item!r}")
    if bib not in {c["BibliographyID"] for c in item.citations}:
        raise SystemExit(f"item {args.item} does not serve BibliographyID {bib}")
    return bib, item


def _refuse_duplicates(s3, bucket, table, slug: str, sha256: str) -> None:
    if _slug_in_use(s3, bucket, table, slug):
        raise SystemExit(
            f"slug {slug} is in use (slugs are immutable): use --part N for another file"
        )
    other = _content_bound_elsewhere(table, sha256, slug)
    if other:
        raise SystemExit(
            f"this exact file was already ingested as {other!r}: not added again"
        )


def _upload(args, table, s3, bucket, slug: str, key: str, row: Dict[str, Any]) -> None:
    """Provenance first (the upload starts intake; the row binds it to its record), then the file;
    the row is removed when the upload does not complete (an error or an interrupt)."""
    table.put_item(
        Item={"cache_key": npl.SCAN_PREFIX + slug, "response": json.dumps(row)},
        ConditionExpression="attribute_not_exists(cache_key)",
    )
    logger.info(
        "OBSERV nara_pull.scan_provenance_written slug=%s bib=%s",
        slug,
        row["BibliographyID"],
    )
    uploaded = False
    try:
        s3.upload_file(
            str(Path(args.file).expanduser()),
            bucket,
            key,
            ExtraArgs={
                "Metadata": {
                    "bibliography-id": row["BibliographyID"],
                    "sha256": row["sha256"],
                }
            },
        )
        uploaded = True
    finally:
        if not uploaded:
            _remove_row(table, slug)
    logger.info("OBSERV nara_pull.scan_uploaded slug=%s key=%s", slug, key)


def cmd_add_scan(args) -> int:
    table, s3, bucket = _aws(args.env, args.region)
    bib, item = _target(args, table, s3, bucket)
    info = npl.check_scan_file(Path(args.file).expanduser())
    slug = npl.scan_slug(item, bib, args.part)
    key = f"{npl.SCAN_DIR}/{slug}{info['suffix']}"
    row = npl.binding(
        slug=slug,
        key=key,
        bibliography_id=bib,
        item=item,
        file_info=info,
        by=args.by,
        scanned_on=args.scanned_on,
        us_government_record=args.us_government_record,
        notes=args.notes or "",
    )
    _refuse_duplicates(s3, bucket, table, slug, info["sha256"])
    print(json.dumps(row, indent=2))
    if args.dry_run:
        print("dry run: nothing written")
        return 0
    _upload(args, table, s3, bucket, slug, key, row)
    if item:
        npl.set_status(table, item.item_id, "scanned", args.by, f"added {key}")
    print(
        f"uploaded s3://{bucket}/{key}; the next Phase 3 binds BibliographyID {bib} to it"
    )
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--region", default="us-east-1")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--env", default="dev")
    b.add_argument("--out", default="tmp/nara_visit")
    b.add_argument(
        "--bib-dir", type=Path, help="a local copy of output/bibliography (else S3)"
    )
    s = sub.add_parser("status")
    s.add_argument("item")
    s.add_argument("status", choices=npl.STATUSES)
    s.add_argument("--env", default="dev")
    s.add_argument("--by", required=True)
    s.add_argument("--notes")
    a = sub.add_parser("add-scan")
    a.add_argument("--env", default="dev")
    a.add_argument("--bib", required=True)
    a.add_argument("--file", required=True)
    a.add_argument("--by", required=True)
    a.add_argument("--scanned-on", required=True)
    a.add_argument("--item")
    a.add_argument("--part", type=_positive)
    a.add_argument("--us-government-record", action="store_true")
    a.add_argument("--notes")
    a.add_argument("--bib-dir", type=Path)
    a.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    try:
        return {"build": cmd_build, "status": cmd_status, "add-scan": cmd_add_scan}[
            args.cmd
        ](args)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
