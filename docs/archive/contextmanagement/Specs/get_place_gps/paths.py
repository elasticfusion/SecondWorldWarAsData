"""Path management for chapter/section review folders."""

from pathlib import Path
from typing import Dict
import logging

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Path anchors – independent of cwd
# ──────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
BOOK_ROOT = PROJECT_ROOT / "BreakoutAndPursuit"
PROMPTS_ROOT = BOOK_ROOT / "data" / "prompts"
OUTPUT_SUFFIXES = ("-place.json", "-gps.json")

def _section_str(section: str | None) -> str:
    """Convert section to string, empty if None."""
    return section if section else ""

def validate_prompts_root():
    """Validate the prompts root directory exists."""
    if not PROMPTS_ROOT.is_dir():
        logger.error("Prompts root missing: %s", PROMPTS_ROOT)
        logger.error("Expected layout: …/BreakoutAndPursuit/data/prompts/")
        raise FileNotFoundError(f"Prompts root missing: {PROMPTS_ROOT}")
    logger.info("Prompts root: %s", PROMPTS_ROOT)

def get_review_folder(chapter: int, section: str | None) -> Path:
    """Get the review folder path for a chapter and section."""
    chapter_str = str(chapter)
    section_str = _section_str(section)
    expected_lower = f"chapter{chapter_str}{section_str}-review".lower()

    chapter_dir = PROMPTS_ROOT / f"chapter{chapter_str}"
    if not chapter_dir.is_dir():
        raise FileNotFoundError(f"Chapter directory missing: {chapter_dir}")

    try:
        for item in chapter_dir.iterdir():
            if item.is_dir() and item.name.lower() == expected_lower:
                return item
    except (OSError, PermissionError) as e:
        msg = f"Cannot read chapter directory {chapter_dir}: {e}"
        raise FileNotFoundError(msg) from e

    raise FileNotFoundError(f"No review folder found for {chapter}{section_str}")

def get_paths(chapter: int, section: str | None,
              review_folder: Path | None = None) -> Dict[str, Path]:
    """Get paths for event, output files."""
    if review_folder is None:
        review_folder = get_review_folder(chapter, section)

    chapter_str = str(chapter)
    section_str = _section_str(section)
    base = f"chapter{chapter_str}{section_str}"

    return {
        "review_folder": review_folder,
        "event_file": review_folder / f"{base}-event.json",
        "output_gps": review_folder / f"{base}-gps.json",
        "output_place": review_folder / f"{base}-place.json",
    }

def is_processed(review_folder: Path, base_name: str) -> bool:
    """Check if the folder is already processed."""
    for suffix in OUTPUT_SUFFIXES:
        if (review_folder / f"{base_name}{suffix}").exists():
            return True
    return False
