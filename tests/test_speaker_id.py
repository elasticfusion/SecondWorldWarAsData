"""Tests for the deterministic speaker-id resolver (src/ingestion/speaker_id.py).

These pin the RELIABILITY POLICY: caption is primary + can auto-assert; a
headshot alone is always review-gated; cross-signal agreement boosts; roster
source trust weights matches; unknown/low-confidence -> needs-review; voice
diarization continuity propagates a confident identity to the same speaker's
other segments (still review-gated).
"""

from src.ingestion.speaker_id import (
    AUTO_ASSERT_THRESHOLD,
    CaptionSignal,
    HeadshotSignal,
    RosterCandidate,
    RosterSource,
    SpeakerRole,
    SpeakerSignals,
    resolve_speakers,
)


def _roster():
    return [
        RosterCandidate("P_BRADLEY", "Omar N. Bradley", RosterSource.IMDB_CAST),
        RosterCandidate("P_PATTON", "George S. Patton", RosterSource.TRANSCRIPT),
        RosterCandidate("P_TOPIC", "Some ETO Figure", RosterSource.TOPIC),
    ]


def test_clear_caption_auto_asserts():
    seg = SpeakerSignals(
        speaker_label="Speaker 1",
        timecode="00:00:10-00:00:20",
        caption=CaptionSignal(
            text="Gen. Bradley", person_id="P_BRADLEY", confidence=0.95
        ),
    )
    (r,) = resolve_speakers([seg], _roster())
    assert r.person_id == "P_BRADLEY"
    assert r.method == "caption"
    assert r.confidence >= AUTO_ASSERT_THRESHOLD
    assert r.needs_review is False


def test_headshot_only_is_always_review_gated():
    """Even a high-confidence headshot alone must NOT auto-assert."""
    seg = SpeakerSignals(
        speaker_label="Speaker 1",
        timecode="00:01:00-00:01:10",
        headshots=[HeadshotSignal("P_BRADLEY", "Omar N. Bradley", confidence=0.99)],
    )
    (r,) = resolve_speakers([seg], _roster())
    assert r.person_id == "P_BRADLEY"
    assert r.method == "headshot"
    assert r.needs_review is True  # the key policy


def test_caption_plus_headshot_agreement_boosts():
    agree = SpeakerSignals(
        "Speaker 1",
        "00:00:10-00:00:20",
        caption=CaptionSignal("Bradley", "P_BRADLEY", 0.6),
        headshots=[HeadshotSignal("P_BRADLEY", "Omar N. Bradley", 0.8)],
    )
    cap_only = SpeakerSignals(
        "Speaker 2",
        "00:00:30-00:00:40",
        caption=CaptionSignal("Bradley", "P_BRADLEY", 0.6),
    )
    r_agree, r_cap = resolve_speakers([agree, cap_only], _roster())
    assert r_agree.method == "caption+headshot"
    assert r_agree.confidence > r_cap.confidence  # agreement lifted it


def test_caption_headshot_disagreement_penalized_and_noted():
    seg = SpeakerSignals(
        "Speaker 1",
        "00:00:10-00:00:20",
        caption=CaptionSignal("Bradley", "P_BRADLEY", 0.9),
        headshots=[HeadshotSignal("P_PATTON", "George S. Patton", 0.85)],
    )
    (r,) = resolve_speakers([seg], _roster())
    assert r.person_id == "P_BRADLEY"  # caption wins
    assert "disagrees" in r.notes


def test_roster_trust_weights_confidence():
    """Same caption confidence, higher-trust roster source -> higher confidence."""
    high = SpeakerSignals(
        "Speaker 1",
        "00:00:10-00:00:20",
        caption=CaptionSignal("Bradley", "P_BRADLEY", 0.9),  # IMDB_CAST trust 1.0
    )
    low = SpeakerSignals(
        "Speaker 2",
        "00:00:30-00:00:40",
        caption=CaptionSignal("Topic", "P_TOPIC", 0.9),  # TOPIC trust 0.3
    )
    r_high, r_low = resolve_speakers([high, low], _roster())
    assert r_high.confidence > r_low.confidence


