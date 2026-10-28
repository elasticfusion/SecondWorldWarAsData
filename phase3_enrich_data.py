#!/usr/bin/env python3
"""
Enrich extracted data from external sources.

Runs enrichment for various entity types (people, places, etc.)
by searching external sources and merging additional data.
"""

import argparse
import json
import os
import re
from pathlib import Path
from typing import Optional

from src.extraction.enrich_groups import enrich_all_groups
from src.extraction.enrich_places import enrich_all_places
from src.extraction.places import link_parent_place_ids
from src.extraction.supplemental_advanced import enrich_bibliography
from src.grok_client import GrokClient
from src.utils.config import load_config
from src.utils.logger import setup_logging

import logging

logger = logging.getLogger(__name__)

# Strip C0 controls (\n, \r, etc.) from exception text before it enters a log line or the
# operator-facing source_stats error — prevents OBSERV log-line forging (repo convention).
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def _notify_enrichment_started() -> None:
    """Send SNS notification that enrichment is starting (downloads complete)."""
    if not os.environ.get("ECS_CONTAINER_METADATA_URI"):
        return  # Local mode
    try:
        import boto3

        topic_arn = os.environ.get("NOTIFICATION_TOPIC_ARN", "")
        if not topic_arn:
            return
        region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        book = os.environ.get("BOOK_NAME", "all")
        boto3.client("sns", region_name=region).publish(
            TopicArn=topic_arn,
            Subject="WWII Pipeline: Phase 3 enrichment in progress",
            Message=f"Enrichment started (downloads complete, API calls beginning).\nBook: {book}",
        )
    except Exception as e:  # noqa: BLE001 - best-effort notify, but DON'T go silent
        logger.warning("Could not send enrichment-started notification: %s", e)


def _update_lock_status(status: str) -> None:
    """Update the Phase 3 lock with current enrichment status (best-effort)."""
    if not os.environ.get("ECS_CONTAINER_METADATA_URI"):
        return  # Local mode — no lock
    try:
        import boto3

        region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        table_name = os.environ.get("CACHE_TABLE", "dev-wwii-api-cache")
        env_name = os.environ.get("ENV_NAME", "dev")
        table = boto3.resource("dynamodb", region_name=region).Table(table_name)
        table.update_item(
            Key={"cache_key": f"lock#{env_name}-wwii-phase3-enrich"},
            UpdateExpression="SET #s = :s",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": status},
        )
    except Exception as e:  # noqa: BLE001 - best-effort status, but DON'T go silent
        logger.warning("Could not update Phase 3 lock status '%s': %s", status, e)


# NOTE: people Grokipedia/Wikipedia enrichment MOVED TO PHASE 2 (people.py
# _enrich_person_phase2 -> enrich_biographies.enrich_person_from_sources). The former
# Phase-3 enrich_people_data() has been removed. Phase-3 people enrichment is OpenSERP-only
# (see the openserp_people step in main()). enrich_all_people remains in enrich_biographies
# for the module CLI + integration tests, but is intentionally NOT wired into this pipeline.


def enrich_groups_data(
    groups_dir: Path,
    grok_client: GrokClient,
    max_items: Optional[int] = None,
    max_workers: int = 6,
) -> int:
    """Enrich people groups with external data."""
    logger.info("[phase3 step 1/6] Enriching people groups")
    _update_lock_status("step 1/6: enriching people_groups")

    enriched = enrich_all_groups(
        groups_dir, grok_client, max_groups=max_items, max_workers=max_workers
    )
    logger.info(f"✓ Enriched {enriched} groups")
    return enriched


