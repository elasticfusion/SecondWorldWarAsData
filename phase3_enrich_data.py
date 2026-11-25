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
from typing import Optional, Tuple

from src.extraction.enrich_groups import enrich_all_groups
from src.extraction.enrich_places import enrich_all_places
from src.extraction.places import link_parent_place_ids
from src.extraction.supplemental_advanced import enrich_bibliography
from src.grok_client import BatchModeCollecting, GrokClient
from src.utils.batch_pending import Pending
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
    # Link parent_place_id after enrichment populates hierarchy. Wrapped in run_step (like the
    # batch-rerun path) so a raise here can't abort the rest of Phase 3 — it is surfaced + counted.
    run_step("link_parent_places", lambda: link_parent_place_ids(places_dir) or 0)

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
            "enriched": _stat_enriched(source_stats, "geocode") + geo_report.geocoded,
            # per-place errors are RECORD failures: those places stay un-geocoded and are
            # retried next run, so they do not fail the step (only a raise does, owner
            # 2026-11-18); the count is shown in the completion summary
            "status": "ok",
            "attempted": geo_report.attempted,
            "not_found": geo_report.not_found,
            "low_confidence": geo_report.low_confidence,
            "errors": geo_report.errors,
        }
        return enriched + geo_report.geocoded
    except BatchModeCollecting as e:
        # places were queued for the batch: the step is pending; what WAS geocoded counts
        done = _stat_enriched(source_stats, "geocode") + getattr(e, "geocoded", 0)
        source_stats["geocode"] = {"enriched": done, "status": "pending"}
        return enriched + getattr(e, "geocoded", 0)
    except Exception as e:  # noqa: BLE001 - geocoding must not abort Phase 3
        logger.error(
            "Geocoding step FAILED (places left un-geocoded): %s", e, exc_info=True
        )
        source_stats["geocode"] = _raised_step_stats(e)
        return enriched


def _run_document_media_steps(args, config, grok_client, run_step) -> int:
    """Maps + images that are IN the source document. Deliberately NOT inside the OpenSERP group
    and NOT gated by external_maps.enabled / use_openserp: those flags control the paid web search;
    gating document media with them silently dropped every in-doc map (and would skip the
    deferred map vision). Returns the count produced."""
    out = args.output_dir
    ext_config = config.get("external_maps", {})
    produced = 0
    # M1: IN-DOCUMENT map links (author-curated maps linked from the narrative, e.g.
    # [Map VIII](maps/*.jpg)) -> Map entities ALWAYS (Phase 2 skips these as images, so not
    # persisting them would lose them); the paid vision read of what each shows (stored on the Map
    # record) stays behind external_maps.recycle_in_doc_maps. Fail-safe.
    produced += run_step(
        "in_doc_map_links",
        lambda: _ingest_in_doc_map_links(out, grok_client, ext_config),
    )
    # Deferred map-vs-image vision: Phase 2 marks non-ibiblio (ambiguous) images pending_vision;
    # they MUST be decided here (maps become Map entities). Never skipped; leftovers alert.
    produced += run_step(
        "image_map_vision",
        lambda: _resolve_pending_image_map_vision(out, grok_client, config),
    )
    return produced


def _run_openserp_enrichment(args, config, grok_client, run_step) -> int:
    """Run the OpenSERP enrichment step-group (people/equipment/groups/places/
    source-sections + optional external maps + metrics): web search only. Returns total
    enriched."""
    logger.info("[phase3 step 5/6] OpenSERP enrichment (people + equipment)")
    _update_lock_status("step 5/6: enriching openserp")
    try:
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
    except (
        Exception
    ) as e:  # noqa: BLE001 - a scaffolding failure must not abort the phase
        logger.warning(
            "OBSERV phase3.openserp_skipped reason=%s status=warn "
            "(import/reset failed — OpenSERP enrichment group skipped)",
            type(e).__name__,
        )
        return 0
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
            out, openserp_url, grok_client, args.max_items
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
    # Durable OpenSERP effectiveness/health metrics -> output/metrics/openserp_metrics.json
    try:
        _write_openserp_metrics(out)
    except Exception as e:  # noqa: BLE001 - metrics write must not abort the phase
        logger.warning(
            "OBSERV phase3.openserp_metrics_write_failed reason=%s status=warn",
            type(e).__name__,
        )
    return enriched


