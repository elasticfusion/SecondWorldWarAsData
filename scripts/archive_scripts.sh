#!/bin/bash
# Archive temporary and obsolete scripts

set -e

SCRIPTS_DIR="/Users/dchristian/projects/SecondWorkldWarasData/scripts"
ARCHIVE_DIR="$SCRIPTS_DIR/archive"

echo "Creating archive directories..."
mkdir -p "$ARCHIVE_DIR/qa"
mkdir -p "$ARCHIVE_DIR/testing"
mkdir -p "$ARCHIVE_DIR/migration"
mkdir -p "$ARCHIVE_DIR/obsolete"

echo "Archiving QA scripts..."
mv "$SCRIPTS_DIR/qa_concurrent.py" "$ARCHIVE_DIR/qa/" 2>/dev/null || true
mv "$SCRIPTS_DIR/qa_logistics.py" "$ARCHIVE_DIR/qa/" 2>/dev/null || true
mv "$SCRIPTS_DIR/check_black.py" "$ARCHIVE_DIR/qa/" 2>/dev/null || true
mv "$SCRIPTS_DIR/format_files.py" "$ARCHIVE_DIR/qa/" 2>/dev/null || true

echo "Archiving test scripts..."
mv "$SCRIPTS_DIR/test_grok_api.py" "$ARCHIVE_DIR/testing/" 2>/dev/null || true
mv "$SCRIPTS_DIR/test_place_extraction.py" "$ARCHIVE_DIR/testing/" 2>/dev/null || true
mv "$SCRIPTS_DIR/test_grok_search.sh" "$ARCHIVE_DIR/testing/" 2>/dev/null || true
mv "$SCRIPTS_DIR/test_blacklist_comments.sh" "$ARCHIVE_DIR/testing/" 2>/dev/null || true
mv "$SCRIPTS_DIR/qa_check_tests.sh" "$ARCHIVE_DIR/testing/" 2>/dev/null || true
mv "$SCRIPTS_DIR/run_tests.sh" "$ARCHIVE_DIR/testing/" 2>/dev/null || true

echo "Archiving migration scripts..."
mv "$SCRIPTS_DIR/migrate_people_schema.py" "$ARCHIVE_DIR/migration/" 2>/dev/null || true
mv "$SCRIPTS_DIR/migrate_place_schema.py" "$ARCHIVE_DIR/migration/" 2>/dev/null || true
mv "$SCRIPTS_DIR/verify_requests_migration.py" "$ARCHIVE_DIR/migration/" 2>/dev/null || true
mv "$SCRIPTS_DIR/fix_place_map_urls.py" "$ARCHIVE_DIR/migration/" 2>/dev/null || true
mv "$SCRIPTS_DIR/deduplicate_ranks.py" "$ARCHIVE_DIR/migration/" 2>/dev/null || true

echo "Archiving obsolete scripts..."
mv "$SCRIPTS_DIR/merge_duplicate_groups.py" "$ARCHIVE_DIR/obsolete/" 2>/dev/null || true
mv "$SCRIPTS_DIR/verify_phase2_setup.py" "$ARCHIVE_DIR/obsolete/" 2>/dev/null || true
mv "$SCRIPTS_DIR/cleanup_people.sh" "$ARCHIVE_DIR/obsolete/" 2>/dev/null || true
mv "$SCRIPTS_DIR/enrich_equipment.py" "$ARCHIVE_DIR/obsolete/" 2>/dev/null || true
mv "$SCRIPTS_DIR/verify_and_import.py" "$ARCHIVE_DIR/obsolete/" 2>/dev/null || true

echo "✅ Archive complete!"
echo ""
echo "Active scripts remaining in scripts/:"
ls -1 "$SCRIPTS_DIR"/*.py "$SCRIPTS_DIR"/*.sh 2>/dev/null | wc -l
echo ""
echo "Archived scripts:"
echo "  QA: $(ls -1 $ARCHIVE_DIR/qa/ 2>/dev/null | wc -l)"
echo "  Testing: $(ls -1 $ARCHIVE_DIR/testing/ 2>/dev/null | wc -l)"
echo "  Migration: $(ls -1 $ARCHIVE_DIR/migration/ 2>/dev/null | wc -l)"
echo "  Obsolete: $(ls -1 $ARCHIVE_DIR/obsolete/ 2>/dev/null | wc -l)"
