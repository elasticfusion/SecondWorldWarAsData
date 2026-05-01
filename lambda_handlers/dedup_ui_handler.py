"""Lambda handler for dedup review web UI.

Serves an HTML page showing duplicate groups and handles merge/skip/exclude actions.
Protected by API Gateway Lambda authorizer (basic auth).

Routes:
  GET  /dedup              — HTML review page
  GET  /dedup/api/groups   — JSON list of pending duplicate groups
  POST /dedup/api/action   — merge, skip, or exclude a group
  GET  /dedup/api/status   — review completion status
  POST /dedup/api/complete — mark review as done, ungate Phase 3
"""

import json
import logging
import os
from pathlib import Path

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

STATUS_KEY = "dedup/review_status.json"


def _append_to_manifest(keys: list) -> None:
    """Append changed S3 keys to the Phase 2 manifest in DynamoDB."""
    if not keys:
        return
    try:
        table_name = os.environ.get("CACHE_TABLE", "dev-wwii-api-cache")
        region = os.environ.get("AWS_REGION", "us-east-1")
        table = boto3.resource("dynamodb", region_name=region).Table(table_name)
        resp = table.get_item(Key={"cache_key": "manifest#phase2"})
        existing = resp.get("Item", {}).get("keys", [])
        merged = list(set(existing) | set(keys))
        table.put_item(Item={"cache_key": "manifest#phase2", "keys": merged})
    except Exception as e:
        logger.warning("Failed to append to manifest: %s", e)


def handler(event, _context):
    """API Gateway proxy handler."""
    from src.utils.config import load_config
    from src.utils.storage import S3Storage

    config = load_config()
    bucket = os.environ.get("S3_BUCKET", "")
    region = config.get("aws", {}).get("region", "us-east-1")
    storage = S3Storage(bucket=bucket, region=region) if bucket else None

    method = event.get("httpMethod", "GET")
    path = event.get("path", "/dedup")
    route_key = f"{method} {path}"

    routes = {
        "GET /dedup": lambda: _serve_ui(),
        "GET /dedup/api/groups": lambda: _get_groups(storage),
        "POST /dedup/api/action": lambda: _handle_action(event, storage, config),
        "GET /dedup/api/status": lambda: _get_status(storage),
        "POST /dedup/api/complete": lambda: _mark_complete(storage, config),
    }

    # Handle parameterized routes
    if path.startswith("/dedup/api/detail/"):
        from urllib.parse import unquote

        decoded_path = unquote(path)
        parts = decoded_path.split("/")
        if len(parts) >= 6:
            return _get_detail(storage, parts[4], "/".join(parts[5:]))

    if path.startswith("/dedup/api/search/"):
        from urllib.parse import unquote

        decoded_path = unquote(path)
        parts = decoded_path.split("/")
        if len(parts) >= 6:
            return _search_entities(storage, parts[4], parts[5])

    if route_key == "POST /dedup/api/reclassify":
        return _reclassify(event, storage)

    if route_key == "POST /dedup/api/assign":
        return _assign_to_person(event, storage)

    return routes.get(route_key, lambda: _json_response(404, {"error": "not found"}))()


ENTITY_PREFIXES = {
    "people": "output/people",
    "places": "output/places",
    "groups": "output/people_groups",
    "equipment": "output/equipment",
}


def _get_detail(storage, entity_type, filename):
    """Return sanitized JSON for a specific entity file."""
    prefix = ENTITY_PREFIXES.get(entity_type)
    if not prefix:
        return _json_response(400, {"error": f"unknown entity type: {entity_type}"})
    try:
        data = storage.read_json(f"{prefix}/{filename}")
        # Summarize event_mentions as event name counts
        if "event_mentions" in data and isinstance(data["event_mentions"], list):
            from collections import Counter

            events = Counter(
                m.get("Event_Name", "Unknown") for m in data["event_mentions"]
            )
            data["event_mentions"] = [
                f"{name} ({count})" for name, count in events.most_common()
            ]
        # Summarize enrichment_data
        if "enrichment_data" in data and isinstance(data["enrichment_data"], dict):
            data["enrichment_data"] = f"[{len(data['enrichment_data'])} fields]"
        return _json_response(200, data)
    except Exception as e:
        return _json_response(404, {"error": str(e)})


