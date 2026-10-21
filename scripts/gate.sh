#!/bin/bash
# Full quality gate — mirrors CI (.github/workflows/lint.yml + tests.yml) EXACTLY.
# Run this as a matter of course before every commit. Runs ALL linters over the
# correct scopes (src/ AND lambda_handlers/), then the test suite.
#
#   bash scripts/gate.sh            # full gate
#   bash scripts/gate.sh --no-tests # linters only (faster)
#
# Companion (NON-deterministic, opt-in): scripts/ai_review.sh runs the independent
# code-review + security sub-agents on the diff. Run it on significant changes / in CI on PRs;
# it is intentionally NOT part of this gate (an LLM check can't be a reliable commit blocker).
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
# Ruff: high-value bug-pattern rules (flake8-bugbear 'B') only — e.g. raise-without-from,
# loop-var-closure, mutable-default. Deliberately NOT the logging/style groups (the project uses
# f-string logging by design; pylint W1203 is disabled for the same reason), so this adds real
# bug detection without 100s of style-noise findings.
run "ruff(B)"  python -m ruff check src/ lambda_handlers/ --select B --quiet
# shellcheck disable=SC2086
# C+ is the GATE THRESHOLD: any C-or-worse block HARD-FAILS unless it is INHERENT-allowlisted
# (scripts/.radon_inherent, body-hash unchanged). The existing C+ backlog was grandfathered via
# `radon_inherent.py seed`, so only a NEW or MODIFIED C+ block fails. D+ is NEVER suppressible.
run_radon() {
  local out
  # Fail-closed if radon is absent: otherwise `python -m radon` errors, the output matches no case
  # arm, and the gate would silently report green (false PASS) — unacceptable for a hard gate.
  if ! python -m radon --version >/dev/null 2>&1; then
    echo "  ✗ radon: not installed (python -m radon unavailable) — cannot evaluate complexity gate"
    fail=1
    return
  fi
  # --show-complexity is REQUIRED: without it radon emits "- D" (no trailing space) and the grade
  # match below never fires. With it, lines read "- C (14)" / "- D (21)".
  out="$(python -m radon cc $SCOPE --min C --show-complexity 2>&1)"

  local cur_file sym grade
  local d_show c_show n_d n_cfail n_supp
  cur_file=""
  d_show=""     # D+ offenders (always fail)
  c_show=""     # non-suppressed C offenders (now fail)
  n_d=0; n_cfail=0; n_supp=0

  # Walk the output tracking the FILE header (radon prints it once above each file's blocks; block
  # lines carry only the symbol). Partition every C+ block into: D+ (always fail), non-suppressed C
  # (fail), or INHERENT-suppressed C (green). A modified allowlisted block's body hash no longer
  # matches -> it falls out of 'suppressed' and RESURFACES as a non-suppressed C failure.
  while IFS= read -r line; do
    case "$line" in
      [A-Za-z]*) cur_file="$line" ;;  # file header (no leading space)
      *" - "[C-F]" ("*)
        grade="$(echo "$line" | sed -E 's/.* - ([C-F]) \([0-9]+\)$/\1/')"
        if [ "$grade" != "C" ]; then
          d_show="${d_show}${line}"$'\n'
          n_d=$((n_d + 1))
          continue
        fi
        sym="$(echo "$line" | sed -E 's/^\s*[A-Z] +[0-9]+:[0-9]+ +([A-Za-z_][A-Za-z0-9_.]*) +- C \([0-9]+\)/\1/')"
        if python scripts/radon_inherent.py suppressed "$cur_file" "$sym" 2>/dev/null; then
          n_supp=$((n_supp + 1))
        else
          c_show="${c_show}${line}"$'\n'
          n_cfail=$((n_cfail + 1))
        fi
        ;;
    esac
  done <<< "$out"

  # D+ : always HARD FAIL (never suppressible).
  if [ -n "$d_show" ]; then
    echo "  ✗ radon: ${n_d} D-or-worse block(s) — MUST refactor below C:"
    printf '%s' "$d_show" | sed 's/^/        /'
    fail=1
  fi
  # Non-suppressed C : NEW or MODIFIED C+ -> HARD FAIL. Reduce complexity below C, or (if genuinely
  # inherent) grandfather it: `python scripts/radon_inherent.py seed` then add a real reason.
  if [ -n "$c_show" ]; then
    echo "  ✗ radon: ${n_cfail} non-allowlisted C block(s) — reduce below C, or allowlist in scripts/.radon_inherent:"
    printf '%s' "$c_show" | sed 's/^/        /'
    fail=1
  fi
  if [ "$n_supp" -gt 0 ]; then
    echo "  · radon: ${n_supp} INHERENT C+ block(s) suppressed (unchanged; see scripts/.radon_inherent)"
  fi
  if [ -z "$d_show" ] && [ -z "$c_show" ]; then
    if [ "$n_supp" -gt 0 ]; then
      echo "  ✓ radon (0 new/modified C+; ${n_supp} grandfathered)"
    else
      echo "  ✓ radon (no C+ blocks)"
    fi
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
run "pip-audit" bash -c 'pip-audit -r requirements.txt --ignore-vuln PYSEC-2026-2447 && pip-audit -r requirements-lambda.txt --ignore-vuln PYSEC-2026-2447 && pip-audit --ignore-vuln PYSEC-2026-2447'
# The final `pip-audit` (no -r) audits the FULL installed environment — it catches a vulnerable
# TRANSITIVE dependency (e.g. certifi, pulled in by requests) that the per-file audits would miss
# when it isn't explicitly declared.
#
# IGNORED VULN (exactly one, justified — NOT a blanket suppression):
#   PYSEC-2026-2447 / CVE-2025-69872 — diskcache <=5.6.3 unsafe pickle deserialization: an attacker
#   with WRITE ACCESS TO THE CACHE DIRECTORY can achieve RCE when the app reads the cache.
#   - NO FIX EXISTS: 5.6.3 is the latest diskcache release (advisory last_affected=5.6.3, no fixed
#     version). There is nothing to upgrade to.
#   - LOW EXPOSURE here: diskcache backs our OWN Grok API-response cache (DiskCacheBackend) in a
#     pipeline-controlled, non-untrusted-writable dir; the RCE vector needs a local writer who would
#     already have code execution.
#   REMOVE this --ignore (and bump diskcache) the moment a fixed release ships. Any OTHER vuln fails
#   the gate — this is the only accepted exception.
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
