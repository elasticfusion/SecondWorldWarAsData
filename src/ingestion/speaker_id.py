"""Deterministic speaker-identification resolver for video transcripts (9a).

Fuses the perception signals (produced elsewhere by Grok vision / STT — 9b) into
a single resolved identity per speaker, with a confidence and full provenance,
and decides what is auto-asserted vs. sent to human review. This module is
PURE + deterministic (no network, no Grok): it encodes the *policy*, so the
reliability rules are explicit and unit-tested rather than an LLM's judgement.

Reliability policy (from the operator design dialogue):
- **Caption/chyron OCR is the PRIMARY, high-trust signal.** Documentaries name
  speakers in lower-thirds; reading printed text off a frame is reliable.
- **Headshot match is a FALLBACK, low-trust signal.** Matching a face from a
  single (often B&W, angled) documentary frame is error-prone, so a headshot
  match alone is a *suggestion* that is REVIEW-GATED, never auto-asserted.
- **The roster is a SUGGESTION with an "unknown" escape hatch**, not a closed
  set — narrators/interviewees/unlisted figures must resolve gracefully. Each
  roster entry carries a SOURCE TRUST level (imdb-cast > transcript-mention >
  topic-roster), so a match against a higher-trust roster entry weighs more.
- **Cross-signal AGREEMENT is the confidence multiplier.** When caption,
  headshot, and voice-diarization continuity converge on the same person, that
  is the strongest evidence. Disagreement lowers confidence / flags review.
- **Flag, never fabricate.** Unknown or low-confidence -> needs-review; only
  strong evidence (a clear caption, or multi-signal agreement) auto-asserts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

# --- roster source trust (higher = more authoritative) -----------------------


class RosterSource(str, Enum):
    """Where a candidate came from — drives how much a match against it counts."""

    IMDB_CAST = "imdb-cast"  # authoritative episode cast/appearances
    WIKIPEDIA = "wikipedia"  # episode "featured"/appearances list
    TRANSCRIPT = "transcript-mention"  # named in this video's own transcript
    TOPIC = "topic-roster"  # campaign/topic figures (ETO), widening fallback


_ROSTER_TRUST: Dict[RosterSource, float] = {
    RosterSource.IMDB_CAST: 1.0,
    RosterSource.WIKIPEDIA: 0.9,
    RosterSource.TRANSCRIPT: 0.6,
    RosterSource.TOPIC: 0.3,
}

# Confidence at/above which an identity is auto-asserted; below -> needs-review.
AUTO_ASSERT_THRESHOLD = 0.75


@dataclass
class RosterCandidate:
    """A person who may appear, offered to perception as a suggestion (9c)."""

    person_id: str
    name: str
    source: RosterSource
    reference_image_urls: List[str] = field(default_factory=list)


@dataclass
class CaptionSignal:
    """On-screen caption/chyron text read from a frame (PRIMARY, Grok vision)."""

    text: str  # e.g. "Gen. Omar N. Bradley"
    person_id: Optional[str] = None  # resolved to a roster PersonID if matched
    confidence: float = 0.0  # vision OCR confidence 0..1


@dataclass
class HeadshotSignal:
    """A headshot-vs-roster match candidate (FALLBACK, Grok vision, low trust)."""

    person_id: str
    name: str
    confidence: float = 0.0  # vision match confidence 0..1


@dataclass
class SpeakerSignals:
    """All perception signals for ONE speaker segment, fed to the resolver."""

    speaker_label: str  # diarization label, e.g. "Speaker 1"
    timecode: str  # provenance anchor "HH:MM:SS-HH:MM:SS"
    caption: Optional[CaptionSignal] = None
    headshots: List[HeadshotSignal] = field(default_factory=list)
    transcript_context: str = ""  # words near the timecode (a textual prior)


@dataclass
class ResolvedSpeaker:
    """The resolver's decision for one speaker segment."""

    speaker_label: str
    timecode: str
    person_id: Optional[str]  # None when unknown
    name: Optional[str]
    confidence: float
    method: str  # "caption" | "headshot" | "caption+headshot" | "diarization" | "none"
    needs_review: bool
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "speaker_label": self.speaker_label,
            "timecode": self.timecode,
            "person_id": self.person_id,
            "name": self.name,
            "confidence": round(self.confidence, 3),
            "method": self.method,
            "needs_review": self.needs_review,
            "notes": self.notes,
        }


def _roster_trust(candidate: Optional[RosterCandidate]) -> float:
    if candidate is None:
        return 0.5  # off-roster (e.g. caption naming someone not listed)
    return _ROSTER_TRUST.get(candidate.source, 0.3)


