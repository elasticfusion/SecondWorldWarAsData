#!/usr/bin/env python3
"""Fix shared EventIDs and Sub-eventIDs across event files, and deduplicate MentionIDs.

For each ID that appears in multiple event files, keeps the first
occurrence (alphabetical by path) and regenerates new ULIDs for all others.
Updates entity files' event_mentions to reference the new IDs.
Also removes duplicate MentionID entries within entity files.

Run locally before uploading to S3.
"""

import json
import logging
from collections import defaultdict
from pathlib import Path

import ulid

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

OUTPUT = Path("output")
ENTITY_DIRS = [
    "people",
    "people_groups",
    "places",
    "equipment",
    "dates",
    "weather",
    "logistics",
    "casualties",
    "bibliography",
]
SKIP_FILES = {
    "index.json",
    "duplicate_report.json",
    "review_queue.json",
    "not_duplicates.json",
    "not_related.json",
    ".processed_events.json",
}


def find_shared_ids():
    """Find EventIDs and Sub-eventIDs used in multiple event files."""
    eid_to_files = defaultdict(list)
    seid_to_files = defaultdict(list)

    for ef in (OUTPUT / "content").rglob("*-event.json"):
        if "notes" in ef.name:
            continue
        try:
            data = json.loads(ef.read_text(encoding="utf-8"))
            event = data.get("Event", data)
            eid = event.get("EventID")
            if eid:
                eid_to_files[eid].append(ef)
            for se in event.get("Sub-events", []):
                sid = se.get("Sub-eventID")
                if sid:
                    seid_to_files[sid].append(ef)
        except Exception:
            continue

    shared_eids = {k: v for k, v in eid_to_files.items() if len(v) > 1}
    shared_seids = {k: v for k, v in seid_to_files.items() if len(v) > 1}
    return shared_eids, shared_seids


def build_remap(shared_eids, shared_seids):
    """Build remap tables. First file (alphabetical) keeps original ID."""
    eid_file_remap = {}  # event_file → (old_eid, new_eid)
    eid_remap = {}  # (old_eid, event_name) → new_eid

    for old_eid, files in shared_eids.items():
        sorted_files = sorted(files, key=lambda f: str(f))
        for ef in sorted_files[1:]:
            new_eid = str(ulid.new())
            eid_file_remap[ef] = (old_eid, new_eid)
            try:
                data = json.loads(ef.read_text(encoding="utf-8"))
                event = data.get("Event", data)
                event_name = event.get("Event_Name", "")[:100]
                eid_remap[(old_eid, event_name)] = new_eid
            except Exception:
                pass

    seid_remap = {}  # (old_seid, summary) → new_seid
    seid_file_remap = defaultdict(dict)  # event_file → {old_seid: new_seid}

    for old_seid, files in shared_seids.items():
        sorted_files = sorted(files, key=lambda f: str(f))
        for ef in sorted_files[1:]:
            try:
                data = json.loads(ef.read_text(encoding="utf-8"))
                event = data.get("Event", data)
                for se in event.get("Sub-events", []):
                    if se.get("Sub-eventID") == old_seid:
                        new_seid = str(ulid.new())
                        summary = se.get("Sub-event_summary", "")[:100]
                        seid_remap[(old_seid, summary)] = new_seid
                        seid_file_remap[ef][old_seid] = new_seid
                        break
            except Exception:
                continue

    return eid_file_remap, eid_remap, seid_remap, seid_file_remap


