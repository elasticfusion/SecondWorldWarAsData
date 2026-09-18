"""Phase 0: ingestion normalization — scanned OOB tables -> structured rows.

Runs ahead of Phase 1. For scanned Order-of-Battle sources whose tables live in
Chandra OCR+AI markdown, Phase 0 parses each section (command-staff, campaigns,
command-posts, statistics, organic-units) into structured rows, persists them to
``output/oob/<section>/``, and builds a non-destructive name->PersonID crosswalk.

This is the runnable entry point for the built ``src/ingestion`` front-end; it
executes locally (``python phase0_ingest.py``) and, in AWS, via
``ecs_entrypoint.py phase0_ingest.py`` (which handles S3 download/sync).

Scope note: this increment wires the OOB markdown structured-parsing path (the
fully built + tested capability). Generic media detection / PDF->markdown region
conversion also exist in ``src/ingestion`` and can be added to this orchestrator
as their end-to-end (Chandra OCR) path is wired.

See docs/current/dataquality/INGESTION_FRONT_END.md.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from src.ingestion.oob_markdown.run import run_oob_markdown_file
from src.utils.config import get_paths, load_config
from src.utils.logger import setup_logging

# OOB Chandra markdown lives in an ``ocr_output`` directory within the source.
_OCR_OUTPUT_DIRNAME = "ocr_output"

# Markdown files that are not per-division/section OOB content.
_SKIP_STEMS = frozenset({"00-missing"})


def discover_oob_markdown(content_root: Path) -> List[Path]:
    """Find OOB Chandra markdown files under the content repository.

    Looks for ``*/ocr_output/*.md`` — the per-division/section markdown the
    Chandra OCR step produces for scanned Order-of-Battle sources.
    """
    files: List[Path] = []
    for ocr_dir in content_root.rglob(_OCR_OUTPUT_DIRNAME):
        if not ocr_dir.is_dir():
            continue
        for md in sorted(ocr_dir.glob("*.md")):
            if md.stem.lower() in _SKIP_STEMS:
                continue
            files.append(md)
    return files


def main() -> None:
    """Main entry point for Phase 0."""
    base_dir = Path(__file__).parent
    config = load_config(base_dir / "config.yaml")
    paths = get_paths(config, base_dir)

    log_config = config.get("logging", {})
    logger = setup_logging(
        level=log_config.get("level", "INFO"),
        log_file=log_config.get("file"),
        console=log_config.get("console", True),
    )

    logger.info("Phase 0: ingestion normalization (OOB markdown -> structured rows)")

    content_root = paths["content_root"]
    output_root = paths["output_root"]
    people_dir = output_root / "people"
    output_root.mkdir(parents=True, exist_ok=True)

    logger.info("[phase0 step 1/2] Scanning for OOB markdown under %s", content_root)
    sources = discover_oob_markdown(content_root)
    logger.info("Found %d OOB markdown file(s)", len(sources))

    logger.info("[phase0 step 2/2] Parsing %d file(s)", len(sources))
    total_rows = 0
    total_review = 0
    for index, md_path in enumerate(sources, start=1):
        summary = run_oob_markdown_file(md_path, output_root, people_dir)
        rows = sum(s["rows"] for s in summary["sections"].values())
        review = sum(s["review"] for s in summary["sections"].values())
        total_rows += rows
        total_review += review
        logger.info(
            "  (%d/%d) %s: %d row(s), %d for review%s",
            index,
            len(sources),
            md_path.name,
            rows,
            review,
            (
                f", crosswalk {summary['crosswalk']['matched']}/"
                f"{summary['crosswalk']['links']} matched"
                if "crosswalk" in summary
                else ""
            ),
        )

    logger.info(
        "Phase 0 complete: %d file(s), %d row(s), %d flagged for review",
        len(sources),
        total_rows,
        total_review,
    )


if __name__ == "__main__":
    main()
