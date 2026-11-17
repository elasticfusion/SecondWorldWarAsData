#!/usr/bin/env python3
"""Remove what one chapter's Phase 2 event extraction produced, so the chapter can be extracted
again cleanly (owner, 2026-11-10: Cross-Channel Attack ch. VIII was extracted from sub-events that
held 16% of it; a re-run only ADDS mentions, so the old ones would stay beside the new).

The chapter's EventID is deterministic (book + chapter), so it survives a re-extraction; its
Sub-eventIDs do not. Everything keyed on the EventID is therefore this chapter's:

* entity mentions - the items of a record's ``event_mentions`` / ``mentions`` lists whose EventID is
  the chapter's, in people, people_groups, places, dates, equipment, logistics, weather and
  bibliography. A record left with no mention at all (it came from this chapter alone) is deleted
  and dropped from its type's ``index.json``; a record still mentioned elsewhere is kept, with its
  enrichment. Deleted records that were enriched are listed in the dry run.
* casualties - one record per event (``event_context.EventID``): deleted.
* the event file, copied to ``diagnostics/superseded/<book>/`` then deleted (bucket versioning
  keeps every version too), and the chapter's ``source_section`` (re-extraction emits a new one,
  with a new id).
* the chapter's entry in each extractor's ``.processed_events.json`` - else it skips the chapter. A
  marker keyed by bare file name ("chapter8full-event.json") names that chapter of EVERY book, so
  removing it re-runs the same chapter of any other book too (reported).

NOT touched: the parsed chapter (Phase 1), images (keyed on the unchanged EventID), the notes event
file (rewritten in place by the re-run), other chapters, the human dedup exclusions. After the
re-run, Phase 2's referential-integrity pass detaches links to sub-events and records that no
longer exist (image -> SourceSectionID links among them).

Dry run by default (prints what it would do); ``--apply`` writes, each change conditional on the
object being unchanged since the plan (ETag), copies before deletes. Refuses while a pipeline task
runs. An apply interrupted after the event file was removed cannot be re-planned: restore the
event file from ``diagnostics/superseded/`` first.

    python scripts/purge_chapter_event.py --book USA-E-XChannel --chapter chapter8full [--apply]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

MENTION_TYPES = (
    "people",
    "people_groups",
    "places",
    "dates",
    "equipment",
    "logistics",
    "weather",
    "bibliography",
)
MARKER_TYPES = (
    "equipment",
    "weather",
    "logistics",
    "casualties",
    "supplemental",
    "people",
    "people_groups",
)
# The lists that hold a record's mentions. Every item counts toward "still mentioned", with or
# without an EventID (a bibliography mention carries one only when its material resolved).
MENTION_KEYS = ("event_mentions", "mentions")
# Fields that mean a record was enriched (Phase 3): reported before such a record is deleted.
ENRICHMENT_KEYS = (
    "biographical_profile",
    "web_results",
    "enrichment_status",
    "openserp_searched",
    "wikipedia_url",
    "grokipedia_urls",
)
_SKIP_NAMES = (
    "index.json",
    "duplicate_report",
    "related_groups",
    "surname_frequency",
    "review_queue",
    "not_duplicates.json",
    "not_related.json",
)


# --- pure functions (tested) -----------------------------------------------------------------


def strip_event(record: Any, event_id: str) -> Tuple[Any, int, bool]:
    """``record`` without the mentions of ``event_id``: (record, mentions removed, whether the
    record now has no mention at all)."""
    removed = 0
    remaining = 0

    def walk(node: Any) -> Any:
        nonlocal removed, remaining
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if k in MENTION_KEYS and isinstance(v, list):
                    kept = [
                        x
                        for x in v
                        if not (isinstance(x, dict) and x.get("EventID") == event_id)
                    ]
                    removed += len(v) - len(kept)
                    remaining += len(kept)
                    out[k] = [walk(x) for x in kept]
                else:
                    out[k] = walk(v)
            return out
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    out = walk(record)
    return out, removed, removed > 0 and remaining == 0


def is_enriched(record: Any) -> bool:
    return isinstance(record, dict) and any(record.get(k) for k in ENRICHMENT_KEYS)


def is_event_record(record: Any, event_id: str) -> bool:
    """A per-event record (a casualty) of ``event_id``."""
    if not isinstance(record, dict):
        return False
    for key in ("event_context", "source"):
        ctx = record.get(key)
        if isinstance(ctx, dict) and ctx.get("EventID") == event_id:
            return True
    return False


def prune_index(index: Any, deleted: set) -> Tuple[Any, int]:
    """The type's index without entries naming a deleted record file (flat ``{key: filename}`` or
    nested ``{section: {key: filename}}``, as bibliography's)."""
    if not isinstance(index, dict):
        return index, 0
    dropped = 0
    out: Dict[str, Any] = {}
    for k, v in index.items():
        if isinstance(v, dict):
            sub, n = prune_index(v, deleted)
            out[k] = sub
            dropped += n
        elif isinstance(v, str) and v in deleted:
            dropped += 1
        else:
            out[k] = v
    return out, dropped


