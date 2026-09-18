"""Tests for the region-aware stage-3 converter (step 5).

Verifies that image/map regions produce asset files + embedded Markdown, that
the emitted image Markdown is parseable by the EXISTING src/parser.py into an
Image (the real compatibility proof), and that mixed documents convert per
region.
"""

from pathlib import Path

import fitz

from src.ingestion.media_detection import build_source_metadata
from src.ingestion.disposition_classifier import classify_pdf
from src.ingestion.region_converter import (
    RESOURCES_DIRNAME,
    convert_manifest,
)
from src.ingestion.routing_manifest import build_manifest
from src.parser import extract_images


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
    _prose_page(doc)  # 1 unstructured
    _image_page(doc)  # 2 image
    path = tmp_path / "mixed.pdf"
    doc.save(str(path))
    doc.close()
    return path


def _manifest_and_pdf(tmp_path: Path):
    path = _mixed_pdf(tmp_path)
    meta = build_source_metadata("01SRC", path, acquisition_method="local")
    manifest = build_manifest(meta, classify_pdf(meta.source_id, path))
    return manifest, path


def test_image_region_extracts_asset_and_emits_embedded_markdown(
    tmp_path: Path,
) -> None:
    manifest, path = _manifest_and_pdf(tmp_path)
    out = tmp_path / "out"
    result = convert_manifest(manifest, path, out)

    # An asset was extracted to the resource store.
    assert result.assets, "expected at least one extracted asset"
    asset = result.assets[0]
    assert asset.path.exists()
    assert asset.path.parent.name == RESOURCES_DIRNAME
    # Embedded Markdown reference for the asset is present.
    assert f"(:/{asset.resource_id})" in result.markdown


def test_emitted_image_markdown_is_parseable_by_stage4(tmp_path: Path) -> None:
    # The real contract: existing src/parser.py must turn our Markdown into an
    # embedded Image.
    manifest, path = _manifest_and_pdf(tmp_path)
    result = convert_manifest(manifest, path, tmp_path / "out")

    images = extract_images(result.markdown)
    assert images, "parser did not extract any image from converter output"
    img_type, resource_id, _alt, _url = images[0]
    assert img_type == "embedded"
    # Parser captures the id after the ':' — must match our resource id.
    assert result.assets[0].resource_id in resource_id


def test_prose_region_produces_text_not_assets(tmp_path: Path) -> None:
    doc = fitz.open()
    _prose_page(doc)
    path = tmp_path / "prose.pdf"
    doc.save(str(path))
    doc.close()
    meta = build_source_metadata("01SRC", path)
    manifest = build_manifest(meta, classify_pdf(meta.source_id, path))

    result = convert_manifest(manifest, path, tmp_path / "out")
    assert result.assets == []
    assert "Lorem ipsum" in result.markdown


def test_mixed_document_has_both_text_and_asset(tmp_path: Path) -> None:
    manifest, path = _manifest_and_pdf(tmp_path)
    result = convert_manifest(manifest, path, tmp_path / "out")
    assert "Lorem ipsum" in result.markdown  # prose region
    assert result.assets  # image region
    assert "(:/" in result.markdown  # embedded ref


def test_unsupported_source_yields_empty_conversion(tmp_path: Path) -> None:
    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    meta = build_source_metadata("01SRC", vid, with_checksum=False)
    manifest = build_manifest(meta, [])  # no regions
    # A video is not a PDF; with no regions, nothing is opened/converted.
    result = convert_manifest(manifest, vid, tmp_path / "out")
    assert result.markdown == ""
    assert result.assets == []


def test_pdf_to_markdown_default_path_untouched() -> None:
    # Guard: the existing whole-document entry point still exists and is not
    # coupled to the new converter (Option A: no modification).
    import scripts.pdf_to_markdown as p2m

    assert hasattr(p2m, "pdf_to_markdown")
