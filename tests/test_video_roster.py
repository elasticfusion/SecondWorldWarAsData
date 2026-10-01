"""Tests for the tiered video roster builder (9c, src/ingestion/video_roster.py).

Roster is a SUGGESTION prior: IMDB/Wikipedia cast (strongest) -> transcript
People -> topic fallback, de-duped by PersonID keeping the highest-trust source
+ merged reference images. Lookups are injected (pure/testable)."""

from src.ingestion.speaker_id import RosterSource
from src.ingestion.video_roster import build_roster, _names_from_extract


class _Appearances:
    def __init__(self, rows):
        self._rows = rows

    def lookup(self, title):
        return self._rows


def test_transcript_and_topic_tiers_tagged():
    roster = build_roster(
        transcript_people=[{"person_id": "P_PATTON", "name": "George S. Patton"}],
        topic_people=[{"person_id": "P_ETO", "name": "Some ETO Figure"}],
    )
    by_id = {c.person_id: c for c in roster}
    assert by_id["P_PATTON"].source == RosterSource.TRANSCRIPT
    assert by_id["P_ETO"].source == RosterSource.TOPIC


def test_imdb_cast_wins_and_merges_images():
    """A person in BOTH the IMDB cast and the transcript keeps the IMDB (higher
    trust) source, and reference images from both are merged."""
    appearances = _Appearances(
        [
            {
                "person_id": "P_BRADLEY",
                "name": "Omar N. Bradley",
                "source": "imdb",
                "image_urls": ["imdb.jpg"],
            }
        ]
    )
    roster = build_roster(
        title="The World at War S01E17",
        transcript_people=[
            {"person_id": "P_BRADLEY", "name": "Bradley", "image_urls": ["trans.jpg"]}
        ],
        appearance_lookup=appearances,
    )
    (c,) = roster
    assert c.person_id == "P_BRADLEY"
    assert c.source == RosterSource.IMDB_CAST  # higher trust wins
    assert set(c.reference_image_urls) == {"imdb.jpg", "trans.jpg"}  # merged


def test_no_title_or_lookup_skips_appearance_tier():
    roster = build_roster(
        transcript_people=[{"person_id": "P1", "name": "A Person"}],
    )
    assert len(roster) == 1 and roster[0].source == RosterSource.TRANSCRIPT


def test_appearance_lookup_failure_is_graceful():
    class _Boom:
        def lookup(self, title):
            raise RuntimeError("network down")

    roster = build_roster(
        title="Some Film",
        transcript_people=[{"person_id": "P1", "name": "A Person"}],
        appearance_lookup=_Boom(),
    )
    # falls back to transcript tier, no crash
    assert [c.person_id for c in roster] == ["P1"]


def test_empty_appearance_rows_yields_only_lower_tiers():
    roster = build_roster(
        title="Unknown Clip",
        transcript_people=[{"person_id": "P1", "name": "A Person"}],
        appearance_lookup=_Appearances([]),
    )
    assert [c.source for c in roster] == [RosterSource.TRANSCRIPT]


def test_wikipedia_name_harvest_conservative():
    names = _names_from_extract(
        "The episode features interviews with Omar Bradley and George Patton."
    )
    harvested = {n["name"] for n in names}
    assert "Omar Bradley" in harvested and "George Patton" in harvested
    assert all(n["source"] == "wikipedia" for n in names)


def test_unnamed_entries_skipped():
    roster = build_roster(
        transcript_people=[
            {"person_id": "P1", "name": ""},
            {"person_id": None, "name": "X"},
        ],
    )
    assert roster == []