def test_caption_text_unmatched_to_person_flags_review():
    seg = SpeakerSignals(
        "Speaker 1",
        "00:00:10-00:00:20",
        caption=CaptionSignal(
            text="Some Unlisted Interviewee", person_id=None, confidence=0.9
        ),
    )
    (r,) = resolve_speakers([seg], _roster())
    assert r.person_id is None
    assert r.name == "Some Unlisted Interviewee"
    assert r.needs_review is True


def test_no_signal_is_unknown_and_review():
    seg = SpeakerSignals("Speaker 1", "00:00:10-00:00:20")
    (r,) = resolve_speakers([seg], _roster())
    assert r.person_id is None and r.method == "none" and r.needs_review is True


def test_diarization_propagates_confident_identity_to_same_speaker():
    """A confident caption ID on one segment carries to the same voice's other
    (unknown) segments — but stays review-gated (inherited, not observed)."""
    named = SpeakerSignals(
        "Speaker 3",
        "00:05:00-00:05:10",
        caption=CaptionSignal("Bradley", "P_BRADLEY", 0.95),
    )
    later_same_voice = SpeakerSignals("Speaker 3", "00:08:00-00:08:10")
    other_voice = SpeakerSignals("Speaker 9", "00:09:00-00:09:10")
    r_named, r_prop, r_other = resolve_speakers(
        [named, later_same_voice, other_voice], _roster()
    )
    assert r_named.needs_review is False  # observed caption
    assert r_prop.person_id == "P_BRADLEY"  # propagated by voice continuity
    assert r_prop.method == "diarization"
    assert r_prop.needs_review is True  # inherited -> confirm
    assert r_other.person_id is None  # different voice, no signal -> unknown


def test_on_screen_person_is_interviewee():
    seg = SpeakerSignals(
        "Speaker 1",
        "00:00:10-00:00:20",
        caption=CaptionSignal("Veteran X", "P_PATTON", 0.9),
        on_screen_person=True,
    )
    (r,) = resolve_speakers([seg], _roster())
    assert r.role == SpeakerRole.INTERVIEWEE
    assert r.to_dict()["role"] == "interviewee"


def test_offscreen_voice_is_narrator_role():
    seg = SpeakerSignals("Speaker 1", "00:00:10-00:00:20", on_screen_person=False)
    (r,) = resolve_speakers([seg], _roster())
    assert r.role == SpeakerRole.NARRATOR


def test_dominant_offscreen_voice_tagged_narrator_across_segments():
    """The recurring off-screen voice (no headshot, voice-over) is the
    documentary's narrator — tagged across all its segments, distinct from
    on-camera interviewees."""
    segs = [
        SpeakerSignals("NARR", "00:00:00-00:00:10", on_screen_person=False),
        SpeakerSignals("NARR", "00:01:00-00:01:10", on_screen_person=False),
        SpeakerSignals("NARR", "00:02:00-00:02:10", on_screen_person=False),
        SpeakerSignals(
            "GUEST",
            "00:03:00-00:03:10",
            caption=CaptionSignal("Bradley", "P_BRADLEY", 0.95),
            on_screen_person=True,
        ),
    ]
    resolved = resolve_speakers(segs, _roster())
    narr = [r for r in resolved if r.speaker_label == "NARR"]
    guest = [r for r in resolved if r.speaker_label == "GUEST"][0]
    assert all(r.role == SpeakerRole.NARRATOR for r in narr)
    assert guest.role == SpeakerRole.INTERVIEWEE  # on-camera, not narrator
    assert guest.person_id == "P_BRADLEY"


def test_role_hint_from_perception_respected():
    seg = SpeakerSignals(
        "Speaker 1", "00:00:10-00:00:20", role_hint=SpeakerRole.ARCHIVAL
    )
    (r,) = resolve_speakers([seg], _roster())
    assert r.role == SpeakerRole.ARCHIVAL