def _run_document_link_steps(args, config, grok_client, run_step) -> int:
    """Steps that work on the book's own documents and records, NOT web search: they run whether
    or not OpenSERP is on or reachable (task 1.4; they used to sit inside the OpenSERP group, so
    `use_openserp: false` or a failed OpenSERP import silently skipped them and the
    SourceSectionID stamp). Each keeps its own config gate. Returns the count produced.
    """
    out = args.output_dir
    enriched = 0
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
    # M8: index the links to OTHER ibiblio volumes (ULID-attributed citations + the list of cited
    # volumes to ingest; nothing fetched or uploaded). Gated by links.intra_corpus (default on).
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
    return enriched


def _stamp_event_provenance(out) -> int:
    """Stamp SourceSectionID onto content events from source_section records. Fail-safe -> 0."""
    from src.extraction.event_provenance import stamp_event_source_sections

    return stamp_event_source_sections(out)


def _load_parsed_docs(out, unreadable_books=None) -> list:
    """Load all parsed.json docs under out/content (shared by the M1 map-link + M3 image-person
    steps). ``unreadable_books``: a set that receives the book (``content/<book>/``) of each parsed
    file that could not be read. Fail-safe: returns [] on any error."""
    import json
    import logging

    log = logging.getLogger(__name__)
    docs = []
    unreadable = 0
    try:
        from src.utils.entity_records import load_record

        for pf in (out / "content").rglob("*-parsed.json"):
            # a corrupt parsed doc is alerted and holds the book (entity_records.load_record)
            doc = load_record(pf, "parsed_docs")
            if doc is None:
                unreadable += 1
                if unreadable_books is not None:
                    rel = pf.relative_to(out / "content").parts
                    unreadable_books.add(rel[0] if len(rel) > 1 else "")
            else:
                docs.append(doc)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "OBSERV phase3.parsed_docs_scan_failed err=%s status=error",
            type(exc).__name__,
        )
        return []
    if unreadable:
        log.warning(
            "OBSERV phase3.parsed_docs_load loaded=%d unreadable=%d status=warn",
            len(docs),
            unreadable,
        )
    if not docs:
        _warn_no_parsed_docs(out)
    return docs


def _warn_no_parsed_docs(out) -> None:
    """No parsed documents are available: every step that reads the source document (in-document
    maps, image->person links, link review, ...) will silently produce nothing. Alert once.
    """
    from src.utils.observe import observe_warn_counted

    observe_warn_counted(
        "phase3",
        "no_parsed_docs",
        key=("no_parsed_docs",),
        every=10**9,
        note=(
            f"no *-parsed.json under {out}/content, so in-document maps, image-person links and "
            "link review are SKIPPED; Phase 3 must download output/content/ (it does for book-"
            "scoped and unscoped runs) - check the S3 sync in this task's log and re-run Phase 3"
        ),
        out=str(out),
    )


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
    totals = {
        "links": 0,
        "enqueued": 0,
        "dropped": 0,
        "undecided": 0,
        "failed": 0,
        "anchors": 0,
    }
    pending = (
        Pending()
    )  # batch rounds: every doc queues its links' requests, one raise at the end
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        base = doc.get("_source_url", "") or ""
        book = str(doc.get("book", ""))
        s = pending.call(
            enqueue_doc_links,
            _doc_link_content(doc),
            base,
            book,
            grok_client=grok_client,
            source_section_id=book_to_section.get(book.strip().lower(), ""),
        )
        for k in totals:
            totals[k] += int((s or {}).get(k, 0))
    logging.getLogger("root").info(
        "OBSERV phase3.step name=link_asset_review links=%d enqueued=%d dropped=%d undecided=%d failed=%d anchors=%d "
        "status=%s",
        totals["links"],
        totals["enqueued"],
        totals["dropped"],
        totals["undecided"],
        totals["failed"],
        totals["anchors"],
        "warn" if totals["failed"] else "ok",
    )
    pending.raise_if_queued()  # batch rounds: the step is pending, the next pass has the answers
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
    """M8: index the links to OTHER ibiblio volumes (src/extraction/corpus_links): per book, the
    volume it is and every volume it cites (ULID-attributed, with chapter and page), then the
    ingestion list of cited volumes not yet in the corpus. Nothing is fetched or uploaded (owner
    2026-11-24). Returns the number of books indexed. Fail-safe."""
    from src.extraction import corpus_links
    from src.extraction.intra_corpus import _corpus_volumes_present

    unreadable: set = set()
    docs = _load_parsed_docs(out, unreadable)
    if not docs:
        return 0
    books, totals = _citations_by_book(docs, _book_section_index(out))
    for book in unreadable & set(books):
        books[book][
            "error"
        ] = True  # a chapter is missing: do not rewrite from the rest
    indexed = sum(
        _index_book(corpus_links, book, entry) for book, entry in books.items()
    )
    listing = corpus_links.build_ingest_list(_corpus_volumes_present(out))
    logging.getLogger("root").info(
        "OBSERV phase3.step name=intra_corpus_links books=%d edges=%d anchors=%d cited=%d "
        "volumes_pending=%d status=ok",
        indexed,
        totals["edges"],
        totals["anchors"],
        totals["cited"],
        listing["pending"],
    )
    return indexed


