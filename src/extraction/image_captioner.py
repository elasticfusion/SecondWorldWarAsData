"""Grok-vision captioner for document still-images (photos / diagrams).

The intake VISION step (``MAP_IMAGE_AV_INGESTION.md``): a still image that carries
no extractable text — a photograph or diagram pulled from source material — is
*identified*, not OCR'd. Given the image bytes plus the surrounding context the
extractor already linked (sub-event summary, place, date, alt/caption text), ask
Grok vision for a factual description + a content classification + a confidence.

Reliability policy (mirrors the speaker-id resolver): deterministic context is
the prior; Grok fills the semantic residual; low confidence is flagged for human
review; the model must NEVER fabricate identities/places it cannot support from
the image + context. On any error the result is "unknown" (empty description,
needs_review=True), never a crash and never a made-up caption.

Separate from ``video_vision`` (that identifies speakers against a roster); this
identifies a historical photo/diagram. Both share the low-level
``extract_json_with_image_base64`` primitive only.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# Below this confidence the caption is kept but flagged for human review.
AUTO_ACCEPT_CONFIDENCE = 0.75

_SYSTEM = (
    "You identify a single still image from a World War II source document (an "
    "after-action report, unit history, or official history). Using the image AND "
    "the provided surrounding context, describe factually what the image shows: "
    "people (by role/unit if evident), place, equipment, action, or diagram "
    "content. Prefer the context when the image is ambiguous. NEVER invent names, "
    "units, places, or dates you cannot support from the image or context — say "
    "what is actually visible. Classify the image and give a 0..1 confidence. "
    "Return strict JSON."
)


@dataclass
class ImageCaption:
    """Result of a vision captioning pass over one document image."""

    description: str
    classification: str  # "photograph" | "diagram" | "map" | "unknown"
    confidence: float
    needs_review: bool
    method: str  # "grok-vision" | "context-only" | "error"

    def to_dict(self) -> dict:
        return {
            "description": self.description,
            "classification": self.classification,
            "caption_confidence": self.confidence,
            "needs_review": self.needs_review,
            "caption_method": self.method,
        }


def _prompt(context: dict) -> str:
    alt = (context.get("alt_text") or "").strip()
    sub_event = (context.get("sub_event_name") or "").strip()
    place = (context.get("place_name") or "").strip()
    date = (context.get("date") or "").strip()
    book = (context.get("book") or "").strip()
    ctx_lines = "\n".join(
        f"- {label}: {val}"
        for label, val in (
            ("Source document", book),
            ("Caption / alt text", alt),
            ("Nearby event", sub_event),
            ("Linked place", place),
            ("Linked date", date),
        )
        if val
    )
    return (
        "Identify this WWII source image.\n\n"
        f"Surrounding context (use as a prior; do not contradict it without clear "
        f"visual evidence):\n{ctx_lines or '(no linked context)'}\n\n"
        "Return JSON:\n"
        "{\n"
        '  "description": "<factual description of what the image shows, grounded '
        'in the image + context; empty if nothing can be said>",\n'
        '  "classification": "photograph|diagram|map|unknown",\n'
        '  "confidence": 0.0\n'
        "}"
    )


class ImageCaptioner:  # pylint: disable=too-few-public-methods
    """Captions document still-images via Grok vision (injectable client)."""

    def __init__(self, grok_client=None) -> None:
        self._client = grok_client

    def _get_client(self):
        if self._client is not None:
            return self._client
        from src.grok_client import GrokClient
        from src.utils.config import load_config, get_paths

        return GrokClient(get_paths(load_config())["api_cache"])

    def caption(self, image_bytes: Optional[bytes], context: dict) -> ImageCaption:
        """Caption one image. Empty/unknown (needs_review) on any failure —
        never fabricate, never raise."""
        if not image_bytes:
            # No image to look at — fall back to the linked context text only.
            alt = (context.get("alt_text") or "").strip()
            return ImageCaption(
                description=alt,
                classification=context.get("classification", "unknown"),
                confidence=0.0,
                needs_review=True,
                method="context-only",
            )
        try:
            client = self._get_client()
            b64 = base64.b64encode(image_bytes).decode("ascii")
            result = client.extract_json_with_image_base64(
                prompt=_prompt(context),
                image_base64=b64,
                system_prompt=_SYSTEM,
                cache_type="default",
            )
            if not isinstance(result, dict):
                raise ValueError("non-dict vision result")
            desc = str(result.get("description", "")).strip()
            cls = str(result.get("classification", "unknown")).strip() or "unknown"
            try:
                conf = float(result.get("confidence", 0.0))
            except (TypeError, ValueError):
                conf = 0.0
            # Needs review if low confidence OR the model produced no description.
            needs_review = conf < AUTO_ACCEPT_CONFIDENCE or not desc
            return ImageCaption(
                description=desc,
                classification=cls,
                confidence=conf,
                needs_review=needs_review,
                method="grok-vision",
            )
        except Exception as e:  # noqa: BLE001 - vision failure -> unknown, not crash
            logger.warning("Image caption failed: %s", e)
            return ImageCaption(
                description=(context.get("alt_text") or "").strip(),
                classification="unknown",
                confidence=0.0,
                needs_review=True,
                method="error",
            )
