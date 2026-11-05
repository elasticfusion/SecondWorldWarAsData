#!/usr/bin/env python3
"""Scan the ``output/`` corpus for JSON files that won't parse or load.

The relational loader (``src/loader/``) skips unreadable/corrupt files so one
bad record can't abort a load — but a skipped file is silently absent from a
dataset you cite from. This script surfaces those files directly so they can be
repaired, and doubles as a post-ingestion integrity gate.

It reports three classes of problem:

* **empty**       — file is zero bytes or whitespace only.
* **unparseable** — ``json.loads`` fails (truncated write, stray bytes, etc.).
* **not_object**  — valid JSON but not a top-level object, so entity readers
                    (which expect a dict) drop it.

Common corruption seen in practice (all recoverable): stray bytes prepended
before the opening ``{`` (botched overwrite), or a truncated tail where a
trailing comma was written but the closing ``}`` never was (interrupted write).

Known operational/non-entity files (``review_queue.json``, ``index.json``,
report/dedup sidecars, dotfiles) are skipped using the same ``_SKIP_FILES`` set
the loader honors, so this only flags files that are meant to be entity records.

Usage:
    python scripts/check_output_integrity.py [OUTPUT_DIR]

Exit codes:
    0 — every JSON file parses as a JSON object (corpus clean)
    1 — one or more problems found (details printed)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Import after the sys.path insert so the script runs standalone; the loader's
# skip set is reused rather than duplicated. pylint: disable=wrong-import-position
from src.loader import transform as _t  # noqa: E402

_SKIP_FILES = _t._SKIP_FILES  # noqa: E402

DEFAULT_OUTPUT = PROJECT_ROOT / "output"


def scan(output_dir: Path) -> Tuple[int, List[Tuple[str, str, str]]]:
    """Scan ``output_dir`` recursively.

    Returns ``(total_scanned, problems)`` where each problem is a tuple of
    ``(kind, path, detail)`` with kind in {empty, unparseable, not_object}.
    """
    total = 0
    problems: List[Tuple[str, str, str]] = []
    for path in sorted(output_dir.rglob("*.json")):
        if path.name in _SKIP_FILES:
            continue  # known operational/non-entity files (match the loader)
        total += 1
        rel = str(path.relative_to(output_dir.parent))
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            problems.append(("unparseable", rel, f"read error: {exc}"))
            continue
        if not raw.strip():
            problems.append(("empty", rel, "0 bytes / whitespace only"))
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            problems.append(("unparseable", rel, str(exc)))
            continue
        if not isinstance(data, dict):
            problems.append(("not_object", rel, f"top-level {type(data).__name__}"))
    return total, problems


def main(argv: List[str]) -> int:
    """Scan the corpus and print a report; return a process exit code."""
    output_dir = Path(argv[1]) if len(argv) > 1 else DEFAULT_OUTPUT
    if not output_dir.is_dir():
        print(f"error: {output_dir} is not a directory", file=sys.stderr)
        return 2

    total, problems = scan(output_dir)
    print(f"Scanned {total} JSON file(s) under {output_dir}/")

    if not problems:
        print("OK: all files parse as JSON objects — corpus clean.")
        return 0

    by_kind: dict[str, int] = {}
    for kind, _, _ in problems:
        by_kind[kind] = by_kind.get(kind, 0) + 1
    print(f"FOUND {len(problems)} problem file(s): {by_kind}")
    for kind, rel, detail in problems:
        print(f"  [{kind}] {rel}")
        print(f"      -> {detail}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