def _get_groups(storage):
    """Return pending duplicate groups for people, places, and groups."""
    result = {}
    for entity, report_path in [
        ("people", "output/people/duplicate_report.json"),
        ("places", "output/places/duplicate_report.json"),
        ("groups", "output/people_groups/duplicate_report.json"),
        ("equipment", "output/equipment/duplicate_report.json"),
    ]:
        try:
            data = storage.read_json(report_path)
            items = data.get("duplicates", data.get("related", []))
            # Sort within each group: people by longest name first, others alphabetically
            for item in items:
                people = item.get("people", item.get("groups", []))
                if entity == "people":
                    people.sort(key=lambda p: len(p.get("name", "")), reverse=True)
                else:
                    people.sort(key=lambda p: p.get("name", "").lower())
            items.sort(
                key=lambda g: max(
                    (p.get("name", "") for p in g.get("people", g.get("groups", [{}]))),
                    key=len,
                    default="",
                ).lower()
            )
            result[entity] = items
        except Exception:
            result[entity] = []
    return _json_response(200, result)


def _handle_action(event, storage, config):
    """Process merge/skip/exclude for a duplicate group."""
    body = json.loads(event.get("body", "{}"))
    action = body.get("action")  # merge, skip, exclude
    entity_type = body.get("entity_type")  # people, places, groups
    group_index = body.get("group_index", 0)
    primary_index = body.get("primary_index", 0)
    excluded_indices = set(body.get("excluded_indices", []))

    if action == "merge":
        return _do_merge(
            entity_type, group_index, primary_index, excluded_indices, storage, config
        )
    elif action == "exclude":
        return _do_exclude(entity_type, group_index, storage)
    elif action == "skip":
        # Read group content for matching
        try:
            rpt = storage.read_json(_report_path(entity_type))
            grps = rpt.get("duplicates", rpt.get("related", []))
            grp_people = (
                grps[group_index].get("people", []) if group_index < len(grps) else []
            )
        except Exception:
            grp_people = []
        _remove_group_from_report(entity_type, group_index, storage, grp_people)
        return _json_response(200, {"result": "skipped"})
    return _json_response(400, {"error": f"unknown action: {action}"})


def _do_merge(
    entity_type, group_index, primary_index, excluded_indices, storage, _config
):
    """Merge a duplicate group, skipping excluded entries."""
    report_path = _report_path(entity_type)
    report = storage.read_json(report_path)
    groups = report.get("duplicates", report.get("related", []))

    if group_index >= len(groups):
        return _json_response(400, {"error": "invalid group_index"})

    group = groups[group_index]
    all_people = group.get("people", group.get("groups", []))
    people, primary_index = _filter_and_remap(
        all_people, excluded_indices, primary_index
    )

    if len(people) < 2:
        _remove_group_from_report(entity_type, group_index, storage, all_people)
        return _json_response(
            200,
            {"result": "skipped", "reason": "fewer than 2 entries after exclusions"},
        )

    if entity_type == "people":
        result = _merge_people_files(people, primary_index, storage)
        if result:
            _remove_group_from_report(entity_type, group_index, storage, all_people)
            return result
    else:
        _merge_generic_files(entity_type, people, primary_index, storage)

    _remove_group_from_report(entity_type, group_index, storage, all_people)

    excluded_people = [p for i, p in enumerate(all_people) if i in excluded_indices]
    if len(excluded_people) >= 2:
        _re_cluster_and_add(entity_type, excluded_people, storage)

    return _json_response(
        200, {"result": "merged", "primary": people[primary_index]["name"]}
    )


def _filter_and_remap(all_people, excluded_indices, primary_index):
    """Filter excluded entries and remap primary_index."""
    people = [p for i, p in enumerate(all_people) if i not in excluded_indices]
    if primary_index < len(all_people) and primary_index not in excluded_indices:
        primary_entry = all_people[primary_index]
        primary_index = next((i for i, p in enumerate(people) if p is primary_entry), 0)
    else:
        primary_index = 0
    return people, primary_index


def _merge_generic_files(entity_type, people, primary_index, storage):
    """Merge entity files for non-people types (places, groups, equipment)."""
    prefix = ENTITY_PREFIXES.get(entity_type, "")
    if not prefix:
        return
    primary = people[primary_index]
    try:
        primary_data = storage.read_json(f"{prefix}/{primary['filename']}")
    except Exception:
        return
    primary_mentions = primary_data.get("event_mentions", [])
    aliases = primary_data.get("aliases", []) or []

    for i, p in enumerate(people):
        if i == primary_index:
            continue
        try:
            secondary = storage.read_json(f"{prefix}/{p['filename']}")
            secondary_mentions = secondary.get("event_mentions", [])
            primary_mentions.extend(secondary_mentions)
            sec_name = secondary.get(
                "name",
                secondary.get(
                    "current_name",
                    secondary.get("group_name", secondary.get("common_name", "")),
                ),
            )
            if sec_name and sec_name not in aliases:
                aliases.append(sec_name)
            storage.delete(f"{prefix}/{p['filename']}")
        except Exception as e:
            logger.warning("Could not delete %s: %s", p.get("filename"), e)

    primary_data["event_mentions"] = primary_mentions
    primary_data["aliases"] = aliases
    storage.write_json(f"{prefix}/{primary['filename']}", primary_data)
    _append_to_manifest([f"{prefix}/{primary['filename']}"])


