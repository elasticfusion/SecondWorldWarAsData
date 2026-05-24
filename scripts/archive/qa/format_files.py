#!/usr/bin/env python3
"""Format files with Black."""

import subprocess
import sys

files = [
    "src/extraction/concurrent.py",
    "src/extraction/logistics.py",
]

print("Formatting files with Black...\n")

result = subprocess.run(
    ["python3", "-m", "black"] + files, capture_output=True, text=True
)

print(result.stdout)
if result.stderr:
    print(result.stderr, file=sys.stderr)

sys.exit(result.returncode)
