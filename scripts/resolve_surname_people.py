#!/usr/bin/env python3
"""Resolve single-word (surname-only) people names from their own event_mentions text.

Usage:
    .venv/bin/python scripts/resolve_surname_people.py [--dry-run] [--max-items 50]
"""

import argparse
import json
import glob
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(message)s")
logging.getLogger("src.grok_client").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

PROMPT = """Extract the full name of "{surname}" from these text excerpts.

Excerpts:
{excerpts}

Rules:
- Return ONLY the full name (e.g., "John T. Cole"), nothing else
- Include rank only if it's part of how the person is consistently named (e.g., keep middle initials, drop "Col.")
- If the full name cannot be determined from the text, return "{surname}" exactly"""

KNOWLEDGE_PROMPT = """Identify the full name of this WWII military person from their surname and context.

Surname: {surname}
Rank: {rank}
Nationality: {nationality}
Position: {position}
Context: {context}

Rules:
- Return ONLY the full name if you are confident (e.g., "Paul W. Baade")
- You must be highly confident this is the correct person — do not guess
- If multiple people could match or you are unsure, return "{surname}" exactly
- Return ONLY the name string, nothing else"""


def _knowledge_lookup(entry: dict, surname: str, grok_client) -> str:
    """Fallback: ask Grok to identify person from rank, nationality, position."""
    rank = entry.get("rank", "")
    nationality = entry.get("nationality", "")
    mentions = entry.get("event_mentions", [])
    positions = [m.get("position_at_event", "") for m in mentions if m.get("position_at_event")]
    contexts = [m.get("original_text", "") for m in mentions if m.get("original_text")]

    position = positions[0] if positions else ""
    context = contexts[0][:150] if contexts else ""

    if not (rank or position):
        return surname

    prompt = KNOWLEDGE_PROMPT.format(
        surname=surname, rank=rank, nationality=nationality,
        position=position, context=context,
    )

    response = grok_client.chat_completion(
        prompt=prompt,
        system_prompt="You identify WWII military personnel from surname and context. Be precise, only answer if confident.",
        temperature=0.0,
        use_cache=True,
        cache_type="people_name_knowledge",
    )
    return response.strip().strip('"').split('\n')[0].strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-items", type=int, default=None)
    args = parser.parse_args()

    from src.grok_client import GrokClient
    grok_client = GrokClient(Path("cache/grok_cache"))

    candidates = []
    for f in sorted(glob.glob("output/people/*.json")):
        try:
            d = json.load(open(f))
            if not isinstance(d, dict):
                continue
            name = d.get("name", "")
            if " " in name.strip() or len(name) <= 2:
                continue
            candidates.append((Path(f), d))
        except (json.JSONDecodeError, OSError):
            pass

    logger.info(f"Found {len(candidates)} single-word name entries")
    if args.max_items:
        candidates = candidates[:args.max_items]

    resolved = 0
    for path, entry in candidates:
        surname = entry.get("name", "")
        mentions = entry.get("event_mentions", [])
        excerpts = [m.get("original_text", "") for m in mentions if m.get("original_text")]
        if not excerpts:
            continue

        # Prioritize excerpts that contain more than just the surname (likely have full name)
        excerpts.sort(key=lambda e: len(e) if surname.lower() in e.lower() else 0, reverse=True)

        prompt = PROMPT.format(surname=surname, excerpts="\n".join(f"- {e[:150]}" for e in excerpts[:5]))

        response = grok_client.chat_completion(
            prompt=prompt,
            system_prompt="You extract full names from military history text. Return only the name.",
            temperature=0.0,
            use_cache=True,
            cache_type="people_name_resolve",
        )

        full_name = response.strip().strip('"').split('\n')[0].strip()
        # Reject if unchanged, too short, or contains explanatory text
        if not full_name or full_name == surname or len(full_name) > 60 or len(full_name) < len(surname):
            # Fallback: ask Grok from knowledge using rank, nationality, position
            full_name = _knowledge_lookup(entry, surname, grok_client)

        if not full_name or full_name == surname or len(full_name) > 60 or len(full_name) < len(surname):
            continue

        if args.dry_run:
            logger.info(f"  {surname} → {full_name}")
        else:
            entry["name"] = full_name
            with open(path, "w") as out:
                json.dump(entry, out, indent=2, ensure_ascii=False)
            # Rename file
            new_fname = full_name.lower().replace(" ", " ") + ".json"
            new_path = path.parent / new_fname
            if not new_path.exists():
                path.rename(new_path)
        resolved += 1

    logger.info(f"\n{'Would resolve' if args.dry_run else 'Resolved'}: {resolved}/{len(candidates)}")


if __name__ == "__main__":
    main()