def _parse_phase3_args() -> argparse.Namespace:
    """Parse Phase 3 CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Enrich extracted data from external sources"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output"),
        help="Output directory containing entity subdirectories (default: output)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("cache/grok_cache"),
        help="Cache directory for Grok API (default: cache/grok_cache)",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        help="Maximum items per entity type to enrich (default: all)",
    )
    parser.add_argument(
        "--no-references",
        action="store_true",
        help="Don't follow references (faster, less complete)",
    )
    parser.add_argument(
        "--people-only",
        action="store_true",
        help="Only enrich people (skip other entity types)",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Set logging level (default: INFO)",
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Use xAI Batch API (50%% cost reduction, async processing)",
    )
    return parser.parse_args()


def _enrich_places_and_geocode(
    args, grok_client, max_workers, run_step, source_stats
) -> int:
    """Enrich places (hierarchy/names) then geocode coordinates (Nominatim -> hill ->
    Grok cascade). Geocoding is wrapped so it never aborts Phase 3. Returns enriched."""
    places_dir = args.output_dir / "places"
    enriched = run_step(
        "places",
        lambda: enrich_all_places(
            places_dir, grok_client, max_places=args.max_items, max_workers=max_workers
        ),
    )
    # Link parent_place_id after enrichment populates hierarchy
    link_parent_place_ids(places_dir)

    logger.info("[phase3 step 3/6] Geocoding places (coordinates)")
    _update_lock_status("step 3/6: geocoding places")
    try:
        from src.enrichment.hill_geocode import make_hill_geocoder
        from src.enrichment.nominatim_geocode import make_nominatim_geocoder
        from src.enrichment.places_grok_geocode import (
            cascade_geocoder,
            configure_elevation,
            geocode_place,
            geocode_places_dir,
        )

        geo_cache = args.cache_dir / "geocode"
        # T2-B (opt-in, default OFF): capture places.elevation_m from the DEM at resolved coords.
        _cfg = load_config()
        _places_cfg = _cfg.get("places", {}) if isinstance(_cfg, dict) else {}
        configure_elevation(
            bool(_places_cfg.get("capture_elevation", False)), geo_cache / "elevation"
        )
        cascade = cascade_geocoder(
            make_nominatim_geocoder(geo_cache / "nominatim"),
            make_hill_geocoder(geo_cache / "elevation"),
            geocode_place,  # Grok fallback for misses
        )
        geo_report = geocode_places_dir(
            places_dir,
            grok_client,
            write=True,
            limit=args.max_items or None,
            geocoder=cascade,
        )
        logger.info(
            "Geocoding: attempted %d, geocoded %d, not-found %d, low-conf %d, errors %d",
            geo_report.attempted,
            geo_report.geocoded,
            geo_report.not_found,
            geo_report.low_confidence,
            geo_report.errors,
        )
        # Q2 coverage observability: canonical machine-parseable rollup + anomalies (geocoder
        # service down = 0-of-N; a high error rate signals a degraded cascade).
        logger.info(
            "OBSERV geocode.summary attempted=%d geocoded=%d not_found=%d low_conf=%d errors=%d",
            geo_report.attempted,
            geo_report.geocoded,
            geo_report.not_found,
            geo_report.low_confidence,
            geo_report.errors,
        )
        if geo_report.attempted > 0 and geo_report.geocoded == 0:
            logger.warning(
                "OBSERV geocode.anomaly 0-of-%d geocoded (geocoder cascade unreachable?)",
                geo_report.attempted,
            )
        elif geo_report.errors and geo_report.errors >= max(
            1, geo_report.attempted // 2
        ):
            logger.warning(
                "OBSERV geocode.anomaly high-error-rate errors=%d of attempted=%d",
                geo_report.errors,
                geo_report.attempted,
            )
        source_stats["geocode"] = {
            "enriched": geo_report.geocoded,
            "status": "error" if geo_report.errors else "ok",
            "attempted": geo_report.attempted,
            "not_found": geo_report.not_found,
            "low_confidence": geo_report.low_confidence,
            "errors": geo_report.errors,
        }
        return enriched + geo_report.geocoded
    except Exception as e:  # noqa: BLE001 - geocoding must not abort Phase 3
        logger.error(
            "Geocoding step FAILED (places left un-geocoded): %s", e, exc_info=True
        )
        source_stats["geocode"] = {
            "enriched": 0,
            "status": "error",
            "error": str(e)[:300],
        }
        return enriched


def _run_openserp_enrichment(args, config, grok_client, run_step) -> int:
    """Run the OpenSERP enrichment step-group (people/equipment/groups/places/
    source-sections + optional external maps + metrics). Returns total enriched."""
    logger.info("[phase3 step 5/6] OpenSERP enrichment (people + equipment)")
    _update_lock_status("step 5/6: enriching openserp")
    from src.enrichment.openserp_enrichment import (
        enrich_equipment_with_openserp,
        enrich_groups_with_openserp,
        enrich_people_with_openserp,
        enrich_places_with_openserp,
        enrich_source_sections_with_openserp,
        reset_metrics as _reset_openserp_metrics,
        write_metrics as _write_openserp_metrics,
    )

    _reset_openserp_metrics()
    openserp_url = config.get("external_maps", {}).get(
        "openserp_url", "http://localhost:7001"
    )
    out = args.output_dir
    enriched = 0
    enriched += run_step(
        "openserp_people",
        lambda: enrich_people_with_openserp(
            out / "people", openserp_url, grok_client, args.max_items
        ),
    )
    enriched += run_step(
        "openserp_equipment",
        lambda: enrich_equipment_with_openserp(
            out / "equipment", openserp_url, grok_client, args.max_items
        ),
    )
    enriched += run_step(
        "openserp_groups",
        lambda: enrich_groups_with_openserp(
            out / "people_groups", openserp_url, grok_client, args.max_items
        ),
    )
    enriched += run_step(
        "openserp_places",
        lambda: enrich_places_with_openserp(
            out / "places", openserp_url, grok_client, args.max_items
        ),
    )
    enriched += run_step(
        "openserp_source_sections",
        lambda: enrich_source_sections_with_openserp(
            out / "source_section", openserp_url, grok_client, args.max_items
        ),
    )
    # External maps via live web search — MOVED FROM PHASE 2 so OpenSERP/NAT come up
    # exactly once (here). Imports map images for places via OpenSERP; the LOCAL
    # external_maps.yaml corpus import stays in Phase 2.
    ext_config = config.get("external_maps", {})
    if ext_config.get("enabled", False):
        from src.extraction.openserp_maps import import_openserp_maps

        enriched += run_step(
            "openserp_external_maps",
            lambda: import_openserp_maps(
                places_dir=out / "places",
                output_dir=out / "external_maps",
                grok_client=grok_client,
                max_places=ext_config.get("max_places") or args.max_items,
                search_limit=ext_config.get("search_limit", 50),
                openserp_url=openserp_url,
                page_timeout=ext_config.get("page_download_timeout", 10),
                image_timeout=ext_config.get("image_download_timeout", 30),
                image_storage_path=ext_config.get("image_storage_path"),
            ),
        )
        # M1: FOLLOW IN-DOCUMENT map links (author-curated maps linked from the narrative, e.g.
        # [Map VIII](maps/*.jpg)) -> map-as-source. Distinct from the OpenSERP web search above:
        # the link is already a direct image URL in the parsed doc's maps[]. Gated with the maps
        # feature; fail-safe.
        if ext_config.get("follow_in_doc_links", True):
            enriched += run_step(
                "in_doc_map_links",
                lambda: _ingest_in_doc_map_links(out, grok_client, ext_config),
            )
    # M3: link document photos to the PersonIDs they depict (caption-driven). Independent of the
    # maps feature; runs after people are written. Gated by images.link_people (default on).
    if config.get("images", {}).get("link_people", True):
        enriched += run_step(
            "image_person_link",
            lambda: _link_images_to_people(out, grok_client),
        )
    # M4: follow ALL links in parsed docs -> recognize media type -> relevancy-gate -> enqueue for
    # HUMAN review (no auto-ingestion). Gated by links.review_external (default on).
    if config.get("links", {}).get("review_external", True):
        enriched += run_step(
            "link_asset_review",
            lambda: _review_external_links(out, grok_client),
        )
    # M5: classify terrain/aerial document photos -> link them to the PlaceID they depict (creating
    # the place if described-but-new). Gated by images.link_places (default on).
    if config.get("images", {}).get("link_places", True):
        enriched += run_step(
            "image_place_link",
            lambda: _link_images_to_places(out, grok_client),
        )
    # M8: recognize links to OTHER in-corpus ibiblio volumes -> mark internal corpus-graph edges
    # (ULID-attributed) + QUEUE a not-yet-ingested cited document as another batch. Gated by
    # links.intra_corpus (default on).
    if config.get("links", {}).get("intra_corpus", True):
        enriched += run_step(
            "intra_corpus_links",
            lambda: _process_intra_corpus_links(out),
        )
    # Provenance: stamp a resolvable SourceSectionID onto every content event (+ sub-events) from
    # the committed source_section records, so entity -> event -> SECTION is a hard enforceable ID
    # chain (not filename-convention only). Always runs; fail-safe.
    enriched += run_step(
        "event_provenance_stamp",
        lambda: _stamp_event_provenance(out),
    )
    # Durable OpenSERP effectiveness/health metrics -> output/metrics/openserp_metrics.json
    _write_openserp_metrics(out)
    return enriched


def _stamp_event_provenance(out) -> int:
    """Stamp SourceSectionID onto content events from source_section records. Fail-safe -> 0."""
    from src.extraction.event_provenance import stamp_event_source_sections

    return stamp_event_source_sections(out)


def _load_parsed_docs(out) -> list:
    """Load all parsed.json docs under out/content (shared by the M1 map-link + M3 image-person
    steps). Fail-safe: returns [] on any error."""
    import json

    docs = []
    try:
        for pf in (out / "content").rglob("*-parsed.json"):
            try:
                docs.append(json.loads(pf.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        return []
    return docs


def _link_images_to_people(out, grok_client) -> int:
    """M3: resolve captioned document photos to PersonIDs and link them into people.images[].
    Returns the count linked. Fail-safe."""
    from src.extraction.image_person_link import link_images_to_people

    docs = _load_parsed_docs(out)
    if not docs:
        return 0
    summary = link_images_to_people(docs, out / "people", grok_client=grok_client)
    logging.getLogger("root").info(
        "OBSERV phase3.step name=image_person_link images=%d linked=%d skipped=%d status=ok",
        summary.get("images", 0),
        summary.get("linked", 0),
        summary.get("skipped", 0),
    )
    return int(summary.get("linked", 0))


def _doc_link_content(doc) -> str:
    """Assemble link-bearing text for one parsed doc: paragraph bodies (inline links intact) plus
    already-parsed footnotes rendered as markdown links so their targets are extracted too.
    """
    parts = [
        p.get("text", "") for p in (doc.get("paragraphs") or []) if isinstance(p, dict)
    ]
    for fn in doc.get("footnotes") or []:
        if isinstance(fn, dict) and fn.get("url"):
            parts.append(f"[{fn.get('text', '') or fn.get('number', '')}]({fn['url']})")
    return "\n".join(parts)


def _review_external_links(out, grok_client) -> int:
    """M4: extract + recognize + relevancy-gate every link in the parsed docs and enqueue the kept
    ones for human review (carrying the originating SourceSectionID ULID). Returns the count
    enqueued. Fail-safe."""
    from src.extraction.link_assets import enqueue_doc_links

    docs = _load_parsed_docs(out)
    if not docs:
        return 0
    book_to_section = _book_section_index(out)  # CRITICAL: ULID attribution per doc
    totals = {"links": 0, "enqueued": 0, "dropped": 0, "anchors": 0}
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        base = doc.get("_source_url", "") or ""
        book = str(doc.get("book", ""))
        s = enqueue_doc_links(
            _doc_link_content(doc),
            base,
            book,
            grok_client=grok_client,
            source_section_id=book_to_section.get(book.strip().lower(), ""),
        )
        for k in totals:
            totals[k] += int(s.get(k, 0))
    logging.getLogger("root").info(
        "OBSERV phase3.step name=link_asset_review links=%d enqueued=%d dropped=%d anchors=%d "
        "status=ok",
        totals["links"],
        totals["enqueued"],
        totals["dropped"],
        totals["anchors"],
    )
    return totals["enqueued"]


def _link_images_to_places(out, grok_client) -> int:
    """M5: classify terrain/aerial document photos and link each to the PlaceID it depicts (creating
    the place when described-but-new). Returns the count linked. Fail-safe."""
    from src.extraction.image_place_link import link_images_to_places

    docs = _load_parsed_docs(out)
    if not docs:
        return 0
    summary = link_images_to_places(docs, out / "places", grok_client=grok_client)
    logging.getLogger("root").info(
        "OBSERV phase3.step name=image_place_link terrain=%d linked=%d created=%d skipped=%d "
        "status=ok",
        summary.get("terrain", 0),
        summary.get("linked", 0),
        summary.get("created", 0),
        summary.get("skipped", 0),
    )
    return int(summary.get("linked", 0))


def _process_intra_corpus_links(out) -> int:
    """M8: recognize links to OTHER in-corpus ibiblio volumes, record internal corpus-graph edges
    (ULID-attributed), and queue a not-yet-ingested cited document as another batch. Returns the
    count of documents queued. Fail-safe."""
    from src.extraction.intra_corpus import (
        _corpus_volumes_present,
        process_doc_links,
    )

    docs = _load_parsed_docs(out)
    if not docs:
        return 0
    book_to_section = _book_section_index(out)
    corpus_volumes = _corpus_volumes_present(out)
    queued: set = set()
    totals = {"edges": 0, "anchors": 0, "queued": 0, "skipped": 0}
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        s = process_doc_links(
            _doc_link_content(doc),
            doc.get("_source_url", "") or "",
            str(doc.get("book", "")),
            out=out,
            book_to_section=book_to_section,
            corpus_volumes=corpus_volumes,
            queued=queued,
        )
        totals["edges"] += len(s.get("edges", []))
        totals["anchors"] += int(s.get("anchors", 0))
        totals["queued"] += int(s.get("queued", 0))
        totals["skipped"] += int(s.get("skipped", 0))
    logging.getLogger("root").info(
        "OBSERV phase3.step name=intra_corpus_links edges=%d anchors=%d queued=%d skipped=%d "
        "status=ok",
        totals["edges"],
        totals["anchors"],
        totals["queued"],
        totals["skipped"],
    )
    return totals["queued"]


def _book_section_index(out):
    """book -> SourceSectionID ULID map (reuse the M5 index builder). Fail-safe -> {}."""
    try:
        from src.extraction.image_place_link import _build_book_section_index

        return _build_book_section_index(out)
    except Exception:  # noqa: BLE001
        return {}


def _ingest_in_doc_map_links(out, grok_client, ext_config) -> int:
    """Load parsed docs' maps[] (in-document map links) and ingest each via map-as-source. Returns
    the count recycled. Fail-safe."""
    from src.extraction.map_link_source import ingest_in_doc_map_links

    docs = _load_parsed_docs(out)
    if not docs:
        return 0
    summary = ingest_in_doc_map_links(
        docs,
        grok_client,
        image_timeout=ext_config.get("image_download_timeout", 30),
    )
    logging.getLogger("root").info(
        "OBSERV phase3.step name=in_doc_map_links docs=%d links=%d recycled=%d "
        "deduped=%d skipped=%d status=ok",
        len(docs),
        summary.get("links", 0),
        summary.get("recycled", 0),
        summary.get("deduped", 0),
        summary.get("skipped", 0),
    )
    return int(summary.get("recycled", 0))


def _run_batch_rerun(args, config, grok_client) -> int:
    """Re-run the batch-cached enrichers after a batch submission completes. Each enricher is
    wrapped per-source fail-safe (mirroring main()'s _run_step) so one raising enricher can neither
    abort the re-run nor the phase teardown. People enrichment moved to Phase 2 (not re-run here).
    Returns the count enriched in this re-run pass."""
    if args.people_only:
        return 0

    def _step(name: str, fn) -> int:
        try:
            n = fn()
            logger.info(
                "OBSERV phase3.batch_step name=%s enriched=%d status=ok", name, n
            )
            return n
        except (
            Exception
        ) as e:  # noqa: BLE001 - a failing batch step must not abort the re-run
            logger.error(
                "OBSERV phase3.batch_step name=%s enriched=0 status=error error=%s",
                name,
                str(e)[:120],
            )
            return 0

    groups_dir = args.output_dir / "people_groups"
    places_dir = args.output_dir / "places"
    bib_dir = args.output_dir / "bibliography"
    supplemental_config = config.get("supplemental_material", {})
    total = 0
    total += _step(
        "people_groups",
        lambda: enrich_groups_data(groups_dir, grok_client, max_items=args.max_items),
    )
    total += _step(
        "places",
        lambda: enrich_all_places(places_dir, grok_client, max_places=args.max_items),
    )
    _step("link_parent_place_ids", lambda: link_parent_place_ids(places_dir) or 0)
    total += _step(
        "bibliography",
        lambda: enrich_bibliography(bib_dir, supplemental_config, grok_client),
    )

    from src.enrichment.bibliography_resolver import resolve_bibliography_dir

    resolve_config = {
        "nara_api_key": config.get("api", {}).get("nara_api_key"),
        "search_gutenberg": supplemental_config.get("search_gutenberg", True),
        "search_archive_org": supplemental_config.get("search_archive_org", True),
        "use_openserp": supplemental_config.get("use_openserp", False),
        "openserp_url": config.get("external_maps", {}).get(
            "openserp_url", "http://localhost:7001"
        ),
    }
    total += _step(
        "bibliography_resolve",
        lambda: resolve_bibliography_dir(
            bib_dir, grok_client, resolve_config, max_items=args.max_items
        ).get("resolved", 0),
    )
    return total


def main():
    """Main entry point."""
    args = _parse_phase3_args()

    # Load config for logging settings
    base_dir = Path(__file__).parent
    config = load_config(base_dir / "config.yaml")
    log_config = config.get("logging", {})

    # Setup logging with file output
    logger = setup_logging(
        level=args.log_level,
        log_file=log_config.get("file"),
        console=log_config.get("console", True),
    )

    if not args.output_dir.exists():
        logger.error(f"Output directory not found: {args.output_dir}")
        return 1

    logger.info("Phase 3: starting enrichment (%s)", args.output_dir)

    # NOTE: Phase 3 is intentionally a WHOLE-CORPUS, run-until-done job — it
    # geocodes every place, fetches weather for every event, resolves every
    # citation/archive reference, etc. Unlike Phase 2 (which is scoped to one book
    # and must not silently fan out to the whole corpus), Phase 3 enriching the
    # entire corpus is the entire point, so there is NO scoped-intent to protect and
    # NO corpus-size ceiling here. Its safety against runaway cost is structural:
    # it processes per entity type with an optional --max-items cap and SKIPS
    # already-enriched records (idempotent + resumable), so re-runs converge and a
    # partial run simply resumes. (A size tripwire here aborted every legitimate
    # single-book run, since one book easily exceeds 50 total entity records.)

    from src.utils.validation_stats import reset_stats as _reset_val_stats

    _reset_val_stats()

    # Notify: enrichment is starting (downloads complete, real work beginning)
    _notify_enrichment_started()

    if args.max_items:
        logger.info(f"Limiting to {args.max_items} items per entity type")
    if args.no_references:
        logger.info("Reference following disabled")

    # Initialize Grok client
    grok_client = GrokClient(args.cache_dir, batch_mode=args.batch)
    if args.batch:
        logger.info(
            "Batch mode enabled — collecting requests for xAI Batch API (50%% off)"
        )

    total_enriched = 0
    max_workers = config.get("concurrency", {}).get("max_enrichment_workers", 6)

    # C3: per-source enrichment stats so "complete" reflects partial success +
    # previously-swallowed failures surface to the operator (via .phase_results.json
    # -> completion notification -> email + Slack). C2: a source that throws is
    # LOGGED + recorded as errored, never silently swallowed and never aborts the
    # rest of Phase 3.
    source_stats: dict = {}

    def _run_step(name: str, fn) -> int:
        """Run one enrichment source; record enriched/status/error. Returns the
        enriched count (0 on error). Never raises — a failed source is captured,
        logged, and surfaced, not swallowed. Emits OBSERV phase3.step per source so the
        whole Phase-3 hand-off (N enriched / status / error) is visible in one query."""
        try:
            result = fn()
            count = (
                result
                if isinstance(result, int)
                else getattr(result, "geocoded", 0) or 0
            )
            source_stats[name] = {"enriched": count, "status": "ok"}
            try:
                logger.info(
                    "OBSERV phase3.step name=%s enriched=%d status=ok", name, count
                )
            except Exception:  # noqa: BLE001 - observability never breaks the step
                pass
            return count
        except Exception as e:  # noqa: BLE001 - capture + surface, don't abort Phase 3
            logger.error("Enrichment source '%s' FAILED: %s", name, e, exc_info=True)
            source_stats[name] = {
                "enriched": 0,
                "status": "error",
                "error": _CTRL.sub(" ", str(e))[:300],
            }
            try:
                logger.info(
                    "OBSERV phase3.step name=%s enriched=0 status=error error=%s",
                    name,
                    _CTRL.sub(" ", str(e))[:120],
                )
            except Exception:  # noqa: BLE001
                pass
            return 0

    # Enrich people — MOVED TO PHASE 2. All Grokipedia/Wikipedia people enrichment
    # (bio text + Wikipedia portrait images + award citations) is now committed
    # natively by the Phase-2 people extractor (src/extraction/people.py ->
    # enrich_biographies.enrich_person_from_sources). Phase 3 owns only OTHER external
    # people enrichment (OpenSERP, below). The former '[phase3 step 1/6] Enriching
    # people' call (enrich_people_data/enrich_all_people) is intentionally removed;
    # those functions remain defined for standalone/CLI + test use.

    # Enrich people groups — STRUCTURED org-history facts (unit_type, nationality,
    # commanding officers, operations) via a Grok extract_json, promoted into spec
    # fields. This is the groups analogue of place hierarchy/names: structured
    # reference facts, NOT descriptive Grokipedia/Wikipedia text+images. The latter
    # (wikipedia_url/extract/images) moved to PHASE 2 (people_groups extractor →
    # enrich_group_from_wikipedia). This structured-facts enrichment therefore stays
    # in Phase 3, mirroring the rule that place hierarchy/names remain in Phase 3.
    if not args.people_only:
        groups_dir = args.output_dir / "people_groups"
        total_enriched += _run_step(
            "people_groups",
            lambda: enrich_groups_data(
                groups_dir,
                grok_client,
                max_items=args.max_items,
                max_workers=max_workers,
            ),
        )

    # Enrich places
    if not args.people_only:
        total_enriched += _enrich_places_and_geocode(
            args, grok_client, max_workers, _run_step, source_stats
        )

    # Enrich bibliography (ISBN, copyright, archive URLs)
    if not args.people_only:
        logger.info("[phase3 step 4/6] Enriching bibliography")
        _update_lock_status("step 4/6: enriching bibliography")
        bib_dir = args.output_dir / "bibliography"
        supplemental_config = config.get("supplemental_material", {})
        total_enriched += _run_step(
            "bibliography",
            lambda: enrich_bibliography(bib_dir, supplemental_config, grok_client),
        )

        # Resolve bibliography sources (NARA, Archive.org, LOC)
        from src.enrichment.bibliography_resolver import resolve_bibliography_dir

        resolve_config = {
            "nara_api_key": config.get("api", {}).get("nara_api_key"),
            "search_gutenberg": supplemental_config.get("search_gutenberg", True),
            "search_archive_org": supplemental_config.get("search_archive_org", True),
            "use_openserp": supplemental_config.get("use_openserp", False),
            "openserp_url": config.get("external_maps", {}).get(
                "openserp_url", "http://localhost:7001"
            ),
        }
        total_enriched += _run_step(
            "bibliography_resolve",
            lambda: resolve_bibliography_dir(
                bib_dir, grok_client, resolve_config, max_items=args.max_items
            ).get("resolved", 0),
        )

    # Equipment Wikipedia enrichment (text + images) now runs in PHASE 2, committed
    # natively by the extractor (src/extraction/equipment.py). It is intentionally NOT a
    # Phase-3 step anymore: Phase 2 owns ALL Grokipedia/Wikipedia enrichment; Phase 3
    # owns only OTHER external enrichment (geocoding, NOAA, OpenSERP, NARA).

    # Groups Wikipedia enrichment (text + images) now runs in PHASE 2, committed
    # natively by the extractor (src/extraction/people_groups.py). It is intentionally
    # NOT a Phase-3 step anymore: Phase 2 owns ALL Grokipedia/Wikipedia enrichment;
    # Phase 3 owns only OTHER external enrichment (geocoding, NOAA, OpenSERP, NARA).

    # OpenSERP enrichment (images, academic sources) — requires OpenSERP running
    if not args.people_only and config.get("supplemental_material", {}).get(
        "use_openserp", False
    ):
        total_enriched += _run_openserp_enrichment(args, config, grok_client, _run_step)

    # NOAA weather enrichment (observed data to supplement Open-Meteo)
    noaa_token = config.get("api", {}).get("noaa_api_token", "")
    if not args.people_only and noaa_token:
        logger.info("[phase3 step 6/6] NOAA weather enrichment")
        _update_lock_status("step 6/6: enriching weather (NOAA)")
        from src.enrichment.noaa_weather import enrich_weather_with_noaa

        weather_dir = args.output_dir / "weather"
        if weather_dir.exists():
            total_enriched += _run_step(
                "noaa_weather",
                lambda: enrich_weather_with_noaa(
                    weather_dir, noaa_token, args.max_items or 0
                ),
            )

    logger.info("Phase 3 complete: %d total items enriched", total_enriched)
    grok_client.log_cache_stats()

    # If batch mode, submit collected requests, wait, then re-run
    if (
        args.batch
        and grok_client._batch_collector
        and len(grok_client._batch_collector) > 0
    ):
        logger.info(
            "Submitting %d requests to xAI Batch API (50%% off)...",
            len(grok_client._batch_collector),
        )
        book = os.environ.get("BOOK_NAME", "all")
        batch_id = grok_client.submit_batch(
            batch_name=f"phase3-{book}-{len(grok_client._batch_collector)}reqs"[:128]
        )
        if batch_id:
            logger.info("Batch complete! Re-running enrichment with cached results...")
            grok_client.batch_mode = False
            # Accumulate the re-run into a SEPARATE counter (do NOT reset total_enriched — that
            # discarded the first-pass tally and misreported completion). Wrap the whole re-run in
            # a fail-safe so a raised enricher can never skip close_session / results-write /
            # validation / referential-integrity below (the H3 fail-safety gap).
            batch_enriched = 0
            try:
                batch_enriched = _run_batch_rerun(args, config, grok_client)
            except (
                Exception
            ) as e:  # noqa: BLE001 - batch re-run must never abort the phase teardown
                logger.error(
                    "Batch re-run FAILED (continuing to teardown): %s", e, exc_info=True
                )
            total_enriched += batch_enriched
            logger.info(
                "[phase3] Batch re-run complete: %d items enriched (phase total %d)",
                batch_enriched,
                total_enriched,
            )

    from src.utils.http_pool import close_session

    close_session()

    # Write results for entrypoint notification
    results_file = args.output_dir / ".phase_results.json"
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
        d = args.output_dir / subdir
        if d.exists():
            entity_counts[subdir] = len(
                [f for f in d.glob("*.json") if f.name != "index.json"]
            )
    errored_sources = sorted(
        n for n, s in source_stats.items() if s.get("status") == "error"
    )
    if errored_sources:
        logger.error(
            "Phase 3: %d enrichment source(s) FAILED: %s",
            len(errored_sources),
            ", ".join(errored_sources),
        )
    results_file.write_text(
        json.dumps(
            {
                "enriched": total_enriched,
                "entity_counts": entity_counts,
                # C3: per-source stats + explicit failure list so "complete"
                # reflects partial success and previously-swallowed failures reach
                # the operator via the completion notification (email + Slack).
                "source_stats": source_stats,
                "errored_sources": errored_sources,
            }
        ),
        encoding="utf-8",
    )

    from src.utils.validation_stats import write_validation_stats as _write_val_stats

    _write_val_stats(args.output_dir)
    # Referential-integrity repair + validate_phase3:
    #   • ECS/S3 run (S3_BUCKET set): do NOT run here. The local tree is synced to S3 AFTER this
    #     process exits (ecs_entrypoint._final_sync), which would clobber any S3-side repair, and
    #     this process's local tree lacks output/content (events) -> false EventID danglers. It runs
    #     DURABLY post-sync in ecs_entrypoint._phase3_durable_referential_fix (complete S3 corpus).
    #   • Pure-local/direct run (no S3_BUCKET): run it HERE — the local output tree is complete and
    #     authoritative (no trailing sync to clobber it), so a developer invoking the script directly
    #     still gets enforcement + validation.
    import os as _os

    if not _os.environ.get("S3_BUCKET"):
        try:
            from src.utils.referential_integrity import run_for_phase as _ref_enforce

            _ref_enforce(args.output_dir, phase="Phase 3 (local)")
        except Exception as _e:  # noqa: BLE001 - enforcement must never crash a phase
            logger.warning("referential-integrity enforcement skipped: %s", _e)
        try:
            from src.utils.provenance_integrity import run_for_phase as _prov_enforce

            _prov_enforce(args.output_dir, phase="Phase 3 (local)")
        except Exception as _e:  # noqa: BLE001 - enforcement must never crash a phase
            logger.warning("provenance-completeness enforcement skipped: %s", _e)
        try:
            from scripts.validate_phase3 import run as _validate_phase3

            rc = _validate_phase3(str(args.output_dir), strict_coverage=False)
            logger.info("Post-Phase-3 validation exit=%d (0=pass, 1=fail)", rc)
        except Exception as _e:  # noqa: BLE001 - validation must not crash a phase
            logger.warning("Post-Phase-3 validation skipped: %s", _e)
    return 0


if __name__ == "__main__":
    exit(main())
