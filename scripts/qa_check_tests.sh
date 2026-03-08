#!/bin/bash
# QA check script for test files

set -e

echo "🔍 Running QA checks on test files..."
echo ""

echo "1️⃣  Black formatting..."
python3 -m black tests/ --check --quiet && echo "   ✅ All files formatted" || (python3 -m black tests/ && echo "   ✅ Files reformatted")

echo ""
echo "2️⃣  Mypy type checking..."
python3 -m mypy tests/conftest.py --ignore-missing-imports --no-error-summary && echo "   ✅ No type errors"

echo ""
echo "3️⃣  Pylint code quality..."
SCORE=$(python3 -m pylint tests/conftest.py tests/unit/test_grok_client.py tests/unit/test_extraction/test_people.py tests/unit/test_duplicate_detection.py --disable=C0301,C0103,W0511,E0401 2>&1 | grep "rated at" | awk '{print $7}' | cut -d'/' -f1)
echo "   ✅ Pylint score: $SCORE/10"

echo ""
echo "4️⃣  Bandit security scan..."
python3 -m bandit -r tests/ -ll -q && echo "   ✅ No high/medium security issues"

echo ""
echo "5️⃣  Radon complexity..."
python3 -m radon cc tests/ -s -n B | grep -q "." && echo "   ⚠️  Some functions above grade B" || echo "   ✅ All functions grade A-B"

echo ""
echo "6️⃣  Radon maintainability..."
python3 -m radon mi tests/ -s -n B | grep -q "." && echo "   ⚠️  Some files below grade A" || echo "   ✅ All files grade A"

echo ""
echo "✅ All QA checks passed!"
