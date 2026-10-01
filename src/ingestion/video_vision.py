"""Live Grok-vision perception for video speaker-id (9b implementation).

Implements the ``VisionAnalyzer`` protocol from ``video_perception``: given one
video frame + the candidate roster (a SUGGESTION prior, with an explicit
"unknown" escape), asks Grok's vision model to (1) read any on-screen name
caption/chyron [PRIMARY] and (2) say which — if any — roster candidate is the
on-screen person [FALLBACK]. Returns raw signals; the deterministic resolver
(speaker_id / 9a) owns all confidence + auto-assert-vs-review policy.

Lives in the separate video container. The Grok call is guarded: any failure
yields empty signals (the resolver then treats the segment as unknown ->
needs-review), never fabricates an identity.
"""

from __future__ import annotations

import base64
import logging
from typing import List

from src.ingestion.speaker_id import RosterCandidate

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You identify who is on screen in a single WWII documentary frame. Read any "
    "on-screen name caption/chyron/lower-third VERBATIM. You are given a list of "
    "candidate people who may appear (a suggestion, NOT exhaustive). Match the "
    "on-screen person to a candidate ONLY if reasonably confident; otherwise say "
    "unknown. Never guess. Return strict JSON."
)


def _prompt(roster: List[RosterCandidate], transcript_context: str) -> str:
    cands = "\n".join(
        f"- {c.person_id}: {c.name} (source={c.source.value})" for c in roster
    )
    return (
        "Analyze this documentary frame.\n\n"
        f"Transcript context near this moment: {transcript_context[:300]}\n\n"
        "Candidate people who MAY appear (suggestion, not a closed set):\n"
        f"{cands or '(none)'}\n\n"
        "Return JSON:\n"
        "{\n"
        '  "caption_text": "<on-screen name caption verbatim, or empty>",\n'
        '  "caption_person_id": "<candidate id the caption matches, or null>",\n'
        '  "caption_confidence": 0.0,\n'
        '  "on_screen_person": true,\n'
        '  "headshots": [{"person_id":"<id>","name":"<name>","confidence":0.0}]\n'
        "}\n"
        "Rules: caption_text is the PRIMARY signal. headshots is a ranked FALLBACK "
        "(may be empty / unknown). on_screen_person=false if it is B-roll/footage "
        "with no visible speaking person (suggests a narrator voice-over)."
    )


class GrokVisionAnalyzer:  # pylint: disable=too-few-public-methods
    """VisionAnalyzer backed by the Grok vision API (extract_json_with_image_base64)."""

    def __init__(self, grok_client=None) -> None:
        self._client = grok_client  # injectable for tests

    def _get_client(self):
        if self._client is not None:
            return self._client
        from src.grok_client import GrokClient
        from src.utils.config import load_config, get_paths

        cfg = load_config()
        paths = get_paths(cfg)
        return GrokClient(paths["api_cache"])

    def analyze_frame(
        self, frame: bytes, roster: List[RosterCandidate], transcript_context: str
    ) -> dict:
        """Frame -> {caption_text, caption_person_id, caption_confidence,
        on_screen_person, headshots[]}. Empty/unknown on any error (never fabricate)."""
        try:
            client = self._get_client()
            image_b64 = base64.b64encode(frame).decode("ascii")
            result = client.extract_json_with_image_base64(
                prompt=_prompt(roster, transcript_context),
                image_base64=image_b64,
                system_prompt=_SYSTEM,
                cache_type="events",
            )
            return result if isinstance(result, dict) else {}
        except (
            Exception
        ) as e:  # noqa: BLE001 - perception failure -> unknown, not crash
            logger.warning("Grok vision analyze_frame failed: %s", e)
            return {}
