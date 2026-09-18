"""Tests for the routing manifest emitter (step 4).

Builds manifests from real step-1/2/3 output over synthetic fitz PDFs.
"""

from pathlib import Path

import fitz

from src.ingestion.disposition import DispositionResult, PageSignals
from src.ingestion.disposition_classifier import classify_pdf
from src.ingestion.media_detection import build_source_metadata
from src.ingestion.routing_manifest import (
    RoutingManifest,
    build_manifest,
)


def _prose_page(doc: fitz.Document) -> None:
    page = doc.new_page()
    page.insert_textbox(
        fitz.Rect(50, 50, 545, 780),
        "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 40,
        fontsize=11,
    )


def _image_page(doc: fitz.Document) -> None:
    page = doc.new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 500, 700))
    pix.clear_with(128)
    page.insert_image(fitz.Rect(40, 40, 555, 760), pixmap=pix)


def _mixed_pdf(tmp_path: Path) -> Path:
    doc = fitz.open()
    _prose_page(doc)  # 1
    _prose_page(doc)  # 2  (adjacent same disposition -> should coalesce)
    _image_page(doc)  # 3
    path = tmp_path / "mixed.pdf"
    doc.save(str(path))
    doc.close()
    return path


def _result(page: int, disp, conf=0.9, review=False, source="heuristic"):
    return DispositionResult(
        source_id="S",
        disposition=disp,
        confidence=conf,
        page_number=page,
        needs_review=review,
        decision_source=source,
        provenance_anchor=str(page),
        signals=PageSignals(),
    )


# --- region coalescing (unit-level, deterministic) -----------------------


def test_adjacent_same_disposition_coalesces() -> None:
    results = [
        _result(1, "unstructured"),
        _result(2, "unstructured"),
        _result(3, "structured"),
    ]
    manifest = build_manifest(_fake_meta(), results)
    assert len(manifest.regions) == 2
    first = manifest.regions[0]
    assert first.start_page == 1 and first.end_page == 2
    assert first.page_count == 2
    assert manifest.regions[1].disposition == "structured"


def test_non_adjacent_same_disposition_does_not_merge() -> None:
    # A gap in page numbers must break the region.
    results = [_result(1, "image"), _result(3, "image")]
    manifest = build_manifest(_fake_meta(), results)
    assert len(manifest.regions) == 2


def test_review_and_confidence_aggregate_across_region() -> None:
    results = [
        _result(1, "structured", conf=0.9, review=False),
        _result(2, "structured", conf=0.55, review=True),
    ]
    manifest = build_manifest(_fake_meta(), results)
    region = manifest.regions[0]
    assert region.needs_review is True
    assert region.min_confidence == 0.55


def test_override_propagates_to_region_decision_source() -> None:
    results = [
        _result(1, "map", source="heuristic"),
        _result(2, "map", source="config_override"),
    ]
    manifest = build_manifest(_fake_meta(), results)
    assert manifest.regions[0].decision_source == "config_override"


def test_summary_counts() -> None:
    results = [
        _result(1, "unstructured"),
        _result(2, "unstructured"),
        _result(3, "image", review=True),
    ]
    manifest = build_manifest(_fake_meta(), results)
    assert manifest.summary.total_pages == 3
    assert manifest.summary.total_regions == 2
    assert manifest.summary.pages_by_disposition == {"unstructured": 2, "image": 1}
    assert manifest.summary.regions_needing_review == 1


def test_query_helpers() -> None:
    results = [_result(1, "unstructured"), _result(2, "image", review=True)]
    manifest = build_manifest(_fake_meta(), results)
    assert len(manifest.regions_for("image")) == 1
    assert len(manifest.regions_needing_review()) == 1


# --- end-to-end from real step 1-3 over a synthetic PDF ------------------


def test_build_from_real_pipeline(tmp_path: Path) -> None:
    path = _mixed_pdf(tmp_path)
    meta = build_source_metadata("01SRC", path, acquisition_method="local")
    results = classify_pdf(meta.source_id, path)
    manifest = build_manifest(meta, results)

    assert manifest.source.media_type == "pdf"
    # Pages 1-2 prose coalesce; page 3 image is its own region.
    assert manifest.summary.total_pages == 3
    dispositions = [r.disposition for r in manifest.regions]
    assert "unstructured" in dispositions and "image" in dispositions
    prose_regions = manifest.regions_for("unstructured")
    assert prose_regions and prose_regions[0].page_count == 2


def test_json_round_trip(tmp_path: Path) -> None:
    path = _mixed_pdf(tmp_path)
    meta = build_source_metadata("01SRC", path)
    manifest = build_manifest(meta, classify_pdf(meta.source_id, path))

    out = tmp_path / "manifest.json"
    manifest.save(out)
    assert out.exists()
    restored = RoutingManifest.load(out)
    assert restored.source == manifest.source
    assert len(restored.regions) == len(manifest.regions)
    assert restored.summary == manifest.summary


def test_unsupported_source_yields_empty_regions(tmp_path: Path) -> None:
    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    meta = build_source_metadata("01SRC", vid, with_checksum=False)
    assert meta.supported is False
    manifest = build_manifest(meta, [])
    assert manifest.regions == []
    assert manifest.summary.total_pages == 0
    assert manifest.source.supported is False


def _fake_meta():
    """Minimal supported SourceMetadata for unit-level manifest tests."""
    from src.ingestion.source_metadata import SourceMetadata

    return SourceMetadata(
        source_id="S",
        original_path=Path("mem.pdf"),
        media_type="pdf",
        supported=True,
    )
