#!/bin/bash
# Full quality gate — mirrors CI (.github/workflows/lint.yml + tests.yml) EXACTLY.
# Run this as a matter of course before every commit. Runs ALL linters over the
# correct scopes (src/ AND lambda_handlers/), then the test suite.
#
#   bash scripts/gate.sh            # full gate
#   bash scripts/gate.sh --no-tests # linters only (faster)
#
# Exits non-zero on the first failure. No partial gates.

set -uo pipefail
cd "$(dirname "$0")/.."

PYLINT_DISABLE="C0301,C0103,R0913,R0914,R0915,W0511,R0917,W0718,W0212,W1203,C0415"
SCOPE="src/ lambda_handlers/"          # code linted for lint/type/complexity/dead-code
BLACK_SCOPE="src/ tests/ scripts/ lambda_handlers/ *.py"
fail=0

run() {  # run <name> <cmd...>
  local name="$1"; shift
  if "$@" >/tmp/gate_out 2>&1; then
    echo "  ✓ $name"
  else
    echo "  ✗ $name"; tail -15 /tmp/gate_out | sed 's/^/      /'; fail=1
  fi
}

echo "=== Quality gate (CI-equivalent) ==="
# shellcheck disable=SC2086
run "black"    python -m black --check $BLACK_SCOPE
# shellcheck disable=SC2086
run "pylint"   python -m pylint --disable=$PYLINT_DISABLE --fail-under=5.0 $SCOPE
# shellcheck disable=SC2086
run "mypy"     python -m mypy $SCOPE --ignore-missing-imports --no-strict-optional
run "bandit"   python -m bandit -r src/ lambda_handlers/ -ll -q
# shellcheck disable=SC2086
run "radon"    python -m radon cc $SCOPE --min C --total-average
run "vulture"  python -m vulture src/ lambda_handlers/ .vulture_whitelist.py --min-confidence 80
run "cfn-lint" bash -c 'cfn-lint cloudformation/*.yaml'
run "pip-audit" bash -c 'pip-audit -r requirements.txt --ignore-vuln PYSEC-2026-2447 && pip-audit -r requirements-lambda.txt --ignore-vuln PYSEC-2026-2447'

if [ "${1:-}" != "--no-tests" ]; then
  echo "--- tests (CI-equivalent: example config) ---"
  cp config.yaml /tmp/gate_config.mine 2>/dev/null || true
  cp config.yaml.example config.yaml
  if python -m pytest tests/ -m "not slow and not requires_api" -q --tb=short >/tmp/gate_tests 2>&1; then
    grep -E "[0-9]+ passed" /tmp/gate_tests | tail -1 | sed 's/^/  ✓ pytest: /'
  else
    echo "  ✗ pytest"; grep -E "FAILED|ERROR" /tmp/gate_tests | head -10 | sed 's/^/      /'; fail=1
  fi
  cp /tmp/gate_config.mine config.yaml 2>/dev/null || true
fi

echo "==================================="
[ "$fail" = "0" ] && echo "GATE: PASS" || { echo "GATE: FAIL"; exit 1; }
