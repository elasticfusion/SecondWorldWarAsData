#!/usr/bin/env python3
"""
Compare the number of occurrences of 'Paragraph_' in two files.

This script counts how many times the exact string 'Paragraph_'
appears in each of the two provided files and displays the results,
including the difference between the counts.

Usage:
    python count_paragraph.py file1.txt file2.txt

Arguments:
    file1     Path to the first file
    file2     Path to the second file
"""

import argparse
import sys
from pathlib import Path


def count_paragraph_occurrences(filepath: str) -> int:
    """
    Count the number of times 'Paragraph_' appears in the given file.

    Args:
        filepath: Path to the file to analyze

    Returns:
        Integer count of 'Paragraph_' occurrences

    Raises:
        FileNotFoundError: If the file does not exist
        PermissionError: If the file cannot be read
    """
    try:
        content = Path(filepath).read_text(encoding='utf-8')
        return content.count('Paragraph_')
    except FileNotFoundError:
        print(f"Error: File not found: {filepath}", file=sys.stderr)
        sys.exit(1)
    except PermissionError:
        print(f"Error: Permission denied reading file: {filepath}", file=sys.stderr)
        sys.exit(1)
    except UnicodeDecodeError:
        print(f"Error: Cannot decode file as UTF-8: {filepath}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error reading file {filepath}: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Compare the number of 'Paragraph_' occurrences in two files."
    )
    parser.add_argument("file1", help="Path to the first file")
    parser.add_argument("file2", help="Path to the second file")

    args = parser.parse_args()

    # Count occurrences in both files
    count1 = count_paragraph_occurrences(args.file1)
    count2 = count_paragraph_occurrences(args.file2)

    # Prepare output
    print("Comparison of 'Paragraph_' occurrences:")
    print("────────────────────────────────────────")
    print(f"  {args.file1:40} : {count1:4d}")
    print(f"  {args.file2:40} : {count2:4d}")
    print("────────────────────────────────────────")

    difference = count2 - count1

    if difference > 0:
        print(f"→ Second file has {difference} more 'Paragraph_' than the first file.")
    elif difference < 0:
        print(f"→ First file has {-difference} more 'Paragraph_' than the second file.")
    else:
        print("→ Both files contain the same number of 'Paragraph_' occurrences.")

    print()


if __name__ == "__main__":
    main()