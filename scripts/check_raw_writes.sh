#!/bin/bash
# Raw-write bypass gate (data-quality block b-2).
#
# The central write guard (src/utils/file_lock.write_json_with_lock / guard_parsed_content /
# Storage.write_json) is the ONLY sanctioned way to persist an entity/parsed record — it enforces
# schema validation, PK repair, semantic flags, and the stats catcher. A raw `json.dump(...)` or
# `.write_text(...)` to an entity output path SILENTLY BYPASSES all of that.
#
# Statically deciding "is THIS raw write an entity record?" is unreliable, so this gate is a
# REGRESSION guard: it counts raw-write call sites in the guarded code and fails if the count rises
# above the committed baseline. A contributor who adds a raw write must either (a) route it through
# the central guard, or (b) if it is a legitimate NON-record sink (index.json / .processed_events /
# metrics / cache), update the baseline in the same commit with a one-line justification. Either way
# the bypass surfaces in review instead of silently reopening the hole the guard closed.
#
# Baseline currently reflects a corpus where ZERO raw writes target an entity-record path (verified
# at creation) — so any increase is the signal to check.
#
# KNOWN BLIND SPOTS (this is a tripwire, not a proof): it matches only the `json.dump(` and
# `.write_text(` idioms. It does NOT catch equivalent raw sinks — `f.write(json.dumps(rec))`,
# `open(path,"w").write(...)`, `Path.write_bytes(json.dumps(rec).encode())`. Those forms are not
# counted; the gate narrows the easy/common bypass, and the corpus-audit job (b-3) + S3 Lambda
# backstop (b-1) are the defence-in-depth that catch an invalid record however it was written.

set -uo pipefail
cd "$(dirname "$0")/.."

BASELINE_FILE="scripts/.raw_writes_baseline"
# Guarded code only: the pipeline writers (phase scripts + the ECS orchestrator + extraction/
# enrichment under src + the Lambda handlers). tests/ and one-off scripts/ are not pipeline writers.
SCOPE="src lambda_handlers phase1_parse.py phase2_extract.py phase3_enrich_data.py ecs_entrypoint.py"

# Count raw persistence call sites, EXCLUDING the sanctioned central writers themselves
# (file_lock.py owns the atomic json.dump; storage.py owns the backend writes). The exclusion is
# ANCHORED to the whole path field (`path:line:...`) so a look-alike filename cannot evade counting.
count="$(grep -rnE 'json\.dump\(|\.write_text\(' $SCOPE --include='*.py' 2>/dev/null \
  | grep -vE '/__pycache__/' \
  | grep -vE '^(src/utils/file_lock\.py|src/utils/storage\.py):' \
  | wc -l | tr -d ' ')"

baseline="$(cat "$BASELINE_FILE" 2>/dev/null || echo 0)"
# Fail CLOSED on a malformed baseline — a non-integer must never silently disable the gate.
case "$baseline" in
  '' | *[!0-9]*)
    echo "  ✗ raw-write gate: baseline '$baseline' is not a non-negative integer ($BASELINE_FILE)"
    exit 1
    ;;
esac

if [ "$count" -gt "$baseline" ]; then
  echo "  ✗ raw-write gate: $count raw json.dump/write_text sites > baseline $baseline"
  echo "      A new raw write was added. Route record persistence through the central guard"
  echo "      (write_json_with_lock / guard_parsed_content / Storage.write_json). If this is a"
  echo "      legitimate non-record sink, bump $BASELINE_FILE in this commit with justification."
  echo "      Current sites:"
  grep -rnE 'json\.dump\(|\.write_text\(' $SCOPE --include='*.py' 2>/dev/null \
    | grep -vE '/__pycache__/' \
    | grep -vE '^(src/utils/file_lock\.py|src/utils/storage\.py):' | sed 's/^/        /'
  exit 1
fi

if [ "$count" -lt "$baseline" ]; then
  echo "  ✓ raw-write gate: $count sites (below baseline $baseline — consider lowering the baseline)"
else
  echo "  ✓ raw-write gate: $count raw-write sites (== baseline)"
fi
exit 0
