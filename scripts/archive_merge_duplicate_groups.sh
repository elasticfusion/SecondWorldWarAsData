#!/bin/bash
# Archive merge_duplicate_groups.py (superseded by merge_related_groups.py)

SCRIPTS_DIR="/Users/dchristian/projects/SecondWorkldWarasData/scripts"
ARCHIVE_DIR="$SCRIPTS_DIR/archive/obsolete"

mkdir -p "$ARCHIVE_DIR"

if [ -f "$SCRIPTS_DIR/merge_duplicate_groups.py" ]; then
    mv "$SCRIPTS_DIR/merge_duplicate_groups.py" "$ARCHIVE_DIR/"
    echo "✓ Archived: merge_duplicate_groups.py → archive/obsolete/"
    echo "  Reason: Superseded by merge_related_groups.py (interactive, more flexible)"
else
    echo "⊘ File not found or already archived"
fi
