"""Markdown content parsing with entity extraction."""

import logging
import functools
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from src.models import (
    ChapterGroup,
    Footnote,
    Image,
    Map,
    MarkdownDocument,
    Metadata,
    PageMarker,
    Paragraph,
)

# Compiled regex patterns for performance
_BLOCKQUOTE_PATTERN = re.compile(r"^>\s*")
_PAGE_MARKER_PATTERN = re.compile(r'<(a|span) id="page\d+"></\1>')
_FOOTNOTE_PATTERN = re.compile(r"\*\\--\d+--\*")
_SEPARATOR_PATTERN = re.compile(r"\n\* \* \*\n")
_HEADING_PATTERN = re.compile(r"^#{1,6}\s+\*.*\*$")
_CHAPTER_NUM_PATTERN = re.compile(r"chapter(\d+)")


logger = logging.getLogger(__name__)


def _parse_yaml_metadata(yaml_file: Path) -> Metadata:
    """Parse metadata from YAML file."""
    with open(yaml_file, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    # yaml.safe_load returns a scalar/list/None for non-mapping documents
    # (empty file, a bare string, a YAML list, etc.). Calling .get() on that
    # raised AttributeError and aborted the whole parse. Treat it as empty
    # metadata instead, loudly, so one malformed meta file never kills the run.
    if not isinstance(data, dict):
        logger.warning(
            "OBSERV parser.meta_not_mapping file=%s type=%s -> empty Metadata",
            yaml_file.name,
            type(data).__name__,
        )
        return Metadata()

    return Metadata(
        series=data.get("series", ""),
        book=data.get("book", ""),
        author=data.get("author", ""),
        chapter_title=data.get("chapter_title", ""),
        license=data.get("license", "Public Domain"),
        copyright_date=data.get("copyright_date", ""),
        source_url=data.get("source_url", ""),
    )


def _parse_legacy_metadata(content: str) -> Metadata:
    """Parse metadata from legacy .md format."""
    lines = [line.strip() for line in content.split("\n") if line.strip()]
    metadata = Metadata()

    if len(lines) >= 1:
        metadata.series = lines[0]
    if len(lines) >= 3:
        metadata.book = lines[2]
    elif len(lines) >= 2:
        metadata.book = lines[1]
    if len(lines) >= 4:
        metadata.author = lines[3]
    if len(lines) >= 5:
        ch = lines[4]
        metadata.chapter_title = ch.split(" - ", 1)[1] if " - " in ch else ch
    if len(lines) >= 6:
        metadata.license = lines[5]

    return metadata


def parse_metadata(meta_file: Path) -> Metadata:
    """Parse metadata from -meta.yaml file (or fallback to .md)."""
    # Try YAML first
    yaml_file = meta_file.with_suffix(".yaml")
    if yaml_file.exists():
        return _parse_yaml_metadata(yaml_file)

    # Fallback to old .md parsing
    content = meta_file.read_text(encoding="utf-8")
    return _parse_legacy_metadata(content)


def extract_page_markers(text: str) -> List[Tuple[int, int, str]]:
    """Extract page markers and their positions. An anchor marks the START of its page:
    ``<a id="page4"></a>``, or ``<span id="page4"></span>`` (what pandoc writes for an HTML page
    anchor - HyperWar's, src/ingestion/hyperwar_html)."""
    markers: List[Tuple[int, int, str]] = []

    for match in re.finditer(r'<(a|span) id="page(\d+)"></\1>', text):
        page_num = int(match.group(2))
        markers.append((match.start(), page_num, "anchor"))

    # Find separator markers: *\--3--*
    for match in re.finditer(r"\*\\--(\d+)--\*", text):
        page_num = int(match.group(1))
        markers.append((match.start(), page_num, "separator"))

    return sorted(markers, key=lambda x: x[0])


def _observe_oversize_image_drops(text: str) -> None:
    """Anti-swallow: the ReDoS-safe image regexes bound the URL to {0,2000}; an image whose URL
    exceeds that is silently NON-matched (dropped). This cheap secondary probe counts such
    over-bound image starts and WARNs so a dropped image is LOUD, never vanished. Fail-safe.
    """
    try:
        if not text:
            return
        dropped = len(
            re.findall(r"!\[[^\]]{0,500}\]\((?:https?://)?[^)\n]{2000,}", text)
        )
        if dropped:
            logger.warning(
                "OBSERV parser.image_dropped reason=url_too_long count=%d "
                "(url exceeds the 2000-char ReDoS bound; image not extracted)",
                dropped,
            )
    except Exception:  # noqa: BLE001 - observability never breaks extraction
        pass


def extract_images(text: str, base_url: str = "") -> List[Tuple[str, str, str, str]]:
    """
    Extract images from markdown, avoiding duplicates.

    Returns:
        List of (type, resource_id_or_url, alt_text, external_url)
    """
    images: List[Tuple[str, str, str, str]] = []
    seen: set[str] = (
        set()
    )  # dedupe on image IDENTITY (resource_id/url), NOT alt_text: source
    # images frequently share an empty/identical alt, which previously collapsed many
    # distinct images into one.
    # Defensive bound: the branches below are lazy-regex scans over UNTRUSTED imported
    # markdown; cap the scanned length so a pathological page can't pin CPU (quadratic
    # worst case). A few hundred KB is far above any real chapter's markdown.
    if text and len(text) > 500_000:
        logger.warning(
            "OBSERV parser.text_truncated area=images original_len=%d cap=500000 "
            "(content past the cap is NOT scanned for images)",
            len(text),
        )
        text = text[:500_000]

    _extract_combined_images(text, images, seen)
    _extract_embedded_images(text, images, seen)
    _extract_html_combined_images(text, images, seen)
    _extract_external_images(text, images, seen)
    _extract_relative_external_images(text, images, seen, base_url)
    _extract_html_anchor_images(text, images, seen, base_url)
    _observe_oversize_image_drops(text)

    return images


def _extract_combined_images(text: str, images: list, seen: "set[str]") -> None:
    # Combined format: [![alt](:/resource-id)](https://url)
    # This is a clickable image with both Joplin resource and external URL
    for match in re.finditer(
        r"!\[([^\]]{0,500})\]\(:([^)]{0,500})\)\]\((https?://[^\)]{0,2000})\)", text
    ):
        alt_text = match.group(1)
        resource_id = match.group(2)
        url = match.group(3)
        key = f"combined:{resource_id}:{url}"
        if key not in seen:
            images.append(("combined", resource_id, alt_text, url))
            seen.add(key)


def _extract_embedded_images(text: str, images: list, seen: "set[str]") -> None:
    # Embedded images only: ![alt](:/resource-id) - standalone Joplin resource
    # Use negative lookahead to exclude those followed by ](url). The resource capture is
    # `[^)]*?` (not `.*?`) so a Joplin COMBINED shape `[![a](:/r)](url)` is NOT re-matched
    # here as a spurious embedded row (the capture stops at the first ')').
    for match in re.finditer(r"!\[([^\]]{0,500})\]\(:([^)]{0,500}?)\)(?!\]\()", text):
        alt_text = match.group(1)
        resource_id = match.group(2)
        key = f"embedded:{resource_id}"
        if key not in seen:
            images.append(("embedded", resource_id, alt_text, ""))
            seen.add(key)


def _extract_html_combined_images(text: str, images: list, seen: "set[str]") -> None:
    # HTML-derived combined: [![alt](IMG_URL)](LINK_URL) — produced when an <img> is wrapped
    # in an <a> (ibiblio thumbnail-linked-to-full-res). The inner IMG_URL is the image the
    # importer selected (full-res); LINK_URL (often <...>) is ignored. Must run BEFORE the
    # external branch, whose (?<!\[) lookbehind would otherwise skip these.
    for match in re.finditer(
        r"\[!\[([^\]]{0,500})\]\((https?://[^\)]{0,2000})\)\]\(<?[^)]{0,2000}>?\)", text
    ):
        alt_text = match.group(1)
        url = match.group(2)
        key = f"external:{url}"
        if key not in seen:
            images.append(("external", url, alt_text, ""))
            seen.add(key)


def _extract_external_images(text: str, images: list, seen: "set[str]") -> None:
    # External images only: ![alt](https://url) - standalone external image
    # Must NOT be preceded by [![ (which would make it part of combined format)
    for match in re.finditer(
        r"(?<!\[)!\[([^\]]{0,500})\]\((https?://[^\)]{0,2000})\)(?!\])", text
    ):
        alt_text = match.group(1)
        url = match.group(2)
        key = f"external:{url}"
        if key not in seen:
            images.append(("external", url, alt_text, ""))
            seen.add(key)


def _extract_relative_external_images(
    text: str, images: list, seen: "set[str]", base_url: str
) -> None:
    """RELATIVE embedded images: ``![alt](icn/USA-E-XChannel-37.jpg)`` — a bare/relative image src
    (the HyperWar ``<img src=icn/...>`` figure convention, converted by html_to_markdown). Resolve
    against ``base_url`` so the image is fetchable; image assets only (skip arbitrary relative
    links). No-op without a base_url. Deduped against the same ``seen`` set as the absolute branch.
    """
    if not base_url:
        return
    from urllib.parse import urljoin

    _img_exts = (".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".webp")
    # ![alt](target) where target is NOT http(s) and NOT a Joplin resource (:/...).
    for match in re.finditer(
        r"(?<!\[)!\[([^\]]{0,500})\]\(([^):][^)]{0,2000})\)(?!\])", text
    ):
        alt_text = match.group(1)
        target = match.group(2).strip()
        low = target.lower()
        if low.startswith("http://") or low.startswith("https://"):
            continue  # handled by the absolute branch
        if not low.endswith(_img_exts):
            continue  # only genuine image assets
        url = urljoin(base_url, target)
        if not url.lower().startswith(("http://", "https://")):
            continue
        key = f"external:{url}"
        if key not in seen:
            images.append(("external", url, alt_text, ""))
            seen.add(key)


_HTML_ANCHOR_RE = re.compile(
    # ReDoS-safe: ALL quantifiers bounded so malformed/unclosed <a in untrusted HTML cannot cause
    # O(n^2) forward scans (attr span, href, and body each capped). Shared by the image + map anchor
    # extractors so there is ONE anchor matcher, not two.
    r"<a\s[^>]{0,500}?href=[\"']?([^\"' >]{0,2000})[^>]{0,500}>(.{0,4000}?)</a>",
    re.IGNORECASE | re.DOTALL,
)


def _iter_html_anchors(text: str):
    """Yield (href, link_text) for each raw-HTML <a href=...>...</a> that pandoc passed through
    verbatim. link_text has inner tags stripped (bounded). Single shared matcher (ReDoS-safe).
    """
    for m in _HTML_ANCHOR_RE.finditer(text):
        href = m.group(1)
        link_text = re.sub(r"<[^>]{0,2000}>", " ", m.group(2)).strip()
        yield href, link_text


def _extract_html_anchor_images(
    text: str, images: list, seen: "set[str]", base_url: str
) -> None:
    """RAW-HTML image/map anchors that pandoc PASSES THROUGH verbatim (it does not always convert
    ``<a href><img></a>`` to markdown). HyperWar maps arrive two ways and BOTH were being missed:
      1. embedded+linked: ``<a href="maps/USA-E-XChannel-1.jpg"...><img src="maps/...-1m.jpg"></a>``
         (thumbnail linked to full-res) -> capture the full-res HREF as the image URL.
      2. link-only: ``<a href="maps/USA-E-XChannel-XII.jpg"...>Map XII</a>`` (no <img>) -> the map
         exists only as a hyperlink; capture it too (the link TEXT, e.g. "Map XII", is the alt).
    Image assets only (href ends in an image extension); relative targets resolved via base_url.
    Deduped against the same ``seen`` set. No-op without a match.
    """
    from urllib.parse import urljoin

    _img_exts = (".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".webp")

    def _add(target: str, alt_text: str) -> None:
        target = (target or "").strip().strip("<>")
        if not target or not target.lower().endswith(_img_exts):
            return
        url = target
        if not url.lower().startswith(("http://", "https://")):
            if not base_url:
                return
            url = urljoin(base_url, target)
        if not url.lower().startswith(("http://", "https://")):
            return
        key = f"external:{url}"
        if key not in seen:
            images.append(("external", url, (alt_text or "").strip()[:500], ""))
            seen.add(key)

    # <a href="IMG">...</a> — captures BOTH the <img>-wrapped (full-res href) and the link-only
    # ("Map XII") forms. The href is the image; link text recovered for the alt. Shared matcher.
    for href, link_text in _iter_html_anchors(text):
        _add(href, link_text)


def extract_maps(text: str, base_url: str = "") -> List[Tuple[str, str]]:
    """
    Extract map references.

    Captures ``[Map X](url)`` links. ``url`` may be ABSOLUTE (``https://...``) or RELATIVE
    (``maps/USA-E-XChannel-VIII.jpg`` — the HyperWar convention for author-curated maps); a relative
    target is resolved against ``base_url`` (the source document's URL) so the map becomes fetchable.
    A relative target is only accepted when it looks like an image/map asset (image extension or a
    ``maps/`` path) so arbitrary intra-doc links are not absolutized.

    Returns:
        List of (map_id, url)
    """
    # Defensive bound (ReDoS/CPU): cap the scanned length like extract_images, so a
    # pathological multi-MB page can't pin CPU on the regex scans below.
    if text and len(text) > 500_000:
        logger.warning(
            "OBSERV parser.text_truncated area=maps original_len=%d cap=500000 "
            "(maps past the cap are NOT scanned)",
            len(text),
        )
        text = text[:500_000]
    maps = []
    seen_urls = set()

    # [Map X](target) — markdown link form. target may be absolute http(s) OR a relative asset path.
    for match in re.finditer(
        r"\[Map\s+([^\]]{1,500})\]\(([^)]{1,2000})\)", text, re.IGNORECASE
    ):
        map_id = match.group(1).strip()
        target = match.group(2).split("%20target=")[0].strip().rstrip()
        url = _resolve_map_target(target, base_url)
        if url and url not in seen_urls:
            maps.append((map_id, url))
            seen_urls.add(url)

    # RAW-HTML anchor form (what pandoc passes through for HyperWar) — shared matcher, hi-res href.
    _extract_anchor_maps(text, base_url, maps, seen_urls)
    return maps


def _extract_anchor_maps(
    text: str, base_url: str, maps: list, seen_urls: "set[str]"
) -> None:
    """Capture maps from raw-HTML <a href=...>...</a> anchors (pandoc passthrough): embedded
    <a href="maps/-N.jpg"><img></a> (hi-res href) and link-only <a href="maps/-XII.jpg">Map XII</a>.
    An anchor is a MAP when its link text starts with "Map" OR its href resolves into a .../maps/
    asset. Captured at HI-RES (the href, not any -Nm thumbnail). Deduped via seen_urls.
    """
    for href, link_text in _iter_html_anchors(text):
        url = _resolve_map_target(href.strip().strip("<>"), base_url)
        if not url or url in seen_urls:
            continue
        lt = (link_text or "").strip()
        if not (re.match(r"(?i)^map\b", lt) or "/maps/" in url.lower()):
            continue
        map_id = (
            lt
            if re.match(r"(?i)^map\b", lt)
            else url.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        )
        maps.append((map_id, url))
        seen_urls.add(url)


_MAP_IMG_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".webp")


def _resolve_map_target(target: str, base_url: str) -> str:
    """Resolve a ``[Map X](target)`` target to an absolute URL, or "" if it should be skipped.

    Absolute http(s) passes through. A RELATIVE target is resolved against ``base_url`` ONLY when it
    looks like a map/image asset (image extension or a ``maps/`` path) and a base_url is present —
    so arbitrary intra-document links are not absolutized.
    """
    if not target:
        return ""
    from urllib.parse import urljoin

    low = target.lower()
    if low.startswith("http://") or low.startswith("https://"):
        return target
    is_asset = low.endswith(_MAP_IMG_EXTS) or low.startswith("maps/") or "/maps/" in low
    if not (base_url and is_asset):
        return ""
    url = urljoin(base_url, target)
    return url if url.lower().startswith(("http://", "https://")) else ""


def extract_footnotes(text: str) -> List[Tuple[int, str, str]]:
    """
    Extract footnote references AND their citation text.

    Two markdown forms (HyperWar/ibiblio convention) are paired by number:
      * in-text marker:    ``<sup>[N](url#fnN)</sup>`` — gives the number + url.
      * end-of-chapter def: ``[N](url#cnN). <citation text>`` (under a "Footnotes"
        heading, usually block-quoted) — gives the citation TEXT.

    Returns a list of ``(number, url, text)`` ordered by first appearance. The
    citation text was previously DROPPED, leaving the bibliography resolver nothing
    to search; it is now captured. ``text`` is "" when no definition is present;
    ``url`` falls back to the #cn url if a number appears only in a definition.
    """
    # Defensive bound (ReDoS/CPU): cap the scanned length like extract_images.
    if text and len(text) > 500_000:
        logger.warning(
            "OBSERV parser.text_truncated area=footnotes original_len=%d cap=500000 "
            "(footnotes past the cap are NOT scanned)",
            len(text),
        )
        text = text[:500_000]
    marker_url: Dict[int, str] = {}
    order: List[int] = []

    # In-text marker: <sup>[N](url#fnN)</sup> (or url without the fragment).
    for match in re.finditer(r'<sup>\[(\d+)\]\((https?://[^\)"]+)', text):
        number = int(match.group(1))
        if number not in marker_url:
            marker_url[number] = match.group(2)
            order.append(number)

    # End-of-chapter definition: [N](url#cnN). <citation text up to end of line>
    # The anchor is #cn (definition) vs #fn (marker). Capture the trailing text.
    def_text: Dict[int, str] = {}
    def_url: Dict[int, str] = {}
    for match in re.finditer(r'\[(\d+)\]\((https?://[^\)"]*#cn\d+)\)\.?\s*(.*)', text):
        number = int(match.group(1))
        if number in def_text:
            continue
        def_url[number] = match.group(2)
        # The ibiblio definition markup wraps the anchor in literal brackets:
        # ``[<a name=fnN href=#cnN>N</a>] text`` -> after link conversion the captured
        # trailing text begins with the stray closing ``]``. Strip a single leading
        # bracket + surrounding punctuation/space so the stored citation text is clean.
        raw = match.group(3).strip()
        raw = re.sub(r"^\]\s*", "", raw).strip()
        def_text[number] = raw
        if number not in marker_url:
            order.append(number)  # definition-only footnote (no in-text marker)

    footnotes: List[Tuple[int, str, str]] = []
    for number in order:
        url = marker_url.get(number) or def_url.get(number, "")
        footnotes.append((number, url, def_text.get(number, "")))
    return footnotes


def split_into_blocks(text: str) -> List[Tuple[str, bool]]:
    """Split text into paragraph blocks, preserving a block-quote flag.

    Returns a list of ``(paragraph_text, is_quote)``. A block is a quote when
    all of its non-empty lines begin with a ``>`` marker (as emitted by the
    markdown-structure block-quote repair). The ``>`` markers are stripped from
    the returned text, but the fact that it *was* a quote is preserved so the
    parser can keep the quotation distinct from the author's own prose.

    ``split_into_paragraphs`` delegates here for the plain-text view, so existing
    callers are unaffected.
    """
    # Remove page/footnote/separator markers first (as before), but do NOT strip
    # blockquote markers yet — we need them to detect quote blocks.
    working = _PAGE_MARKER_PATTERN.sub("", text)
    working = _FOOTNOTE_PATTERN.sub("", working)
    working = _SEPARATOR_PATTERN.sub("\n\n", working)

    blocks = working.split("\n\n")
    result: List[Tuple[str, bool]] = []
    for block in blocks:
        raw_lines = block.split("\n")
        nonempty = [ln for ln in raw_lines if ln.strip()]
        is_quote = bool(nonempty) and all(
            _BLOCKQUOTE_PATTERN.match(ln.lstrip()) for ln in nonempty
        )
        # Strip blockquote markers to get the clean text (same as before).
        cleaned = "\n".join(_BLOCKQUOTE_PATTERN.sub("", ln) for ln in raw_lines)
        cleaned = cleaned.strip()
        if not cleaned:
            continue
        # Skip standalone headings (unchanged behavior). JUSTIFIED non-emission (not a silent
        # content drop): a lone markdown heading (``#``/``##``, incl. asterisk-wrapped ``## *..*``)
        # is structural navigation, NOT provenance-bearing prose — the chapter/section title is
        # captured separately as document metadata, so dropping the heading block here loses no
        # citable content and does not warrant an anomaly OBSERV.
        if _HEADING_PATTERN.match(cleaned):
            continue
        result.append((cleaned, is_quote))
    return result


def split_into_paragraphs(text: str) -> List[str]:
    """Split text into paragraphs, preserving all content.

    Thin wrapper over :func:`split_into_blocks` returning only the text, so
    existing callers keep their exact behavior (blockquote markers stripped).
    """
    return [para for para, _is_quote in split_into_blocks(text)]


def _build_page_map(content: str) -> Dict[int, int]:
    """Build map of text position to page number."""
    page_markers_raw = extract_page_markers(content)
    return {pos: page_num for pos, page_num, _ in page_markers_raw}


_ANY_PAGE_MARKER = re.compile(r'<(a|span) id="page(\d+)"></\1>|\*\\--(\d+)--\*')


def _paginate(content: str) -> Tuple[str, Dict[int, int], List[Tuple[int, str]]]:
    """(content WITHOUT page markers, {position in it: page}, [(page, marker type)]).

    Paragraph text has its markers removed (split_into_blocks), so paragraphs are located in the
    marker-free content: a marker inside a paragraph's first 60 characters (a HyperWar paragraph
    rejoined across a page) would otherwise make it unlocatable."""
    pieces: List[str] = []
    page_map: Dict[int, int] = {}
    markers: List[Tuple[int, str]] = []
    size = 0
    last = 0
    for m in _ANY_PAGE_MARKER.finditer(content):
        chunk = content[last : m.start()]
        pieces.append(chunk)
        size += len(chunk)
        page = int(m.group(2) or m.group(3))
        page_map[size] = page
        markers.append((page, "anchor" if m.group(2) else "separator"))
        last = m.end()
    pieces.append(content[last:])
    return "".join(pieces), page_map, markers


def _page_markers_for(markers, paragraphs) -> List[PageMarker]:
    """Each page marker with the paragraph it falls in (a paragraph running onto that page), else
    the first paragraph starting on it; 0 when no paragraph has that page."""
    out = []
    for page, kind in markers:
        number = next(
            (
                p.absolute_number
                for p in paragraphs
                if p.page_number is not None
                and p.page_number < page <= (p.page_end or p.page_number)
            ),
            None,
        )
        if number is None:
            number = next(
                (p.absolute_number for p in paragraphs if p.page_number == page), 0
            )
        out.append(
            PageMarker(page_number=page, paragraph_number=number, marker_type=kind)
        )
    return out


def _find_page_number(
    para_text: str, content: str, page_map: Dict[int, int], search_from: int = 0
) -> Tuple[Optional[int], int]:
    """Find the page in effect for a paragraph by its position in the ORIGINAL content.

    Returns ``(page_number, found_pos)``. ``search_from`` is the previous paragraph's position so
    the scan is MONOTONIC: a paragraph whose leading text repeats an earlier phrase resolves to the
    correct *sequential* occurrence instead of always the first (the content.find-from-0 bug). A
    paragraph that cannot be located at all (marker-stripping shifted its prefix) returns
    ``found_pos == -1`` so the caller can surface it; the returned position is otherwise clamped to
    never precede ``search_from`` so the caller's cursor cannot go backwards.
    """
    probe = para_text[:60]
    para_pos = content.find(probe, search_from)
    if para_pos == -1:
        # Not found at/after the cursor. Try a from-start search ONLY to recover a page estimate
        # for a normalized block; the position we hand back is still clamped to search_from so the
        # cursor stays monotonic (a genuinely-absent prefix returns -1 and the caller falls back).
        fallback = content.find(probe)
        located = max(fallback, search_from) if fallback != -1 else search_from
        current_page = _page_in_effect(page_map, located)
        return current_page, (-1 if fallback == -1 else located)
    current_page = _page_in_effect(page_map, para_pos)
    return current_page, para_pos


def _page_in_effect(page_map: Dict[int, int], located: int) -> Optional[int]:
    """The page whose marker is at or before ``located`` (markers precede the text they page)."""
    current_page = None
    for pos in sorted(page_map.keys()):
        if pos <= located:
            current_page = page_map[pos]
        else:
            break
    return current_page


def _create_paragraphs(
    paragraph_blocks: List[Tuple[str, bool]],
    start_paragraph_num: int,
    section_id: str,
    file_path: Path,
    content: str,
    page_map: Dict[int, int],
) -> List[Paragraph]:
    """Create paragraph objects with page numbers.

    ``paragraph_blocks`` is a list of ``(text, is_quote)`` from
    :func:`split_into_blocks`; the quote flag is preserved on each Paragraph so
    quotations stay distinct from the author's own prose.
    """
    paragraphs = []
    search_from = 0  # monotonic cursor into the original content
    unlocated = 0

    for i, (para_text, is_quote) in enumerate(paragraph_blocks):
        para_num = start_paragraph_num + i
        current_page, found_pos = _find_page_number(
            para_text, content, page_map, search_from
        )
        if found_pos == -1:
            # The paragraph text wasn't locatable in the original content (marker stripping
            # shifted its prefix). Page provenance falls back to the last-known page; surface it
            # (a provenance mis-bind risk), never silently assert a wrong page.
            if page_map:
                unlocated += 1
        else:
            search_from = found_pos + 1  # advance so repeats resolve sequentially

        page_end = None
        if found_pos != -1 and current_page is not None:
            end_page = _page_in_effect(page_map, found_pos + max(len(para_text) - 1, 0))
            if end_page is not None and end_page != current_page:
                page_end = end_page
        para = Paragraph(
            absolute_number=para_num,
            text=para_text,
            page_number=current_page,
            page_end=page_end,
            section_id=section_id,
            source_file=file_path.name,
            is_quote=is_quote,
        )
        paragraphs.append(para)

    if unlocated:
        logger.warning(
            "OBSERV parser.page_unlocated section=%s file=%s paragraphs=%d status=warn "
            "(page_number fell back to last-known; position not found in source)",
            section_id or "full",
            file_path.name,
            unlocated,
        )

    return paragraphs


_PROBE_BASE = "https://unresolved.invalid/"


def _warn_unresolvable_media(content: str, base_url: str, book: str) -> int:
    """Phase 1 guard: relative image/map references (``img/…``, ``icn/…``, ``maps/…``) resolve
    only against an http(s) ``base_url``. Without one they are silently skipped, so count them by
    running the SAME extractors against a probe base (no second set of regexes) and raise an
    actionable alert — once per book, not once per section. Pandoc-extracted ``media/…`` files
    (EPUB/DOCX) have no web URL to supply, so they are not counted. Returns the count.
    """
    if (base_url or "").lower().startswith(("http://", "https://")):
        return 0
    probed = [u for _k, u, _a, _e in extract_images(content, base_url=_PROBE_BASE)]
    probed += [u for _id, u in extract_maps(content, base_url=_PROBE_BASE)]
    lost = len(
        {
            u
            for u in probed
            if u.startswith(_PROBE_BASE) and not u.startswith(_PROBE_BASE + "media/")
        }
    )
    if lost > 0:
        from src.utils.observe import observe_warn_counted

        observe_warn_counted(
            "parser",
            "media_unresolvable",
            key=(book,),
            every=10**9,  # once per book per process, not again every 25th section
            note=(
                "relative images/maps were DROPPED because source_url is not an http(s) URL; "
                "re-upload the source with --metadata source-url=<page URL>, re-run Phase 0, "
                "then re-run Phase 1"
            ),
            book=book,
            refs=lost,
        )
    return lost


def _add_images_to_doc(
    doc: MarkdownDocument,
    content: str,
    base_url: str = "",
    anchor_content: Optional[str] = None,
    notes=None,
) -> None:
    """Extract and add images to document. ``anchor_content`` is the text the paragraphs were
    built from (the notes-separated body) when it differs from ``content``; ``notes`` are the
    section's notes, to anchor an image linked only from a note."""
    for img_type, resource_or_url, alt_text, external_url in extract_images(
        content, base_url=base_url
    ):
        if img_type == "combined":
            img = Image(
                type="combined",
                resource_id=resource_or_url,
                url=external_url,
                alt_text=alt_text,
                paragraph_number=0,
            )
        elif img_type == "embedded":
            img = Image(
                type="embedded",
                resource_id=resource_or_url,
                url=None,
                alt_text=alt_text,
                paragraph_number=0,
            )
        else:  # external
            img = Image(
                type="external",
                resource_id=None,
                url=resource_or_url,
                alt_text=alt_text,
                paragraph_number=0,
            )
        doc.images.append(img)
    _pair_image_captions(doc, content, base_url)
    _anchor_images_to_paragraphs(
        doc,
        content if anchor_content is None else anchor_content,
        notes,
        base_url=base_url,
    )


_MIN_PROBE_CHARS = 20  # shorter probes (page markers, one-liners) match too easily


def _probe_regex(text: str):
    """Regex for a paragraph's leading words tolerant of the raw source's line breaks and
    blockquote '>' prefixes (para.text has them stripped)."""
    words = text.split()[:8]
    if not words or len(" ".join(words)) < _MIN_PROBE_CHARS:
        return None
    return re.compile(r"[\s>]+".join(re.escape(w) for w in words))


def _paragraph_positions(paragraphs, content: str) -> list:
    """(offset, absolute_number) of each paragraph locatable in ``content``, in document order.
    Searched sequentially so repeated text resolves in order; misses are counted (OBSERV).
    """
    out = []
    cursor = 0
    missed = 0
    for para in paragraphs:
        rx = _probe_regex((para.text or "").strip())
        m = rx.search(content, cursor) if rx else None
        if m is None:
            missed += 1
            continue
        out.append((m.start(), para.absolute_number))
        cursor = m.start() + 1
    if missed:
        logger.info(
            "OBSERV parser.image_anchor_probe_miss status=ok paragraphs=%d located=%d "
            "(images after an unlocated paragraph anchor to the previous located one)",
            missed,
            len(out),
        )
    return out


# A token must stand as a whole reference, delimited the way markdown / HTML delimit a URL:
# ![](X) [t](X "T") ![](<X>) src="X" src='X' src=X> - so img/X.jpg never matches other/img/X.jpg.
_REF_BEFORE = r"(?<![^\s\"'(=<])"
_REF_AFTER = r"(?![^\s\"')#?>])"


@functools.lru_cache(maxsize=4096)
def _ref_regex(token: str) -> "re.Pattern[str]":
    return re.compile(_REF_BEFORE + re.escape(token) + _REF_AFTER)


def _image_pos(token: str, content: str, cursor: int) -> int:
    """Offset of an image token at/after ``cursor``; else from the start (out-of-order image)."""
    if not token:
        return -1
    rx = _ref_regex(token)
    m = rx.search(content, cursor) or rx.search(content)
    return m.start() if m else -1


def _safe_urlsplit(url: str):
    """``urlsplit`` or None: a malformed URL from an untrusted page (``https://[x/a.jpg``) raises
    ValueError, which must not abort the chapter's parse."""
    from urllib.parse import urlsplit

    try:
        return urlsplit(url)
    except ValueError:
        logger.warning(
            "OBSERV parser.image_url_malformed status=warn url=%.200s (unparseable URL: only its "
            "exact text can locate it; fix the reference in the source markup)",
            url,
        )
        return None


def _relative_forms(u, base) -> List[str]:
    """Root-relative and document-relative forms of ``u`` against ``base`` (same host only)."""
    import posixpath

    if not (base and u and base.netloc and u.path):
        return []
    if u.netloc.lower() != base.netloc.lower():
        return []
    query = f"?{u.query}" if u.query else ""
    rel = posixpath.relpath(u.path, posixpath.dirname(base.path) or "/") + query
    forms = [u.path + query, rel]  # /hyperwar/.../img/X.jpg ; img/X.jpg or ../img/X.jpg
    if not rel.startswith("."):
        forms.append("./" + rel)  # urljoin collapsed a written "./img/X.jpg"
    return forms


def _image_tokens(img, base_url: str) -> List[str]:
    """The forms an image reference can be WRITTEN in the source, most specific first. Image URLs
    are resolved to absolute against the chapter's source URL, but the source keeps them as
    written (HyperWar: ``img/X.jpg``), so the absolute form alone never matched (XChannel-8:
    39/39 unanchored). Each form is derived exactly from the URL and the base (no basename
    guessing: ``icn/X.jpg`` and ``img/X.jpg`` are different images)."""
    base = _safe_urlsplit((base_url or "").split("#", 1)[0]) if base_url else None
    tokens: List[str] = []
    for ref, is_resource in ((img.url, False), (img.resource_id, True)):
        if not ref:
            continue
        if is_resource and ref.startswith("/"):
            tokens.append(":" + ref)  # Joplin resource, written (:/id)
        tokens.append(ref)
        if "://" in ref:
            tokens.extend(_relative_forms(_safe_urlsplit(ref), base))
    return list(dict.fromkeys(tokens))


def _anchor_one(
    img, content: str, positions: list, cursor: int, base_url: str = ""
) -> tuple:
    """(paragraph_number or None, new_cursor, found) for one image: the nearest LOCATED paragraph
    at or before it (short paragraphs cannot be probed, so this can be an earlier one); None
    when no located paragraph precedes it or it is not in ``content``; ``found`` says whether it
    was located.
    """
    pos = -1
    for token in _image_tokens(img, base_url):
        pos = _image_pos(token, content, cursor)
        if pos != -1:
            break
    if pos == -1:
        return None, cursor, False
    preceding = [num for off, num in positions if off <= pos]
    # An image before ALL text follows no paragraph: leave it unanchored (0) rather than claim it.
    return (preceding[-1] if preceding else None), max(cursor, pos + 1), True


def _anchor_in_notes(img, notes, base_url: str) -> bool:
    """Link an image that is not in the narrative to the note whose text links it ("See naval
    command chart"): its paragraph is the one citing that note. True when found."""
    tokens = _image_tokens(img, base_url)
    for note in notes or ():
        if any(_image_pos(t, note.text or "", 0) != -1 for t in tokens):
            img.note_number = note.number
            img.paragraph_number = note.paragraph_number
            return True
    return False


def _unanchored_kind(doc, found: bool, positions: list) -> str:
    """Why a FOUND-or-not image got no paragraph: 'leading' (no located paragraph precedes it),
    'unprobeable' (the file has no paragraph long enough to locate - nothing to fix), 'probe_miss'
    (long paragraphs exist but none was located), or 'not_found'."""
    if not found:
        return "not_found"
    if positions:
        return "leading"
    probeable = any(_probe_regex((p.text or "").strip()) for p in doc.paragraphs)
    return "probe_miss" if probeable else "unprobeable"


def _anchor_images_to_paragraphs(
    doc: MarkdownDocument, content: str, notes=None, base_url: Optional[str] = None
) -> None:
    """Set each image's ``paragraph_number`` to the paragraph it follows in the source. It was
    hard-coded 0, so a figure placed BETWEEN paragraphs could not be traced to any sub-event
    (XChannel ch8: 6 figures with no provenance link). An image linked only from a note
    (``notes``) takes that note's citing paragraph. An image no LOCATED paragraph precedes (a
    frontispiece, or only short unprobeable paragraphs before it) stays 0 by design and is
    counted at INFO; images not found in the source, or in a file whose long paragraphs could
    not be located, stay 0 and are alerted."""
    positions = _paragraph_positions(doc.paragraphs, content)
    cursor = 0
    counts: Dict[str, int] = {}
    base: str = (
        base_url if base_url is not None else (getattr(doc, "source_url", "") or "")
    )
    for img in doc.images:
        num, cursor, found = _anchor_one(img, content, positions, cursor, base)
        if num is not None:
            img.paragraph_number = num
        elif not found and _anchor_in_notes(img, notes, base):
            continue
        else:
            kind = _unanchored_kind(doc, found, positions)
            counts[kind] = counts.get(kind, 0) + 1
    _report_unanchored_images(counts, len(doc.images), len(positions))


def _report_unanchored_images(counts: Dict[str, int], total: int, located: int) -> None:
    benign = {k: counts[k] for k in ("leading", "unprobeable") if counts.get(k)}
    if benign:
        logger.info(
            "OBSERV parser.image_leading status=ok %s (no LOCATED paragraph precedes them: a "
            "frontispiece, or only short paragraphs; paragraph_number 0 by design)",
            " ".join(f"{k}={v}" for k, v in benign.items()),
        )
    for kind, hint in (
        (
            "not_found",
            "image position not found in source; check how the image is written in the "
            "source against src/parser._image_tokens / _REF_BEFORE / _REF_AFTER",
        ),
        (
            "probe_miss",
            "no paragraph could be located in the source although long paragraphs exist; "
            "check src/parser._probe_regex against this file's paragraph markup",
        ),
    ):
        if counts.get(kind):
            logger.warning(
                "OBSERV parser.image_unanchored status=warn reason=%s images=%d file_images=%d "
                "located_paragraphs=%d (paragraph_number left 0 - %s)",
                kind,
                counts[kind],
                total,
                located,
                hint,
            )


# An ALL-CAPS figure caption: the line's letters are (mostly) uppercase and it has ≥2 words
# (HyperWar convention, e.g. "ALLIED INVASION CHIEFS. Left to right: ..."). The caption carries the
# figure's rich context (names/place/action) — pairing it to the image lets M3/M5 link it.
# Where a caption's headline ends: ':', the italic continuation '*', or a sentence period that is
# not an abbreviation's ("U.S. TROOPS LAND.", "GEN. EISENHOWER" keep going).
_CAPTION_HEAD_END = re.compile(
    r"[:*]|(?<!\b[A-Z])(?<!\b[A-Z]{2})(?<!\b[A-Z]{3})\.(?:\s|$)"
)
_NOT_CAPTION = re.compile(
    r"^(?:#|\*{0,2}\\?-{1,2}\d)"
)  # "## CHAPTER VIII", "*--271--*"


def _is_caption_line(line: str) -> bool:
    s = line.strip()
    if len(s) < 8 or " " not in s or _NOT_CAPTION.match(s):
        return False
    # Take the leading heading-ish run (up to the first sentence break, or the italic continuation
    # HyperWar appends without a period: "PARACHUTE TROOPS *marching onto airfield...*").
    head = _CAPTION_HEAD_END.split(s, maxsplit=1)[0]
    letters = [c for c in head if c.isalpha()]
    if len(letters) < 6:
        return False
    upper = sum(1 for c in letters if c.isupper())
    return upper / len(letters) >= 0.85


def _pair_image_captions(
    doc: MarkdownDocument, content: str, base_url: str = ""
) -> None:
    """Pair the ALL-CAPS caption that immediately FOLLOWS an image into the image's ``caption``
    field. HyperWar renders a figure as ``<a href="img/X"><img src="icn/X"></a>`` (or ``![](src)``)
    then a caption paragraph; the caption (not alt text) holds the names/place/action. The image
    line is found with the SAME exact reference forms as anchoring (``_image_tokens``): matching
    only markdown ``![`` lines missed every HTML figure (XChannel-8: 0 of 39 captioned).
    Fail-safe: no caption -> stays None."""
    try:
        lines = content.splitlines()
        paired = 0
        for img in doc.images:
            if img.caption:
                continue
            caption = _find_following_caption(lines, _image_tokens(img, base_url))
            if caption:
                img.caption = caption
                paired += 1
        if paired:
            logger.info(
                "OBSERV image.captioned paired=%d of %d", paired, len(doc.images)
            )
    except Exception as e:  # noqa: BLE001 - captioning must never break parse
        logger.warning(
            "OBSERV image.caption_pairing_failed status=warn book=%s error=%s (captions for "
            "this file were skipped; fix src/parser._pair_image_captions for this input)",
            doc.book,
            e,
        )


_LINE_QUOTE = re.compile(r"^[ \t]{0,3}(?:>[ \t]?){0,3}")
_IMAGE_LINE = re.compile(r"<img\b|!\[", re.IGNORECASE)
_GROUP_WINDOW = 8  # lines scanned each way for a figure group's caption


# A caption SHARED by a figure group names each image's position. Only such a caption may go to
# an image it does not directly follow, and only in the matching DIRECTION: an image ABOVE the
# caption (reached past other images) needs "(above)"/"(top)"/"upper left"; an image BELOW it
# needs "(below)"/"(bottom)"/"lower right". ("Left to right" names people in ONE photo, and a
# bare word like "(Lower Normandy)" is not a position.)
_CUE_UPPER = re.compile(
    r"\((?:above|top)\)|\bupper\s+(?:left|right)\b|\bat\s+(?:the\s+)?top\b",
    re.IGNORECASE,
)
_CUE_LOWER = re.compile(
    r"\((?:below|bottom)\)|\blower\s+(?:left|right)\b|\bat\s+(?:the\s+)?bottom\b",
    re.IGNORECASE,
)
_TAGS = re.compile(r"<[^>]{0,300}>")


def _group_caption(lines: "list[str]", indices) -> Tuple[Optional[str], bool]:
    """(caption, passed_another_image): walk ``indices`` past blank / bare '>' / other image
    lines; the first real text line is the caption if it is an ALL-CAPS caption line, else None
    (narrative or a page marker stops it)."""
    passed = False
    for i in indices:
        text = _LINE_QUOTE.sub("", lines[i]).strip()
        if _IMAGE_LINE.search(text):
            passed = True
            continue
        if not _TAGS.sub("", text).strip():
            continue  # blank, bare '>' or a tag-only anchor line (<span id="page279"></span>)
        if not _is_caption_line(text):
            return None, passed
        # markdown emphasis is a conversion artefact of the printed italics, not caption text
        return re.sub(r"\s+", " ", text.replace("*", "")).strip(), passed
    return None, passed


def _caption_for_line(lines: "list[str]", idx: int) -> Optional[str]:
    after = range(idx + 1, min(len(lines), idx + 1 + _GROUP_WINDOW))
    caption, passed = _group_caption(lines, after)
    if caption and (not passed or _CUE_UPPER.search(caption)):
        return (
            caption  # directly follows it, or the group caption names an image above it
        )
    before = range(idx - 1, max(-1, idx - 1 - _GROUP_WINDOW), -1)
    caption, _passed = _group_caption(lines, before)
    return caption if caption and _CUE_LOWER.search(caption) else None


def _find_following_caption(lines: "list[str]", tokens: List[str]) -> Optional[str]:
    """The caption of the image's FIGURE, blockquote prefix and emphasis removed; else None.
    Only lines DISPLAYING the image count (an in-text "Map 2" link is not the figure). HyperWar
    shares one caption across a group of adjacent images, after the whole group ("TERRAIN ON
    OMAHA BEACH. Bluffs ... (top)") or between its rows ("PREINVASION SCENES. ... (above) ...
    (below)"); a caption reached past other images, or before the image, is taken only when it
    names the image's position in that direction (_CUE_UPPER / _CUE_LOWER)."""
    for idx, ln in enumerate(lines):
        if not any(_image_pos(t, ln, 0) != -1 for t in tokens):
            continue
        if not _IMAGE_LINE.search(ln):
            # an <a href="img/X"> left OPEN, whose <img> thumbnail starts the next line, is still
            # the figure; an in-text link ("<a href=img/X>the chart</a>") is not
            nxt = (
                _LINE_QUOTE.sub("", lines[idx + 1]).lstrip()
                if idx + 1 < len(lines)
                else ""
            )
            if "</a" not in ln.lower() and nxt.lower().startswith("<img"):
                idx += 1
            else:
                continue
        caption = _caption_for_line(lines, idx)
        if caption:
            return caption
    return None


def _add_maps_to_doc(doc: MarkdownDocument, content: str, base_url: str = "") -> None:
    """Extract maps and attach each map's in-document REFERENCE FOOTPRINT (OI3: provenance
    paramount). For every map, scan the document paragraphs for mentions (by its "Map <id>" label)
    and record {paragraph_number (MANDATORY), verbatim snippet}. Emits OBSERV maps.parse; a map with
    ZERO located references is a loud provenance-gap warning (never silently footprint-less).
    """
    maps = extract_maps(content, base_url=base_url)
    gapless = 0
    for map_id, url in maps:
        refs = _map_reference_footprint(map_id, doc.paragraphs)
        first_para = refs[0]["paragraph_number"] if refs else 0
        doc.maps.append(
            Map(
                url=url,
                description=f"Map {map_id}",
                map_id=map_id,
                paragraph_number=first_para,
                references=refs,
            )
        )
        if not refs:
            gapless += 1
            logger.warning(
                "OBSERV maps.ref_gap map_id=%s url=%s status=warn "
                "(map captured but NO in-document reference located — provenance gap)",
                map_id,
                url[:120],
            )
    if maps:
        logger.info(
            "OBSERV maps.parse maps_found=%d refs_total=%d ref_gaps=%d status=%s",
            len(maps),
            sum(len(m.references) for m in doc.maps),
            gapless,
            "warn" if gapless else "ok",
        )


def _map_reference_footprint(
    map_id: str, paragraphs: "List[Paragraph]"
) -> "List[Dict[str, Any]]":
    """Paragraphs that reference this map, by its "Map <id>" label. Returns [{paragraph_number,
    text}] (paragraph_number MANDATORY). `map_id` may be "Map VIII" or a filename stem like
    "USA-E-XChannel-1" — match the human label form ("Map VIII"/"Map 1") case-insensitively.
    """
    label = map_id.strip()
    m = re.match(r"(?i)^map\s+(.+)$", label)
    token = m.group(1).strip() if m else ""
    if not token:
        # filename-stem id (e.g. USA-E-XChannel-1): derive the trailing number/roman as the label.
        tail = re.search(r"([0-9]+|[IVXLC]+)$", label)
        token = tail.group(1) if tail else ""
    if not token:
        return []
    # Word-bounded "Map <token>" so "Map 1" does not match "Map 11".
    pat = re.compile(r"(?i)\bmap\s+" + re.escape(token) + r"\b")
    refs: "List[Dict[str, Any]]" = []
    for para in paragraphs:
        if pat.search(para.text or ""):
            refs.append(
                {
                    "paragraph_number": para.absolute_number,
                    "text": (para.text or "")[:300],
                }
            )
    return refs


def _add_footnotes_to_doc(
    doc: MarkdownDocument, content: str, notes: Optional[list] = None
) -> None:
    """Add the section's notes, each linked to the paragraph (in this section) that cites it.

    ``notes`` come from the separated notes section (src/notes). When the file has none in that
    form, fall back to the legacy markdown-link form (``extract_footnotes``). Notes cited from an
    EARLIER section of the chapter are linked, and unlinked ones reported, chapter-wide by
    ``_link_chapter_notes`` (a chapter's notes section usually sits only in its last file).
    """
    from src.notes import Note, attach_citing_paragraphs

    if not notes:
        notes = [
            Note(number=n, kind="footnote", text=t, url=u)
            for n, u, t in extract_footnotes(content)
        ]
    attach_citing_paragraphs(notes, doc.paragraphs)
    for n in notes:
        doc.footnotes.append(
            Footnote(
                number=n.number,
                url=n.url,
                paragraph_number=n.paragraph_number,
                text=n.text,
                kind=n.kind,
            )
        )


def _link_note_images(documents: List[MarkdownDocument]) -> None:
    """An image linked from a note takes the note's (chapter-wide) citing paragraph. Looked up in
    the image's OWN file: numbering can restart per file, so a chapter-wide number map could
    hand it another file's note."""
    for d in documents:
        cited_at = {f.number: f.paragraph_number for f in d.footnotes}
        for img in d.images:
            if getattr(img, "note_number", 0) and not img.paragraph_number:
                img.paragraph_number = cited_at.get(img.note_number, 0)


def _report_chapter_notes(
    book: str, chapter, footnotes: list, orphans: List[int]
) -> None:
    """OBSERV line + actionable alerts for a chapter's notes: unanchored notes, orphan markers."""
    located = sum(1 for f in footnotes if f.paragraph_number)
    logger.info(
        "OBSERV parser.notes book=%s notes=%d located=%d orphan_markers=%d kinds=%s",
        book,
        len(footnotes),
        located,
        len(orphans),
        ",".join(sorted({f.kind for f in footnotes})),
    )
    from src.utils.observe import observe_warn_counted

    if located < len(footnotes):
        observe_warn_counted(
            "parser",
            "notes_unanchored",
            key=(book, chapter),
            every=10**9,
            note=(
                "some notes have no in-text marker anywhere in the chapter, so their citing "
                "paragraph is unknown (provenance gap); check the chapter's marker markup "
                "against src/notes._HTML_MARKER / CANONICAL_MARKER"
            ),
            book=book,
            chapter=chapter,
            notes=len(footnotes),
            located=located,
            unanchored=[f.number for f in footnotes if not f.paragraph_number][:10],
        )
    if orphans:
        observe_warn_counted(
            "parser",
            "notes_orphan_markers",
            key=(book, chapter),
            every=10**9,
            note=(
                "in-text note markers have no note definition, so those citations are lost; "
                "the chapter's notes section was not recognised - check its heading / "
                "definition markup against src/notes._HEADING and _DEF_HTML/_DEF_MD"
            ),
            book=book,
            chapter=chapter,
            orphan_markers=len(orphans),
            numbers=orphans[:10],
        )


def _link_chapter_notes(documents: List[MarkdownDocument], book: str) -> None:
    """Link each note not cited in its own file to the first paragraph citing it elsewhere in the
    chapter, then report notes no paragraph cites (provenance gap) and markers no note defines (a
    notes section the parser did not recognise - the XChannel-8 0-of-102 failure). Alerts are
    chapter-level so a multi-file chapter is not flagged for markers in its other files.
    """
    from src.notes import attach_citing_paragraphs, first_citing_paragraphs

    paragraphs = [p for d in documents for p in d.paragraphs]
    footnotes = [f for d in documents for f in d.footnotes]
    markers = first_citing_paragraphs(paragraphs)
    if not footnotes and not markers:
        return
    # A note cited in its own file is already linked (_add_footnotes_to_doc); only the rest are
    # looked up chapter-wide, so restarted per-file numbering keeps its local citation.
    attach_citing_paragraphs(
        [f for f in footnotes if not f.paragraph_number], paragraphs
    )
    _link_note_images(documents)
    orphans = sorted(set(markers) - {f.number for f in footnotes})
    _report_chapter_notes(book, _chapter_key(documents[0]), footnotes, orphans)


def _chapter_key(doc) -> object:
    """The chapter's alert identity: its number, or (chapter 0 = no chapterN in the file name)
    its file, so un-numbered chapters do not collide on one alert key."""
    return doc.chapter_number or str(getattr(doc, "file_path", "") or "")


def _apply_structure_repair(content: str) -> Tuple[str, Dict[int, Optional[str]]]:
    """Apply in-memory markdown-structure repair before parsing (opt-in).

    Runs the block-quote detector (src.ingestion.markdown_structure) so Chandra
    quotations that were emitted as plain paragraphs get re-marked as ``>``
    blockquotes, which ``split_into_blocks`` then preserves as ``is_quote``
    paragraphs. Returns the (possibly rewritten) content plus a map from the
    0-based paragraph-block index of a re-marked quote to its attribution text
    (the quote's own source), so the parser can record ``quote_attribution``.

    Never rewrites the quotation's words; only adds ``>`` markers. Import is
    local so the parser has no hard dependency on the ingestion package unless
    the repair is actually requested.
    """
    from src.ingestion.markdown_structure import detect_block_quotes

    result = detect_block_quotes(content)
    if not result.changed:
        return content, {}
    # Map each applied quote span's paragraph range to its attribution.
    attribution_by_para: Dict[int, Optional[str]] = {}
    for span in result.spans:
        if span.needs_review:
            continue
        for para_idx in range(span.start_para, span.end_para + 1):
            attribution_by_para[para_idx] = span.attribution
    return result.markdown, attribution_by_para


def _detect_table_hints(content: str) -> List[dict]:
    """Detect flattened task-org / 2-D tables in Chandra markdown (opt-in).

    Returns review-flagged hint dicts (snapshot -> group -> units) for the
    document's ``table_hints``; empty when no flattened table is found. Local
    import keeps the parser free of a hard ingestion dependency unless repair is
    requested.
    """
    from src.ingestion.markdown_structure import detect_flattened_tables

    return detect_flattened_tables(content).to_hint_dicts()


def parse_content_file(
    file_path: Path,
    section_id: str,
    start_paragraph_num: int,
    metadata: Metadata,
    *,
    apply_structure_repair: bool = False,
) -> MarkdownDocument:
    """Parse a single content markdown file.

    When ``apply_structure_repair`` is True, Chandra's markdown structural blind
    spots are repaired in-memory first (block quotes re-marked) so quotations are
    preserved as ``is_quote`` paragraphs. Off by default: existing callers and
    non-Chandra sources are unaffected.
    """
    content = file_path.read_text(encoding="utf-8")

    attribution_by_para: Dict[int, Optional[str]] = {}
    table_hints: List[dict] = []
    if apply_structure_repair:
        table_hints = _detect_table_hints(content)
        content, attribution_by_para = _apply_structure_repair(content)

    # Extract chapter number from filename
    match = _CHAPTER_NUM_PATTERN.search(file_path.name)
    chapter_num = int(match.group(1)) if match else 0

    # Create document
    doc = MarkdownDocument(
        book=metadata.book,
        chapter_number=chapter_num,
        chapter_title=metadata.chapter_title,
        section_id=section_id,
        author=metadata.author,
        series=metadata.series,
        license=metadata.license,
        source_url=metadata.source_url,
        file_path=file_path,
    )

    # Phase 1 SEPARATES the notes from the narrative (src/notes): the body loses the notes section
    # and site navigation, and its note markers are canonicalised; the notes keep their provenance.
    from src.notes import separate_notes_detailed

    body, notes, separation = separate_notes_detailed(content, metadata.source_url)
    if separation.duplicates:
        from src.utils.observe import observe_warn_counted

        observe_warn_counted(
            "parser",
            "notes_duplicate_definition",
            key=(metadata.book, str(file_path)),
            every=10**9,
            note=(
                "a note number is defined more than once in the notes section; the FIRST "
                "definition was kept and the repeat dropped - check the source for a repeated "
                "section or restarted numbering and fix the markdown"
            ),
            book=metadata.book,
            file=str(file_path),
            numbers=separation.duplicates[:10],
        )

    # Build page map and create paragraphs (from the BODY only), located in the marker-free body
    page_content, page_map, page_markers = _paginate(body)
    paragraph_blocks = split_into_blocks(body)
    doc.paragraphs = _create_paragraphs(
        paragraph_blocks,
        start_paragraph_num,
        section_id,
        file_path,
        page_content,
        page_map,
    )
    doc.page_markers = _page_markers_for(page_markers, doc.paragraphs)
    # Attach quote attribution to re-marked quote paragraphs (by block index).
    if attribution_by_para:
        for block_idx, para in enumerate(doc.paragraphs):
            if para.is_quote and block_idx in attribution_by_para:
                para.quote_attribution = attribution_by_para[block_idx]

    # Add footnotes, images and maps. Notes first: an image linked only from a note anchors to the
    # paragraph citing that note. Media are extracted from the FULL content (a map linked only
    # from a note is still a map) but anchored against the BODY the paragraphs were built from.
    _add_footnotes_to_doc(doc, content, notes)
    _warn_unresolvable_media(content, metadata.source_url, metadata.book)
    _add_images_to_doc(
        doc,
        content,
        base_url=metadata.source_url,
        anchor_content=body,
        notes=doc.footnotes,
    )
    _add_maps_to_doc(doc, content, base_url=metadata.source_url)

    # Attach flattened-table hints (review-flagged) from structure repair.
    doc.table_hints = table_hints

    return doc


def parse_chapter(
    chapter_group: ChapterGroup, *, apply_structure_repair: bool = False
) -> List[MarkdownDocument]:
    """Parse all sections of a chapter with continuous paragraph numbering.

    ``apply_structure_repair`` (opt-in) enables in-memory Chandra
    structure repair (block-quote re-marking) per section file.
    """
    metadata = parse_metadata(chapter_group.meta_file)

    documents = []
    current_para_num = 1

    # Sort sections to ensure consistent ordering
    sorted_sections = sorted(chapter_group.content_files.items())

    for section_id, file_path in sorted_sections:
        doc = parse_content_file(
            file_path,
            section_id,
            current_para_num,
            metadata,
            apply_structure_repair=apply_structure_repair,
        )
        documents.append(doc)

        # Update paragraph counter for next section
        if doc.paragraphs:
            current_para_num = doc.paragraphs[-1].absolute_number + 1

    if documents:
        _link_chapter_notes(documents, metadata.book)
    return documents
