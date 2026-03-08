#!/usr/bin/env python3
"""Test Phase 2 setup without calling API."""

import sys
from pathlib import Path

print("=" * 70)
print("PHASE 2 SETUP VERIFICATION")
print("=" * 70)

# Check imports
print("\n1. Checking imports...")
try:
    from src.grok_client import GrokClient
    from src.extraction.events import extract_events
    from src.schemas import EventOutput, SubEvent

    print("   ✓ All imports successful")
except ImportError as e:
    print(f"   ✗ Import error: {e}")
    print("   Run: pip install -r requirements.txt")
    sys.exit(1)

# Check for .env file
print("\n2. Checking for .env file...")
env_file = Path(".env")
if env_file.exists():
    print("   ✓ .env file found")
    from dotenv import load_dotenv
    import os

    load_dotenv()
    if os.getenv("GROK_API_KEY"):
        print("   ✓ GROK_API_KEY is set")
    else:
        print("   ⚠ GROK_API_KEY not found in .env")
        print("   Add: GROK_API_KEY=your_key_here")
else:
    print("   ⚠ .env file not found")
    print("   Create from template: cp .env.example .env")

# Check for parsed files
print("\n3. Checking for parsed files...")
output_dir = Path("output")
if output_dir.exists():
    parsed_files = list(output_dir.rglob("*-parsed.json"))
    if parsed_files:
        print(f"   ✓ Found {len(parsed_files)} parsed file(s)")
        for f in parsed_files[:3]:
            print(f"     - {f.relative_to(output_dir)}")
        if len(parsed_files) > 3:
            print(f"     ... and {len(parsed_files) - 3} more")
    else:
        print("   ✗ No parsed files found")
        print("   Run: python3 phase1_parse.py")
else:
    print("   ✗ output/ directory not found")
    print("   Run: python3 phase1_parse.py")

# Check cache directory
print("\n4. Checking cache directory...")
cache_dir = Path("cache")
if cache_dir.exists():
    print(f"   ✓ Cache directory exists")
else:
    print("   ℹ Cache directory will be created on first run")

# Test schema validation
print("\n5. Testing schema validation...")
try:
    from src.schemas import generate_ulid

    test_ulid = generate_ulid()
    print(f"   ✓ ULID generation works: {test_ulid}")
except Exception as e:
    print(f"   ✗ Schema error: {e}")

print("\n" + "=" * 70)
print("SETUP VERIFICATION COMPLETE")
print("=" * 70)
print("\nReady to run Phase 2:")
print("  python3 phase2_extract.py")
print()
