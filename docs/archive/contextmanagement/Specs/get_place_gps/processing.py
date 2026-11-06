"""Processing of sub-events for GPS geocoding."""

import json
import logging
import unicodedata
from pathlib import Path
from typing import Dict, List, Tuple

from .api import call_grok, parse_gps_response
from .cache import (
    extract_place_from_response,
    get_cached_result,
    store_cached_result,
)
from .extraction import extract_places
from .json_validator import JSONValidationError, validate_event_json
from .paths import BOOK_ROOT, get_paths, get_review_folder, is_processed
from .prompt_assembly import assemble_and_save_prompt

logger = logging.getLogger(__name__)

def _normalize_place_name(name: str) -> str:
    """Normalize place name by removing accents and converting to lowercase."""
    nfd = unicodedata.normalize('NFD', name)
    return ''.join(c for c in nfd if unicodedata.category(c) != 'Mn').lower().strip()

def load_sub_events(paths: Dict[str, Path]) -> List[Dict]:
    """Load sub-events from event file with validation."""
    try:
        data = validate_event_json(paths["event_file"])
    except JSONValidationError as exc:
        logger.error("JSON validation failed: %s", exc)
        raise
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Failed to read event file %s: %s", paths["event_file"], exc)
        raise

    sub_events = data.get(
        "Sub-events", data.get("Sub-event", data.get("sub_events", []))
    )
    if not isinstance(sub_events, list):
        logger.error(
            "Unexpected event structure in %s: expected list but got %s",
            paths["event_file"],
            type(sub_events).__name__,
        )
        sub_events = []
    return sub_events

def _build_second_pass_prompt(place_name: str, context: str) -> str:
    """Build second pass prompt from templates with context."""
    templates_dir = BOOK_ROOT / "data" / "prompts"
    second_pass_file = templates_dir / "place_second_pass.yaml"
    json_structure_file = templates_dir / "json-structure-place-gps.yaml"

    parts = []
    for file_path in [second_pass_file, json_structure_file]:
        if not file_path.is_file():
            raise FileNotFoundError(f"Template file not found: {file_path}")
        try:
            content = file_path.read_text(encoding="utf-8")
            parts.append(content.rstrip() + "\n\n")
        except OSError as exc:
            raise OSError(f"Failed to read {file_path}: {exc}") from exc

    prompt = "".join(parts)
    prompt = prompt.replace("#PLACE#", place_name.strip())
    prompt = f"{context}\n\n{prompt}"
    return prompt

def _extract_place_from_notes(notes: str) -> str:
    """Extract place name from notes string."""
    if "Place '" in notes:
        start = notes.find("Place '") + 7
        end = notes.find("'", start)
        if end > start:
            return notes[start:end]
    return "Unknown"

def _write_json_output(file_path: Path, data: dict, dry_run: bool, description: str) -> None:
    """Write JSON data to file with dry-run support."""
    if data and not dry_run:
        with file_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info("Wrote %s to %s", description, file_path)
    elif dry_run and data:
        logger.info("[DRY RUN] Would write %s", description)

