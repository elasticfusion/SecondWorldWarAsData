#!/usr/bin/env python3
"""
JSON File Comparison Tool

Compares two JSON files and displays structural and value differences.
Supports nested structures and provides clear, hierarchical output.

Usage:
    python json_compare.py file1.json file2.json

Required arguments:
    file1          Path to the first JSON file
    file2          Path to the second JSON file

Optional flags:
    --ignore-order    Ignore differences in array order (treat arrays as sets)
    --show-values     Show actual differing values (default: True)
"""

import argparse
import json
import sys
from typing import Any, Dict, List, Union, Set


def load_json_file(filepath: str) -> Any:
    """Load and parse a JSON file, handling common errors."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Error: File not found: {filepath}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in {filepath}: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error reading {filepath}: {e}", file=sys.stderr)
        sys.exit(1)


def compare_values(path: str, val1: Any, val2: Any, differences: List[str]) -> None:
    """Compare two values and record differences."""
    if type(val1) != type(val2):
        differences.append(f"{path} → type mismatch: {type(val1).__name__} vs {type(val2).__name__}")
    elif val1 != val2:
        differences.append(f"{path} → value changed: {repr(val1)} → {repr(val2)}")


def compare_dicts(d1: Dict, d2: Dict, path: str = "", ignore_order: bool = False) -> List[str]:
    """Recursively compare two dictionaries."""
    differences = []

    # Find keys present in one but not the other
    keys1 = set(d1.keys())
    keys2 = set(d2.keys())

    added = keys2 - keys1
    removed = keys1 - keys2
    common = keys1 & keys2

    for key in sorted(added):
        differences.append(f"+ {path}.{key} (added in second file)")

    for key in sorted(removed):
        differences.append(f"- {path}.{key} (removed from first file)")

    # Compare common keys recursively
    for key in sorted(common):
        new_path = f"{path}.{key}" if path else key
        v1 = d1[key]
        v2 = d2[key]

        if isinstance(v1, dict) and isinstance(v2, dict):
            differences.extend(compare_dicts(v1, v2, new_path, ignore_order))
        elif isinstance(v1, list) and isinstance(v2, list):
            differences.extend(compare_lists(v1, v2, new_path, ignore_order))
        else:
            compare_values(new_path, v1, v2, differences)

    return differences


def compare_lists(l1: List, l2: List, path: str, ignore_order: bool = False) -> List[str]:
    """Compare two lists, optionally ignoring order."""
    differences = []

    if ignore_order:
        # Treat as sets (order-insensitive)
        set1 = set(map(str, l1))  # str() for hashability of nested structures
        set2 = set(map(str, l2))

        only_in_1 = set1 - set2
        only_in_2 = set2 - set1

        if only_in_1:
            differences.append(f"{path} → elements only in first: {only_in_1}")
        if only_in_2:
            differences.append(f"{path} → elements only in second: {only_in_2}")
    else:
        # Order-sensitive comparison
        if len(l1) != len(l2):
            differences.append(f"{path} → length changed: {len(l1)} → {len(l2)}")

        min_len = min(len(l1), len(l2))
        for i in range(min_len):
            v1 = l1[i]
            v2 = l2[i]
            item_path = f"{path}[{i}]"

            if isinstance(v1, dict) and isinstance(v2, dict):
                differences.extend(compare_dicts(v1, v2, item_path, ignore_order))
            elif isinstance(v1, list) and isinstance(v2, list):
                differences.extend(compare_lists(v1, v2, item_path, ignore_order))
            else:
                compare_values(item_path, v1, v2, differences)

        # Extra elements
        if len(l1) > len(l2):
            differences.append(f"{path} → extra elements in first file at indices {len(l2)}..{len(l1)-1}")
        elif len(l2) > len(l1):
            differences.append(f"{path} → extra elements in second file at indices {len(l1)}..{len(l2)-1}")

    return differences


def main():
    parser = argparse.ArgumentParser(description="Compare two JSON files and show differences.")
    parser.add_argument("file1", help="Path to first JSON file")
    parser.add_argument("file2", help="Path to second JSON file")
    parser.add_argument("--ignore-order", action="store_true",
                        help="Treat arrays as sets (ignore element order)")
    parser.add_argument("--no-values", action="store_true",
                        help="Do not show actual differing values (more compact output)")

    args = parser.parse_args()

    data1 = load_json_file(args.file1)
    data2 = load_json_file(args.file2)

    if type(data1) != type(data2):
        print("Error: Top-level types differ — cannot compare")
        print(f"  First file:  {type(data1).__name__}")
        print(f"  Second file: {type(data2).__name__}")
        sys.exit(1)

    differences = []

    if isinstance(data1, dict) and isinstance(data2, dict):
        differences = compare_dicts(data1, data2, ignore_order=args.ignore_order)
    elif isinstance(data1, list) and isinstance(data2, list):
        differences = compare_lists(data1, data2, "root", ignore_order=args.ignore_order)
    else:
        # scalar top-level values
        if data1 != data2:
            differences.append(f"root → value changed: {repr(data1)} → {repr(data2)}")

    if not differences:
        print("The two JSON files are identical.")
    else:
        print(f"Found {len(differences)} difference(s):\n")
        for diff in differences:
            print(diff)

        if args.no_values:
            print("\n(Note: value differences hidden due to --no-values flag)")


if __name__ == "__main__":
    main()