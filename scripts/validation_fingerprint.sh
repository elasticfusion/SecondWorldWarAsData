#!/bin/bash
# Deterministic fingerprint of the validation primitive that the S3 backstop Lambda MUST run.
# Hashes the files that define the guard's behavior: the schemas + the guard + the stats catcher.
# update_lambdas.sh stamps this into the Lambda env (VALIDATION_FINGERPRINT); the deploy preflight
# (check_component_versions.sh) compares repo-HEAD vs the deployed value to catch a stale backstop.
#
# Output: a single sha256 hex over the sorted file list (stable across machines).
set -uo pipefail
cd "$(dirname "$0")/.."

# Sorted, content-only hash (file contents concatenated in a fixed path order).
{
  find src/schemas -name '*.py' -type f | sort
  echo src/utils/file_lock.py
  echo src/utils/validation_stats.py
} | while read -r f; do
  [ -f "$f" ] && sha256sum "$f"
done | sha256sum | awk '{print $1}'