def process_sub_event(
    chapter: int,
    section: str,
    idx: int,
    event: Dict,
    review_folder: Path,
    dry_run: bool,
    force_refresh: bool,
    cache_dir: Path | None = None,
    show_prompts: bool = False,
) -> List[Dict]:
    """Process a single sub-event."""
    places = extract_places(event)
    if not places:
        return []

    # Build rich context from multiple fields
    summary = event.get("Sub-event_summary", "").strip()

    # Collect fulltext paragraphs in order
    fulltext_parts = []
    fulltext = event.get("Sub-event_fulltext", {})
    if isinstance(fulltext, dict):
        # Extract paragraph numbers and sort numerically
        paragraph_items = []
        for k, v in fulltext.items():
            if k.startswith("Paragraph_"):
                try:
                    para_num = int(k.split("_")[1])
                    paragraph_items.append((para_num, k, v))
                except (IndexError, ValueError):
                    logger.warning("Invalid paragraph key format: %s", k)
                    continue
        paragraph_items.sort(key=lambda x: x[0])
        for _, key, paragraph_text in paragraph_items:
            paragraph_text = paragraph_text.strip()
            if paragraph_text:
                fulltext_parts.append(f"{key}: {paragraph_text}")

    fulltext_combined = (
        "\n\n".join(fulltext_parts) if fulltext_parts else ""
    )

    # Include raw Sub-Event-Places list for reference
    raw_places = event.get("Sub-Event-Places", [])
    places_str = (
        "Sub-Event-Places: " + ", ".join(raw_places)
        if raw_places
        else ""
    )

    # Combine all parts
    context_parts = [p for p in [summary, fulltext_combined, places_str] if p]
    context = "\n\n".join(context_parts)

    # Assemble and save the base prompt **once per sub-event**
    base_prompt = assemble_and_save_prompt(
        chapter=chapter,
        section=section,
        place="",
        context=context,
        review_folder=review_folder,
        dry_run=dry_run,
    )

    event_results = []

    for place in places:
        norm_place = place.strip()
        # Strip commas and periods from place name for API submission
        api_place = norm_place.replace(",", "").replace(".", "")
        cache_key = norm_place.lower()

        cached = (
            get_cached_result(cache_key, cache_dir)
            if cache_dir
            else None
        )
        if cached and not force_refresh:
            logger.info(
                "Place '%s' (sub-event %d): retrieved from CACHE",
                norm_place,
                idx,
            )
            event_results.append(cached)
            continue

        logger.info(
            "Place '%s' (sub-event %d): CACHE MISS – querying Grok API",
            norm_place,
            idx,
        )

        prompt = base_prompt.replace("#PLACE#", api_place)

        # Log full prompt if --show-prompts is enabled
        if show_prompts:
            logger.info(
                "Prompt submitted to Grok for place '%s' (sub-event %d):\n"
                "----------------------------------------\n"
                "%s\n"
                "----------------------------------------",
                norm_place,
                idx,
                prompt,
            )

        try:
            api_resp = call_grok(prompt)
            choices = api_resp.get("choices", [])
            if not choices:
                raise ValueError("Empty choices array in API response")
            content = choices[0].get("message", {}).get("content")
            if not content:
                raise ValueError("Empty API response content")
            gps_data = parse_gps_response(content)

            # Unwrap if response has "places" key
            if isinstance(gps_data, dict) and "places" in gps_data:
                gps_data = gps_data["places"]

            # Extract only this place from the response
            place_data = extract_place_from_response(norm_place, gps_data)

            # Validate that returned place matches requested place
            if place_data and isinstance(place_data, dict):
                returned_place = place_data.get("place", "")
                returned_normalized = _normalize_place_name(returned_place)
                if (
                    returned_place
                    and returned_normalized != _normalize_place_name(norm_place)
                ):
                    logger.warning(
                        "Place mismatch: requested '%s' but got '%s'",
                        norm_place,
                        returned_place,
                    )
                    place_data = {
                        "latitude": None,
                        "longitude": None,
                        "confidence": 0.0,
                        "notes": (
                            f"API returned '{returned_place}' "
                            f"instead of '{norm_place}'"
                        ),
                    }
            elif not place_data:
                place_data = {
                    "latitude": None,
                    "longitude": None,
                    "confidence": 0.0,
                    "notes": f"Place '{norm_place}' not found in API response",
                }

            # Write to cache immediately after first pass
            if cache_dir:
                store_cached_result(cache_key, place_data, cache_dir)

            # Second pass for zero confidence
            if place_data.get("confidence", 0) == 0.0:
                logger.info(
                    "Place '%s' (sub-event %d): zero confidence – "
                    "attempting second pass",
                    norm_place,
                    idx,
                )
                try:
                    second_prompt = _build_second_pass_prompt(
                        api_place, context
                    )
                    if show_prompts:
                        logger.info(
                            "Second pass prompt for place '%s' "
                            "(sub-event %d):\n"
                            "----------------------------------------\n"
                            "%s\n"
                            "----------------------------------------",
                            norm_place,
                            idx,
                            second_prompt,
                        )
                    logger.info(
                        "Place '%s' (sub-event %d): submitting second pass "
                        "API request",
                        norm_place,
                        idx,
                    )
                    api_resp2 = call_grok(second_prompt)
                    choices2 = api_resp2.get("choices", [])
                    if not choices2:
                        raise ValueError(
                            "Empty choices array in second pass response"
                        )
                    content2 = choices2[0].get("message", {}).get("content")
                    if content2:
                        gps_data2 = parse_gps_response(content2)
                        if (
                            isinstance(gps_data2, dict)
                            and "places" in gps_data2
                        ):
                            gps_data2 = gps_data2["places"]
                        place_data2 = extract_place_from_response(
                            norm_place, gps_data2
                        )
                        if place_data2 and place_data2.get("confidence", 0) > 0:
                            place_data = place_data2
                            logger.info(
                                "Place '%s' (sub-event %d): second pass "
                                "successful",
                                norm_place,
                                idx,
                            )
                            if cache_dir:
                                store_cached_result(
                                    cache_key, place_data, cache_dir
                                )
                except (ValueError, KeyError, TypeError) as exc:
                    logger.warning(
                        "Second pass failed for place '%s': %s",
                        norm_place,
                        exc,
                    )

            event_results.append(place_data)
            logger.info(
                "Place '%s' (sub-event %d): API call successful – "
                "result cached",
                norm_place,
                idx,
            )

        except (ValueError, KeyError, TypeError, OSError) as exc:
            logger.exception(
                "API call failed for place '%s' (sub-event %d)",
                norm_place,
                idx,
            )
            event_results.append(
                {
                    "latitude": None,
                    "longitude": None,
                    "confidence": 0.0,
                    "notes": f"API error: {str(exc)}",
                }
            )

    return event_results

