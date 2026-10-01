#!/bin/bash
set -e

# Usage: entrypoint.sh <s3-input-path> <s3-output-prefix> [chandra-args...]
# Example: entrypoint.sh s3://bucket/source/book.pdf s3://bucket/ocr-output/book/chunk-000/ --page-range 1-50

INPUT_S3="$1"
shift
OUTPUT_S3="$1"
shift

LOCAL_INPUT="/tmp/input.pdf"
LOCAL_OUTPUT="/tmp/output"

echo "Chandra OCR Job"
echo "  Input:  $INPUT_S3"
echo "  Output: $OUTPUT_S3"
echo "  Args:   $@"

# Validate page ranges (catch backwards ranges like 538-536)
for arg in "$@"; do
    if echo "$arg" | grep -qP '^\d+-\d+$'; then
        START=$(echo "$arg" | cut -d- -f1)
        END=$(echo "$arg" | cut -d- -f2)
        if [ "$START" -gt "$END" ]; then
            echo "ERROR: Invalid page range $arg (start > end)"
            exit 1
        fi
    fi
done

# Download PDF from S3
echo "Downloading input..."
aws s3 cp "$INPUT_S3" "$LOCAL_INPUT"

# Run chandra under the progress-watchdog. The watchdog fails the job on
# LACK OF PROGRESS (no page completed within OCR_NO_PROGRESS_SECS, default
# 900s), not on wall-clock time — so a slow-but-healthy dense scanned chunk is
# never killed mid-run, while a true hang is caught fast. The AWS Batch
# AttemptDurationSeconds is only a loose absolute backstop. See
# scripts/ocr_watchdog.py and docs/current/OCR_OPERATIONS.md.
echo "Running OCR (progress-watchdog: no-progress limit ${OCR_NO_PROGRESS_SECS:-900}s)..."
WATCHDOG="$(dirname "$0")/ocr_watchdog.py"
# --paginate_output inserts per-page separators in the merged markdown, which
# the post-OCR table-recovery router (src/ingestion/chunk_pages.py) parses to
# map a flattened table back to its physical PDF page. See OCR_OPERATIONS.md.
# Capture the watchdog exit code explicitly (temporarily disable -e) so a
# watchdog failure — notably EXIT_OOM(76), which the watchdog raises even when
# Chandra swallows a CUDA OOM and exits 0 — is PRESERVED as the container exit
# code. We must NOT upload partial/empty output and must NOT exit 0 on failure,
# or the job looks successful with no data (the B406/B400 silent-success bug).
set +e
if [ -f "$WATCHDOG" ]; then
    python3 "$WATCHDOG" -- chandra --method hf --paginate_output "$@" "$LOCAL_INPUT" "$LOCAL_OUTPUT"
    OCR_RC=$?
else
    echo "WARN: ocr_watchdog.py not found — running chandra without watchdog"
    chandra --method hf --paginate_output "$@" "$LOCAL_INPUT" "$LOCAL_OUTPUT"
    OCR_RC=$?
fi
set -e

if [ "$OCR_RC" -ne 0 ]; then
    echo "OCR failed (exit $OCR_RC) — NOT uploading partial output; failing the job."
    rm -rf "$LOCAL_INPUT" "$LOCAL_OUTPUT"
    exit "$OCR_RC"
fi

# Upload results to S3
echo "Uploading results..."
aws s3 sync "$LOCAL_OUTPUT/" "$OUTPUT_S3"

# Clean up local files (free disk for next container on same instance)
rm -rf "$LOCAL_INPUT" "$LOCAL_OUTPUT"

echo "Done."
