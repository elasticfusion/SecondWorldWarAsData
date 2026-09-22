#!/bin/bash
set -e

# PP-StructureV3 table-recovery worker entrypoint (GPU Batch container).
#
# Recovers a flattened 2-D task-org table (Chandra blind spot) from ONE page,
# using the page image + Chandra's markdown for that page as the routing input.
#
# Usage:
#   paddle_entrypoint.sh <s3-page-image> <s3-chandra-markdown> <s3-output-json>
# Example:
#   paddle_entrypoint.sh \
#     s3://bucket/ocr-output/stvith/p155.png \
#     s3://bucket/ocr-output/stvith/p155.md \
#     s3://bucket/ocr-output/stvith/p155.recovery.json

INPUT_IMAGE_S3="$1"
INPUT_MD_S3="$2"
OUTPUT_JSON_S3="$3"

if [ -z "$INPUT_IMAGE_S3" ] || [ -z "$INPUT_MD_S3" ] || [ -z "$OUTPUT_JSON_S3" ]; then
    echo "ERROR: usage: paddle_entrypoint.sh <s3-page-image> <s3-chandra-markdown> <s3-output-json>"
    exit 2
fi

LOCAL_IMAGE="/tmp/page_image"
LOCAL_MD="/tmp/page.md"
LOCAL_OUT="/tmp/recovery.json"

echo "PP-StructureV3 Table Recovery Job"
echo "  Page image: $INPUT_IMAGE_S3"
echo "  Markdown:   $INPUT_MD_S3"
echo "  Output:     $OUTPUT_JSON_S3"

# Preserve the image extension so PaddleOCR reads the correct format.
EXT="${INPUT_IMAGE_S3##*.}"
LOCAL_IMAGE="${LOCAL_IMAGE}.${EXT}"

echo "Downloading inputs..."
aws s3 cp "$INPUT_IMAGE_S3" "$LOCAL_IMAGE"
aws s3 cp "$INPUT_MD_S3" "$LOCAL_MD"

echo "Running recovery..."
python3 /app/scripts/paddle_recover_page.py "$LOCAL_IMAGE" "$LOCAL_MD" "$LOCAL_OUT"

echo "Uploading result..."
aws s3 cp "$LOCAL_OUT" "$OUTPUT_JSON_S3"

# Clean up local files (free disk for next container on the same instance).
rm -rf "$LOCAL_IMAGE" "$LOCAL_MD" "$LOCAL_OUT"

echo "Done."
