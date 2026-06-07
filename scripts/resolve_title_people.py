#!/usr/bin/env python3
"""Resolve people records that have a title/position instead of a name.

Asks Grok to identify the actual person based on the title, time period,
and event context. Updates the record if identified.

Usage:
    .venv/bin/python scripts/resolve_title_people.py [--dry-run]
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

TITLE_PATTERNS = [
    "commander",
    "chief of",
    "head of",
    "director",
    "minister",
    "officer",
    "leader",
    "president",
    "secretary",
    "fortress",
]

PROMPT = """Identify the specific person who held this position/title during WWII.

Title/Position: "{title}"
Context: {context}
Time period: {time_period}
Book source: {book}

If you can identify the specific person with reasonable confidence, return ONLY a JSON object:
{{"name": "Full Name", "confidence": 0.9, "source": "reason you know this"}}

If you cannot determine the specific person, return:
{{"name": null, "confidence": 0, "source": null}}

Return ONLY the JSON object, nothing else."""


def find_title_people():
    """Find people files where name is a title/position."""
    candidates = []
    for f in sorted(glob.glob("output/people/*.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
            if not isinstance(d, dict):
                continue
            name = (d.get("name") or "").lower()
            if any(p in name for p in TITLE_PATTERNS):
                candidates.append((Path(f), d))
        except (json.JSONDecodeError, OSError):
            pass
    return candidates


def resolve_person(entry: dict, grok_client) -> dict | None:
    """Ask Grok to identify the person. Returns {name, confidence, source} or None."""
    title = entry.get("name", "")
    mentions = entry.get("event_mentions", [])

    contexts = []
    books = set()
    for m in mentions[:3]:
        if m.get("context"):
            contexts.append(m["context"])
        if m.get("original_text"):
            contexts.append(m["original_text"])
        if m.get("book"):
            books.add(m["book"])

    context = "; ".join(contexts[:3]) if contexts else "No additional context"
    book = ", ".join(books) if books else "Unknown"
    time_period = "1939-1945 (WWII)"

    prompt = PROMPT.format(
        title=title, context=context, time_period=time_period, book=book
    )

    response = grok_client.chat_completion(
        prompt=prompt,
        system_prompt="You identify specific historical persons from their WWII titles/positions. Be precise.",
        temperature=0.0,
        use_cache=True,
        cache_type="people_title_resolve",
    )

    try:
        result = json.loads(response.strip())
        if result.get("name") and result.get("confidence", 0) >= 0.7:
            return result
    except json.JSONDecodeError:
        pass
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from src.grok_client import GrokClient

    grok_client = GrokClient(Path("cache/grok_cache"))

    candidates = find_title_people()
    logger.info(f"Found {len(candidates)} people with title-as-name")

    resolved = 0
    for path, entry in candidates:
        old_name = entry.get("name", "")
        result = resolve_person(entry, grok_client)

        if result:
            if args.dry_run:
                logger.info(
                    f"  {old_name} → {result['name']} (conf={result['confidence']}, src={result['source'][:50]})"
                )
            else:
                entry["name"] = result["name"]
                entry.setdefault(
                    "name_resolution",
                    {
                        "original_title": old_name,
                        "resolved_by": "grok",
                        "confidence": result["confidence"],
                        "source": result["source"],
                    },
                )
                with open(path, "w", encoding="utf-8") as out:
                    json.dump(entry, out, indent=2, ensure_ascii=False)

                # Rename file
                new_fname = result["name"].lower().replace(" ", "_") + ".json"
                new_path = path.parent / new_fname
                if not new_path.exists():
                    path.rename(new_path)

            resolved += 1
        else:
            if args.dry_run:
                logger.info(f"  {old_name} → [unresolved]")

    logger.info(
        f"\n{'Would resolve' if args.dry_run else 'Resolved'}: {resolved}/{len(candidates)}"
    )


if __name__ == "__main__":
    main()
