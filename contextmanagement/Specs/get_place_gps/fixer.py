"""Search and replace fixer for JSON validation errors."""

import shutil
import time
from pathlib import Path
from typing import List, Tuple, Optional

# Error patterns and their fixes
ERROR_FIXES = {
    "Sub-Event-Maps": {
        "replacement": "Sub-Events-Maps",
        "description": "Rename field from Sub-Event-Maps to Sub-Events-Maps"
    }
}


def _create_fix_proposal(file_path: Path, error: str, pattern: str,
                         fix_info: dict) -> dict:
    """Create a fix proposal dictionary."""
    return {
        "file_path": file_path,
        "error": error,
        "search": f'"{pattern}"',
        "replace": f'"{fix_info["replacement"]}"',
        "description": fix_info["description"]
    }


def propose_fixes(failed_files: List[Tuple[Path, str]]) -> List[dict]:
    """
    Propose search and replace fixes for validation errors.

    Args:
        failed_files: List of (file_path, error_message) tuples

    Returns:
        List of fix proposals with keys: file_path, error, search, replace,
        description
    """
    if not failed_files:
        return []

    fixes = []
    for item in failed_files:
        try:
            file_path, error = item
        except (TypeError, ValueError):
            continue
        for error_pattern, fix_info in ERROR_FIXES.items():
            if error_pattern in error:
                fixes.append(_create_fix_proposal(file_path, error,
                                                   error_pattern, fix_info))
    return fixes


def apply_fix(
    file_path: Path,
    search: str,
    replace: str,
    dry_run: bool = True,
    interactive: bool = False,
    backup_dir: Optional[Path] = None
) -> Tuple[bool, str]:
    """
    Apply search and replace fix to a file.

    Args:
        file_path: Path to file to fix
        search: String to search for
        replace: String to replace with
        dry_run: If True, don't modify file
        interactive: If True, ask for confirmation before applying
        backup_dir: Directory to store backups (default: file's parent/.backups)

    Returns:
        Tuple of (success, message)
    """
    if not isinstance(file_path, Path):
        return False, f"file_path must be a Path, got {type(file_path).__name__}"
    if not isinstance(search, str):
        return False, f"search must be a string, got {type(search).__name__}"
    if not isinstance(replace, str):
        return False, f"replace must be a string, got {type(replace).__name__}"

    try:
        content = file_path.read_text(encoding='utf-8')
    except (FileNotFoundError, PermissionError, UnicodeDecodeError, OSError) as e:
        return False, f"Failed to read {file_path}: {e}"

    if search not in content:
        return False, f"Search string not found in {file_path}"

    new_content = content.replace(search, replace)
    if len(replace) != len(search):
        count = (len(content) - len(new_content)) // (len(replace) - len(search))
    else:
        count = content.count(search)

    if interactive and not dry_run:
        print(f"\nFile: {file_path}")
        print(f"Occurrences: {count}")
        print(f"Search:  {search}")
        print(f"Replace: {replace}")
        if input("Apply fix? (y/n): ").strip().lower() != 'y':
            return False, "Skipped by user"

    if dry_run:
        return True, f"[DRY RUN] Would replace {count} occurrence(s)"

    # Create backup
    if backup_dir is None:
        backup_dir = file_path.parent / ".backups"
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"{file_path.name}.{int(time.time())}.bak"
        shutil.copy2(file_path, backup_path)
    except OSError as e:
        return False, f"Failed to create backup: {e}"

    # Apply fix
    try:
        file_path.write_text(new_content, encoding='utf-8')
    except OSError as e:
        return False, f"Failed to write {file_path}: {e}"
    return True, f"Fixed {count} occurrence(s), backup saved to {backup_path}"


def apply_fixes_batch(
    fixes: List[dict],
    dry_run: bool = True,
    interactive: bool = False,
    backup_dir: Optional[Path] = None
) -> List[Tuple[Path | None, bool, str]]:
    """
    Apply multiple fixes.

    Args:
        fixes: List of fix proposals from propose_fixes()
        dry_run: If True, don't modify files
        interactive: If True, ask for confirmation before each fix
        backup_dir: Directory to store backups

    Returns:
        List of (file_path, success, message) tuples
    """
    if not isinstance(fixes, list):
        raise TypeError(f"fixes must be a list, got {type(fixes).__name__}")

    results = []

    for fix in fixes:
        if not isinstance(fix, dict):
            msg = f"Fix item must be a dictionary, got {type(fix).__name__}"
            results.append((None, False, msg))
            continue
        if not all(k in fix for k in ["file_path", "search", "replace"]):
            results.append((fix.get("file_path"), False,
                           "Missing required fix keys"))
            continue
        success, msg = apply_fix(
            fix["file_path"],
            fix["search"],
            fix["replace"],
            dry_run=dry_run,
            interactive=interactive,
            backup_dir=backup_dir
        )
        results.append((fix["file_path"], success, msg))

    return results
