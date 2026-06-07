#!/usr/bin/env python3
"""Resolve single-word (surname-only) people names from their own event_mentions text.

Usage:
    .venv/bin/python scripts/resolve_surname_people.py [--dry-run] [--max-items 50]
"""

import argparse
import json
import glob
import logging
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
    positions = [
        m.get("position_at_event", "") for m in mentions if m.get("position_at_event")
    ]
    contexts = [m.get("original_text", "") for m in mentions if m.get("original_text")]

    position = positions[0] if positions else ""
    context = contexts[0][:150] if contexts else ""

    if not (rank or position):
        return surname

    prompt = KNOWLEDGE_PROMPT.format(
        surname=surname,
        rank=rank,
        nationality=nationality,
        position=position,
        context=context,
    )

    response = grok_client.chat_completion(
        prompt=prompt,
        system_prompt="You identify WWII military personnel from surname and context. Be precise, only answer if confident.",
        temperature=0.0,
        use_cache=True,
        cache_type="people_name_knowledge",
    )
    return response.strip().strip('"').split("\n")[0].strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-items", type=int, default=None)
    args = parser.parse_args()

    from src.grok_client import GrokClient

    grok_client = GrokClient(Path("cache/grok_cache"))

    candidates = _find_candidates()
    logger.info(f"Found {len(candidates)} single-word name entries")
    if args.max_items:
        candidates = candidates[: args.max_items]

    resolved = 0
    for path, entry in candidates:
        full_name = _resolve_name(entry, grok_client)
        if not full_name:
            continue
        if args.dry_run:
            logger.info(f"  {entry['name']} → {full_name}")
        else:
            _apply_rename(path, entry, full_name)
        resolved += 1

    logger.info(
        f"\n{'Would resolve' if args.dry_run else 'Resolved'}: {resolved}/{len(candidates)}"
    )


def _find_candidates():
    """Find people files with single-word names."""
    candidates = []
    for f in sorted(glob.glob("output/people/*.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
            if not isinstance(d, dict):
                continue
            name = d.get("name", "")
            if " " in name.strip() or len(name) <= 2:
                continue
            candidates.append((Path(f), d))
        except (json.JSONDecodeError, OSError):
            pass
    return candidates


def _resolve_name(entry, grok_client):
    """Resolve a single-word surname to a full name via Grok."""
    surname = entry.get("name", "")
    mentions = entry.get("event_mentions", [])
    excerpts = [m.get("original_text", "") for m in mentions if m.get("original_text")]
    if not excerpts:
        return None

    excerpts.sort(
        key=lambda e: len(e) if surname.lower() in e.lower() else 0, reverse=True
    )

    prompt = PROMPT.format(
        surname=surname, excerpts="\n".join(f"- {e[:150]}" for e in excerpts[:5])
    )

    response = grok_client.chat_completion(
        prompt=prompt,
        system_prompt="You extract full names from military history text. Return only the name.",
        temperature=0.0,
        use_cache=True,
        cache_type="people_name_resolve",
    )

    full_name = response.strip().strip('"').split("\n")[0].strip()
    if not _is_valid_resolution(full_name, surname):
        full_name = _knowledge_lookup(entry, surname, grok_client)

    if not _is_valid_resolution(full_name, surname):
        return None
    return full_name


def _is_valid_resolution(full_name, surname):
    """Check if a resolved name is valid."""
    return (
        full_name
        and full_name != surname
        and len(full_name) <= 60
        and len(full_name) >= len(surname)
    )


def _apply_rename(path, entry, full_name):
    """Write resolved name and rename file."""
    entry["name"] = full_name
    with open(path, "w", encoding="utf-8") as out:
        json.dump(entry, out, indent=2, ensure_ascii=False)
    new_fname = full_name.lower().replace(" ", "_") + ".json"
    new_path = path.parent / new_fname
    if not new_path.exists():
        path.rename(new_path)


if __name__ == "__main__":
    main()
