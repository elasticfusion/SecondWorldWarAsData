"""Shared merge logic for dedup — used by both ECS scripts and Lambda.

Handles people merge, generic entity merge, event ref updates, and index updates.
"""

import json
import logging
import shutil
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def _write_entity_guarded(filepath: Path, data: Dict) -> bool:
    """Write an ENTITY record through the central schema write-guard.

    Dedup/merge previously wrote merged entity records with raw json.dump, bypassing the
    write-guard — so a malformed merge (e.g. a person record missing PersonID/name) could be
    persisted. Routing through write_json_with_lock makes every merged entity schema-validated:
    an invalid record is BLOCKED + logged, never written. Entity is derived from the parent dir
    (output/<entity>/<file>.json), matching the guard's path-based resolution. Returns True if
    written, False if the guard blocked it.
    """
    from src.utils.file_lock import write_json_with_lock

    before = filepath.exists()
    before_mtime = filepath.stat().st_mtime if before else None
    write_json_with_lock(filepath, data, entity=filepath.parent.name)
    # Detect a block: file unchanged (not created, or mtime unchanged).
    if not filepath.exists():
        logger.error(
            "Merge write BLOCKED (schema-invalid) — not persisted: %s", filepath.name
        )
        return False
    if before and filepath.stat().st_mtime == before_mtime:
        logger.error(
            "Merge write BLOCKED (schema-invalid) — kept prior: %s", filepath.name
        )
        return False
    return True


def _backup_before_delete(filepath: Path) -> None:
    """Copy file to dedup/backups/ before deletion for undo support."""
    try:
        # Find output root (parent of entity dir)
        output_root = filepath.parent.parent
        backup_dir = output_root.parent / "dedup" / "backups" / filepath.parent.name
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(filepath, backup_dir / filepath.name)
    except Exception:
        pass  # Best-effort — don't block merge on backup failure


def _notify_deletion(path: Path) -> None:
    """Notify that a file was deleted (for S3 cleanup)."""
    if _deletion_callback:
        _deletion_callback(path)


_deletion_callback = None


def set_deletion_callback(callback) -> None:
    """Register a callback for tracking file deletions. Called by ecs_entrypoint."""
    global _deletion_callback
    _deletion_callback = callback


from src.extraction.people import _merge_person


def _dynamo_merge_sync(
    entity_type: str, primary_id: str, primary_data: Dict, secondary_id: str
) -> None:
    """Mirror a dedup merge into DynamoEntityStore (#9): delete the merged-away
    secondary and put the merged primary, so the store doesn't drift from the
    files (a stale secondary lingering + a stale primary). Best-effort — a Dynamo
    failure must not block the (already-completed) file merge; the Phase-3 start
    reconciliation / next incremental dedup will re-sync. No-op when no store
    (local/file mode)."""
    try:
        from src.utils.entity_store import get_entity_store

        store = get_entity_store()
        if not store:
            return
        if secondary_id:
            store.delete(entity_type, secondary_id)
        if primary_id:
            store.put(entity_type, primary_id, primary_data)
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(
            "Dynamo merge-sync failed (%s primary=%s secondary=%s): %s",
            entity_type,
            primary_id,
            secondary_id,
            e,
        )


def load_person(people_dir: Path, filename: str) -> Dict:
    """Load a person/entity file."""
    with open(people_dir / filename, "r", encoding="utf-8") as f:
        return json.load(f)


def merge_people(primary: Dict, secondary: Dict) -> Dict:
    """Merge secondary person into primary."""
    return _merge_person(primary, secondary)


def update_index(index_path: Path, old_name: str, new_filename: str) -> None:
    """Update index.json to point old name to new file."""
    if not index_path.exists():
        return
    with open(index_path, "r", encoding="utf-8") as f:
        index = json.load(f)
    index[old_name.lower().strip()] = new_filename
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)


def update_event_refs(
    output_root: Path, old_id: str, new_id: str, ref_key: str
) -> None:
    """Replace old entity ID with new ID in all event and entity files."""
    for f in sorted(output_root.rglob("*-event.json")):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        event = d.get("Event", d)
        changed = False
        for se in event.get("Sub-events", []):
            refs = se.get(ref_key, [])
            if old_id in refs:
                refs[refs.index(old_id)] = new_id
                changed = True
        if changed:
            with open(f, "w", encoding="utf-8") as fh:
                json.dump(d, fh, indent=2, ensure_ascii=False)

    # Update entity files that reference the old ID (targeted field replacement)
    id_fields = {
        "PersonID",
        "PlaceID",
        "PeopleGroupID",
        "GroupID",
        "EquipmentID",
        "DateID",
        "DateMentionID",
        "PlaceMentionID",
        "WeatherMentionID",
        "WeatherID",
        "EventID",
        "Sub-eventID",
        "Sub_eventID",
        "CasualtyID",
        "LogisticsID",
    }
    # Scan ALL entity dirs that can hold cross-references (not just logistics/casualties/
    # weather) — e.g. equipment mentions carry PlaceID/PeopleGroupID/PersonID/EquipmentID,
    # so a place/person/group merge must rebase those too or they dangle.
    for subdir in (
        "logistics",
        "casualties",
        "weather",
        "equipment",
        "people",
        "people_groups",
        "dates",
        "maps",
    ):
        entity_dir = output_root / subdir
        if not entity_dir.exists():
            continue
        for f in entity_dir.glob("*.json"):
            if f.name.startswith(".") or f.name == "index.json":
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if _replace_id_in_obj(data, old_id, new_id, id_fields):
                _write_entity_guarded(f, data)


