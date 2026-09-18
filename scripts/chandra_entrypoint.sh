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

# Run chandra
echo "Running OCR..."
chandra --method hf "$@" "$LOCAL_INPUT" "$LOCAL_OUTPUT"

# Upload results to S3
echo "Uploading results..."
aws s3 sync "$LOCAL_OUTPUT/" "$OUTPUT_S3"

# Clean up local files (free disk for next container on same instance)
rm -rf "$LOCAL_INPUT" "$LOCAL_OUTPUT"

echo "Done."
