"""Shared merge logic for dedup — used by both ECS scripts and Lambda.

Handles people merge, generic entity merge, event ref updates, and index updates.
"""

import json
import logging
import shutil
from pathlib import Path
from typing import Dict, List, Optional


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

logger = logging.getLogger(__name__)


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
        "EquipmentID",
        "DateID",
        "DateMentionID",
        "PlaceMentionID",
        "WeatherMentionID",
        "EventID",
        "Sub-eventID",
        "Sub_eventID",
        "CasualtyID",
        "LogisticsID",
    }
    for subdir in ("logistics", "casualties", "weather"):
        entity_dir = output_root / subdir
        if not entity_dir.exists():
            continue
        for f in entity_dir.glob("*.json"):
            if f.name.startswith("."):
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if _replace_id_in_obj(data, old_id, new_id, id_fields):
                f.write_text(
                    json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
                )


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

    with open(people_dir / primary_person["filename"], "w", encoding="utf-8") as f:
        json.dump(primary_data, f, indent=2, ensure_ascii=False)

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

    primary_data["event_mentions"] = primary_mentions
    primary_data["aliases"] = aliases
    (entity_dir / primary["filename"]).write_text(
        json.dumps(primary_data, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    primary_name = primary.get("name", primary.get("filename", ""))
    logger.info("✓ Merged %d into %s", len(people) - 1, primary_name)
    return primary_name
