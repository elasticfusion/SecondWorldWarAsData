#!/usr/bin/env python3
"""Load and apply CSV search-and-replace rules from config/csv_search_replacements.yaml."""

from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "csv_search_replacements.yaml"


@dataclass(frozen=True)
class ReplacementRule:
    pattern: str
    replacement: str
    description: str = ""
    compiled: re.Pattern[str] | None = None

    def __post_init__(self) -> None:
        if self.compiled is None and not self.pattern.startswith("__python__:"):
            object.__setattr__(self, "compiled", re.compile(self.pattern))


@dataclass(frozen=True)
class RuleSet:
    name: str
    description: str
    columns: tuple[str, ...]
    rules: tuple[ReplacementRule, ...]
    repeat: int = 1


def load_rule_sets(config_path: Path | None = None) -> list[RuleSet]:
    path = config_path or DEFAULT_CONFIG
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rule_sets: list[RuleSet] = []
    for name, spec in (data.get("rule_sets") or {}).items():
        columns = tuple(spec.get("columns") or ())
        rules = tuple(
            ReplacementRule(
                pattern=rule["pattern"],
                replacement=rule["replacement"],
                description=rule.get("description", ""),
            )
            for rule in spec.get("rules") or []
        )
        rule_sets.append(
            RuleSet(
                name=name,
                description=spec.get("description", ""),
                columns=columns,
                rules=rules,
                repeat=max(1, int(spec.get("repeat") or 1)),
            )
        )
    return rule_sets


def strip_stray_division_close_paren(text: str) -> tuple[str, int]:
    """Replace stray Division) with Division while keeping balanced (N Division)."""
    needle = "Division)"
    div_only = "Division"
    out: list[str] = []
    i = 0
    replacements = 0
    while True:
        idx = text.find(needle, i)
        if idx == -1:
            out.append(text[i:])
            break
        prefix = text[: idx + len(div_only)]
        if prefix.count("(") > prefix.count(")"):
            out.append(text[i : idx + len(needle)])
        else:
            out.append(text[i : idx + len(div_only)])
            replacements += 1
        i = idx + len(needle)
    return "".join(out), replacements


PYTHON_REPLACERS: dict[str, object] = {
    "strip_stray_division_close_paren": strip_stray_division_close_paren,
}


def apply_rules_to_text(
    text: str,
    rules: list[ReplacementRule],
    *,
    repeat: int = 1,
) -> tuple[str, int]:
    updated = text
    total = 0
    for _ in range(max(1, repeat)):
        previous = updated
        for rule in rules:
            if rule.pattern.startswith("__python__:"):
                handler_name = rule.pattern.removeprefix("__python__:")
                handler = PYTHON_REPLACERS[handler_name]
                updated, count = handler(updated)  # type: ignore[operator]
                total += count
                continue
            updated, count = rule.compiled.subn(rule.replacement, updated)
            total += count
        if updated == previous:
            break
    return updated, total


def rules_for_columns(
    rule_sets: list[RuleSet],
    columns: set[str] | None = None,
    rule_set_names: set[str] | None = None,
) -> dict[str, list[tuple[list[ReplacementRule], int]]]:
    """Map column name -> ordered (rules, repeat) batches for that column."""
    by_column: dict[str, list[tuple[list[ReplacementRule], int]]] = {}
    for rule_set in rule_sets:
        if rule_set_names and rule_set.name not in rule_set_names:
            continue
        for column in rule_set.columns:
            if columns and column not in columns:
                continue
            by_column.setdefault(column, []).append(
                (list(rule_set.rules), rule_set.repeat)
            )
    return by_column


def apply_to_csv_file(
    path: Path,
    rules_by_column: dict[str, list[ReplacementRule]],
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            return {}
        fieldnames = reader.fieldnames
        rows = list(reader)

    applicable = {
        column: batches
        for column, batches in rules_by_column.items()
        if column in fieldnames and batches
    }
    if not applicable:
        return {}

    stats: dict[str, int] = {}
    changed_rows = 0
    for row in rows:
        row_changed = False
        for column, batches in applicable.items():
            value = row.get(column, "")
            if not value:
                continue
            new_value = value
            count = 0
            for rules, repeat in batches:
                new_value, batch_count = apply_rules_to_text(
                    new_value, rules, repeat=repeat
                )
                count += batch_count
            if new_value != value:
                row_changed = True
                if count:
                    stats[column] = stats.get(column, 0) + count
                if not dry_run:
                    row[column] = new_value
        if row_changed:
            changed_rows += 1

    if changed_rows and not dry_run:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

    stats["_rows_changed"] = changed_rows
    return stats


def iter_csv_files(root: Path, glob_pattern: str) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob(glob_pattern)):
        if ".venv" in path.parts:
            continue
        if path.is_file():
            files.append(path)
    return files


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply configured CSV search-and-replace rules."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="Path to replacements YAML (default: config/csv_search_replacements.yaml)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=PROJECT_ROOT,
        help="Root directory to search for CSV files",
    )
    parser.add_argument(
        "--glob",
        default="*.csv",
        help="Glob pattern under --root (default: *.csv)",
    )
    parser.add_argument(
        "--column",
        action="append",
        dest="columns",
        help="Limit to specific column(s); repeatable",
    )
    parser.add_argument(
        "--rule-set",
        action="append",
        dest="rule_sets",
        help="Limit to specific rule set name(s); repeatable",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report changes without writing files",
    )
    args = parser.parse_args()

    rule_sets = load_rule_sets(args.config)
    column_filter = set(args.columns) if args.columns else None
    rule_set_filter = set(args.rule_sets) if args.rule_sets else None
    rules_by_column = rules_for_columns(rule_sets, column_filter, rule_set_filter)

    if not rules_by_column:
        raise SystemExit("No rules matched the requested filters.")

    total_rows = 0
    total_replacements = 0
    touched_files = 0
    for path in iter_csv_files(args.root, args.glob):
        stats = apply_to_csv_file(path, rules_by_column, dry_run=args.dry_run)
        if not stats.get("_rows_changed"):
            continue
        touched_files += 1
        rows_changed = stats.pop("_rows_changed", 0)
        total_rows += rows_changed
        file_replacements = sum(stats.values())
        total_replacements += file_replacements
        suffix = " (dry run)" if args.dry_run else ""
        print(
            f"{path.relative_to(args.root)}: {rows_changed} row(s), "
            f"{file_replacements} replacement(s){suffix}"
        )
        for column, count in sorted(stats.items()):
            print(f"  {column}: {count}")

    action = "Would update" if args.dry_run else "Updated"
    print(
        f"\n{action} {total_replacements} replacement(s) in {total_rows} row(s) "
        f"across {touched_files} file(s)."
    )


if __name__ == "__main__":
    main()