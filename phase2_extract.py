#!/usr/bin/env python3
"""
Phase 2: Event and Entity Extraction with Grok API
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from src.utils.config import load_config, get_paths
from src.utils.logger import setup_logging
from src.grok_client import GrokClient, BatchModeCollecting, current_book
from src.extraction.events import extract_events
from src.extraction.external_maps import import_maps

from scripts.find_duplicate_people import generate_duplicate_report
from scripts.find_related_groups import generate_related_groups_report

# ---------------------------------------------------------------------------
# Stage helpers
# ---------------------------------------------------------------------------


def _count_entity_dir(d: Path) -> int:
    """Count persisted record files in an entity dir, excluding index/report/queue/hidden files.
    Fail-safe: returns 0 if the dir is missing/unreadable."""
    # Non-record artifacts written into entity dirs by the analysis (step 5/5) and the later dedup
    # pass — must NOT be counted as records (they inflate the count, WARNING-2 from review).
    _skip_stems = {
        "index",
        "duplicate_report",
        "related_groups_report",
        "not_duplicates",
        "not_related",
        "not_people",
        "review_queue",
        "coords",
        "surname_frequency_suggestions",
    }
    try:
        if not d.is_dir():
            return 0
        n = 0
        for f in d.glob("*.json"):
            stem = f.stem
            if stem in _skip_stems or stem.startswith(".") or stem.endswith("_report"):
                continue
            n += 1
        return n
    except Exception:  # noqa: BLE001 - counting must never break the run
        return 0


def _log_phase2_summary(output_root: Path, logger) -> dict:
    """Emit ONE end-of-run OBSERV line with the persisted record count per entity type, so a
    zero-output bug (the optional-entity-zero / people-bio-zero class) is visible immediately
    instead of via log archaeology. Fail-safe: a summary error must never abort Phase 2.

    Returns the counts dict (also used by tests)."""
    try:
        output_root = Path(output_root)
        counts: dict[str, int] = {}
        # Events live per-chapter under content/<book>/*-event.json (not a flat dir).
        try:
            counts["events"] = sum(
                1 for _ in (output_root / "content").rglob("*-event.json")
            )
        except Exception:  # noqa: BLE001
            counts["events"] = 0
        # File-per-record entity dirs (singular source_section dir name is intentional).
        # NOTE: 'supplemental' is intentionally NOT counted — supplemental material persists into
        # bibliography + event sub-events, never its own record dir, so it is structurally always 0
        # (WARNING-1 from review: flagging it as a zero cried wolf every run).
        core_types = ("events", "people", "places", "dates")
        for name in (
            "people",
            "places",
            "dates",
            "people_groups",
            "maps",
            "images",
            "bibliography",
            "source_section",
            "casualties",
            "weather",
            "equipment",
            "logistics",
        ):
            counts[name] = _count_entity_dir(output_root / name)

        summary = " ".join(f"{k}={v}" for k, v in counts.items())
        logger.info("OBSERV phase2.summary %s", summary)
        # Make a zero LOUD only for CORE types (events/people/places/dates) that should essentially
        # never be 0 on a real run — this is the "zero = probable bug" signal. Optional types
        # (maps/images/casualties/weather/equipment/logistics/people_groups) are legitimately 0 for
        # many chapters, so their zeros are reported at INFO to avoid crying wolf (SUGGESTION-1).
        core_zeros = [k for k in core_types if counts.get(k, 0) == 0]
        if core_zeros:
            logger.warning(
                "OBSERV phase2.summary.zero scope=core entity_types=%s "
                "(0 persisted — probable bug, verify)",
                ",".join(core_zeros),
            )
        optional_zeros = [
            k for k, v in counts.items() if v == 0 and k not in core_types
        ]
        if optional_zeros:
            logger.info(
                "OBSERV phase2.summary.zero scope=optional entity_types=%s "
                "(0 persisted — expected for some chapters)",
                ",".join(optional_zeros),
            )
        return counts
    except Exception as e:  # noqa: BLE001 - summary must never break the run
        logger.warning("OBSERV phase2.summary status=error reason=%s", type(e).__name__)
        return {}


def _apply_scope_guard(parsed_files, book, expected_count, content_root, logger):
    """Scope ``parsed_files`` by book, then enforce the shared run-scope contract.

    The ``--book`` filter restricts to one book (unknown book is a hard error).
    Count/ceiling enforcement is delegated to the shared, UNOVERRIDABLE
    :func:`src.utils.run_scope_guard.enforce_scope` so Phase 2 and Phase 3 share
    one rule that no local flag can disable.

    Returns the (possibly filtered) list. Raises SystemExit on mismatch.
    """
    from src.utils.run_scope_guard import ScopeMismatchError, enforce_scope

    total = len(parsed_files)

    recorded_expected = None
    if book:
        book_dir = content_root / book
        scoped = [p for p in parsed_files if p.is_relative_to(book_dir)]
        if not scoped:
            logger.error(
                "--book %r matched 0 of %d parsed files under %s — aborting "
                "(refusing to fall through to a whole-corpus run).",
                book,
                total,
                content_root,
            )
            raise SystemExit(2)
        logger.info(
            "Scoped to book %r: %d of %d parsed files", book, len(scoped), total
        )
        parsed_files = scoped

        # INDEPENDENT source-of-truth: the count Phase 1 recorded for this book (NOT a re-glob
        # of what we just scoped). enforce_scope reconciles our ACTUAL work-set against it and
        # fails loud only on FAN-OUT (actual > recorded; processing more than Phase 1 produced).
        # A full run == recorded and an incremental resume < recorded both pass. None (not
        # recorded) → magic-50 fallback.
        try:
            from src.utils.run_scope import read_scope

            output_root = content_root.parent  # output/content -> output
            recorded_expected = read_scope(output_root, book)
            if recorded_expected is not None:
                logger.info(
                    "Run-scope: recorded Phase-1 section_count=%d for book %r",
                    recorded_expected,
                    book,
                )
        except Exception as _e:  # noqa: BLE001 - never block on the read
            # A corrupt/unreadable run_scope record silently DOWNGRADES the scope guard to the
            # weaker magic-50 fallback (recorded_expected stays None). That must be VISIBLE — a
            # downgraded guard is exactly when a fan-out bug could slip through unnoticed.
            logger.warning(
                "OBSERV run_scope.read_failed book=%r err=%s — scope guard DOWNGRADED to the "
                "magic-50 fallback (recorded Phase-1 count unavailable)",
                book,
                _e,
            )

    try:
        enforce_scope(
            actual=len(parsed_files),
            expected=expected_count,
            recorded_expected=recorded_expected,
            label="Phase 2 chapters",
            logger=logger,
        )
    except ScopeMismatchError as e:
        raise SystemExit(e.exit_code) from e
    return parsed_files


def _complete_metadata(base_dir, paths, logger, batch_mode=False):
    """Step 0: Complete any incomplete metadata."""
    try:
        from scripts.complete_metadata_with_grok import (
            is_metadata_incomplete,
            extract_metadata_with_grok,
        )
        import yaml

        content_dir = base_dir / "contentrepository"
        meta_files = list(content_dir.glob("**/*-meta.yaml"))

        incomplete_count = 0
        updated_count = 0

        for meta_file in meta_files:
            with open(meta_file, "r", encoding="utf-8") as f:
                metadata = yaml.safe_load(f)

            if is_metadata_incomplete(metadata):
                incomplete_count += 1
                logger.info("  Completing: %s", meta_file.relative_to(content_dir))

                grok_client = GrokClient(paths["api_cache"], batch_mode=batch_mode)
                updated_metadata = extract_metadata_with_grok(
                    meta_file.parent, metadata, grok_client
                )

                if updated_metadata != metadata:
                    with open(meta_file, "w", encoding="utf-8") as f:
                        yaml.dump(
                            updated_metadata,
                            f,
                            default_flow_style=False,
                            sort_keys=False,
                        )
                    logger.info(
                        "    ✓ Updated: %s", updated_metadata.get("chapter_title")
                    )
                    updated_count += 1

        if incomplete_count > 0:
            logger.info(
                "  Completed %d/%d metadata file(s)", updated_count, incomplete_count
            )
        else:
            logger.info("  ✓ All metadata complete")

    except Exception as e:
        logger.warning("  Metadata completion failed: %s", e)
        logger.warning("  Continuing with existing metadata...")


def _is_processed(output_root: Path, entity_type: str, event_name: str) -> bool:
    """Check if an event file has already been processed for this entity type."""
    marker = output_root / entity_type / ".processed_events.json"
    if not marker.exists():
        return False
    return event_name in set(json.loads(marker.read_text()))


def _mark_processed(output_root: Path, entity_type: str, event_name: str) -> None:
    """Record that an event file has been processed for this entity type."""
    marker = output_root / entity_type / ".processed_events.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    processed = set(json.loads(marker.read_text())) if marker.exists() else set()
    processed.add(event_name)
    marker.write_text(json.dumps(sorted(processed)))


def _extract_optional_entities(
    event_file, parsed_file, grok_client, paths, config, logger
):
    """Run optional entity extractors (weather, equipment, logistics, casualties, supplemental) for a single event file."""
    output_root = paths["output_root"]

    _extract_weather(event_file, parsed_file, grok_client, output_root, config, logger)
    _extract_equipment(event_file, grok_client, output_root, config, logger)
    _extract_logistics(event_file, grok_client, output_root, config, logger)
    _extract_casualties(event_file, grok_client, output_root, config, logger)
    _extract_supplemental(event_file, grok_client, output_root, config, logger)


def _extract_weather(event_file, parsed_file, grok_client, output_root, config, logger):
    if not config.get("weather", {}).get("enabled", False):
        return
    try:
        from src.extraction.weather_central import extract_weather_central

        result = extract_weather_central(
            event_file=event_file,
            weather_dir=output_root / "weather",
            grok_client=grok_client,
            places_dir=output_root / "places",
            parsed_file=parsed_file,
            fetch_api=config.get("weather", {}).get("fetch_api_data", False),
            max_retries=3,
        )
        if result:
            logger.info("    ✓ Weather updated")
        logger.info(
            "OBSERV optional.persisted entity=weather event=%s persisted=%s",
            event_file.name,
            bool(result),
        )
    except ValueError as e:
        logger.error("    Weather config error for %s: %s", event_file.name, e)
    except (OSError, IOError) as e:
        logger.error("    Weather file I/O error for %s: %s", event_file.name, e)
    except BatchModeCollecting:
        logger.info(
            "OBSERV optional.collecting entity=weather event=%s", event_file.name
        )
    except Exception as e:  # pylint: disable=broad-except
        logger.error("    Error extracting weather from %s: %s", event_file.name, e)


def _extract_equipment(event_file, grok_client, output_root, config, logger):
    if not config.get("equipment", {}).get("enabled", False):
        return
    try:
        from src.extraction.equipment import extract_equipment_from_event

        equipment_files = extract_equipment_from_event(
            event_file=event_file,
            output_dir=output_root / "equipment",
            grok_client=grok_client,
            output_root=output_root,
            enable_enrichment=config.get("equipment", {}).get(
                "enable_enrichment", False
            ),
            verify_media_with_vision=config.get("equipment", {}).get(
                "verify_media_with_vision", True
            ),
        )
        if equipment_files:
            logger.info("    ✓ Equipment: %d file(s)", len(equipment_files))
        logger.info(
            "OBSERV optional.persisted entity=equipment event=%s count=%d",
            event_file.name,
            len(equipment_files or []),
        )
    except json.JSONDecodeError as e:
        logger.error("    Equipment JSON parse error for %s: %s", event_file.name, e)
    except (OSError, IOError) as e:
        logger.error("    Equipment file I/O error for %s: %s", event_file.name, e)
    except BatchModeCollecting:
        logger.info(
            "OBSERV optional.collecting entity=equipment event=%s", event_file.name
        )
    except Exception as e:  # pylint: disable=broad-except
        logger.error("    Error extracting equipment from %s: %s", event_file.name, e)


def _extract_logistics(event_file, grok_client, output_root, config, logger):
    if not config.get("logistics", {}).get("enabled", False):
        return
    from src.utils.config import should_reprocess

    if not should_reprocess("logistics") and _is_processed(
        output_root, "logistics", event_file.name
    ):
        return
    try:
        from src.extraction.logistics import extract_logistics_from_event

        result = extract_logistics_from_event(
            event_file=event_file,
            output_root=output_root,
            grok_client=grok_client,
        )
        if result:
            logger.info("    ✓ Logistics updated")
        logger.info(
            "OBSERV optional.persisted entity=logistics event=%s count=%d",
            event_file.name,
            len(result) if hasattr(result, "__len__") else (1 if result else 0),
        )
        _mark_processed(output_root, "logistics", event_file.name)
    except json.JSONDecodeError as e:
        logger.error("    Logistics JSON parse error for %s: %s", event_file.name, e)
    except (OSError, IOError) as e:
        logger.error("    Logistics file I/O error for %s: %s", event_file.name, e)
    except BatchModeCollecting:
        logger.info(
            "OBSERV optional.collecting entity=logistics event=%s", event_file.name
        )
    except Exception as e:  # pylint: disable=broad-except
        logger.error("    Error extracting logistics from %s: %s", event_file.name, e)


def _extract_casualties(event_file, grok_client, output_root, config, logger):
    if not config.get("casualties", {}).get("enabled", False):
        return
    from src.utils.config import should_reprocess

    if not should_reprocess("casualties") and _is_processed(
        output_root, "casualties", event_file.name
    ):
        return
    try:
        from src.extraction.casualties import extract_casualties

        casualties = extract_casualties(
            event_file=event_file,
            output_root=output_root,
            grok_client=grok_client,
        )
        if casualties:
            logger.info("    ✓ Casualties: %d record(s)", len(casualties))
        logger.info(
            "OBSERV optional.persisted entity=casualties event=%s count=%d",
            event_file.name,
            len(casualties or []),
        )
        _mark_processed(output_root, "casualties", event_file.name)
    except json.JSONDecodeError as e:
        logger.error("    Casualties JSON parse error for %s: %s", event_file.name, e)
    except (OSError, IOError) as e:
        logger.error("    Casualties file I/O error for %s: %s", event_file.name, e)
    except BatchModeCollecting:
        logger.info(
            "OBSERV optional.collecting entity=casualties event=%s", event_file.name
        )
    except Exception as e:  # pylint: disable=broad-except
        logger.error("    Error extracting casualties from %s: %s", event_file.name, e)


def _extract_supplemental(event_file, grok_client, output_root, config, logger):
    if not config.get("supplemental_material", {}).get("enabled", False):
        return
    from src.utils.config import should_reprocess

    # Skip already-processed endnotes so a retrieve re-run doesn't re-fetch EVERY
    # endnote (slow per-endnote HTTP fetches to ibiblio) from scratch. Without
    # this guard + the _mark_processed below, supplemental never converged on a
    # --retrieve-only re-run (every endnote re-processed each time).
    if not should_reprocess("supplemental") and _is_processed(
        output_root, "supplemental", event_file.name
    ):
        return
    try:
        from src.extraction.supplemental import extract_supplemental

        result = extract_supplemental(
            event_file=event_file,
            grok_client=grok_client,
            output_root=output_root,
            enrich=config.get("supplemental_material", {}).get(
                "enrich_with_searches", False
            ),
        )
        if result:
            logger.info("    ✓ Supplemental updated")
        _mark_processed(output_root, "supplemental", event_file.name)
    except json.JSONDecodeError as e:
        logger.error("    Supplemental JSON parse error for %s: %s", event_file.name, e)
    except (OSError, IOError) as e:
        logger.error("    Supplemental file I/O error for %s: %s", event_file.name, e)
    except BatchModeCollecting:
        logger.debug("    Batch collecting (supplemental): %s", event_file.name)
    except Exception as e:  # pylint: disable=broad-except
        logger.error(
            "    Error extracting supplemental from %s: %s", event_file.name, e
        )


def _extract_maps(output_root, config, logger):
    """Extract maps from Phase 1 parsed files."""
    if not config.get("maps", {}).get("enabled", False):
        return

    logger.info("[phase2 step 2/5] Extracting maps from source material")
    try:
        from src.extraction.maps import extract_maps

        extract_maps(
            _parsed_dir=output_root,
            output_dir=output_root,
            places_dir=output_root / "places",
            dates_dir=output_root / "dates",
            config=config,
        )
    except Exception as e:
        logger.error("  Error extracting maps: %s", e)


def _extract_images(output_root, config, logger):
    """Extract images from source material."""
    if not config.get("images", {}).get("enabled", True):
        return

    logger.info("[phase2 step 3/5] Extracting images from source material")
    try:
        from src.extraction.images import extract_images

        img_config = config.get("images", {})
        storage = img_config.get("image_storage_path")
        storage_path = Path(storage) if storage else None

        extract_images(
            output_dir=output_root,
            places_dir=output_root / "places",
            dates_dir=output_root / "dates",
            download=img_config.get("download", True),
            image_storage_path=storage_path,
        )
    except Exception as e:
        logger.error("  Error extracting images: %s", e)


def _extract_external_maps(base_dir, grok_client, paths, config, logger):
    """Import external maps from the LOCAL corpus (external_maps.yaml).

    OpenSERP (live web map search) is NOT done here — it is Phase 3 only, so that
    OpenSERP/NAT come up exactly once (in Phase 3) and Phase 2 stays limited to
    Grokipedia/Wikipedia + local-corpus work. This step imports only the
    local-file maps that are part of the imported corpus.
    """
    if not config.get("external_maps", {}).get("enabled", False):
        return

    # Import from the local corpus YAML if it exists (no internet / no OpenSERP).
    yaml_path = base_dir / "external_maps.yaml"
    if yaml_path.exists():
        logger.info(
            "[phase2 step 3/5] Importing local corpus maps (external_maps.yaml)"
        )
        try:
            yaml_imported = import_maps(
                yaml_path=yaml_path,
                output_dir=paths["output_root"] / "external_maps",
                places_dir=paths["output_root"] / "places",
                dates_dir=paths["output_root"] / "dates",
                storage_backend=config.get("external_maps", {}).get(
                    "storage_backend", "filesystem"
                ),
                allowed_licenses=config.get("external_maps", {}).get(
                    "allowed_licenses"
                ),
            )
            logger.info("  ✓ Imported %d maps from local corpus YAML", yaml_imported)
        except Exception as e:
            logger.error("  Error importing from YAML: %s", e)


def _event_file_needs_retry(event_file: Path) -> bool:
    """Check if event file is missing, empty, or has no sub-events."""
    if not event_file.exists():
        return True
    try:
        with open(event_file, "r", encoding="utf-8") as f:
            return not json.load(f).get("Sub-events")
    except Exception:
        return True


def _retry_missing_events(parsed_files, grok_client, logger):
    """Retry any parsed files that are missing or empty event files.

    NO-OP in batch (submit-only) mode: after the collection pass every chapter legitimately has no
    event file yet — the results arrive only after ``submit_batch`` + the batch re-run. Re-calling
    ``extract_events`` here would re-add the identical request_ids to the BatchCollector (dropped as
    duplicates -> ``OBSERV batch.duplicate_request_id_dropped`` + a false ``✗ Retry failed`` ERROR on
    a perfectly healthy run) and can never succeed pre-submit. The live (non-batch) path, where a
    cache-clear-and-retry can actually recover a transient miss, is unchanged.
    """
    if getattr(grok_client, "batch_mode", False):
        logger.debug(
            "Skipping missing-event retry in batch mode "
            "(events are fulfilled by submit_batch + re-run, not an inline retry)"
        )
        return 0, 0

    missing_events = []
    for parsed_file in sorted(parsed_files):
        event_file = parsed_file.parent / parsed_file.name.replace(
            "-parsed.json", "-event.json"
        )
        if _event_file_needs_retry(event_file):
            if event_file.exists():
                logger.warning("  Empty event file (0 sub-events): %s", event_file.name)
            missing_events.append(parsed_file)

    if not missing_events:
        return 0, 0

    logger.warning("\n%d file(s) missing event files", len(missing_events))
    logger.info("Retrying with cache cleared...")

    retried = 0
    retry_failed = 0
    for parsed_file in missing_events:
        logger.info("Retrying: %s", parsed_file.name)
        current_book.set(parsed_file.parent.name)

        # Clear this chapter's cached event response
        from src.extraction.events import create_event_prompt

        with open(parsed_file, "r", encoding="utf-8") as f:
            parsed_data = json.load(f)
        prompt = create_event_prompt(parsed_data)
        if prompt:
            grok_client.clear_cache_entry(prompt, cache_type="events")

        try:
            output_file = extract_events(
                parsed_file=parsed_file,
                grok_client=grok_client,
                output_dir=parsed_file.parent,
            )
            if output_file:
                logger.info("  ✓ Generated: %s", output_file.name)
                retried += 1
            else:
                logger.warning("  ⏭ Skipped (footnotes only)")
        except Exception as e:
            logger.error("  ✗ Retry failed: %s", e)
            retry_failed += 1

    return retried, retry_failed


def _run_source_section_enrichment(output_root, grok_client, logger):
    """Phase-2 source_section enrichment: derive section summary + operation label, then fetch
    Grokipedia/Wikipedia reference articles + Wikipedia media. Fail-safe (never aborts phase2).
    """
    logger.info(
        "[phase2 step 4b/5] Source-section enrichment (summary + operation + Grok/Wiki)"
    )
    try:
        from src.extraction.source_section import enrich_all_source_sections

        n = enrich_all_source_sections(output_root, grok_client)
        logger.info("  ✓ Enriched %d source section(s)", n)
    except Exception as e:  # noqa: BLE001
        logger.error("  ✗ Source-section enrichment failed: %s", e)


def _run_analysis(output_root, logger):
    """Run duplicate detection and group analysis."""
    # People duplicates
    logger.info("[phase2 step 5/5] Analyzing for duplicate people")

    people_dir = output_root / "people"
    if people_dir.exists():
        try:
            duplicate_report = people_dir / "duplicate_report.json"
            generate_duplicate_report(people_dir, duplicate_report)
            logger.info("  ✓ Saved: %s", duplicate_report.name)
        except Exception as e:
            logger.error("  ✗ Duplicate detection failed: %s", e)
    else:
        logger.warning("No people directory found")

    # Related groups
    logger.info("[phase2 step 5/5] Analyzing people groups for relationships")

    groups_dir = output_root / "people_groups"
    if groups_dir.exists():
        try:
            related_report = groups_dir / "related_groups_report.json"
            generate_related_groups_report(groups_dir, related_report)
            logger.info("  ✓ Saved: %s", related_report.name)
        except Exception as e:
            logger.error("  ✗ Related groups analysis failed: %s", e)
    else:
        logger.warning("No people groups directory found")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _run_core_extraction(
    parsed_files,
    grok_client,
    output_root,
    config,
    paths,
    max_parallel,
    heartbeat,
    logger,
    skip_optional=False,
):
    """Run steps 1-3: parallel extraction, retry, optional entities. Returns (processed, failed)."""
    from src.extraction.batch_parallel import process_chapters_parallel

    results = asyncio.run(
        process_chapters_parallel(
            parsed_files=parsed_files,
            grok_client=grok_client,
            output_root=output_root,
            config=config,
            max_parallel=max_parallel,
            heartbeat=heartbeat,
        )
    )

    processed = results["processed"]
    failed = results["failed"]

    if os.environ.get("SKIP_RETRY"):
        # In retrieve-only mode: only retry chapters with substantial content
        # (skip footnotes-only sections that legitimately have 0 sub-events)
        substantial = []
        for parsed_file in sorted(parsed_files):
            event_file = parsed_file.parent / parsed_file.name.replace(
                "-parsed.json", "-event.json"
            )
            if not _event_file_needs_retry(event_file):
                continue
            # Check if parsed file has enough content to warrant retry
            try:
                with open(parsed_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                paragraphs = data.get("paragraphs", [])
                if len(paragraphs) >= 3:
                    substantial.append(parsed_file)
                else:
                    logger.info(
                        "  Skipping %s (only %d paragraphs — likely footnotes)",
                        parsed_file.name,
                        len(paragraphs),
                    )
            except Exception:
                substantial.append(parsed_file)
        if substantial:
            logger.info("Retrying %d substantial empty event files", len(substantial))
            retried, retry_failed = _retry_missing_events(
                substantial, grok_client, logger
            )
        else:
            logger.info(
                "SKIP_RETRY: all empty event files are footnotes-only, skipping"
            )
            retried, retry_failed = 0, 0
    else:
        retried, retry_failed = _retry_missing_events(parsed_files, grok_client, logger)
    processed += retried
    failed += retry_failed

    any_optional = any(
        config.get(feature, {}).get("enabled", False)
        for feature in [
            "weather",
            "equipment",
            "logistics",
            "casualties",
            "supplemental_material",
        ]
    )

    # PRIMARY deterministic bibliography pass — writes a citable record per parsed
    # footnote/endnote with NO LLM/batch/cache, so bibliography exists the moment Phase 1
    # parses the chapter (independent of the fragile supplemental batch→cache→retrieve
    # chain). Gated ONLY on supplemental_material.enabled — NOT on the optional-batch gate
    # — because its whole purpose is to run as a primary step every pass; it is idempotent
    # (dedups by title/verbatim), so safe in the batch-collect submit pass AND the retrieve
    # re-run. The LLM supplemental step (optional block below) is an enrichment overlay on
    # these records. Scoped to the in-run parsed_files (never a whole-corpus rglob).
    if config.get("supplemental_material", {}).get("enabled", False):
        from src.extraction.bibliography import extract_bibliography_deterministic

        for pf in sorted(parsed_files):
            try:
                current_book.set(pf.parent.name)
                extract_bibliography_deterministic(
                    pf,
                    paths["output_root"],
                    book=pf.parent.name,
                    chapter=pf.name.replace("-parsed.json", ""),
                )
            except Exception as e:  # pylint: disable=broad-except
                logger.warning(
                    "Deterministic bibliography pass failed for %s: %s", pf.name, e
                )

        # Durability: in AWS mode store_bibliography_entry writes to the Dynamo index
        # only; mirror the records to local output/bibliography/*.json so the Phase-2 S3
        # sync persists them to the DURABLE system of record (S3 is authoritative, Dynamo
        # is a derived rebuildable index). This makes bibliography durable within Phase 2,
        # NOT dependent on the later dedup-phase materialization (interrupted-run safe).
        try:
            from src.extraction.bibliography import materialize_bibliography_to_local

            materialize_bibliography_to_local(paths["output_root"])
        except Exception as e:  # pylint: disable=broad-except
            logger.warning("Bibliography S3-materialize failed: %s", e)

        # Reconcile (Option B / Dynamo-deprecation): collapse any duplicate bibliography
        # records that share a match key — transient cross-Fargate writes of the same
        # citation AND legacy random-ULID files — into one canonical deterministic file,
        # unioning mentions. S3-native, no Dynamo. Fail-safe.
        try:
            from src.extraction.bibliography import reconcile_bibliography_dir

            reconcile_bibliography_dir(paths["output_root"] / "bibliography")
        except Exception as e:  # pylint: disable=broad-except
            logger.warning("Bibliography reconcile failed: %s", e)

    if any_optional and not skip_optional:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        # Scope to the SAME set the run was guarded for: derive each event file
        # from the in-scope parsed_files, never a fresh whole-corpus rglob. This
        # keeps --book / --expected-count honest for the optional extractors too
        # (CFN security review: a scoped run must not touch other books here).
        event_files = sorted(
            {
                pf.parent / pf.name.replace("-parsed.json", "-event.json")
                for pf in parsed_files
            }
        )
        event_files = [e for e in event_files if e.exists()]
        max_workers = config.get("concurrency", {}).get("max_event_files", 3)

        def _process_event(event_file):
            current_book.set(event_file.parent.name)
            parsed_file = event_file.parent / event_file.name.replace(
                "-event.json", "-parsed.json"
            )
            if not parsed_file.exists():
                parsed_file = None
            _extract_optional_entities(
                event_file, parsed_file, grok_client, paths, config, logger
            )

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_process_event, ef) for ef in event_files]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    logger.warning("Optional extraction failed: %s", e)

    return processed, failed


def main():
    """Main entry point for Phase 2."""
    parser = argparse.ArgumentParser(
        description="Extract events from parsed WWII documents"
    )
    parser.add_argument(
        "--log-level",
        choices=["TRACE", "DEBUG", "INFO", "WARN", "ERROR", "FATAL"],
        default=None,
        help="Set logging level (overrides config.yaml)",
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Use xAI Batch API (50%% cost reduction, async processing)",
    )
    parser.add_argument(
        "--book",
        default=None,
        help="Scope extraction to a single book (output book dir name). Without "
        "it, ALL parsed chapters in the corpus are processed.",
    )
    parser.add_argument(
        "--expected-count",
        type=int,
        default=None,
        help="Expected number of chapters to process. If the actual number of "
        "parsed files to be processed differs, ABORT (fail-loud scope guard "
        "against an unintended whole-corpus run).",
    )
    args = parser.parse_args()

    # Load configuration
    base_dir = Path(__file__).parent
    config = load_config(base_dir / "config.yaml")
    paths = get_paths(config, base_dir)

    from src.utils.validation_stats import reset_stats as _reset_val_stats

    _reset_val_stats()

    # Setup logging
    log_config = config.get("logging", {})
    log_level = args.log_level or log_config.get("level", "INFO")
    logger = setup_logging(
        level=log_level,
        log_file=log_config.get("file"),
        console=log_config.get("console", True),
    )

    logger.info("Starting Phase 2: Event and Entity Extraction")

    # Step 0: Complete metadata
    logger.info("[phase2 step 0/5] Checking metadata completeness")
    _complete_metadata(base_dir, paths, logger, batch_mode=args.batch)

    # Check for API key
    from dotenv import load_dotenv

    load_dotenv()

    if not os.getenv("GROK_API_KEY"):
        logger.error("GROK_API_KEY not found in environment")
        logger.error("Please create .env file with your API key")
        sys.exit(1)

    # Initialize Grok client
    cache_dir = paths["api_cache"]
    cache_dir.mkdir(parents=True, exist_ok=True)
    grok_client = GrokClient(cache_dir, batch_mode=args.batch)
    if args.batch:
        logger.info(
            "Batch mode enabled — collecting requests for xAI Batch API (50%% off)"
        )
    logger.info("Initialized Grok client with API cache: %s", cache_dir)

    # Ensure cache directories exist
    for cache_type in ["image_cache", "map_cache"]:
        if cache_type in paths:
            paths[cache_type].mkdir(parents=True, exist_ok=True)

    # Find parsed files
    output_root = paths["output_root"]
    from src.utils.config import get_content_root

    content_root = get_content_root(paths)
    parsed_files = list(content_root.rglob("*-parsed.json"))

    if not parsed_files:
        logger.error("No parsed files found in %s", content_root)
        logger.error("Please run phase1_parse.py first")
        sys.exit(1)

    # Scope + fail-loud guard: a scoped intent must not silently become a
    # whole-corpus run. --book takes precedence; otherwise fall back to the
    # BOOK_NAME env the ECS entrypoint sets for the triggering title, so the
    # extraction scope matches what was downloaded.
    scope_book = args.book or os.environ.get("BOOK_NAME", "").strip() or None
    parsed_files = _apply_scope_guard(
        parsed_files, scope_book, args.expected_count, content_root, logger
    )

    logger.info("Found %d parsed file(s)", len(parsed_files))

    # Start heartbeat monitor (warns if no progress for 5 minutes)
    from src.utils.heartbeat import Heartbeat

    heartbeat = Heartbeat(timeout=300, label="Phase 2")
    heartbeat.start()
    heartbeat.ping("Step 1: parallel extraction started")

    # -----------------------------------------------------------------------
    # Steps 1-3: Parallel extraction, retry, optional entities
    # -----------------------------------------------------------------------
    logger.info(
        "[phase2 step 1/5] Extracting events and entities (%d chapters)",
        len(parsed_files),
    )

    from src.extraction.batch_parallel import process_chapters_parallel

    max_parallel = config.get("concurrency", {}).get("max_parallel_chapters", 3)

    heartbeat.ping("Step 1: parallel extraction started")

    processed, failed = _run_core_extraction(
        parsed_files,
        grok_client,
        output_root,
        config,
        paths,
        max_parallel,
        heartbeat,
        logger,
    )

    logger.info(
        "[phase2 step 1/5] Core extraction complete: %d processed, %d failed",
        processed,
        failed,
    )

    # -----------------------------------------------------------------------
    # Step 4: Maps and Images extraction
    # -----------------------------------------------------------------------
    _extract_maps(output_root, config, logger)
    _extract_external_maps(base_dir, grok_client, paths, config, logger)
    _extract_images(output_root, config, logger)

    # -----------------------------------------------------------------------
    # Step 4b: Source-section enrichment (summary + operation + Grok/Wiki articles + media)
    # -----------------------------------------------------------------------
    _run_source_section_enrichment(output_root, grok_client, logger)

    # -----------------------------------------------------------------------
    # Step 5: Analysis
    # -----------------------------------------------------------------------
    _run_analysis(output_root, logger)

    # -----------------------------------------------------------------------
    # Done
    # -----------------------------------------------------------------------
    logger.info("Phase 2 complete: %d processed, %d failed", processed, failed)
    grok_client.log_cache_stats()
    from src.utils.validation_stats import write_validation_stats as _write_val_stats

    _write_val_stats(paths["output_root"])
    heartbeat.stop()

    # If batch mode, submit collected requests, wait, then re-run
    if (
        args.batch
        and grok_client._batch_collector
        and len(grok_client._batch_collector) > 0
    ):
        logger.info(
            "[phase2] Submitting %d requests to Grok Batch API",
            len(grok_client._batch_collector),
        )
        # Build descriptive batch name from processed books
        books = set()
        for pf in parsed_files:
            books.add(pf.parent.name)
        req_count = len(grok_client._batch_collector)
        batch_name = f"phase2-{'-'.join(sorted(books))}-{len(parsed_files)}files-{req_count}reqs"[
            :128
        ]
        batch_id = grok_client.submit_batch(batch_name=batch_name)
        if batch_id:
            logger.info("Batch complete! Re-running pipeline with cached results...")
            grok_client.batch_mode = False
            heartbeat = Heartbeat(timeout=300, label="Phase 2 (batch re-run)")
            heartbeat.start()
            heartbeat.ping("Batch re-run: step 1")

            processed, failed = _run_core_extraction(
                parsed_files,
                grok_client,
                output_root,
                config,
                paths,
                max_parallel,
                heartbeat,
                logger,
                skip_optional=True,
            )

            heartbeat.stop()
            logger.info(
                "[phase2] Batch re-run complete: %d processed, %d failed",
                processed,
                failed,
            )

            # Source-section enrichment now runs with cached batch results available
            # (grok_client is back in real-time mode), so the derive pass actually produces
            # output in batch mode (it is a no-op during the collection phase above).
            _run_source_section_enrichment(output_root, grok_client, logger)

            # Second batch cycle: optional extractors
            if any(
                config.get(f, {}).get("enabled", False)
                for f in [
                    "weather",
                    "equipment",
                    "logistics",
                    "casualties",
                    "supplemental_material",
                ]
            ):
                from src.utils.batch_api import BatchCollector

                grok_client.batch_mode = True
                grok_client._batch_collector = BatchCollector()
                logger.info("\nCollecting optional extractor requests for batch...")

                # Same scope as the guarded run — derive from parsed_files, not a
                # whole-corpus rglob (keeps --book/--expected-count honest).
                event_files = sorted(
                    {
                        pf.parent / pf.name.replace("-parsed.json", "-event.json")
                        for pf in parsed_files
                    }
                )
                event_files = [e for e in event_files if e.exists()]
                for event_file in event_files:
                    current_book.set(event_file.parent.name)
                    parsed_file = event_file.parent / event_file.name.replace(
                        "-event.json", "-parsed.json"
                    )
                    if not parsed_file.exists():
                        parsed_file = None
                    _extract_optional_entities(
                        event_file,
                        parsed_file,
                        grok_client,
                        paths,
                        config,
                        logger,
                    )

                batch_id2 = grok_client.submit_batch(
                    batch_name=f"{batch_name}-optional"
                )
                if batch_id2:
                    logger.info(
                        "Optional batch complete! Re-running with cached results..."
                    )
                    grok_client.batch_mode = False
                    for event_file in event_files:
                        current_book.set(event_file.parent.name)
                        parsed_file = event_file.parent / event_file.name.replace(
                            "-event.json", "-parsed.json"
                        )
                        if not parsed_file.exists():
                            parsed_file = None
                        _extract_optional_entities(
                            event_file,
                            parsed_file,
                            grok_client,
                            paths,
                            config,
                            logger,
                        )
                else:
                    grok_client.batch_mode = False
                    logger.info("No optional batch requests (all cached)")

    from src.utils.http_pool import close_session

    close_session()

    # Post-Phase-2 data validation (docs/current/PHASE2_DATA_VALIDATION.md). Runs on the FULLY
    # FINALIZED corpus (after any --batch re-run). Fail-safe: logs the LOUD result + surfaces
    # failures but never aborts the phase here (the standalone CLI / CI can hard-gate on exit code).
    try:
        from scripts.validate_phase2 import run as _validate_phase2

        rc = _validate_phase2(str(output_root), strict_enrichment=False)
        logger.info("Post-Phase-2 validation exit=%d (0=pass, 1=fail)", rc)
    except Exception as e:  # noqa: BLE001 - validation must not crash the phase
        logger.warning("Post-Phase-2 validation skipped: %s", e)

    # Write results for entrypoint notification
    _write_phase_results(output_root, processed, failed)


def _write_phase_results(output_root, processed, failed):
    """Write phase results JSON for entrypoint to read."""
    results_file = output_root / ".phase_results.json"
    entity_counts = {}
    for subdir in [
        "people",
        "people_groups",
        "places",
        "dates",
        "equipment",
        "weather",
        "logistics",
        "casualties",
        "maps",
        "supplemental",
    ]:
        d = output_root / subdir
        if d.exists():
            entity_counts[subdir] = len(
                [f for f in d.glob("*.json") if f.name != "index.json"]
            )
    results_file.write_text(
        json.dumps(
            {
                "processed": processed,
                "failed": failed,
                "entity_counts": entity_counts,
            }
        ),
        encoding="utf-8",
    )

    # Sanity check: warn if all optional extractors produced 0 with >5 events
    optional_types = ["equipment", "weather", "logistics", "casualties"]
    event_count = entity_counts.get("dates", 0)  # dates ~= event count proxy
    content_dir = output_root / "content"
    if content_dir.exists():
        event_count = len(list(content_dir.rglob("*-event.json")))
    if event_count > 5:
        all_zero = all(entity_counts.get(t, 0) == 0 for t in optional_types)
        if all_zero:
            import logging

            logging.getLogger(__name__).warning(
                "⚠ All optional extractors produced 0 entities with %d events — possible extraction failure",
                event_count,
            )

    # End-of-run per-entity-type persisted-count summary (OBSERV phase2.summary + a LOUD
    # phase2.summary.zero for any type at 0). Emitted HERE because this is the single final point
    # reached by ALL modes (incl. the batch retrieve re-run), so counts reflect the final corpus.
    import logging as _logging

    _log_phase2_summary(output_root, _logging.getLogger(__name__))

    # End-of-phase referential-integrity enforcement — AFTER all extraction (incl. any batch
    # re-run), so references are checked against the final corpus. Must never crash the phase.
    try:
        from src.utils.referential_integrity import run_for_phase as _ref_enforce

        _ref_enforce(output_root, phase="Phase 2")
    except Exception as _e:  # noqa: BLE001
        logger.warning("referential-integrity enforcement skipped: %s", _e)

    try:
        from src.utils.provenance_integrity import run_for_phase as _prov_enforce

        _prov_enforce(output_root, phase="Phase 2")
    except Exception as _e:  # noqa: BLE001 - enforcement must never crash a phase
        logger.warning("provenance-completeness enforcement skipped: %s", _e)


if __name__ == "__main__":
    main()
