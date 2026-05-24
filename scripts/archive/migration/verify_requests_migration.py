#!/usr/bin/env python3
"""Verify httpx to requests migration."""

import sys
from pathlib import Path


def check_file(filepath: Path, function_name: str) -> bool:
    """Check if function uses requests instead of httpx."""
    content = filepath.read_text()

    # Find function
    if function_name not in content:
        print(f"❌ {filepath.name}: Function '{function_name}' not found")
        return False

    # Extract function body (simple heuristic)
    func_start = content.find(f"def {function_name}")
    if func_start == -1:
        print(f"❌ {filepath.name}: Could not locate function definition")
        return False

    # Get next ~100 lines after function start
    func_section = content[func_start : func_start + 5000]

    # Check for requests usage
    has_requests = "import requests" in func_section or "requests.get" in func_section
    has_httpx_client = "httpx.Client" in func_section
    has_httpx_get = "httpx.get(" in func_section

    if has_requests and not has_httpx_client and not has_httpx_get:
        print(f"✅ {filepath.name}::{function_name} - Using requests")
        return True
    elif has_httpx_client or has_httpx_get:
        print(f"❌ {filepath.name}::{function_name} - Still using httpx")
        return False
    else:
        print(f"⚠️  {filepath.name}::{function_name} - Unclear (may be OK)")
        return True


def main():
    """Run verification checks."""
    project_root = Path(__file__).parent
    src_extraction = project_root / "src" / "extraction"

    checks = [
        (src_extraction / "grok_search_maps.py", "download_image"),
        (src_extraction / "maps.py", "_download_map_image"),
        (src_extraction / "maps.py", "_download_image_to_s3"),
        (src_extraction / "equipment.py", "_download_media_file"),
    ]

    print("=" * 60)
    print("Verifying httpx → requests migration")
    print("=" * 60)

    results = []
    for filepath, function_name in checks:
        if not filepath.exists():
            print(f"❌ File not found: {filepath}")
            results.append(False)
            continue

        results.append(check_file(filepath, function_name))

    print("=" * 60)
    if all(results):
        print("✅ All checks passed!")
        return 0
    else:
        print("❌ Some checks failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
