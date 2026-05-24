#!/usr/bin/env python3
"""Run Black formatter check on modified files."""

import subprocess
import sys

files = [
    "src/grok_client.py",
    "src/utils/file_lock.py",
    "src/extraction/concurrent.py",
    "src/extraction/logistics.py",
    "src/extraction/dates.py",
    "src/extraction/places.py",
    "src/extraction/weather_central.py",
    "phase2_extract.py",
]

print("Checking code formatting with Black...\n")

# Check only (don't modify)
result = subprocess.run(
    ["python3", "-m", "black", "--check", "--diff"] + files,
    capture_output=True,
    text=True,
)

if result.stdout:
    print(result.stdout)
if result.stderr:
    print(result.stderr, file=sys.stderr)

if result.returncode == 0:
    print("\n✅ All files are properly formatted")
    sys.exit(0)
else:
    print("\n❌ Some files need formatting")
    print("\nRun to fix:")
    print(f"  python3 -m black {' '.join(files)}")
    sys.exit(1)
