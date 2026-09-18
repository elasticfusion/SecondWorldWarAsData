"""Region-aware stage-3 converter.

Consumes a :class:`RoutingManifest` and converts each region of a PDF to
Markdown using a disposition-appropriate method, concatenating the result. This
is the seam that unblocks PDF image/map extraction: image/map regions have their
assets extracted to a resource store and emitted as embedded Markdown
references, so the existing stage-4 parser (``src/parser.py``) populates its
``Image``/``Map`` slots with no changes to that parser.

Disposition handling:
  * ``unstructured`` -> ``pymupdf4llm.to_markdown`` over the region's pages.
  * ``structured``   -> table-aware Markdown over the region's pages (currently
    ``pymupdf4llm`` with tables enabled; the OOB normalization is relocated here
    in a follow-up).
  * ``image`` / ``map`` -> extract the page asset(s) to the resource store and
    emit ``![alt](:/<resource-id>)``.

This module does NOT modify ``scripts/pdf_to_markdown.py``; the whole-document
path remains the default. Callers opt in by supplying a manifest.

Parser contract (see src/parser.py): only ``![alt](:/resource-id)`` (embedded),
``![alt](http(s)://url)`` (external), and ``[Map <id>](http(s)://url)`` are
recognized. Extracted assets are local, so the embedded ``:/`` form is emitted.
Local map assets cannot yet populate the ``Map`` slot via the parser's
URL-only map regex; they are emitted as embedded images for now (a small parser
extension to accept local maps is a documented follow-up).

See docs/current/dataquality/INGESTION_FRONT_END.md (step 5).
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import fitz

try:
    import pymupdf4llm
except ImportError:  # pragma: no cover - exercised only without the dep
    pymupdf4llm = None  # type: ignore[assignment]

from src.ingestion.disposition import Disposition
from src.ingestion.routing_manifest import RoutingManifest, RoutingRegion

logger = logging.getLogger(__name__)

# Filesystem name for the extracted-asset store, relative to the output dir.
RESOURCES_DIRNAME = "resources"


def _new_resource_id() -> str:
    """Return a short, unique resource id for an extracted asset."""
    return secrets.token_hex(12)


@dataclass
class ConvertedAsset:
    """An asset extracted from an image/map region."""

    resource_id: str
    path: Path
    disposition: Disposition
    page_number: int
    alt_text: str = ""


@dataclass
class ConversionResult:
    """Output of converting a manifest: Markdown plus extracted assets."""

    markdown: str
    assets: List[ConvertedAsset] = field(default_factory=list)


def _pages_of(region: RoutingRegion) -> List[int]:
    """Return the 1-based page numbers a paged region spans."""
    if region.start_page is None or region.end_page is None:
        return []
    return list(range(region.start_page, region.end_page + 1))


def _to_zero_based(page_numbers: List[int]) -> List[int]:
    """Convert 1-based page numbers to 0-based fitz indices."""
    return [n - 1 for n in page_numbers]


def _text_markdown_for_pages(pdf_path: Path, page_numbers: List[int]) -> str:
    """Return Markdown for the given pages via pymupdf4llm.

    ``pymupdf4llm`` handles tables inline, so structured and unstructured
    regions share this path for now; the disposition still drives asset handling
    for image/map regions.
    """
    if pymupdf4llm is None:  # pragma: no cover - dependency always present
        raise RuntimeError("pymupdf4llm is required for text/table conversion")
    pages = _to_zero_based(page_numbers)
    return pymupdf4llm.to_markdown(str(pdf_path), pages=pages).strip()


def _extract_page_assets(
    doc: "fitz.Document",
    page_number: int,
    disposition: Disposition,
    resources_dir: Path,
) -> List[ConvertedAsset]:
    """Extract image/map asset(s) from one page to the resource store.

    Embedded raster images are extracted directly. If a page has no extractable
    raster image (e.g. a vector map), the page is rendered to a PNG instead so
    the visual content is still captured.
    """
    page = doc[page_number - 1]
    assets: List[ConvertedAsset] = []
    resources_dir.mkdir(parents=True, exist_ok=True)

    raster = page.get_images(full=True)
    if raster:
        for xref, *_rest in raster:
            extracted = doc.extract_image(xref)
            ext = extracted.get("ext", "png")
            resource_id = _new_resource_id()
            out = resources_dir / f"{resource_id}.{ext}"
            out.write_bytes(extracted["image"])
            assets.append(
                ConvertedAsset(
                    resource_id=resource_id,
                    path=out,
                    disposition=disposition,
                    page_number=page_number,
                    alt_text=f"{disposition} p{page_number}",
                )
            )
    else:
        # No embedded raster (e.g. vector map): render the page to PNG.
        resource_id = _new_resource_id()
        out = resources_dir / f"{resource_id}.png"
        out.write_bytes(page.get_pixmap().tobytes("png"))
        assets.append(
            ConvertedAsset(
                resource_id=resource_id,
                path=out,
                disposition=disposition,
                page_number=page_number,
                alt_text=f"{disposition} p{page_number}",
            )
        )
    return assets


def _asset_markdown(asset: ConvertedAsset) -> str:
    """Embedded-resource Markdown recognized by src/parser.py."""
    return f"![{asset.alt_text}](:/{asset.resource_id})"


def _convert_media_region(
    doc: "fitz.Document",
    region: RoutingRegion,
    resources_dir: Path,
) -> tuple[str, List[ConvertedAsset]]:
    """Convert an image/map region to Markdown + extracted assets."""
    lines: List[str] = []
    assets: List[ConvertedAsset] = []
    for page_number in _pages_of(region):
        page_assets = _extract_page_assets(
            doc, page_number, region.disposition, resources_dir
        )
        assets.extend(page_assets)
        lines.extend(_asset_markdown(a) for a in page_assets)
    return "\n\n".join(lines), assets


def convert_manifest(
    manifest: RoutingManifest,
    pdf_path: Path,
    output_dir: Path,
) -> ConversionResult:
    """Convert every region of a PDF per its manifest disposition.

    Args:
        manifest: Routing manifest (from :func:`build_manifest`).
        pdf_path: Path to the source PDF.
        output_dir: Directory for extracted assets (under ``resources/``).

    Returns:
        A :class:`ConversionResult` with concatenated Markdown and the list of
        extracted assets. An unsupported source (no regions) yields empty
        output.
    """
    resources_dir = output_dir / RESOURCES_DIRNAME
    markdown_parts: List[str] = []
    all_assets: List[ConvertedAsset] = []

    if not manifest.regions:
        logger.info(
            "No regions to convert for %s (source supported=%s)",
            pdf_path.name,
            manifest.source.supported,
        )
        return ConversionResult(markdown="", assets=[])

    with fitz.open(str(pdf_path)) as doc:
        for region in manifest.regions:
            if region.disposition in ("image", "map"):
                text, assets = _convert_media_region(doc, region, resources_dir)
                all_assets.extend(assets)
            else:
                text = _text_markdown_for_pages(pdf_path, _pages_of(region))
            if text:
                markdown_parts.append(text)

    logger.info(
        "Converted %s: %d region(s), %d asset(s) extracted",
        pdf_path.name,
        len(manifest.regions),
        len(all_assets),
    )
    return ConversionResult(
        markdown="\n\n".join(markdown_parts).strip(),
        assets=all_assets,
    )
