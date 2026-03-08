#!/usr/bin/env python3
import json
from pathlib import Path


def validate_place_structure(data, filename):
    """Validate place JSON against schema"""
    issues = []

    if not isinstance(data, list):
        issues.append(f"Root should be array, got {type(data)}")
        return issues

    for idx, item in enumerate(data):
        # Check top-level fields
        required = ["Event_Name", "EventID", "Sub_event_Name", "Sub_eventID", "Place_Mentions"]
        for field in required:
            if field not in item:
                issues.append(f"Item {idx}: Missing '{field}'")

        # Check Place_Mentions
        if "Place_Mentions" in item:
            for pidx, place in enumerate(item["Place_Mentions"]):
                # Required fields for all places
                place_required = ["PlaceMentionID", "source_language", "original_text"]
                for field in place_required:
                    if field not in place:
                        issues.append(f"Item {idx}, Place {pidx}: Missing '{field}'")

                # Check if it's a route or regular place
                if "route" in place:
                    # Route validation
                    if not isinstance(place["route"], list):
                        issues.append(f"Item {idx}, Place {pidx}: 'route' should be array")
                    else:
                        for ridx, stop in enumerate(place["route"]):
                            route_required = [
                                "sequence",
                                "current_name",
                                "latitude",
                                "longitude",
                                "geography_type",
                                "bounding_box_100km",
                            ]
                            for field in route_required:
                                if field not in stop:
                                    issues.append(
                                        f"Item {idx}, Place {pidx}, Route {ridx}: Missing '{field}'"
                                    )
                else:
                    # Regular place validation
                    regular_required = [
                        "current_name",
                        "latitude",
                        "longitude",
                        "geography_type",
                        "bounding_box_100km",
                    ]
                    for field in regular_required:
                        if field not in place:
                            issues.append(f"Item {idx}, Place {pidx}: Missing '{field}'")

                    # Validate bounding_box_100km
                    if "bounding_box_100km" in place:
                        bbox = place["bounding_box_100km"]
                        bbox_required = ["north", "south", "east", "west"]
                        for field in bbox_required:
                            if field not in bbox:
                                issues.append(
                                    f"Item {idx}, Place {pidx}: bounding_box missing '{field}'"
                                )

    return issues


# Find all place JSON files
output_dir = Path("output")
place_files = list(output_dir.rglob("*-places.json"))

print(f"Found {len(place_files)} place files\n")

all_valid = True
for file in sorted(place_files):
    try:
        with open(file) as f:
            data = json.load(f)

        issues = validate_place_structure(data, file.name)

        if issues:
            all_valid = False
            print(f"❌ {file.relative_to(output_dir)}")
            for issue in issues[:5]:  # Show first 5 issues
                print(f"   - {issue}")
            if len(issues) > 5:
                print(f"   ... and {len(issues) - 5} more issues")
        else:
            print(f"✅ {file.relative_to(output_dir)}")

    except json.JSONDecodeError as e:
        all_valid = False
        print(f"❌ {file.relative_to(output_dir)} - Invalid JSON: {e}")
    except Exception as e:
        all_valid = False
        print(f"❌ {file.relative_to(output_dir)} - Error: {e}")

print(f"\n{'✅ All files valid!' if all_valid else '❌ Some files have issues'}")
