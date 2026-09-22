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
from typing import List, Optional

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

# Caption detection tunables. Captions in scanned/printed books sit close to the
# image (usually just below, sometimes above) and are short relative to body
# text. These bounds keep a stray paragraph from being mistaken for a caption.
CAPTION_MAX_GAP = 90.0  # max vertical gap (pt) between image edge and caption
CAPTION_MAX_CHARS = 300  # a caption is a short line, not a paragraph
CAPTION_MIN_CHARS = 3  # ignore stray single glyphs / page numbers of len<3
# A caption is typically set smaller than body text; treat a candidate whose
# font is at most this fraction of the page's body size as font-corroborated.
CAPTION_SMALLER_RATIO = 0.92
# PyMuPDF span flag bit 1 (value 2) marks italic — captions are often italic.
_ITALIC_FLAG = 2


def _new_resource_id() -> str:
    """Return a short, unique resource id for an extracted asset."""
    return secrets.token_hex(12)


def _block_text(block: dict) -> str:
    """Concatenate a text block's span text."""
    return " ".join(
        span.get("text", "")
        for line in block.get("lines", [])
        for span in line.get("spans", [])
    ).strip()


def _block_font(block: dict) -> tuple[float, bool]:
    """Return (median-ish span size, any-italic) for a text block.

    Uses the max span size as the block's representative size (headings/body
    dominate over stray small glyphs) and ORs the italic flag across spans.
    Returns (0.0, False) when no spans carry size info.
    """
    sizes: list[float] = []
    italic = False
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            size = span.get("size")
            if isinstance(size, (int, float)):
                sizes.append(float(size))
            if int(span.get("flags", 0)) & _ITALIC_FLAG:
                italic = True
    return (max(sizes) if sizes else 0.0, italic)


def _body_font_size(blocks: list[dict]) -> float:
    """Estimate the page's body text size as the most common span size.

    The dominant size on a text page is the body; captions sit below it. Returns
    0.0 when size info is unavailable (heuristic then degrades to proximity).
    """
    counts: dict[float, int] = {}
    for block in blocks:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                size = span.get("size")
                if isinstance(size, (int, float)):
                    key = round(float(size), 1)
                    counts[key] = counts.get(key, 0) + len(span.get("text", ""))
    if not counts:
        return 0.0
    # Most character-weight, not most blocks: body text dominates by volume.
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _vertical_gap(
    block_bbox: tuple[float, float, float, float],
    image_bbox: tuple[float, float, float, float],
    gap_limit: float,
) -> Optional[tuple[float, float]]:
    """Return ``(gap, placement_penalty)`` for a block relative to an image.

    A caption sits just below the image (no penalty) or, less often, just above
    it (penalized so below wins on a tie). Returns ``None`` when the block is
    outside ``gap_limit`` in both directions (not a caption position).
    """
    _, iy0, _, iy1 = image_bbox
    _, by0, _, by1 = block_bbox
    gap_below = by0 - iy1  # block starts below the image bottom
    if 0 <= gap_below <= gap_limit:
        return gap_below, 0.0
    gap_above = iy0 - by1  # block ends above the image top
    if 0 <= gap_above <= gap_limit:
        return gap_above, 20.0  # prefer below over above
    return None


def _caption_text_if_eligible(
    block: dict, image_bbox: tuple[float, float, float, float]
) -> Optional[str]:
    """Return a block's text if it could be a caption, else ``None``.

    Eligibility is position/shape only (no scoring): a text block that overlaps
    the image horizontally and whose text is caption-length. Keeps the type,
    overlap and length guards out of :func:`_score_caption_candidate`.
    """
    if block.get("type") != 0:  # text blocks only
        return None
    ix0, _, ix1, _ = image_bbox
    bx0, _, bx1, _ = block.get("bbox", (0, 0, 0, 0))
    if bx1 < ix0 or bx0 > ix1:  # require horizontal overlap with the image
        return None
    text = _block_text(block)
    if not CAPTION_MIN_CHARS <= len(text) <= CAPTION_MAX_CHARS:
        return None
    return text


