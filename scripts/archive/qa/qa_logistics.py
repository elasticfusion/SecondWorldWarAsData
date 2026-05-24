#!/usr/bin/env python3
"""Run QA checks on logistics.py"""

import subprocess
import sys


def run_command(cmd, description):
    """Run command and report results."""
    print(f"\n{'='*60}")
    print(f"Running: {description}")
    print(f"Command: {' '.join(cmd)}")
    print("=" * 60)

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)

    return result.returncode


def main():
    """Run all QA checks."""
    target = "src/extraction/logistics.py"

    checks = [
        (
            ["python3", "-m", "black", "--check", target],
            "Black - Code Formatting Check",
        ),
        (
            ["python3", "-m", "mypy", target, "--ignore-missing-imports"],
            "Mypy - Type Checking",
        ),
        (
            ["python3", "-m", "pylint", target, "--disable=C0301,C0103,R0913,R0914"],
            "Pylint - Code Quality",
        ),
        (
            ["python3", "-m", "bandit", "-r", target, "-ll"],
            "Bandit - Security Analysis",
        ),
        (
            ["python3", "-m", "radon", "cc", target, "-s"],
            "Radon - Cyclomatic Complexity",
        ),
        (
            ["python3", "-m", "radon", "mi", target, "-s"],
            "Radon - Maintainability Index",
        ),
        (["python3", "-m", "py_compile", target], "Syntax Check"),
    ]

    results = {}
    for cmd, desc in checks:
        returncode = run_command(cmd, desc)
        results[desc] = "✅ PASS" if returncode == 0 else "❌ FAIL"

    # Summary
    print(f"\n{'='*60}")
    print("QA SUMMARY")
    print("=" * 60)
    for desc, status in results.items():
        print(f"{status} {desc}")

    # Overall result
    failed = sum(1 for status in results.values() if "FAIL" in status)
    if failed == 0:
        print(f"\n✅ All {len(checks)} checks passed!")
        return 0
    else:
        print(f"\n❌ {failed}/{len(checks)} checks failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