def _merge_people_files(people, primary_index, storage):
    """Download, merge, and re-upload people files. Returns error response or None."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        people_dir = tmpdir / "people"
        people_dir.mkdir()
        available = _download_people(people, people_dir, storage)
        if len(available) < 2:
            return _json_response(
                200,
                {"result": "skipped", "reason": "already merged or missing files"},
            )
        # Download or create index.json
        try:
            index_data = storage.read_json("output/people/index.json")
        except Exception:
            index_data = {}
        (people_dir / "index.json").write_text(
            json.dumps(index_data, ensure_ascii=False), encoding="utf-8"
        )
        (tmpdir / "output").mkdir(exist_ok=True)

        from scripts.merge_duplicate_people import _do_merge as do_merge_people

        do_merge_people(people_dir, available, min(primary_index, len(available) - 1))
        _upload_merged(people, primary_index, people_dir, storage)
    return None


def _download_people(people, people_dir, storage):
    """Download person files to temp dir. Returns list of available entries."""
    available = []
    for p in people:
        try:
            data = storage.read_json(f"output/people/{p['filename']}")
            (people_dir / p["filename"]).write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
            available.append(p)
        except Exception:
            logger.warning("File not found, skipping: %s", p["filename"])
    return available


def _upload_merged(people, primary_index, people_dir, storage):
    """Upload merged files and delete removed ones."""
    changed_keys = []
    for f in people_dir.glob("*.json"):
        key = f"output/people/{f.name}"
        storage.write_json(
            key,
            json.loads(f.read_text(encoding="utf-8")),
        )
        changed_keys.append(key)
    for i, p in enumerate(people):
        if i != primary_index and not (people_dir / p["filename"]).exists():
            storage.delete(f"output/people/{p['filename']}")
    _append_to_manifest(changed_keys)


def _do_exclude(entity_type, group_index, storage):
    """Add group to exclusion list."""
    report_path = _report_path(entity_type)
    report = storage.read_json(report_path)
    groups = report.get("duplicates", report.get("related", []))

    if group_index >= len(groups):
        return _json_response(400, {"error": "invalid group_index"})

    group = groups[group_index]
    people = group.get("people", group.get("groups", []))

    excl_path = _exclusion_path(entity_type)
    try:
        excl = storage.read_json(excl_path)
    except Exception:
        excl = {"exclusions": []}

    # Add all pairs
    for i, p1 in enumerate(people):
        for p2 in people[i + 1 :]:
            excl["exclusions"].append(
                {
                    "person1": p1.get("filename", p1.get("name", "")),
                    "person2": p2.get("filename", p2.get("name", "")),
                }
            )
    storage.write_json(excl_path, excl)
    _remove_group_from_report(entity_type, group_index, storage, people)
    return _json_response(200, {"result": "excluded"})


def _search_entities(storage, entity_type, query):
    """Search entities by partial name match. Returns top 10 matches."""
    prefix = ENTITY_PREFIXES.get(entity_type)
    if not prefix:
        return _json_response(400, {"error": f"unknown entity type: {entity_type}"})
    query_lower = query.lower()
    matches = []
    try:
        files = storage.list_files(prefix, "*.json")
        for filepath in files:
            filename = filepath.split("/")[-1]
            if filename in (
                "index.json",
                "duplicate_report.json",
                "not_duplicates.json",
            ):
                continue
            name = filename.replace(".json", "").replace("_", " ")
            if query_lower in name.lower():
                matches.append({"name": name, "filename": filename})
                if len(matches) >= 10:
                    break
    except Exception as e:
        return _json_response(500, {"error": str(e)})
    return _json_response(200, {"matches": matches, "query": query})


def _assign_to_person(event, storage):
    """Merge a title-only entry into an existing named person."""
    body = json.loads(event.get("body", "{}"))
    source_file = body.get("source_file", "")
    target_file = body.get("target_file", "")
    entity_type = body.get("entity_type", "people")
    prefix = ENTITY_PREFIXES.get(entity_type)
    if not prefix or not source_file or not target_file:
        return _json_response(
            400, {"error": "missing source_file, target_file, or entity_type"}
        )
    try:
        source = storage.read_json(f"{prefix}/{source_file}")
        target = storage.read_json(f"{prefix}/{target_file}")
        source_mentions = source.get("event_mentions", [])
        target_mentions = target.get("event_mentions", [])
        aliases = target.get("aliases", []) or []
        source_name = source.get("name", "")
        if source_name and source_name not in aliases:
            aliases.append(source_name)
        target["aliases"] = aliases
        target["event_mentions"] = target_mentions + source_mentions
        storage.write_json(f"{prefix}/{target_file}", target)
        storage.delete(f"{prefix}/{source_file}")
        _append_to_manifest([f"{prefix}/{target_file}"])
        _remove_entry_from_report(entity_type, source_file, storage)
        logger.info("Assigned %s → %s", source_file, target_file)
        return _json_response(
            200, {"result": "assigned", "source": source_file, "target": target_file}
        )
    except Exception as e:
        return _json_response(500, {"error": str(e)})


def _reclassify(event, storage):
    """Move an entity file from one category to another, transforming the schema."""
    body = json.loads(event.get("body", "{}"))
    source_type = body.get("entity_type")
    target_type = body.get("target_type")
    filename = body.get("filename", "")

    if not all([source_type, target_type, filename]) or source_type == target_type:
        return _json_response(400, {"error": "invalid reclassify request"})

    source_prefix = ENTITY_PREFIXES.get(source_type)
    target_prefix = ENTITY_PREFIXES.get(target_type)
    if not source_prefix or not target_prefix:
        return _json_response(400, {"error": "unknown entity type"})

    try:
        data = storage.read_json(f"{source_prefix}/{filename}")
    except Exception:
        # File already moved or deleted — clean up the report and return
        _remove_entry_from_report(source_type, filename, storage)
        return _json_response(200, {"result": "skipped", "reason": "file not found"})

    try:
        transformed = _transform_schema(data, source_type, target_type)
        storage.write_json(f"{target_prefix}/{filename}", transformed)
        storage.delete(f"{source_prefix}/{filename}")
        _append_to_manifest([f"{target_prefix}/{filename}"])
        logger.info("Reclassified %s from %s to %s", filename, source_type, target_type)
        # Remove any groups containing this file from the source report
        _remove_entry_from_report(source_type, filename, storage)
        return _json_response(
            200, {"result": "reclassified", "from": source_type, "to": target_type}
        )
    except Exception as e:
        return _json_response(500, {"error": str(e)})


def _transform_schema(data, source_type, target_type):
    """Transform entity data from one schema to another."""
    name = data.get("name", data.get("current_name", data.get("group_name", "")))
    mentions = data.get("event_mentions", [])
    lang = data.get("source_language", "English")

    if target_type == "groups":
        return {
            "GroupID": data.get(
                "GroupID", data.get("PersonID", data.get("PlaceID", ""))
            ),
            "name": name,
            "group_name": name,
            "group_type": "military_unit",
            "source_language": lang,
            "country_of_origin": data.get(
                "nationality", data.get("country_of_origin", "")
            ),
            "description": data.get(
                "biographical_details", data.get("description", "")
            ),
            "event_mentions": mentions,
        }
    elif target_type == "people":
        return {
            "PersonID": data.get(
                "PersonID", data.get("GroupID", data.get("PlaceID", ""))
            ),
            "name": name,
            "source_language": lang,
            "nationality": data.get("country_of_origin", data.get("nationality", "")),
            "event_mentions": mentions,
        }
    elif target_type == "places":
        return {
            "PlaceID": data.get(
                "PlaceID", data.get("PersonID", data.get("GroupID", ""))
            ),
            "current_name": name,
            "name": name,
            "source_language": lang,
            "geography_type": "other",
            "event_mentions": mentions,
        }
    elif target_type == "equipment":
        return {
            "EquipmentID": data.get(
                "EquipmentID",
                data.get("PersonID", data.get("GroupID", data.get("PlaceID", ""))),
            ),
            "common_name": name,
            "name": name,
            "category": "other",
            "country_of_origin": data.get(
                "nationality", data.get("country_of_origin", "")
            ),
            "event_mentions": mentions,
        }
    return data


def _re_cluster_and_add(entity_type, people, storage):
    """Re-cluster excluded entries into sub-groups before adding back to report."""
    from difflib import SequenceMatcher

    if len(people) < 2:
        return

    # Simple clustering: group entries with >=0.65 name similarity
    clusters = []
    used = set()
    for i, p1 in enumerate(people):
        if i in used:
            continue
        cluster = [p1]
        used.add(i)
        for j, p2 in enumerate(people[i + 1 :], i + 1):
            if j in used:
                continue
            sim = SequenceMatcher(None, p1["name"].lower(), p2["name"].lower()).ratio()
            if sim >= 0.65:
                cluster.append(p2)
                used.add(j)
        if len(cluster) >= 2:
            clusters.append(cluster)

    for cluster in clusters:
        _add_group_to_report(entity_type, cluster, storage)


def _add_group_to_report(entity_type, people, storage):
    """Add a new group to the duplicate report (for excluded entries that need re-review)."""
    report_path = _report_path(entity_type)
    try:
        report = storage.read_json(report_path)
        groups = report.get("duplicates", report.get("related", []))
        groups.append(
            {
                "confidence": 0.5,
                "reasons": ["split from previous group"],
                "people": people,
            }
        )
        report["duplicate_groups"] = len(groups)
        storage.write_json(report_path, report)
    except Exception as e:
        logger.warning("Failed to add group to report %s: %s", report_path, e)


def _remove_entry_from_report(entity_type, filename, storage):
    """Remove a reclassified entry from all groups in the duplicate report."""
    report_path = _report_path(entity_type)
    try:
        report = storage.read_json(report_path)
        groups = report.get("duplicates", report.get("related", []))
        updated = []
        for g in groups:
            people = g.get("people", g.get("groups", []))
            filtered = [p for p in people if p.get("filename") != filename]
            if len(filtered) == len(people):
                updated.append(g)  # entry wasn't in this group
            elif len(filtered) >= 1:
                g["people"] = filtered
                updated.append(g)  # keep for further reclassification
            # else: empty group, drop it
        report["duplicates"] = updated
        report["duplicate_groups"] = len(updated)
        storage.write_json(report_path, report)
    except Exception as e:
        logger.warning("Failed to update report after reclassify: %s", e)


def _sort_groups(groups):
    """Sort groups same as UI display — alphabetically by longest name."""
    for g in groups:
        people = g.get("people", g.get("groups", []))
        people.sort(key=lambda p: p.get("name", "").lower())
    groups.sort(
        key=lambda g: max(
            (p.get("name", "") for p in g.get("people", g.get("groups", [{}]))),
            key=len,
            default="",
        ).lower()
    )
    return groups


def _remove_group_from_report(entity_type, group_index, storage, group_people=None):
    """Remove a processed group from the duplicate report."""
    report_path = _report_path(entity_type)
    try:
        report = storage.read_json(report_path)
        groups = _sort_groups(report.get("duplicates", report.get("related", [])))
        removed = False
        if group_people:
            names = {p.get("filename", p.get("name", "")) for p in group_people}
            for i, g in enumerate(groups):
                g_names = {
                    p.get("filename", p.get("name", ""))
                    for p in g.get("people", g.get("groups", []))
                }
                if names & g_names:  # any filename overlap
                    groups.pop(i)
                    removed = True
                    break
        if not removed and 0 <= group_index < len(groups):
            groups.pop(group_index)
        report["duplicate_groups"] = len(groups)
        storage.write_json(report_path, report)
    except Exception as e:
        logger.warning("Failed to update report %s: %s", report_path, e)


def _get_status(storage):
    """Return review status — how many groups remaining."""
    try:
        status = storage.read_json(STATUS_KEY)
    except Exception:
        status = {"complete": False}

    totals = {}
    for entity, report_path in [
        ("people", "output/people/duplicate_report.json"),
        ("places", "output/places/duplicate_report.json"),
        ("groups", "output/people_groups/duplicate_report.json"),
        ("equipment", "output/equipment/duplicate_report.json"),
    ]:
        try:
            data = storage.read_json(report_path)
            items = data.get("duplicates", data.get("related", []))
            totals[entity] = {
                "total": len(items),
                "reviewed": 0,
                "reviewed_indices": [],
            }
        except Exception:
            totals[entity] = {"total": 0, "reviewed": 0, "reviewed_indices": []}

    return _json_response(
        200,
        {"entities": totals, "complete": status.get("complete", False)},
    )


def _mark_complete(storage, config):
    """Mark dedup review as complete, allowing Phase 3 to proceed."""
    try:
        status = storage.read_json(STATUS_KEY)
    except Exception:
        status = {"reviewed": {}}
    status["complete"] = True
    storage.write_json(STATUS_KEY, status)

    # Publish to SNS to trigger Phase 3
    topic_arn = os.getenv("DEDUP_COMPLETE_TOPIC_ARN", "")
    if topic_arn:
        import boto3

        region = config.get("aws", {}).get("region", "us-east-1")
        sns = boto3.client("sns", region_name=region)
        sns.publish(TopicArn=topic_arn, Message=json.dumps({"dedup_complete": True}))

    return _json_response(200, {"result": "complete", "phase3_unblocked": True})


def _report_path(entity_type):
    paths = {
        "people": "output/people/duplicate_report.json",
        "places": "output/places/duplicate_report.json",
        "groups": "output/people_groups/duplicate_report.json",
        "equipment": "output/equipment/duplicate_report.json",
    }
    return paths.get(entity_type, "")


def _exclusion_path(entity_type):
    paths = {
        "people": "output/people/not_duplicates.json",
        "places": "output/places/not_duplicates.json",
        "groups": "output/people_groups/not_related.json",
        "equipment": "output/equipment/not_duplicates.json",
    }
    return paths.get(entity_type, "")


def _json_response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body),
    }


def _serve_ui():
    """Return the HTML review interface."""
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "text/html"},
        "body": _HTML_UI,
    }


_HTML_UI = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dedup Review</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,sans-serif;background:#f5f7fa;color:#1f2937}
.header{background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;padding:20px;text-align:center}
.container{max-width:900px;margin:20px auto;padding:0 20px}
.status-bar{display:flex;gap:20px;margin:20px 0;flex-wrap:wrap}
.status-card{background:#fff;padding:15px 20px;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.1);flex:1;min-width:150px;text-align:center}
.status-card h3{font-size:14px;color:#6b7280;text-transform:uppercase;letter-spacing:1px}
.status-card .num{font-size:32px;font-weight:700;margin:5px 0}
.group-card{background:#fff;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.1);margin:15px 0;overflow:hidden}
.group-header{padding:15px 20px;border-bottom:1px solid #e5e7eb;display:flex;justify-content:space-between;align-items:center}
.confidence{font-weight:600;color:#059669}
.reasons{font-size:13px;color:#6b7280}
.person-row{padding:10px 20px;border-bottom:1px solid #f3f4f6;display:flex;justify-content:space-between;align-items:center}
.person-row:last-child{border-bottom:none}
.person-name{font-weight:500}
.person-file{font-size:12px;color:#9ca3af}
.actions{padding:15px 20px;background:#f9fafb;display:flex;gap:10px;flex-wrap:wrap}
.btn{padding:8px 16px;border:none;border-radius:6px;cursor:pointer;font-size:14px;font-weight:500}
.btn-merge{background:#059669;color:#fff}
.btn-skip{background:#6b7280;color:#fff}
.btn-exclude{background:#dc2626;color:#fff}
.btn-complete{background:#2563eb;color:#fff;padding:12px 24px;font-size:16px;width:100%;margin-top:20px}
.btn:hover{opacity:.9}
.btn:disabled{opacity:.5;cursor:not-allowed}
.tab-bar{display:flex;gap:0;margin:20px 0 0}
.tab{padding:10px 20px;background:#e5e7eb;cursor:pointer;border:none;font-size:14px}
.tab.active{background:#fff;font-weight:600}
.tab:first-child{border-radius:8px 0 0 0}
.tab:last-child{border-radius:0 8px 0 0}
.done{text-decoration:line-through;opacity:.5}
.radio-primary{margin-right:8px}
.btn-detail{background:none;border:1px solid #d1d5db;border-radius:4px;padding:2px 8px;font-size:11px;cursor:pointer;color:#6b7280;margin-left:4px}
.btn-detail:hover{background:#e5e7eb}
.reclassify-sel{font-size:11px;padding:1px 4px;border:1px solid #d1d5db;border-radius:4px;color:#6b7280;margin-left:8px}
</style>
</head>
<body>
<div class="header"><h1>Dedup Review</h1><p>Review and merge duplicates before Phase 3 enrichment</p></div>
<div class="container">
<div class="status-bar" id="status-bar"></div>
<div class="tab-bar" id="tab-bar"></div>
<div id="groups"></div>
<button class="btn btn-complete" id="complete-btn" onclick="markComplete()">Mark Review Complete &amp; Start Phase 3</button>
</div>
<script>
const API = window.location.pathname.replace(/\\/$/, '') + '/api';
let data = {}, reviewed = {}, currentTab = 'people';

async function load() {
  try {
    const [grResp, stResp] = await Promise.all([
      fetch(API+'/groups', {credentials:'include'}),
      fetch(API+'/status', {credentials:'include'})
    ]);
    if (!grResp.ok || !stResp.ok) {
      if (grResp.status === 403 || stResp.status === 403) {
        window.location.reload();
        return;
      }
      console.error('API error:', grResp.status, stResp.status);
      return;
    }
    const [gr, st] = await Promise.all([grResp.json(), stResp.json()]);
    data = gr;
    reviewed = {};
    for (const [k,v] of Object.entries(st.entities||{})) reviewed[k] = new Set(v.reviewed_indices||[]);
    if (st.complete) document.getElementById('complete-btn').disabled = true;
    renderStatus(st);
    renderTabs();
    renderGroups();
  } catch(e) {
    console.error('Load failed:', e);
    setTimeout(load, 2000);
  }
}

function renderStatus(st) {
  const bar = document.getElementById('status-bar');
  bar.innerHTML = '';
  for (const [entity, info] of Object.entries(st.entities||{})) {
    bar.innerHTML += '<div class="status-card"><h3>'+entity+'</h3><div class="num">'+info.reviewed+'/'+info.total+'</div></div>';
  }
}

function renderTabs() {
  const bar = document.getElementById('tab-bar');
  bar.innerHTML = '';
  for (const entity of ['people','places','groups','equipment']) {
    const cls = entity===currentTab?'tab active':'tab';
    bar.innerHTML += '<button class="'+cls+'" onclick="switchTab(\\''+entity+'\\')">'+entity+' ('+(data[entity]||[]).length+')</button>';
  }
}

function switchTab(t) { currentTab=t; renderTabs(); renderGroups(); }

function renderGroups() {
  const el = document.getElementById('groups');
  const groups = data[currentTab] || [];
  if (!groups.length) { el.innerHTML='<p style="padding:20px;text-align:center;color:#9ca3af">No duplicates found</p>'; return; }
  el.innerHTML = groups.map((g,i) => {
    const done = (reviewed[currentTab]||new Set()).has(i);
    const people = g.people || g.groups || [];
    return '<div class="group-card'+(done?' done':'')+'"><div class="group-header"><div><span class="confidence">'+
      (g.confidence*100).toFixed(0)+'% match</span> <span class="reasons">'+
      (g.reasons||[]).join(', ')+'</span></div><div>#'+(i+1)+'</div></div>'+
      '<div class="person-row" style="font-size:11px;color:#9ca3af;padding:5px 20px"><div>Include &nbsp; Primary</div></div>'+
      people.map((p,j)=>'<div class="person-row"><div><input type="checkbox" class="include-cb" data-group="'+i+'" data-idx="'+j+'" checked title="Include in merge" onchange="syncInclude('+i+','+j+')"> <input type="radio" name="primary-'+i+'" value="'+j+'" class="radio-primary"'+(j===0?' checked':'')+' title="Merge target" onclick="ensureIncluded('+i+','+j+')">'+
        '<span class="person-name">'+p.name+'</span> <button class="btn-detail" onclick="toggleDetail('+i+','+j+',\\''+encodeURIComponent(p.filename||'')+'\\')">▶ Details</button></div><div class="person-file">'+
        (p.filename||p.PersonID||p.GroupID||'')+' <select class="reclassify-sel" id="recl-'+i+'-'+j+'"><option value="">Move to...</option><option value="people">people</option><option value="places">places</option><option value="groups">groups</option><option value="equipment">equipment</option></select><button class="btn-detail" onclick="reclassify('+i+','+j+',\\''+encodeURIComponent(p.filename||'')+'\\')">↗</button> <input type="text" class="assign-input" id="assign-'+i+'-'+j+'" placeholder="Assign to..." style="width:120px;font-size:11px;padding:1px 4px;border:1px solid #d1d5db;border-radius:4px;margin-left:4px"><button class="btn-detail" onclick="searchAndAssign('+i+','+j+',\\''+encodeURIComponent(p.filename||'')+'\\')">→</button></div></div><div class="detail-panel" id="detail-'+i+'-'+j+'" style="display:none;padding:5px 20px 10px 50px;background:#f9fafb;font-size:12px;overflow-x:auto"><pre>Loading...</pre></div>').join('')+
      '<div class="actions">'+
        (done?'<span style="color:#059669">✓ Reviewed</span>':
        (people.length>=2?'<button class="btn btn-merge" onclick="doAction('+i+',\\'merge\\')">Merge Selected</button>'+
        '<button class="btn btn-skip" onclick="doAction('+i+',\\'skip\\')">Skip</button>'+
        '<button class="btn btn-exclude" onclick="doAction('+i+',\\'exclude\\')">Not Duplicates</button>':
        '<button class="btn btn-skip" onclick="doAction('+i+',\\'skip\\')">Dismiss</button> <span style="color:#6b7280;font-size:13px">Use ↗ to reclassify</span>'))+
      '</div></div>';
  }).join('');
}

let processing = false;

async function doAction(idx, action) {
  if (processing) return;
  processing = true;
  document.querySelectorAll('.btn').forEach(b => b.disabled = true);
  try {
    const primary = document.querySelector('input[name="primary-'+idx+'"]:checked');
    const excluded = [];
    document.querySelectorAll('.include-cb[data-group="'+idx+'"]').forEach(cb => {
      if (!cb.checked) excluded.push(parseInt(cb.dataset.idx));
    });
    const body = {action, entity_type:currentTab, group_index:idx, primary_index: primary?parseInt(primary.value):0, excluded_indices: excluded};
    await fetch(API+'/action', {method:'POST', body:JSON.stringify(body), headers:{'Content-Type':'application/json'}, credentials:'include'});
    await load();
  } finally {
    processing = false;
    document.querySelectorAll('.btn').forEach(b => b.disabled = false);
  }
}

async function markComplete() {
  if (processing) return;
  if (!confirm('Mark review complete and unblock Phase 3?')) return;
  processing = true;
  document.getElementById('complete-btn').disabled = true;
  await fetch(API+'/complete', {method:'POST', credentials:'include'});
  alert('Phase 3 unblocked!');
  processing = false;
}

async function searchAndAssign(groupIdx, personIdx, filename) {
  const input = document.getElementById('assign-'+groupIdx+'-'+personIdx);
  const query = input.value.trim();
  if (!query) { alert('Enter a partial name to search'); return; }
  if (processing) return;
  processing = true;
  try {
    const resp = await fetch(API+'/search/'+currentTab+'/'+encodeURIComponent(query), {credentials:'include'});
    const data = await resp.json();
    const matches = data.matches || [];
    if (!matches.length) { alert('No matches found for: '+query); return; }
    const names = matches.map((m,i) => (i+1)+'. '+m.name+' ('+m.filename+')').join('\\n');
    const choice = prompt('Select a match (enter number):\\n\\n'+names+'\\n\\nOr 0 to cancel:');
    if (!choice || choice === '0') return;
    const idx = parseInt(choice) - 1;
    if (idx < 0 || idx >= matches.length) { alert('Invalid selection'); return; }
    const target = matches[idx];
    const body = {source_file: decodeURIComponent(filename), target_file: target.filename, entity_type: currentTab};
    const assignResp = await fetch(API+'/assign', {method:'POST', body:JSON.stringify(body), headers:{'Content-Type':'application/json'}, credentials:'include'});
    const result = await assignResp.json();
    if (result.error) { alert(result.error); } else { alert('Assigned to: '+target.name); await load(); }
  } finally { processing = false; }
}

async function reclassify(groupIdx, personIdx, filename) {
  const sel = document.getElementById('recl-'+groupIdx+'-'+personIdx);
  const target = sel.value;
  if (!target || target === currentTab) { alert('Select a different category'); return; }
  if (processing) return;
  processing = true;
  try {
    const body = {entity_type: currentTab, target_type: target, filename: decodeURIComponent(filename)};
    const resp = await fetch(API+'/reclassify', {method:'POST', body:JSON.stringify(body), headers:{'Content-Type':'application/json'}, credentials:'include'});
    const data = await resp.json();
    if (data.error) { alert(data.error); } else { await load(); }
  } finally { processing = false; }
}

function syncInclude(groupIdx, personIdx) {
  const cb = document.querySelector('.include-cb[data-group="'+groupIdx+'"][data-idx="'+personIdx+'"]');
  if (!cb.checked) {
    const radio = document.querySelector('input[name="primary-'+groupIdx+'"][value="'+personIdx+'"]');
    if (radio && radio.checked) {
      const firstChecked = document.querySelector('.include-cb[data-group="'+groupIdx+'"]:checked');
      if (firstChecked) {
        document.querySelector('input[name="primary-'+groupIdx+'"][value="'+firstChecked.dataset.idx+'"]').checked = true;
      }
    }
  }
}

function ensureIncluded(groupIdx, personIdx) {
  const cb = document.querySelector('.include-cb[data-group="'+groupIdx+'"][data-idx="'+personIdx+'"]');
  if (cb && !cb.checked) cb.checked = true;
}

async function toggleDetail(groupIdx, personIdx, filename) {
  const panel = document.getElementById('detail-'+groupIdx+'-'+personIdx);
  if (panel.style.display === 'none') {
    panel.style.display = 'block';
    if (panel.querySelector('pre').textContent === 'Loading...') {
      try {
        const resp = await fetch(API+'/detail/'+currentTab+'/'+filename, {credentials:'include'});
        const data = await resp.json();
        panel.querySelector('pre').textContent = JSON.stringify(data, null, 2);
      } catch(e) {
        panel.querySelector('pre').textContent = 'Error loading: '+e.message;
      }
    }
  } else {
    panel.style.display = 'none';
  }
}

load();
</script>
</body>
</html>"""