def process_single(
    chapter: int,
    section: str,
    dry_run: bool,
    force_refresh: bool,
    cache_dir_arg: Path | str | None = None,
    show_prompts: bool = False,
) -> None:
    """Process a single chapter/section."""
    try:
        review_folder = get_review_folder(chapter, section)
        paths = get_paths(chapter, section, review_folder)
        base_name = f"chapter{chapter}{section or ''}"

        cache_dir = None
        if cache_dir_arg:
            try:
                cache_dir = Path(cache_dir_arg) if isinstance(cache_dir_arg, str) else cache_dir_arg
                cache_dir = cache_dir.resolve()
            except (OSError, ValueError, TypeError) as exc:
                logger.error("Invalid cache directory path: %s", exc)
                return

        logger.info("Processing %s → %s", base_name, review_folder)

        if (
            not dry_run
            and not force_refresh
            and is_processed(review_folder, base_name)
        ):
            logger.info("Already processed → skipping")
            return

        if not paths["event_file"].is_file():
            logger.error("Event file missing: %s", paths["event_file"])
            return

        try:
            sub_events = load_sub_events(paths)
        except (OSError, json.JSONDecodeError, JSONValidationError) as exc:
            logger.error("Failed to load sub-events: %s", exc)
            return

        results = {}
        failures = []

        for idx, event in enumerate(sub_events):
            event_results = process_sub_event(
                chapter,
                section,
                idx,
                event,
                review_folder,
                dry_run,
                force_refresh,
                cache_dir,
                show_prompts,
            )
            if event_results:
                successes = [
                    r for r in event_results if r.get("confidence", 0) > 0
                ]
                zero_conf = [
                    r for r in event_results if r.get("confidence", 0) == 0.0
                ]

                if successes:
                    results[str(idx)] = successes

                for failure in zero_conf:
                    place_name = (
                        failure.get("place")
                        or _extract_place_from_notes(failure.get("notes", ""))
                    )
                    failures.append(
                        {
                            "chapter": chapter,
                            "section": section,
                            "sub_event": idx,
                            "place": place_name,
                            "notes": failure.get("notes", ""),
                        }
                    )

        if results:
            _write_json_output(paths["output_gps"], results, dry_run, f"{len(results)} events")

        if failures:
            failure_log = review_folder / f"{base_name}-gps-failures.json"
            _write_json_output(failure_log, failures, dry_run, f"{len(failures)} failures")

    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        logger.error("Error processing chapter/section: %s", exc)

def find_unprocessed_folders() -> List[Tuple[int, str]]:
    """Find unprocessed review folders."""
    import re

    from .paths import PROMPTS_ROOT

    folders = []

    for ch_dir in sorted(PROMPTS_ROOT.iterdir()):
        if not ch_dir.is_dir() or not ch_dir.name.lower().startswith(
            "chapter"
        ):
            continue
        match = re.search(r"chapter(\d+)", ch_dir.name.lower())
        if not match:
            continue
        try:
            ch_num = int(match.group(1))
        except ValueError:
            continue

        for rev_dir in ch_dir.iterdir():
            if not rev_dir.is_dir() or not rev_dir.name.lower().endswith(
                "-review"
            ):
                continue
            rev_lower = rev_dir.name.lower().removesuffix("-review")
            section = rev_lower.replace(f"chapter{ch_num}", "")
            base_name = f"chapter{ch_num}{section}"

            if is_processed(rev_dir, base_name):
                continue

            folders.append((ch_num, section))

    return sorted(folders)