def prune_marker(marker: Any, event_file: str, book: str) -> Tuple[Any, int, bool]:
    """The processed-events marker without the chapter: (marker, entries removed, whether one was a
    bare file name). A path key matches only this book's chapter (``.../<book>/<event_file>``).
    """

    def hit(k: Any) -> bool:
        return str(k) == event_file or str(k).endswith(f"/{book}/{event_file}")

    bare = event_file in (marker if isinstance(marker, (list, dict)) else ())
    if isinstance(marker, list):
        kept = [k for k in marker if not hit(k)]
        return kept, len(marker) - len(kept), bare
    if isinstance(marker, dict):
        kept_d = {k: v for k, v in marker.items() if not hit(k)}
        return kept_d, len(marker) - len(kept_d), bare
    return marker, 0, False


@dataclass
class Plan:
    """Every S3 change, decided before any is made, with each touched object's ETag at plan time
    (the apply refuses to overwrite or delete an object changed since)."""

    rewrite: Dict[str, Any] = field(default_factory=dict)  # key -> new JSON
    delete: List[str] = field(default_factory=list)
    move: List[Tuple[str, str]] = field(default_factory=list)  # (from, to)
    etag: Dict[str, str] = field(default_factory=dict)
    mentions: Dict[str, int] = field(default_factory=dict)  # type -> mentions removed
    records_deleted: Dict[str, int] = field(default_factory=dict)
    enriched_deleted: List[str] = field(default_factory=list)
    enriched_kept: List[str] = field(default_factory=list)
    shared_markers: List[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"mentions removed {self.mentions}; records deleted {self.records_deleted}; "
            f"{len(self.rewrite)} files rewritten, {len(self.delete)} deleted, "
            f"{len(self.move)} moved; enriched records deleted: {len(self.enriched_deleted)}"
        )


# --- S3 ------------------------------------------------------------------------------------


def _keys(s3, bucket: str, prefix: str) -> List[str]:
    out = []
    for page in s3.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=prefix
    ):
        out += [o["Key"] for o in page.get("Contents", [])]
    return out


def _get(s3, bucket: str, key: str, plan: Plan) -> bytes:
    """The object's bytes; its ETag is recorded in ``plan``."""
    obj = s3.get_object(Bucket=bucket, Key=key)
    plan.etag[key] = obj.get("ETag", "")
    return obj["Body"].read()


def _record_keys(s3, bucket: str, etype: str) -> List[str]:
    return [
        k
        for k in _keys(s3, bucket, f"output/{etype}/")
        if k.endswith(".json")
        and "/." not in k
        and "/files/" not in k
        and not any(k.rsplit("/", 1)[-1].startswith(s) for s in _SKIP_NAMES)
    ]


def plan_purge(
    s3, bucket: str, book: str, chapter: str, keep_enriched: bool = False
) -> Tuple[str, Plan]:
    """(EventID, plan) for the chapter ``chapter`` ("chapter8full") of ``book``. With
    ``keep_enriched`` a record that came from this chapter alone but was enriched is kept, with no
    mentions, for the re-extraction to merge into (its enrichment is not paid for again).
    """
    plan = Plan()
    event_key = f"output/content/{book}/{chapter}-event.json"
    event_id = json.loads(_get(s3, bucket, event_key, plan))["Event"]["EventID"]
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    plan.move.append(
        (event_key, f"diagnostics/superseded/{book}/{chapter}-event.{stamp}.json")
    )
    for etype in MENTION_TYPES:
        _plan_mentions(s3, bucket, etype, event_id, plan, keep_enriched)
    for key in _record_keys(s3, bucket, "casualties"):
        if is_event_record(json.loads(_get(s3, bucket, key, plan)), event_id):
            plan.delete.append(key)
            plan.records_deleted["casualties"] = (
                plan.records_deleted.get("casualties", 0) + 1
            )
    for key in _keys(s3, bucket, f"output/content/{book}/source_section/"):
        if key.endswith(".json"):
            if json.loads(_get(s3, bucket, key, plan)).get("EventID") == event_id:
                plan.delete.append(key)
    _plan_markers(s3, bucket, book, f"{chapter}-event.json", plan)
    return event_id, plan