def _citations_by_book(docs, book_to_section) -> tuple:
    """Each parsed document's cross-volume edges, grouped by book: (books, totals)."""
    from src.extraction.intra_corpus import process_doc_links

    books: dict = {}  # book -> {"source_url", "chapters", "error"}
    totals = {"edges": 0, "anchors": 0, "cited": 0}
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        book = str(doc.get("book", ""))
        source_url = doc.get("_source_url", "") or ""
        s = process_doc_links(
            _doc_link_content(doc), source_url, book, book_to_section=book_to_section
        )
        entry = books.setdefault(
            book, {"source_url": source_url, "chapters": [], "error": False}
        )
        entry["source_url"] = entry["source_url"] or source_url
        entry["chapters"].append(
            {
                "chapter": doc.get("chapter_number"),  # Phase 1's id (chapterN[a-z])
                "chapter_title": doc.get("chapter_title"),
                "edges": s["edges"],
            }
        )
        entry["error"] = entry["error"] or bool(s.get("error"))
        totals["edges"] += len(s["edges"])
        totals["anchors"] += int(s.get("anchors", 0))
        totals["cited"] += int(s.get("cited", 0))
    return books, totals


def _index_book(corpus_links, book: str, entry: dict) -> int:
    """Record one book's citations (1), or skip it (0): no name, or a document whose links could
    not be read - rewriting now would drop them, so its last complete index stays."""
    if not book:
        return 0
    if entry["error"]:
        logger.warning(
            "OBSERV corpus_links.book_skipped book=%r status=warn - a document's links could "
            "not be read, so this book's citation index is not rewritten this run; see the "
            "OBSERV intra_corpus.error line and fix the parsed document",
            book[:80],
        )
        return 0
    corpus_links.record_book(book, entry["source_url"], entry["chapters"])
    return 1


def _book_section_index(out):
    """book -> SourceSectionID ULID map (reuse the M5 index builder). Fail-safe -> {}."""
    try:
        from src.extraction.image_place_link import _build_book_section_index

        return _build_book_section_index(out)
    except Exception:  # noqa: BLE001
        return {}


def _recycle_in_doc_maps(ext_config) -> bool:
    """Recycle in-document maps as map-as-source? Its OWN flag: external_maps.enabled also turns on
    the paid OpenSERP web search, which must stay independent of reading the maps already in the
    document. Default off (cost); config.yaml turns it on."""
    return bool(
        ext_config.get("recycle_in_doc_maps", False)
        and ext_config.get("follow_in_doc_links", True)
    )


