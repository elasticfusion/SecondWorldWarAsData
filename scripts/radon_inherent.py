#!/usr/bin/env python3
"""Content-hash INHERENT allowlist for the radon gate.

Policy (steer-343779fe): a block legitimately judged INHERENTLY complex is accepted debt and
SUPPRESSED from the gate's high-C "to EVALUATE" list — BUT only while its code is unchanged. If the
function body is modified, its content hash changes, it falls off the allowlist, and it RESURFACES
for re-evaluation (the edit may have made it reducible, or pushed it to D).

Allowlist file: scripts/.radon_inherent — one entry per line:  <file>::<symbol>  <sha256>  # reason
The hash is over the function's AST-unparsed body (so comment/formatting/whitespace churn does NOT
trigger a resurface — only a real logic edit does).

Usage:
  python scripts/radon_inherent.py hash <file> <symbol>     # print the current body hash
  python scripts/radon_inherent.py suppressed <file> <symbol>  # exit 0 if listed+hash matches
Both are tiny; the gate shells out per high-C block.
"""

from __future__ import annotations

import ast
import hashlib
import pathlib
import sys

_ALLOWLIST = pathlib.Path(__file__).with_name(".radon_inherent")


def _body_hash(file: str, symbol: str) -> str | None:
    """sha256 of the AST-unparsed body of top-level (or one-level-nested) function ``symbol`` in
    ``file``. None if not found / unparseable. AST-based so comments + formatting don't churn it.
    """
    try:
        tree = ast.parse(pathlib.Path(file).read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None
    target = symbol.split(".")[-1]  # accept Class.method or bare name
    found: list[ast.AST] = []

    class _V(ast.NodeVisitor):
        def visit_FunctionDef(self, node):  # noqa: N802
            if node.name == target:
                found.append(node)
            self.generic_visit(node)

        visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815

    _V().visit(tree)
    if not found:
        return None
    # Unparse the body only (ignore the signature/decorators/docstring-position noise is fine —
    # the body carries the logic that drives complexity).
    try:
        body_src = "\n".join(ast.unparse(n) for n in found[0].body)
    except Exception:  # noqa: BLE001
        return None
    return hashlib.sha256(body_src.encode("utf-8")).hexdigest()


def _load_allowlist() -> dict[str, str]:
    out: dict[str, str] = {}
    if not _ALLOWLIST.exists():
        return out
    for line in _ALLOWLIST.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2:
            out[parts[0]] = parts[1]
    return out


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(__doc__)
        return 2
    cmd, file, symbol = argv[1], argv[2], argv[3]
    key = f"{file}::{symbol}"
    if cmd == "hash":
        h = _body_hash(file, symbol)
        if h is None:
            return 1
        print(h)
        return 0
    if cmd == "suppressed":
        listed = _load_allowlist().get(key)
        cur = _body_hash(file, symbol)
        # Suppressed ONLY if listed AND the current body hash matches the recorded one.
        return 0 if (listed is not None and cur == listed) else 1
    print(f"unknown command: {cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
