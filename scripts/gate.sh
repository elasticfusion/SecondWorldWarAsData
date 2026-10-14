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
# Radon complexity: a C-grade block (moderate complexity) must BUBBLE UP for evaluation
# (listed, not silently tolerated); a D-or-worse block HARD-FAILS the gate.
run_radon() {
  local out
  out="$(python -m radon cc $SCOPE --min C 2>&1)"
  local dworse cblocks allcplus
  dworse="$(echo "$out" | grep -E ' - [D-F] ' || true)"
  cblocks="$(echo "$out" | grep -E ' - C ' || true)"
  # ALWAYS surface every C-and-above block for decision-making (C/D/E/F), in all cases —
  # even when a D+ triggers the hard failure (previously the C blocks were hidden then).
  allcplus="$(echo "$out" | grep -E ' - [C-F] ' || true)"
  if [ -n "$allcplus" ]; then
    echo "  radon C+ complexity blocks (review all):"
    echo "$allcplus" | sed 's/^/      /'
  fi
  if [ -n "$dworse" ]; then
    echo "  ✗ radon (D+ complexity — must refactor)"
    fail=1
  elif [ -n "$cblocks" ]; then
    echo "  ⚠ radon: C-grade block(s) to EVALUATE (not blocking)"
  else
    echo "  ✓ radon"
  fi
}
run_radon
run "vulture"  python -m vulture src/ lambda_handlers/ .vulture_whitelist.py --min-confidence 80
run "cfn-lint" bash -c 'cfn-lint cloudformation/*.yaml'
# JSON lint: strict-parse every PROJECT-OWNED *.json (stdlib json, no new dep). Excludes
# vendored/third-party trees (aws/ bundled CLI = ~1500 files, .venv, node_modules, dist,
# build, .git). A malformed JSON (schema fixture, agent config, prompt, test fixture) fails
# the gate instead of surfacing only at runtime. Prefers git-tracked files so untracked
# scratch JSON is ignored.
run "json-lint" python - <<'PYJSON'
import json, subprocess, sys
_EXCL = ("aws/", ".venv/", "node_modules/", "dist/", "build/", ".git/", "contentrepository/", "logs/")
try:
    files = subprocess.check_output(["git", "ls-files", "*.json"], text=True).split()
except Exception:
    import glob
    files = glob.glob("**/*.json", recursive=True)
bad = []
checked = 0
for f in files:
    if any(f.startswith(p) or ("/" + p) in f for p in _EXCL):
        continue
    checked += 1
    try:
        with open(f, encoding="utf-8") as fh:
            json.load(fh)
    except Exception as e:  # noqa: BLE001
        bad.append(f"{f}: {e}")
if bad:
    print("Malformed JSON:")
    for b in bad:
        print("  " + b)
    sys.exit(1)
print(f"json-lint: {checked} project JSON files parse OK")
PYJSON
run "pip-audit" bash -c 'pip-audit -r requirements.txt --ignore-vuln PYSEC-2026-2447 && pip-audit -r requirements-lambda.txt --ignore-vuln PYSEC-2026-2447'
# Data-quality bypass guard: no NEW raw json.dump/write_text that could skip the central write guard.
if bash scripts/check_raw_writes.sh >/tmp/gate_rawwrites 2>&1; then
  grep '✓' /tmp/gate_rawwrites | tail -1
else
  cat /tmp/gate_rawwrites; fail=1
fi

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
