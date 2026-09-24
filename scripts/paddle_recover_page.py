"""Batch worker: recover a flattened 2-D table from one page via PP-StructureV3.

This is the GPU-side counterpart to Chandra. It exists because Chandra flattens
complex 2-D task-organization tables into vertical lists (validated blind spot;
see CHANDRA_OCR_DESIGN.md). The auto-routing bridge
(``src/ingestion/table_recovery.py``) decides *whether* a page needs recovery by
inspecting Chandra's markdown for the flattened signature; this worker performs
the recovery on the page image using the in-repo PP-StructureV3 runner
(``src/ingestion/paddle_structure.py``).

Contract (invoked by ``paddle_entrypoint.sh`` after S3 download):

    paddle_recover_page.py <page-image> <chandra-markdown> <output-json>

* ``<page-image>``      — rendered page (PNG/JPG), e.g. 300-DPI from the PDF.
* ``<chandra-markdown>``— Chandra's markdown for THAT page (the routing input).
* ``<output-json>``     — where to write the recovery result.

The result JSON records both the routing decision and, when recovery ran, the
recovered ``<table>`` HTML alongside Chandra's review-flagged hint tree — never
replacing one with the other (ensemble-as-verification; reconciliation is
downstream). Exit code is 0 on success (including the legitimate "no flattened
table / nothing to recover" outcomes) and non-zero only on unexpected failure.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# The container installs the repo at /app; ensure it is importable when this is
# invoked as a bare script by the entrypoint. The sys.path insert must precede
# the src imports, so those imports are intentionally not at module top.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ingestion.paddle_structure import (  # pylint: disable=wrong-import-position
    PaddleStructureRunner,
    is_available,
)
from src.ingestion.table_recovery import (  # pylint: disable=wrong-import-position
    recover_page_tables,
)


def _build_result(recovery, *, page_image: str, markdown_path: str) -> dict:
    """Serialize a PageTableRecovery into the output JSON structure."""
    recovered = recovery.recovered
    return {
        "page_image": page_image,
        "chandra_markdown": markdown_path,
        "routed": recovery.routed,
        "recovered_a_table": recovery.recovered_a_table,
        "notes": recovery.notes,
        # Chandra-side: the review-flagged flattened-table hint trees.
        "flattened_hints": [
            {
                "start_line": s.start_para,
                "end_line": s.end_para,
                "confidence": s.confidence,
                "needs_review": s.needs_review,
                "notes": s.notes,
                "snapshots": [
                    {
                        "label": snap.label,
                        "descriptor": snap.descriptor,
                        "groups": [
                            {"group": g.group, "units": list(g.units)}
                            for g in snap.groups
                        ],
                    }
                    for snap in s.snapshots
                ],
            }
            for s in recovery.flattened_spans
        ],
        # Paddle-side: the recovered structure (present only when routed + ran).
        "recovered": (
            {
                "engine": recovered.engine,
                "device": recovered.device,
                "det_limit_side_len": recovered.det_limit_side_len,
                "table_count": recovered.table_count,
                "html": recovered.html,
                "markdown": recovered.markdown,
                "notes": recovered.notes,
                # Diagnostic: layout detector regions (label+score) — shows
                # whether a table-class box was produced/rejected vs. the
                # content being classified as text.
                "layout_boxes": recovered.layout_boxes,
            }
            if recovered is not None
            else None
        ),
    }


def main(argv: list[str]) -> int:
    """Run recovery for one page. See module docstring for the argument contract."""
    if len(argv) != 4:
        print(
            "usage: paddle_recover_page.py <page-image> <chandra-markdown> "
            "<output-json>",
            file=sys.stderr,
        )
        return 2

    page_image = Path(argv[1])
    markdown_path = Path(argv[2])
    output_json = Path(argv[3])

    if not page_image.exists():
        print(f"ERROR: page image not found: {page_image}", file=sys.stderr)
        return 2
    if not markdown_path.exists():
        print(f"ERROR: markdown not found: {markdown_path}", file=sys.stderr)
        return 2

    if not is_available():
        # In the Paddle container this should never happen; fail loudly so a
        # broken image is caught rather than silently producing empty output.
        print("ERROR: PaddleOCR is not importable in this environment", file=sys.stderr)
        return 1

    markdown = markdown_path.read_text(encoding="utf-8", errors="replace")

    # Build the runner once (prefers GPU; the container provides one). Reused if
    # this worker is later extended to loop over multiple pages.
    runner = PaddleStructureRunner()
    print(f"PP-StructureV3 recovery (device={runner.device})", flush=True)

    recovery = recover_page_tables(markdown, page_image=page_image, runner=runner)

    result = _build_result(
        recovery, page_image=str(page_image), markdown_path=str(markdown_path)
    )
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(
        f"Wrote {output_json} — routed={recovery.routed}, "
        f"recovered_table={recovery.recovered_a_table}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