def _plan_mentions(
    s3, bucket: str, etype: str, event_id: str, plan: Plan, keep_enriched: bool
) -> None:
    deleted = set()
    for key in _record_keys(s3, bucket, etype):
        raw = _get(s3, bucket, key, plan)
        if event_id.encode() not in raw:
            continue
        before = json.loads(raw)
        record, removed, empty = strip_event(before, event_id)
        if not removed:
            continue
        plan.mentions[etype] = plan.mentions.get(etype, 0) + removed
        if empty and keep_enriched and is_enriched(before):
            # kept with no mention: the re-extraction merges into it by name, enrichment kept
            plan.rewrite[key] = record
            plan.enriched_kept.append(key)
        elif empty:
            plan.delete.append(key)
            deleted.add(key.rsplit("/", 1)[-1])
            plan.records_deleted[etype] = plan.records_deleted.get(etype, 0) + 1
            if is_enriched(before):
                plan.enriched_deleted.append(key)
        else:
            plan.rewrite[key] = record
    if deleted:
        index_key = f"output/{etype}/index.json"
        try:
            index, n = prune_index(
                json.loads(_get(s3, bucket, index_key, plan)), deleted
            )
        except Exception:  # noqa: BLE001 - no index for this type
            return
        if n:
            plan.rewrite[index_key] = index


def _plan_markers(s3, bucket: str, book: str, event_file: str, plan: Plan) -> None:
    for etype in MARKER_TYPES:
        key = f"output/{etype}/.processed_events.json"
        try:
            marker = json.loads(_get(s3, bucket, key, plan))
        except Exception:  # noqa: BLE001 - absent: nothing to clear
            continue
        kept, n, bare = prune_marker(marker, event_file, book)
        if n:
            plan.rewrite[key] = kept
            if bare:
                plan.shared_markers.append(key)


def apply(s3, bucket: str, plan: Plan) -> None:
    """Make the planned changes: copies first (verified), then rewrites, then deletes - each
    rewrite/delete only if the object is unchanged since the plan (IfMatch on its ETag).
    """
    for src, dst in plan.move:
        s3.copy_object(
            Bucket=bucket, Key=dst, CopySource={"Bucket": bucket, "Key": src}
        )
        s3.head_object(Bucket=bucket, Key=dst)  # the copy exists before the source goes
    for key, body in plan.rewrite.items():
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(body, indent=2, ensure_ascii=False).encode("utf-8"),
            ContentType="application/json",
            IfMatch=plan.etag[key],
        )
    for key in plan.delete + [src for src, _ in plan.move]:
        s3.delete_object(Bucket=bucket, Key=key, IfMatch=plan.etag[key])


def _running_tasks(region: str, cluster: str) -> int:
    import boto3

    ecs = boto3.client("ecs", region_name=region)
    try:
        return len(ecs.list_tasks(cluster=cluster)["taskArns"])
    except ecs.exceptions.ClusterNotFoundException:
        raise SystemExit(f"cluster {cluster} not found: check --env/--region")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--book", required=True)
    ap.add_argument("--chapter", required=True, help='e.g. "chapter8full"')
    ap.add_argument("--env", default="dev")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument(
        "--keep-enriched",
        action="store_true",
        help="keep (mention-less) enriched records the chapter alone produced",
    )
    args = ap.parse_args(argv)
    import boto3

    bucket = f"{args.env}-wwii-data-pipeline"
    if _running_tasks(args.region, f"{args.env}-wwii-pipeline"):
        print("refused: a pipeline task is running (it would race these edits)")
        return 2
    s3 = boto3.client("s3", region_name=args.region)
    event_id, plan = plan_purge(
        s3, bucket, args.book, args.chapter, keep_enriched=args.keep_enriched
    )
    print(f"{args.book} {args.chapter} EventID={event_id}: {plan.summary()}")
    for key in sorted(plan.enriched_deleted):
        print(f"  ENRICHED record deleted (recoverable from its prior version): {key}")
    for key in sorted(plan.enriched_kept):
        print(f"  ENRICHED record kept without mentions (--keep-enriched): {key}")
    for key in sorted(plan.shared_markers):
        print(
            f"  NOTE {key} names the chapter by bare file name: that chapter of every "
            "other book is re-run too"
        )
    for key in sorted(plan.delete):
        print(f"  delete  {key}")
    for key in sorted(plan.rewrite):
        print(f"  rewrite {key}")
    for src, dst in plan.move:
        print(f"  move    {src} -> {dst}")
    if not args.apply:
        print("dry run: nothing changed (--apply to make these changes)")
        return 0
    apply(s3, bucket, plan)
    print("applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
