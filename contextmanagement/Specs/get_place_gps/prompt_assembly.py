"""Prompt assembly for GPS geocoding requests."""

import logging
from pathlib import Path

from config import (
    BOOKNAME,
    CONTEXT,
    GPS_PROMPT_ASSEMBLY_ORDER,
    GPS_PROMPT_LOOKUP_STRATEGY,
    GPS_PROMPT_TARGET_FILENAME,
    MODEL,
    TYPE,
)

from .paths import BOOK_ROOT

logger = logging.getLogger(__name__)

TEMPLATES_DIR = BOOK_ROOT / "data" / "prompts"

def _get_context_limit() -> int:
    """Get context limit from config with error handling."""
    try:
        return int(CONTEXT)
    except (ValueError, TypeError) as e:
        logger.error("Invalid CONTEXT value: %s", e)
        return 1000

def find_prompt_file(filename: str, review_folder: Path) -> Path | None:
    """Find prompt file in configured locations."""
    candidates = []
    if GPS_PROMPT_LOOKUP_STRATEGY in ("prefer_review_folder", "review_first"):
        candidates.append(review_folder / filename)
    if GPS_PROMPT_LOOKUP_STRATEGY != "central_only":
        candidates.append(TEMPLATES_DIR / filename)

    for p in candidates:
        if p.is_file():
            return p
    logger.warning("Prompt component not found: %s", filename)
    return None

def load_prompt_template(review_folder: Path) -> str:
    """Load and concatenate prompt templates."""
    parts = []
    missing = []

    for fn in GPS_PROMPT_ASSEMBLY_ORDER:
        p = find_prompt_file(fn, review_folder)
        if p:
            try:
                content = p.read_text(encoding="utf-8")
                logger.debug("Loaded template %s (%d chars)", fn, len(content))
                parts.append(content.rstrip() + "\n\n")
            except (OSError, UnicodeDecodeError) as e:
                logger.error("Cannot read %s: %s", p, e)
                missing.append(fn)
        else:
            missing.append(fn)

    if not parts:
        raise RuntimeError(f"No prompt components loaded. Missing: {', '.join(missing)}")

    if missing:
        logger.warning("Incomplete prompt – missing: %s", ', '.join(missing))

    return "".join(parts)

def assemble_and_save_prompt(
    chapter: int,
    section: str,
    place: str,
    context: str,
    review_folder: Path,
    dry_run: bool = False,
) -> str:
    """Assemble and save prompt, return rendered string."""
    chapter_str = str(chapter)
    section_str = section if section else ""

    # Use configurable target filename
    target_filename = GPS_PROMPT_TARGET_FILENAME.format(
        chapter=chapter_str, section=section_str
    )
    target_path = review_folder / target_filename

    # Load template using existing function
    template = load_prompt_template(review_folder)

    # Apply chapter-level substitutions (lowercase keys to match review.yaml)
    substitutions = {
        "#bookname#": BOOKNAME,
        "#chapter#": chapter_str,
        "#section#": section_str,
        "#sourcelink#": (
            "https://www.ibiblio.org/hyperwar/USA/USA-E-Breakout/"
            f"USA-E-Breakout-{chapter_str}.html"
        ),
        "#endnotelink#": (
            "https://www.ibiblio.org/hyperwar/USA/USA-E-Breakout/"
            f"fn{chapter_str}.html"
        ),
        "#localsource#": f"chapter{chapter_str}{section_str}-event.json",
    }
    for placeholder, value in substitutions.items():
        template = template.replace(placeholder, value)

    # Apply place-specific substitutions (lowercase keys)
    place_substitutions = {
        "#PLACE#": place.strip(),
        "#CONTEXT#": context.strip()[: _get_context_limit()],
        "#TYPE#": TYPE,
        "#MODEL#": MODEL,
    }
    rendered = template
    for placeholder, value in place_substitutions.items():
        rendered = rendered.replace(placeholder, value)

    # Save to review folder (unless dry run)
    if not dry_run:
        target_path.write_text(rendered, encoding="utf-8")
        logger.info("Saved rendered prompt: %s", target_path)
        logger.debug("Saved prompt preview (first 400 chars):\n%s...", rendered[:400])
    else:
        logger.info("[DRY RUN] Would save rendered prompt to: %s", target_path)

    return rendered