def _ingest_in_doc_map_links(out, grok_client, ext_config) -> int:
    """Persist parsed docs' maps[] (in-document map links) as Map entities - ALWAYS - and, only when
    external_maps.recycle_in_doc_maps, read each map not read yet with Grok vision and store what it
    shows on its Map record (src/extraction/map_link_source.read_in_doc_maps; one paid vision call
    per map, never repeated). Returns the number of maps read. Fail-safe.
    """
    from src.extraction.map_link_source import persist_map_entities, read_in_doc_maps

    docs = _load_parsed_docs(out)
    if not docs:
        return 0
    summary: dict = {}
    read = _recycle_in_doc_maps(ext_config)
    pending = Pending()  # batch rounds: both calls queue their own requests
    # Step 5a FIRST: the Map records (URL-keyed, cross-chapter footprint), so vision has a record
    # to store its answer on
    pending.call(
        persist_map_entities,
        docs,
        out / "maps",
        download=ext_config.get("download", True),
        output_root=out,
        grok_client=grok_client,
    )
    if read:
        summary = (
            pending.call(
                read_in_doc_maps,
                docs,
                grok_client,
                out / "maps",
                image_timeout=ext_config.get("image_download_timeout", 30),
                allowed_hosts=tuple(ext_config.get("allowed_hosts") or ()),
                max_per_doc=int(ext_config.get("max_recycled_per_doc", 50)),
            )
            or {}
        )
    logging.getLogger("root").info(
        "OBSERV phase3.step name=in_doc_map_links docs=%d vision=%s links=%d read=%d "
        "depicted=%d already=%d skipped=%d status=ok",
        len(docs),
        ("on" if read else "off(external_maps.recycle_in_doc_maps=false)"),
        summary.get("links", 0),
        summary.get("read", 0),
        summary.get("depicted", 0),
        summary.get("already", 0),
        summary.get("skipped", 0),
    )
    pending.raise_if_queued()  # batch rounds: the step is pending, the next pass has the answers
    return int(summary.get("read", 0))


def _recheck_finds(config) -> int:
    """Re-check approved finds by checksum (src/enrichment/find_recheck). Returns finds checked."""
    from src.enrichment.find_recheck import run

    return run(config)


def _drop_infected_photos(out) -> int:
    """Photos ClamAV found infected (the drain deleted the file) leave their records
    (src/enrichment/web_photos.drop_infected_photos). Returns photos removed."""
    from src.enrichment.web_photos import drop_infected_photos

    return drop_infected_photos(out)


def _resolve_equipment_images(out, grok_client, config) -> int:
    """Vision-checked equipment images (src/enrichment/equipment_images). Returns images verified."""
    from src.enrichment.equipment_images import resolve_equipment_images

    counts = resolve_equipment_images(
        out / "equipment",
        grok_client,
        images_per_record=int(config.get("equipment", {}).get("images_per_record", 3)),
    )
    return int(counts.get("verified", 0))


def _resolve_pending_image_map_vision(out, grok_client, config) -> int:
    """Run the deferred Grok map-vision check on pending images. Returns maps created."""
    from src.extraction.image_map_vision import resolve_pending

    counts = resolve_pending(
        out, grok_client, download=config.get("images", {}).get("download", True)
    )
    return int(counts.get("map", 0))


def _bibliography_resolve_config(config: dict) -> dict:
    """Config for ``resolve_bibliography_dir`` - ONE definition for both call sites. Every
    supplemental search (NARA, archive.org, Gutenberg, OpenSERP, Grok LLM search) is a Phase 3
    concern; Phase 2 performs none."""
    supplemental = config.get("supplemental_material", {})
    return {
        "nara_api_key": config.get("api", {}).get("nara_api_key"),
        "search_gutenberg": supplemental.get("search_gutenberg", True),
        "search_archive_org": supplemental.get("search_archive_org", True),
        "llm_search": supplemental.get("llm_search", True),
        "use_openserp": supplemental.get("use_openserp", False),
        "openserp_url": config.get("external_maps", {}).get(
            "openserp_url", "http://localhost:7001"
        ),
    }


