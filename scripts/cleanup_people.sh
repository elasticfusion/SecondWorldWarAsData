#!/bin/bash
# Clean up old people files and cache before re-running extraction
set -euo pipefail

echo "Cleaning up old people files..."
find output -name "*-people.json" -type f -delete
rm -rf output/*/people-central.json
rm -rf output/*/people-consolidated.json
rm -rf output/people/*.json
echo "✓ Deleted old people files"

echo "Cleaning up people cache..."
rm -rf cache/api/people/*
echo "✓ Cleared people cache"

echo ""
echo "New structure will be:"
echo "  output/people/"
echo "    ├── index.json (name → filename lookup)"
echo "    ├── Dwight_D_Eisenhower_01ABC123.json"
echo "    ├── George_S_Patton_01DEF456.json"
echo "    └── duplicate_report.json (potential duplicates)"
echo ""
echo "Ready to run: python phase2_extract.py"
