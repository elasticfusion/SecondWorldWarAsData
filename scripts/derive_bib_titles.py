#!/usr/bin/env python3
"""Derive proper bibliography titles using Grok for entries with verbatim-backfilled titles.

Run independently of the pipeline. Processes entries where title was copied
directly from verbatim_reference and asks Grok to extract the actual document title.

Usage:
    python3 scripts/derive_bib_titles.py [--max-items 100] [--dry-run]
"""

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.grok_client import GrokClient
from src.utils.config import load_config

logger = logging.getLogger(__name__)

PROMPT = """Extract the proper document title from this bibliography citation.

Verbatim references (one or more mentions of this document):
{verbatim_list}

Current title guess: "{current_title}"

Rules:
- Return ONLY the document title — not the person interviewed, not the date of interview
- For interviews: the title is the collection/series name (e.g., "Historical Division Combat Interviews")
- For reports: the title is the report name (e.g., "After Action Report, 4th Armored Division, November 1944")
- For journals/diaries: include the unit and type (e.g., "37th Tank Battalion Journal, November 1944")
- Strip footnote numbers, leading punctuation
- If you cannot determine a better title than the current one, return the current title exactly

Return ONLY the title string, nothing else."""


REJECT_TITLES = {"ibid.", "ibid", "see above", "op. cit.", "loc. cit."}


def _is_valid_title(title: str) -> bool:
    """Reject titles that are cross-references, not actual document titles."""
    t = title.lower().strip().rstrip(".")
    if t in REJECT_TITLES:
        return False
    if t.startswith("see chap") or t.startswith("see p.") or t.startswith("see above"):
        return False
    if len(title) < 6:
        return False
    return True


def needs_title_fix(entry: dict) -> bool:
    """Check if this entry likely has a verbatim-backfilled title."""
    title = entry.get("title") or ""
    if not title or title == "Unknown":
        return True

    mentions = entry.get("mentions") or []
    for m in mentions:
        v = m.get("verbatim_reference", "")
        # Title matches (or is substring of) a verbatim reference
        if v and (title in v or v.startswith(title[:30])):
            return True
    return False


def derive_title(entry: dict, grok_client: GrokClient) -> str:
    """Ask Grok to derive the proper title."""
    mentions = entry.get("mentions") or []
    verbatims = [m.get("verbatim_reference", "") for m in mentions if m.get("verbatim_reference")]
    if not verbatims:
        return entry.get("title", "Unknown")

    verbatim_list = "\n".join(f"- {v}" for v in verbatims[:5])
    current_title = entry.get("title") or "Unknown"

    prompt = PROMPT.format(verbatim_list=verbatim_list, current_title=current_title)

    response = grok_client.chat_completion(
        prompt=prompt,
        system_prompt="You extract document titles from military bibliography citations. Be precise and concise.",
        temperature=0.0,
        use_cache=True,
        cache_type="bibliography_titles",
    )
    return response.strip().strip('"').split('\n')[0].strip()


def main():
    parser = argparse.ArgumentParser(description="Derive proper bibliography titles via Grok")
    parser.add_argument("--max-items", type=int, default=None, help="Max items to process")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    parser.add_argument("--bib-dir", type=Path, default=Path("output/bibliography"))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("src.grok_client").setLevel(logging.WARNING)

    config = load_config()
    grok_client = GrokClient(Path("cache/grok_cache"))

    files = sorted(args.bib_dir.glob("*.json"))
    candidates = []

    for f in files:
        if f.name in ("index.json", "review_queue.json"):
            continue
        try:
            d = json.load(open(f))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(d, dict):
            continue
        if needs_title_fix(d):
            candidates.append((f, d))

    logger.info(f"Found {len(candidates)} entries needing title derivation")

    if args.max_items:
        candidates = candidates[:args.max_items]

    fixed = 0
    for f, d in candidates:
        old_title = d.get("title", "Unknown")
        try:
            new_title = derive_title(d, grok_client)
        except Exception as e:
            logger.warning(f"  Failed: {f.name}: {e}")
            continue

        if new_title and new_title != old_title and _is_valid_title(new_title):
            if args.dry_run:
                logger.info(f"  {old_title[:50]}")
                logger.info(f"    → {new_title[:50]}")
            else:
                d["title"] = new_title
                citation = d.get("citation") or {}
                citation["title"] = new_title
                d["citation"] = citation
                with open(f, "w") as out:
                    json.dump(d, out, indent=2, ensure_ascii=False)
                fixed += 1

    if args.dry_run:
        logger.info(f"\nDry run: {fixed or len(candidates)} would be updated")
    else:
        logger.info(f"\nUpdated {fixed} bibliography titles")


if __name__ == "__main__":
    main()