def _replace_id_in_obj(obj, old_id: str, new_id: str, id_fields: set) -> bool:
    """Recursively replace old_id with new_id only in known ID fields. Returns True if changed."""
    changed = False
    if isinstance(obj, dict):
        for key, val in obj.items():
            if key in id_fields and val == old_id:
                obj[key] = new_id
                changed = True
            elif isinstance(val, (dict, list)):
                if _replace_id_in_obj(val, old_id, new_id, id_fields):
                    changed = True
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            if isinstance(item, str) and item == old_id:
                obj[i] = new_id
                changed = True
            elif isinstance(item, (dict, list)):
                if _replace_id_in_obj(item, old_id, new_id, id_fields):
                    changed = True
    return changed


def do_merge(people_dir: Path, people: List[Dict], primary_idx: int) -> Optional[str]:
    """Merge secondary people into primary. Returns primary name or None on failure."""
    primary_person = people[primary_idx]
    try:
        primary_data = load_person(people_dir, primary_person["filename"])
    except (OSError, json.JSONDecodeError) as e:
        logger.error("Failed to load primary %s: %s", primary_person["filename"], e)
        return None

    primary_id = primary_data.get("PersonID", "")
    output_root = people_dir.parent
    merged_count = 0

    for i, person in enumerate(people):
        if i == primary_idx:
            continue
        secondary_file = people_dir / person["filename"]
        if not secondary_file.exists():
            logger.info("Skipping %s: file already merged/deleted", person["name"])
            continue
        try:
            secondary_data = load_person(people_dir, person["filename"])
        except (OSError, json.JSONDecodeError):
            logger.warning("Failed to load %s, skipping", person["filename"])
            continue

        secondary_id = secondary_data.get("PersonID", "")
        primary_data = merge_people(primary_data, secondary_data)

        index_path = people_dir / "index.json"
        update_index(index_path, person["name"], primary_person["filename"])

        _backup_before_delete(secondary_file)
        secondary_file.unlink()
        _notify_deletion(secondary_file)
        merged_count += 1

        if secondary_id and primary_id:
            update_event_refs(output_root, secondary_id, primary_id, "people")
        # #9: remove the merged-away secondary from DynamoDB too.
        if secondary_id:
            _dynamo_merge_sync("people", "", {}, secondary_id)

    _write_entity_guarded(people_dir / primary_person["filename"], primary_data)

    # #9: mirror the merged primary into DynamoDB (put, not delete).
    if primary_id and merged_count:
        _dynamo_merge_sync("people", primary_id, primary_data, "")

    logger.info(
        "✓ Merged %d duplicate(s) into %s", merged_count, primary_person["name"]
    )
    return primary_person["name"]


def merge_generic(
    entity_dir: Path,
    people: List[Dict],
    primary_idx: int,
    id_field: str = "PersonID",
    name_fields: tuple = ("name", "current_name", "group_name", "common_name"),
) -> Optional[str]:
    """Merge generic entity files (places, groups, equipment). Returns primary name."""
    primary = people[primary_idx]
    try:
        primary_data = json.loads(
            (entity_dir / primary["filename"]).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return None

    primary_mentions = primary_data.get("event_mentions", [])
    aliases = primary_data.get("aliases", []) or []
    seen_sub_events = {
        m.get("Sub_eventID") for m in primary_mentions if m.get("Sub_eventID")
    }
    merged_any = False

    for i, p in enumerate(people):
        if i == primary_idx:
            continue
        f = entity_dir / p["filename"]
        if not f.exists():
            continue
        try:
            secondary = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        # Dedup event mentions by Sub_eventID
        for m in secondary.get("event_mentions", []):
            sub_id = m.get("Sub_eventID")
            if not sub_id or sub_id not in seen_sub_events:
                primary_mentions.append(m)
                if sub_id:
                    seen_sub_events.add(sub_id)

        # Add secondary name as alias
        for field in name_fields:
            sec_name = secondary.get(field, "")
            if sec_name and sec_name not in aliases:
                aliases.append(sec_name)
                break

        # Update event files: replace old ID with primary ID
        old_id = secondary.get(id_field, "")
        new_id = primary_data.get(id_field, "")
        if old_id and new_id and old_id != new_id:
            output_root = entity_dir.parent
            update_event_refs(output_root, old_id, new_id, id_field)

        _backup_before_delete(f)
        f.unlink()
        _notify_deletion(f)
        # #9: remove the merged-away secondary from DynamoDB too.
        if old_id:
            _dynamo_merge_sync(entity_dir.name, "", {}, old_id)
        merged_any = True

    primary_data["event_mentions"] = primary_mentions
    primary_data["aliases"] = aliases
    _write_entity_guarded(entity_dir / primary["filename"], primary_data)

    # #9: mirror the merged primary into DynamoDB (put, not delete).
    primary_id = primary_data.get(id_field, "")
    if primary_id and merged_any:
        _dynamo_merge_sync(entity_dir.name, primary_id, primary_data, "")

    primary_name = primary.get("name", primary.get("filename", ""))
    logger.info("✓ Merged %d into %s", len(people) - 1, primary_name)
    return primary_name