def _score_caption_candidate(
    block: dict,
    image_bbox: tuple[float, float, float, float],
    body_size: float,
) -> Optional[tuple[float, str]]:
    """Score one text block as a caption candidate for an image.

    Returns ``(score, text)`` (lower score = better caption) or ``None`` when the
    block is not a viable candidate. Combines proximity (:func:`_vertical_gap`)
    with a font signal (smaller/italic than body text corroborates a caption).
    """
    text = _caption_text_if_eligible(block, image_bbox)
    if text is None:
        return None

    size, italic = _block_font(block)
    font_caption = bool(
        (body_size and size and size <= body_size * CAPTION_SMALLER_RATIO) or italic
    )
    # A font-distinct caption may sit slightly past the strict gap.
    gap_limit = CAPTION_MAX_GAP * (1.5 if font_caption else 1.0)
    placement = _vertical_gap(block.get("bbox", (0, 0, 0, 0)), image_bbox, gap_limit)
    if placement is None:
        return None
    gap, placement_penalty = placement

    # Font-corroborated candidates get a strong score bonus so they beat a
    # closer-but-body-font block.
    score = gap + placement_penalty - (60.0 if font_caption else 0.0)
    return score, text


def _caption_for_image(
    page: "fitz.Page", image_bbox: tuple[float, float, float, float]
) -> str:
    """Find the caption text nearest an image on a page.

    Book/scan captions sit adjacent to the image — most often the short text
    line immediately *below* it, occasionally *above* — and are typically set in
    a *smaller and/or italic* font than body text. Two signals combine (see
    :func:`_score_caption_candidate`):

    * **Proximity** — horizontal overlap with the image and a small vertical
      gap (preferring below), and
    * **Font** — a candidate whose font is smaller than the page body size (or
      italic) is corroborated as a caption; this lets a font-distinct caption
      win over a merely-closer body paragraph, and rescues a caption sitting
      just past the strict proximity gap.

    Font is best-effort: when span size data is absent the decision degrades to
    proximity alone. Returns "" when no plausible caption is found so the caller
    can fall back to a placeholder.
    """
    try:
        blocks = page.get_text("dict").get("blocks", [])
    except (RuntimeError, ValueError):  # pragma: no cover - defensive
        return ""

    body_size = _body_font_size(blocks)

    # Keep the lowest-scoring (best) candidate across all blocks.
    best_score = float("inf")
    best_text = ""
    for block in blocks:
        scored = _score_caption_candidate(block, image_bbox, body_size)
        if scored is not None and scored[0] < best_score:
            best_score, best_text = scored

    return best_text


@dataclass
class ConvertedAsset:
    """An asset extracted from an image/map region."""

    resource_id: str
    path: Path
    disposition: Disposition
    page_number: int
    alt_text: str = ""
    caption: str = ""


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
            # Locate the image on the page to look for an adjacent caption.
            caption = ""
            try:
                rects = page.get_image_rects(xref)
            except (RuntimeError, ValueError):  # pragma: no cover - defensive
                rects = []
            if rects:
                caption = _caption_for_image(page, tuple(rects[0]))
            alt = caption or f"{disposition} p{page_number}"
            assets.append(
                ConvertedAsset(
                    resource_id=resource_id,
                    path=out,
                    disposition=disposition,
                    page_number=page_number,
                    alt_text=alt,
                    caption=caption,
                )
            )
    else:
        # No embedded raster (e.g. vector map): render the page to PNG. There is
        # no single image bbox to anchor a caption to, so use the whole page's
        # nearby-text heuristic against the full page rect.
        resource_id = _new_resource_id()
        out = resources_dir / f"{resource_id}.png"
        out.write_bytes(page.get_pixmap().tobytes("png"))
        caption = _caption_for_image(page, tuple(page.rect))
        alt = caption or f"{disposition} p{page_number}"
        assets.append(
            ConvertedAsset(
                resource_id=resource_id,
                path=out,
                disposition=disposition,
                page_number=page_number,
                alt_text=alt,
                caption=caption,
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
