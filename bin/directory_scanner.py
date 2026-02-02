# directory_scanner.py

"""
Sub-module for scanning the prompts directory and extracting all CHAPTER and SECTION from matching review folders.

This module iterates through all folders in {BOOK}/data/prompts/ matching the 'chapter{CHAPTER}{SECTION}-review' pattern
and returns a sorted list of (CHAPTER, SECTION) pairs for processing.
"""

import os
import re

# Define the base directory to scan (relative to the project root or bin; adjust as needed)
BASE_DIR = "../BreakoutAndPursuit/data/prompts"  # Based on BOOK = "BreakoutAndPursuit"

# Pattern to match review folders (e.g., chapter5a-review or chapter20-review, with optional section)
REVIEW_FOLDER_PATTERN = r"chapter(\d+)([a-z]?)-review"


def get_all_review_folders():
    """Scan the prompts directory recursively for all review folders and extract CHAPTER and SECTION values, skipping processed ones."""
    if not os.path.exists(BASE_DIR):
        raise FileNotFoundError(f"Base directory not found: {BASE_DIR}")

    extracted = []
    for root, dirs, files in os.walk(BASE_DIR):
        for folder in dirs:
            match = re.match(REVIEW_FOLDER_PATTERN, folder)
            if match:
                chapter = int(match.group(1))
                section = match.group(2) if match.group(2) else ''  # Set to empty string if no section
                # Check for indicator file to skip processed directories
                indicator_file = f"chapter{chapter}{section}-place.json"
                indicator_path = os.path.join(root, folder, indicator_file)
                if os.path.exists(indicator_path):
                    print(f"Skipping processed folder: {folder} (indicator file {indicator_file} found)")
                    continue
                extracted.append((chapter, section))

    # Sort extracted values for sequential order (by chapter, then section; treat empty section as lowest)
    extracted.sort(key=lambda x: (x[0], ord(x[1]) if x[1] else 0))
    return extracted


if __name__ == "__main__":
    folders = get_all_review_folders()
    print(f"Extracted review folders: {folders}")