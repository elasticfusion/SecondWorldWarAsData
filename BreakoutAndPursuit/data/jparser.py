#!/usr/bin/env python3
import json
import argparse


def get_nested_value(d, key_path):
    parts = key_path.split(".")

    def recurse(current, remaining_parts):
        if not remaining_parts:
            return current
        part = remaining_parts[0]
        if isinstance(current, dict):
            if part in current:
                return recurse(current[part], remaining_parts[1:])
            else:
                return None
        elif isinstance(current, list):
            try:
                index = int(part)
                if 0 <= index < len(current):
                    return recurse(current[index], remaining_parts[1:])
                else:
                    return None
            except ValueError:
                # Apply remaining path to each item in the list
                results = []
                for item in current:
                    res = recurse(item, remaining_parts)
                    if res is not None:
                        results.append(res)
                return results if results else None
        else:
            return None

    return recurse(d, parts)


def is_non_empty(value):
    if value is None:
        return False
    if isinstance(value, str):
        return value != ""
    if isinstance(value, (list, tuple, set)):
        return len(value) > 0 and any(is_non_empty(v) for v in value)
    if isinstance(value, dict):
        return len(value) > 0 and any(is_non_empty(v) for v in value.values())
    return True  # Numbers, booleans, etc., are considered non-empty


parser = argparse.ArgumentParser(description="Extract specific fields from JSON file based on filters")
parser.add_argument("json_file", type=str, help="Path to the JSON file")
parser.add_argument("--event", type=str, default=None, help="Filter by Event")
parser.add_argument("--sub_event", type=str, default=None, help="Filter by Sub-Event")
parser.add_argument("--filter", type=str, default=None,
                    help="Comma-separated field.path=value pairs for filtering (e.g., Place.Place_Name=Normandy,Events.Event=Attack)")
parser.add_argument("--fields", type=str, default=None,
                    help="Comma-separated fields to extract (supports dot notation for nested fields and [index] for lists, e.g., Place.Place_Name,Footnotes.Footnote[0].Source)")
parser.add_argument("--list_events", action="store_true", help="List all events (Event and Sub-Event)")
parser.add_argument("--ignore_empty", action="store_true",
                    help="Ignore empty fields in the output when --fields is used")

args = parser.parse_args()

try:
    with open(args.json_file, "r") as f:
        data = json.load(f)
except FileNotFoundError:
    print("Error: JSON file not found.")
    exit(1)
except json.JSONDecodeError:
    print("Error: Invalid JSON format.")
    exit(1)

# Parse filters if provided
filters = {}
if args.filter:
    for f in args.filter.split(","):
        if '=' in f:
            key, value = f.split('=', 1)
            filters[key.strip()] = value.strip()

fields = args.fields.split(",") if args.fields else None

if args.list_events:
    events = []
    for entry in data:
        event = entry.get("Events", {}).get("Event")
        sub_event = entry.get("Events", {}).get("Sub-Event")
        if event or sub_event:
            item = {}
            if event:
                item["Event"] = event
            if sub_event:
                item["Sub-Event"] = sub_event
            field_values = {}
            has_non_empty_field = False
            if fields:
                for field in fields:
                    value = get_nested_value(entry, field)
                    field_values[field] = value
                    if is_non_empty(value):
                        has_non_empty_field = True
            skip_entry = False
            if fields:
                if args.ignore_empty:
                    if has_non_empty_field:
                        item.update(field_values)
                    else:
                        skip_entry = True
                else:
                    item.update(field_values)
            if not skip_entry:
                events.append(item)
    print(json.dumps(events, indent=4))
else:
    filtered = []
    for entry in data:
        match = True
        if args.event and entry.get("Events", {}).get("Event") != args.event:
            match = False
        if args.sub_event and entry.get("Events", {}).get("Sub-Event") != args.sub_event:
            match = False
        for key_path, value in filters.items():
            if get_nested_value(entry, key_path) != value:
                match = False
        if match:
            filtered.append(entry)

    if fields:
        extracted = []
        for entry in filtered:
            ext = {}
            field_values = {}
            has_non_empty_field = False
            for field in fields:
                value = get_nested_value(entry, field)
                field_values[field] = value
                if is_non_empty(value):
                    has_non_empty_field = True
            skip_entry = False
            if args.ignore_empty:
                if has_non_empty_field:
                    ext.update(field_values)
                else:
                    skip_entry = True
            else:
                ext.update(field_values)
            if not skip_entry:
                extracted.append(ext)
        print(json.dumps(extracted, indent=4))
    else:
        print(json.dumps(filtered, indent=4))