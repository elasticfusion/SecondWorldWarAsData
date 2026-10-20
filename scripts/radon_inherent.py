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
    """sha256 of the AST-unparsed body of function ``symbol`` in ``file``. ``symbol`` may be a bare
    name (matched only at MODULE top level) or a dotted ``Class.method`` / ``Outer.inner`` path
    (resolved by walking the named owners). Returns None if not found, AMBIGUOUS (>1 match — never
    silently pick one), or unparseable. AST-based so comments + formatting don't churn the hash.
    """
    try:
        tree = ast.parse(pathlib.Path(file).read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None

    _FUNC = (ast.FunctionDef, ast.AsyncFunctionDef)
    parts = symbol.split(".")

    # Walk the dotted owner path down to the container of the final name, then match the final name
    # among that container's DIRECT children only (no recursion -> no cross-scope collision). A bare
    # name matches module-level defs only.
    containers: list = [tree]
    for owner in parts[:-1]:
        nxt = [
            n
            for c in containers
            for n in getattr(c, "body", [])
            if isinstance(n, (ast.ClassDef, *_FUNC)) and n.name == owner
        ]
        if len(nxt) != 1:
            return None  # ambiguous/missing owner
        containers = nxt

    target = parts[-1]
    found = [
        n
        for c in containers
        for n in getattr(c, "body", [])
        if isinstance(n, _FUNC) and n.name == target
    ]
    if len(found) != 1:
        return None  # not found, or AMBIGUOUS (same-name collision) -> fail closed
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
