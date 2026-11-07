"""Single map-vs-image classifier for document media (DRY: used by Phase 2 images + Phase 3 maps).

Rule (project decision):

* **ibiblio (HyperWar)** — the URL PATH decides, deterministically, no LLM:
  ``/maps/`` -> map; ``/img/`` and ``/icn/`` (photo full-res / thumbnail) -> image (photograph);
  ``/charts/`` -> image (diagram). Any other ibiblio path is ambiguous.
* **every other host** — URL patterns and captions are NOT trusted: the item is ``ambiguous`` and
  MUST be decided by Grok vision. That vision step is deferred to Phase 3
  (``image_map_vision.resolve_pending``) but never skipped: an image still pending after Phase 3
  raises an actionable alert.

``content_type`` is the image-record hint (``map`` | ``photograph`` | ``diagram``). For ambiguous
items it is a keyword prior only (shown on the image record until vision decides).
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

_IBIBLIO_HOST = "ibiblio.org"

# ibiblio path segment -> (kind, content_type)
_IBIBLIO_DIRS = {
    "maps": ("map", "map"),
    "img": ("image", "photograph"),
    "icn": ("image", "photograph"),
    "charts": ("image", "diagram"),
}

_DIAGRAM_WORDS = ("diagram", "chart", "sketch", "table", "graph", "organization")

# Values stored on image records in ``map_check``.
MAP_CHECK_MAP = "map"
MAP_CHECK_NOT_MAP = "not_map"
MAP_CHECK_PENDING = "pending_vision"


@dataclass(frozen=True)
class MediaVerdict:
    """kind: 'map' | 'image' | 'ambiguous'; content_type: image-record hint; basis: why."""

    kind: str
    content_type: str
    basis: str


def is_ibiblio(url: str) -> bool:
    """True if ``url`` is hosted on ibiblio.org (any subdomain)."""
    host = (urlparse(url or "").hostname or "").lower()
    return host == _IBIBLIO_HOST or host.endswith("." + _IBIBLIO_HOST)


def _keyword_content_type(name: str) -> str:
    n = (name or "").lower()
    return "diagram" if any(w in n for w in _DIAGRAM_WORDS) else "photograph"


def classify_media(name: str, url: str) -> MediaVerdict:
    """Classify one document image/map reference (see module docstring for the rule)."""
    if is_ibiblio(url):
        segments = [s.lower() for s in urlparse(url).path.split("/") if s]
        # The asset directory is the segment just before the filename (…/<vol>/<dir>/<file>).
        asset_dir = segments[-2] if len(segments) >= 2 else ""
        hit = _IBIBLIO_DIRS.get(asset_dir)
        if hit:
            return MediaVerdict(hit[0], hit[1], "ibiblio_url")
    return MediaVerdict("ambiguous", _keyword_content_type(name), "vision_required")


def vision_is_map(image_bytes: bytes | None, context: dict, grok_client):
    """THE Grok-vision map-vs-image decision (one implementation, used by the Phase 3 map tier-3
    backstop and the Phase 3 pending-image step). Reuses ImageCaptioner's single vision call, which
    answers ``classification: photograph|diagram|map|unknown`` (+ a description we keep).

    Returns ``(is_map, caption)``: is_map is True/False when vision actually decided, or None when
    it could not (no bytes, no client, vision error, or 'unknown') — callers MUST leave the item
    pending (never stamp a guess). ``caption`` is the ImageCaption (None when not attempted).
    """
    if not image_bytes or grok_client is None:
        return None, None
    from src.extraction.image_captioner import ImageCaptioner

    caption = ImageCaptioner(grok_client).caption(image_bytes, context)
    return verdict_from_classification(caption.method, caption.classification), caption


# The only vision answers that DECIDE map-vs-image; anything else ('unknown', 'illustration', a
# typo, a context-only fallback) is undecided and stays pending — never a silent 'not a map'.
_DECIDING = {"map": True, "photograph": False, "diagram": False}


def verdict_from_classification(method: str, classification: str) -> bool | None:
    """is_map from a captioner (method, classification), or None when it did not truly decide."""
    if method != "grok-vision":
        return None
    return _DECIDING.get(str(classification or "").strip().lower())
