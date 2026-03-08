#!/usr/bin/env python3
"""Run QA checks on all modified files."""

import subprocess
import sys
from pathlib import Path

def run_check(cmd, description):
    """Run command and return status."""
    print(f"\n{'='*70}")
    print(f"{description}")
    print('='*70)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result.returncode == 0

def main():
    """Run QA on all modified files."""
    files = [
        "src/grok_client.py",
        "src/utils/file_lock.py",
        "src/extraction/concurrent.py",
        "src/extraction/logistics.py",
        "src/extraction/dates.py",
        "src/extraction/places.py",
        "src/extraction/weather_central.py",
    ]
    
    results = {}
    
    for filepath in files:
        print(f"\n{'#'*70}")
        print(f"# QA: {filepath}")
        print('#'*70)
        
        file_results = {}
        
        # Syntax check
        file_results["syntax"] = run_check(
            ["python3", "-m", "py_compile", filepath],
            "Syntax Check"
        )
        
        # Type checking
        file_results["mypy"] = run_check(
            ["python3", "-m", "mypy", filepath, "--ignore-missing-imports"],
            "Type Checking (mypy)"
        )
        
        # Code quality
        file_results["pylint"] = run_check(
            ["python3", "-m", "pylint", filepath, 
             "--disable=C0301,C0103,R0913,R0914,W0511"],
            "Code Quality (pylint)"
        )
        
        # Complexity
        file_results["radon_cc"] = run_check(
            ["python3", "-m", "radon", "cc", filepath, "-s"],
            "Cyclomatic Complexity (radon)"
        )
        
        file_results["radon_mi"] = run_check(
            ["python3", "-m", "radon", "mi", filepath, "-s"],
            "Maintainability Index (radon)"
        )
        
        # Dead code detection
        file_results["vulture"] = run_check(
            ["python3", "-m", "vulture", filepath, "--min-confidence", "80"],
            "Dead Code Detection (vulture)"
        )
        
        results[filepath] = file_results
    
    # Summary
    print(f"\n{'='*70}")
    print("QA SUMMARY")
    print('='*70)
    
    for filepath, checks in results.items():
        print(f"\n{filepath}:")
        for check, passed in checks.items():
            status = "✅ PASS" if passed else "❌ FAIL"
            print(f"  {status} {check}")
    
    # Overall
    total_checks = sum(len(checks) for checks in results.values())
    passed_checks = sum(sum(checks.values()) for checks in results.values())
    failed_checks = total_checks - passed_checks
    
    print(f"\n{'='*70}")
    print(f"Total: {passed_checks}/{total_checks} checks passed")
    if failed_checks > 0:
        print(f"❌ {failed_checks} checks failed")
        return 1
    else:
        print("✅ All checks passed!")
        return 0

if __name__ == "__main__":
    sys.exit(main())