def _run_enrichment_steps(args, config, grok_client, source_stats, max_workers) -> int:
    """One pass over every Phase 3 step, in order. Returns the items enriched in this pass.

    Under ``--batch`` a pass QUEUES what it cannot answer from the cache (``BatchModeCollecting``,
    a step then leaves that record unstamped) and the rounds loop (``_run_batch_rounds``) repeats
    the pass until nothing is queued: every step is stamp-driven, so a repeat only does what is
    left."""
    total_enriched = 0

    def _run_step(name: str, fn) -> int:
        return run_enrichment_step(source_stats, name, fn)

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

        resolve_config = _bibliography_resolve_config(config)
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

    # Document media (in-doc maps + deferred map vision) — independent of OpenSERP.
    if not args.people_only:
        total_enriched += _run_document_media_steps(
            args, config, grok_client, _run_step
        )

    # OpenSERP enrichment (images, academic sources) — requires OpenSERP running
    if not args.people_only and config.get("supplemental_material", {}).get(
        "use_openserp", False
    ):
        total_enriched += _run_openserp_enrichment(args, config, grok_client, _run_step)

    # Document links, photos and the provenance stamp - not web search, never gated by OpenSERP
    if not args.people_only:
        total_enriched += _run_document_link_steps(args, config, grok_client, _run_step)

    # Photos whose file ClamAV deleted leave their records (every run, every record: not gated)
    _run_step("infected_photos", lambda: _drop_infected_photos(args.output_dir))

    # Approved finds whose re-check is due: unchanged -> recorded, changed -> queued for review
    # (no Grok request). Not a web search, so not gated by use_openserp; capped per run.
    if not args.people_only:
        _run_step("find_recheck", lambda: _recheck_finds(config))

    # Equipment images: candidates for SPECIFIC equipment only (qualified Wikipedia article +
    # any OpenSERP candidates above), each confirmed by Grok vision; generic records are cleaned.
    # NOT gated by use_openserp (it is not a web search). Never skipped; leftovers alert.
    if not args.people_only:
        total_enriched += _run_step(
            "equipment_images",
            lambda: _resolve_equipment_images(args.output_dir, grok_client, config),
        )

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

    return total_enriched


