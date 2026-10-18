#!/bin/bash
# AI review companion to scripts/gate.sh — runs the INDEPENDENT code-review + security
# sub-agents over the current change set. DELIBERATELY SEPARATE from gate.sh:
#
#   gate.sh      = fast, DETERMINISTIC pre-commit gate (black/pylint/mypy/bandit/radon/pytest).
#                  Safe to block every commit on.
#   ai_review.sh = heavyweight, NON-DETERMINISTIC LLM review (code-review + security agents).
#                  Needs model/network access, costs tokens, takes minutes. Run it on
#                  SIGNIFICANT changes (and in CI on PRs) — NOT on every local commit, because a
#                  non-deterministic check cannot be a reliable commit blocker.
#
# The code-review and security reviews MUST run CONCURRENTLY as two INDEPENDENT agents (no
# dependency between them, no shared context) — they review the same diff from different angles in
# parallel, and BOTH must PASS. Running them concurrently keeps wall-clock cost to one review, not
# two serial ones, and keeps the two verdicts independent.
#
# Usage:
#   bash scripts/ai_review.sh            # review the diff vs origin/main
#   bash scripts/ai_review.sh <base>     # review the diff vs <base>
#
# This script does NOT itself call the model — it assembles the review scope and emits the
# instruction the agent runner (or a human driving the CLI) executes. It exits non-zero if there
# is nothing to review, so CI can skip cleanly.

set -uo pipefail
cd "$(dirname "$0")/.."

BASE="${1:-origin/main}"

# Determine the changed files + a bounded diff for the reviewers.
CHANGED="$(git diff --name-only "$BASE"...HEAD 2>/dev/null)"
if [ -z "$CHANGED" ]; then
  echo "ai_review: no changes vs $BASE — nothing to review."
  exit 1
fi

echo "=== AI review scope (vs $BASE) ==="
echo "$CHANGED" | sed 's/^/  /'
echo
echo "Run TWO independent sub-agents CONCURRENTLY (in parallel — NO dependency between them, no"
echo "shared context) over the diff above:"
echo "  1. code-reviewer  — correctness, design, test coverage, regressions."
echo "  2. security       — injection/traversal/SSRF/ReDoS/secrets/fail-safety."
echo
echo "Both run at once and BOTH must PASS. Each reports file:line + severity + PASS/FAIL."
echo "Address CRITICAL/WARNING before merge."
echo
echo "Diff is written to /tmp/ai_review.diff for the reviewers to read:"
git diff "$BASE"...HEAD > /tmp/ai_review.diff 2>/dev/null
wc -l /tmp/ai_review.diff | sed 's/^/  /'