def update_event_files(eid_file_remap, seid_file_remap):
    """Rewrite event files with new EventIDs and Sub-eventIDs."""
    updated = 0
    all_files = set(eid_file_remap.keys()) | set(seid_file_remap.keys())

    for ef in all_files:
        try:
            data = json.loads(ef.read_text(encoding="utf-8"))
            event = data.get("Event", data)
            changed = False

            if ef in eid_file_remap:
                old_eid, new_eid = eid_file_remap[ef]
                if event.get("EventID") == old_eid:
                    event["EventID"] = new_eid
                    changed = True

            if ef in seid_file_remap:
                id_map = seid_file_remap[ef]
                for se in event.get("Sub-events", []):
                    old = se.get("Sub-eventID")
                    if old in id_map:
                        se["Sub-eventID"] = id_map[old]
                        changed = True

            if changed:
                ef.write_text(
                    json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                updated += 1
        except Exception as e:
            logger.warning("Failed to update %s: %s", ef, e)
    return updated


def update_entity_files(eid_remap, seid_remap):
    """Update entity event_mentions to use new IDs and deduplicate."""
    updated = 0

    by_old_eid = defaultdict(list)
    for (old_eid, event_name), new_eid in eid_remap.items():
        by_old_eid[old_eid].append((event_name, new_eid))

    by_old_seid = defaultdict(list)
    for (old_seid, summary), new_seid in seid_remap.items():
        by_old_seid[old_seid].append((summary, new_seid))

    old_eids = set(by_old_eid.keys())
    old_seids = set(by_old_seid.keys())

    for dirname in ENTITY_DIRS:
        d = OUTPUT / dirname
        if not d.exists():
            continue
        for f in d.glob("*.json"):
            if f.name in SKIP_FILES:
                continue
            try:
                content = f.read_text(encoding="utf-8")
                needs_id_fix = any(eid in content for eid in old_eids) or any(
                    sid in content for sid in old_seids
                )
                data = json.loads(content)
                mentions = data.get("event_mentions", [])
                if not mentions and not needs_id_fix:
                    continue

                changed = False

                # Deduplicate MentionIDs
                seen_mids = set()
                deduped = []
                for m in mentions:
                    mid = m.get("MentionID")
                    if mid and mid in seen_mids:
                        changed = True
                        continue
                    if mid:
                        seen_mids.add(mid)
                    deduped.append(m)
                mentions = deduped

                # Update EventIDs and Sub-eventIDs
                if needs_id_fix:
                    for mention in mentions:
                        old_eid = mention.get("EventID")
                        if old_eid in by_old_eid:
                            m_event_name = mention.get("Event_Name", "")[:100]
                            for event_name, new_eid in by_old_eid[old_eid]:
                                if m_event_name == event_name:
                                    mention["EventID"] = new_eid
                                    changed = True
                                    break

                        old_seid = mention.get("Sub_eventID") or mention.get(
                            "Sub-eventID"
                        )
                        if old_seid in by_old_seid:
                            m_summary = (
                                mention.get("Sub_event_Name", "")
                                or mention.get("Sub-event_Name", "")
                            )[:100]
                            for summary, new_seid in by_old_seid[old_seid]:
                                if m_summary == summary:
                                    if "Sub_eventID" in mention:
                                        mention["Sub_eventID"] = new_seid
                                    else:
                                        mention["Sub-eventID"] = new_seid
                                    changed = True
                                    break

                if changed:
                    data["event_mentions"] = mentions
                    f.write_text(
                        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
                    )
                    updated += 1
            except Exception as e:
                logger.debug("Skipping %s: %s", f.name, e)
    return updated


def main():
    logger.info("Scanning for shared EventIDs and Sub-eventIDs...")
    shared_eids, shared_seids = find_shared_ids()
    logger.info(
        "Found %d shared EventIDs, %d shared Sub-eventIDs",
        len(shared_eids),
        len(shared_seids),
    )

    if not shared_eids and not shared_seids:
        logger.info("No shared IDs found — deduplicating MentionIDs only")

    logger.info("Building remap table...")
    eid_file_remap, eid_remap, seid_remap, seid_file_remap = build_remap(
        shared_eids, shared_seids
    )
    logger.info(
        "Will regenerate %d EventIDs, %d Sub-eventIDs",
        len(eid_file_remap),
        len(seid_remap),
    )

    logger.info("Updating event files...")
    ev_count = update_event_files(eid_file_remap, seid_file_remap)
    logger.info("Updated %d event files", ev_count)

    logger.info("Updating entity files (IDs + deduplicating MentionIDs)...")
    ent_count = update_entity_files(eid_remap, seid_remap)
    logger.info("Updated %d entity files", ent_count)

    logger.info(
        "Done. Total: %d event files, %d entity files modified", ev_count, ent_count
    )


if __name__ == "__main__":
    main()