def _write_phase_results(output_dir: Path, payload: dict) -> None:
    """The one writer of ``.phase_results.json`` (the entrypoint reads it: notification, and for a
    run that stops early ``pending_batch`` / ``incomplete``)."""
    (output_dir / ".phase_results.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _stat_enriched(source_stats: dict, name: str) -> int:
    """What a step enriched in the earlier passes of this run (rounds repeat the pass)."""
    return int(source_stats.get(name, {}).get("enriched", 0) or 0)


def _sources_with(source_stats: dict, status: str) -> list:
    """The steps whose status is ``status`` ("error": raised; "pending": requests set aside)."""
    return sorted(n for n, st in source_stats.items() if st.get("status") == status)


def _results_note(args, enriched: int, source_stats: dict, **fields) -> None:
    """The results file of a run that stops before its end (hand-off, round cap): what the
    entrypoint reads, plus the steps' stats so a failed step is not lost."""
    _write_phase_results(
        args.output_dir,
        {
            "enriched": enriched,
            "source_stats": source_stats,
            "errored_sources": _sources_with(source_stats, "error"),
            "pending_sources": _sources_with(source_stats, "pending"),
            "corrupt_records": _corrupt_record_names(),
            **fields,
        },
    )


def _run_batch_rounds(
    args, config, grok_client, source_stats, max_workers, enriched: int
) -> Tuple[int, Optional[int]]:
    """Submit the queued requests, repeat the whole pass on the answers, until a pass queues
    nothing. Returns ``(enriched, stop_code)``. ``stop_code`` is None when no round is left to run;
    a step that still has requests set aside is then ``pending`` and ``_exit_for_steps`` keeps its
    book queued. Otherwise it is the exit code of a run that ends early:

    - a round not answered within the in-task wait: ``HANDED_OFF_EXIT`` (75), ``pending_batch`` in
      the results file; the poller retrieves the batch and a new task resumes the run;
    - over the round cap (alerted; a human can approve more): exit 1, ``incomplete`` in the results.

    Requests submitted ``batch.max_request_attempts`` times without an answer are set aside.
    """
    import time as _time

    from src.utils.batch_api import BatchCollector, BatchPending, in_task_wait_minutes
    from src.utils.batch_rounds import (
        DEFAULT_MAX_ROUNDS,
        BatchRounds,
        HANDED_OFF_EXIT,
        first_round_from_env,
    )

    book = os.environ.get("BOOK_NAME", "all")
    batch_cfg = config.get("batch", {}) or {}
    rounds = BatchRounds(
        scope=book,
        phase="phase3",
        cap=int(batch_cfg.get("max_rounds", DEFAULT_MAX_ROUNDS)),
        first=first_round_from_env(),
        max_attempts=int(batch_cfg.get("max_request_attempts", 3)),
    )
    # The task waits for each round up to batch.in_task_wait_minutes (as Phase 2 does); a round not
    # answered by then is handed to the poller and a retrieve task resumes the run (exit 75).
    wait_minutes = in_task_wait_minutes(config)
    started = _time.monotonic()
    while True:
        rounds.drop_stuck(grok_client._batch_collector)
        queued = list(getattr(grok_client._batch_collector, "requests", None) or [])
        if not queued:
            break
        if not rounds.admit(queued, grok_client):  # alerted: no more spend
            logger.error("[phase3] over the batch round cap: stopped incomplete")
            _results_note(
                args,
                enriched,
                source_stats,
                incomplete=f"batch rounds over cap ({rounds.cap})",
            )
            return enriched, 1
        logger.info(
            "[phase3] Batch round %d: submitting %d requests (waiting up to %.0f min)",
            rounds.count,
            len(queued),
            wait_minutes,
        )
        suffix = f"-r{rounds.count}-{len(queued)}reqs"
        try:
            batch_id = grok_client.submit_batch(
                batch_name=f"phase3-{book}"[: 128 - len(suffix)] + suffix,
                max_wait_minutes=wait_minutes,
            )
        except BatchPending as e:
            logger.info("[phase3] %s: handed to the poller", e)
            _results_note(
                args,
                enriched,
                source_stats,
                pending_batch=e.batch_id,
                pending_batch_name=e.batch_name,
            )
            return enriched, HANDED_OFF_EXIT
        if not batch_id:
            raise RuntimeError(
                f"batch round {rounds.count}: {len(queued)} requests queued but not submitted"
            )
        grok_client.batch_mode = True  # the next pass queues what these answers unlock
        grok_client._batch_collector = BatchCollector()
        enriched += _run_enrichment_steps(
            args, config, grok_client, source_stats, max_workers
        )
    rounds.complete(_time.monotonic() - started)
    grok_client.batch_mode = False
    grok_client._batch_collector = None
    waiting = _sources_with(source_stats, "pending")
    if waiting:
        logger.error(
            "OBSERV phase3.steps_pending steps=%s status=error - these steps still had requests "
            "set aside after the last round (see the set-aside alert); their records are not "
            "stamped and the next run asks again",
            ",".join(waiting),
        )
    return enriched, None


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
    from src.utils.entity_records import reset_corrupt_records

    reset_corrupt_records()  # task 1.2f PART 2: corrupt records found this run hold the book
    from src.utils.link_policy import log_policy

    log_policy()  # task 1.9: what this run trusts and how far links are followed

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

    max_workers = config.get("concurrency", {}).get("max_enrichment_workers", 6)

    # C3: per-source enrichment stats so "complete" reflects partial success +
    # previously-swallowed failures surface to the operator (via .phase_results.json
    # -> completion notification -> email + Slack). C2: a source that throws is
    # LOGGED + recorded as errored, never silently swallowed and never aborts the
    # rest of Phase 3.
    source_stats: dict = {}

    total_enriched = _run_enrichment_steps(
        args, config, grok_client, source_stats, max_workers
    )
    logger.info("Phase 3 complete: %d total items enriched", total_enriched)
    grok_client.log_cache_stats()

    # Batch mode: submit what the pass queued, repeat the pass on the answers, until a pass queues
    # nothing (rounds: a later question can depend on an earlier answer).
    if args.batch:
        total_enriched, stop_code = _run_batch_rounds(
            args, config, grok_client, source_stats, max_workers, total_enriched
        )
        if stop_code is not None:  # handed to the poller, or over the round cap
            return stop_code

    from src.utils.http_pool import close_session

    close_session()

    # Write results for entrypoint notification
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
    errored_sources = _sources_with(source_stats, "error")
    if errored_sources:
        logger.error(
            "Phase 3: %d enrichment source(s) FAILED: %s",
            len(errored_sources),
            ", ".join(errored_sources),
        )
    _write_phase_results(
        args.output_dir,
        {
            "enriched": total_enriched,
            "entity_counts": entity_counts,
            # C3: per-source stats + explicit failure list so "complete"
            # reflects partial success and previously-swallowed failures reach
            # the operator via the completion notification (email + Slack).
            "source_stats": source_stats,
            "errored_sources": errored_sources,
            # steps that still had requests set aside after the last batch round
            "pending_sources": _sources_with(source_stats, "pending"),
            # stored records found corrupt this run (entity_records.load_record): the
            # book is held for the operator (EXIT_DATA_CORRUPT)
            "corrupt_records": _corrupt_record_names(),
        },
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
    return _exit_for_steps(source_stats)


def _raised_step_stats(e: BaseException) -> dict:
    """The stats of a step that RAISED (a step failure: the book stays queued, owner
    2026-11-18). ``corrupt`` marks one stopped by the book's own bad data
    (``stop_cause.RecordCorrupt``), which holds the book instead."""
    from src.utils.stop_cause import is_data_corruption

    return {
        "enriched": 0,
        "status": "error",
        "error": _CTRL.sub(" ", str(e))[:300],
        # data corruption cannot be fixed by a retry: the book is held (owner 2026-11-18)
        "corrupt": is_data_corruption(e),
    }


def run_enrichment_step(source_stats: dict, name: str, fn) -> int:
    """Run one enrichment source; record enriched/status/error in ``source_stats``. Returns the
    enriched count (0 on error). Never raises - a failed source is captured, logged, and
    surfaced, not swallowed. ``corrupt`` marks a step stopped by the book's own bad data
    (``stop_cause.RecordCorrupt``), which holds the book. Emits OBSERV phase3.step per source so
    the whole Phase-3 hand-off (N enriched / status / error) is visible in one query."""
    try:
        result = fn()
        count = (
            result if isinstance(result, int) else getattr(result, "geocoded", 0) or 0
        )
        # rounds repeat the pass: the step's total is every pass's count
        total = count + _stat_enriched(source_stats, name)
        source_stats[name] = {"enriched": total, "status": "ok"}
        try:
            logger.info("OBSERV phase3.step name=%s enriched=%d status=ok", name, count)
        except Exception:  # noqa: BLE001 - observability never breaks the step
            pass
        return count
    except BatchModeCollecting:
        # requests were queued for the batch: not an answer and not a failure (the next round
        # asks again; a step that is still pending when the rounds end is reported)
        source_stats[name] = {
            "enriched": _stat_enriched(source_stats, name),
            "status": "pending",
        }
        logger.info("OBSERV phase3.step name=%s enriched=0 status=pending", name)
        return 0
    except Exception as e:  # noqa: BLE001 - capture + surface, don't abort Phase 3
        logger.error("Enrichment source '%s' FAILED: %s", name, e, exc_info=True)
        source_stats[name] = _raised_step_stats(e)
        try:
            logger.info(
                "OBSERV phase3.step name=%s enriched=0 status=error corrupt=%s error=%s",
                name,
                source_stats[name]["corrupt"],
                _CTRL.sub(" ", str(e))[:120],
            )
        except Exception:  # noqa: BLE001
            pass
        return 0


def _corrupt_record_names(limit: int = 50) -> list:
    """The corrupt records found this run, as ``<entity>/<file>`` (at most ``limit``; then an
    "... and N more" entry so the list never looks complete when it is not)."""
    from src.utils.entity_records import corrupt_records

    found = corrupt_records()
    names = ["/".join(Path(p).parts[-2:]) for p in found[:limit]]
    if len(found) > limit:
        names.append(f"... and {len(found) - limit} more")
    return names


def _exit_for_steps(source_stats: dict) -> int:
    """0 when every step finished. A step that raised means part of its work was never
    attempted, so the book must not be marked finished (owner 2026-11-18): EXIT_STEP_FAILED (the
    book stays queued and is retried), or EXIT_DATA_CORRUPT when the cause was bad data (held
    for the operator: a retry cannot fix it)."""
    from src.utils import stop_cause

    from src.utils.entity_records import corrupt_records

    failed = [s for s in source_stats.values() if s.get("status") == "error"]
    # a step that skipped a corrupt record went on with the rest, but the book is not done: a
    # retry cannot repair the record (task 1.2f PART 2)
    if corrupt_records() or any(s.get("corrupt") for s in failed):
        return stop_cause.EXIT_DATA_CORRUPT
    # a step still ``pending`` at the END of the run had requests set aside by the rounds loop
    # (nothing else leaves one pending): its records are unstamped, so the book is not finished
    pending = [s for s in source_stats.values() if s.get("status") == "pending"]
    if failed or pending:
        return stop_cause.EXIT_STEP_FAILED
    return 0


if __name__ == "__main__":
    from src.utils.stop_cause import run_main

    # a Grok outage or no credits stops the run with its own exit code (task 1.2e): the ECS
    # wrapper defers the book and it resumes when Grok is back
    exit(run_main(main))