def _resolve_one(
    signals: SpeakerSignals,
    roster: Dict[str, RosterCandidate],
) -> ResolvedSpeaker:
    """Fuse one segment's signals -> a resolved identity + confidence + review flag."""
    caption = signals.caption
    top_head = max(signals.headshots, key=lambda h: h.confidence, default=None)

    cap_pid = caption.person_id if caption else None
    head_pid = top_head.person_id if top_head else None
    agree = bool(cap_pid) and cap_pid == head_pid

    # 1) Caption present (PRIMARY). High trust, especially if the headshot agrees.
    if caption and caption.text and cap_pid:
        base = 0.7 + 0.25 * caption.confidence  # a clear caption is strong on its own
        base *= _roster_trust(roster.get(cap_pid))
        method = "caption"
        notes = f"caption='{caption.text}'"
        if agree:
            base = min(1.0, base + 0.15)  # caption+headshot agreement -> boost
            method = "caption+headshot"
            notes += " (headshot agrees)"
        elif head_pid and not agree:
            base = max(0.0, base - 0.1)  # disagreement -> slight penalty + note
            notes += f" (headshot disagrees: {top_head.name})"
        conf = min(1.0, base)
        return ResolvedSpeaker(
            speaker_label=signals.speaker_label,
            timecode=signals.timecode,
            person_id=cap_pid,
            name=(roster.get(cap_pid).name if cap_pid in roster else caption.text),
            confidence=conf,
            method=method,
            needs_review=conf < AUTO_ASSERT_THRESHOLD,
            notes=notes,
        )

    # 2) A caption with TEXT but no roster resolution — read a name we can't yet
    # map to a PersonID. Never auto-assert; surface the name for review.
    if caption and caption.text and not cap_pid:
        return ResolvedSpeaker(
            speaker_label=signals.speaker_label,
            timecode=signals.timecode,
            person_id=None,
            name=caption.text,
            confidence=min(0.6, 0.5 * caption.confidence + 0.1),
            method="caption",
            needs_review=True,
            notes=f"caption='{caption.text}' unmatched to a known person",
        )

    # 3) Headshot only (FALLBACK). ALWAYS review-gated regardless of confidence —
    # a face match from one documentary frame is a suggestion, not an assertion.
    if top_head:
        conf = min(0.7, top_head.confidence * _roster_trust(roster.get(head_pid)))
        return ResolvedSpeaker(
            speaker_label=signals.speaker_label,
            timecode=signals.timecode,
            person_id=head_pid,
            name=top_head.name,
            confidence=conf,
            method="headshot",
            needs_review=True,  # headshot-only NEVER auto-asserts
            notes=f"headshot suggestion (conf {top_head.confidence:.2f}) — review",
        )

    # 4) No visual signal -> unknown for this segment (diarization continuity may
    # still name it later via _propagate). Unknown -> review.
    return ResolvedSpeaker(
        speaker_label=signals.speaker_label,
        timecode=signals.timecode,
        person_id=None,
        name=None,
        confidence=0.0,
        method="none",
        needs_review=True,
        notes="no caption/headshot — unknown",
    )


def _propagate_diarization(resolved: List[ResolvedSpeaker]) -> None:
    """Voice-diarization continuity: if the SAME speaker_label is confidently
    identified in one segment, carry that identity to its other (unknown)
    segments — but at reduced confidence and still review-gated, since a shared
    voice label is weaker than a per-segment visual signal."""
    best: Dict[str, ResolvedSpeaker] = {}
    for r in resolved:
        if r.person_id and not r.needs_review:
            cur = best.get(r.speaker_label)
            if cur is None or r.confidence > cur.confidence:
                best[r.speaker_label] = r
    for r in resolved:
        if r.person_id is None and r.speaker_label in best:
            src = best[r.speaker_label]
            r.person_id = src.person_id
            r.name = src.name
            r.confidence = min(0.7, src.confidence * 0.8)
            r.method = "diarization"
            r.needs_review = True  # inherited identity — confirm
            r.notes = (
                f"same voice as {src.timecode} ({src.name}) — diarization continuity"
            )


def resolve_speakers(
    segments: List[SpeakerSignals],
    roster: List[RosterCandidate],
) -> List[ResolvedSpeaker]:
    """Resolve every speaker segment to an identity decision.

    roster is a SUGGESTION (with an implicit 'unknown' escape): candidates are
    used to weight matches by source trust, never to force a choice. Returns one
    ResolvedSpeaker per input segment; low-confidence/unknown carry
    needs_review=True (flag, never fabricate)."""
    roster_by_id = {c.person_id: c for c in roster}
    resolved = [_resolve_one(s, roster_by_id) for s in segments]
    _propagate_diarization(resolved)
    return resolved
